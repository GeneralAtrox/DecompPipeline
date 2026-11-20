"""Proofs of tools/vote_encode.py, and the corpus assembler built on it.

  assemble(case): the case's words (two passes: labels, then words at 0x82010000 + 4i), or the reason it cannot be
    assembled.
  round trip: for every word, decode.py's op equals the encoder's and every field the text set decodes to the same value;
    and asm_text(decode(word)) (decode.py's fields printed back in assembler syntax) re-encodes to the same word.
  table_check(): the primary / extended opcodes vote_encode.py wrote from the manuals against decode.py's tables.
  golden_anchor(): golden_words.json (words from the game's code on which decode.py agreed with a reference
    disassembler) re-encoded from their fields, for every mnemonic the corpus uses.
  literal_anchor(): instruction words written as literals with their assembly in a comment in this repo's tests
    (`0x7CA32014,   # addc r5,r3,r4`), encoded from the comment.

Nearest existing: translator/tests/test_decode.py - checks decode.py against hand-worked words; nothing checked an
encoder, since none existed.
"""
import json
import pathlib
import re

import vote_encode as E
from vote_encode import D

BASE = 0x82010000
ROOT = pathlib.Path(__file__).resolve().parents[3]
GOLDEN = ROOT / "targets" / "xbox360_ppc" / "translator" / "tests" / "golden_words.json"


