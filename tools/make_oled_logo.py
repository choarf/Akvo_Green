#!/usr/bin/env python3
"""
Builds the OLED boot logo, gateway/oled_logo.png (128x60, 1 bit):

  row 1  the GHOTRIX wordmark, cut from the real logo image (both its navy
         and teal letters are lit - the display has one colour)
  row 2  "Venko → Akvo" in DejaVu Sans Bold - oled_display.py animates this
         last row at boot (writes itself on 3 times, then flashes 3 times)

  python3 tools/make_oled_logo.py                       # defaults below
  python3 tools/make_oled_logo.py --source logo.png --line "Venko → Akvo" --out /tmp/x.png

Needs Pillow and the DejaVu fonts (fonts-dejavu-core); run it on the dev
machine and commit the PNG - the Pi only reads the result.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFont

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = REPO_ROOT.parent / "VenkoDemo" / "docs" / "OLED" / "ghotrix_logo.png"
DEFAULT_OUT = REPO_ROOT / "gateway" / "oled_logo.png"
DEFAULT_FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
W, H = 128, 60          # oled_display.py fits the logo into 128x60


def wordmark(source: Path, width: int) -> Image.Image:
    """The logo's letters as white on black, scaled to `width` px, 1 bit.
    Ink = any pixel clearly darker than the white / pale-cyan page, so the
    navy (GHO) and teal (TRIX) letters both count."""
    rgb = Image.open(source).convert("RGB")
    r, g, b = rgb.split()
    darkest = ImageChops.darker(ImageChops.darker(r, g), b)     # min(r, g, b) per pixel
    ink = darkest.point(lambda v: 255 if v < 150 else 0)
    # the source is a page-header screenshot: skip its dark top border
    top = next(y for y in range(rgb.height) if not any(ink.getpixel((x, y)) for x in range(rgb.width))) + 1
    ink = ink.crop((0, top, rgb.width, rgb.height))
    ink = ink.crop(ink.getbbox())
    small = ink.resize((width, round(ink.height * width / ink.width)), Image.LANCZOS)
    return small.point(lambda v: 255 if v >= 110 else 0)


def text_row(text: str, font_path: str, size: int) -> Image.Image:
    font = ImageFont.truetype(font_path, size)
    left, top, right, bottom = font.getbbox(text)
    img = Image.new("L", (right - left + 2, bottom - top + 2))
    ImageDraw.Draw(img).text((1 - left, 1 - top), text, font=font, fill=255)
    img = img.point(lambda v: 255 if v >= 128 else 0)
    return img.crop(img.getbbox())


def build(source: Path, line: str, font_path: str, size: int, gap: int = 9) -> Image.Image:
    top = wordmark(source, 122)
    bottom = text_row(line, font_path, size)
    if bottom.width > W:
        raise SystemExit(f"'{line}' is {bottom.width} px wide at size {size}; max {W} - use a smaller --size")
    y0 = (H - (top.height + gap + bottom.height)) // 2
    canvas = Image.new("L", (W, H))
    canvas.paste(top, ((W - top.width) // 2, y0))
    canvas.paste(bottom, ((W - bottom.width) // 2, y0 + top.height + gap))
    return canvas.convert("1")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", type=Path, default=DEFAULT_SOURCE, help="logo image (dark letters on a light page)")
    ap.add_argument("--line", default="Venko → Akvo", help="second row, animated at boot")
    ap.add_argument("--font", default=DEFAULT_FONT)
    ap.add_argument("--size", type=int, default=11)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args(argv)

    img = build(args.source, args.line, args.font, args.size)
    img.save(args.out)
    print(f"{args.out} ({img.width}x{img.height})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
