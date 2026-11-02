"""What can be generated: the models (gpu-scheduler names) and the presets (design §4, §5)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Model:
    name: str                      # the gpu-scheduler model
    label: str
    licence: str
    commercial: bool
    note: str = ""
    edits: bool = True             # takes a reference image (edit_image); text-to-image-only models don't
    sprite_size: int | None = None # default square side for sprite presets, when the caller gives no size


MODELS: dict[str, Model] = {m.name: m for m in [
    Model("flux2-klein-4b", "FLUX.2 klein 4B", "Apache-2.0", True, "Fast. ~3 s per image."),
    Model("flux2-klein-9b", "FLUX.2 klein 9B", "FLUX Non-Commercial License", False,
          "Better at hard prompts, ~2x slower. Its images may not be used commercially."),
    # On the 3080 (design §4a): ~4 s per 512² image warm, ~12 s at 1024². Sprites end at 64 px, so 512².
    Model("qwen-image-2512", "Qwen-Image 2512", "Apache-2.0", True,
          "Cleaner pixel art. ~4 s per sprite (512²), ~12 s at 1024². Can't edit images: edits of its pictures are "
          "made with FLUX.2 klein 4B.", edits=False, sprite_size=512),
]}
DEFAULT_MODEL = "flux2-klein-4b"


@dataclass(frozen=True)
class Preset:
    name: str
    label: str
    template: str = "{prompt}"
    width: int = 1024
    height: int = 1024
    hint: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)

    def apply(self, prompt: str, background: str = "magenta") -> str:
        return self.template.replace("{prompt}", prompt.strip()).replace("{bg}", BACKGROUNDS[background])


# Sprite templates come from the prototype (ai/model-test-scripts/flux2_klein/gen.sh). {bg} is the key colour the
# clean-up removes (sprites.py): magenta by default, green for pink/purple subjects. Two templates because the
# character one ("full body, side view") turned "a treasure map" into a person. The "sprite" name stays for characters
# so existing artifacts keep their preset.
BACKGROUNDS = {"magenta": "flat solid magenta (#ff00ff) background", "green": "flat solid green (#00ff00) background"}
DEFAULT_BACKGROUND = "magenta"
SPRITE_PRESETS = ("sprite", "item_sprite")

PRESETS: dict[str, Preset] = {p.name: p for p in [
    Preset("none", "Plain", hint="Your prompt, as written."),
    Preset("sprite", "Character sprite",
           "pixel art game sprite of {prompt}, full body, single character centred, side view, clean outlines, "
           "limited palette, {bg}, no shadow, no text",
           hint="Describe the character, e.g. \"a goblin archer\"."),
    Preset("item_sprite", "Item sprite",
           "pixel art game sprite of {prompt}, a single object centred, clean outlines, limited palette, {bg}, "
           "no shadow, no text",
           hint="Describe the object, e.g. \"a treasure map\", \"a red potion\"."),
]}
DEFAULT_PRESET = "none"

def default_size(model: str, preset: str) -> tuple[int, int]:
    m, p = MODELS.get(model), PRESETS[preset]
    if m and m.sprite_size and preset in SPRITE_PRESETS:
        return m.sprite_size, m.sprite_size
    return p.width, p.height


SIZES = [(1024, 1024), (768, 1024), (1024, 768), (512, 512), (1024, 1536), (1536, 1024)]


def public() -> dict:
    return {
        "models": [{"name": m.name, "label": m.label, "licence": m.licence, "commercial": m.commercial,
                    "note": m.note, "default": m.name == DEFAULT_MODEL, "edits": m.edits,
                    "sprite_size": m.sprite_size} for m in MODELS.values()],
        "presets": [{"name": p.name, "label": p.label, "hint": p.hint, "template": p.template,
                     "width": p.width, "height": p.height, "sprite": p.name in SPRITE_PRESETS}
                    for p in PRESETS.values()],
        "backgrounds": list(BACKGROUNDS),
        "sizes": [{"width": w, "height": h} for w, h in SIZES],
    }


def background_of(full_prompt: str) -> str | None:
    """Which key colour an artifact was generated on, read back from its full prompt (no column needed). None for an
    edit, whose full prompt is just the instruction (its background is its source's)."""
    if "(#00ff00)" in full_prompt:
        return "green"
    if "(#ff00ff)" in full_prompt:
        return "magenta"
    return None
