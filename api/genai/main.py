"""HTTP API and the built UI (design §7). Everything but sign-in, health and config needs a session."""

from __future__ import annotations

import contextlib
import logging
import os
import re
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from urllib.parse import quote, urlsplit

from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, StreamingResponse
from mcp.server.auth.routes import create_auth_routes, create_protected_resource_routes
from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions
from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator
from starlette.routing import Route
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import __version__, access, auth, catalogue, generation, links, mcp_server, oauth, oidc, sheets, sprites, storage, tokens
from .config import Settings
from .db import Database
from .models import AccessToken, Artifact, OAuthGrant, Project, Sheet, SheetCandidate, SheetSlot, User, Variant
from .scheduler import HttpScheduler, LocalSdServer, Scheduler, Unconfigured

log = logging.getLogger("genai")

CSP = ("default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; script-src 'self'; "
       "connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")


NEXT_COOKIE = "genai_next"


class _Issuer(BaseModel):
    # RFC 8414 clients compare the issuer exactly; keep it free of a trailing slash.
    model_config = ConfigDict(url_preserve_empty_path=True)
    u: AnyHttpUrl


class NameIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class DevLoginIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=2000)
    visibility: str = "private"

    @field_validator("visibility")
    @classmethod
    def _vis(cls, v: str) -> str:
        if v not in ("private", "public"):
            raise ValueError("visibility is private or public")
        return v


class ProjectPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=2000)
    visibility: str | None = None

    @field_validator("visibility")
    @classmethod
    def _vis(cls, v: str | None) -> str | None:
        if v is not None and v not in ("private", "public"):
            raise ValueError("visibility is private or public")
        return v


class GenerateIn(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)
    project_id: str | None = None
    preset: str = catalogue.DEFAULT_PRESET
    model: str = catalogue.DEFAULT_MODEL
    width: int | None = Field(default=None, ge=256, le=2048, multiple_of=16)    # None: the model's/preset's default
    height: int | None = Field(default=None, ge=256, le=2048, multiple_of=16)
    seed: int | None = Field(default=None, ge=0, lt=2 ** 31)
    count: int = Field(default=1, ge=1, le=generation.MAX_COUNT)
    steps: int | None = Field(default=None, ge=1, le=50)
    background: str = catalogue.DEFAULT_BACKGROUND


class PixelIn(BaseModel):
    size: int = Field(default=64, ge=8, le=512)
    colors: int = Field(default=24, ge=0, le=256)
    canvas: str = "fit"


def iso(dt: datetime | None) -> str | None:
    """UTC ISO-8601 with an offset. SQLite hands back naive datetimes (stored as UTC); without the offset a browser
    reads them as local time."""
    if dt is None:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).isoformat()


class TweakIn(BaseModel):
    op: str
    prompt: str | None = Field(default=None, max_length=4000)
    seed: int | None = Field(default=None, ge=0, lt=2 ** 31)
    count: int = Field(default=1, ge=1, le=generation.MAX_COUNT)
    model: str | None = None
    project_id: str | None = None


def project_out(p: Project, user: User, count: int = 0, cover: str | None = None) -> dict:
    return {"id": p.id, "name": p.name, "description": p.description, "visibility": p.visibility,
            "is_default": p.is_default, "owner": p.owner.username, "mine": p.owner_id == user.id,
            "created_at": iso(p.created_at), "artifacts": count,
            "cover_url": f"/api/artifacts/{cover}/image" if cover else None}


def variant_out(v: Variant) -> dict:
    return {"id": v.id, "kind": v.kind, "params": v.params, "width": v.width, "height": v.height, "bytes": v.bytes,
            "image_url": f"/api/variants/{v.id}/image", "created_at": iso(v.created_at)}


def artifact_out(a: Artifact) -> dict:
    m = catalogue.MODELS.get(a.model)
    return {
        "id": a.id, "project": {"id": a.project.id, "name": a.project.name, "visibility": a.project.visibility},
        "by": a.creator.username, "created_at": iso(a.created_at),
        "prompt": a.prompt, "full_prompt": a.full_prompt, "preset": a.preset,
        "model": a.model, "model_label": m.label if m else a.model, "commercial": m.commercial if m else None,
        "seed": a.seed, "width": a.width, "height": a.height, "steps": a.steps, "cfg_scale": a.cfg_scale,
        "parent_id": a.parent_id, "op": a.op, "status": a.status, "error": a.error, "title": a.title,
        "ran_model": a.ran_model, "worker": a.worker, "seconds": a.seconds, "bytes": a.bytes,
        "finished_at": iso(a.finished_at),
        "image_url": f"/api/artifacts/{a.id}/image" if a.status == "done" else None,
        "background": catalogue.background_of(a.full_prompt) if a.preset in catalogue.SPRITE_PRESETS else None,
    }


class SheetIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=4000)
    base_artifact_id: str | None = None
    project_id: str | None = None
    model: str = catalogue.DEFAULT_MODEL
    background: str = catalogue.DEFAULT_BACKGROUND


class SheetPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    pixel_size: int | None = Field(default=None, ge=8, le=512)
    pixel_colors: int | None = Field(default=None, ge=0, le=256)


class LinesIn(BaseModel):
    lines: list[str] = Field(min_length=1, max_length=sheets.MAX_SLOTS)


class RerollIn(BaseModel):
    instruction: str | None = Field(default=None, max_length=1000)


class CandidatePatch(BaseModel):
    frozen: bool | None = None
    kept: bool | None = None


class BaseIn(BaseModel):
    candidate_id: str


def sheet_brief(sh: Sheet, user: User, kept: int = 0) -> dict:
    m = catalogue.MODELS.get(sh.model)
    return {"id": sh.id, "name": sh.name, "description": sh.description, "model": sh.model,
            "model_label": m.label if m else sh.model, "edit_model": sheets.edit_model(sh),
            "background": sh.background, "by": sh.creator.username, "created_at": iso(sh.created_at),
            "project": {"id": sh.project.id, "name": sh.project.name, "visibility": sh.project.visibility},
            "base_artifact_id": sh.base_artifact_id,
            "cover_url": f"/api/artifacts/{sh.base_artifact_id}/image" if sh.base_artifact_id else None,
            "pixel_size": sh.pixel_size, "pixel_colors": sh.pixel_colors, "kept": kept,
            "can_edit": sh.project.owner_id == user.id}


def sheet_out(s: Session, sh: Sheet, user: User) -> dict:
    out = []
    kept = 0
    for slot in sheets.slots(s, sh):
        cands = []
        for c in sheets.candidates(s, slot):
            kept += c.kept
            cands.append({"id": c.id, "position": c.position, "frozen": c.frozen, "kept": c.kept, "held": c.held,
                          "artifact": artifact_out(c.artifact) if c.artifact is not None else None})
        out.append({"id": slot.id, "kind": slot.kind, "label": slot.label, "instruction": slot.instruction,
                    "position": slot.position, "candidates": cands})
    return {**sheet_brief(sh, user, kept), "slots": out}


