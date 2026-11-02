"""The MCP server: what an agent sees (design §6).

Streamable HTTP at /mcp, stateless, JSON responses. Every request carries a bearer token, either a personal access
token or one issued through OAuth (oauth.py); McpAuth rejects anything else before the SDK sees it. Agents act as their
user with the same visibility rules as the web UI (access.py), and never more: they can generate into and create the
user's own projects, and read whatever the user can read. Nothing here deletes, or changes a project's visibility.
"""

from __future__ import annotations

import io
import time
from typing import Annotated, Any, Callable, Literal

from pydantic import Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.mcpserver.utilities.types import Image
from mcp.server.streamable_http_manager import StreamableHTTPASGIApp
from mcp.server.transport_security import TransportSecuritySettings

from . import __version__, access, auth, catalogue, generation, links, sheets, sprites, tokens
from .config import Settings
from .db import Database
from .models import Artifact, Project, Sheet, SheetCandidate, SheetSlot, User, Variant
from .scheduler import Scheduler
from .storage import Store

INSTRUCTIONS = """\
genai generates images (FLUX.2 klein or Qwen-Image, on a shared GPU) and keeps them in projects, with the prompt and settings that
made each one. You act for the signed-in user who connected you, with their permissions.

- generate makes images from a prompt. Presets wrap the prompt in a game-sprite template (pixel art on a flat key-colour
  background): "sprite" for characters (full body, side view), "item_sprite" for objects (loot, props). Give just the
  subject, e.g. "a goblin archer", "a treasure map". background "magenta" (default) or "green": use green for pink,
  magenta or purple subjects, magenta for green ones, so the clean-up doesn't eat the subject. model
  "flux2-klein-4b" is the default (fast, Apache-2.0); "flux2-klein-9b" is better at hard prompts but about twice as slow
  and under a NON-COMMERCIAL licence: its images may not be used in a commercial game. Say so if you pick it.
  "qwen-image-2512" (Apache-2.0) draws cleaner pixel art and follows item prompts better; sprites default to 512²
  (~4 s each, ~12 s at 1024²). It can't edit: edit_image on its pictures is done by flux2-klein-4b.
- generate waits up to wait_seconds for the images; anything still pending comes back with its status. Check later with
  get_artifact. A 4B image takes ~3 s once the model is loaded; the first one after a model swap can take longer.
- Images go into a project: one of the user's own (by id or name), or their private "Scratch" project when none is
  given. list_projects shows them; create_project makes a new one (private unless the user asked otherwise).
  Others' public projects are readable but you can't add to them.
- get_image returns the picture itself so you can look at it (preview: scaled to 512 px; full: the original PNG, large).
  To save the full-size file, fetch download_url (e.g. `curl -fo assets/mage.png "<download_url>"`): it needs no token
  and expires after 10 minutes (download_expires_at); call get_artifact again for a fresh one.
- The same seed with the same prompt and settings reproduces an image (on the same GPU); vary the seed to explore.
- Sprite presets get a transparent "cutout" version automatically (background removed, same canvas). make_sprite turns
  an image into a pixel-grid sprite (e.g. 64 px, 24 colours; canvas "square" puts it on a size×size tile standing on the
  bottom row, "fit" crops to it). Results list them under variants, each with its own download_url.
- tweak makes new images from an existing one: reroll (new seed), edit_prompt (changed prompt, same seed) or
  edit_image (an instruction applied to the picture itself: "same character, holding an axe"). edit_image keeps a
  character consistent across variants and poses; generating from scratch does not.
- Character sheets: one character in poses, expressions and weapon sets, kept consistent. create_sheet from a
  description makes 4 base candidates; look at them (get_image), freeze the good ones (sheet_mark), sheet_reroll the
  rest, sheet_set_base, then sheet_add_variations (each row: 4 edits of the base), sheet_mark keep, export_sheet (zip
  of cutouts + pixel sprites at the base's scale). Ask the user which candidates they like rather than choosing for
  them when they are around.
- list_artifacts finds earlier images by prompt text or project. Reuse what exists before generating near-duplicates.
- Each user can have a limited number of images queued at once; if generate says the queue is full, wait for some to
  finish rather than retrying in a loop.
"""

