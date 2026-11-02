"""Writes port/sdk/sy_font_table.h: the 12x24 glyphs sdk/sy_font.c hands a game in place of the Dreamcast BIOS font.

py -3.12 port/tools/gen_sy_font.py [FONT.ttf]

The console's font lives in its boot ROM, which the port does not have and must not need, so the glyphs are drawn from
DejaVu Sans Mono (free to embed, Bitstream Vera licence). 1 bit per pixel, MSB first, rows run on without padding: the
packing a game's glyph plotter reads from the BIOS. The table is committed, so a build does not need the font or Pillow.

Nearest existing: port/tools/texture_pack.py - packs a game's replaced textures for the host; it draws no glyphs.
"""
import pathlib
import sys

from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT, BASELINE, PX = 12, 24, 19, 20
OUT = pathlib.Path(__file__).resolve().parent.parent / "sdk" / "sy_font_table.h"


def glyph(font, ch):
    """One glyph as (WIDTH * HEIGHT + 7) // 8 bytes."""
    im = Image.new("1", (WIDTH, HEIGHT), 0)
    d = ImageDraw.Draw(im)
    d.fontmode = "1"
    d.text((0, BASELINE), ch, font=font, fill=1, anchor="ls")
    bits = [1 if im.getpixel((x, y)) else 0 for y in range(HEIGHT) for x in range(WIDTH)]
    return bytes(sum(b << (7 - i) for i, b in enumerate(bits[n:n + 8])) for n in range(0, len(bits), 8))


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "C:/Windows/Fonts/DejaVuSansMono.ttf"
    font = ImageFont.truetype(path, PX)
    lines = ["/* Written by port/tools/gen_sy_font.py from DejaVu Sans Mono: ASCII 0x20..0x7f, 12x24, 36 bytes each. */"]
    for code in range(0x20, 0x80):
        data = glyph(font, chr(code)) if code < 0x7f else bytes(36)
        lines.append("/* %02x */ " % code + ",".join("0x%02x" % b for b in data) + ",")
    OUT.write_text("\n".join(lines) + "\n", encoding="ascii", newline="\n")


if __name__ == "__main__":
    main()
