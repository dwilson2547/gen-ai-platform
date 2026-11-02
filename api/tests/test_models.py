"""Model differences (design §4a): Qwen-Image's sprite size, and that it can't edit (its pictures are edited by FLUX)."""
from tests.conftest import H
from tests.test_tweak import done, tweak

QWEN = "qwen-image-2512"


def test_catalogue_lists_qwen_as_text_to_image(alice):
    models = {m["name"]: m for m in alice.get("/api/catalogue").json()["models"]}
    assert models[QWEN]["edits"] is False and models[QWEN]["sprite_size"] == 512 and models[QWEN]["commercial"]
    assert models["flux2-klein-4b"]["edits"] is True and models["flux2-klein-4b"]["default"]


def test_default_sizes(world, alice):
    sched = world[2]
    for body, size in [({"model": QWEN, "preset": "sprite"}, (512, 512)),
                       ({"model": QWEN, "preset": "none"}, (1024, 1024)),
                       ({"model": QWEN, "preset": "item_sprite", "width": 768, "height": 768}, (768, 768)),
                       ({"preset": "sprite"}, (1024, 1024))]:
        a = alice.post("/api/generate", json={"prompt": "a goblin", **body}, headers=H).json()["artifacts"][0]
        model, inputs = sched.submitted[-1]
        assert (a["width"], a["height"]) == size and (inputs[0]["width"], inputs[0]["height"]) == size
        assert model == body.get("model", "flux2-klein-4b")


def test_a_qwen_picture_is_edited_by_flux(world, alice):
    sched = world[2]
    a = done(world, alice, model=QWEN)
    k = tweak(alice, a, op="edit_image", prompt="same goblin, holding an axe").json()["artifacts"][0]
    model, inputs = sched.submitted[-1]
    assert model == "flux2-klein-4b" and k["model"] == "flux2-klein-4b" and inputs[0]["ref_images"]
    assert (k["width"], k["height"]) == (512, 512) and k["preset"] == "sprite"
    r = tweak(alice, a, op="edit_image", prompt="same goblin", model=QWEN)                 # asked for explicitly
    assert r.status_code == 422 and "can't edit" in r.json()["detail"]


def test_rerolls_of_a_qwen_picture_stay_qwen(world, alice):
    sched = world[2]
    a = done(world, alice, model=QWEN)
    tweak(alice, a, op="reroll")
    assert sched.submitted[-1][0] == QWEN and "ref_images" not in sched.submitted[-1][1][0]
