"""Character sheets (design §11): base candidates with freeze/re-roll, variations as edits of the base, the queue
limit (held candidates), keep and export at the base's scale, and who may see or change a sheet."""
import base64
import io
import zipfile

from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from tests.conftest import H


def scene(height: int) -> str:
    """A 256² sprite on magenta with a green subject `height` px tall, standing on y=230."""
    im = Image.new("RGB", (256, 256), (250, 20, 150))
    ImageDraw.Draw(im).rectangle((110, 230 - height, 146, 230), fill=(40, 200, 60), outline=(10, 10, 10), width=3)
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def finish_all(world, height: int = 120) -> None:
    app, _, sched, _ = world
    for jid, j in list(sched.jobs.items()):
        if j["status"] == "queued":
            sched.finish(jid, uploaded=False, png=scene(height))
    app.state.poller.poll_once()


def new(client, **body):
    r = client.post("/api/sheets", json={"name": "Grub", "description": "a goblin archer", **body}, headers=H)
    assert r.status_code == 201, r.text
    return r.json()


def get(client, sh):
    return client.get(f"/api/sheets/{sh['id']}").json()


def base_slot(sh):
    return next(x for x in sh["slots"] if x["kind"] == "base")


def mark(client, sh, cand, **body):
    r = client.patch(f"/api/sheets/{sh['id']}/candidates/{cand['id']}", json=body, headers=H)
    assert r.status_code == 200, r.text
    return r.json()


def with_base(world, client, **body):
    """A sheet whose base is picked."""
    sh = new(client, **body)
    finish_all(world)
    sh = get(client, sh)
    r = client.post(f"/api/sheets/{sh['id']}/base", json={"candidate_id": base_slot(sh)["candidates"][0]["id"]},
                    headers=H)
    assert r.status_code == 200, r.text
    return r.json()


def test_base_candidates_freeze_and_reroll(world, alice):
    sched = world[2]
    sh = new(alice, model="qwen-image-2512")
    cands = base_slot(sh)["candidates"]
    assert len(cands) == 4 and len(sched.submitted) == 4
    model, inputs = sched.submitted[0]
    assert model == "qwen-image-2512" and inputs[0]["prompt"].startswith("pixel art game sprite of a goblin archer")
    assert (inputs[0]["width"], inputs[0]["height"]) == (512, 512)             # Qwen's sprite size
    r = alice.post(f"/api/sheets/{sh['id']}/slots/{base_slot(sh)['id']}/reroll", json={}, headers=H)
    assert r.status_code == 409                                                 # still being made
    finish_all(world)
    sh = mark(alice, get(alice, sh), cands[1], frozen=True)
    frozen_art = base_slot(sh)["candidates"][1]["artifact"]["id"]
    r = alice.post(f"/api/sheets/{sh['id']}/slots/{base_slot(sh)['id']}/reroll", json={}, headers=H)
    assert r.status_code == 202 and len(sched.submitted) == 7                  # the 3 unfrozen only
    after = base_slot(r.json())["candidates"]
    assert after[1]["artifact"]["id"] == frozen_art and all(c["artifact"]["status"] == "queued"
                                                              for i, c in enumerate(after) if i != 1)


def test_variations_are_edits_of_the_base_and_wait_for_room_in_the_queue(world, alice):
    app, _, sched, _ = world
    sh = with_base(world, alice, model="qwen-image-2512")
    base_id = sh["base_artifact_id"]
    before = len(sched.submitted)
    r = alice.post(f"/api/sheets/{sh['id']}/slots", json={"lines": ["running", "laughing"]}, headers=H)
    assert r.status_code == 201, r.text
    rows = [x for x in r.json()["slots"] if x["kind"] == "variation"]
    cands = [c for x in rows for c in x["candidates"]]
    assert [x["label"] for x in rows] == ["running", "laughing"] and len(cands) == 8
    assert sum(c["held"] for c in cands) == 2 and len(sched.submitted) == before + 6     # limit is 6 in tests
    model, inputs = sched.submitted[-1]
    assert model == "flux2-klein-4b"                                             # Qwen can't edit
    assert inputs[0]["prompt"].startswith("same character, same pixel art style, same size and framing")
    assert inputs[0]["prompt"].endswith(": laughing") and "magenta" in inputs[0]["prompt"]
    assert inputs[0]["ref_images"] and (inputs[0]["width"], inputs[0]["height"]) == (512, 512)
    art = cands[0]["artifact"]
    assert art["parent_id"] == base_id and art["op"] == "edit_image" and art["prompt"] == "running"
    finish_all(world)                                                            # frees the queue: held ones go out
    sh = get(alice, sh)
    assert not any(c["held"] for x in sh["slots"] for c in x["candidates"]) and len(sched.submitted) == before + 8


