"""gpu-scheduler client: submit image jobs, read them back. The scheduler owns the GPU; this app never runs a model."""

from __future__ import annotations

from typing import Protocol

import httpx


class SchedulerError(Exception):
    pass


class Scheduler(Protocol):
    def submit(self, model: str, inputs: list[dict]) -> list[str]: ...
    def job(self, job_id: str) -> dict: ...


class HttpScheduler:
    def __init__(self, url: str, token: str | None, timeout: float = 15.0):
        self.url, self.timeout = url.rstrip("/"), timeout
        self.headers = {"Authorization": f"Bearer {token}"} if token else {}

    def submit(self, model: str, inputs: list[dict]) -> list[str]:
        try:
            # One attempt per job beyond the scheduler's own retries is enough: a failed image is re-rolled by the user.
            r = httpx.post(self.url + "/v1/jobs", headers=self.headers, timeout=self.timeout,
                           json={"model": model, "inputs": inputs, "max_attempts": 2})
        except httpx.HTTPError as e:
            raise SchedulerError(f"Cannot reach the GPU scheduler: {e}") from None
        if r.status_code != 202:
            raise SchedulerError(f"The GPU scheduler refused the job: HTTP {r.status_code} {r.text[:200]}")
        return [str(j) for j in r.json()["job_ids"]]

    def job(self, job_id: str) -> dict:
        try:
            r = httpx.get(f"{self.url}/v1/jobs/{job_id}", headers=self.headers, timeout=self.timeout)
        except httpx.HTTPError as e:
            raise SchedulerError(f"Cannot reach the GPU scheduler: {e}") from None
        if r.status_code == 404:
            return {"status": "failed", "error": "the GPU scheduler has no record of this job"}
        if r.status_code != 200:
            raise SchedulerError(f"GPU scheduler HTTP {r.status_code}")
        return r.json()


class Unconfigured:
    """No SCHEDULER_URL: generation is off, everything else works."""

    def submit(self, model, inputs):
        raise SchedulerError("Generation is not set up on this server (no SCHEDULER_URL)")

    def job(self, job_id):
        return {"status": "failed", "error": "no GPU scheduler configured"}


class LocalSdServer:
    """Development only: runs jobs on a local stable-diffusion.cpp sd-server (DEV_SD_SERVER), one at a time, so the UI
    can be exercised with real images without gpu-scheduler. Whatever model that server loaded answers every job."""

    def __init__(self, url: str):
        import itertools
        import threading
        from concurrent.futures import ThreadPoolExecutor

        self.url = url.rstrip("/")
        self.jobs: dict[str, dict] = {}
        self._pool = ThreadPoolExecutor(max_workers=1)
        self._ids = itertools.count(1)
        self._lock = threading.Lock()

    def submit(self, model, inputs):
        ids = []
        for item in inputs:
            with self._lock:
                jid = f"local-{next(self._ids)}"
                self.jobs[jid] = {"status": "queued"}
            self._pool.submit(self._run, jid, model, item)
            ids.append(jid)
        return ids

    def _run(self, jid: str, model: str, item: dict) -> None:
        import random
        import time

        self.jobs[jid] = {"status": "running"}
        seed = item.get("seed", random.randrange(2 ** 31))
        body = {"prompt": item["prompt"], "width": item["width"], "height": item["height"], "seed": seed,
                "steps": item.get("steps", 4), "cfg_scale": 1.0}
        t = time.monotonic()
        try:
            r = httpx.post(self.url + "/sdapi/v1/txt2img", json=body, timeout=600)
            r.raise_for_status()
            png = r.json()["images"][0]
        except Exception as e:  # noqa: BLE001
            self.jobs[jid] = {"status": "failed", "error": f"local sd-server: {e}"}
            return
        self.jobs[jid] = {"status": "done", "ran_model": model, "worker": "local-sd-server", "result": {
            "seed": seed, "width": body["width"], "height": body["height"], "steps": body["steps"], "cfg_scale": 1.0,
            "seconds": round(time.monotonic() - t, 2), "bytes": len(png) * 3 // 4, "uploaded": False, "png_b64": png}}

    def job(self, job_id):
        return self.jobs.get(job_id) or {"status": "failed", "error": "unknown job (the dev server restarted?)"}
