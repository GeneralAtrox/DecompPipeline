"""Settles leg A's disagreements with the corpus (tools/x360_vote.py settle) from the architecture's rules: the PowerPC
ISA (Book I: floating-point arithmetic, NaN propagation, FPSCR exceptions, conversions, undefined upper words) and the
AltiVec PEM (vsl/vsr shift counts, estimate precision, partial loads). Each rule is written here from the manuals, not
from the translator's C helpers (runtime/tc_ppc.c, vmx.py) nor from Xenia. Verdicts per case: "ours" (A follows the
rule, the corpus does not), "corpus", "neither", "both" (the rule leaves the differing bits undefined, both are legal)
or "undetermined" (no rule here, or it depends on a machine state the corpus does not give).

Assumptions, stated: FPSCR starts 0 (round to nearest, every exception disabled) as the corpus's cases imply; the
single-precision result of a NaN is the NaN with its low 29 fraction bits cleared (the ISA states this for frsp);
the Xbox 360 runs its code in 32-bit mode (MSR[SF]=0), so XER[CA] is the carry out of the low 32 bits and a record
form's CR0 is that of the low 32 bits (Book I 3.3.9; the game's own code needs it: _fptostr 82a6cdc0 tests an int
with addic. / bgt after cmpwi).

Nearest existing: tests/test_xbox360_helpers.py - Python models of the runtime's float helpers, used to test that C;
being the C's own answer key they are not an independent vote, and they model no FPSCR flags or NaN choice.
"""
import math
import struct
from fractions import Fraction

DEF_NAN = 0x7FF8000000000000
QBIT = 1 << 51
M64 = (1 << 64) - 1


def dbl(b):
    return struct.unpack(">d", (b & M64).to_bytes(8, "big"))[0]


def bits(x):
    return int.from_bytes(struct.pack(">d", x), "big")


def is_nan(b):
    return (b >> 52) & 0x7FF == 0x7FF and b & ((1 << 52) - 1) != 0


def is_snan(b):
    return is_nan(b) and not b & QBIT


def round_bin(q, p, emin, emax):
    """The nearest (ties to even) binary float with p bits and exponents emin..emax to the exact q: (Fraction or +-inf
    as float, inexact, tiny)."""
    if q == 0:
        return Fraction(0), False, False
    s, a = (-1 if q < 0 else 1), abs(q)
    e = a.numerator.bit_length() - a.denominator.bit_length()
    while Fraction(2) ** e > a:
        e -= 1
    while Fraction(2) ** (e + 1) <= a:
        e += 1
    tiny = e < emin
    ulp = Fraction(2) ** (max(e, emin) - p + 1)
    n, r = divmod(a, ulp)
    if r > ulp / 2 or (r == ulp / 2 and n % 2):
        n += 1
    v = n * ulp
    if v >= Fraction(2) ** (emax + 1):
        return s * math.inf, True, False
    return s * v, v != a, tiny


def to_bits(v, single):
    """Bits of a rounded value as the FPR holds it (singles as the double of the same value)."""
    if isinstance(v, float):
        return bits(v)
    return bits(float(v)) if v != 0 else 0


def nan_result(op, srcs, single):
    """The ISA's NaN choice: frA, then frB, then frC (each quieted); None when no operand is a NaN."""
    for b in srcs:
        if b is not None and is_nan(b):
            r = b | QBIT
            return r & ~((1 << 29) - 1) if single else r
    return None


