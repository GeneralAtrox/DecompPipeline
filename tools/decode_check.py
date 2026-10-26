"""The decode check of a new workspace (tools/game_setup.py): Ghidra's reading of every instruction of every function
against the image's own bytes and the target's own decoder (the translator's decode.py, written from the
manufacturer's encoding tables, independent of Ghidra's SLEIGH).

It reads TcDecodeCheck.java's output ("F<TAB>entry" per function, then "addr<TAB>bytes<TAB>mnemonic" per
instruction) and checks:
- the bytes are the binary's bytes at that address (Ghidra loaded the binary at the right place);
- Ghidra decoded every address of every body ("??" is a gap: a word Ghidra cannot read, or data inside a body);
- Ghidra's mnemonic is the target decoder's, once Ghidra's simplified forms are named as the decoder names them
  (PPC_ALIAS and the rules below: PowerPC li is addi, blr is bclr, ...; SH-4 fmov is fmov.s whatever FPSCR.SZ
  selects; SH delay slots lose Ghidra's "_" prefix).
Any disagreement fails the check: Ghidra and the decoder would hand the oracle and the translator different programs.
A function with no instruction at all (an entry Ghidra made where it decoded no code: a one-byte body inside the
image, or a call target past it) is no disagreement; those are listed apart, for the catalog's owner (project.json
catalog_overrides "data" or "not_entry"), and do not fail the check.

usage: python tools/decode_check.py <workspace> [check.tsv]   (default state/decode_check.tsv)
"""
import importlib
import os
import pathlib
import re
import struct
import sys
from collections import Counter

TOOL = pathlib.Path(__file__).resolve().parents[1]

# Ghidra's PowerPC simplified mnemonics (record "." and overflow forms handled apart) -> the base form
PPC_ALIAS = {
    "li": "addi", "la": "addi", "subi": "addi", "lis": "addis", "subis": "addis", "subic": "addic",
    "cmpwi": "cmpi", "cmplwi": "cmpli", "cmpw": "cmp", "cmplw": "cmpl",
    "mr": "or", "nop": "ori", "not": "nor", "mtcr": "mtcrf",
    "slwi": "rlwinm", "srwi": "rlwinm", "clrlwi": "rlwinm", "clrrwi": "rlwinm", "rotlwi": "rlwinm", "rotrwi": "rlwinm",
    "extlwi": "rlwinm", "extrwi": "rlwinm", "clrlslwi": "rlwinm", "inslwi": "rlwimi", "insrwi": "rlwimi",
    "rotlw": "rlwnm", "sub": "subf", "subo": "subfo", "subc": "subfc", "subco": "subfco",
    "crset": "creqv", "crclr": "crxor", "crmove": "cror", "crnot": "crnor", "trap": "tw",
}
PPC_SPR = re.compile(r"m([ft])(lr|ctr|xer|dsisr|dar|dec|sdr1|srr0|srr1|sprg[0-3]|ear|pvr|ibat[0-3][ul]|dbat[0-3][ul]"
                     r"|gqr[0-7]|hid[0-4]|wpar|dmau|dmal|ummcr[01]|upmc[1-4]|usia|mmcr[01]|pmc[1-4]|sia|thrm[1-3]"
                     r"|ictc|l2cr|iabr|dabr)")
PPC_BRANCH = re.compile(r"b(dnzt|dnzf|dzt|dzf|dnz|dz|lt|le|eq|ge|gt|nl|ne|ng|so|ns|un|nu)?(lr|ctr)?(l)?(a)?")


def ppc_name(m):
    """Ghidra's PowerPC mnemonic as ppctranslate's disasm names it."""
    m = m.rstrip("+-")  # branch prediction hints
    dot = m.endswith(".") and m != "stwcx."
    m = m[:-1] if dot else m
    b = PPC_BRANCH.fullmatch(m)
    if b and m not in ("b", "ba"):
        cond, reg, lk, aa = b.groups()
        if cond or reg:
            m = ("bclr" if reg == "lr" else "bcctr" if reg == "ctr" else "bc") + (lk or "") + (aa or "")
    elif m.startswith("tw") and m != "tw":
        m = "twi" if m.endswith("i") else "tw"
    else:
        s = PPC_SPR.fullmatch(m)
        m = f"m{s.group(1)}spr" if s else PPC_ALIAS.get(m, m)
    return m + ("." if dot else "")


def sh_name(m):
    return m.lstrip("_")  # a delay-slot instruction


def sh4_name(m):
    m = sh_name(m)
    return "fmov.s" if m == "fmov" else m


TARGETS = {  # translator decoder module, instruction-name rule for Ghidra's mnemonics
    "dreamcast_sh4": ("sh4translate", sh4_name),
    "saturn_sh2": ("sh2translate", sh_name),
    "gamecube_ppc": ("ppctranslate", ppc_name),
}


