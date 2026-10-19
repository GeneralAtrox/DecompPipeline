"""The f32 constants a function's original reads from its literal pool, and whether a C file writes them
(docs/PROOF_GATES.md, float gaps).

The packet listing notes the pool word of a `mov.l` load (TcBase.describeConst: "f32 X" for a plain number), but a
`mova` only names the pool address; the `fmov.s @r0,frN` after it reads the constant. TC 8c035a0c compares an f32
callee result with the word at 0x8c035b10 (1e-06): matched C wrote 1e-06f, the readable C 1.0f, and the oracle never
drew an f32 callee result that told them apart (TcFret now does). Static and host-only:
- pool_floats(): {pool address: u32 bits} of the words the listing reads as f32: each word a pool `mova`'s r0 (or a
  copy of it, moved by `add #k`) points at when `fmov.s @rN` / `@rN+` reads it, and each `mov.l` pool load noted
  as an f32;
- literals(): the float and integer literals of a C text (comments dropped, a quotient of two folded), the hex
  addresses it names and the values of the names it uses that defines() found (`#define PROJ_DEFAULT_NEAR 10.0f` in
  a readable header);
- missing(): the pool constants a C text writes in none of those forms (as the float, its negation, the same bits,
  a read of the pool address itself, or an integer of the same value as a `*` or `/` operand or under an f32 cast: `y * 2`, `(f32)STEP` with `#define STEP 2` for 2.0f).
  An integer of the same value elsewhere doesn't count: TC 8c078f94's C has KC = 0.5f for the pool's -6.0f, and
  its `6u` integers hid that.
A C file that computes a constant (1.0f / 11.0f) is a false suspect, so the scan (tools/float_gaps.py) reports these
as suspects to review, and compares a readable file with its matched file: a constant the matched C writes as a
float but the readable C lost is the 8c035a0c case.
"""
import re
import struct

from core import asm_pool, sh4_asm

F32_NOTE = re.compile(r"=\s*0x([0-9a-f]{8})\s*\(.*\bf32\b")
POOL_OP = re.compile(r"@?\(?\s*0x([0-9a-f]{8})")
FLOAT = re.compile(r"(?<![\w.])((?:\d+\.\d*|\.\d+)(?:[eE][-+]?\d+)?|\d+[eE][-+]?\d+)[fFlL]?(?![\w.])")
INT = re.compile(r"(?<![\w.])(0[xX][0-9a-fA-F]+|\d+)[uUlL]*(?![\w.])")
COMMENT = re.compile(r"/\*.*?\*/|//[^\n]*", re.S)
DEFINE = re.compile(r"^[ \t]*#[ \t]*define[ \t]+(\w+)[ \t]+([^\n]+)$|\bconst\s+(?:f32|float|u32|s32|int)\s+(\w+)\s*=\s*"
                    r"([^;]+);", re.M)
IDENT = re.compile(r"\b[A-Za-z_]\w*\b")
_NUM = r"((?:\d+\.\d*|\.\d+)(?:[eE][-+]?\d+)?|\d+[eE][-+]?\d+)[fF]?"
QUOTIENT = re.compile(r"(?<![\w.])" + _NUM + r"\s*/\s*" + _NUM + r"(?![\w.])")
FACTOR = re.compile(r"(?:(?:[*/]|\(\s*(?:f32|float|f64|double)\s*\))\s*\(?\s*(\d+)(?![\w.])|(?<![\w.])(\d+)\s*\)?\s*[*/])")
# y * 2, 2 * y, y / 2, (f32)2, (f32)(2): an integer in f32 arithmetic (no u suffix)
SCAN = 16  # instructions after a mova that may read its words


def _bits(text):
    try:
        return struct.unpack("<I", struct.pack("<f", float(text)))[0]
    except (OverflowError, ValueError):
        return None


def _f32_of(bits):
    return struct.unpack("<f", struct.pack("<I", bits))[0] if bits is not None else 0.0


def _word(image, base, a):
    off = a - base
    return struct.unpack_from("<I", image, off)[0] if 0 <= off <= len(image) - 4 else None