def fp_arith(op, a, b, c):
    """(result bits, fpscr flags {VX, OX, UX, ZX, XX}) of one scalar arithmetic op from FPR bits a, b, c (None when the
    form has no such operand)."""
    single = op.endswith("s") and op not in ("fabs",)
    base = op[:-1] if single else op
    neg = base.startswith("fn")
    fl = dict(VX=0, OX=0, UX=0, ZX=0, XX=0)
    srcs = {"fadd": (a, b), "fsub": (a, b), "fdiv": (a, b), "fmul": (a, c), "fsqrt": (b,)}.get(base, (a, b, c))
    if any(x is not None and is_snan(x) for x in srcs):
        fl["VX"] = 1
    n = nan_result(op, srcs, single)
    if n is not None:
        ac = [dbl(x) for x in (a, c) if x is not None and not is_nan(x)]
        if base in ("fmul", "fmadd", "fmsub", "fnmadd", "fnmsub") and len(ac) == 2 and 0 in ac and any(
                math.isinf(x) for x in ac):
            fl["VX"] = 1  # assumed: 0 x inf signals VXIMZ even when the addend is a QNaN
        return n, fl
    A, B, C = (dbl(x) if x is not None else None for x in (a, b, c))
    fma = base in ("fmadd", "fmsub", "fnmadd", "fnmsub")
    invalid = ((base in ("fadd", "fsub") and math.isinf(A) and math.isinf(B) and (A == B) == (base == "fsub"))
               or (base == "fmul" and {A * 0 == 0, C * 0 == 0} == {True, False} and 0 in (A, C))
               or (base == "fdiv" and ((A == 0 and B == 0) or (math.isinf(A) and math.isinf(B))))
               or (base == "fsqrt" and B < 0)
               or (fma and ((math.isinf(A) and C == 0) or (A == 0 and math.isinf(C)))))
    if fma and not invalid and (math.isinf(A) or math.isinf(C)) and math.isinf(B):
        p = A * C
        invalid = (p == B) == base.endswith("sub") if not math.isnan(p) else False
    if invalid:
        fl["VX"] = 1
        return DEF_NAN, fl
    if any(math.isinf(x) for x in (A, B, C) if x is not None) or (base == "fdiv" and B == 0):
        if base == "fdiv" and B == 0 and not math.isinf(A):
            fl["ZX"] = 1  # a finite nonzero dividend only: inf / 0 is an exact inf
        r = {"fadd": lambda: A + B, "fsub": lambda: A - B, "fmul": lambda: A * C,
             "fdiv": lambda: math.copysign(math.inf, A) * math.copysign(1, B) if B == 0 else A / B,
             "fsqrt": lambda: math.sqrt(B)}.get(base, lambda: A * C + (-B if base.endswith("sub") else B))()
        return bits(-r if neg else r), fl
    FA, FB, FC = (Fraction(x) if x is not None else None for x in (A, B, C))
    if base == "fsqrt":
        q = None
        r = math.sqrt(B)  # correctly rounded in double; exactness from the square
        if single:
            v, inexact, tiny = round_bin(Fraction(r), 24, -126, 127)
            inexact |= Fraction(r) ** 2 != FB
        else:
            v, inexact, tiny = Fraction(r), Fraction(r) ** 2 != FB, False
    else:
        q = {"fadd": lambda: FA + FB, "fsub": lambda: FA - FB, "fmul": lambda: FA * FC, "fdiv": lambda: FA / FB}.get(
            base, lambda: FA * FC + (-FB if base.endswith("sub") else FB))()
        v, inexact, tiny = round_bin(q, 24, -126, 127) if single else round_bin(q, 53, -1022, 1023)
    if isinstance(v, float):
        fl["OX"] = fl["XX"] = 1
    else:
        fl["XX"] = int(inexact)
        fl["UX"] = int(tiny and inexact)
    if v == 0 and not isinstance(v, float):
        zs = zero_sign(base, A, B, C, q)
        r = -0.0 if zs else 0.0
    else:
        r = float(v)
    return bits(-r if neg else r), fl


def zero_sign(base, A, B, C, q):
    """Sign (1 = minus) of an exact zero result in round to nearest."""
    if q is not None and q != 0:
        return q < 0
    if base == "fadd":
        terms = (A, B)
    elif base == "fsub":
        terms = (A, -B)
    elif base == "fmul":
        terms = (A * C,)
    elif base == "fdiv":
        terms = (math.copysign(1, A) * math.copysign(1, B),)
    elif base == "fsqrt":
        terms = (B,)
    else:
        terms = (A * C, -B if base.endswith("sub") else B)
    if len(terms) == 1:
        return math.copysign(1, terms[0]) < 0
    return all(math.copysign(1, t) < 0 for t in terms)


def cr1(fl):
    """CR1 of an Rc=1 float op: FX FEX VX OX, FPSCR starting 0 with every exception disabled (FEX 0)."""
    fx = int(any(fl.values()))
    return fx << 3 | fl["VX"] << 1 | fl["OX"]


def fcti(b, word, zero):
    """fctiw[z] / fctid[z]: (bits, defined mask)."""
    lo, hi = (-(1 << 31), (1 << 31) - 1) if word else (-(1 << 63), (1 << 63) - 1)
    mask = 0xFFFFFFFF if word else M64
    if is_nan(b):
        return (lo & mask), mask
    x = dbl(b)
    if math.isinf(x):
        n = hi if x > 0 else lo
    else:
        f = Fraction(x)
        n = math.trunc(f) if zero else round(f)  # round(): ties to even
        n = max(lo, min(hi, n))
    return n & mask, mask


def single_ok(b):
    """Is the double b representable in single format (the ISA's condition on single-precision operands)?"""
    return is_nan(b) or frsp(b) == b


