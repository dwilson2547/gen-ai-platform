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


MODELS: dict[str, Model] = {m.name: m for m in [
    Model("flux2-klein-4b", "FLUX.2 klein 4B", "Apache-2.0", True, "Fast. ~3 s per image."),
    Model("flux2-klein-9b", "FLUX.2 klein 9B", "FLUX Non-Commercial License", False,
          "Better at hard prompts, ~2x slower. Its images may not be used commercially."),
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

SIZES = [(1024, 1024), (768, 1024), (1024, 768), (512, 512), (1024, 1536), (1536, 1024)]


def public() -> dict:
    return {
        "models": [{"name": m.name, "label": m.label, "licence": m.licence, "commercial": m.commercial,
                    "note": m.note, "default": m.name == DEFAULT_MODEL} for m in MODELS.values()],
        "presets": [{"name": p.name, "label": p.label, "hint": p.hint, "template": p.template,
                     "width": p.width, "height": p.height, "sprite": p.name in SPRITE_PRESETS}
                    for p in PRESETS.values()],
        "backgrounds": list(BACKGROUNDS),
        "sizes": [{"width": w, "height": h} for w, h in SIZES],
    }


def background_of(full_prompt: str) -> str:
    """Which key colour an artifact was generated on, read back from its full prompt (no column needed)."""
    return "green" if "(#00ff00)" in full_prompt else "magenta"
