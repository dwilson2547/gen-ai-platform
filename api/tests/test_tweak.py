"""Tweaks (design §1): reroll, edit_prompt, edit_image, where they land, and lineage visibility."""
from tests.conftest import H
from tests.test_projects import make


def done(world, client, **body):
    app, _, sched, _ = world
    r = client.post("/api/generate", json={"prompt": "a dwarf blacksmith", "preset": "sprite", "seed": 7, **body},
                    headers=H)
    a = r.json()["artifacts"][0]
    sched.finish(f"job-{len(sched.submitted)}", uploaded=False, seed=a["seed"] or 99)
    app.state.poller.poll_once()
    return a


def tweak(client, a, **body):
    return client.post(f"/api/artifacts/{a['id']}/tweak", json=body, headers=H)


def test_reroll_keeps_the_recipe_with_a_new_seed(world, alice):
    sched = world[2]
    a = done(world, alice, width=512, height=768)
    kids = tweak(alice, a, op="reroll", count=2).json()["artifacts"]
    assert [k["op"] for k in kids] == ["reroll", "reroll"] and {k["parent_id"] for k in kids} == {a["id"]}
    _, inputs = sched.submitted[-1]
    assert inputs[0]["prompt"] == a["full_prompt"] and (inputs[0]["width"], inputs[0]["height"]) == (512, 768)
    assert "seed" not in inputs[0] and "ref_images" not in inputs[0]                # random seed, no reference


def test_edit_prompt_keeps_the_seed(world, alice):
    sched = world[2]
    a = done(world, alice)
    k = tweak(alice, a, op="edit_prompt", prompt="a dwarf blacksmith with a red beard").json()["artifacts"][0]
    _, inputs = sched.submitted[-1]
    assert inputs[0]["seed"] == 7 and "red beard" in inputs[0]["prompt"] and inputs[0]["prompt"].startswith("pixel art")
    assert k["preset"] == "sprite" and k["prompt"] == "a dwarf blacksmith with a red beard"
    assert tweak(alice, a, op="edit_prompt").status_code == 422


def test_edit_image_sends_the_image_as_reference(world, alice):
    sched = world[2]
    a = done(world, alice)
    k = tweak(alice, a, op="edit_image", prompt="same dwarf, holding an axe").json()["artifacts"][0]
    _, inputs = sched.submitted[-1]
    assert inputs[0]["prompt"] == "same dwarf, holding an axe" and k["preset"] == "none"     # no template on edits
    assert inputs[0]["ref_images"] == [{"image_url": f"http://s3.test/b/artifacts/{a['id']}/original.png?sig=get"}]


def test_cannot_edit_an_unfinished_image(world, alice):
    r = alice.post("/api/generate", json={"prompt": "x"}, headers=H).json()["artifacts"][0]
    assert tweak(alice, r, op="edit_image", prompt="y").status_code == 422
    assert tweak(alice, r, op="sharpen").status_code == 422


def test_where_tweaks_go(world, alice, bob):
    mine = make(alice, "Dungeon", "public")
    a = done(world, alice, project_id=mine["id"])
    assert tweak(alice, a, op="reroll").json()["artifacts"][0]["project"]["id"] == mine["id"]     # parent's, mine
    k = tweak(bob, a, op="reroll").json()["artifacts"][0]                                           # bob's Scratch
    assert k["project"]["name"] == "Scratch" and k["by"] == "bob"
    assert tweak(bob, a, op="reroll", project_id=mine["id"]).status_code == 403                    # not his


def test_lineage_respects_visibility(world, alice, bob):
    pub = make(alice, "Shared", "public")
    a = done(world, alice, project_id=pub["id"])
    bob_kid = tweak(bob, a, op="reroll").json()["artifacts"][0]           # into bob's private Scratch
    alice_kid = tweak(alice, a, op="reroll").json()["artifacts"][0]       # into the public project
    seen_by_alice = alice.get(f"/api/artifacts/{a['id']}").json()
    assert [c["id"] for c in seen_by_alice["children"]] == [alice_kid["id"]]          # bob's private child hidden
    assert {c["id"] for c in bob.get(f"/api/artifacts/{a['id']}").json()["children"]} == {alice_kid["id"], bob_kid["id"]}
    assert bob.get(f"/api/artifacts/{bob_kid['id']}").json()["parent"]["id"] == a["id"]
    # make the original private: bob's child no longer reveals its parent
    alice.patch(f"/api/projects/{pub['id']}", json={"visibility": "private"}, headers=H)
    assert bob.get(f"/api/artifacts/{bob_kid['id']}").json()["parent"] is None


def test_tweaking_an_edit_redoes_it_from_the_same_source(world, alice):
    app, _, sched, _ = world
    a = done(world, alice)
    e = tweak(alice, a, op="edit_image", prompt="same dwarf, holding an axe").json()["artifacts"][0]
    sched.finish(f"job-{len(sched.submitted)}", uploaded=False)
    app.state.poller.poll_once()
    src = f"http://s3.test/b/artifacts/{a['id']}/original.png?sig=get"
    tweak(alice, e, op="reroll")
    assert sched.submitted[-1][1][0]["ref_images"] == [{"image_url": src}]
    assert sched.submitted[-1][1][0]["prompt"] == "same dwarf, holding an axe"
    tweak(alice, e, op="edit_prompt", prompt="same dwarf, holding a hammer")
    assert sched.submitted[-1][1][0]["ref_images"] == [{"image_url": src}]
