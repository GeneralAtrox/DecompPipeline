"""Controller pictures for a texture pack's pad variants (pvr/pvr_pack.c, base/pad_style.c): draws a simple Xbox-
style and PlayStation-style pad, seen from above and from the front edge, into the boxes of a game's controller
texture and adds them to a pack as `KEY FILE xbox` / `KEY FILE playstation` lines. The art is drawn here from shapes
(no manufacturer art, nothing taken from the game), so these images may be committed; the pack's other images may not.

    python pad_icons.py PACK_DIR KEY --size N --top X,Y,W,H [--front X,Y,W,H] [--scale S] [--flip-v]

KEY: the texture's pack key (a run with --texture-keys DIR lists them and writes each texture as KEY.png). --size: the
texture's width and height in texels; --top and --front: the boxes of the picture seen from above and from the front
edge in the texture, in texels as the PNG shows them. --scale S draws S times as many pixels (the pack takes any
size). --flip-v: the texture is stored upside down (a game that draws it with its V flipped: --texture-keys shows
mirrored letters), so the pad is drawn upside down in the box. The rest of the texture is left empty: a variant
replaces the whole texture, so this is for a texture that holds nothing else (compose() takes a base image for one
that does).

Nearest existing: tools/texture_pack.py (the pack writer, PNG encoder and key; this only draws pictures for it).
"""
import argparse
import math
import pathlib
import struct
import sys
import zlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import texture_pack as tp  # noqa: E402

OUTLINE = (24, 24, 28)
BODY = {"xbox": (236, 236, 238), "playstation": (250, 250, 252)}
DARK = (70, 72, 80)
LIGHT = (196, 200, 208)


class Canvas:
    """RGBA floats, w x h, transparent; shapes are signed-distance functions in pixels (negative inside)."""

    def __init__(self, w, h):
        self.w, self.h = w, h
        self.px = [[0.0, 0.0, 0.0, 0.0] for _ in range(w * h)]

    def fill(self, sd, box, color):
        x0, y0, x1, y1 = box
        for y in range(max(0, int(y0)), min(self.h, int(y1) + 1)):
            for x in range(max(0, int(x0)), min(self.w, int(x1) + 1)):
                a = min(1.0, max(0.0, 0.5 - sd(x + 0.5, y + 0.5)))
                if a <= 0.0:
                    continue
                d = self.px[y * self.w + x]
                out = a + d[3] * (1.0 - a)
                for c in range(3):
                    d[c] = (color[c] * a + d[c] * d[3] * (1.0 - a)) / out
                d[3] = out

    def rgba(self):
        out = bytearray()
        for r, g, b, a in self.px:
            out += bytes((round(r), round(g), round(b), round(a * 255)))
        return bytes(out)


def circle(cx, cy, r):
    return (lambda x, y: math.hypot(x - cx, y - cy) - r), (cx - r - 2, cy - r - 2, cx + r + 2, cy + r + 2)


def rrect(cx, cy, hw, hh, r):
    def sd(x, y):
        qx, qy = abs(x - cx) - (hw - r), abs(y - cy) - (hh - r)
        return math.hypot(max(qx, 0.0), max(qy, 0.0)) + min(max(qx, qy), 0.0) - r
    return sd, (cx - hw - 2, cy - hh - 2, cx + hw + 2, cy + hh + 2)


def capsule(ax, ay, bx, by, r):
    def sd(x, y):
        dx, dy = bx - ax, by - ay
        t = max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / (dx * dx + dy * dy)))
        return math.hypot(x - ax - t * dx, y - ay - t * dy) - r
    return sd, (min(ax, bx) - r - 2, min(ay, by) - r - 2, max(ax, bx) + r + 2, max(ay, by) + r + 2)


def union(*shapes):
    sds = [s[0] for s in shapes]
    box = (min(s[1][0] for s in shapes), min(s[1][1] for s in shapes), max(s[1][2] for s in shapes),
           max(s[1][3] for s in shapes))
    return (lambda x, y: min(f(x, y) for f in sds)), box


def grown(shape, by):
    return (lambda x, y: shape[0](x, y) - by), tuple(v + (by if i > 1 else -by) for i, v in enumerate(shape[1]))


def ring(shape, width):
    box = shape[1]
    return (lambda x, y: abs(shape[0](x, y)) - width / 2.0), (box[0] - width, box[1] - width, box[2] + width, box[3] + width)


def segment(ax, ay, bx, by, width):
    return capsule(ax, ay, bx, by, width / 2.0)


def triangle(cx, cy, r, width):
    pts = [(cx + r * math.sin(a), cy - r * math.cos(a)) for a in (0, 2 * math.pi / 3, 4 * math.pi / 3)]
    return union(*[segment(*pts[i], *pts[(i + 1) % 3], width) for i in range(3)])


def draw(c, shape, color):
    c.fill(shape[0], shape[1], color)


