"""Generation: artifacts become gpu-scheduler jobs; a poller brings the results back.

Each artifact is one job. With S3, the job carries a presigned PUT and gpu-scheduler uploads the PNG itself, so images
never pass through the queue; otherwise (local runs) the PNG comes back inline and is stored here.

Polling rather than callbacks: one small query every POLL_SECONDS while anything is pending, and nothing to expose to
the scheduler. A restart loses nothing: pending artifacts are picked up again from the database.
"""

from __future__ import annotations

import base64
import logging
import threading
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import sprites
from .catalogue import BACKGROUNDS, MODELS, PRESETS, SPRITE_PRESETS, background_of
from .db import Database
from .models import Artifact, Project, User, Variant, utcnow
from .scheduler import Scheduler, SchedulerError
from .storage import Store

log = logging.getLogger("genai.generation")

PENDING = ("queued", "running")
MAX_COUNT = 4
OPS = ("generate", "reroll", "edit_prompt", "edit_image")


class GenerateError(Exception):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


def image_key(artifact_id: str) -> str:
    return f"artifacts/{artifact_id}/original.png"


def pending_count(s: Session, user: User) -> int:
    return s.scalar(select(func.count()).select_from(Artifact)
                    .where(Artifact.created_by == user.id, Artifact.status.in_(PENDING))) or 0


def create(s: Session, scheduler: Scheduler, store: Store, user: User, project: Project, *, prompt: str,
           preset: str, model: str, width: int, height: int, seed: int | None, count: int, steps: int | None,
           max_pending: int, parent: Artifact | None = None, op: str = "generate",
           reference: Artifact | None = None, background: str = "magenta",
           full_prompt: str | None = None) -> list[Artifact]:
    """Queue `count` images of one prompt. Raises GenerateError; the caller's transaction then rolls back, so a
    refused submit leaves nothing behind. `parent`/`op` record a tweak's lineage; `reference` is an image the model
    edits from (FLUX.2 klein reference editing)."""
    if model not in MODELS:
        raise GenerateError(f"Unknown model {model!r}")
    if preset not in PRESETS:
        raise GenerateError(f"Unknown preset {preset!r}")
    if not 1 <= count <= MAX_COUNT:
        raise GenerateError(f"Count must be 1-{MAX_COUNT}")
    queued = pending_count(s, user)
    if queued + count > max_pending:
        raise GenerateError(f"You have {queued} image{'s' if queued != 1 else ''} in the queue and the limit is "
                            f"{max_pending}. Wait for some to finish.", 429)
    if background not in BACKGROUNDS:
        raise GenerateError(f"Unknown background {background!r}: {', '.join(BACKGROUNDS)}")
    if reference is not None and (reference.status != "done" or not reference.image_key):
        raise GenerateError("The image to edit from isn't finished")
    full = full_prompt or PRESETS[preset].apply(prompt, background)
    arts = []
    for i in range(count):
        a = Artifact(project_id=project.id, created_by=user.id, prompt=prompt.strip(), full_prompt=full,
                     preset=preset, model=model, width=width, height=height, steps=steps,
                     seed=None if seed is None else seed + i, parent_id=parent.id if parent else None, op=op)
        s.add(a)
        arts.append(a)
    s.flush()                                                   # ids, for the storage keys
    ref = None
    if reference is not None:
        url = store.presign_get(reference.image_key)
        ref = {"image_url": url} if url else {"image_b64": base64.b64encode(b"".join(store.get(reference.image_key))).decode()}
    inputs = []
    for a in arts:
        a.image_key = image_key(a.id)
        item = {"prompt": full, "width": width, "height": height}
        if a.seed is not None:
            item["seed"] = a.seed
        if steps is not None:
            item["steps"] = steps
        if url := store.presign_put(a.image_key):
            item["output_url"] = url
        if ref:
            item["ref_images"] = [ref]
        inputs.append(item)
    try:
        job_ids = scheduler.submit(model, inputs)
    except SchedulerError as e:
        raise GenerateError(str(e), 503) from None
    for a, j in zip(arts, job_ids):
        a.job_id = j
    return arts


def apply_job(a: Artifact, job: dict, store: Store) -> None:
    """Fold a gpu-scheduler job into its artifact."""
    status = job.get("status")
    if status == "running" and a.status == "queued":
        a.status = "running"
    elif status == "done":
        r = job.get("result") or {}
        if not r.get("uploaded"):
            b64 = r.get("png_b64")
            if not b64:
                a.status, a.error = "failed", "the GPU scheduler returned no image"
                a.finished_at = utcnow()
                return
            data = base64.b64decode(b64)
            store.put(a.image_key, data, "image/png")
        a.status = "done"
        a.seed, a.steps, a.cfg_scale = r.get("seed", a.seed), r.get("steps", a.steps), r.get("cfg_scale")
        a.seconds, a.bytes = r.get("seconds"), r.get("bytes")
        a.ran_model, a.worker = job.get("ran_model") or a.model, job.get("worker")
        a.finished_at = _ts(job.get("finished_at"))
        if a.preset in SPRITE_PRESETS:
            try:
                make_cutout(a, store)
            except Exception:                                   # noqa: BLE001 — the image itself is still fine
                log.exception("cutout for %s failed", a.id)
    elif status in ("failed", "cancelled"):
        a.status, a.error = "failed", (job.get("error") or status)[:2000]
        a.finished_at = _ts(job.get("finished_at"))


def _ts(v) -> datetime:
    if isinstance(v, str):
        try:
            return datetime.fromisoformat(v)
        except ValueError:
            pass
    return utcnow()