def asm_text(i):
    """decode.py's Insn printed in the assembler syntax vote_encode.py reads (base mnemonics only)."""
    o, f = i.op, i.f
    dot = "." if f.get("rc") and not o.endswith(".") else ""
    g = f.get
    if o in E.DFORM or o in E.DS:
        return f"{o} {'f' if o.startswith(('lf', 'stf')) else 'r'}{f['rd']}, {f['d']}(r{f['ra']})"
    if o in ("twi", "tdi"):
        return f"{o} {f['to']}, r{f['ra']}, {f['simm']}"
    if o in E.IMM:
        return f"{o} r{f['rd']}, r{f['ra']}, {f['simm']}"
    if o in E.LOGI:
        return f"{o} r{f['ra']}, r{f['rs']}, {f['uimm']}"
    if o in ("cmp", "cmpl"):
        return f"{o} cr{f['crfd']}, {f['l']}, r{f['ra']}, r{f['rb']}"
    if o in ("cmpi", "cmpli"):
        return f"{o} cr{f['crfd']}, {f['l']}, r{f['ra']}, {g('simm', g('uimm'))}"
    if o in E.XO31:
        t = f"{o}{'o' if g('oe') else ''}{dot} r{f['rd']}, r{f['ra']}"
        return t if o in E.XO_UNARY else t + f", r{f['rb']}"
    if o in ("srawi", "sradi"):
        return f"{o}{dot} r{f['ra']}, r{f['rs']}, {f['sh']}"
    if o in ("rlwinm", "rlwimi"):
        return f"{o}{dot} r{f['ra']}, r{f['rs']}, {f['sh']}, {f['mb']}, {f['me']}"
    if o == "rlwnm":
        return f"{o}{dot} r{f['ra']}, r{f['rs']}, r{f['rb']}, {f['mb']}, {f['me']}"
    if o in ("rldicl", "rldicr", "rldic", "rldimi"):
        return f"{o}{dot} r{f['ra']}, r{f['rs']}, {f['sh']}, {g('mb', g('me'))}"
    if o in ("rldcl", "rldcr"):
        return f"{o}{dot} r{f['ra']}, r{f['rs']}, r{f['rb']}, {g('mb', g('me'))}"
    if o in E.LOGIC:
        return f"{o}{dot} r{f['ra']}, r{f['rs']}, r{f['rb']}"
    if o in E.LOGIC1:
        return f"{o}{dot} r{f['ra']}, r{f['rs']}"
    if o in E.LOADX or o in E.STOREX:
        k = "f" if o.startswith(("lf", "stf")) else "r"
        return f"{o} {k}{g('rd', g('rs'))}, r{f['ra']}, r{f['rb']}"
    if o in E.VLS31 or o in E.V128_LS:
        return f"{o} v{g('vd', g('vs'))}, r{f['ra']}, r{f['rb']}"
    if o in E.CACHE or o == "dcbz128":
        return f"{o} r{f['ra']}, r{f['rb']}"
    if o in ("tw", "td"):
        return f"{o} {f['to']}, r{f['ra']}, r{f['rb']}"
    if o in ("sync", "lwsync", "eieio", "isync"):
        return o
    if o in ("mfspr", "mftb"):
        return f"{o} r{f['rd']}, {f['spr']}"
    if o == "mtspr":
        return f"{o} {f['spr']}, r{f['rd']}"
    if o in ("mfcr", "mfmsr"):
        return f"{o} r{f['rd']}"
    if o == "mfocrf":
        return f"{o} r{f['rd']}, {f['crm']}"
    if o == "mtcrf":
        return f"{o} {f['crm']}, r{f['rs']}"
    if o in ("mtmsr", "mtmsrd"):
        return f"{o} r{f['rs']}, {f['l']}"
    if o == "mcrxr":
        return f"{o} cr{f['crfd']}"
    if o in ("mcrf", "mcrfs"):
        return f"{o} cr{f['crfd']}, cr{f['crfs']}"
    if o in D.CR_LOGIC:
        return f"{o} {f['crbd']}, {f['crba']}, {f['crbb']}"
    if o in ("bclr", "bcctr"):
        return f"{o}{'l' if f['lk'] else ''} {f['bo']}, {f['bi']}"
    if o == "b":
        return f"b{'l' if f['lk'] else ''} 0x{f['target']:x}"
    if o == "bc":
        return f"bc{'l' if f['lk'] else ''} {f['bo']}, {f['bi']}, 0x{f['target']:x}"
    if o in E.A59 or o in E.A63:
        core = o[:-1] if o in E.A59 else o
        if core in ("fadd", "fsub", "fdiv"):
            ops = (f["frd"], f["fra"], f["frb"])
        elif core == "fmul":
            ops = (f["frd"], f["fra"], f["frc"])
        elif core in ("fsqrt", "fre", "frsqrte"):
            ops = (f["frd"], f["frb"])
        else:
            ops = (f["frd"], f["fra"], f["frc"], f["frb"])
        return f"{o}{dot} " + ", ".join(f"f{x}" for x in ops)
    if o in ("fcmpu", "fcmpo"):
        return f"{o} cr{f['crfd']}, f{f['fra']}, f{f['frb']}"
    if o == "mffs":
        return f"{o}{dot} f{f['frd']}"
    if o == "mtfsf":
        return f"{o}{dot} {f['fm']}, f{f['frb']}"
    if o == "mtfsfi":
        return f"{o}{dot} cr{f['crfd']}, {f['imm']}"
    if o in ("mtfsb0", "mtfsb1"):
        return f"{o}{dot} {f['crbd']}"
    if o in E.X63:
        return f"{o}{dot} f{f['frd']}, f{f['frb']}"
    v = lambda k: f"v{f[k]}"  # noqa: E731
    if o in ("vmaddfp", "vnmsubfp"):
        return f"{o} {v('vd')}, {v('va')}, {v('vc')}, {v('vb')}"
    if o in ("vmaddfp128", "vnmsubfp128", "vsel128"):
        return f"{o} {v('vd')}, {v('va')}, {v('vb')}, {v('vd')}"
    if o == "vmaddcfp128":
        return f"{o} {v('vd')}, {v('va')}, {v('vd')}, {v('vb')}"
    if o == "vupkd3d128":
        return f"{o} {v('vd')}, {v('vb')}, {(f['imm'] << 2) | f['mask']}"
    parts = [v(k) for k in ("vd", "vs", "va", "vb", "vc") if k in f]
    parts += [str(f[k]) for k in ("sh", "uimm", "simm", "perm", "imm", "mask", "z") if k in f]
    if o in E.VA or o in E.VC or o in E.VX or o.endswith("128"):
        return f"{o}{dot} " + ", ".join(parts) if parts else o
    raise E.EncodeError(f"no assembler text for {o}")


