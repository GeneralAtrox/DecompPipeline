"""Typed C expressions for the translator: built by the instruction lifter, rewritten by the dataflow passes
(constant and copy propagation, substitution), and printed as C that compiles cleanly at /W4 /WX.

Types: u8 u16 u32 u64 s8 s16 s32 s64 f32 f64, `bool` (a C comparison: int 0/1), `flag` (a u32 variable that
only ever holds 0 or 1: a CR bit, XER[CA]/[SO]/[OV]) and `xv` (a 128-bit vector register: the struct the emitted C
declares). General registers are u64 (Xenon runs titles in 64-bit mode); a 32-bit operation casts to u32 or s32 and
widens its result back as the architecture does. Every node is immutable; the smart constructors fold constants and
drop identities.
"""
from __future__ import annotations

import struct

INT_BITS = {"u8": 8, "u16": 16, "u32": 32, "u64": 64, "s8": 8, "s16": 16, "s32": 32, "s64": 64,
            "bool": 32, "flag": 32}
SIGNED = {"s8", "s16", "s32", "s64"}
FLOAT = {"f32", "f64"}
TRUTH = {"bool", "flag"}


def is_float(t):
    return t in FLOAT


def wrap(v, t):
    """v as a value of integer type t."""
    n = INT_BITS[t]
    v &= (1 << n) - 1
    if t in SIGNED and v >> (n - 1):
        v -= 1 << n
    return v


def f32round(x):
    try:
        return struct.unpack("<f", struct.pack("<f", x))[0]
    except OverflowError:
        return float("inf") if x > 0 else float("-inf")


class Expr:
    __slots__ = ("ty",)
    prec = 16

    def vars(self):
        out = set()
        self._vars(out)
        return out

    def _vars(self, out):
        pass

    def has_load(self):
        return False

    def size(self):
        return 1

    def map(self, fn):
        """A copy with fn applied bottom-up (fn(node) -> node)."""
        return fn(self)


class Var(Expr):
    __slots__ = ("name",)

    def __init__(self, name, ty):
        self.name, self.ty = name, ty

    def _vars(self, out):
        out.add(self.name)

    def __eq__(self, o):
        return isinstance(o, Var) and o.name == self.name

    def __hash__(self):
        return hash(("v", self.name))

    def __repr__(self):
        return self.name


class Const(Expr):
    __slots__ = ("v",)

    def __init__(self, v, ty):
        self.ty = ty
        self.v = float(v) if ty in FLOAT else wrap(int(v), ty)

    def __eq__(self, o):
        return isinstance(o, Const) and o.ty == self.ty and (
            o.v == self.v if self.ty not in FLOAT else struct.pack("<d", o.v) == struct.pack("<d", self.v))

    def __hash__(self):
        return hash(("c", self.ty, self.v))

    def __repr__(self):
        return f"{self.v}:{self.ty}"


class Un(Expr):
    __slots__ = ("op", "a")
    prec = 15

    def __init__(self, op, a, ty):
        self.op, self.a, self.ty = op, a, ty

    def _vars(self, out):
        self.a._vars(out)

    def has_load(self):
        return self.a.has_load()

    def size(self):
        return 1 + self.a.size()

    def map(self, fn):
        return fn(Un(self.op, self.a.map(fn), self.ty))

    def __eq__(self, o):
        return isinstance(o, Un) and (o.op, o.ty) == (self.op, self.ty) and o.a == self.a

    def __hash__(self):
        return hash(("u", self.op, self.a))


class Cast(Expr):
    __slots__ = ("a",)
    prec = 15

    def __init__(self, ty, a):
        self.ty, self.a = ty, a

    def _vars(self, out):
        self.a._vars(out)

    def has_load(self):
        return self.a.has_load()

    def size(self):
        return 1 + self.a.size()

    def map(self, fn):
        return fn(cast(self.ty, self.a.map(fn)))

    def __eq__(self, o):
        return isinstance(o, Cast) and o.ty == self.ty and o.a == self.a

    def __hash__(self):
        return hash(("k", self.ty, self.a))


PREC = {"*": 13, "/": 13, "%": 13, "+": 12, "-": 12, "<<": 11, ">>": 11, "<": 10, "<=": 10, ">": 10, ">=": 10,
        "==": 9, "!=": 9, "&": 8, "^": 7, "|": 6, "&&": 5, "||": 4}
