import base64
import io
import itertools

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from genai import oidc
from genai.config import Settings
from genai.main import create_app
from genai.scheduler import SchedulerError
from genai.storage import MemoryStore

H = {"X-Genai": "1"}
GROUP = "gen-ai-platform-users"


def png_b64() -> str:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (255, 0, 255)).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


class FakeProvider(oidc.Provider):
    """authentik stand-in: any code is accepted and maps to the subject (and groups) recorded for it."""

    def __init__(self, settings):
        super().__init__(settings)
        self.subjects: dict[str, tuple[str, list[str]]] = {}

    def metadata(self):
        return {"authorization_endpoint": "https://auth.example/authorize"}

    def exchange(self, code, verifier, redirect_uri):
        if code not in self.subjects:
            raise oidc.SignInRefused("bad code")
        return {"id_token": code}

    def verify(self, id_token, nonce):
        sub, groups = self.subjects[id_token]
        return {"iss": self.settings.oidc_issuer, "sub": sub, "preferred_username": sub, "groups": groups}


class FakeScheduler:
    """gpu-scheduler stand-in: records submissions; tests set each job's state in `jobs`."""

    def __init__(self):
        self.submitted: list[tuple[str, list[dict]]] = []
        self.jobs: dict[str, dict] = {}
        self.down = False
        self._ids = itertools.count(1)

    def submit(self, model, inputs):
        if self.down:
            raise SchedulerError("cannot reach the GPU scheduler")
        self.submitted.append((model, inputs))
        ids = [f"job-{next(self._ids)}" for _ in inputs]
        for i in ids:
            self.jobs[i] = {"status": "queued"}
        return ids

    def job(self, job_id):
        if self.down:
            raise SchedulerError("down")
        return self.jobs[job_id]

    def finish(self, job_id, uploaded=True, seed=123):
        r = {"seed": seed, "steps": 4, "cfg_scale": 1.0, "width": 1024, "height": 1024, "bytes": 100, "seconds": 2.9,
             "model": "x", "uploaded": uploaded}
        if not uploaded:
            r["png_b64"] = png_b64()
        self.jobs[job_id] = {"status": "done", "result": r, "ran_model": "flux2-klein-4b", "worker": "ainode",
                             "finished_at": "2026-11-01T12:00:00+00:00"}


@pytest.fixture
def settings(tmp_path):
    return Settings(database_url=f"sqlite:///{tmp_path}/t.db", public_url="http://testserver",
                    oidc_issuer="https://auth.example/app/", oidc_client_id="genai", oidc_client_secret="s",
                    user_group=GROUP, background_poll=False, max_pending_per_user=6)


@pytest.fixture
def world(settings):
    """(app, provider, scheduler, store). The store presigns, as S3 does in prod."""
    provider, sched, store = FakeProvider(settings), FakeScheduler(), MemoryStore(presign_base="http://s3.test/b")
    app = create_app(settings, provider=provider, store=store, scheduler=sched)
    return app, provider, sched, store


def sign_in(world, sub: str, groups=(GROUP,)) -> TestClient:
    app, provider, *_ = world
    provider.subjects[sub] = (sub, list(groups))
    c = TestClient(app)
    r = c.get("/api/auth/oidc/login", follow_redirects=False)
    state = r.headers["location"].split("state=")[1].split("&")[0]
    r = c.get(f"/api/auth/oidc/callback?code={sub}&state={state}", follow_redirects=False)
    assert r.status_code == 302, r.text
    c.last_redirect = r.headers["location"]
    return c


@pytest.fixture
def alice(world):
    return sign_in(world, "alice")


@pytest.fixture
def bob(world):
    return sign_in(world, "bob")
