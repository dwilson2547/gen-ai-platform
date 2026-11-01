"""HTTP API and the built UI (design §7). Everything but sign-in, health and config needs a session."""

from __future__ import annotations

import contextlib
import logging
import os
import re
from datetime import datetime

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import __version__, access, auth, catalogue, generation, oidc, storage
from .config import Settings
from .db import Database
from .models import Artifact, Project, User
from .scheduler import HttpScheduler, Scheduler, Unconfigured

log = logging.getLogger("genai")

CSP = ("default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; script-src 'self'; "
       "connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")


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
    width: int = Field(default=1024, ge=256, le=2048, multiple_of=16)
    height: int = Field(default=1024, ge=256, le=2048, multiple_of=16)
    seed: int | None = Field(default=None, ge=0, lt=2 ** 31)
    count: int = Field(default=1, ge=1, le=generation.MAX_COUNT)
    steps: int | None = Field(default=None, ge=1, le=50)


def project_out(p: Project, user: User, count: int = 0, cover: str | None = None) -> dict:
    return {"id": p.id, "name": p.name, "description": p.description, "visibility": p.visibility,
            "is_default": p.is_default, "owner": p.owner.username, "mine": p.owner_id == user.id,
            "created_at": p.created_at.isoformat(), "artifacts": count,
            "cover_url": f"/api/artifacts/{cover}/image" if cover else None}


def artifact_out(a: Artifact) -> dict:
    m = catalogue.MODELS.get(a.model)
    return {
        "id": a.id, "project": {"id": a.project.id, "name": a.project.name, "visibility": a.project.visibility},
        "by": a.creator.username, "created_at": a.created_at.isoformat(),
        "prompt": a.prompt, "full_prompt": a.full_prompt, "preset": a.preset,
        "model": a.model, "model_label": m.label if m else a.model, "commercial": m.commercial if m else None,
        "seed": a.seed, "width": a.width, "height": a.height, "steps": a.steps, "cfg_scale": a.cfg_scale,
        "parent_id": a.parent_id, "op": a.op, "status": a.status, "error": a.error,
        "ran_model": a.ran_model, "worker": a.worker, "seconds": a.seconds, "bytes": a.bytes,
        "finished_at": a.finished_at.isoformat() if a.finished_at else None,
        "image_url": f"/api/artifacts/{a.id}/image" if a.status == "done" else None,
    }


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
        scheduler = (HttpScheduler(settings.scheduler_url, settings.scheduler_token) if settings.scheduler_url
                     else Unconfigured())
    poller = generation.Poller(db, scheduler, store, settings.poll_seconds)

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        if settings.background_poll:
            poller.start()
        try:
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
            raise HTTPException(403, "missing X-Genai header")
        auth.end_session(s, request, settings, response)
        return {"ok": True}

    @app.get(oidc.FLOW_PATH + "/login")
    def oidc_login():
        if provider is None:
            raise HTTPException(404, "sign-in is not set up on this server")
        try:
            url, flow = oidc.begin(provider, settings)
        except Exception as exc:  # noqa: BLE001
            log.warning("oidc: discovery failed: %s", exc)
            raise HTTPException(502, "cannot reach the sign-in server") from None
        res = RedirectResponse(url, status_code=302)
        res.set_cookie(oidc.FLOW_COOKIE, flow, max_age=oidc.FLOW_TTL_S, path=oidc.FLOW_PATH,
                       httponly=True, secure=settings.secure, samesite="lax")
        return res

    @app.get(oidc.FLOW_PATH + "/callback")
    def oidc_callback(request: Request, code: str = "", state: str = "", error: str = "",
                      s: Session = Depends(session)):
        if provider is None:
            raise HTTPException(404, "sign-in is not set up on this server")
        res = RedirectResponse("/", status_code=302)
        res.delete_cookie(oidc.FLOW_COOKIE, path=oidc.FLOW_PATH)
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
                raise HTTPException(403, "missing X-Genai header")
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
            raise HTTPException(409, "you already have a project with that name")
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
                raise HTTPException(409, "you already have a project with that name")
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
                         seed=body.seed, count=body.count, steps=body.steps,
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
                q = q.where(Artifact.created_at < datetime.fromisoformat(before))
            except ValueError:
                raise HTTPException(422, "before must be an ISO timestamp") from None
        rows = list(s.scalars(q.order_by(Artifact.created_at.desc()).limit(max(1, min(limit, 100)))))
        return {"artifacts": [artifact_out(a) for a in rows],
                "next": rows[-1].created_at.isoformat() if len(rows) == max(1, min(limit, 100)) else None}

    @app.get("/api/artifacts/{artifact_id}")
    def get_artifact(artifact_id: str, user: User = Depends(user_dep), s: Session = Depends(session)):
        return artifact_out(access.artifact_for_view(s, artifact_id, user))

    @app.delete("/api/artifacts/{artifact_id}")
    def delete_artifact(artifact_id: str, user: User = Depends(user_dep), s: Session = Depends(session)):
        a = access.artifact_for_view(s, artifact_id, user)
        if a.project.owner_id != user.id:
            raise HTTPException(403, "only the project's owner can delete from it")
        if a.image_key:
            try:
                store.delete(a.image_key)
            except Exception:  # noqa: BLE001 — a missing object must not keep the row
                log.warning("could not delete %s from the store", a.image_key)
        s.delete(a)
        return {"ok": True}

    @app.get("/api/artifacts/{artifact_id}/image")
    def artifact_image(artifact_id: str, download: bool = False, user: User = Depends(user_dep),
                       s: Session = Depends(session)):
        a = access.artifact_for_view(s, artifact_id, user)
        if a.status != "done" or not a.image_key:
            raise HTTPException(404, "no image yet")
        try:
            body = store.get(a.image_key)
        except KeyError:
            raise HTTPException(404, "the image is missing from storage") from None
        headers = {"Cache-Control": "private, max-age=86400, immutable"}      # an artifact's image never changes
        if download:
            slug = re.sub(r"[^a-z0-9]+", "-", a.prompt.lower())[:40].strip("-") or "image"
            headers["Content-Disposition"] = f'attachment; filename="{slug}-{a.id[:8]}.png"'
        return StreamingResponse(body, media_type="image/png", headers=headers)

    @app.api_route("/api/{rest:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    def api_not_found(rest: str):
        raise HTTPException(404, "not found")

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
