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

    def apply(self, prompt: str) -> str:
        return self.template.replace("{prompt}", prompt.strip())


# The sprite template is the prototype's (ai/model-test-scripts/flux2_klein/gen.sh). Its background key-out and
# downscale are post-processing, phase 4.
PRESETS: dict[str, Preset] = {p.name: p for p in [
    Preset("none", "Plain", hint="Your prompt, as written."),
    Preset("sprite", "Game sprite",
           "pixel art game sprite of {prompt}, full body, single character centred, side view, clean outlines, "
           "limited palette, flat solid magenta (#ff00ff) background, no shadow, no text",
           hint="Describe the character or object, e.g. \"a goblin archer\"."),
]}
DEFAULT_PRESET = "none"

SIZES = [(1024, 1024), (768, 1024), (1024, 768), (512, 512), (1024, 1536), (1536, 1024)]


def public() -> dict:
    return {
        "models": [{"name": m.name, "label": m.label, "licence": m.licence, "commercial": m.commercial,
                    "note": m.note, "default": m.name == DEFAULT_MODEL} for m in MODELS.values()],
        "presets": [{"name": p.name, "label": p.label, "hint": p.hint, "template": p.template,
                     "width": p.width, "height": p.height} for p in PRESETS.values()],
        "sizes": [{"width": w, "height": h} for w, h in SIZES],
    }