def frsp(b):
    if is_nan(b):
        return (b | QBIT) & ~((1 << 29) - 1)
    x = dbl(b)
    if math.isinf(x) or x == 0:
        return b
    v, _, _ = round_bin(Fraction(x), 24, -126, 127)
    return bits(v) if isinstance(v, float) else bits(float(v))


def estimate_ok(op, b, got):
    """Is got (bits) within the ISA's bound of op's exact value? fres 1/256, frsqrte 1/32 (Book I); vrefp, vrsqrtefp
    1/4096 (AltiVec PEM). None when the exact value is a special case (then the result is defined exactly)."""
    x = dbl(b) if op in ("fres", "frsqrte") else struct.unpack(">f", b.to_bytes(4, "big"))[0]
    y = dbl(got) if op in ("fres", "frsqrte") else struct.unpack(">f", got.to_bytes(4, "big"))[0]
    if x == 0 or math.isinf(x) or math.isnan(x) or (x < 0 and "sqrt" in op):
        return None
    exact = 1 / x if op in ("fres", "vrefp") else 1 / math.sqrt(x)
    bound = {"fres": 1 / 256, "frsqrte": 1 / 32, "vrefp": 1 / 4096, "vrsqrtefp": 1 / 4096, "vrsqrtefp128": 1 / 4096,
             "vrefp128": 1 / 4096}[op]
    return abs(y - exact) <= abs(exact) * bound


def vshift(op, va, vb):
    """vsl / vsr: (result words or None when the shift counts differ, the PEM's undefined case)."""
    bs = b"".join(w.to_bytes(4, "big") for w in vb)
    counts = {x & 7 for x in bs}
    if len(counts) != 1:
        return None
    n = counts.pop()
    v = int.from_bytes(b"".join(w.to_bytes(4, "big") for w in va), "big")
    v = (v << n) & ((1 << 128) - 1) if op == "vsl" else v >> n
    return tuple((v >> (96 - 32 * i)) & 0xFFFFFFFF for i in range(4))


def carry(op, a, b, ca_in=0):
    """(CA with 64-bit carry, CA with 32-bit carry) of addc/addic/adde a, b."""
    s64 = a + b + (ca_in if op == "adde" else 0)
    s32 = (a & 0xFFFFFFFF) + (b & 0xFFFFFFFF) + (ca_in if op == "adde" else 0)
    return int(s64 > M64), int(s32 > 0xFFFFFFFF)


FPA = {"fadd", "fsub", "fmul", "fdiv", "fsqrt", "fmadd", "fmsub", "fnmadd", "fnmsub"}
FPA |= {o + "s" for o in FPA}
LOADS = {"lbz": (1, 0), "lhz": (2, 0), "lha": (2, 1), "lwz": (4, 0), "lwa": (4, 1), "ld": (8, 0)}


def load_rule(op, f, regs, mem_in):
    """(destination register, value) of a load from the case's memory, or None for an op without a rule here."""
    vec = {"lvx": "lvx", "lvxl": "lvx", "lvx128": "lvx", "lvxl128": "lvx", "lwarx": "lwz", "ldarx": "ld"}
    base = vec.get(op) or (op[:-1] if op.endswith("x") and op not in ("lhbrx", "lwbrx") else op)
    base = base[:-1] if base.endswith("u") and base not in ("lhbr",) else base
    sizes = {"lbz": 1, "lhz": 2, "lha": 2, "lwz": 4, "lwa": 4, "ld": 8, "lhbrx": 2, "lwbrx": 4, "lfs": 4, "lfd": 8,
             "lvx": 16}
    if base not in sizes:
        return None
    ra = regs.get(f"r{f['ra']}", 0) if f.get("ra") else 0
    ea = (ra + (regs.get(f"r{f['rb']}", 0) if "rb" in f else f.get("d", 0))) & 0xFFFFFFFF
    if base == "lvx":
        ea &= ~15
    mem = {a + i: x for a, data in mem_in for i, x in enumerate(data)}
    raw = bytes(mem.get(ea + i, 0) for i in range(sizes[base]))
    if base == "lvx":
        return f"v{f['vd']}", tuple(int.from_bytes(raw[i:i + 4], "big") for i in range(0, 16, 4))
    v = int.from_bytes(raw[::-1] if base.endswith("brx") else raw, "big")
    if base in ("lha", "lwa") and v >> (8 * len(raw) - 1):
        v = (v - (1 << (8 * len(raw)))) & M64
    if base == "lfs":
        return f"f{f['rd']}", bits(struct.unpack(">f", raw)[0]) if not is_nan(v << 29) else v << 29
    return (f"f{f['rd']}" if base == "lfd" else f"r{f['rd']}"), v


