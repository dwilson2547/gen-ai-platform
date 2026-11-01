"""Sprite presets, key colours, the automatic cutout and pixel-grid versions (design §5)."""
import base64
import io

from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from genai import sprites
from tests.conftest import H, Mcp, make_token


def scene(bg=(250, 20, 150)) -> bytes:
    """A 256² sprite render: hot-pink background, a darker pink drop shadow, a green subject with a dark outline."""
    im = Image.new("RGB", (256, 256), bg)
    d = ImageDraw.Draw(im)
    d.ellipse((70, 200, 190, 230), fill=(150, 10, 90))                 # shadow: same hue, darker
    d.rectangle((96, 60, 160, 210), fill=(40, 200, 60), outline=(10, 10, 10), width=4)
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def finished(world, client, **body):
    app, _, sched, _ = world
    a = client.post("/api/generate", json={"prompt": "a goblin", "preset": "sprite", **body}, headers=H).json()["artifacts"][0]
    sched.finish(f"job-{len(sched.submitted)}", uploaded=False, png=base64.b64encode(scene()).decode())
    app.state.poller.poll_once()
    return client.get(f"/api/artifacts/{a['id']}").json()


def test_cutout_removes_background_and_shadow_keeps_subject():
    cut, key = sprites.cutout(scene())
    assert key == (250, 20, 150)
    im = Image.open(io.BytesIO(cut))
    assert im.mode == "RGBA" and im.size == (256, 256)
    a = im.getchannel("A")
    assert a.getpixel((5, 5)) == 0 and a.getpixel((80, 215)) == 0       # background and shadow gone
    assert a.getpixel((128, 128)) == 255 and a.getpixel((97, 100)) == 255   # subject and its outline kept


def test_pixelate_fit_and_square():
    cut, _ = sprites.cutout(scene())
    fit = Image.open(io.BytesIO(sprites.pixelate(cut, sprites.PixelParams(32, 8, "fit"))))
    assert max(fit.size) == 32 and fit.size[0] < 32                    # cropped to the tall subject
    sq = Image.open(io.BytesIO(sprites.pixelate(cut, sprites.PixelParams(32, 8, "square"))))
    assert sq.size == (32, 32)
    assert sq.getchannel("A").getpixel((16, 31)) == 255                  # standing on the bottom row
    assert sq.getchannel("A").getpixel((0, 0)) == 0
    assert len({p[:3] for p in sq.convert("RGBA").get_flattened_data() if p[3]}) <= 8


def test_presets_and_backgrounds(world, alice):
    sched = world[2]
    cat = alice.get("/api/catalogue").json()
    assert {p["name"]: p["sprite"] for p in cat["presets"]} == {"none": False, "sprite": True, "item_sprite": True}
    assert cat["backgrounds"] == ["magenta", "green"]
    alice.post("/api/generate", json={"prompt": "a treasure map", "preset": "item_sprite", "background": "green"}, headers=H)
    p = sched.submitted[-1][1][0]["prompt"]
    assert "a single object centred" in p and "(#00ff00)" in p and "full body" not in p
    assert alice.post("/api/generate", json={"prompt": "x", "background": "blue"}, headers=H).status_code == 422


def test_rerolls_and_prompt_edits_keep_the_background(world, alice):
    sched = world[2]
    a = finished(world, alice, background="green")
    assert a["background"] == "green"
    alice.post(f"/api/artifacts/{a['id']}/tweak", json={"op": "reroll"}, headers=H)
    assert sched.submitted[-1][1][0]["prompt"] == a["full_prompt"]          # the exact stored prompt
    alice.post(f"/api/artifacts/{a['id']}/tweak", json={"op": "edit_prompt", "prompt": "a hobgoblin"}, headers=H)
    assert "(#00ff00)" in sched.submitted[-1][1][0]["prompt"] and "hobgoblin" in sched.submitted[-1][1][0]["prompt"]


def test_sprites_get_a_cutout_automatically(world, alice):
    a = finished(world, alice)
    [v] = a["variants"]
    assert v["kind"] == "cutout" and v["params"]["key"] == "#fa1496" and (v["width"], v["height"]) == (256, 256)
    img = Image.open(io.BytesIO(alice.get(v["image_url"]).content))
    assert img.getchannel("A").getpixel((5, 5)) == 0
    plain = alice.post("/api/generate", json={"prompt": "a landscape"}, headers=H).json()["artifacts"][0]
    world[2].finish(f"job-{len(world[2].submitted)}", uploaded=False)
    world[0].state.poller.poll_once()
    assert alice.get(f"/api/artifacts/{plain['id']}").json()["variants"] == []      # not for plain images