class Poller:
    def __init__(self, db: Database, scheduler: Scheduler, store: Store, interval: float):
        self.db, self.scheduler, self.store, self.interval = db, scheduler, store, interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def poll_once(self) -> int:
        """Check every pending artifact once; returns how many are still pending."""
        with self.db.session() as s:
            pending = list(s.scalars(select(Artifact).where(Artifact.status.in_(PENDING),
                                                            Artifact.job_id.is_not(None))
                                     .order_by(Artifact.created_at).limit(200)))
            for a in pending:
                try:
                    apply_job(a, self.scheduler.job(a.job_id), self.store)
                except SchedulerError as e:
                    log.warning("poll %s: %s", a.id, e)
                    break                                       # scheduler down: try again next round
                except Exception:                               # noqa: BLE001 — one bad artifact must not stop the rest
                    log.exception("poll %s failed", a.id)
            return sum(1 for a in pending if a.status in PENDING)

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="genai-poller", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception:                                   # noqa: BLE001
                log.exception("poller round failed")
            self._stop.wait(self.interval)


def tweak(s: Session, scheduler: Scheduler, store: Store, user: User, parent: Artifact, target: Project, *, op: str,
          prompt: str | None, seed: int | None, count: int, model: str | None, max_pending: int) -> list[Artifact]:
    """New artifacts made from `parent` (design §1), linked to it:
    - reroll: the same recipe with a new seed (random unless given), to explore.
    - edit_prompt: a changed prompt with the parent's seed (unless given), so the difference is the prompt's.
    - edit_image: an instruction ("same character, holding an axe") applied to the parent's image itself; the preset's
      template is not applied, since the instruction is about the picture, not a subject.
    Re-rolling or re-wording an image that is itself an edit redoes that edit from the same source image.
    `target` is the project they go into (one of the user's own); the caller decides it."""
    if op not in ("reroll", "edit_prompt", "edit_image"):
        raise GenerateError(f"Unknown tweak {op!r}: reroll, edit_prompt or edit_image")
    if op in ("edit_prompt", "edit_image") and not (prompt or "").strip():
        raise GenerateError("This tweak needs a prompt" if op == "edit_prompt" else "Say what to change in the image")
    common = dict(model=model or parent.model, width=parent.width, height=parent.height, steps=parent.steps,
                  count=count, max_pending=max_pending, parent=parent, op=op)
    # Re-rolling or re-wording an edit redoes the edit: same reference image (the edit's own source), so the subject
    # stays the one being edited instead of being drawn from scratch.
    source = s.get(Artifact, parent.parent_id) if parent.op == "edit_image" and parent.parent_id else None
    redo = source is not None and source.status == "done" and bool(source.image_key)
    if op == "reroll":
        return create(s, scheduler, store, user, target, prompt=parent.prompt, preset=parent.preset, seed=seed,
                      reference=source if redo else None, full_prompt=parent.full_prompt, **common)
    if op == "edit_prompt":
        return create(s, scheduler, store, user, target, prompt=prompt, preset=parent.preset,
                      seed=parent.seed if seed is None else seed, reference=source if redo else None,
                      background=background_of(parent.full_prompt), **common)
    return create(s, scheduler, store, user, target, prompt=prompt, preset="none", seed=seed, reference=parent,
                  **common)


# --- variants (design §5) -------------------------------------------------------------------------------------------

def _variant(a: Artifact, store: Store, kind: str, name: str, png: bytes, params: dict,
             user_id: str | None) -> Variant:
    key = f"artifacts/{a.id}/{name}.png"
    store.put(key, png, "image/png")
    w, h = sprites.size_of(png)
    v = Variant(artifact_id=a.id, kind=kind, params=params, image_key=key, width=w, height=h, bytes=len(png),
                created_by=user_id)
    from sqlalchemy.orm import object_session
    object_session(a).add(v)
    return v


def make_cutout(a: Artifact, store: Store) -> Variant:
    """The background keyed out (sprites.cutout), same canvas. Made once per sprite, when it finishes."""
    from sqlalchemy.orm import object_session
    existing = object_session(a).scalar(select(Variant).where(Variant.artifact_id == a.id, Variant.kind == "cutout"))
    if existing is not None:
        return existing
    png, key = sprites.cutout(b"".join(store.get(a.image_key)))
    return _variant(a, store, "cutout", "cutout", png, {"key": "#%02x%02x%02x" % key}, None)


def make_pixel(a: Artifact, store: Store, params: sprites.PixelParams, user: User) -> Variant:
    """A pixel-grid version: from the cutout for sprites (made now if missing), else from the original image."""
    if a.status != "done" or not a.image_key:
        raise GenerateError("The image isn't finished")
    if err := params.check():
        raise GenerateError(err)
    if a.preset in SPRITE_PRESETS:
        source = b"".join(store.get(make_cutout(a, store).image_key))
    else:
        source = b"".join(store.get(a.image_key))
    png = sprites.pixelate(source, params)
    name = f"pixel-{params.size}-{params.colors}-{params.canvas}"
    from sqlalchemy.orm import object_session
    s = object_session(a)
    old = s.scalar(select(Variant).where(Variant.artifact_id == a.id, Variant.kind == "pixel",
                                         Variant.image_key == f"artifacts/{a.id}/{name}.png"))
    if old is not None:                                          # same settings: replace, don't pile up copies
        s.delete(old)
        s.flush()
    return _variant(a, store, "pixel", name, png,
                    {"size": params.size, "colors": params.colors, "canvas": params.canvas}, user.id)
