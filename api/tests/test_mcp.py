"""MCP tools over a personal access token: projects, generate (waiting for the scheduler), images, search, visibility."""
import base64
import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from tests.conftest import H, Mcp, make_token, sign_in


@pytest.fixture
def agent(world):
    app, _, sched, _ = world
    sched.auto_finish = True
    web = sign_in(world, "alice")
    with TestClient(app) as http:
        yield Mcp(http, make_token(web, "laptop")), web, http


def test_needs_a_token(agent):
    mcp, _, http = agent
    r = http.post("/mcp", json={}, headers={"Accept": "application/json, text/event-stream"})
    assert r.status_code == 401 and "resource_metadata=" in r.headers["www-authenticate"]
    assert mcp.rpc("tools/list", token="gnp_nope").status_code == 401


def test_tools_listed(agent):
    mcp, _, _ = agent
    names = {t["name"] for t in mcp.rpc("tools/list").json()["result"]["tools"]}
    assert names == {"list_models_and_presets", "list_projects", "create_project", "generate", "get_artifact",
                     "get_image", "list_artifacts"}


def test_generate_waits_and_returns_done_images(agent):
    mcp, web, _ = agent
    out = mcp.call("generate", prompt="a goblin archer", preset="sprite", count=2, seed=5)
    arts = out["artifacts"]
    assert [a["status"] for a in arts] == ["done", "done"] and "note" not in out
    assert arts[0]["project"]["name"] == "Scratch" and arts[0]["download_url"].endswith(f"/api/artifacts/{arts[0]['id']}/image")
    got = mcp.call("get_artifact", artifact_id=arts[0]["id"])
    assert got["full_prompt"].startswith("pixel art game sprite of a goblin archer") and got["seed"] == 123
    # the web UI sees the agent's work
    assert {a["id"] for a in web.get("/api/artifacts").json()["artifacts"]} == {a["id"] for a in arts}


def test_generate_returns_pending_after_wait(world, agent):
    mcp, _, _ = agent
    world[2].auto_finish = False
    out = mcp.call("generate", prompt="a slow one", wait_seconds=0)
    assert out["artifacts"][0]["status"] == "queued" and "get_artifact" in out["note"]


def test_9b_carries_the_licence(agent):
    mcp, _, _ = agent
    a = mcp.call("generate", prompt="x", model="flux2-klein-9b")["artifacts"][0]
    assert "not for commercial use" in a["licence"]
    assert "Unknown model" in mcp.error("generate", prompt="x", model="sdxl")


def test_projects_by_name_and_ownership(world, agent):
    mcp, _, _ = agent
    p = mcp.call("create_project", name="Dungeon", visibility="public")
    assert p["mine"] and p["visibility"] == "public"
    a = mcp.call("generate", prompt="an orc", project="dungeon")["artifacts"][0]
    assert a["project"]["id"] == p["id"]
    assert "already have" in mcp.error("create_project", name="Dungeon")
    # bob sees alice's public project but can't add to it
    bob = Mcp(mcp.http, make_token(sign_in(world, "bob")))
    assert [x["name"] for x in bob.call("list_projects")["public"]] == ["Dungeon"]
    assert "only its owner" in bob.error("generate", prompt="mine now", project=p["id"])
    assert [x["id"] for x in bob.call("list_artifacts", project="Dungeon")["artifacts"]] == [a["id"]]


def test_private_stays_private(world, agent):
    mcp, _, _ = agent
    a = mcp.call("generate", prompt="secret thing")["artifacts"][0]
    bob = Mcp(mcp.http, make_token(sign_in(world, "bob")))
    assert "No such artifact" in bob.error("get_artifact", artifact_id=a["id"])
    assert bob.call("list_artifacts", query="secret")["artifacts"] == []
    assert bob.call("list_artifacts", project="Scratch")["artifacts"] == []      # "Scratch" is bob's own, empty
    assert "No such project" in bob.error("list_artifacts", project=a["project"]["id"]) or \
        "no project" in bob.error("list_artifacts", project=a["project"]["id"])


def test_get_image_preview_and_full(agent):
    mcp, _, _ = agent
    a = mcp.call("generate", prompt="a cat")["artifacts"][0]
    for size in ("preview", "full"):
        res = mcp.raw("get_image", artifact_id=a["id"], size=size)
        img = res["content"][0]
        assert img["type"] == "image" and img["mimeType"] == "image/png"
        assert Image.open(io.BytesIO(base64.b64decode(img["data"]))).size == (8, 8)


def test_list_artifacts_query_and_paging(agent):
    mcp, _, _ = agent
    for p in ("red potion", "blue potion", "wooden shield"):
        mcp.call("generate", prompt=p)
    assert {a["prompt"] for a in mcp.call("list_artifacts", query="potion")["artifacts"]} == {"red potion", "blue potion"}
    page = mcp.call("list_artifacts", limit=2)
    assert len(page["artifacts"]) == 2 and page["next"]
    assert [a["prompt"] for a in mcp.call("list_artifacts", limit=2, before=page["next"])["artifacts"]] == ["red potion"]


def test_bearer_downloads_the_image_and_revoking_stops_it(agent):
    mcp, web, http = agent
    a = mcp.call("generate", prompt="a cat")["artifacts"][0]
    r = http.get(a["download_url"].replace("http://testserver", ""), headers={"Authorization": f"Bearer {mcp.token}"})
    assert r.status_code == 200 and r.content.startswith(b"\x89PNG")
    tok = web.get("/api/agents").json()["tokens"][0]
    assert tok["name"] == "laptop" and tok["prefix"].startswith("gnp_")
    assert web.delete(f"/api/tokens/{tok['id']}", headers=H).status_code == 200
    assert http.get(f"/api/artifacts/{a['id']}/image", headers={"Authorization": f"Bearer {mcp.token}"}).status_code == 401
    assert mcp.rpc("tools/list").status_code == 401


def test_tokens_cannot_use_the_rest_of_the_api(agent):
    mcp, _, http = agent
    hdr = {"Authorization": f"Bearer {mcp.token}", "X-Genai": "1"}
    assert http.get("/api/projects", headers=hdr).status_code == 401
    assert http.post("/api/tokens", json={"name": "more"}, headers=hdr).status_code == 401
