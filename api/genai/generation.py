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

from .catalogue import MODELS, PRESETS
from .db import Database
from .models import Artifact, Project, User, utcnow
from .scheduler import Scheduler, SchedulerError
from .storage import Store

log = logging.getLogger("genai.generation")

PENDING = ("queued", "running")
MAX_COUNT = 4


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
           max_pending: int) -> list[Artifact]:
    """Queue `count` images of one prompt. Raises GenerateError; the caller's transaction then rolls back, so a
    refused submit leaves nothing behind."""
    if model not in MODELS:
        raise GenerateError(f"unknown model {model!r}")
    if preset not in PRESETS:
        raise GenerateError(f"unknown preset {preset!r}")
    if not 1 <= count <= MAX_COUNT:
        raise GenerateError(f"count must be 1-{MAX_COUNT}")
    if pending_count(s, user) + count > max_pending:
        raise GenerateError(f"you already have images in the queue; at most {max_pending} at a time", 429)
    full = PRESETS[preset].apply(prompt)
    arts = []
    for i in range(count):
        a = Artifact(project_id=project.id, created_by=user.id, prompt=prompt.strip(), full_prompt=full,
                     preset=preset, model=model, width=width, height=height, steps=steps,
                     seed=None if seed is None else seed + i)
        s.add(a)
        arts.append(a)
    s.flush()                                                   # ids, for the storage keys
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
