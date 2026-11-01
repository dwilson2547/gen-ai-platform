from tests.conftest import H


def make(c, name, visibility="private"):
    r = c.post("/api/projects", json={"name": name, "visibility": visibility}, headers=H)
    assert r.status_code == 201, r.text
    return r.json()


def test_private_is_owner_only_public_is_everyone(alice, bob):
    priv, pub = make(alice, "secret"), make(alice, "shared", "public")
    seen = bob.get("/api/projects").json()
    assert [p["name"] for p in seen["public"]] == ["shared"]
    assert all(p["name"] != "secret" for p in seen["public"] + seen["mine"])
    assert bob.get(f"/api/projects/{priv['id']}").status_code == 404
    assert bob.get(f"/api/projects/{pub['id']}").json()["mine"] is False


def test_only_the_owner_changes_or_adds_to_a_project(alice, bob):
    pub = make(alice, "shared", "public")
    assert bob.patch(f"/api/projects/{pub['id']}", json={"name": "mine now"}, headers=H).status_code == 403
    r = bob.post("/api/generate", json={"prompt": "a cat", "project_id": pub["id"]}, headers=H)
    assert r.status_code == 403
    r = alice.patch(f"/api/projects/{pub['id']}", json={"visibility": "private"}, headers=H)
    assert r.json()["visibility"] == "private"
    assert bob.get(f"/api/projects/{pub['id']}").status_code == 404


def test_names_are_unique_per_owner(alice, bob):
    make(alice, "sprites")
    assert alice.post("/api/projects", json={"name": "sprites"}, headers=H).status_code == 409
    make(bob, "sprites")                                           # another owner may reuse it
    assert alice.post("/api/projects", json={"name": "x", "visibility": "open"}, headers=H).status_code == 422
