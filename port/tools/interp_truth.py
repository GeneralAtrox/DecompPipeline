"""In-between frames against the real ones (--interp-truth, port/pvr/pvr_blend_opt.c).

A fixed-clock run with --present interp --interp-scene on|only --interp-frames 1 --interp-truth on --dump-every 1
--dump-extra 1 writes, after each odd frame N, frame_N_e1: the scene of frame N-2 blended with N's at one half, which
is where frame N-1 was, and N-1 was rendered and dumped too. Each is compared with frame N-1, and so is what the screen
would show with no in-between frame: N-2 held, or N early. A blend that is right comes out well under both. An odd
frame with no in-between frame dumped was held (the renderer drew none) and scores as holding.

Per frame: the mean difference of a pixel's channels (0-255), and the share of pixels off by more than 32. The worst
frames are written side by side (the real frame, the in-between one, the difference four times over) as PNGs.

usage: interp_truth.py DUMPDIR [--from N] [--to N] [--worst 6] [--out DIR]

Nearest existing: frames.py - compares frames of two runs; this compares one run's in-between frames with its own real
frames.
"""
import argparse
import pathlib
import sys

import numpy as np
from PIL import Image

BAD = 32


def load(path):
    return np.asarray(Image.open(path).convert("RGB"), dtype=np.int16)


def score(a, b):
    """(mean difference, per cent of pixels off by more than BAD)"""
    d = np.abs(a - b)
    return float(d.mean()), float((d.max(axis=2) > BAD).mean() * 100.0)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("dump_dir", type=pathlib.Path)
    ap.add_argument("--from", dest="first", type=int, default=0)
    ap.add_argument("--to", type=int, default=1 << 30)
    ap.add_argument("--worst", type=int, default=6)
    ap.add_argument("--out", type=pathlib.Path, help="where the side-by-side PNGs go (default DUMPDIR/truth)")
    a = ap.parse_args(argv)
    rows, held = [], 0
    have = sorted(int(p.name[6:12]) for p in a.dump_dir.glob("frame_*_e1.png"))
    for n in range(have[0] if have else 1, have[-1] + 1 if have else 0):
        e1 = a.dump_dir / f"frame_{n:06d}_e1.png"
        paths = [a.dump_dir / f"frame_{k:06d}.png" for k in (n - 2, n - 1, n)]
        if n % 2 == 0 or not a.first <= n <= a.to or not all(p.exists() for p in paths):
            continue
        before, truth, after = (load(p) for p in paths)
        blend = load(e1) if e1.exists() else before  # none drawn: the screen held the frame before
        held += not e1.exists()
        rows.append((n - 1, score(blend, truth), score(before, truth), score(after, truth)))
    if not rows:
        print("no odd frame with its in-between frame and the two before it", file=sys.stderr)
        return 2
    print("frame  in-between mean/bad%   held N-2 mean/bad%   N early mean/bad%")
    for f, b, h, x in rows:
        print(f"{f:6d}  {b[0]:7.2f} {b[1]:6.2f}%      {h[0]:7.2f} {h[1]:6.2f}%      {x[0]:7.2f} {x[1]:6.2f}%")
    col = np.array([[b[0], b[1], h[0], h[1], x[0], x[1]] for _, b, h, x in rows])
    for name, q in (("median", 50), ("p95", 95), ("worst", 100)):
        v = np.percentile(col, q, axis=0)
        print(f"{name:6s}  {v[0]:7.2f} {v[1]:6.2f}%      {v[2]:7.2f} {v[3]:6.2f}%      {v[4]:7.2f} {v[5]:6.2f}%")
    worse = sum(1 for _, b, h, x in rows if b[0] > min(h[0], x[0]))
    print(f"frames {len(rows)} ({held} held: no in-between drawn); in-between worse than holding: "
          f"{sum(1 for _, b, h, _ in rows if b[0] > h[0])}, worse than holding or showing early: {worse}")
    out = a.out or a.dump_dir / "truth"
    out.mkdir(parents=True, exist_ok=True)
    for f, b, _, _ in sorted(rows, key=lambda r: -r[1][0])[: a.worst]:
        e1 = a.dump_dir / f"frame_{f + 1:06d}_e1.png"
        truth, blend = load(a.dump_dir / f"frame_{f:06d}.png"), load(e1 if e1.exists() else
                                                                     a.dump_dir / f"frame_{f - 1:06d}.png")
        diff = np.clip(np.abs(blend - truth) * 4, 0, 255)
        Image.fromarray(np.concatenate([truth, blend, diff], axis=1).astype(np.uint8)).save(out / f"truth_{f:06d}.png")
    print(f"side by side (real, in-between, difference x4): {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
