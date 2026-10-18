"""Frame comparison for the regression run and by hand: per-pixel error, a structural score, and for a mismatch a
side-by-side (golden | frame | difference) image.

    python port/tools/frames.py GOLDEN.png FRAME.png [--out SHEET.png]

compare() gives:
- identical: the pixels are the same;
- mean: the mean absolute difference per channel (0..255);
- changed: the percentage of pixels with a channel more than CHANGED_LEVEL apart;
- ssim: the structural similarity of the grey images over 8x8 blocks (1.0 is the same).
A frame passes when it is identical, or when changed <= MAX_CHANGED and ssim >= MIN_SSIM (another GPU or driver
may round a few pixels differently; a moved or missing object fails).
"""
import argparse
import sys

import numpy as np
from PIL import Image

CHANGED_LEVEL = 8
MAX_CHANGED = 0.1   # percent of pixels
MIN_SSIM = 0.99
BLOCK = 8


def load(path):
    return np.asarray(Image.open(path).convert("RGB"), dtype=np.int16)


def ssim(a, b):
    """Mean SSIM of the grey images over non-overlapping BLOCK x BLOCK windows."""
    def grey(x):
        g = x[..., 0] * 0.299 + x[..., 1] * 0.587 + x[..., 2] * 0.114
        h, w = (g.shape[0] // BLOCK) * BLOCK, (g.shape[1] // BLOCK) * BLOCK
        return g[:h, :w].reshape(h // BLOCK, BLOCK, w // BLOCK, BLOCK).swapaxes(1, 2)
    x, y = grey(a.astype(np.float64)), grey(b.astype(np.float64))
    mx, my = x.mean(axis=(2, 3)), y.mean(axis=(2, 3))
    vx, vy = x.var(axis=(2, 3)), y.var(axis=(2, 3))
    cov = ((x - mx[..., None, None]) * (y - my[..., None, None])).mean(axis=(2, 3))
    c1, c2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    s = ((2 * mx * my + c1) * (2 * cov + c2)) / ((mx * mx + my * my + c1) * (vx + vy + c2))
    return float(s.mean())


def compare(golden, frame):
    """The scores for two images of the same size (a size mismatch fails with ssim 0)."""
    a, b = load(golden), load(frame)
    if a.shape != b.shape:
        return {"identical": False, "mean": 255.0, "changed": 100.0, "ssim": 0.0, "pass": False,
                "note": f"size {a.shape[1]}x{a.shape[0]} vs {b.shape[1]}x{b.shape[0]}"}
    d = np.abs(a - b)
    same = not d.any()
    r = {"identical": same, "mean": float(d.mean()),
         "changed": float((d.max(axis=2) > CHANGED_LEVEL).mean() * 100.0), "ssim": 1.0 if same else ssim(a, b)}
    r["pass"] = same or (r["changed"] <= MAX_CHANGED and r["ssim"] >= MIN_SSIM)
    return r


def sheet(golden, frame, out):
    """golden | frame | the difference, amplified 4 times, side by side."""
    a, b = load(golden), load(frame)
    h, w = max(a.shape[0], b.shape[0]), max(a.shape[1], b.shape[1])
    img = Image.new("RGB", (w * 3 + 16, h), (40, 40, 40))
    img.paste(Image.fromarray(a.astype(np.uint8)), (0, 0))
    img.paste(Image.fromarray(b.astype(np.uint8)), (w + 8, 0))
    if a.shape == b.shape:
        img.paste(Image.fromarray(np.clip(np.abs(a - b) * 4, 0, 255).astype(np.uint8)), (w * 2 + 16, 0))
    img.save(out)


def describe(r):
    if r["identical"]:
        return "identical"
    return (f"mean {r['mean']:.2f}, {r['changed']:.3f}% changed, ssim {r['ssim']:.4f}"
            + (f" ({r['note']})" if "note" in r else ""))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("golden")
    ap.add_argument("frame")
    ap.add_argument("--out", help="write the side-by-side sheet here")
    a = ap.parse_args()
    r = compare(a.golden, a.frame)
    print(("PASS " if r["pass"] else "FAIL ") + describe(r))
    if a.out:
        sheet(a.golden, a.frame, a.out)
    return 0 if r["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
