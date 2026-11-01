"""MCP OAuth: discovery, registration, consent (including sign-in on the way), code exchange, refresh, revocation."""
import base64
import hashlib
import re
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from genai.config import Settings
from genai.main import create_app
from genai.storage import MemoryStore
from tests.conftest import GROUP, H, FakeProvider, FakeScheduler, Mcp

REDIRECT = "http://127.0.0.1:9999/callback"
VERIFIER = "v" * 64
CHALLENGE = base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).rstrip(b"=").decode()


@pytest.fixture
def env(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path}/o.db", public_url="http://localhost",
                        oidc_issuer="https://auth.example/app/", oidc_client_id="genai", oidc_client_secret="s",
                        user_group=GROUP, background_poll=False)
    provider = FakeProvider(settings)
    provider.subjects["alice"] = ("alice", [GROUP])
    app = create_app(settings, provider=provider, store=MemoryStore(), scheduler=FakeScheduler())
    with TestClient(app, base_url="http://localhost") as http:
        yield app, http


def register(http, redirect=REDIRECT, name="Claude Code"):
    return http.post("/register", json={"redirect_uris": [redirect], "client_name": name,
                                        "token_endpoint_auth_method": "none",
                                        "grant_types": ["authorization_code", "refresh_token"],
                                        "response_types": ["code"]})


def authorize(http, client_id, state="st4te"):
    r = http.get("/authorize", params={
        "response_type": "code", "client_id": client_id, "redirect_uri": REDIRECT, "code_challenge": CHALLENGE,
        "code_challenge_method": "S256", "state": state, "resource": "http://localhost/mcp"}, follow_redirects=False)
    assert r.status_code == 302, r.text
    loc = r.headers["location"]
    assert loc.startswith("http://localhost/oauth/consent?req=")
    return parse_qs(urlsplit(loc).query)["req"][0]


def connect(app, http):
    """The whole browser flow, starting signed out: consent → sign-in → back to consent → allow → token."""
    client_id = register(http).json()["client_id"]
    req = authorize(http, client_id)
    web = TestClient(app, base_url="http://localhost")
    r = web.get(f"/oauth/consent?req={req}", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"].startswith("/api/auth/oidc/login?next=")
    r = web.get(r.headers["location"], follow_redirects=False)
    state = r.headers["location"].split("state=")[1].split("&")[0]
    r = web.get(f"/api/auth/oidc/callback?code=alice&state={state}", follow_redirects=False)
    assert r.headers["location"] == f"/oauth/consent?req={req}"          # sign-in came back to the consent page
    page = web.get(r.headers["location"])
    assert page.status_code == 200 and "Allow <strong>Claude Code</strong> to use genai?" in page.text
    nonce = re.search(r'name="nonce" value="([^"]+)"', page.text).group(1)
    r = web.post("/oauth/consent", data={"req": req, "nonce": nonce, "decision": "allow"}, follow_redirects=False)
    assert r.status_code == 303
    q = parse_qs(urlsplit(r.headers["location"]).query)
    assert q["state"] == ["st4te"]
    tok = http.post("/token", data={"grant_type": "authorization_code", "code": q["code"][0], "redirect_uri": REDIRECT,
                                    "client_id": client_id, "code_verifier": VERIFIER})
    assert tok.status_code == 200, tok.text
    return {**tok.json(), "client_id": client_id}, web


def test_discovery(env):
    _, http = env
    r = http.post("/mcp", json={}, headers={"Accept": "application/json, text/event-stream"})
    assert r.status_code == 401
    assert 'resource_metadata="http://localhost/.well-known/oauth-protected-resource/mcp"' in r.headers["www-authenticate"]
    assert http.get("/.well-known/oauth-protected-resource/mcp").json()["resource"] == "http://localhost/mcp"
    assert http.get("/.well-known/oauth-protected-resource").status_code == 200
    meta = http.get("/.well-known/oauth-authorization-server").json()
    assert meta["registration_endpoint"].endswith("/register") and meta["code_challenge_methods_supported"] == ["S256"]


def test_registration_rejects_remote_http_redirects(env):
    _, http = env
    assert register(http, "http://evil.example/cb").status_code == 400
    assert register(http, "javascript:alert(1)").status_code == 400
    assert register(http, "https://claude.ai/api/mcp/auth_callback").status_code == 201


def test_full_flow_then_refresh_and_disconnect(env):
    app, http = env
    tok, web = connect(app, http)
    assert tok["access_token"].startswith("gno_") and tok["refresh_token"].startswith("gnr_")
    mcp = Mcp(http, tok["access_token"])
    assert mcp.call("list_projects")["mine"][0]["name"] == "Scratch"
    grants = web.get("/api/agents").json()["grants"]
    assert [g["client_name"] for g in grants] == ["Claude Code"]

    r = http.post("/token", data={"grant_type": "refresh_token", "client_id": tok["client_id"],
                                  "refresh_token": tok["refresh_token"]})
    assert r.status_code == 200
    assert mcp.rpc("tools/list").status_code == 401                         # old access token rotated out
    mcp.token = r.json()["access_token"]
    assert mcp.rpc("tools/list").status_code == 200

    assert web.delete(f"/api/oauth/grants/{grants[0]['id']}", headers=H).status_code == 200
    assert mcp.rpc("tools/list").status_code == 401


def test_deny_and_forged_nonce(env):
    app, http = env
    client_id = register(http).json()["client_id"]
    req = authorize(http, client_id)
    _, web = connect(app, http)                                               # a signed-in browser
    page = web.get(f"/oauth/consent?req={req}")
    nonce = re.search(r'name="nonce" value="([^"]+)"', page.text).group(1)
    assert web.post("/oauth/consent", data={"req": req, "nonce": "forged", "decision": "allow"},
                    follow_redirects=False).status_code == 400
    req = authorize(http, client_id)
    nonce = re.search(r'name="nonce" value="([^"]+)"', web.get(f"/oauth/consent?req={req}").text).group(1)
    r = web.post("/oauth/consent", data={"req": req, "nonce": nonce, "decision": "deny"}, follow_redirects=False)
    assert "error=access_denied" in r.headers["location"]
