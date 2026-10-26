"""Compare xenontranslate.decode against a reference disassembler over a whole executable's code.

The reference (XenonRecomp's disassembler build, `dis.exe IMAGE START_HEX COUNT`) is used only as an oracle here: its
text output is read and compared, none of its code is used. For every word inside the code ranges it records which of
our ops each reference instruction id maps to, and whether every operand value the reference prints is one of the
values our decoder found. A consistent decoder maps each reference id to one op (aliases such as li/addi share one);
the minority entries are the disagreements, listed with examples.

    python tools/check_decoder.py [--workspace DIR] [--dis PATH] [--out FILE]
"""
from __future__ import annotations

import argparse
import bisect
import collections
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from xenontranslate.decode import decode, disasm  # noqa: E402

CHUNK = 400_000          # words per reference run (each run is bounded by a timeout)


def load_ranges(ws):
    rs = []
    for line in open(os.path.join(ws, "code_ranges.txt")):
        a, b = line.split()
        rs.append((int(a, 16), int(b, 16)))
    rs.sort()
    return rs


def in_ranges(starts, rs, a):
    k = bisect.bisect_right(starts, a) - 1
    return k >= 0 and rs[k][0] <= a < rs[k][1]


def operand_values(insn):
    vals = set()
    for k, v in insn.f.items():
        if k in ("rc", "oe", "aa", "lk"):
            continue
        vals.update((v, v & 0xFFFF, v & 0xFFFFFFFF, -v if v < 0 else v))
        if k == "bi":
            vals.add(v >> 2)                 # the reference prints the CR field of a simplified branch
        if k == "imm" and "mask" in insn.f:
            vals.add((v << 2) | insn.f["mask"])
        if k == "d" and insn.op in ("ld", "ldu", "lwa", "std", "stdu"):
            vals.add(v & 0xFFFC)
    if insn.op in ("vupkhsb128", "vupklsb128"):
        vals.add(96)                         # the reference prints the fixed vA field (0x60) as an operand
    return vals


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--dis", required=True)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    ws = args.workspace
    img = open(os.path.join(ws, "image.bin"), "rb").read()
    base = 0x82000000
    rs = load_ranges(ws)
    starts = [a for a, _ in rs]
    lo, hi = rs[0][0], rs[-1][1]

    by_id = collections.defaultdict(collections.Counter)
    op_mn = collections.defaultdict(collections.Counter)
    examples = {}
    opnd_bad = collections.Counter()
    opnd_ex = {}
    ours_illegal = collections.Counter()
    ref_illegal = 0
    total = 0
    a = lo
    while a < hi:
        n = min(CHUNK, (hi - a) // 4)
        p = subprocess.run([args.dis, os.path.join(ws, "image.bin"), f"{a:08X}", str(n)], capture_output=True,
                           text=True, timeout=300)
        for line in p.stdout.splitlines():
            parts = line.split(None, 3)
            if len(parts) < 3:
                continue
            try:
                addr = int(parts[0], 16)
            except ValueError:
                continue
            if not in_ranges(starts, rs, addr):
                continue
            w = int.from_bytes(img[addr - base:addr - base + 4], "big")
            ins = decode(w, addr)
            total += 1
            mn = parts[2]
            if mn == "??":
                ref_illegal += 1
                if ins.op != "illegal":
                    by_id["??"][ins.op] += 1
                    examples.setdefault(("??", ins.op), (addr, w, line, disasm(ins)))
                continue
            rest = parts[3] if len(parts) > 3 else ""
            rid = rest[rest.find("[id="):].split()[0] if "[id=" in rest else "id?"
            key = rid
            by_id[key][ins.op] += 1
            op_mn[ins.op][mn] += 1
            examples.setdefault((key, ins.op), (addr, w, line.strip(), disasm(ins)))
            if ins.op == "illegal":
                ours_illegal[mn] += 1
                continue
            if "ops=" in rest:
                ops = [int(x, 16) for x in rest[rest.find("ops=") + 4:].rstrip("] ,").split(",") if x]
                have = operand_values(ins)
                miss = [v for v in ops if v and v not in have]
                if miss:
                    opnd_bad[(mn, ins.op)] += 1
                    opnd_ex.setdefault((mn, ins.op), (addr, w, line.strip(), disasm(ins), miss))
        a += n * 4

    out = []
    out.append(f"words compared: {total}   reference '??': {ref_illegal}")
    out.append(f"ours illegal where reference decodes: {sum(ours_illegal.values())}  {dict(ours_illegal.most_common(20))}")
    out.append("")
    out.append("reference ids mapping to more than one of our ops (minority = suspect):")
    for rid, c in sorted(by_id.items(), key=lambda kv: -sum(kv[1].values())):
        if len(c) > 1:
            out.append(f"  {rid}: {dict(c.most_common())}")
            for op, _ in c.most_common()[1:4]:
                e = examples[(rid, op)]
                out.append(f"      {op}: {e[0]:08X} {e[1]:08X} ref: {e[2]}   ours: {e[3]}")
    ops_to_ids = collections.defaultdict(set)
    for rid, c in by_id.items():
        for op in c:
            ops_to_ids[op].add(rid)
    out.append("")
    out.append("our ops shown under more than one reference mnemonic (aliases; review):")
    for op, c in sorted(op_mn.items()):
        if len(c) > 1:
            out.append(f"  {op}: {dict(c.most_common())}")
    out.append("")
    out.append("operand disagreements (reference operand value not among ours):")
    for k, n in opnd_bad.most_common():
        e = opnd_ex[k]
        out.append(f"  {k[0]} / {k[1]}: {n}   e.g. {e[0]:08X} {e[1]:08X} ref: {e[2]}   ours: {e[3]}  missing {e[4]}")
    text = "\n".join(out)
    if args.out:
        open(args.out, "w").write(text + "\n")
    print(text)


if __name__ == "__main__":
    main()
