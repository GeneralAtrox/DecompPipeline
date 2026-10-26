"""One variable per value: a register the raw C reuses for unrelated values becomes one variable per def-use web.

Reaching definitions over the tree (forward, loops to a fixpoint, break / continue / return followed): every read
of v is joined with the assignments that reach it, and assignments reaching a common read are one web. The web
that the entry value reaches (a read no assignment may precede) keeps the name; the others get new names. Nothing
changes what the function does: each read still sees exactly the assignments it saw.
"""
from __future__ import annotations

from .cast import (Assign, Bin, Block, Break, Call, Case, Cast, Cond, Continue, DoWhile, ExprS, For, If, Index, Member,
                   Num, Raw, Return, Switch, Unary, Var, While)
from .flow import Unsupported, exprs, walk


def clone(e):
    if isinstance(e, Var):
        return Var(e.name)
    if isinstance(e, (Num, Raw)) or e is None:
        return e
    if isinstance(e, Call):
        return Call(e.fn if isinstance(e.fn, str) else clone(e.fn), [clone(a) for a in e.args])
    if isinstance(e, Unary):
        return Unary(e.op, clone(e.e))
    if isinstance(e, Cast):
        return Cast(e.type, clone(e.e))
    if isinstance(e, Bin):
        return Bin(e.op, clone(e.a), clone(e.b))
    if isinstance(e, Cond):
        return Cond(clone(e.c), clone(e.a), clone(e.b))
    if isinstance(e, Index):
        return Index(clone(e.a), clone(e.i))
    if isinstance(e, Member):
        return Member(clone(e.e), e.name, e.arrow)
    raise TypeError(e)


def _join(*states):
    out = None
    for s in states:
        if s is None:
            continue
        if out is None:
            out = dict(s)
            continue
        for k, v in s.items():
            out[k] = out.get(k, frozenset()) | v
    return out


class _Webs:
    def __init__(self, names):
        self.names = names
        self.parent = {}
        self.uses = []          # (Var node, defs)
        self.defs = {}          # id(Assign) -> def id
        self.stmt_of = {}       # def id -> Assign

    def find(self, x):
        while self.parent.setdefault(x, x) != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        self.parent[self.find(a)] = self.find(b)

    def reach(self, st, v):
        return st.get(v, frozenset({("entry", v)}))

    def read(self, e, st):
        if st is None:
            return
        for x in walk(e):
            if isinstance(x, Var) and x.name in self.names:
                self.uses.append((x, self.reach(st, x.name)))

    def simple(self, s, st):
        if st is None:
            return None
        for e in exprs(s):
            self.read(e, st)
        if isinstance(s, Assign) and isinstance(s.lhs, Var) and s.lhs.name in self.names:
            v = s.lhs.name
            d = self.defs.setdefault(id(s), ("def", len(self.defs)))
            self.stmt_of[d] = s
            self.find(d)
            if s.op != "=":
                for r in self.reach(st, v):
                    self.union(d, r)
            st = dict(st)
            st[v] = frozenset({d})
        return st

    def block(self, stmts, st, ctx):
        for s in stmts:
            st = self.stmt(s, st, ctx)
        return st

    def stmt(self, s, st, ctx):
        if isinstance(s, (Assign, ExprS)):
            return self.simple(s, st)
        if st is None and not isinstance(s, Case):
            return None
        if isinstance(s, Block):
            return self.block(s.body, st, ctx)
        if isinstance(s, If):
            self.read(s.c, st)
            return _join(self.stmt(s.t, st, ctx), self.stmt(s.e, st, ctx) if s.e is not None else st)
        if isinstance(s, While):
            head = st
            while True:
                self.read(s.c, head)
                inner = {"brk": [], "cont": []}
                end = self.stmt(s.body, head, inner)
                new = _join(head, end, *inner["cont"])
                if new == head:
                    return _join(head, *inner["brk"])
                head = new
        if isinstance(s, DoWhile):
            head = st
            while True:
                inner = {"brk": [], "cont": []}
                end = self.stmt(s.body, head, inner)
                cond = _join(end, *inner["cont"])
                self.read(s.c, cond)
                new = _join(st, cond)
                if new == head:
                    return _join(cond, *inner["brk"])
                head = new
        if isinstance(s, For):
            if s.init is not None:
                st = self.stmt(s.init, st, ctx)
            head = st
            while True:
                if s.c is not None:
                    self.read(s.c, head)
                inner = {"brk": [], "cont": []}
                end = self.stmt(s.body, head, inner)
                step = _join(end, *inner["cont"])
                if s.step is not None:
                    step = self.stmt(s.step, step, ctx)
                new = _join(head, step)
                if new == head:
                    return _join(head if s.c is not None else None, *inner["brk"])
                head = new
        if isinstance(s, Switch):
            self.read(s.e, st)
            inner = {"brk": [], "cont": ctx.get("cont")}
            cur, has_default = None, False
            for x in s.body.body:
                if isinstance(x, Case):
                    has_default |= x.v is None
                    cur = _join(cur, st)
                    continue
                cur = self.stmt(x, cur, inner)
            return _join(cur, *inner["brk"], None if has_default else st)
        if isinstance(s, Return):
            if s.e is not None:
                self.read(s.e, st)
            return None
        if isinstance(s, Break):
            ctx["brk"].append(st)
            return None
        if isinstance(s, Continue):
            if ctx.get("cont") is None:
                raise Unsupported("continue outside a loop")
            ctx["cont"].append(st)
            return None
        if isinstance(s, Case):
            return st
        raise Unsupported(f"webs: {type(s).__name__}")


def split(body, names, types):
    """Rename the webs of the variables in names; types gets the new names. Returns {new name: old name}."""
    from .lift import map_exprs
    map_exprs(body.body, clone)
    w = _Webs(set(names))
    w.block(body.body, {}, {"brk": [], "cont": None})
    for _, ds in w.uses:
        ds = list(ds)
        for d in ds[1:]:
            w.union(ds[0], d)
    # each web's name: the entry's web keeps the variable's name
    by_var = {}
    for d, s in w.stmt_of.items():
        by_var.setdefault(s.lhs.name, set()).add(w.find(d))
    name_of, out = {}, {}
    for v, roots in by_var.items():
        entry = w.find(("entry", v)) if ("entry", v) in w.parent else None
        k, first = 0, True
        for r in sorted(roots, key=lambda r: min(d[1] for d in w.stmt_of if w.find(d) == r)):
            if r == entry or (entry is None and first):
                name_of[r] = v
                first = False
                continue
            k += 1
            nv = f"{v}__w{k}"
            name_of[r] = nv
            out[nv] = v
            types[nv] = types.get(v, "u32")
        if entry is not None:
            name_of.setdefault(entry, v)
    for d, s in w.stmt_of.items():
        s.lhs = Var(name_of[w.find(d)])
    for x, ds in w.uses:
        r = w.find(next(iter(ds)))
        x.name = name_of.get(r, x.name)
    return out