def pool_floats(insns, image, base, ranges, fpu=None):
    """{pool address: u32 bits} (module docstring). fpu: a set that receives the addresses an `fmov.s` reads (an
    f32 for sure; a `mov.l` word noted as an f32 may be an integer such as 0x4000000d)."""
    out = {}
    for x in insns:
        if x.mn == "mov.l":
            m = F32_NOTE.search(x.note or "")
            p = POOL_OP.match(x.ops[0]) if m and x.ops else None
            if p:
                out[int(p.group(1), 16)] = int(m.group(1), 16)
        if x.mn != "mova" or not x.ops:
            continue
        p = POOL_OP.match(x.ops[0])
        t = int(p.group(1), 16) if p else None
        if t is None or any(lo <= t < hi for lo, hi in ranges):
            continue
        alias = {"r0": 0}  # register -> offset from t
        for y in insns[x.idx + 1:x.idx + 1 + SCAN]:
            if not alias:
                break
            src = y.ops[0] if y.ops else ""
            m = re.fullmatch(r"@(r\d+)(\+?)", src)
            if y.mn == "fmov.s" and m and m.group(1) in alias:
                a = t + alias[m.group(1)]
                w = _word(image, base, a)
                if w is not None and asm_pool._f32(w) is not None:
                    out[a] = w
                    if fpu is not None:
                        fpu.add(a)
                if m.group(2):
                    alias[m.group(1)] += 4
                continue
            if y.mn == "add" and len(y.ops) == 2 and y.ops[1] in alias and y.ops[0].startswith("#"):
                alias[y.ops[1]] += int(y.ops[0][1:], 0)
                continue
            if y.mn == "mov" and len(y.ops) == 2 and y.ops[0] in alias:
                alias[y.ops[1]] = alias[y.ops[0]]
                continue
            for r in list(alias):
                if sh4_asm.uses(y, r)[1]:
                    del alias[r]
            if y.mn in sh4_asm.CALLS or y.mn in ("rts", "bra", "jmp", "braf"):
                break
    return out


def defines(texts):
    """{name: value text} of the `#define NAME value` and `const T NAME = value;` lines of header texts."""
    out = {}
    for t in texts:
        for m in DEFINE.finditer(COMMENT.sub(" ", t)):
            out[m.group(1) or m.group(3)] = (m.group(2) or m.group(4)).strip()
    return out


def literals(text, defs=None):
    """(float bits, integer values, factors) a C text writes outside comments (an address is an integer value), the
    values of the defs names it uses included; factors are the unsuffixed integer operands of `*` and `/` and of an
    f32 cast, with the defs names put in place."""
    code = COMMENT.sub(" ", text)
    inline = code
    if defs:
        for _ in range(3):
            inline = IDENT.sub(lambda m: f"({defs[m.group(0)]})" if m.group(0) in defs else m.group(0), inline)
        add, todo, seen = [], set(IDENT.findall(code)), set()
        for _ in range(3):  # a name defined by another name
            nxt = set()
            for n in todo - seen:
                if n in defs:
                    add.append(defs[n])
                    nxt |= set(IDENT.findall(defs[n]))
            seen |= todo
            todo = nxt
        code += "\n" + "\n".join(add)
    floats = {b for b in (_bits(m.group(1)) for m in FLOAT.finditer(code)) if b is not None}
    for m in QUOTIENT.finditer(code):  # a folded constant: (1.0f / 4096.0f)
        x, y = (_f32_of(_bits(g)) for g in m.groups())
        if y:
            floats.add(_bits(repr(x / y)))
    ints = set()
    for m in INT.finditer(code):
        v = int(m.group(1), 0) if not re.fullmatch(r"0\d+", m.group(1)) else int(m.group(1), 8)
        ints.add(v & 0xFFFFFFFF)
    factors = {int(m.group(1) or m.group(2)) for m in FACTOR.finditer(COMMENT.sub(" ", inline))}
    return floats, ints, factors


def as_float(bits, lits):
    """The C text writes bits as a float literal (or its negation)."""
    return bits in lits[0] or bits ^ 0x80000000 in lits[0]


def present(bits, addr, lits):
    ints = lits[1]
    v = struct.unpack("<f", struct.pack("<I", bits))[0]
    whole = int(abs(v)) if v == v and abs(v) < 2 ** 32 and v == int(v) else None  # `x * 2` for 2.0f
    return (as_float(bits, lits) or bits in ints or addr in ints or (bits & 0x7FFFFFFF) == 0
            or (whole is not None and whole in lits[2]))


def missing(consts, text, defs=None):
    """{pool address: bits} of consts the C text writes in no form (present())."""
    lits = literals(text, defs)
    return {a: b for a, b in consts.items() if not present(b, a, lits)}


def describe(bits):
    return f"0x{bits:08x} ({asm_pool._f32(bits) or struct.unpack('<f', struct.pack('<I', bits))[0]})"