def create_app(settings: Settings | None = None, provider: oidc.Provider | None = None,
               store: storage.Store | None = None, scheduler: Scheduler | None = None) -> FastAPI:
    """`store` / `scheduler` override what the settings name (tests pass fakes)."""
    settings = settings or Settings.from_env()
    db = Database(settings.database_url)
    db.create_schema()
    if provider is None and settings.oidc_enabled:
        provider = oidc.Provider(settings)
    if settings.dev_login and not settings.dev_login_enabled:
        log.warning("DEV_LOGIN ignored: PUBLIC_URL is https")
    store = store if store is not None else storage.from_settings(settings)
    if scheduler is None:
        if settings.scheduler_url:
            scheduler = HttpScheduler(settings.scheduler_url, settings.scheduler_token)
        elif settings.dev_sd_server and not settings.secure:
            scheduler = LocalSdServer(settings.dev_sd_server)
        else:
            scheduler = Unconfigured()
    poller = generation.Poller(db, scheduler, store, settings.poll_seconds, settings.max_pending_per_user)
    mcp, mcp_app = mcp_server.build(db, settings, scheduler, store, poller)

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        if settings.background_poll:
            poller.start()
        try:
            async with mcp.session_manager.run():
                yield
        finally:
            poller.stop()

    app = FastAPI(title="gen-ai-platform", version=__version__, docs_url=None, redoc_url=None, openapi_url=None,
                  lifespan=lifespan)
    app.state.settings, app.state.db, app.state.poller, app.state.store = settings, db, poller, store

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("Content-Security-Policy", CSP)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("X-Robots-Tag", "noindex, nofollow")
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    def session():
        with db.session() as s:
            yield s

    def user_dep(request: Request, s: Session = Depends(session)) -> User:
        return auth.require_user(s, request, settings)

    def gen_error(fn, *a, **kw):
        try:
            return fn(*a, **kw)
        except generation.GenerateError as e:
            raise HTTPException(e.status, str(e)) from None

    # --- meta and auth ----------------------------------------------------------------------------------------

    @app.get("/api/health")
    def health():
        return {"ok": True, "version": __version__}

    @app.get("/api/config")
    def public_config():
        """What the splash page needs before sign-in. Nothing about the catalogue or content."""
        return {"oidc": provider is not None, "dev_login": settings.dev_login_enabled, "version": __version__}

    @app.get("/api/me")
    def me(user: User = Depends(user_dep)):
        return {"id": user.id, "username": user.username, "display_name": user.display_name}

    @app.get("/api/catalogue")
    def get_catalogue(user: User = Depends(user_dep)):
        return catalogue.public()

    @app.post("/api/auth/logout")
    def logout(request: Request, response: Response, s: Session = Depends(session)):
        if request.headers.get(auth.CSRF_HEADER) != "1":
            raise HTTPException(403, "Missing X-Genai header")
        auth.end_session(s, request, settings, response)
        return {"ok": True}

    def safe_next(value: str) -> str:
        # Only the OAuth consent page needs a way back after sign-in.
        return value if value.startswith(oauth.CONSENT_PATH + "?") and len(value) < 300 else ""

    @app.get(oidc.FLOW_PATH + "/login")
    def oidc_login(next: str = ""):
        if provider is None:
            raise HTTPException(404, "Sign-in is not set up on this server")
        try:
            url, flow = oidc.begin(provider, settings)
        except Exception as exc:  # noqa: BLE001
            log.warning("oidc: discovery failed: %s", exc)
            raise HTTPException(502, "Cannot reach the sign-in server") from None
        res = RedirectResponse(url, status_code=302)
        res.set_cookie(oidc.FLOW_COOKIE, flow, max_age=oidc.FLOW_TTL_S, path=oidc.FLOW_PATH,
                       httponly=True, secure=settings.secure, samesite="lax")
        if safe_next(next):
            res.set_cookie(NEXT_COOKIE, next, max_age=oidc.FLOW_TTL_S, path=oidc.FLOW_PATH,
                           httponly=True, secure=settings.secure, samesite="lax")
        return res

    @app.get(oidc.FLOW_PATH + "/callback")
    def oidc_callback(request: Request, code: str = "", state: str = "", error: str = "",
                      s: Session = Depends(session)):
        if provider is None:
            raise HTTPException(404, "Sign-in is not set up on this server")
        res = RedirectResponse(safe_next(request.cookies.get(NEXT_COOKIE, "")) or "/", status_code=302)
        res.delete_cookie(oidc.FLOW_COOKIE, path=oidc.FLOW_PATH)
        res.delete_cookie(NEXT_COOKIE, path=oidc.FLOW_PATH)
        if error:
            return RedirectResponse("/?signin_error=" + re.sub(r"[^a-z_]", "", error)[:40], 302)
        try:
            claims = oidc.finish(provider, settings, request.cookies.get(oidc.FLOW_COOKIE, ""), code, state)
            user = oidc.resolve_user(s, claims, settings.user_group)
            auth.start_session(s, user, settings, res)
        except oidc.SignInRefused as e:
            return RedirectResponse(f"/?signin_error={e.code}", 302)
        except Exception:  # noqa: BLE001
            log.exception("oidc: callback failed")
            return RedirectResponse("/?signin_error=failed", 302)
        return res

    if settings.dev_login_enabled:
        @app.post("/api/auth/dev-login")
        def dev_login(body: DevLoginIn, request: Request, response: Response, s: Session = Depends(session)):
            if request.headers.get(auth.CSRF_HEADER) != "1":
                raise HTTPException(403, "Missing X-Genai header")
            name = auth.USERNAME_RE.sub("", body.name.lower())[:60] or "dev"
            user = s.scalar(select(User).where(User.username == name)) or auth.create_user(s, name)
            auth.start_session(s, user, settings, response)
            return {"id": user.id, "username": user.username}

    # --- projects ---------------------------------------------------------------------------------------------

    def project_stats(s: Session, ids: list[str]) -> tuple[dict, dict]:
        if not ids:
            return {}, {}
        counts = dict(s.execute(select(Artifact.project_id, func.count()).where(Artifact.project_id.in_(ids))
                                .group_by(Artifact.project_id)).all())
        covers = {}
        for pid in ids:          # newest finished image per project; projects are few
            covers[pid] = s.scalar(select(Artifact.id).where(Artifact.project_id == pid, Artifact.status == "done")
                                   .order_by(Artifact.created_at.desc()).limit(1))
        return counts, covers

    @app.get("/api/projects")
    def list_projects(user: User = Depends(user_dep), s: Session = Depends(session)):
        auth.ensure_default_project(s, user)
        ps = list(s.scalars(select(Project).where(access.visible_projects_clause(user))
                            .order_by(Project.is_default.desc(), Project.created_at.desc())))
        counts, covers = project_stats(s, [p.id for p in ps])
        out = [project_out(p, user, counts.get(p.id, 0), covers.get(p.id)) for p in ps]
        return {"mine": [p for p in out if p["mine"]], "public": [p for p in out if not p["mine"]]}

    @app.post("/api/projects", status_code=201)
    def create_project(body: ProjectIn, user: User = Depends(user_dep), s: Session = Depends(session)):
        name = body.name.strip()
        if s.scalar(select(Project.id).where(Project.owner_id == user.id, Project.name == name)):
            raise HTTPException(409, "You already have a project with that name")
        p = Project(owner_id=user.id, name=name, description=body.description.strip(), visibility=body.visibility)
        s.add(p)
        s.flush()
        return project_out(p, user)

    @app.get("/api/projects/{project_id}")
    def get_project(project_id: str, user: User = Depends(user_dep), s: Session = Depends(session)):
        p = access.project_for_view(s, project_id, user)
        counts, covers = project_stats(s, [p.id])
        return project_out(p, user, counts.get(p.id, 0), covers.get(p.id))

    @app.patch("/api/projects/{project_id}")
    def update_project(project_id: str, body: ProjectPatch, user: User = Depends(user_dep),
                       s: Session = Depends(session)):
        p = access.project_for_write(s, project_id, user)
        if body.name is not None and body.name.strip() != p.name:
            if s.scalar(select(Project.id).where(Project.owner_id == user.id, Project.name == body.name.strip())):
                raise HTTPException(409, "You already have a project with that name")
            p.name = body.name.strip()
        if body.description is not None:
            p.description = body.description.strip()
        if body.visibility is not None:
            p.visibility = body.visibility
        return project_out(p, user)

    # --- generation and artifacts -----------------------------------------------------------------------------

    @app.post("/api/generate", status_code=202)
    def generate(body: GenerateIn, user: User = Depends(user_dep), s: Session = Depends(session)):
        project = (access.project_for_write(s, body.project_id, user) if body.project_id
                   else auth.ensure_default_project(s, user))
        arts = gen_error(generation.create, s, scheduler, store, user, project, prompt=body.prompt,
                         preset=body.preset, model=body.model, width=body.width, height=body.height,
                         seed=body.seed, count=body.count, steps=body.steps, background=body.background,
                         max_pending=settings.max_pending_per_user)
        s.flush()
        for a in arts:
            s.refresh(a)
        return {"artifacts": [artifact_out(a) for a in arts]}

    @app.get("/api/artifacts")
    def list_artifacts(project_id: str | None = None, before: str | None = None, limit: int = 48,
                       user: User = Depends(user_dep), s: Session = Depends(session)):
        """A project's artifacts, or (no project) everything the user made, newest first. `before` pages by
        created_at."""
        q = select(Artifact)
        if project_id:
            access.project_for_view(s, project_id, user)
            q = q.where(Artifact.project_id == project_id)
        else:
            q = q.where(Artifact.created_by == user.id)
        if before:
            try:
                cursor = datetime.fromisoformat(before)
            except ValueError:
                raise HTTPException(422, "The before cursor must be an ISO timestamp") from None
            if cursor.tzinfo is not None and db.engine.dialect.name == "sqlite":
                cursor = cursor.astimezone(timezone.utc).replace(tzinfo=None)   # SQLite stores naive UTC
            q = q.where(Artifact.created_at < cursor)
        rows = list(s.scalars(q.order_by(Artifact.created_at.desc()).limit(max(1, min(limit, 100)))))
        return {"artifacts": [artifact_out(a) for a in rows],
                "next": iso(rows[-1].created_at) if len(rows) == max(1, min(limit, 100)) else None}

    def lineage(s: Session, a: Artifact, user: User) -> dict:
        """The image this one was made from and the ones made from it, as far as the user can see them."""
        parent = s.get(Artifact, a.parent_id) if a.parent_id else None
        if parent is not None and not access.can_view(parent.project, user):
            parent = None
        kids = list(s.scalars(select(Artifact).join(Project, Artifact.project_id == Project.id)
                              .where(Artifact.parent_id == a.id, access.visible_projects_clause(user))
                              .order_by(Artifact.created_at.desc()).limit(48)))
        return {"parent": artifact_out(parent) if parent else None, "children": [artifact_out(k) for k in kids]}

    @app.get("/api/artifacts/{artifact_id}")
    def get_artifact(artifact_id: str, user: User = Depends(user_dep), s: Session = Depends(session)):
        a = access.artifact_for_view(s, artifact_id, user)
        vs = s.scalars(select(Variant).where(Variant.artifact_id == a.id).order_by(Variant.created_at))
        return {**artifact_out(a), **lineage(s, a, user), "variants": [variant_out(v) for v in vs],
                "can_edit": a.project.owner_id == user.id}

    def tweak_target(s: Session, user: User, parent: Artifact, project_id: str | None) -> Project:
        """Where a tweak goes: the project asked for (one of yours), else the parent's if it is yours, else Scratch."""
        if project_id:
            return access.project_for_write(s, project_id, user)
        if parent.project.owner_id == user.id:
            return parent.project
        return auth.ensure_default_project(s, user)

    @app.post("/api/artifacts/{artifact_id}/tweak", status_code=202)
    def tweak(artifact_id: str, body: TweakIn, user: User = Depends(user_dep), s: Session = Depends(session)):
        parent = access.artifact_for_view(s, artifact_id, user)
        arts = gen_error(generation.tweak, s, scheduler, store, user, parent,
                         tweak_target(s, user, parent, body.project_id), op=body.op, prompt=body.prompt,
                         seed=body.seed, count=body.count, model=body.model,
                         max_pending=settings.max_pending_per_user)
        s.flush()
        for a in arts:
            s.refresh(a)
        return {"artifacts": [artifact_out(a) for a in arts]}

    @app.delete("/api/artifacts/{artifact_id}")
    def delete_artifact(artifact_id: str, user: User = Depends(user_dep), s: Session = Depends(session)):
        a = access.artifact_for_view(s, artifact_id, user)
        if a.project.owner_id != user.id:
            raise HTTPException(403, "Only the project's owner can delete from it")
        for v in s.scalars(select(Variant).where(Variant.artifact_id == a.id)):
            try:
                store.delete(v.image_key)
            except Exception:  # noqa: BLE001
                log.warning("could not delete %s from the store", v.image_key)
        if a.image_key:
            try:
                store.delete(a.image_key)
            except Exception:  # noqa: BLE001 — a missing object must not keep the row
                log.warning("could not delete %s from the store", a.image_key)
        s.delete(a)
        return {"ok": True}

    @app.get("/api/artifacts/{artifact_id}/image")
    def artifact_image(artifact_id: str, request: Request, download: bool = False, s: Session = Depends(session)):
        """Cookie session (the UI), or an agent's bearer token (personal or OAuth) so it can save the full file."""
        raw = tokens.bearer(request.headers.get("authorization"))
        if raw:
            who = tokens.principal(s, raw)
            if who is None:
                raise HTTPException(401, "The token is missing, expired or revoked")
            user = who.user
        else:
            user = auth.require_user(s, request, settings)
        a = access.artifact_for_view(s, artifact_id, user)
        if a.status != "done" or not a.image_key:
            raise HTTPException(404, "No image yet")
        try:
            body = store.get(a.image_key)
        except KeyError:
            raise HTTPException(404, "The image is missing from storage") from None
        headers = {"Cache-Control": "private, max-age=86400, immutable"}      # an artifact's image never changes
        if download:
            headers["Content-Disposition"] = f'attachment; filename="{links.filename(a)}"'
        return StreamingResponse(body, media_type="image/png", headers=headers)

    @app.get(links.PATH + "{token}")
    def download_link(token: str, variant: str | None = None, s: Session = Depends(session)):
        """A short-lived link handed to agents (links.py): the PNG, no other auth. `variant` picks one of the image's
        versions (the link grants the image, so any of its versions)."""
        if token.startswith(sheets.LINK_PREFIX):
            sh = sheets.resolve_link(s, token)
            if sh is None:
                raise HTTPException(404, "This download link is unknown or has expired; export again")
            try:
                body = store.get(f"sheets/{sh.id}/export.zip")
            except KeyError:
                raise HTTPException(404, "The export is missing; export again") from None
            return StreamingResponse(body, media_type="application/zip", headers={
                "Content-Disposition": f'attachment; filename="{sheets.zip_name(sh)}"', "Cache-Control": "no-store"})
        a = links.resolve(s, token)
        if a is None:
            raise HTTPException(404, "This download link is unknown or has expired; ask for a new one")
        key, name = a.image_key, links.filename(a)
        if variant:
            v = s.get(Variant, variant)
            if v is None or v.artifact_id != a.id:
                raise HTTPException(404, "No such version of this image")
            key, name = v.image_key, links.filename(a, v)
        try:
            body = store.get(key)
        except KeyError:
            raise HTTPException(404, "The image is missing from storage") from None
        return StreamingResponse(body, media_type="image/png", headers={
            "Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})

    # --- variants: cutouts and pixel-grid versions (sprites.py) -----------------------------------------------

    @app.post("/api/artifacts/{artifact_id}/variants", status_code=201)
    def make_variant(artifact_id: str, body: PixelIn, user: User = Depends(user_dep), s: Session = Depends(session)):
        a = access.artifact_for_view(s, artifact_id, user)
        if a.project.owner_id != user.id:
            raise HTTPException(403, "Only the project's owner can add versions; re-roll it into your own project first")
        v = gen_error(generation.make_pixel, a, store, sprites.PixelParams(body.size, body.colors, body.canvas), user)
        s.flush()
        return variant_out(v)

    @app.get("/api/variants/{variant_id}/image")
    def variant_image(variant_id: str, request: Request, download: bool = False, s: Session = Depends(session)):
        raw = tokens.bearer(request.headers.get("authorization"))
        if raw:
            who = tokens.principal(s, raw)
            if who is None:
                raise HTTPException(401, "The token is missing, expired or revoked")
            user = who.user
        else:
            user = auth.require_user(s, request, settings)
        v = s.get(Variant, variant_id)
        if v is None or not access.can_view(v.artifact.project, user):
            raise HTTPException(404, "No such version")
        try:
            body = store.get(v.image_key)
        except KeyError:
            raise HTTPException(404, "The image is missing from storage") from None
        headers = {"Cache-Control": "private, max-age=86400"}
        if download:
            headers["Content-Disposition"] = f'attachment; filename="{links.filename(v.artifact, v)}"'
        return StreamingResponse(body, media_type="image/png", headers=headers)

    @app.delete("/api/variants/{variant_id}")
    def delete_variant(variant_id: str, user: User = Depends(user_dep), s: Session = Depends(session)):
        v = s.get(Variant, variant_id)
        if v is None or not access.can_view(v.artifact.project, user):
            raise HTTPException(404, "No such version")
        if v.artifact.project.owner_id != user.id:
            raise HTTPException(403, "Only the project's owner can delete versions")
        try:
            store.delete(v.image_key)
        except Exception:  # noqa: BLE001
            log.warning("could not delete %s from the store", v.image_key)
        s.delete(v)
        return {"ok": True}

    # --- agents: personal access tokens and connected apps (cookie session only) ------------------------------

    # --- character sheets (design §11, sheets.py) ---------------------------------------------------------------

    def sheet_for_view(s: Session, sheet_id: str, user: User) -> Sheet:
        sh = s.get(Sheet, sheet_id)
        if sh is None or not access.can_view(sh.project, user):
            raise HTTPException(404, "No such character sheet")
        return sh

    def sheet_for_write(s: Session, sheet_id: str, user: User) -> Sheet:
        sh = sheet_for_view(s, sheet_id, user)
        if sh.project.owner_id != user.id:
            raise HTTPException(403, "Only the project's owner can change its character sheets")
        return sh

    def slot_of(s: Session, sh: Sheet, slot_id: str) -> SheetSlot:
        slot = s.get(SheetSlot, slot_id)
        if slot is None or slot.sheet_id != sh.id:
            raise HTTPException(404, "No such row on this sheet")
        return slot

    def sheet_reply(s: Session, sh: Sheet, user: User) -> dict:
        s.flush()
        s.expire_all()
        return sheet_out(s, s.get(Sheet, sh.id), user)

    @app.get("/api/sheets")
    def list_sheets(project_id: str | None = None, user: User = Depends(user_dep), s: Session = Depends(session)):
        q = select(Sheet).join(Project, Sheet.project_id == Project.id).where(access.visible_projects_clause(user))
        if project_id:
            q = q.where(Sheet.project_id == access.project_for_view(s, project_id, user).id)
        else:
            q = q.where(Sheet.created_by == user.id)
        rows = list(s.scalars(q.order_by(Sheet.created_at.desc()).limit(200)))
        kept = sheets.counts(s, [r.id for r in rows])
        return {"sheets": [sheet_brief(r, user, kept.get(r.id, 0)) for r in rows]}

    @app.post("/api/sheets", status_code=201)
    def create_sheet(body: SheetIn, user: User = Depends(user_dep), s: Session = Depends(session)):
        base = access.artifact_for_view(s, body.base_artifact_id, user) if body.base_artifact_id else None
        if body.project_id:
            project = access.project_for_write(s, body.project_id, user)
        elif base is not None and base.project.owner_id == user.id:
            project = base.project
        else:
            project = auth.ensure_default_project(s, user)
        sh = gen_error(sheets.new_sheet, s, scheduler, store, user, project, name=body.name,
                       description=body.description, base=base, model=body.model, background=body.background,
                       max_pending=settings.max_pending_per_user)
        return sheet_reply(s, sh, user)

    @app.get("/api/sheets/{sheet_id}")
    def get_sheet(sheet_id: str, user: User = Depends(user_dep), s: Session = Depends(session)):
        return sheet_out(s, sheet_for_view(s, sheet_id, user), user)

    @app.patch("/api/sheets/{sheet_id}")
    def update_sheet(sheet_id: str, body: SheetPatch, user: User = Depends(user_dep), s: Session = Depends(session)):
        sh = sheet_for_write(s, sheet_id, user)
        if body.name is not None:
            sh.name = body.name.strip()
        if body.pixel_size is not None:
            sh.pixel_size = body.pixel_size
        if body.pixel_colors is not None:
            if body.pixel_colors == 1:
                raise HTTPException(422, "Colours must be 0 (all) or 2–256")
            sh.pixel_colors = body.pixel_colors
        return sheet_reply(s, sh, user)

    @app.delete("/api/sheets/{sheet_id}")
    def delete_sheet(sheet_id: str, user: User = Depends(user_dep), s: Session = Depends(session)):
        """The sheet goes; its images stay in the project."""
        s.delete(sheet_for_write(s, sheet_id, user))
        return {"ok": True}

    @app.post("/api/sheets/{sheet_id}/base")
    def set_sheet_base(sheet_id: str, body: BaseIn, user: User = Depends(user_dep), s: Session = Depends(session)):
        sh = sheet_for_write(s, sheet_id, user)
        c = s.get(SheetCandidate, body.candidate_id)
        if c is None:
            raise HTTPException(404, "No such image on this sheet")
        gen_error(sheets.set_base, s, sh, c)
        return sheet_reply(s, sh, user)

    @app.post("/api/sheets/{sheet_id}/slots", status_code=201)
    def add_sheet_slots(sheet_id: str, body: LinesIn, user: User = Depends(user_dep), s: Session = Depends(session)):
        sh = sheet_for_write(s, sheet_id, user)
        gen_error(sheets.add_variations, s, scheduler, store, user, sh, body.lines, settings.max_pending_per_user)
        return sheet_reply(s, sh, user)

    @app.post("/api/sheets/{sheet_id}/slots/{slot_id}/reroll", status_code=202)
    def reroll_sheet_slot(sheet_id: str, slot_id: str, body: RerollIn, user: User = Depends(user_dep),
                          s: Session = Depends(session)):
        sh = sheet_for_write(s, sheet_id, user)
        gen_error(sheets.reroll, s, scheduler, store, user, sh, slot_of(s, sh, slot_id),
                  settings.max_pending_per_user, body.instruction)
        return sheet_reply(s, sh, user)

    @app.delete("/api/sheets/{sheet_id}/slots/{slot_id}")
    def remove_sheet_slot(sheet_id: str, slot_id: str, user: User = Depends(user_dep), s: Session = Depends(session)):
        sh = sheet_for_write(s, sheet_id, user)
        gen_error(sheets.remove_slot, s, slot_of(s, sh, slot_id))
        return sheet_reply(s, sh, user)

    @app.patch("/api/sheets/{sheet_id}/candidates/{candidate_id}")
    def mark_sheet_candidate(sheet_id: str, candidate_id: str, body: CandidatePatch, user: User = Depends(user_dep),
                             s: Session = Depends(session)):
        sh = sheet_for_write(s, sheet_id, user)
        c = s.get(SheetCandidate, candidate_id)
        if c is None or slot_of(s, sh, c.slot_id) is None:
            raise HTTPException(404, "No such image on this sheet")
        if body.kept and (c.artifact is None or c.artifact.status != "done"):
            raise HTTPException(422, "Only finished images can be kept")
        if body.frozen is not None:
            c.frozen = body.frozen
        if body.kept is not None:
            c.kept = body.kept
        return sheet_reply(s, sh, user)

    @app.post("/api/sheets/{sheet_id}/export")
    def export_sheet(sheet_id: str, user: User = Depends(user_dep), s: Session = Depends(session)):
        """Builds the zip of the kept images and returns a short-lived link to it, with the sprites that had to be
        shrunk to fit the tile (bigger than the base's scale allows)."""
        sh = sheet_for_view(s, sheet_id, user)
        key, files, shrunk = gen_error(sheets.export, s, sh, store)
        path, expires = sheets.link(s, sh)
        return {"download_url": path, "expires_at": expires, "files": files, "shrunk": shrunk}

    @app.get("/api/agents")
    def agents(user: User = Depends(user_dep), s: Session = Depends(session)):
        toks = s.scalars(select(AccessToken).where(AccessToken.user_id == user.id).order_by(AccessToken.created_at))
        grants = s.scalars(select(OAuthGrant).where(OAuthGrant.user_id == user.id)
                           .order_by(OAuthGrant.created_at.desc()))
        return {"mcp_url": settings.public_url.rstrip("/") + "/mcp", "tokens": [tokens.out(t) for t in toks],
                "grants": [oauth.grant_out(g) for g in grants]}

    @app.post("/api/tokens", status_code=201)
    def create_token(body: NameIn, user: User = Depends(user_dep), s: Session = Depends(session)):
        if tokens.count(s, user) >= tokens.MAX_PER_USER:
            raise HTTPException(409, f"At most {tokens.MAX_PER_USER} tokens; revoke one first")
        row, raw = tokens.create(s, user, body.name)
        return {**tokens.out(row), "token": raw, "mcp_url": settings.public_url.rstrip("/") + "/mcp"}

    @app.delete("/api/tokens/{token_id}")
    def revoke_token(token_id: str, user: User = Depends(user_dep), s: Session = Depends(session)):
        row = s.get(AccessToken, token_id)
        if row is None or row.user_id != user.id:
            raise HTTPException(404, "No such token")
        s.delete(row)
        return {"ok": True}

    @app.delete("/api/oauth/grants/{grant_id}")
    def revoke_grant(grant_id: str, user: User = Depends(user_dep), s: Session = Depends(session)):
        g = s.get(OAuthGrant, grant_id)
        if g is None or g.user_id != user.id:
            raise HTTPException(404, "No such connected app")
        s.delete(g)
        return {"ok": True}

    @app.api_route("/api/{rest:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    def api_not_found(rest: str):
        raise HTTPException(404, "Not found")

    # --- OAuth consent for MCP clients (oauth.py) --------------------------------------------------------------

    def consent_page(body: str, status: int = 200, redirect_to: str | None = None) -> HTMLResponse:
        csp = CSP
        if redirect_to:
            csp = CSP.replace("form-action 'self'", f"form-action 'self' {oauth.form_action_source(redirect_to)}")
        return HTMLResponse(body, status_code=status, headers={
            "Content-Security-Policy": csp, "Cache-Control": "no-store", "X-Frame-Options": "DENY"})

    @app.get(oauth.CONSENT_PATH)
    def consent(request: Request, req: str = "", s: Session = Depends(session)):
        try:
            row, info = oauth.pending(s, req)
        except oauth.ConsentError as exc:
            return consent_page(oauth.error_html(str(exc)), 400)
        user = auth.user_for_request(s, request, settings)
        if user is None:
            if provider is None:
                return consent_page(oauth.error_html("Sign in to genai in this browser first, then reload this page."),
                                    401)
            back = f"{oauth.CONSENT_PATH}?req={req}"
            return RedirectResponse(f"{oidc.FLOW_PATH}/login?next={quote(back, safe='')}", 302)
        try:
            nonce = oauth.show(s, row, user)
        except oauth.ConsentError as exc:
            return consent_page(oauth.error_html(str(exc)), 403)
        return consent_page(oauth.consent_html(info, row.params, user, req, nonce),
                            redirect_to=row.params["redirect_uri"])

    @app.post(oauth.CONSENT_PATH)
    async def consent_decide(request: Request):
        form = await request.form()
        req, nonce = str(form.get("req") or ""), str(form.get("nonce") or "")
        approve = form.get("decision") == "allow"
        with db.session() as s:
            user = auth.user_for_request(s, request, settings)
            try:
                row, _ = oauth.pending(s, req)
                if user is None:
                    raise oauth.ConsentError("You are no longer signed in. Start again.")
                url = oauth.decide(s, row, user, nonce, approve)
            except oauth.ConsentError as exc:
                return consent_page(oauth.error_html(str(exc)), 400)
        return RedirectResponse(url, status_code=303)

    # RFC 8414 wants an https issuer; plain http is only allowed on loopback.
    if settings.secure or urlsplit(settings.public_url).hostname in ("localhost", "127.0.0.1"):
        issuer = _Issuer(u=settings.public_url).u
        app.router.routes.extend(create_auth_routes(
            oauth.Provider(db, settings.public_url), issuer_url=issuer,
            client_registration_options=ClientRegistrationOptions(
                enabled=True, valid_scopes=[oauth.SCOPE], default_scopes=[oauth.SCOPE]),
            revocation_options=RevocationOptions(enabled=True)))
        resource_routes = create_protected_resource_routes(
            resource_url=AnyHttpUrl(settings.public_url + "/mcp"), authorization_servers=[issuer],
            scopes_supported=[oauth.SCOPE], resource_name="genai")
        app.router.routes.extend(resource_routes)
        # Some clients look for the metadata without the resource path.
        app.router.routes.append(Route("/.well-known/oauth-protected-resource", endpoint=resource_routes[0].endpoint,
                                       methods=["GET", "OPTIONS"]))
    else:
        log.warning("MCP OAuth disabled: PUBLIC_URL is neither https nor localhost")

    # MCP endpoint (bearer tokens, not cookies); before the UI catch-all.
    app.router.routes.append(Route("/mcp", endpoint=mcp_app, methods=["GET", "POST", "DELETE"]))

    # --- the built UI -----------------------------------------------------------------------------------------

    static = settings.static_dir
    if static and os.path.isdir(static):
        root = os.path.realpath(static)

        @app.api_route("/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
        def ui(path: str):
            target = os.path.realpath(os.path.join(root, path))
            if path and target.startswith(root + os.sep) and os.path.isfile(target):
                cache = "public, max-age=31536000, immutable" if path.startswith("assets/") else "no-cache"
                return FileResponse(target, headers={"Cache-Control": cache})
            return FileResponse(os.path.join(root, "index.html"), headers={"Cache-Control": "no-cache"})

    return app