def test_pixel_versions_owner_only_replace_and_delete(world, alice, bob):
    pub = alice.post("/api/projects", json={"name": "Shared", "visibility": "public"}, headers=H).json()
    a = finished(world, alice, project_id=pub["id"])
    r = alice.post(f"/api/artifacts/{a['id']}/variants", json={"size": 48, "colors": 16, "canvas": "square"}, headers=H)
    assert r.status_code == 201 and (r.json()["width"], r.json()["height"]) == (48, 48)
    again = alice.post(f"/api/artifacts/{a['id']}/variants", json={"size": 48, "colors": 16, "canvas": "square"}, headers=H)
    kinds = [v["kind"] for v in alice.get(f"/api/artifacts/{a['id']}").json()["variants"]]
    assert kinds == ["cutout", "pixel"]                                        # same settings replaced, not added
    assert bob.post(f"/api/artifacts/{a['id']}/variants", json={}, headers=H).status_code == 403
    assert bob.get(again.json()["image_url"]).status_code == 200               # public: bob can view it
    assert bob.delete(f"/api/variants/{again.json()['id']}", headers=H).status_code == 403
    assert alice.post(f"/api/artifacts/{a['id']}/variants", json={"canvas": "round"}, headers=H).status_code == 422
    assert alice.delete(f"/api/variants/{again.json()['id']}", headers=H).status_code == 200
    assert [v["kind"] for v in alice.get(f"/api/artifacts/{a['id']}").json()["variants"]] == ["cutout"]


def test_mcp_make_sprite_and_variant_downloads(world):
    app, _, sched, _ = world
    from tests.conftest import sign_in
    web = sign_in(world, "alice")
    with TestClient(app) as http:
        mcp = Mcp(http, make_token(web))
        a = finished(world, web)
        out = mcp.call("make_sprite", artifact_id=a["id"], size=32, canvas="square")
        pixel = [v for v in out["variants"] if v["kind"] == "pixel"][0]
        assert pixel["id"] == out["made"] and (pixel["width"], pixel["size"], pixel["canvas"]) == (32, 32, "square")
        dl = TestClient(app).get(pixel["download_url"].replace("http://testserver", ""))
        assert dl.status_code == 200 and Image.open(io.BytesIO(dl.content)).size == (32, 32)
        assert "-32x32.png" in dl.headers["content-disposition"]
        img = mcp.raw("get_image", artifact_id=a["id"], variant=pixel["id"])["content"][0]
        assert Image.open(io.BytesIO(base64.b64decode(img["data"]))).size == (512, 512)   # enlarged 16x to the preview size
        bob = Mcp(http, make_token(sign_in(world, "bob")))
        assert "No such artifact" in bob.error("make_sprite", artifact_id=a["id"])


def test_an_edit_of_a_sprite_is_cleaned_up_as_a_sprite(world, alice):
    app, _, sched, _ = world
    a = finished(world, alice, background="green")
    e = alice.post(f"/api/artifacts/{a['id']}/tweak", json={"op": "edit_image", "prompt": "same goblin, waving"},
                   headers=H).json()["artifacts"][0]
    assert sched.submitted[-1][1][0]["prompt"] == "same goblin, waving"           # only the instruction
    assert e["preset"] == "sprite" and e["background"] is None
    sched.finish(f"job-{len(sched.submitted)}", uploaded=False, png=base64.b64encode(scene()).decode())
    app.state.poller.poll_once()
    assert [v["kind"] for v in alice.get(f"/api/artifacts/{e['id']}").json()["variants"]] == ["cutout"]
    # re-wording the edit redoes it from the same source, again with only the instruction
    alice.post(f"/api/artifacts/{e['id']}/tweak", json={"op": "edit_prompt", "prompt": "same goblin, jumping"}, headers=H)
    item = sched.submitted[-1][1][0]
    assert item["prompt"] == "same goblin, jumping" and item["ref_images"]
