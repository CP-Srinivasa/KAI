"""Rasterize kai-mark / kai-wordmark to PNG without cairo.

Pure-Pillow rendering of the same geometry used in the SVGs, so the PNG
and the SVG stay visually identical. Anti-aliasing via 4x supersampling.
"""

from __future__ import annotations

from pathlib import Path
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent
FG = (17, 17, 17, 255)   # #111
FG_LIGHT = (245, 245, 245, 255)  # for dark-mode export
BG_TRANSPARENT = (0, 0, 0, 0)
SS = 4  # 4x supersample for AA


def _draw_mark(draw: ImageDraw.ImageDraw, scale: float, color=FG) -> None:
    """Draw KAI mark into a 128x128 logical canvas, pre-scaled by `scale`."""
    def S(*pts):
        return [(x * scale, y * scale) for (x, y) in pts]

    # K stem
    draw.rectangle(S((22, 18), (38, 110)), fill=color)
    # K upper diagonal — extends above top edge as chevron
    draw.polygon(S((38, 64), (96, 6), (110, 22), (52, 80)), fill=color)
    # K lower diagonal
    draw.polygon(S((38, 64), (82, 110), (66, 110), (30, 72)), fill=color)


def _draw_wordmark(draw: ImageDraw.ImageDraw, scale: float, color=FG) -> None:
    """Draw full KAI wordmark into a 320x96 logical canvas, pre-scaled."""
    def S(*pts):
        return [(x * scale, y * scale) for (x, y) in pts]

    # K
    draw.rectangle(S((14, 10), (28, 86)), fill=color)
    draw.polygon(S((28, 48), (78, 4), (90, 16), (40, 60)), fill=color)
    draw.polygon(S((28, 48), (70, 90), (56, 90), (22, 56)), fill=color)

    # A — two legs as trapezoids + crossbar + apex cap
    draw.polygon(S((128, 90), (142, 90), (166, 18), (152, 18)), fill=color)
    draw.polygon(S((162, 18), (176, 18), (200, 90), (186, 90)), fill=color)
    draw.rectangle(S((144, 54), (184, 64)), fill=color)
    draw.polygon(S((152, 18), (176, 18), (164, 6)), fill=color)

    # I
    draw.rectangle(S((222, 10), (236, 86)), fill=color)


def render_mark(out: Path, size: int, color=FG) -> None:
    big = size * SS
    img = Image.new("RGBA", (big, big), BG_TRANSPARENT)
    draw = ImageDraw.Draw(img)
    _draw_mark(draw, big / 128, color=color)
    img = img.resize((size, size), Image.LANCZOS)
    img.save(out, "PNG")
    print(f"wrote {out} ({size}x{size})")


def render_wordmark(out: Path, width: int, color=FG) -> None:
    height = int(width * 96 / 320)
    big_w, big_h = width * SS, height * SS
    img = Image.new("RGBA", (big_w, big_h), BG_TRANSPARENT)
    draw = ImageDraw.Draw(img)
    _draw_wordmark(draw, big_w / 320, color=color)
    img = img.resize((width, height), Image.LANCZOS)
    img.save(out, "PNG")
    print(f"wrote {out} ({width}x{height})")


if __name__ == "__main__":
    render_mark(ROOT / "kai-mark.png", 512)
    render_mark(ROOT / "kai-mark-256.png", 256)
    render_mark(ROOT / "kai-mark-64.png", 64)
    render_mark(ROOT / "kai-mark-32.png", 32)
    render_mark(ROOT / "kai-mark-light.png", 512, color=FG_LIGHT)
    render_wordmark(ROOT / "kai-wordmark.png", 800)
    render_wordmark(ROOT / "kai-wordmark-light.png", 800, color=FG_LIGHT)