def fp_expect(op, f, fin):
    """(destination, result bits, flags) of a scalar arithmetic op by the rule, from fin(register) -> input bits; None
    when a single-precision op has an input not representable in single (undefined)."""
    a, b, cc = (fin(f"f{f[k]}") if k in f else None for k in ("fra", "frb", "frc"))
    base = op[:-1] if op.endswith("s") else op
    if base in ("fadd", "fsub", "fdiv"):
        cc = None
    elif base == "fmul":
        b = None
    elif base == "fsqrt":
        a = cc = None
    if op.endswith("s") and any(x is not None and not single_ok(x) for x in (a, b, cc)):
        return None
    r, fl = fp_arith(op, a, b, cc)
    return f"f{f['frd']}", r, fl


def model_check(c, words, decode):
    """The rule against a case where A and the corpus agree (a check of this model): True / False, None if no rule."""
    ins = [decode(w) for w in words]
    if len(ins) != 2 or ins[0].op not in FPA:
        return None
    e = fp_expect(ins[0].op, ins[0].f, lambda k: c["reg_in"].get(k, 0))
    if e is None:
        return None
    exp = {k: v for k, v, _ in c["reg_out"]}
    return exp.get(e[0]) == e[1] and ("cr" not in exp or exp["cr"] == cr1(e[2]) << 24)


def judge(rule, corpus, ours):
    """Verdict of one differing value against the rule (None: no rule)."""
    if rule is None:
        return "undetermined"
    c, o = corpus == rule, ours == rule
    return "both" if c and o else "corpus" if c else "ours" if o else "neither"