def decoder_name(dec, word, addr):
    i = dec.decode(word, addr)
    if i.op == "illegal":
        return "illegal"
    try:
        text = dec.disasm(i)
    except (KeyError, AttributeError, TypeError):
        text = ""
    name = (text.split() or [i.op])[0].lower()
    return "fmov.s" if name == "fmov" else name


class Image:
    """The bytes of the binary at an address: a flat image at the load address (SH-4, SH-2), or a DOL's sections."""

    def __init__(self, cfg):
        self.data = pathlib.Path(cfg["binary_path"]).read_bytes()
        self.dol = None
        if cfg["target"] == "gamecube_ppc":
            sys.path.insert(0, str(TOOL / "targets" / "gamecube_ppc" / "tools"))
            import gcdisc
            secs = gcdisc.dol_sections(self.data)
            self.dol = secs["text"] + secs["data"]
        self.base = int(str(cfg["load_address"]), 16)

    def read(self, addr, n):
        if self.dol is None:
            off = addr - self.base
            return self.data[off:off + n] if 0 <= off <= len(self.data) - n else None
        for _, off, a, size in self.dol:
            if a <= addr and addr + n <= a + size:
                return self.data[off + addr - a:off + addr - a + n]
        return None


def check(rows, cfg):
    """rows: [(function, addr, bytes hex, mnemonic)] -> (counts, disagreement lines, empty functions)."""
    mod, name = TARGETS[cfg["target"]]
    sys.path.insert(0, str(TOOL / "targets" / cfg["target"] / "translator"))
    dec = importlib.import_module(mod + ".decode")
    img = Image(cfg)
    fmt = {2: "<H" if cfg["target"] == "dreamcast_sh4" else ">H", 4: ">I"}
    n, bad, kinds, decoded = 0, [], Counter(), {}
    gaps = []
    for fn, addr, hexb, m in rows:
        n += 1
        decoded.setdefault(fn, False)
        if m == "??":
            gaps.append((fn, f"{addr:08x} {hexb}: Ghidra decoded no instruction (function {fn:08x})"))
            continue
        decoded[fn] = True
        b = bytes.fromhex(hexb)
        if img.read(addr, len(b)) != b:
            kinds["bytes"] += 1
            bad.append(f"{addr:08x} {hexb} {m}: not the binary's bytes")
            continue
        word = struct.unpack(fmt[len(b)], b)[0] if len(b) in fmt else None
        want = decoder_name(dec, word, addr) if word is not None else f"{len(b)}-byte"
        if name(m) != want:
            kinds["name"] += 1
            bad.append(f"{addr:08x} {hexb} {m}: the decoder reads {want}")
    empty = sorted(fn for fn, ok in decoded.items() if not ok)
    for fn, line in gaps:
        if decoded[fn]:
            kinds["gap"] += 1
            bad.append(line)
    counts = {"functions": len(decoded), "instructions": n - sum(1 for fn, _ in gaps if not decoded[fn]),
              **{k: kinds[k] for k in ("gap", "bytes", "name")}, "empty_functions": len(empty)}
    return counts, bad, [f"{a:08x}" for a in empty]


def read_rows(path):
    rows, fn = [], None
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            t = line.rstrip("\n").split("\t")
            if len(t) == 2 and t[0] == "F":
                fn = int(t[1], 16)
            elif len(t) == 3:
                rows.append((fn, int(t[0], 16), t[1], t[2]))
    return rows


def report(counts, bad, empty, out=sys.stdout):
    """Prints the first disagreements, the empty functions and the verdict line; True when the check passes."""
    for b in bad[:200]:
        print(b, file=out)
    if empty:
        print(f"functions with no instruction (not a disagreement; for the catalog's owner): {' '.join(empty[:50])}"
              + (" ..." if len(empty) > 50 else ""), file=out)
    ok = not bad and counts["instructions"] > 0
    print(f"decode check: {counts['instructions']} instructions of {counts['functions']} functions; {len(bad)} "
          f"disagreement(s) ({counts['gap']} gaps, {counts['bytes']} not the binary's bytes, {counts['name']} other "
          f"instructions); {len(empty)} function(s) with no instruction: {'passes' if ok else 'FAILS'}", file=out)
    return ok


def main(argv):
    if len(argv) < 2:
        raise SystemExit(__doc__)
    ws = pathlib.Path(argv[1]).resolve()
    os.environ["DECOMP_PROJECT"] = str(ws)
    sys.path.insert(0, str(TOOL))
    from core import paths
    cfg = paths.config()
    tsv = pathlib.Path(argv[2]) if len(argv) > 2 else paths.STATE / "decode_check.tsv"
    counts, bad, empty = check(read_rows(tsv), cfg)
    return 0 if report(counts, bad, empty) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