def test_rewording_rerolls_the_unfrozen(world, alice):
    sched = world[2]
    sh = with_base(world, alice)
    sh = alice.post(f"/api/sheets/{sh['id']}/slots", json={"lines": ["holding a sword"]}, headers=H).json()
    finish_all(world)
    row = sh["slots"][1]
    for c in row["candidates"]:
        sh = mark(alice, sh, c, frozen=True)
    r = alice.post(f"/api/sheets/{sh['id']}/slots/{row['id']}/reroll", json={}, headers=H)
    assert r.status_code == 422 and "frozen" in r.json()["detail"]
    sh = mark(alice, sh, row["candidates"][0], frozen=False)
    r = alice.post(f"/api/sheets/{sh['id']}/slots/{row['id']}/reroll",
                   json={"instruction": "holding a short sword and a round shield"}, headers=H)
    assert r.status_code == 202
    assert r.json()["slots"][1]["label"] == "holding a short sword and a round shield"
    assert sched.submitted[-1][1][0]["prompt"].endswith(": holding a short sword and a round shield")


def test_base_rules(world, alice):
    sh = new(alice)
    c = base_slot(sh)["candidates"][0]
    assert alice.post(f"/api/sheets/{sh['id']}/base", json={"candidate_id": c["id"]}, headers=H).status_code == 422
    assert alice.post(f"/api/sheets/{sh['id']}/slots", json={"lines": ["x"]}, headers=H).status_code == 409
    finish_all(world)
    sh = alice.post(f"/api/sheets/{sh['id']}/base", json={"candidate_id": c["id"]}, headers=H).json()
    alice.post(f"/api/sheets/{sh['id']}/slots", json={"lines": ["running"]}, headers=H)
    other = base_slot(sh)["candidates"][2]
    r = alice.post(f"/api/sheets/{sh['id']}/base", json={"candidate_id": other["id"]}, headers=H)
    assert r.status_code == 409                                                  # variations are edits of the old one
    assert alice.delete(f"/api/sheets/{sh['id']}/slots/{base_slot(sh)['id']}", headers=H).status_code == 422


def test_from_an_existing_image(world, alice):
    app, _, sched, _ = world
    a = alice.post("/api/generate", json={"prompt": "a bat", "preset": "sprite", "background": "green",
                                          "model": "qwen-image-2512"},
                   headers=H).json()["artifacts"][0]
    finish_all(world)
    n = len(sched.submitted)
    sh = new(alice, description=None, base_artifact_id=a["id"])
    assert sh["base_artifact_id"] == a["id"] and len(sched.submitted) == n and sh["background"] == "green"
    assert base_slot(sh)["candidates"][0]["kept"] and sh["slots"][0]["instruction"] == "a bat"
    assert sh["model"] == "qwen-image-2512" and sh["edit_model"] == "flux2-klein-4b"
    assert alice.post("/api/sheets", json={"name": "x"}, headers=H).status_code == 422     # no description, no image


