"""Check the decoder against Ghidra's disassembly of the same executable.

Two sources (read only):
  - instruction listings: lines `ADDR<tab or spaces>mnemonic operands` (DP packets' asm.txt and the research
    folder's *-instructions-*.txt dumps); every listed address is decoded and its mnemonic compared after mapping
    Ghidra's simplified mnemonics (li, mr, slwi, beq, blr, mtlr, ...) back to the base instruction;
  - the floating-point inventory CSV (per function: how many of each FP / paired-single mnemonic Ghidra found);
    each function's body is decoded and counted the same way.

usage: python tools/check_decoder.py --dol main.dol --listings <dir or file>... [--inventory csv --bodies txt]
"""
import argparse
import collections
import csv
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from ppctranslate.decode import disasm  # noqa: E402
from ppctranslate.image import Dol  # noqa: E402

SIMPLE = {
    "li": "addi", "lis": "addis", "subi": "addi", "subis": "addis", "subic": "addic", "subic.": "addic.",
    "la": "addi", "mr": "or", "mr.": "or.", "nop": "ori", "not": "nor", "not.": "nor.",
    "sub": "subf", "sub.": "subf.", "subo": "subfo", "subo.": "subfo.", "subc": "subfc", "subc.": "subfc.",
    "rotlwi": "rlwinm", "slwi": "rlwinm", "srwi": "rlwinm", "clrlwi": "rlwinm", "clrrwi": "rlwinm",
    "extlwi": "rlwinm", "extrwi": "rlwinm", "clrlslwi": "rlwinm", "rotrwi": "rlwinm", "rotlw": "rlwnm",
    "inslwi": "rlwimi", "insrwi": "rlwimi",
    "cmpwi": "cmpi", "cmpw": "cmp", "cmplwi": "cmpli", "cmplw": "cmpl",
    "crclr": "crxor", "crset": "creqv", "crmove": "cror", "crnot": "crnor", "mtcr": "mtcrf",
    "mftbu": "mftb", "trap": "tw",
}
SPRMOVE = re.compile(r"^(mt|mf)(lr|ctr|xer|dec|srr0|srr1|sprg\d|dsisr|dar|gqr\d|hid\d|l2cr|wpar|dmau|dmal)$")


def canon_ghidra(m):
    m = m.lower().rstrip("+-")
    for suf in (".", ""):
        base = m[:-1] if suf and m.endswith(".") else m
        if suf and not m.endswith("."):
            continue
        if base in SIMPLE:
            r = SIMPLE[base]
            return r if r.endswith(".") or not suf else r + "."
        if base.endswith(".") is False and SPRMOVE.match(base):
            return "mtspr" if base.startswith("mt") else "mfspr"
    if m in ("b", "bl", "ba", "bla"):
        return "b"
    if re.fullmatch(r"tw[a-z]*i", m):
        return "twi"
    if re.fullmatch(r"tw[a-z]*", m):
        return "tw"
    if m.startswith("b") and not m.startswith(("bl.",)):
        stem = m[:-1] if m.endswith("l") and not m.endswith(("lr",)) and m not in ("bl",) else m
        if m.endswith(("lr", "lrl")):
            return "bclr"
        if m.endswith(("ctr", "ctrl")):
            return "bcctr"
        if stem not in ("b",):
            return "bc"
    return m


def canon_mine(i):
    o = i.op
    if o in ("b",):
        return "b"
    suffix = ("o" if i.f.get("oe") else "") + ("." if i.f.get("rc") and not o.endswith(".") else "")
    return o + suffix


def listings(paths):
    pat = re.compile(r"^\s*([0-9a-f]{8})\s+([a-z_][a-z0-9_.+-]*)\b(.*)$", re.I)
    for p in paths:
        p = pathlib.Path(p)
        files = [p] if p.is_file() else sorted(list(p.rglob("asm.txt")) + list(p.glob("*instructions*.txt")))
        for f in files:
            for ln in f.read_text(encoding="utf-8", errors="replace").splitlines():
                m = pat.match(ln)
                if m:
                    mn, ops = m.group(2), m.group(3).strip()
                    if ops.startswith(". ") or ops == ".":
                        mn, ops = mn + ".", ops[1:].strip()    # Ghidra prints record forms as `rlwinm . r0,...`
                    yield f, int(m.group(1), 16), mn, ops


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dol", required=True)
    ap.add_argument("--listings", nargs="*", default=[])
    ap.add_argument("--inventory")
    ap.add_argument("--bodies")
    args = ap.parse_args()
    dol = Dol(pathlib.Path(args.dol).read_bytes())
    seen, bad = set(), collections.Counter()
    examples = {}
    for f, a, m, ops in listings(args.listings):
        if a in seen or not dol.has(a, 4):
            continue
        seen.add(a)
        mine, theirs = canon_mine(dol.insn(a)), canon_ghidra(m)
        if mine != theirs:
            bad[(theirs, mine)] += 1
            examples.setdefault((theirs, mine), f"{a:08x} {m} {ops} | {disasm(dol.insn(a))}")
    print(f"listings: {len(seen)} distinct addresses, {sum(bad.values())} mismatches")
    for k, n in bad.most_common(40):
        print(f"  {n:6} ghidra={k[0]} mine={k[1]}  e.g. {examples[k]}")
    if args.inventory and args.bodies:
        bodies = collections.defaultdict(list)
        for ln in pathlib.Path(args.bodies).read_text().splitlines():
            t = ln.split()
            if len(t) == 3:
                bodies[int(t[0], 16)].append((int(t[1], 16), int(t[2], 16)))
        want = collections.defaultdict(collections.Counter)
        for row in csv.reader(ln for ln in open(args.inventory, encoding="utf-8") if not ln.startswith("#")):
            if row[0] == "category":
                continue
            want[int(row[6], 16)][canon_ghidra(row[1])] += int(row[10])
        tracked = {m for c in want.values() for m in c}
        nbad, total = 0, 0
        for fn, w in sorted(want.items()):
            got = collections.Counter()
            if fn not in bodies:
                continue
            for lo, hi in bodies.get(fn, []):
                for a in range(lo, hi, 4):
                    i = dol.insn(a)
                    c = canon_mine(i)
                    if c in ("mfspr", "mtspr") and not 912 <= i.f["spr"] <= 919:
                        continue
                    if c.rstrip(".") in tracked or c in tracked:
                        got[c.rstrip(".") if c.rstrip(".") in tracked else c] += 1
            total += 1
            if +got != +w:
                nbad += 1
                if nbad <= 10:
                    print(f"  inventory {fn:08x}: ghidra {dict(w)} mine {dict(got)}")
        print(f"inventory: {total} functions, {nbad} with different FP/paired-single counts")


if __name__ == "__main__":
    main()
