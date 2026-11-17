#!/usr/bin/env python3
"""Generate the Bolo brand assets (wordmark, mark, inverse lockup, imprint, favicon).

Bolo is a BSD-2-Clause white-label fork of an upstream voice AI platform. The
upstream lettering is a commercial typeface, so this script does not reuse any
upstream artwork: it draws the Bolo mark from scratch and renders the wordmark
with a locally installed geometric sans.

Usage:
    python3 scripts/brand/generate_brand_assets.py

Requires Pillow. Re-runnable: overwrites the generated files in place.
"""

from __future__ import annotations

import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
PUBLIC = ROOT / "ui" / "public"
DOCS = ROOT / "docs"

# --- Bolo palette (Teal Green) ---------------------------------------------
TEAL_DEEP = (15, 118, 110)  # #0F766E — wordmark on light surfaces
TEAL = (13, 148, 136)  # #0D9488 — primary brand teal
TEAL_BRIGHT = (20, 184, 166)  # #14B8A6 — dark-mode brand teal
GREEN = (16, 185, 129)  # #10B981 — accent green
WHITE = (255, 255, 255)

# Font preference order: geometric upright first, then widely available
# fallbacks. Each entry is (path, ttc_index); ttc_index is ignored for .ttf.
WORDMARK_FONTS = [
    ("/System/Library/Fonts/Supplemental/Avenir Next.ttc", 2),  # Avenir Next Demi Bold
    ("/System/Library/Fonts/Supplemental/Futura.ttc", 2),  # Futura Bold
    ("/System/Library/Fonts/HelveticaNeue.ttc", 0),
    ("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 0),
]


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    for path, index in WORDMARK_FONTS:
        if not os.path.exists(path):
            continue
        try:
            if path.endswith(".ttc"):
                return ImageFont.truetype(path, size, index=index)
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _rounded_bar(draw: ImageDraw.ImageDraw, box, radius: int) -> None:
    draw.rounded_rectangle(box, radius=radius, fill=WHITE)


def draw_mark(size: int = 500) -> Image.Image:
    """Rounded teal tile holding a five-bar voice waveform."""
    scale = 4
    s = size * scale
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # Tile
    d.rounded_rectangle((0, 0, s - 1, s - 1), radius=int(s * 0.24), fill=TEAL + (255,))

    # Waveform: five rounded bars, tallest in the middle.
    bar_w = int(s * 0.072)
    gap = int(s * 0.048)
    heights = [0.30, 0.47, 0.62, 0.47, 0.30]
    total_w = len(heights) * bar_w + (len(heights) - 1) * gap
    x = (s - total_w) // 2
    cy = s // 2
    for frac in heights:
        h = int(s * frac)
        top = cy - h // 2
        _rounded_bar(d, (x, top, x + bar_w, top + h), radius=bar_w // 2)
        x += bar_w + gap

    return img.resize((size, size), Image.LANCZOS)


def _draw_wordmark(
    d: ImageDraw.ImageDraw,
    text: str,
    x: int,
    y: int,
    font: ImageFont.FreeTypeFont,
    fill,
    tracking: int = 0,
) -> int:
    """Draw `text` with manual letter tracking; returns the advance width."""
    cursor = x
    for ch in text:
        d.text((cursor, y), ch, font=font, fill=fill)
        advance = int(d.textlength(ch, font=font))
        cursor += advance + tracking
    return cursor - x - (tracking if text else 0)


def _wordmark_size(draw: ImageDraw.ImageDraw, text: str, font) -> tuple[int, int]:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def build_logo(inverse: bool = False) -> Image.Image:
    """1000x250 lockup: mark + 'bolo' wordmark."""
    scale = 4
    W, H = 1000 * scale, 250 * scale
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    fill = WHITE if inverse else TEAL_DEEP
    mark = draw_mark(500).resize((190 * scale, 190 * scale), Image.LANCZOS)
    img.alpha_composite(mark, (8 * scale, (250 - 190) // 2 * scale))

    font = _load_font(150 * scale)
    word = "bolo"
    tracking = 6 * scale
    # Shrink to fit the lockup width.
    while True:
        w, _ = _wordmark_size(d, word, font)
        approx = w + tracking * (len(word) - 1)
        if approx <= (W - 240 * scale) or font.size <= 40 * scale:
            break
        font = _load_font(int(font.size * 0.92))
    _, th = _wordmark_size(d, word, font)
    bbox = d.textbbox((0, 0), word, font=font)
    y = (H - th) // 2 - bbox[1]
    _draw_wordmark(d, word, 235 * scale, y, font, fill, tracking)

    return img.resize((1000, 250), Image.LANCZOS)


def build_imprint(dark: bool = False) -> Image.Image:
    """Giant faded 'bolo' wordmark used as a background watermark."""
    scale = 3
    W, H = 1000 * scale, 282 * scale
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    fill = TEAL_BRIGHT + (26,) if dark else TEAL_DEEP + (23,)
    font = _load_font(300 * scale)
    w, th = _wordmark_size(d, "bolo", font)
    bbox = d.textbbox((0, 0), "bolo", font=font)
    x = (W - w) // 2 - bbox[0]
    y = (H - th) // 2 - bbox[1]
    _draw_wordmark(d, "bolo", x, y, font, fill, 10 * scale)
    return img.resize((1000, 282), Image.LANCZOS)


def build_favicon() -> Image.Image:
    return draw_mark(256)


def build_logo_svg(dark: bool = False) -> str:
    fill = "#14B8A6" if dark else "#0F766E"
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 32 32">'
        f'<rect width="32" height="32" rx="7.7" fill="{fill}"/>'
        '<g fill="#ffffff">'
        '<rect x="9.1" y="12.6" width="2.3" height="6.8" rx="1.15"/>'
        '<rect x="13.0" y="9.9" width="2.3" height="12.2" rx="1.15"/>'
        '<rect x="16.9" y="7.2" width="2.3" height="17.6" rx="1.15"/>'
        '<rect x="20.8" y="9.9" width="2.3" height="12.2" rx="1.15"/>'
        '<rect x="24.7" y="12.6" width="2.3" height="6.8" rx="1.15"/>'
        "</g></svg>"
    )


def main() -> None:
    PUBLIC.mkdir(parents=True, exist_ok=True)
    DOCS.mkdir(parents=True, exist_ok=True)

    written = []

    build_logo(inverse=False).save(PUBLIC / "bolo-logo.png")
    written.append(PUBLIC / "bolo-logo.png")
    build_logo(inverse=True).save(PUBLIC / "bolo-logo-inverse.png")
    written.append(PUBLIC / "bolo-logo-inverse.png")
    draw_mark(500).save(PUBLIC / "bolo-mark.png")
    written.append(PUBLIC / "bolo-mark.png")

    build_imprint(dark=False).save(PUBLIC / "brand-imprint-light.png")
    written.append(PUBLIC / "brand-imprint-light.png")
    build_imprint(dark=True).save(PUBLIC / "brand-imprint-dark.png")
    written.append(PUBLIC / "brand-imprint-dark.png")

    fav = build_favicon()
    fav.save(DOCS / "favicon.ico", format="ICO", sizes=[(16, 16), (32, 32), (48, 48), (256, 256)])
    written.append(DOCS / "favicon.ico")

    (DOCS / "logo" / "dark.svg").write_text(build_logo_svg(dark=False))
    written.append(DOCS / "logo" / "dark.svg")
    (DOCS / "logo" / "light.svg").write_text(build_logo_svg(dark=True))
    written.append(DOCS / "logo" / "light.svg")

    for path in written:
        print(f"wrote {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