def settle(c, rec, decode):
    """(kind, verdict, note) of one differing case. c: the corpus case (vote_corpus), rec: leg A's record (outs as
    hex strings), decode: the decoder (word -> instruction with .op and .f)."""
    ins = [decode(int(w, 16)) for w in rec["words"]]
    ops = [i.op for i in ins]
    exp = {k: v for k, v, _ in c["reg_out"]}
    ours = {k: (tuple(int(x, 16) for x in v) if isinstance(v, list) else int(v, 16) if v else None)
            for k, v in rec.get("outs", {}).items()}
    diff = [d[0] for d in rec.get("diffs", [])]
    fin = lambda k: c["reg_in"].get(k, 0)  # noqa: E731
    main = next((i for i in ins if i.op not in ("bclr", "mfcr", "addi", "addis", "ori", "oris", "rldicr", "rldicl",
                                                "rlwinm")), ins[0])
    op, f = main.op, main.f
    if c.get("bracket_mem"):
        regs = dict(c["reg_in"])
        for i in ins:
            if i is main:
                break
            if i.op == "addi":
                regs[f"r{i.f['rd']}"] = ((regs.get(f"r{i.f['ra']}", 0) if i.f["ra"] else 0) + i.f["simm"]) & M64
        r = load_rule(op, main.f, regs, c["mem_in"])
        if r is None:
            return "bracketed memory (" + op + ")", "undetermined", "Xenia reads [..] bytes as text; no rule for the op"
        d, v = r
        return "load, bracketed memory", judge(v, exp.get(d), ours.get(d)), "Xenia reads [..] bytes as text"
    if diff == ["cr"] and op.startswith("f") and op not in FPA:
        return f"{op}. CR1 (FPSCR flags)", "undetermined", "the translator models no FPSCR; flags not modelled here"
    if op in ("fcmpu", "fcmpo"):
        x, y = dbl(fin(f"f{f['fra']}")), dbl(fin(f"f{f['frb']}"))
        nib = 1 if math.isnan(x) or math.isnan(y) else 8 if x < y else 4 if x > y else 2
        r = next((k for k in diff if k.startswith("r")), None)
        sh = 28 - 4 * f["crfd"]
        return op, judge(nib << sh, (exp.get(r) or 0) & (15 << sh), (ours.get(r) or 0) & (15 << sh)), ""
    if op in FPA:
        e = fp_expect(op, f, fin)
        if e is None:
            return f"{op} non-single input", "both", "an input not representable in single: the result is undefined"
        d, r, fl = e
        part = "cr" if diff == ["cr"] else d[0]
        if part == "cr":
            return f"{op}. CR1 (FPSCR flags)", judge(cr1(fl) << 24, exp["cr"], ours.get("cr")), \
                "the translator models no FPSCR, so CR1 stays 0"
        kind = "NaN choice" if is_nan(r) or is_nan(exp.get(d, 0)) else "value"
        return f"{op} {kind}", judge(r, exp.get(d), ours.get(d)), ""
    if op in ("fctiw", "fctiwz", "fctid", "fctidz"):
        r, m = fcti(fin(f"f{f['frb']}"), op.startswith("fctiw"), op.endswith("z"))
        d = f"f{f['frd']}"
        v = judge(r, exp.get(d, 0) & m, (ours.get(d) or 0) & m)
        return op, v, "fctiw's upper word is undefined" if m != M64 else ""
    if op == "frsp":
        d = f"f{f['frd']}"
        return op, judge(frsp(fin(f"f{f['frb']}")), exp.get(d), ours.get(d)), ""
    if op in ("fres", "frsqrte"):
        d = f"f{f['frd']}"
        x = fin(f"f{f['frb']}")
        okc, oko = estimate_ok(op, x, exp.get(d, 0)), estimate_ok(op, x, ours.get(d) or 0)
        if okc is None:
            return op + " special input", "undetermined", "special inputs: exact results not modelled here"
        return op + " estimate", {(1, 1): "both", (1, 0): "corpus", (0, 1): "ours", (0, 0): "neither"}[(okc, oko)], \
            "an estimate: any value within the ISA's bound is legal"
    if op in ("vrefp", "vrsqrtefp", "vrefp128", "vrsqrtefp128"):
        d = f"v{f['vd']}"
        src = c["reg_in"].get(f"v{f['vb']}", (0, 0, 0, 0))
        lanes = []
        for x, e, o in zip(src, exp.get(d, (0,) * 4), ours.get(d) or (0,) * 4):
            if e == o:
                continue
            ok = estimate_ok(op, x, e), estimate_ok(op, x, o)
            lanes.append("undetermined" if None in ok else {(1, 1): "both", (1, 0): "corpus", (0, 1): "ours",
                                                             (0, 0): "neither"}[ok])
        v = lanes[0] if lanes and len(set(lanes)) == 1 else "undetermined"
        return op + " estimate", v, "an estimate: any lane within 1/4096 is legal (AltiVec PEM)"
    if op in ("vlogefp", "vexptefp", "vlogefp128", "vexptefp128"):
        return op + " estimate", "undetermined", "precision bound not checked here"
    if op in ("vsl", "vsr"):
        d = f"v{f['vd']}"
        r = vshift(op, c["reg_in"].get(f"v{f['va']}", (0,) * 4), c["reg_in"].get(f"v{f['vb']}", (0,) * 4))
        if r is None:
            return op + " unequal shift counts", "both", "the PEM leaves the result undefined"
        return op, judge(r, exp.get(d), ours.get(d)), ""
    if op in ("mulhw", "mulhwu", "divw", "divwu"):
        d = f"r{f['rd']}"
        lo = lambda v: (v or 0) & 0xFFFFFFFF  # noqa: E731
        return op + " upper word", "both" if lo(exp.get(d)) == lo(ours.get(d)) else "undetermined", \
            "the ISA leaves RT[0:31] undefined"
    if op in ("addc", "addic", "subfc", "subfic", "adde", "addze", "addme", "subfe") or "adde" in ops:
        first = next((i for i in ins if i.op in ("addc", "addic")), None)
        if first and all(i.op in ("addc", "addic", "adde", "mfcr", "bclr") for i in ins):
            a = fin(f"r{first.f['ra']}")
            b = fin(f"r{first.f['rb']}") if first.op == "addc" else first.f["simm"] & M64
            c64, c32 = carry(first.op, a, b)
            r = next((k for k in diff if k.startswith("r")), None)
            return "XER[CA] width", judge(c32, exp.get(r), ours.get(r)), \
                f"carry out of 32 bits {c32} (32-bit mode), of 64 bits {c64}"
        return "XER[CA] width", "undetermined", "same carry-width question, inputs made in code"
    if op in ("lvebx", "lvehx", "lvewx", "lvewx128"):
        return op + " other elements", "both", "the PEM leaves the other elements undefined"
    if op == "mffs" or "mffs" in ops:
        d = next(f"f{i.f['frd']}" for i in ins if i.op == "mffs")
        lo = lambda v: (v or 0) & 0xFFFFFFFF  # noqa: E731
        return "mffs upper word", "both" if lo(exp.get(d)) == lo(ours.get(d)) else "undetermined", \
            "FPSCR is the low word; the upper word is undefined"
    if op in ("mfmsr", "mftb", "mfvscr", "mfspr"):
        return op + " machine state", "undetermined", "depends on the machine's state, not on the case's inputs"
    if op in ("stwcx", "stdcx"):
        return op + " without reservation", "corpus", "no reservation: the store is not done and CR0.EQ is 0 (ISA)"
    return op, "undetermined", "no rule written for it"