def test_keep_and_export_at_the_base_scale(world, alice):
    app = world[0]
    sh = with_base(world, alice)                                                 # base subject 120 px tall
    sh = alice.post(f"/api/sheets/{sh['id']}/slots", json={"lines": ["crouching", "jumping"]}, headers=H).json()
    sched = world[2]
    # crouching: subjects 60 px tall (half the base); jumping: 200 px, taller than the base's scale allows in 64 px.
    # Jobs are submitted in row order, so the first 4 queued are the crouching row's.
    queued = [jid for jid, j in sched.jobs.items() if j["status"] == "queued"]
    for jid in queued[:4]:
        sched.finish(jid, uploaded=False, png=scene(60))
    for jid in queued[4:]:
        sched.finish(jid, uploaded=False, png=scene(200))
    app.state.poller.poll_once()
    sh = get(alice, sh)
    mark(alice, sh, sh["slots"][1]["candidates"][0], kept=True)
    mark(alice, sh, sh["slots"][1]["candidates"][1], kept=True)
    sh = mark(alice, sh, sh["slots"][2]["candidates"][0], kept=True)
    assert sh["kept"] == 4                                                       # + the base, kept when picked
    r = alice.post(f"/api/sheets/{sh['id']}/export", headers=H)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["files"] == 8 and out["shrunk"] == ["jumping_1_64px.png"]
    z = zipfile.ZipFile(io.BytesIO(TestClient(app).get(out["download_url"]).content))   # no session needed
    names = sorted(z.namelist())
    assert "base_1_64px.png" in names and "crouching_2_cutout.png" in names
    def subject_h(name):
        im = Image.open(io.BytesIO(z.read(name)))
        assert im.size == (64, 64)
        box = im.getchannel("A").getbbox()
        assert box[3] == 64                                                      # standing on the bottom row
        return box[3] - box[1]
    base_h, crouch_h, jump_h = subject_h("base_1_64px.png"), subject_h("crouching_1_64px.png"), subject_h("jumping_1_64px.png")
    assert base_h == 48 and 22 <= crouch_h <= 27 and jump_h == 64                # base 3/4 of the tile; half; fitted


def test_who_sees_and_changes_a_sheet(world, alice):
    from tests.conftest import sign_in
    bob = sign_in(world, "bob")
    sh = with_base(world, alice)
    assert bob.get(f"/api/sheets/{sh['id']}").status_code == 404                 # in alice's private Scratch
    proj = alice.post("/api/projects", json={"name": "Game", "visibility": "public"}, headers=H).json()
    pub = new(alice, project_id=proj["id"])
    assert bob.get(f"/api/sheets/{pub['id']}").json()["can_edit"] is False
    assert bob.patch(f"/api/sheets/{pub['id']}", json={"name": "mine"}, headers=H).status_code == 403
    assert [x["id"] for x in bob.get(f"/api/sheets?project_id={proj['id']}").json()["sheets"]] == [pub["id"]]
    assert {x["id"] for x in alice.get("/api/sheets").json()["sheets"]} == {sh["id"], pub["id"]}
    arts_before = alice.get("/api/artifacts").json()
    assert alice.delete(f"/api/sheets/{sh['id']}", headers=H).status_code == 200
    assert alice.get(f"/api/sheets/{sh['id']}").status_code == 404
    assert len(alice.get("/api/artifacts").json()["artifacts"]) == len(arts_before["artifacts"])   # images stay


def test_sheet_over_mcp(world):
    """The agent's version of the flow; jobs finish as soon as they are read (auto_finish)."""
    from tests.conftest import Mcp, make_token, sign_in
    app, _, sched, _ = world
    sched.auto_finish = True
    web = sign_in(world, "carol")
    with TestClient(app) as http:
        _sheet_flow(Mcp(http, make_token(web)))


def _sheet_flow(mcp):
    sh = mcp.call("create_sheet", name="Grub", description="a goblin archer", wait_seconds=5)
    base_row = sh["rows"][0]
    assert base_row["kind"] == "base" and len(base_row["candidates"]) == 4
    assert all(c["status"] == "done" for c in base_row["candidates"])
    first = base_row["candidates"][0]["candidate_id"]
    sh = mcp.call("sheet_mark", sheet_id=sh["sheet_id"], freeze=[first])
    sh = mcp.call("sheet_reroll", sheet_id=sh["sheet_id"], row_id=base_row["row_id"], wait_seconds=5)
    assert sh["rows"][0]["candidates"][0]["frozen"]
    sh = mcp.call("sheet_set_base", sheet_id=sh["sheet_id"], candidate_id=first)
    sh = mcp.call("sheet_add_variations", sheet_id=sh["sheet_id"], variations=["running", "laughing"], wait_seconds=10)
    assert [r["label"] for r in sh["rows"][1:]] == ["running", "laughing"]
    keep = [sh["rows"][1]["candidates"][0]["candidate_id"]]
    sh = mcp.call("sheet_mark", sheet_id=sh["sheet_id"], keep=keep)
    out = mcp.call("export_sheet", sheet_id=sh["sheet_id"], pixel_size=32)
    assert out["files"] == 4 and out["download_url"].startswith("http://testserver/dl/gns_")
    assert "isn't a candidate" in mcp.error("sheet_mark", sheet_id=sh["sheet_id"], keep=["nope"])
