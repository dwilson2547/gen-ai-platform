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
            raise SchedulerError(f"cannot reach the GPU scheduler: {e}") from None
        if r.status_code != 202:
            raise SchedulerError(f"GPU scheduler refused the job: HTTP {r.status_code} {r.text[:200]}")
        return [str(j) for j in r.json()["job_ids"]]

    def job(self, job_id: str) -> dict:
        try:
            r = httpx.get(f"{self.url}/v1/jobs/{job_id}", headers=self.headers, timeout=self.timeout)
        except httpx.HTTPError as e:
            raise SchedulerError(f"cannot reach the GPU scheduler: {e}") from None
        if r.status_code == 404:
            return {"status": "failed", "error": "the GPU scheduler has no record of this job"}
        if r.status_code != 200:
            raise SchedulerError(f"GPU scheduler HTTP {r.status_code}")
        return r.json()


class Unconfigured:
    """No SCHEDULER_URL: generation is off, everything else works."""

    def submit(self, model, inputs):
        raise SchedulerError("generation is not set up on this server (no SCHEDULER_URL)")

    def job(self, job_id):
        return {"status": "failed", "error": "no GPU scheduler configured"}
