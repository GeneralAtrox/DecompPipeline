"""Pick the first Ghidra proof set of an Xbox 360 title: 50 phase-1 functions in seven groups (PROOF_PLAN.md), from
catalog_inputs.py's output, without Ghidra. The same inputs give the same list.

Every pick has exact bounds from .pdata, no indirect call, no jump table and no tail call, and is 8..400 bytes. Bodies
of the same shape count once (the same words with branch offsets masked: a title has hundreds of `li r3,0; blr` and of
one-call wrappers), and the picks are spread evenly over the group's size order (then address), so each group runs
from its smallest functions to 400-byte ones.

usage: python proof_pick.py <catalog_dir> [out.json]   (catalog_dir: catalog_inputs.py's out_dir)
"""
import hashlib
import json
import pathlib
import sys

GROUPS = [  # (name, count, test of a function row)
    ("integer leaf", 8, lambda f, c: not f["callees"] and not f["imports"] and not f["frame_helpers"]
     and not c.keys() - {"trap"}),
    ("frame helpers", 4, lambda f, c: not f["callees"] and not f["imports"] and f["frame_helpers"]
     and not c.keys() - {"trap"}),
    ("64-bit leaf", 5, lambda f, c: not f["callees"] and not f["imports"] and "ld64" in c and "fpu" not in c),
    ("r13 user", 4, lambda f, c: "r13" in c and "fpu" not in c and not f["imports"]),
    ("caller", 14, lambda f, c: f["callees"] and not f["imports"] and "fpu" not in c),
    ("float, no fma", 6, lambda f, c: "fpu" in c and "fma" not in c and not f["imports"]),
    ("fma", 5, lambda f, c: "fma" in c and not f["imports"]),
    ("kernel import", 4, lambda f, c: f["imports"] and "fpu" not in c),
]
MIN_SIZE, MAX_SIZE = 8, 400


def shape(body):
    """A digest of the body with the offsets of b/bl (opcode 18) and bc (16) masked."""
    h = hashlib.sha1()
    for o in range(0, len(body) - 3, 4):
        w = int.from_bytes(body[o:o + 4], "big")
        op = w >> 26
        h.update((w & 0xFC000003 if op == 18 else w & 0xFFFF0003 if op == 16 else w).to_bytes(4, "big"))
    return h.digest()


def eligible(f):
    return (f["source"] == "pdata" and not f["refuse"] and f["kind"] == "function" and not f["tail_calls"]
            and not f["indirect_calls"] and not f["jump_tables"] and MIN_SIZE <= f["size"] <= MAX_SIZE)


def pick(cat, image, base):
    taken, seen, out = set(), set(), []
    for name, count, test in GROUPS:
        pool = []
        for f in cat["functions"]:
            if f["addr"] in taken or not eligible(f) or not test(f, f["counts"]):
                continue
            off = int(f["addr"], 16) - base
            body = shape(image[off:off + f["size"]])
            if body in seen:
                continue
            seen.add(body)
            pool.append(f)
        pool.sort(key=lambda f: (f["size"], f["addr"]))
        if len(pool) < count:
            raise SystemExit(f"proof_pick: group {name!r} has {len(pool)} functions, {count} wanted")
        for k in range(count):
            f = pool[(2 * k + 1) * len(pool) // (2 * count)]
            taken.add(f["addr"])
            out.append({"addr": f["addr"], "group": name, "size": f["size"], "insns": f["insns"],
                        "counts": f["counts"], "callees": len(f["callees"]), "imports": len(f["imports"]),
                        "frame_helpers": f["frame_helpers"]})
    return out


def main(argv):
    if len(argv) < 2:
        print(__doc__.strip().splitlines()[-1].strip())
        return 2
    d = pathlib.Path(argv[1])
    cat = json.loads((d / "catalog_inputs.json").read_text(encoding="utf-8"))
    desc = json.loads((d / "image.json").read_text(encoding="utf-8"))
    picks = pick(cat, (d / "image.bin").read_bytes(), desc["load_address"])
    text = json.dumps({"image_sha1": desc["image_sha1"], "functions": picks}, indent=1)
    if len(argv) > 2:
        pathlib.Path(argv[2]).write_text(text + "\n", encoding="utf-8")
    for p in picks:
        print(f"{p['addr']}  {p['group']:14} {p['size']:4} bytes  {p['insns']:3} insns  callees {p['callees']}  "
              f"imports {p['imports']}  helpers {p['frame_helpers']}  {p['counts']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
