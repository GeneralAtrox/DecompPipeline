"""Whether two Xbox 360 images carry the same statically linked library: fingerprints every function of a library span in the first
image (instructions with their immediates, registers and branch targets masked, so a relinked copy at other addresses still
matches) and looks for each in the second image. The answer decides whether the kit's Direct3D entries written from one game
serve the other: a function found in both is the same code, a span that matches as a block is the same library version.

Nearest existing: boundary.py - it finds the span in one image; this compares a span between images.

  python library_share.py --ws A_WORKSPACE --span 821c64f0 821e55d0 --other B_WORKSPACE [--out share.json]
"""
import argparse
import bisect
import collections
import hashlib
import json
import pathlib

MIN_WORDS = 12     # shorter functions (thunks, getters) collide by shape alone and are counted apart


def load(ws):
    ws = pathlib.Path(ws)
    cat = json.loads((ws / "catalog_inputs.json").read_text())
    img = json.loads((ws / "image.json").read_text())
    data = (ws / "image.bin").read_bytes()
    base = img.get("load_address", 0x82000000)
    funcs = sorted((int(f["addr"], 16), f["size"]) for f in cat["functions"])
    return data, base, funcs


def shape(word):
    """The word with everything but its operation kept out: the primary opcode, and the extended opcode of the groups that have one."""
    op = word >> 26
    if op == 31:
        return (op, (word >> 1) & 0x3FF)
    if op in (59, 63):
        return (op, (word >> 1) & 0x1F if op == 59 else (word >> 1) & 0x3FF)
    if op == 19:
        return (op, (word >> 1) & 0x3FF)
    return (op,)


def fingerprint(data, base, lo, size):
    words = [int.from_bytes(data[a - base:a - base + 4], "big") for a in range(lo, lo + size, 4) if 0 <= a - base and a - base + 4 <= len(data)]
    return hashlib.sha1(repr([shape(w) for w in words]).encode()).hexdigest(), len(words)


def index(ws):
    data, base, funcs = load(ws)
    table = collections.defaultdict(list)
    for lo, size in funcs:
        h, n = fingerprint(data, base, lo, size)
        table[h].append((lo, n))
    return table


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ws", required=True)
    ap.add_argument("--span", nargs=2, required=True)
    ap.add_argument("--other", required=True)
    ap.add_argument("--out")
    a = ap.parse_args()
    lo_s, hi_s = int(a.span[0], 16), int(a.span[1], 16)
    data, base, funcs = load(a.ws)
    other = index(a.other)
    inside = [(lo, size) for lo, size in funcs if lo_s <= lo < hi_s]
    rows = []
    big = [0, 0, 0]       # functions of MIN_WORDS or more: total, found in the other image, found exactly once
    small = [0, 0]
    for lo, size in inside:
        h, n = fingerprint(data, base, lo, size)
        hits = other.get(h, [])
        rows.append({"addr": f"{lo:08x}", "words": n, "other": [f"{x:08x}" for x, _ in hits][:4]})
        if n >= MIN_WORDS:
            big[0] += 1
            big[1] += bool(hits)
            big[2] += len(hits) == 1
        else:
            small[0] += 1
            small[1] += bool(hits)
    found = sorted(int(r["other"][0], 16) for r in rows if r["words"] >= MIN_WORDS and len(r["other"]) == 1)
    out = {"span": a.span, "functions": len(inside), "min_words": MIN_WORDS,
           "long": {"total": big[0], "found": big[1], "found_once": big[2]}, "short": {"total": small[0], "found": small[1]},
           "other_block": [f"{found[0]:08x}", f"{found[-1]:08x}"] if found else None, "rows": rows}
    if found:
        spread = sorted(found)
        # a block shows as most matches in a short address range
        best = 0
        j = 0
        for i in range(len(spread)):
            while spread[i] - spread[j] > 0x30000:
                j += 1
            best = max(best, i - j + 1)
        out["matches_in_one_0x30000_window"] = best
    if a.out:
        pathlib.Path(a.out).write_text(json.dumps(out, indent=1))
    print({k: v for k, v in out.items() if k != "rows"})


if __name__ == "__main__":
    main()
