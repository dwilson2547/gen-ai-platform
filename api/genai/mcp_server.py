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

from . import __version__, access, auth, catalogue, generation, links, tokens
from .config import Settings
from .db import Database
from .models import Artifact, Project, User
from .scheduler import Scheduler
from .storage import Store

INSTRUCTIONS = """\
genai generates images (FLUX.2 klein, on a shared GPU) and keeps them in projects, with the prompt and settings that
made each one. You act for the signed-in user who connected you, with their permissions.

- generate makes images from a prompt. preset "sprite" wraps the prompt in a game-sprite template (pixel art, one
  centred subject on a flat magenta background): give it just the subject, e.g. "a goblin archer". model
  "flux2-klein-4b" is the default (fast, Apache-2.0); "flux2-klein-9b" is better at hard prompts but about twice as slow
  and under a NON-COMMERCIAL licence: its images may not be used in a commercial game. Say so if you pick it.
- generate waits up to wait_seconds for the images; anything still pending comes back with its status. Check later with
  get_artifact. A 4B image takes ~3 s once the model is loaded; the first one after a model swap can take longer.
- Images go into a project: one of the user's own (by id or name), or their private "Scratch" project when none is
  given. list_projects shows them; create_project makes a new one (private unless the user asked otherwise).
  Others' public projects are readable but you can't add to them.
- get_image returns the picture itself so you can look at it (preview: scaled to 512 px; full: the original PNG, large).
  To save the full-size file, fetch download_url (e.g. `curl -fo assets/mage.png "<download_url>"`): it needs no token
  and expires after 10 minutes (download_expires_at); call get_artifact again for a fresh one.
- The same seed with the same prompt and settings reproduces an image (on the same GPU); vary the seed to explore.
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
                 width: Annotated[int, Field(ge=256, le=2048, multiple_of=16)] = 1024,
                 height: Annotated[int, Field(ge=256, le=2048, multiple_of=16)] = 1024,
                 seed: Annotated[int | None, Field(ge=0, lt=2 ** 31)] = None,
                 count: Annotated[int, Field(ge=1, le=generation.MAX_COUNT)] = 1,
                 steps: Annotated[int | None, Field(ge=1, le=50)] = None,
                 wait_seconds: Annotated[int, Field(ge=0, le=MAX_WAIT)] = 60) -> dict:
        """Generate `count` images of `prompt` into `project` (one of yours, by id or name; default: your Scratch
        project). preset "sprite" for game sprites (give just the subject). Waits up to wait_seconds for them; look at
        a result with get_image."""
        def submit(s, user):
            p = find_project(s, user, project, write=True) if project else auth.ensure_default_project(s, user)
            arts = generation.create(s, scheduler, store, user, p, prompt=prompt, preset=preset, model=model,
                                     width=width, height=height, seed=seed, count=count, steps=steps,
                                     max_pending=settings.max_pending_per_user)
            return [a.id for a in arts]
        ids = run(ctx, submit)
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
    def get_artifact(ctx: Context, artifact_id: str) -> dict:
        """One image's status and recipe (prompt as given and as sent to the model, model, seed, size, steps), with
        its web page and, once done, a fresh download_url (no token needed, valid 10 minutes)."""
        def go(s, user):
            a = access.artifact_for_view(s, artifact_id, user)
            return {**brief(a, s), "full_prompt": a.full_prompt, "steps": a.steps, "gpu_seconds": a.seconds,
                    "created_at": a.created_at.isoformat()}
        poller.poll_once()
        return run(ctx, go)

    @server.tool()
    def get_image(ctx: Context, artifact_id: str, size: Literal["preview", "full"] = "preview") -> Image:
        """The image itself, so you can look at it. preview: scaled to fit 512 px (cheap); full: the original PNG
        (1024 px is ~1 MB). To save a file, fetch download_url from generate or get_artifact instead."""
        def go(s, user):
            a = access.artifact_for_view(s, artifact_id, user)
            if a.status != "done" or not a.image_key:
                raise ToolError(f"no image yet: the artifact is {a.status}"
                                + (f" ({a.error})" if a.error else ""))
            try:
                data = b"".join(store.get(a.image_key))
            except KeyError:
                raise ToolError("the image is missing from storage") from None
            if size == "preview":
                from PIL import Image as PILImage
                im = PILImage.open(io.BytesIO(data))
                if max(im.size) > PREVIEW_SIDE:
                    im.thumbnail((PREVIEW_SIDE, PREVIEW_SIDE),
                                 PILImage.NEAREST if a.preset == "sprite" else PILImage.LANCZOS)
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

    host = settings.public_url.split("://", 1)[-1].split("/", 1)[0]
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[host, "localhost", "localhost:*", "127.0.0.1:*", "testserver"],
        allowed_origins=[base, "http://localhost:*", "http://127.0.0.1:*"])
    server.streamable_http_app(streamable_http_path="/mcp", json_response=True, stateless_http=True,
                               transport_security=security)
    return server, McpAuth(StreamableHTTPASGIApp(server.session_manager), db, settings.public_url)
