"""Sprite clean-up (design §5): the flat background keyed out, then optionally shrunk to a pixel grid.

From the prototype (ai/model-test-scripts/flux2_klein/spriteify.py), with what its first runs showed:
- The key colour is sampled from the corners, never assumed: asked for #ff00ff, klein paints anything from magenta to
  hot pink.
- Keying is by hue, not by distance to one colour: a drop shadow is a darker shade of the background (the treasure
  chest's survived distance keying), and it has the same hue. A pixel goes when its hue is near the key's, it is clearly
  saturated, and not near-black.
- Shrinking averages (BOX) and then reduces the palette; nearest-neighbour picks one source pixel per cell and turns
  detail into speckle.
"""

from __future__ import annotations

import colorsys
import io
from dataclasses import dataclass

import numpy as np
from PIL import Image

HUE_TOLERANCE = 0.07          # of the hue circle (~25°)
MIN_SATURATION = 0.35
MIN_VALUE = 0.15
ALPHA_CUTOFF = 128


@dataclass(frozen=True)
class PixelParams:
    size: int = 64            # longest side (fit) or the square's side (square)
    colors: int = 24          # 0 = keep every colour
    canvas: str = "fit"       # fit: cropped to the subject. square: size×size, subject centred, standing on the bottom row

    def check(self) -> str | None:
        if not 8 <= self.size <= 512:
            return "Size must be 8–512 px"
        if not 0 <= self.colors <= 256 or self.colors == 1:
            return "Colours must be 0 (all) or 2–256"
        if self.canvas not in ("fit", "square"):
            return "Canvas is fit or square"
        return None


def key_colour(im: Image.Image) -> tuple[int, int, int]:
    """Median of small patches in the four corners."""
    a = np.asarray(im.convert("RGB"))
    h, w = a.shape[:2]
    m = max(2, min(h, w) // 64)
    patches = np.concatenate([a[:m, :m], a[:m, w - m:], a[h - m:, :m], a[h - m:, w - m:]]).reshape(-1, 3)
    r, g, b = np.median(patches, axis=0).astype(int)
    return int(r), int(g), int(b)


def cutout(png: bytes) -> tuple[bytes, tuple[int, int, int]]:
    """The image with its background transparent, same canvas. Returns (PNG, the key colour found)."""
    im = Image.open(io.BytesIO(png)).convert("RGB")
    key = key_colour(im)
    kh, ks, _ = colorsys.rgb_to_hsv(*(c / 255 for c in key))
    hsv = np.asarray(im.convert("HSV"), dtype=np.float32) / 255.0
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    dh = np.abs(h - kh)
    dh = np.minimum(dh, 1.0 - dh)                         # hue is a circle
    if ks < MIN_SATURATION:                               # a grey/white key: fall back to colour distance
        rgb = np.asarray(im, dtype=np.int32)
        bg = np.sum((rgb - np.array(key)) ** 2, axis=-1) < 60 ** 2
    else:
        bg = (dh < HUE_TOLERANCE) & (s > MIN_SATURATION) & (v > MIN_VALUE)
    alpha = np.where(bg, 0, 255).astype(np.uint8)
    out = im.convert("RGBA")
    out.putalpha(Image.fromarray(alpha))
    buf = io.BytesIO()
    out.save(buf, format="PNG", optimize=True)
    return buf.getvalue(), key


def pixelate(cut_png: bytes, p: PixelParams) -> bytes:
    """A pixel-grid sprite from a cutout: cropped to the subject, BOX-downscaled, palette-reduced, hard alpha."""
    return pixelate_at(cut_png, p)[0]


def subject_scale(cut_png: bytes, size: int) -> float:
    """The factor that fits this cutout's subject to `size` px on its longest side: a character sheet applies the
    base's factor to every variation, so their sizes stay comparable (design §11)."""
    im = Image.open(io.BytesIO(cut_png)).convert("RGBA")
    bbox = im.getchannel("A").getbbox() or (0, 0, im.width, im.height)
    return size / max(bbox[2] - bbox[0], bbox[3] - bbox[1])


def pixelate_at(cut_png: bytes, p: PixelParams, scale: float | None = None) -> tuple[bytes, bool]:
    """As pixelate; `scale` (output px per source px) instead of fitting this subject to p.size. Returns (PNG, shrunk):
    shrunk when the scaled subject was bigger than the tile and had to be fitted after all."""
    im = Image.open(io.BytesIO(cut_png)).convert("RGBA")
    if bbox := im.getchannel("A").getbbox():
        im = im.crop(bbox)
    fit = p.size / max(im.size)
    shrunk = scale is not None and scale > fit
    k = fit if scale is None or shrunk else scale
    small = im.resize((max(1, round(im.width * k)), max(1, round(im.height * k))), Image.BOX)
    alpha = small.getchannel("A").point(lambda a: 255 if a >= ALPHA_CUTOFF else 0)
    rgb = small.convert("RGB")
    if p.colors:
        rgb = rgb.quantize(p.colors, method=Image.Quantize.MEDIANCUT).convert("RGB")
    sprite = rgb.convert("RGBA")
    sprite.putalpha(alpha)
    if p.canvas == "square":
        canvas = Image.new("RGBA", (p.size, p.size), (0, 0, 0, 0))
        canvas.paste(sprite, ((p.size - sprite.width) // 2, p.size - sprite.height), sprite)
        sprite = canvas
    buf = io.BytesIO()
    sprite.save(buf, format="PNG", optimize=True)
    return buf.getvalue(), shrunk


def size_of(png: bytes) -> tuple[int, int]:
    return Image.open(io.BytesIO(png)).size
