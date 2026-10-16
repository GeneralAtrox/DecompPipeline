"""Completeness check: account for every byte of a project's game ranges.

  python coverage.py WORKSPACE [--list N] [--ranges CODE_RANGES]   (default: WORKSPACE/state/code_ranges.txt)

Each byte is one of:
  function     inside a function body (state/code_ranges.txt, from the catalog)
  pool         read by a PC-relative load (mov.w/mov.l @(disp,PC)) or a `mova` table of a function
  padding      0x0009 (nop) or zero bytes before a 32-byte-aligned function start, or zero runs
  data         referenced by an address constant somewhere in the image (a table, a string, a global)
  unexplained  none of these: possibly code that function discovery missed

Prints the totals and the unexplained runs, each with a code score: whether it holds a typical prologue
(`sts.l pr,@-r15`, `mov.l r14,@-r15`) and an `rts`. Extra functions (project.json "extra_functions", core/scope.py:
library code outside the game ranges that the project decompiles) get a line of their own: their functions and
code bytes (catalog sizes), never mixed into the game-range bytes.
"""
import bisect
import json
import pathlib
import struct
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))  # the pipeline root, for core/scope.py
from core import scope  # noqa: E402  (pure: no core.paths)

LOAD = 0x8C010000


def load(ws, code_ranges=None):
    cfg = json.loads((ws / "project.json").read_text(encoding="utf-8"))
    img = (ws / cfg["binary"]).resolve().read_bytes()
    ranges = scope.game_ranges(cfg)  # the game's code ranges (the extras: extras_line)
    funcs = []
    for line in pathlib.Path(code_ranges or ws / "state" / "code_ranges.txt").read_text(encoding="utf-8").split("\n"):
        p = line.split()
        if len(p) >= 2:
            funcs.append((int(p[0], 16), int(p[1], 16)))
    return img, ranges, sorted(funcs)


def classify(img, ranges, funcs):
    end_img = LOAD + len(img)

    def u16(a):
        return struct.unpack_from("<H", img, a - LOAD)[0] if LOAD <= a < end_img - 1 else None

    kind = {}
    for lo, hi in funcs:
        for a in range(lo, hi):
            kind[a] = "function"
    # pools: decode PC-relative loads in every function body
    for lo, hi in funcs:
        for pc in range(lo, hi, 2):
            w = u16(pc)
            if w is None:
                continue
            if w >> 12 == 0x9:  # mov.w @(disp,PC),Rn
                t = pc + 4 + (w & 0xFF) * 2
                for a in range(t, t + 2):
                    kind.setdefault(a, "pool")
            elif w >> 12 == 0xD or w >> 8 == 0xC7:  # mov.l @(disp,PC),Rn / mova @(disp,PC),R0
                t = (pc & ~3) + 4 + (w & 0xFF) * 4
                for a in range(t, t + 4):
                    kind.setdefault(a, "pool")
    # data: any aligned 32-bit value in the image pointing into the game ranges
    refs = set()
    for off in range(0, len(img) - 3, 4):
        v = struct.unpack_from("<I", img, off)[0]
        if 0x0C000000 <= v < 0x0D000000:
            v |= 0x80000000
        if any(lo <= v < hi for lo, hi in ranges):
            refs.add(v)
    starts = [lo for lo, _ in funcs]
    totals = {"function": 0, "pool": 0, "padding": 0, "data": 0, "unexplained": 0}
    runs = []
    for lo, hi in ranges:
        a = lo
        while a < hi:
            k = kind.get(a)
            if k:
                totals[k] += 1
                a += 1
                continue
            b = a  # a hole: [a, b)
            while b < hi and b not in kind:
                b += 1
            hole = img[a - LOAD:b - LOAD]
            nxt = starts[bisect.bisect_left(starts, b)] if bisect.bisect_left(starts, b) < len(starts) else None
            if all(x == 0 for x in hole) or (b - a < 32 and nxt == b and set(
                    struct.unpack_from(f"<{len(hole) // 2}H", hole)) <= {0x0009, 0}):
                totals["padding"] += b - a
            elif any(a <= r < b for r in refs):
                totals["data"] += b - a
            else:
                totals["unexplained"] += b - a
                words = struct.unpack_from(f"<{len(hole) // 2}H", hole) if len(hole) >= 2 else ()
                score = (0x4F22 in words) + (0x2FE6 in words) + (0x000B in words)
                runs.append((a, b, score))
            a = b
    return totals, runs


def extras_line(ws):
    """'extra functions: ...' for a project with extra_functions, else ''."""
    sc = scope.Scope(json.loads((ws / "project.json").read_text(encoding="utf-8")), ws)
    if not sc.extras:
        return ""
    cat = json.loads((ws / "state" / "catalog.json").read_text(encoding="utf-8"))["functions"]
    mine = [f for f in cat if f["addr"] in sc.extras]
    return (f"extra functions (outside the game ranges): {len(mine)} of {len(sc.extras)} listed in the catalog, "
            f"{sum(int(f['bytes']) for f in mine)} code bytes")


def main():
    ws = pathlib.Path(sys.argv[1])
    n = int(sys.argv[sys.argv.index("--list") + 1]) if "--list" in sys.argv else 40
    img, ranges, funcs = load(ws, sys.argv[sys.argv.index("--ranges") + 1] if "--ranges" in sys.argv else None)
    totals, runs = classify(img, ranges, funcs)
    size = sum(hi - lo for lo, hi in ranges)
    for k, v in totals.items():
        print(f"{k:12s} {v:8d} bytes  {100 * v / size:5.1f}%")
    likely = [r for r in runs if r[2] >= 2]
    print(f"unexplained runs: {len(runs)}; with a prologue and an rts (likely missed code): {len(likely)}")
    if extras_line(ws):
        print(extras_line(ws))
    for a, b, s in sorted(runs, key=lambda r: (-r[2], r[0]))[:n]:
        print(f"  {a:08x}-{b:08x} {b - a:6d} bytes  code score {s}")


if __name__ == "__main__":
    main()