def assemble(case, base=BASE):
    """(words, None) or (None, reason)."""
    lines = [(k, t) for k, t in case["lines"]]
    labels, pc = {}, base
    for k, t in lines:
        if k == "label":
            labels[t] = pc
        else:
            pc += 4
    words, pc = [], base
    for k, t in lines:
        if k == "label":
            continue
        try:
            w, op, f = E.encode(t if k == "insn" else ".long " + t, pc, labels)
        except E.EncodeError as e:
            return None, f"encoder: {e}"
        except (IndexError, ValueError) as e:
            return None, f"encoder: cannot parse {t!r} ({e})"
        if op is not None:
            bad = round_trip(w, op, f, pc)
            if bad:
                return None, f"round trip: {t!r}: {bad}"
        words.append(w)
        pc += 4
    return words, None


def round_trip(w, op, f, pc=0):
    """None, or what failed: decode.py's op and fields against the encoder's, and the re-encoding of its text."""
    d = D.decode(w, pc)
    if d.op != op:
        return f"decodes as {d.op} ({D.disasm(d)}), not {op}"
    for k, val in f.items():
        if d.f.get(k, 0) != val:
            return f"field {k}: decodes {d.f.get(k)}, text {val}"
    try:
        w2 = E.encode(asm_text(d), pc)[0]
    except Exception as e:  # noqa: BLE001  (reported, never passed)
        return f"decoded text {asm_text(d)!r} does not re-encode: {e}"
    return None if w2 == w else f"decoded text {asm_text(d)!r} re-encodes as {w2:08x}"


def table_check():
    """{mnemonic: problem} for every manual-written opcode that decode.py's tables disagree with or lack."""
    out = {}
    pairs = [(E.DFORM, D.D_LOADSTORE), (E.XO31, D.OP31_XO), (E.X19, D.OP19), (E.A59, D.OP59), (E.A63, D.OP63_A),
             (E.X63, D.OP63_X), (E.VA, D.VA4), (E.VC, D.VC4), (E.VX, D.VX4)]
    for own, dec in pairs:
        for m, x in own.items():
            if dec.get(x) != m:
                out[m] = f"own {x}, decode.py {'has ' + str(dec.get(x)) if x in dec else 'has no entry'} there"
    for m, x in E.X31.items():
        got = D.OP31_X.get(x)
        if got != m and not (m == "sync" and got == "sync"):
            out[m] = f"own {x}, decode.py {'has ' + str(got) if got else 'has no entry'} there"
    for m, (p, x) in E.DS.items():
        if (D.DS58 if p == 58 else D.DS62).get(x) != m:
            out[m] = "DS form differs"
    return out


def golden_anchor(ops):
    """{op: True/False} for the corpus's ops that golden_words.json holds: its word re-encoded from its fields."""
    g = json.loads(GOLDEN.read_text(encoding="utf-8"))
    out = {}
    for op, e in g.items():
        if op not in ops:
            continue
        a, w = int(e["addr"], 16), int(e["word"], 16)
        d = D.Insn(a, w, op, e["f"])
        try:
            out[op] = E.encode(asm_text(d), a)[0] == w
        except Exception:  # noqa: BLE001
            out[op] = False
    return out


# a line whose first hex literal is a 32-bit word and which ends with its assembly in a comment
LITERAL = re.compile(r"^(?:(?!0x)[^#\n])*?\b0x([0-9A-Fa-f]{8})\b[^#\n]*#[ \t]*"
                     r"([a-z][a-z0-9.]*(?:[ \t][^\n]*)?)$", re.M)


def literal_anchor(root=ROOT):
    """{mnemonic: [matched, mismatched, mismatch notes]} over the repo's test literals (branches with relative offsets left out)."""
    out = {}
    files = list((root / "tests").glob("test_x*360*.py")) + list((root / "targets" / "xbox360_ppc").rglob("tests/*.py"))
    for p in files:
        for m in LITERAL.finditer(p.read_text(encoding="utf-8", errors="replace")):
            w, text = int(m.group(1), 16), re.split(r"\s{2,}|;", m.group(2).strip())[0].rstrip(",").strip()
            mn = text.split()[0]
            if re.match(r"b(?!lr|ctr)|bd", mn) and mn not in ("blr", "bctr"):
                continue
            try:
                got = E.encode(text, 0)[0]
            except Exception:  # noqa: BLE001  (not an instruction comment this encoder reads)
                continue
            out.setdefault(mn, [0, 0, []])[0 if got == w else 1] += 1
            if got != w:
                out[mn][2].append(f"{p.name}: 0x{w:08x} # {text} (encodes {got:08x})")
    return out
