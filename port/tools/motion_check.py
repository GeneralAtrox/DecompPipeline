#!/usr/bin/env python3
"""Looks across consecutive frames for something that pops: a piece of the picture that appears or goes at once.

A still frame can't show a scrolling backdrop's logo vanishing at x=640, so an aspect proof judged by eye on stills
called such a frame correct. This compares each frame with the next. A pixel is explained if it didn't change, or if it
matches the previous frame moved by the pair's scroll (searched, within --search pixels). What's left unexplained,
counted per tile, is a pop. Run it on frames dumped one apart (--dump-frames) at a size whose scale from the
game's screen is a whole number, so a scroll moves pixels exactly.

    motion_check.py DIR [--tile 24] [--tile-share 0.25] [--search 8] [--side-only]

--side-only looks only outside the centred 4:3 picture (left and right, or above and below on a tall window), for a backdrop behind a menu that animates by itself.
Exit 0: no pair over the limit; 1: a pop; 2: too few frames.

Nearest existing: tools/aspect_check - there is none for motion: docs/aspect/*.md are sheets of still frames judged by
eye, and port/tools comparisons look at one frame against a reference, not one frame against the next.
"""
import argparse
import pathlib
import re
import sys

import numpy as np
from PIL import Image

NOISE = 24  # a channel difference below this is the same pixel (filtering, a fade's last step)


def load(path):
    return np.asarray(Image.open(path).convert("RGB"), dtype=np.int16)


def differs(a, b):
    return (np.abs(a - b).max(axis=2) > NOISE)


def moved(a, dx, dy):
    return np.roll(np.roll(a, dy, axis=0), dx, axis=1)


def best_scroll(a, b, search):
    """The shift that explains the most of the pixels that changed."""
    changed = differs(a, b)
    if not changed.any():
        return 0, 0, changed
    best = (0, 0)
    most = -1
    h, w = changed.shape
    ys, xs = np.nonzero(changed)
    step = max(1, ys.size // 4000)  # enough of the changed pixels to tell the shifts apart
    ys, xs = ys[::step], xs[::step]
    want = b[ys, xs]
    for dy in range(-search, search + 1):
        for dx in range(-search, search + 1):
            if not dx and not dy:
                continue
            n = int((np.abs(a[(ys - dy) % h, (xs - dx) % w] - want).max(axis=1) <= NOISE).sum())
            if n > most:
                most, best = n, (dx, dy)
    return best[0], best[1], changed


def worst_tile(left, tile):
    """The tile with the most unexplained pixels: its share of the tile, and where it is."""
    total = np.zeros((left.shape[0] + 1, left.shape[1] + 1), dtype=np.int64)
    total[1:, 1:] = left.cumsum(axis=0).cumsum(axis=1)
    counts = total[tile:, tile:] - total[:-tile, tile:] - total[tile:, :-tile] + total[:-tile, :-tile]
    y, x = np.unravel_index(int(counts.argmax()), counts.shape)
    return float(counts[y, x]) / (tile * tile), int(x), int(y)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", type=pathlib.Path)
    ap.add_argument("--tile", type=int, default=24)
    ap.add_argument("--tile-share", type=float, default=0.25)
    ap.add_argument("--search", type=int, default=8)
    ap.add_argument("--side-only", action="store_true")
    args = ap.parse_args()
    frames = sorted(p for p in args.folder.glob("frame_*.png") if re.fullmatch(r"frame_\d+", p.stem))
    if len(frames) < 2:
        print(f"motion_check: {len(frames)} frames in {args.folder}")
        return 2
    pops = []
    worst = 0.0
    prev = load(frames[0])
    for path in frames[1:]:
        cur = load(path)
        dx, dy, changed = best_scroll(prev, cur, args.search)
        left = changed & differs(moved(prev, dx, dy), cur)
        h, w = left.shape
        if args.side_only:
            side = (w - h * 4 // 3) // 2
            over = (h - w * 3 // 4) // 2
            if side > 0:
                left[:, side:w - side] = False
            elif over > 0:
                left[over:h - over, :] = False
        share, x, y = worst_tile(left, args.tile)
        worst = max(worst, share)
        if share >= args.tile_share:
            pops.append(f"{path.stem}: scroll {dx},{dy}, {share:.0%} of the tile at {x},{y}")
        prev = cur
    print(f"motion_check: {len(frames)} frames, worst tile {worst:.0%} unexplained, limit {args.tile_share:.0%}, {len(pops)} pop(s)")
    for line in pops[:20]:
        print(" ", line)
    return 1 if pops else 0


if __name__ == "__main__":
    sys.exit(main())