CMP = {"<", "<=", ">", ">=", "==", "!="}
ASSOC = {"+", "*", "&", "|", "^", "&&", "||"}


class Bin(Expr):
    __slots__ = ("op", "a", "b")

    def __init__(self, op, a, b, ty):
        self.op, self.a, self.b, self.ty = op, a, b, ty

    @property
    def prec(self):
        return PREC[self.op]

    def _vars(self, out):
        self.a._vars(out)
        self.b._vars(out)

    def has_load(self):
        return self.a.has_load() or self.b.has_load()

    def size(self):
        return 1 + self.a.size() + self.b.size()

    def map(self, fn):
        return fn(binop(self.op, self.a.map(fn), self.b.map(fn), self.ty))

    def __eq__(self, o):
        return isinstance(o, Bin) and (o.op, o.ty) == (self.op, self.ty) and o.a == self.a and o.b == self.b

    def __hash__(self):
        return hash(("b", self.op, self.a, self.b))


class Call(Expr):
    """A runtime or helper function call. load: it reads guest memory (rd8..rdf64)."""
    __slots__ = ("fn", "args", "load")

    def __init__(self, fn, args, ty, load=False):
        self.fn, self.args, self.ty, self.load = fn, tuple(args), ty, load

    def _vars(self, out):
        for a in self.args:
            a._vars(out)

    def has_load(self):
        return self.load or any(a.has_load() for a in self.args)

    def size(self):
        return 1 + sum(a.size() for a in self.args)

    def map(self, fn):
        return fn(Call(self.fn, [a.map(fn) for a in self.args], self.ty, self.load))

    def __eq__(self, o):
        return isinstance(o, Call) and o.fn == self.fn and o.args == self.args

    def __hash__(self):
        return hash(("f", self.fn, self.args))


class Cond(Expr):
    __slots__ = ("c", "a", "b")
    prec = 3

    def __init__(self, c, a, b, ty):
        self.c, self.a, self.b, self.ty = c, a, b, ty

    def _vars(self, out):
        self.c._vars(out)
        self.a._vars(out)
        self.b._vars(out)

    def has_load(self):
        return self.c.has_load() or self.a.has_load() or self.b.has_load()

    def size(self):
        return 1 + self.c.size() + self.a.size() + self.b.size()

    def map(self, fn):
        return fn(cond(self.c.map(fn), self.a.map(fn), self.b.map(fn)))

    def __eq__(self, o):
        return isinstance(o, Cond) and o.c == self.c and o.a == self.a and o.b == self.b

    def __hash__(self):
        return hash(("?", self.c, self.a, self.b))


LOADS = {"rd8": "u8", "rd16": "u16", "rd32": "u32", "rd64": "u64", "rdf32": "f32", "rdf64": "f64"}


def load(fn, addr):
    return Call(fn, [addr], LOADS[fn], load=True)


# ------------------------------------------------------------------------------------------- smart constructors
def u(v):
    return Const(v, "u32")


def q(v):
    """A 64-bit register constant (v sign- or zero-extended by the caller: negative v wraps to 64 bits)."""
    return Const(v, "u64")


def s(v):
    return Const(v, "s32")


ZERO = Const(0, "u32")
ONE = Const(1, "u32")
FZERO = Const(0.0, "f64")


def cast(t, a):
    if a.ty == t:
        return a
    if isinstance(a, Const):
        if t in FLOAT:
            return Const(a.v if a.ty in FLOAT else a.v, t) if t == "f64" else Const(f32round(float(a.v)), t)
        if a.ty in FLOAT:
            return Cast(t, a)
        return Const(a.v, t)
    plain = set(INT_BITS) - TRUTH
    if t in plain and a.ty in plain and isinstance(a, Cast) and a.a.ty in plain:
        # (t)(m)x is (t)x when the middle cast keeps every bit t takes (m at least as wide as t), or keeps x's
        # value exactly (m wider than x)
        m, i = INT_BITS[a.ty], INT_BITS[a.a.ty]
        if m >= INT_BITS[t] or m > i:
            return cast(t, a.a)
    if t in ("u32", "s32") and a.ty in TRUTH:
        return Cast(t, a) if t == "s32" or a.ty == "bool" else a
    if t == "flag" and a.ty == "bool":
        return a
    return Cast(t, a)


