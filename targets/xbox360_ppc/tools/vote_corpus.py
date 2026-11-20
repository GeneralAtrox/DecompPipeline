"""Reader of an instruction test corpus in the Xenia PPC testing format (src/xenia/cpu/ppc/testing/*.s, BSD; its
README.md): each case starts at a `test_<name>:` label; its lines are instructions (other labels are branch targets
inside the case), `#_ REGISTER_IN reg value`, `#_ REGISTER_OUT reg value`, `#_ MEMORY_IN addr bytes` and
`#_ MEMORY_OUT addr bytes`. Cases run from an all-zero state except the REGISTER_INs and MEMORY_INs, until `blr`.
`.macro` / `.endm` blocks are expanded (\\arg substitution); `.long` is a raw word.

Values, as the corpus's own runner reads them (its ppc_context.cc / string_util.h, read for the format only):
  r: integer, optional "-", "0x" hex or decimal, taken mod 2^64.
  f: "0x..." is the double's bits; a decimal is a double (strtod). "[hex]" is NOT a recognised form there: strtod of
     "[..." is 0.0, so the corpus's own runner seeds and checks 0.0. Here it is read as bits (the evident intent) and
     the case is flagged "bracket_float", so a result can be reported both ways.
  v: "[w0, w1, w2, w3]" hex words, element 0 first.  cr: integer (the 32-bit CR).
Compare kinds kept per output: "bits" (r, v, cr, f given as hex) or "value" (f given as a decimal: compared as a double,
so -0.0 equals 0.0 and a NaN never equals anything, as the corpus's runner does).

Nothing here copies the corpus: it is read from the path given (tools/x360_vote.py --corpus).

Nearest existing: targets/xbox360_ppc/translator/tests/golden_words.json - fixed words with fields, no register
inputs or expected outputs; no reader of this format existed in the repo (git log --all -S REGISTER_IN: none).
"""
import math
import pathlib
import re
import struct

DEFAULT = pathlib.Path(r"C:\Projects\XeniaRedux\src\xenia\cpu\ppc\testing")
M64 = (1 << 64) - 1


def int_value(s):
    s = s.strip()
    neg = s.startswith("-")
    s = s[1:] if neg else s
    if s.lower().startswith("0x"):
        v = int(re.match(r"[0-9A-Fa-f]+", s[2:]).group(0), 16)   # the runner's from_chars: the hex prefix ("...ull")
    elif s.lower().endswith("h"):
        v = int(s[:-1], 16)
    else:
        v = int(s, 10)
    return (-v if neg else v) & M64


def dbits(d):
    return struct.unpack(">Q", struct.pack(">d", d))[0]


def float_value(s):
    """(bits, kind, bracket): kind "bits" for hex forms, "value" for a decimal."""
    s = s.strip()
    if s.startswith("[") and s.endswith("]"):
        return int(s[1:-1], 16) & M64, "bits", True
    neg = s.startswith("-")
    t = s[1:] if neg else s
    if t.lower().startswith("0x"):
        v = int(t[2:], 16)
        return ((-v) & M64 if neg else v), "bits", False
    return dbits(float(s)), "value", False


def vec_value(s):
    m = re.fullmatch(r"\[\s*([0-9A-Fa-f]+)\s*,\s*([0-9A-Fa-f]+)\s*,\s*([0-9A-Fa-f]+)\s*,\s*([0-9A-Fa-f]+)\s*\]", s.strip())
    if not m:
        raise ValueError(f"vector value {s!r}")
    return tuple(int(x, 16) for x in m.groups())


def reg_value(name, s):
    """(value, compare kind, bracket flag) of one REGISTER_IN/OUT."""
    if re.fullmatch(r"r\d+", name):
        return int_value(s), "bits", False
    if re.fullmatch(r"f\d+", name):
        return float_value(s)
    if re.fullmatch(r"v\d+", name):
        return vec_value(s), "bits", False
    if name == "cr":
        return int_value(s) & 0xFFFFFFFF, "bits", False
    raise ValueError(f"register {name}")


def mem_bytes(s):
    s = s.strip()
    if s.startswith("["):
        return bytes(int(x, 16) for x in re.findall(r"[0-9A-Fa-f]{2}", s))
    s = s.replace(" ", "")
    return bytes.fromhex(s)


def strip_comment(line):
    """An assembler line without its '#' comment (annotations are taken before this)."""
    i = line.find("#")
    return line if i < 0 else line[:i]