def top_view(style, w, h):
    """A pad seen from above in a w x h box (the origin top left), as it would show on screen."""
    c = Canvas(w, h)
    s = min(w, h * 1.1)
    u = lambda v: v * w          # noqa: E731
    v = lambda t: t * h          # noqa: E731
    line = max(1.4, s * 0.03)
    body = union(rrect(u(0.5), v(0.40), u(0.40), v(0.24), s * 0.18),
                 capsule(u(0.27), v(0.52), u(0.17), v(0.80), s * 0.13),
                 capsule(u(0.73), v(0.52), u(0.83), v(0.80), s * 0.13))
    draw(c, grown(body, line), OUTLINE)
    draw(c, body, BODY[style])
    stick = lambda cx, cy: (draw(c, circle(u(cx), v(cy), s * 0.085), OUTLINE),  # noqa: E731
                            draw(c, circle(u(cx), v(cy), s * 0.065), DARK),
                            draw(c, circle(u(cx), v(cy), s * 0.035), LIGHT))
    dpad = lambda cx, cy: (draw(c, rrect(u(cx), v(cy), s * 0.07, s * 0.022, s * 0.01), DARK),  # noqa: E731
                           draw(c, rrect(u(cx), v(cy), s * 0.022, s * 0.07, s * 0.01), DARK))
    cx, cy, d, r = 0.72, 0.38, 0.10, s * 0.05
    if style == "xbox":
        stick(0.28, 0.34)
        dpad(0.40, 0.62)
        stick(0.60, 0.62)
        for dx, dy, col in ((0, -d, (244, 196, 36)), (d, 0, (224, 56, 48)), (0, d, (56, 168, 72)),
                            (-d, 0, (52, 112, 224))):
            draw(c, circle(u(cx + dx * h / w), v(cy + dy), r), OUTLINE)
            draw(c, circle(u(cx + dx * h / w), v(cy + dy), r - line * 0.7), col)
        draw(c, circle(u(0.5), v(0.22), s * 0.04), DARK)
        draw(c, circle(u(0.45), v(0.36), s * 0.016), DARK)
        draw(c, circle(u(0.55), v(0.36), s * 0.016), DARK)
    else:
        dpad(0.28, 0.34)
        stick(0.38, 0.64)
        stick(0.62, 0.64)
        draw(c, rrect(u(0.46), v(0.20), u(0.12), v(0.045), s * 0.025), LIGHT)
        sw = max(1.6, s * 0.03)
        x, y = lambda dx: u(cx + dx * h / w), lambda dy: v(cy + dy)  # noqa: E731
        draw(c, triangle(x(0), y(-d), r * 1.05, sw), (58, 168, 118))
        draw(c, ring(circle(x(d), y(0), r * 0.9), sw), (214, 64, 59))
        for sgn in (1, -1):
            draw(c, segment(x(sgn * 0) - r * 0.8, y(d) - sgn * r * 0.8, x(0) + r * 0.8, y(d) + sgn * r * 0.8, sw),
                 (74, 120, 216))
        draw(c, ring(rrect(x(-d), y(0), r * 0.8, r * 0.8, 0.5), sw), (216, 106, 168))
    return c


def front_view(style, w, h):
    """The pad's front edge (its triggers and bumpers) in a w x h box."""
    c = Canvas(w, h)
    u = lambda t: t * w          # noqa: E731
    v = lambda t: t * h          # noqa: E731
    line = max(1.4, h * 0.04)
    body = rrect(u(0.5), v(0.66), u(0.46), v(0.26), h * 0.16)
    draw(c, grown(body, line), OUTLINE)
    draw(c, body, BODY[style])
    for cx in (0.17, 0.83):
        trig = rrect(u(cx), v(0.30), u(0.12), v(0.17), h * 0.07)
        draw(c, grown(trig, line), OUTLINE)
        draw(c, trig, DARK)
    draw(c, rrect(u(0.5), v(0.66), u(0.12), v(0.08), h * 0.04), LIGHT if style == "xbox" else (86, 140, 232))
    return c


def compose(size, placements, scale=1, flip_v=False, base=None):
    """An RGBA image of size x size texels (x scale): each placement (kind, style, x, y, w, h) drawn in its box."""
    n = size * scale
    out = bytearray(base if base else bytes(n * n * 4))
    for kind, style, x, y, w, h in placements:
        pw, ph = w * scale, h * scale
        c = (top_view if kind == "top" else front_view)(style, pw, ph)
        rows = c.rgba()
        for j in range(ph):
            src = (ph - 1 - j if flip_v else j) * pw * 4
            dst = ((y * scale + j) * n + x * scale) * 4
            for i in range(pw):
                if rows[src + i * 4 + 3]:                      # drawn pixels replace; empty ones leave the base
                    out[dst + i * 4:dst + i * 4 + 4] = rows[src + i * 4:src + i * 4 + 4]
    return bytes(out), n


def box(text):
    x, y, w, h = (int(v) for v in text.split(","))
    return x, y, w, h


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("pack")
    ap.add_argument("key")
    ap.add_argument("--size", type=int, required=True)
    ap.add_argument("--top", type=box, required=True)
    ap.add_argument("--front", type=box)
    ap.add_argument("--scale", type=int, default=2)
    ap.add_argument("--flip-v", action="store_true")
    a = ap.parse_args(argv)
    key = int(a.key, 16)
    pack = tp.Pack(a.pack, "")
    index = pathlib.Path(a.pack) / "index.txt"
    if index.is_file():
        for (k, v), name in tp.read_index(a.pack).items():
            pack.rows[(k, v)] = (name, "")
    for style in ("xbox", "playstation"):
        boxes = [("top", style) + a.top] + ([("front", style) + a.front] if a.front else [])
        rgba, n = compose(a.size, boxes, a.scale, a.flip_v)
        pack.rows.pop((key, style), None)
        pack.add(key, rgba, n, n, variant=style, note=f"{style} pad pictures (tools/pad_icons.py)")
    print(f"{pack.close()} images in {a.pack}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