SCHEME = "Bearer"
RESOURCE_METADATA_PATH = "/.well-known/oauth-protected-resource/mcp"
PREVIEW_SIDE = 512
MAX_WAIT = 120


class McpAuth:
    """Bearer-token gate in front of the SDK's ASGI app. A 401 points the client at the protected-resource metadata
    (RFC 9728), which is how an OAuth-capable client discovers it can sign the user in from a browser."""

    def __init__(self, inner: ASGIApp, db: Database, public_url: str):
        self.inner, self.db, self.public_url = inner, db, public_url.rstrip("/")
        self.metadata_url = self.public_url + RESOURCE_METADATA_PATH

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            raw = tokens.bearer(dict(scope["headers"]).get(b"authorization", b"").decode("latin-1"))
            ok = False
            if raw:
                with self.db.session() as s:
                    ok = tokens.principal(s, raw) is not None
            if not ok:
                challenge = f'{SCHEME} error="invalid_token", resource_metadata="{self.metadata_url}"'
                res = JSONResponse(
                    {"error": "invalid_token",
                     "error_description": "sign in through your MCP client, or send an access token created under "
                                          f"Agents at {self.public_url}"},
                    status_code=401, headers={"WWW-Authenticate": challenge})
                await res(scope, receive, send)
                return
        await self.inner(scope, receive, send)