def to_u32(a):
    return cast("u32", a)


def to_s32(a):
    return cast("s32", a)


def _int(a):
    return isinstance(a, Const) and a.ty not in FLOAT


def binop(op, a, b, ty):
    """Fold and normalise a binary operation."""
    if op in CMP:
        return compare(op, a, b)
    if op in ("&&", "||"):
        return logic(op, a, b)
    if ty in FLOAT:
        if isinstance(a, Const) and isinstance(b, Const):
            try:
                v = {"+": a.v + b.v, "-": a.v - b.v, "*": a.v * b.v}.get(op)
            except ArithmeticError:
                v = None
            if v is not None:
                return Const(v, ty)
        return Bin(op, a, b, ty)
    if _int(a) and _int(b):
        x, y = a.v, b.v
        n = INT_BITS[ty]
        v = None
        if op == "+":
            v = x + y
        elif op == "-":
            v = x - y
        elif op == "*":
            v = x * y
        elif op == "&":
            v = x & y
        elif op == "|":
            v = x | y
        elif op == "^":
            v = x ^ y
        elif op == "<<" and 0 <= y < n:
            v = x << y
        elif op == ">>" and 0 <= y < n:
            v = (x if ty in SIGNED else x & ((1 << n) - 1)) >> y
        elif op in ("/", "%") and y != 0 and ty not in SIGNED:
            v = (x // y) if op == "/" else (x % y)
        if v is not None:
            return Const(v, ty)
    if _int(b):
        y = b.v & ((1 << INT_BITS[ty]) - 1)
        full = (1 << INT_BITS[ty]) - 1
        if op in ("+", "-", "|", "^", "<<", ">>") and y == 0:
            return a
        if op == "&" and y == full:
            return a
        if op == "&" and y == 0:
            return Const(0, ty)
        if op == "*" and y == 1:
            return a
        if op == "+" and isinstance(a, Bin) and a.op in ("+", "-") and _int(a.b) and a.ty == ty:
            return binop("+", a.a, Const(a.b.v + y if a.op == "+" else -a.b.v + y, ty), ty)
        if op == "-":
            return binop("+", a, Const(-y, ty), ty)
        if op == "&" and isinstance(a, Bin) and a.op == "&" and _int(a.b):
            return binop("&", a.a, Const(a.b.v & y, ty), ty)
        if op in ("|", "^") and isinstance(a, Bin) and a.op == op and _int(a.b):
            return binop(op, a.a, Const(a.b.v | y if op == "|" else a.b.v ^ y, ty), ty)
    if _int(a) and op in ("+", "*", "&", "|", "^"):
        return binop(op, b, a, ty)
    if _int(a) and a.v == 0 and op in ("<<", ">>"):
        return a
    return Bin(op, a, b, ty)


def ity(a, b):
    """The unsigned type of an integer operation on a and b: u64 if either is 64-bit, else u32."""
    return "u64" if INT_BITS.get(a.ty) == 64 or INT_BITS.get(b.ty) == 64 else "u32"


def _w(a, ty):
    """a widened to ty for an operation of that type (a 32-bit constant keeps its value; a signed one extends)."""
    if ty == "u64" and INT_BITS.get(a.ty) != 64 and isinstance(a, Const):
        return Const(a.v, "u64")
    return a


def _bin(op, a, b):
    t = ity(a, b)
    return binop(op, _w(a, t), _w(b, t), t)


def add(a, b):
    return _bin("+", a, b)


def sub(a, b):
    return _bin("-", a, b)


def band(a, b):
    return _bin("&", a, b)


def bor(a, b):
    return _bin("|", a, b)


def bxor(a, b):
    return _bin("^", a, b)


def shl(a, n):
    return binop("<<", a, n if isinstance(n, Expr) else u(n), "u32" if a.ty in TRUTH else a.ty)


def shr(a, n):
    return binop(">>", a, n if isinstance(n, Expr) else u(n), "u32" if a.ty in TRUTH else a.ty)


def bnot(a):
    if _int(a):
        return Const(~a.v, a.ty)
    if isinstance(a, Un) and a.op == "~":
        return a.a
    return Un("~", a, a.ty)


def neg(a):
    if _int(a):
        return Const(-a.v, a.ty)
    if a.ty in FLOAT:
        return Un("-", a, a.ty)
    return binop("-", Const(0, a.ty), a, a.ty)


def fneg(a):
    if isinstance(a, Const):
        return Const(-a.v, a.ty)
    if isinstance(a, Un) and a.op == "-":
        return a.a
    return Un("-", a, a.ty)


INVERT = {"<": ">=", "<=": ">", ">": "<=", ">=": "<", "==": "!=", "!=": "=="}
SWAP = {"<": ">", "<=": ">=", ">": "<", ">=": "<=", "==": "==", "!=": "!="}


def compare(op, a, b):
    if a.ty in FLOAT or b.ty in FLOAT:
        if isinstance(a, Const) and isinstance(b, Const):
            x, y = a.v, b.v
            return Const(int({"<": x < y, "<=": x <= y, ">": x > y, ">=": x >= y, "==": x == y,
                              "!=": x != y}[op]), "bool")
        return Bin(op, a, b, "bool")
    if _int(a) and _int(b):
        x, y = a.v, b.v
        return Const(int({"<": x < y, "<=": x <= y, ">": x > y, ">=": x >= y, "==": x == y, "!=": x != y}[op]),
                     "bool")
    if _int(a) and not _int(b):
        return compare(SWAP[op], b, a)
    if op in ("==", "!=") and a.ty in TRUTH and _int(b) and b.v in (0, 1):
        # flag == 0 -> !flag, flag != 0 -> flag
        return a if (op == "!=") == (b.v == 0) else lnot(a)
    if a.ty in TRUTH and b.ty not in TRUTH:
        a = cast("u32", a)
    return Bin(op, a, b, "bool")


def lnot(a):
    if _int(a):
        return Const(int(a.v == 0), "bool")
    if isinstance(a, Un) and a.op == "!" and a.a.ty in TRUTH:
        return a.a
    if isinstance(a, Bin) and a.op in INVERT and not (a.a.ty in FLOAT or a.b.ty in FLOAT):
        return Bin(INVERT[a.op], a.a, a.b, "bool")
    if isinstance(a, Bin) and a.op in ("==", "!=") and (a.a.ty in FLOAT or a.b.ty in FLOAT):
        return Bin(INVERT[a.op], a.a, a.b, "bool")      # == and != invert exactly, NaN included
    if a.ty in TRUTH:
        return Un("!", a, "bool")
    return Bin("==", a, Const(0, a.ty), "bool")


def truth(a):
    """a as a C condition (nonzero = true)."""
    return a


def logic(op, a, b):
    if _int(a):
        if op == "&&":
            return truthy(b) if a.v else Const(0, "bool")
        return Const(1, "bool") if a.v else truthy(b)
    if _int(b):
        if op == "&&":
            return truthy(a) if b.v else Const(0, "bool")
        return Const(1, "bool") if b.v else truthy(a)
    return Bin(op, a, b, "bool")


def truthy(a):
    if a.ty in TRUTH:
        return a
    return compare("!=", a, Const(0, a.ty))


def flag_and(a, b):
    return logic("&&", truthy(a), truthy(b))


def flag_or(a, b):
    return logic("||", truthy(a), truthy(b))


def flag_xor(a, b):
    if _int(a) and _int(b):
        return Const(int(bool(a.v) != bool(b.v)), "bool")
    if _int(b):
        return truthy(a) if not b.v else lnot(a)
    if _int(a):
        return flag_xor(b, a)
    return Bin("!=", cast("u32", truthy(a)) if a.ty == "bool" else a,
               cast("u32", truthy(b)) if b.ty == "bool" else b, "bool")


def cond(c, a, b):
    if _int(c):
        return a if c.v else b
    if a == b:
        return a
    return Cond(c, a, b, a.ty)


def call(fn, args, ty):
    return Call(fn, args, ty)


def fround(a):
    """(f64)(f32)a: a single-precision result in a double register."""
    if isinstance(a, Const):
        return Const(f32round(a.v), "f64")
    if isinstance(a, Cast) and a.ty == "f64" and a.a.ty == "f32":
        return a
    return Cast("f64", Cast("f32", a))


# ------------------------------------------------------------------------------------------------------- render
# Diagnostic only (tools/dp_judge.py --oracle-flush), never the translator's output: round to single as the judge's
# p-code oracle does, which gives 0 for a value below the smallest single subnormal where IEEE rounding (and the
# 750CL) gives that subnormal. A failure that this makes pass is the oracle's, not the translation's.
ORACLE_FLUSH = False


def lit(v, ty):
    if ty in FLOAT:
        if v != v or v in (float("inf"), float("-inf")) or (v != 0 and abs(v) < (1.1754943508222875e-38 if
                                                                                ty == "f32" else 2.2250738585072014e-308)):
            # no C literal for these (and MSVC's reading of subnormal literals is not to be trusted): the bits
            if ty == "f32":
                return f"px_w2f(0x{struct.unpack('<I', struct.pack('<f', v))[0]:08x}u)"
            return f"px_b2f(0x{struct.unpack('<Q', struct.pack('<d', v))[0]:016x}ull)"
        r = repr(float(v))
        if "e" not in r and "." not in r:
            r += ".0"
        return r + ("f" if ty == "f32" else "")
    if ty in ("u64",):
        v &= 0xFFFFFFFFFFFFFFFF
        return f"{v}ull" if v < 10 else f"0x{v:x}ull"
    if ty == "s64":
        return f"{v}ll"
    if ty in SIGNED or ty == "bool":
        if ty == "s32" and v == -0x80000000:
            return "(-2147483647 - 1)"
        return str(v)
    v &= (1 << INT_BITS[ty]) - 1
    return f"{v}u" if v < 10 else f"0x{v:x}u"


def render(e: Expr, parent_prec=0, side="l", parent_op=None) -> str:
    t = _render(e)
    p = e.prec
    if isinstance(e, Const) and e.ty not in FLOAT and e.v < 0 and parent_prec >= 15:
        return f"({t})"
    if isinstance(e, Const) and e.ty in FLOAT and e.v < 0 and parent_prec >= 15:
        return f"({t})"
    if isinstance(e, (Bin, Cond)):
        if parent_prec == 0:
            return t
        if isinstance(e, Bin) and parent_op is not None and side == "l" and (
                e.op == parent_op and e.op in ASSOC or (e.op in ("+", "-") and parent_op in ("+", "-"))):
            return t
        return f"({t})"
    return t


def _render(e: Expr) -> str:
    if isinstance(e, Var):
        return e.name
    if isinstance(e, Const):
        return lit(e.v, e.ty)
    if isinstance(e, Un):
        inner = render(e.a, 15)
        if e.op == "-" and inner.startswith("-"):
            inner = f"({inner})"
        return f"{e.op}{inner}"
    if isinstance(e, Cast):
        if ORACLE_FLUSH and e.ty == "f64" and isinstance(e.a, Cast) and e.a.ty == "f32" and e.a.a.ty == "f64":
            return f"px_fr({render(e.a.a)})"
        ty = "u32" if e.ty in ("flag",) else "s32" if e.ty == "bool" else e.ty
        return f"({ty}){render(e.a, 15)}"
    if isinstance(e, Call):
        if ORACLE_FLUSH and e.fn in ("ppc_fmadds", "ppc_fmsubs", "ppc_fnmadds", "ppc_fnmsubs"):
            # tc_rt.h: (f64)(f32)((a * c) +/- b), each step in double; the fnm forms negate the rounded result
            x, c, y = (render(v, 13) for v in e.args)
            s = f"px_fr({x} * {c} {'+' if 'madd' in e.fn else '-'} {render(e.args[2], 13)})"
            return f"-{s}" if "fnm" in e.fn else s
        return f"{e.fn}({', '.join(render(a) for a in e.args)})"
    if isinstance(e, Cond):
        return f"{render(e.c, 4)} ? {render(e.a, 4)} : {render(e.b, 4)}"
    if isinstance(e, Bin):
        op, a, b = e.op, e.a, e.b
        if op == "+" and _int(b) and e.ty in ("u32", "u64", "u16", "u8") and b.v >> (INT_BITS[e.ty] - 1):
            op, b = "-", Const(-b.v, e.ty)          # x + 0xfffffff0u -> x - 0x10u (the same u32 sum)
        if op == "+" and _int(b) and e.ty in SIGNED and b.v < 0:
            op, b = "-", Const(-b.v, e.ty)
        return f"{render(a, PREC[op], 'l', op)} {op} {render(b, PREC[op], 'r', op)}"
    raise TypeError(e)
