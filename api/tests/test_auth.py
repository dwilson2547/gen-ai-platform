from fastapi.testclient import TestClient

from tests.conftest import H, sign_in


def test_signed_out_sees_only_the_splash_data(world):
    c = TestClient(world[0])
    assert c.get("/api/config").json()["oidc"] is True
    for path in ("/api/me", "/api/catalogue", "/api/projects", "/api/artifacts"):
        assert c.get(path).status_code == 401, path


def test_member_signs_in_and_gets_a_private_scratch_project(world):
    c = sign_in(world, "alice")
    assert c.last_redirect == "/"
    assert c.get("/api/me").json()["username"] == "alice"
    mine = c.get("/api/projects").json()["mine"]
    assert [(p["name"], p["visibility"], p["is_default"]) for p in mine] == [("Scratch", "private", True)]


def test_non_member_is_refused(world):
    c = sign_in(world, "mallory", groups=["someone-else"])
    assert c.last_redirect == "/?signin_error=not_member"
    assert c.get("/api/me").status_code == 401


def test_writes_need_the_csrf_header(alice):
    assert alice.post("/api/projects", json={"name": "x"}).status_code == 403
    assert alice.post("/api/projects", json={"name": "x"}, headers=H).status_code == 201


def test_logout_ends_the_session(alice):
    assert alice.post("/api/auth/logout", headers=H).status_code == 200
    assert alice.get("/api/me").status_code == 401


def test_catalogue_names_both_models_and_the_9b_licence(alice):
    cat = alice.get("/api/catalogue").json()
    models = {m["name"]: m for m in cat["models"]}
    assert models["flux2-klein-4b"]["default"] and models["flux2-klein-4b"]["commercial"]
    assert not models["flux2-klein-9b"]["commercial"]
    assert {p["name"] for p in cat["presets"]} >= {"none", "sprite"}