def build(db: Database, settings: Settings, scheduler: Scheduler, store: Store,
          poller: generation.Poller) -> tuple[MCPServer, ASGIApp]:
    server = MCPServer(name="genai", version=__version__, instructions=INSTRUCTIONS, website_url=settings.public_url)
    base = settings.public_url.rstrip("/")

    def run(ctx: Context, fn: Callable[[Session, User], Any]) -> Any:
        raw = tokens.bearer((ctx.headers or {}).get("authorization"))
        with db.session() as s:
            who = tokens.principal(s, raw)
            if who is None:
                raise ToolError("not signed in: the token is missing, expired or revoked")
            try:
                return fn(s, who.user)
            except generation.GenerateError as exc:
                raise ToolError(str(exc)) from None
            except access.HTTPException as exc:          # access.py speaks HTTP; say the same thing to the agent
                raise ToolError(str(exc.detail)) from None

    def brief(a: Artifact, s: Session, link: bool = True) -> dict:
        out = _brief(a, s, link)
        vs = list(s.scalars(select(Variant).where(Variant.artifact_id == a.id).order_by(Variant.created_at)))
        if vs:
            out["variants"] = [{"id": v.id, "kind": v.kind, **v.params, "width": v.width, "height": v.height,
                                **({"download_url": f"{out['download_url']}?variant={v.id}"} if "download_url" in out
                                   else {})} for v in vs]
        return out

    def _brief(a: Artifact, s: Session, link: bool) -> dict:
        m = catalogue.MODELS.get(a.model)
        out = {"id": a.id, "status": a.status, "prompt": a.prompt, "preset": a.preset, "model": a.model,
               "project": {"id": a.project.id, "name": a.project.name, "visibility": a.project.visibility},
               "seed": a.seed, "width": a.width, "height": a.height, "by": a.creator.username,
               "web_url": f"{base}/a/{a.id}"}
        if m and not m.commercial:
            out["licence"] = f"{m.licence}: not for commercial use"
        if a.status == "done" and link:
            path, expires = links.create(s, a)
            out["download_url"], out["download_expires_at"] = base + path, expires
        if a.status == "failed":
            out["error"] = a.error
        if a.parent_id:
            out["made_from"], out["op"] = a.parent_id, a.op
        return out

    def find_project(s: Session, user: User, ref: str, write: bool) -> Project:
        """By id, or by name: among the user's own projects first, then (reads only) public ones."""
        ref = (ref or "").strip()
        p = s.get(Project, ref)
        if p is not None and access.can_view(p, user):
            if write and p.owner_id != user.id:
                raise ToolError(f"project {p.name!r} belongs to {p.owner.username}; only its owner adds to it. "
                                "Use one of your own (list_projects) or create_project.")
            return p
        mine = list(s.scalars(select(Project).where(Project.owner_id == user.id,
                                                    func.lower(Project.name) == ref.lower())))
        if mine:
            return mine[0]
        if not write:
            pub = list(s.scalars(select(Project).where(Project.visibility == "public",
                                                       func.lower(Project.name) == ref.lower())))
            if len(pub) == 1:
                return pub[0]
            if len(pub) > 1:
                raise ToolError(f"{ref!r} names {len(pub)} public projects; pass one of these ids: "
                                + ", ".join(f"{p.id} (by {p.owner.username})" for p in pub))
        raise ToolError(f"no project {ref!r} you can {'add to' if write else 'see'}. list_projects shows them.")

    def project_brief(p: Project, user: User, count: int) -> dict:
        return {"id": p.id, "name": p.name, "description": p.description, "visibility": p.visibility,
                "owner": p.owner.username, "mine": p.owner_id == user.id, "default": p.is_default,
                "images": count, "web_url": f"{base}/p/{p.id}"}

    # --- tools ------------------------------------------------------------------------------------------------

    @server.tool()
    def list_models_and_presets(ctx: Context) -> dict:
        """The models (with licences), prompt presets and image sizes generate accepts."""
        return run(ctx, lambda s, user: catalogue.public())

    @server.tool()
    def list_projects(ctx: Context) -> dict:
        """Your projects (you can generate into these) and other users' public ones (read-only)."""
        def go(s, user):
            auth.ensure_default_project(s, user)
            ps = list(s.scalars(select(Project).where(access.visible_projects_clause(user))
                                .order_by(Project.is_default.desc(), Project.created_at.desc())))
            counts = dict(s.execute(select(Artifact.project_id, func.count())
                                    .where(Artifact.project_id.in_([p.id for p in ps]))
                                    .group_by(Artifact.project_id)).all()) if ps else {}
            out = [project_brief(p, user, counts.get(p.id, 0)) for p in ps]
            return {"mine": [p for p in out if p["mine"]], "public": [p for p in out if not p["mine"]]}
        return run(ctx, go)

    @server.tool()
    def create_project(ctx: Context, name: Annotated[str, Field(min_length=1, max_length=100)],
                       description: Annotated[str, Field(max_length=2000)] = "",
                       visibility: Literal["private", "public"] = "private") -> dict:
        """Create one of your projects. private: only you see it. public: every user of the platform can view it
        (only you add to it). Keep private unless the user asked to share."""
        def go(s, user):
            if s.scalar(select(Project.id).where(Project.owner_id == user.id, Project.name == name.strip())):
                raise ToolError(f"you already have a project named {name.strip()!r}")
            p = Project(owner_id=user.id, name=name.strip(), description=description.strip(), visibility=visibility)
            s.add(p)
            s.flush()
            return project_brief(p, user, 0)
        return run(ctx, go)

    @server.tool()
    def generate(ctx: Context, prompt: Annotated[str, Field(min_length=1, max_length=4000)],
                 project: str | None = None, preset: str = catalogue.DEFAULT_PRESET,
                 model: str = catalogue.DEFAULT_MODEL,
                 width: Annotated[int | None, Field(ge=256, le=2048, multiple_of=16)] = None,
                 height: Annotated[int | None, Field(ge=256, le=2048, multiple_of=16)] = None,
                 seed: Annotated[int | None, Field(ge=0, lt=2 ** 31)] = None,
                 count: Annotated[int, Field(ge=1, le=generation.MAX_COUNT)] = 1,
                 steps: Annotated[int | None, Field(ge=1, le=50)] = None,
                 background: Literal["magenta", "green"] = "magenta",
                 wait_seconds: Annotated[int, Field(ge=0, le=MAX_WAIT)] = 60) -> dict:
        """Generate `count` images of `prompt` into `project` (one of yours, by id or name; default: your Scratch
        project). preset "sprite" for game sprites (give just the subject). No width/height: 1024², or the model's
        sprite size for sprite presets (512² for qwen-image-2512). Waits up to wait_seconds for them; look at
        a result with get_image."""
        def submit(s, user):
            p = find_project(s, user, project, write=True) if project else auth.ensure_default_project(s, user)
            arts = generation.create(s, scheduler, store, user, p, prompt=prompt, preset=preset, model=model,
                                     width=width, height=height, seed=seed, count=count, steps=steps,
                                     background=background, max_pending=settings.max_pending_per_user)
            return [a.id for a in arts]
        return wait_for(run(ctx, submit), wait_seconds)

    def wait_for(ids: list[str], wait_seconds: int) -> dict:
        deadline = time.monotonic() + wait_seconds
        while True:
            poller.poll_once()
            with db.session() as s:
                arts = [s.get(Artifact, i) for i in ids]
                if all(a.status not in generation.PENDING for a in arts) or time.monotonic() >= deadline:
                    pending = sum(a.status in generation.PENDING for a in arts)
                    out = {"artifacts": [brief(a, s) for a in arts]}
                    if pending:
                        out["note"] = (f"{pending} still queued or generating; check with get_artifact in a "
                                       "little while.")
                    return out
            time.sleep(1.0)

    @server.tool()
    def tweak(ctx: Context, artifact_id: str, op: Literal["reroll", "edit_prompt", "edit_image"],
              prompt: Annotated[str | None, Field(max_length=4000)] = None,
              seed: Annotated[int | None, Field(ge=0, lt=2 ** 31)] = None,
              count: Annotated[int, Field(ge=1, le=generation.MAX_COUNT)] = 1,
              model: str | None = None, project: str | None = None,
              wait_seconds: Annotated[int, Field(ge=0, le=MAX_WAIT)] = 60) -> dict:
        """New images made from an existing one, linked to it (made_from):
        - reroll: same recipe, new seed (random unless given). For "more like this".
        - edit_prompt: a changed prompt, same seed (unless given), same settings. For "the same but with ...".
        - edit_image: `prompt` is an instruction applied to the image itself, e.g. "same character, now holding an
          axe", "make it night time". Keeps the subject consistent; use it for variants of one character or object.
        Goes into `project` (one of yours), else the original's project if it's yours, else your Scratch project."""
        def submit(s, user):
            parent = access.artifact_for_view(s, artifact_id, user)
            if project:
                target = find_project(s, user, project, write=True)
            elif parent.project.owner_id == user.id:
                target = parent.project
            else:
                target = auth.ensure_default_project(s, user)
            arts = generation.tweak(s, scheduler, store, user, parent, target, op=op, prompt=prompt, seed=seed,
                                    count=count, model=model, max_pending=settings.max_pending_per_user)
            return [a.id for a in arts]
        return wait_for(run(ctx, submit), wait_seconds)

    @server.tool()
    def make_sprite(ctx: Context, artifact_id: str, size: Annotated[int, Field(ge=8, le=512)] = 64,
                    colors: Annotated[int, Field(ge=0, le=256)] = 24,
                    canvas: Literal["fit", "square"] = "fit") -> dict:
        """A pixel-grid version of one of your images: background removed (sprite presets), cropped, shrunk to `size`
        px (longest side for "fit"; a size×size tile with the subject standing on the bottom row for "square"), reduced
        to `colors` colours (0 keeps all). Same settings again replaces the earlier one. Returns the image's variants
        with download URLs."""
        def go(s, user):
            a = access.artifact_for_view(s, artifact_id, user)
            if a.project.owner_id != user.id:
                raise ToolError("only the project's owner can add versions; tweak (reroll) it into your own project "
                                "first")
            v = generation.make_pixel(a, store, sprites.PixelParams(size, colors, canvas), user)
            s.flush()
            out = brief(a, s)
            out["made"] = v.id
            return out
        return run(ctx, go)

    @server.tool()
    def get_artifact(ctx: Context, artifact_id: str) -> dict:
        """One image's status and recipe (prompt as given and as sent to the model, model, seed, size, steps), with
        its web page and, once done, a fresh download_url (no token needed, valid 10 minutes)."""
        def go(s, user):
            a = access.artifact_for_view(s, artifact_id, user)
            kids = list(s.scalars(select(Artifact).join(Project, Artifact.project_id == Project.id)
                                  .where(Artifact.parent_id == a.id, access.visible_projects_clause(user))
                                  .order_by(Artifact.created_at.desc()).limit(20)))
            return {**brief(a, s), "full_prompt": a.full_prompt, "steps": a.steps, "gpu_seconds": a.seconds,
                    "created_at": a.created_at.isoformat(),
                    "made_into": [{"id": k.id, "op": k.op, "status": k.status, "prompt": k.prompt} for k in kids]}
        poller.poll_once()
        return run(ctx, go)

    @server.tool()
    def get_image(ctx: Context, artifact_id: str, size: Literal["preview", "full"] = "preview",
                  variant: str | None = None) -> Image:
        """The image itself, so you can look at it. preview: scaled to fit 512 px (cheap); full: the original PNG
        (1024 px is ~1 MB). `variant`: one of its versions instead (a cutout or pixel sprite; pixel sprites are shown
        enlarged to the preview size so you can see them). To save a file, fetch a download_url instead."""
        def go(s, user):
            a = access.artifact_for_view(s, artifact_id, user)
            if a.status != "done" or not a.image_key:
                raise ToolError(f"no image yet: the artifact is {a.status}"
                                + (f" ({a.error})" if a.error else ""))
            key = a.image_key
            if variant:
                v = s.get(Variant, variant)
                if v is None or v.artifact_id != a.id:
                    raise ToolError("no such variant of this image (get_artifact lists them)")
                key = v.image_key
            try:
                data = b"".join(store.get(key))
            except KeyError:
                raise ToolError("the image is missing from storage") from None
            if size == "preview":
                from PIL import Image as PILImage
                im = PILImage.open(io.BytesIO(data))
                if variant and max(im.size) < PREVIEW_SIDE // 2:          # a small pixel sprite: enlarge, crisp
                    f = max(1, PREVIEW_SIDE // max(im.size))
                    im = im.resize((im.width * f, im.height * f), PILImage.NEAREST)
                    buf = io.BytesIO()
                    im.save(buf, format="PNG")
                    data = buf.getvalue()
                elif max(im.size) > PREVIEW_SIDE:
                    im.thumbnail((PREVIEW_SIDE, PREVIEW_SIDE),
                                 PILImage.NEAREST if a.preset in ("sprite", "item_sprite") else PILImage.LANCZOS)
                    buf = io.BytesIO()
                    im.save(buf, format="PNG")
                    data = buf.getvalue()
            return Image(data=data, format="png")
        return run(ctx, go)

    @server.tool()
    def list_artifacts(ctx: Context, query: Annotated[str | None, Field(max_length=200)] = None,
                       project: str | None = None, mine_only: bool = False,
                       limit: Annotated[int, Field(ge=1, le=100)] = 20,
                       before: str | None = None) -> dict:
        """Images you can see, newest first: filtered by words in the prompt (`query`), a project (id or name), or
        only ones you made. Page with `before` (the previous call's `next`). get_artifact gives a download link."""
        def go(s, user):
            q = select(Artifact).join(Project, Artifact.project_id == Project.id).where(
                access.visible_projects_clause(user))
            if project:
                q = q.where(Artifact.project_id == find_project(s, user, project, write=False).id)
            if mine_only:
                q = q.where(Artifact.created_by == user.id)
            for word in (query or "").split()[:8]:
                q = q.where(or_(Artifact.prompt.ilike(f"%{word}%"), Artifact.title.ilike(f"%{word}%")))
            if before:
                from datetime import datetime, timezone
                try:
                    cur = datetime.fromisoformat(before)
                except ValueError:
                    raise ToolError("before must be the `next` value of a previous call") from None
                if cur.tzinfo is not None and db.engine.dialect.name == "sqlite":
                    cur = cur.astimezone(timezone.utc).replace(tzinfo=None)
                q = q.where(Artifact.created_at < cur)
            rows = list(s.scalars(q.order_by(Artifact.created_at.desc()).limit(limit)))
            from .main import iso
            return {"artifacts": [brief(a, s, link=False) for a in rows],
                    "next": iso(rows[-1].created_at) if len(rows) == limit else None}
        return run(ctx, go)

    # --- character sheets (design §11, sheets.py) ---------------------------------------------------------------

    def sheet_view(s: Session, user: User, sheet_id: str, write: bool) -> Sheet:
        sh = s.get(Sheet, sheet_id)
        if sh is None or not access.can_view(sh.project, user):
            raise ToolError(f"no character sheet {sheet_id!r} you can see")
        if write and sh.project.owner_id != user.id:
            raise ToolError("only the project's owner can change its character sheets")
        return sh

    def sheet_brief(s: Session, sh: Sheet) -> dict:
        rows = []
        for slot in sheets.slots(s, sh):
            cands = []
            for c in sheets.candidates(s, slot):
                one = {"candidate_id": c.id, "frozen": c.frozen, "kept": c.kept}
                if c.held:
                    one["status"] = "waiting for room in your queue"
                elif c.artifact is None:
                    one["status"] = "deleted"
                else:
                    one.update(_brief(c.artifact, s, link=False))
                cands.append(one)
            rows.append({"row_id": slot.id, "kind": slot.kind, "label": slot.label, "candidates": cands})
        return {"sheet_id": sh.id, "name": sh.name, "project": sh.project.name, "model": sh.model,
                "variations_model": sheets.edit_model(sh), "base_artifact_id": sh.base_artifact_id,
                "pixel_size": sh.pixel_size, "pixel_colors": sh.pixel_colors, "rows": rows,
                "web_url": f"{base}/s/{sh.id}"}

    def sheet_wait(sheet_id: str, wait_seconds: int, ctx: Context) -> dict:
        deadline = time.monotonic() + wait_seconds
        while True:
            poller.poll_once()
            def look(s, user):
                sh = sheet_view(s, user, sheet_id, write=False)
                busy = any(c.held or (c.artifact is not None and c.artifact.status in generation.PENDING)
                           for slot in sheets.slots(s, sh) for c in sheets.candidates(s, slot))
                return busy, sheet_brief(s, sh)
            busy, out = run(ctx, look)
            if not busy or time.monotonic() >= deadline:
                if busy:
                    out["note"] = "some images are still being made; call get_sheet again in a little while"
                return out
            time.sleep(1.0)

    @server.tool()
    def create_sheet(ctx: Context, name: Annotated[str, Field(min_length=1, max_length=100)],
                     description: Annotated[str | None, Field(max_length=4000)] = None,
                     base_artifact_id: str | None = None, project: str | None = None,
                     model: str = catalogue.DEFAULT_MODEL, background: Literal["magenta", "green"] = "magenta",
                     wait_seconds: Annotated[int, Field(ge=0, le=MAX_WAIT)] = 60) -> dict:
        """Start a character sheet: one character in poses, expressions, weapon sets. From a `description` ("a goblin
        archer") it makes 4 base candidates (character sprite preset, `model`); or from `base_artifact_id`, an image
        that becomes the base. Then: sheet_mark to freeze the ones you like, sheet_reroll to replace the rest,
        sheet_set_base to pick the base, sheet_add_variations, sheet_mark keep, export_sheet. Variations are edits,
        so a Qwen base gets FLUX variations."""
        def go(s, user):
            b = access.artifact_for_view(s, base_artifact_id, user) if base_artifact_id else None
            if project:
                target = find_project(s, user, project, write=True)
            elif b is not None and b.project.owner_id == user.id:
                target = b.project
            else:
                target = auth.ensure_default_project(s, user)
            return sheets.new_sheet(s, scheduler, store, user, target, name=name, description=description, base=b,
                                    model=model, background=background,
                                    max_pending=settings.max_pending_per_user).id
        return sheet_wait(run(ctx, go), wait_seconds, ctx)

    @server.tool()
    def get_sheet(ctx: Context, sheet_id: str) -> dict:
        """A character sheet's rows (the base, then each variation) and their candidate images: status, frozen,
        kept. Look at a candidate with get_image."""
        return run(ctx, lambda s, user: sheet_brief(s, sheet_view(s, user, sheet_id, write=False)))

    @server.tool()
    def sheet_mark(ctx: Context, sheet_id: str, freeze: list[str] | None = None, unfreeze: list[str] | None = None,
                   keep: list[str] | None = None, unkeep: list[str] | None = None) -> dict:
        """Mark candidates (candidate_id values from get_sheet): freeze = keep it through re-rolls; keep = include it
        in the export (finished images only; any number per row)."""
        def go(s, user):
            sh = sheet_view(s, user, sheet_id, write=True)
            mine = {c.id: c for slot in sheets.slots(s, sh) for c in sheets.candidates(s, slot)}
            for ids, field, value in ((freeze, "frozen", True), (unfreeze, "frozen", False), (keep, "kept", True),
                                      (unkeep, "kept", False)):
                for cid in ids or []:
                    c = mine.get(cid)
                    if c is None:
                        raise ToolError(f"{cid!r} isn't a candidate on this sheet")
                    if field == "kept" and value and (c.artifact is None or c.artifact.status != "done"):
                        raise ToolError(f"{cid!r} isn't finished, so it can't be kept yet")
                    setattr(c, field, value)
            s.flush()
            return sheet_brief(s, sh)
        return run(ctx, go)

    @server.tool()
    def sheet_reroll(ctx: Context, sheet_id: str, row_id: str,
                     instruction: Annotated[str | None, Field(max_length=1000)] = None,
                     wait_seconds: Annotated[int, Field(ge=0, le=MAX_WAIT)] = 60) -> dict:
        """New images for a row's unfrozen candidates; `instruction` rewords the row first (the base's description, or
        the variation, e.g. "holding a short sword and a round shield")."""
        def go(s, user):
            sh = sheet_view(s, user, sheet_id, write=True)
            slot = s.get(SheetSlot, row_id)
            if slot is None or slot.sheet_id != sh.id:
                raise ToolError(f"{row_id!r} isn't a row of this sheet")
            sheets.reroll(s, scheduler, store, user, sh, slot, settings.max_pending_per_user, instruction)
        run(ctx, go)
        return sheet_wait(sheet_id, wait_seconds, ctx)

    @server.tool()
    def sheet_set_base(ctx: Context, sheet_id: str, candidate_id: str) -> dict:
        """Pick the base from the base row's finished candidates. Variations are edits of it, so it can't change once
        there are variations."""
        def go(s, user):
            sh = sheet_view(s, user, sheet_id, write=True)
            c = s.get(SheetCandidate, candidate_id)
            if c is None:
                raise ToolError(f"{candidate_id!r} isn't a candidate on this sheet")
            sheets.set_base(s, sh, c)
            s.flush()
            return sheet_brief(s, sh)
        return run(ctx, go)

    @server.tool()
    def sheet_add_variations(ctx: Context, sheet_id: str,
                             variations: Annotated[list[str], Field(min_length=1, max_length=sheets.MAX_SLOTS)],
                             wait_seconds: Annotated[int, Field(ge=0, le=MAX_WAIT)] = 60) -> dict:
        """Add rows of variations, one per item: poses ("running", "crouching"), expressions ("laughing"), weapon sets
        ("holding a war hammer"). Each gets 4 candidates, each an edit of the base. Rows beyond your queue limit wait
        and go out as earlier images finish."""
        def go(s, user):
            sh = sheet_view(s, user, sheet_id, write=True)
            sheets.add_variations(s, scheduler, store, user, sh, variations, settings.max_pending_per_user)
        run(ctx, go)
        return sheet_wait(sheet_id, wait_seconds, ctx)

    @server.tool()
    def export_sheet(ctx: Context, sheet_id: str, pixel_size: Annotated[int | None, Field(ge=8, le=512)] = None,
                     pixel_colors: Annotated[int | None, Field(ge=0, le=256)] = None) -> dict:
        """Zip the kept images: each one's transparent cutout and a pixel sprite on a pixel_size² tile (standing on
        the bottom row), all at the base's scale so sizes compare. Returns download_url (no token needed, 10 minutes)
        and `shrunk`: sprites that were too big for the tile at that scale and were fitted instead."""
        def go(s, user):
            sh = sheet_view(s, user, sheet_id, write=pixel_size is not None or pixel_colors is not None)
            if pixel_size is not None:
                sh.pixel_size = pixel_size
            if pixel_colors is not None:
                if pixel_colors == 1:
                    raise ToolError("pixel_colors must be 0 (all) or 2-256")
                sh.pixel_colors = pixel_colors
            _key, files, shrunk = sheets.export(s, sh, store)
            path, expires = sheets.link(s, sh)
            return {"download_url": base + path, "download_expires_at": expires, "files": files, "shrunk": shrunk}
        return run(ctx, go)

    host = settings.public_url.split("://", 1)[-1].split("/", 1)[0]
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[host, "localhost", "localhost:*", "127.0.0.1:*", "testserver"],
        allowed_origins=[base, "http://localhost:*", "http://127.0.0.1:*"])
    server.streamable_http_app(streamable_http_path="/mcp", json_response=True, stateless_http=True,
                               transport_security=security)
    return server, McpAuth(StreamableHTTPASGIApp(server.session_manager), db, settings.public_url)
