"""The translator's intermediate form: a function is a list of blocks; a block is a list of statements and one
terminator. Statements and terminators know what they read and write, so the passes (passes.py) can run
dataflow over them without knowing PowerPC."""
from __future__ import annotations

from .expr import Expr, Var


class Assign:
    __slots__ = ("dst", "e")

    def __init__(self, dst, e: Expr):
        self.dst, self.e = dst, e

    def reads(self):
        return self.e.vars()

    def writes(self):
        return {self.dst}

    def exprs(self):
        return [self.e]

    def set_exprs(self, es):
        self.e = es[0]

    def __repr__(self):
        return f"{self.dst} = {self.e!r}"


class Store:
    """wr8/wr16/wr32/wr64/wrf32d/wrf64(addr, val). removable: a stack-frame store nothing reads (passes.stack)."""
    __slots__ = ("fn", "addr", "val")

    def __init__(self, fn, addr, val):
        self.fn, self.addr, self.val = fn, addr, val

    def reads(self):
        return self.addr.vars() | self.val.vars()

    def writes(self):
        return set()

    def exprs(self):
        return [self.addr, self.val]

    def set_exprs(self, es):
        self.addr, self.val = es

    def size(self):
        return {"wr8": 1, "wr16": 2, "wr32": 4, "wr64": 8, "wrf32d": 4, "wrf64": 8}[self.fn]


class CallS:
    """A call to another original function: TcArgs fields from args [(field, index, expr)], then the callee's
    result in r3 (tc_call) or f1 (tc_callf). kind: 'r' / 'f', or None until passes.choose_calls decides from what
    is read after the call. auto: the argument registers are chosen by passes.fill_auto_args."""
    __slots__ = ("target", "args", "kind", "auto", "addr", "note", "selfc")

    def __init__(self, target: Expr, args, addr, auto=False, kind=None):
        self.target, self.args, self.addr, self.auto, self.kind = target, list(args), addr, auto, kind
        self.note = None
        self.selfc = False                    # a call of its own entry: px_rec(params..., sp_), args ("p", name, e)

    def reads(self):
        out = self.target.vars()
        for _, _, e in self.args:
            out |= e.vars()
        return out

    def writes(self):
        if self.note == "both":
            return {"r3", "f1"}
        if self.kind == "r":
            return {"r3"}
        if self.kind == "f":
            return {"f1"}
        return {"r3", "f1"}

    def exprs(self):
        return [self.target] + [e for _, _, e in self.args]

    def set_exprs(self, es):
        self.target = es[0]
        self.args = [(f, k, e) for (f, k, _), e in zip(self.args, es[1:])]


class Comment:
    __slots__ = ("text",)

    def __init__(self, text):
        self.text = text

    def reads(self):
        return set()

    def writes(self):
        return set()

    def exprs(self):
        return []

    def set_exprs(self, es):
        pass


# ---------------------------------------------------------------------------------------------------- terminators
class Goto:
    __slots__ = ("label",)

    def __init__(self, label):
        self.label = label

    def succs(self):
        return [self.label]

    def reads(self):
        return set()

    def exprs(self):
        return []

    def set_exprs(self, es):
        pass


class If:
    """if (cond) goto yes; else goto no."""
    __slots__ = ("cond", "yes", "no")

    def __init__(self, cond, yes, no):
        self.cond, self.yes, self.no = cond, yes, no

    def succs(self):
        return [self.yes, self.no]

    def reads(self):
        return self.cond.vars()

    def exprs(self):
        return [self.cond]

    def set_exprs(self, es):
        self.cond = es[0]


class Switch:
    """A jump table: switch on the loaded target address. default: statements (a tail call) ending in a Ret,
    run when the value is none of the cases (it names the value as g_)."""
    __slots__ = ("e", "cases", "default")

    def __init__(self, e, cases, default):
        self.e, self.cases, self.default = e, cases, default

    def succs(self):
        out = []
        for _, lab in self.cases:
            if lab not in out:
                out.append(lab)
        return out

    def reads(self):
        out = self.e.vars()
        for st in self.default:
            out |= st.reads()
        return out - {"g_"}

    def exprs(self):
        return [self.e]

    def set_exprs(self, es):
        self.e = es[0]


class Ret:
    __slots__ = ("e",)

    def __init__(self, e=None):
        self.e = e

    def writes(self):
        return set()

    def succs(self):
        return []

    def reads(self):
        return self.e.vars() if self.e is not None else set()

    def exprs(self):
        return [self.e] if self.e is not None else []

    def set_exprs(self, es):
        if es:
            self.e = es[0]


class Block:
    __slots__ = ("label", "addr", "stmts", "term")

    def __init__(self, label, addr=None):
        self.label, self.addr, self.stmts, self.term = label, addr, [], None

    def __repr__(self):
        return f"<{self.label} {len(self.stmts)} {type(self.term).__name__}>"


def V(name, ty="u32"):
    return Var(name, ty)
