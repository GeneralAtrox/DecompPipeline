"""Gate G2 of the proof (PROOF_PLAN.md): Ghidra's decoding of the proof functions against the offline scanner.

Reads the TcDecodeAudit output ("addr<TAB>word<TAB>mnemonic" per instruction word) and checks:
- every word is the image's word at that address (Ghidra loaded image.bin at the right base);
- Ghidra decoded every word ("??" is a gap: an instruction Ghidra cannot read, e.g. a VMX128 encoding);
- no word is a vector instruction, by either reading (phase 1 refuses VMX; the proof functions have none);
- Ghidra's mnemonic and ppcscan.classify agree on the classes both can tell: vmx, fpu, fma, trap, atomic.
Prints the disagreements; exit 0 when there are none (the gate passes), 1 otherwise.

usage: python decode_audit.py <audit.tsv> <image.bin> [--base 0x82000000]
"""
import argparse
import re
import struct
import sys

import ppcscan

# Ghidra's mnemonics (lower case, record forms with a trailing "."), by class
GHIDRA = {
    "vmx": r"(v|lv|stv|dst|dss|mfvscr|mtvscr)\w*\.?|m[ft]vrsave",
    "fma": r"fn?m(add|sub)s?\.?",
    "fpu": r"(f|lf|stf|mffs|mtfs)\w*\.?",
    "trap": r"t[wd]\w*|trap",
    "atomic": r"l[wd]arx|st[wd]cx\.",
}


def ghidra_classes(m):
    return {c for c, rx in GHIDRA.items() if re.fullmatch(rx, m)}


def audit(rows, image, base):
    """rows: [(addr, word, mnemonic)]; returns the disagreement lines."""
    bad = []
    for addr, word, m in rows:
        off = addr - base
        if not 0 <= off <= len(image) - 4 or struct.unpack_from(">I", image, off)[0] != word:
            bad.append(f"{addr:08x} {word:08x} {m}: not the image's word")
            continue
        if m == "??":
            bad.append(f"{addr:08x} {word:08x}: Ghidra decoded no instruction")
            continue
        scan = set(ppcscan.classify(word)) & set(GHIDRA)
        got = ghidra_classes(m)
        if "vmx" in scan or "vmx" in got:
            bad.append(f"{addr:08x} {word:08x} {m}: a vector instruction (scanner {sorted(scan)})")
        elif got != scan:
            bad.append(f"{addr:08x} {word:08x} {m}: Ghidra {sorted(got)}, scanner {sorted(scan)}")
    return bad


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("audit")
    ap.add_argument("image")
    ap.add_argument("--base", default="0x82000000")
    a = ap.parse_args(argv[1:])
    rows = []
    with open(a.audit, encoding="utf-8") as fh:
        for line in fh:
            t = line.rstrip("\n").split("\t")
            if len(t) == 3:
                rows.append((int(t[0], 16), int(t[1], 16), t[2]))
    with open(a.image, "rb") as fh:
        image = fh.read()
    bad = audit(rows, image, int(a.base, 16))
    for b in bad[:200]:
        print(b)
    print(f"decode_audit: {len(rows)} words, {len(bad)} disagreement(s): gate G2 {'FAILS' if bad else 'passes'}")
    return 1 if bad or not rows else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