def annotate(cur, key, a, b):
    """One #_ annotation into its case."""
    if key == "REGISTER_IN":
        v, _, br = reg_value(a, b)
        cur["reg_in"][a] = v
        cur["bracket_float"] |= br
    elif key == "REGISTER_OUT":
        v, kind, br = reg_value(a, b)
        cur["reg_out"].append((a, v, kind))
        cur["bracket_float"] |= br
    elif key in ("MEMORY_IN", "MEMORY_OUT") and b.strip().startswith("["):
        cur["bracket_mem"] = True  # Xenia's reader takes "[AB, CD]" as hex text with the commas: its values differ
        cur["mem_in" if key == "MEMORY_IN" else "mem_out"].append((int(a, 16) & 0xFFFFFFFF, mem_bytes(b)))
    elif key == "MEMORY_IN":
        cur["mem_in"].append((int(a, 16) & 0xFFFFFFFF, mem_bytes(b)))
    elif key == "MEMORY_OUT":
        cur["mem_out"].append((int(a, 16) & 0xFFFFFFFF, mem_bytes(b)))


def parse_text(text, file="<text>"):
    """[case dict] of one file's text. A case: file, label, lines [(kind, text)] where kind is "insn", "label" or
    "long", reg_in {reg: value}, reg_out [(reg, value, kind)], mem_in [(addr, bytes)], mem_out [(addr, bytes)],
    bracket_float, line (1-based line of its label)."""
    macros, cases, cur, mac = {}, [], None, None
    for n, raw in enumerate(text.splitlines(), 1):
        s = raw.strip()
        if mac is not None:
            if s.startswith(".endm"):
                macros[mac[0]] = mac
                mac = None
            else:
                mac[2].append(strip_comment(s).strip())
            continue
        if s.startswith(".macro"):
            parts = re.split(r"[\s,]+", s[len(".macro"):].strip())
            mac = (parts[0], parts[1:], [])
            continue
        if s.startswith("#_"):
            p = s[2:].strip().split(None, 2)
            if cur is None or len(p) < 3:
                continue
            key, a, b = p[0], p[1], p[2].split("#", 1)[0].strip()
            try:
                annotate(cur, key, a, b)
            except ValueError as e:
                cur["errors"].append(f"line {n}: {e}")
            continue
        s = strip_comment(s).strip()
        if not s or s.startswith(("/*", "*", ".globl", ".text", ".section", ".align")):
            continue
        m = re.fullmatch(r"([A-Za-z_.$][\w.$]*):(.*)", s)
        if m:
            if m.group(1).startswith("test_"):
                cur = {"file": file, "label": m.group(1), "line": n, "lines": [], "reg_in": {}, "reg_out": [],
                       "mem_in": [], "mem_out": [], "bracket_float": False, "bracket_mem": False, "errors": []}
                cases.append(cur)
            elif cur is not None:
                cur["lines"].append(("label", m.group(1)))
            s = m.group(2).strip()
            if not s:
                continue
        if cur is None:
            continue
        head = s.split(None, 1)
        if head[0] == ".long":
            cur["lines"].append(("long", head[1].strip()))
        elif head[0] in macros:
            name, params, body = macros[head[0]]
            args = [a.strip() for a in head[1].split(",")] if len(head) > 1 else []
            for b in body:
                for p, a in zip(params, args):
                    b = b.replace("\\" + p, a)
                if b:
                    cur["lines"].append(("insn", b))
        else:
            cur["lines"].append(("insn", s))
    return cases


def read_skip(d):
    p = pathlib.Path(d) / "skip.txt"
    if not p.is_file():
        return set()
    return {ln.strip() for ln in p.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()}


def read_corpus(d=DEFAULT):
    """All cases of every *.s file under d, in file order; each with "skip": its name is in the corpus's skip.txt (the
    corpus's own runner skips it)."""
    d = pathlib.Path(d)
    skip = read_skip(d)
    out = []
    for f in sorted(d.glob("*.s")):
        for c in parse_text(f.read_text(encoding="utf-8", errors="replace"), f.name):
            c["skip"] = c["label"][len("test_"):] in skip
            out.append(c)
    return out


def is_nan_bits(b):
    return (b >> 52) & 0x7FF == 0x7FF and b & ((1 << 52) - 1)


def f_equal(exp, act, kind):
    """(equal, nan_payload_only) of an FPR: "value" compares as doubles (corpus rule); "bits" compares bits, and two
    NaNs whose bits differ are flagged nan_payload (a QNaN's payload and sign are the FPU's choice)."""
    if kind == "value":
        e, a = struct.unpack(">d", struct.pack(">Q", exp))[0], struct.unpack(">d", struct.pack(">Q", act))[0]
        if math.isnan(e) and math.isnan(a):
            return False, True
        return e == a, False
    if exp == act:
        return True, False
    return False, bool(is_nan_bits(exp) and is_nan_bits(act))
