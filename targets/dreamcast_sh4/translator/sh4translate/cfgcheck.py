"""A check of the CFG structurer (cfgstruct): the structured tree must run the same statements in the same order as
the goto graph it was made from, on every path.

Both are run on the same branch decisions: a condition's value is a pseudo-random function of the statements run so
far and of the condition's atoms (its comparisons and flags), so where the two forms have run the same statements
they see the same program state and decide alike. A condition the structurer rewrote (negated, merged with && or
||, turned into a while or do-while test) is evaluated from its atoms, with a comparison and its textual inverse
(`a <= b` for `a > b`, `!=` for `==`) as one atom. The flag a structured tree adds (fl_) is interpreted. Each run
stops at a return or after a step limit; the two statement traces must be equal.

Used by tools/struct_check.py and by cfgstruct.structure_cfg when SH4_CFG_CHECK is set.
"""
from __future__ import annotations

import re
import zlib

from .structure import IDENT, top_ops, wrapped

INVERSE = {"!=": "==", ">=": "<", "<=": ">"}
FLAG_SET = re.compile(r"^fl_ = (\d+)u;$")
FLAG_TEST = re.compile(r"^fl_ (==|!=) (\d+)u$")


def mix(*xs):
    """A hash that is the same in every process (str hashes are salted per process)."""
    return zlib.crc32(repr(xs).encode())


class Mismatch(Exception):
    pass


class _Brk(Exception):
    pass


class _Cnt(Exception):
    pass


class _End(Exception):
    pass


class Run:
    """One run: the trace so far, its hash (the program state's stand-in) and the flag."""

    def __init__(self, seed, limit):
        self.seed, self.limit = seed, limit
        self.trace, self.h, self.fl = [], mix("seed", seed), 0

    def stmt(self, line):
        y = line.strip()
        if not y or y.startswith("/*") or y.startswith("(void)("):
            return
        m = FLAG_SET.match(y)
        if m:
            self.fl = int(m.group(1))
            return
        self.trace.append(y)
        self.h = mix(self.h, y)
        if len(self.trace) >= self.limit:
            raise _End

    def atom(self, key):
        return mix(self.h, key) & 1 == 1

    def cond(self, c):
        c = c.strip()
        if wrapped(c):
            return self.cond(c[1:-1])
        if c.startswith("!") and (IDENT.match(c) or wrapped(c[1:])):
            return not self.cond(c[1:])
        ops = top_ops(c)
        for lop in ("||", "&&"):
            ks = [k for k, op in ops if op == lop]
            if ks and not any(op == "?" for _, op in ops):
                parts, last = [], 0
                for k in ks:
                    parts.append(c[last:k])
                    last = k + 2
                parts.append(c[last:])
                return any(self.cond(p) for p in parts) if lop == "||" else all(self.cond(p) for p in parts)
        m = FLAG_TEST.match(c)
        if m:
            return (self.fl == int(m.group(2))) == (m.group(1) == "==")
        if len(ops) == 1 and ops[0][1] in ("==", "!=", "<", ">", "<=", ">="):
            k, op = ops[0]
            lhs, rhs = c[:k].strip(), c[k + len(op):].strip()
            if op in INVERSE:
                return not self.atom((lhs, INVERSE[op], rhs))
            return self.atom((lhs, op, rhs))
        return self.atom(c)

    def pick(self, key, n):
        return mix(self.h, key) % n


def arms_key(groups):
    return sorted(tuple(sorted(g)) for g in groups)


def run_graph(nodes, succ, run):
    """The goto graph (cfgstruct.build_graph's nodes and successors, before any rewrite)."""
    x = 0
    try:
        while True:
            n = nodes[x]
            for line in n.stmts:
                run.stmt(line)
            t = n.term
            if t[0] == "goto":
                x = succ[x][0]
            elif t[0] == "cond":
                x = succ[x][0] if run.cond(t[1]) else succ[x][1]
            elif t[0] == "switch":
                prefix, cases, suffix = t[1]
                for line in prefix:
                    run.stmt(line)
                by = {}
                for v, lab in cases:
                    by.setdefault(lab, []).append(v)
                labs = list(dict.fromkeys(lab for _, lab in cases))
                keys = arms_key(by[lab] for lab in labs)
                k = run.pick(("switch", tuple(map(tuple, keys))), len(keys) + 1)
                if k == len(keys):
                    for line in suffix:
                        run.stmt(line)
                    return
                lab = next(lab for lab in labs if tuple(sorted(by[lab])) == keys[k])
                x = succ[x][labs.index(lab)]
            else:
                return
    except _End:
        return


def run_tree(root, run, op_ends):
    def seq(ss):
        for s in ss:
            one(s)

    def one(s):
        if s.k == "ops":
            for line in s.a:
                run.stmt(line)
            if op_ends(s.a):
                raise _End
        elif s.k == "if":
            seq(s.a if run.cond(s.c) else s.b)
        elif s.k == "loop":
            shape = s.c[0] if s.c else None
            if shape == "while":
                while run.cond(s.c[1]):
                    try:
                        seq(s.a)
                    except _Cnt:
                        pass
                    except _Brk:
                        break
            elif shape == "do":
                while True:
                    try:
                        seq(s.a)
                    except _Cnt:
                        pass
                    except _Brk:
                        break
                    if not run.cond(s.c[1]):
                        break
            else:
                while True:
                    try:
                        seq(s.a)
                    except _Cnt:
                        pass
                    except _Brk:
                        break
        elif s.k == "block":
            if s.c:
                try:        # do { } while (0): a break or a continue leaves it
                    seq(s.a)
                except (_Brk, _Cnt):
                    pass
            else:
                seq(s.a)
        elif s.k == "switch":
            prefix, suffix = s.x
            for line in prefix:
                run.stmt(line)
            keys = arms_key(vals for vals, _ in s.a)
            k = run.pick(("switch", tuple(map(tuple, keys))), len(keys) + 1)
            if k == len(keys):
                for line in suffix:
                    run.stmt(line)
                raise _End
            at = next(i for i, (vals, _) in enumerate(s.a) if tuple(sorted(vals)) == keys[k])
            try:
                for vals, body in s.a[at:]:
                    seq([b for b in body if b.k != "fall"])
                    if not (body and body[-1].k == "fall"):
                        break
            except _Brk:
                pass
        elif s.k == "brk":
            raise _Brk
        elif s.k == "cnt":
            raise _Cnt
        elif s.k == "fall":
            pass
        else:
            raise Mismatch(f"a statement the check does not model: {s.k}")
    try:
        seq(root)
    except _End:
        pass
    except (_Brk, _Cnt):
        raise Mismatch("a break or continue outside any loop")


def check(nodes, succ, root, op_ends, seeds=64, limit=4000):
    """Raise Mismatch at the first seed whose traces differ, naming the first statement where they part."""
    for seed in range(seeds):
        a, b = Run(seed, limit), Run(seed, limit)
        run_graph(nodes, succ, a)
        run_tree(root, b, op_ends)
        if a.trace != b.trace:
            k = next((i for i, (x, y) in enumerate(zip(a.trace, b.trace)) if x != y), min(len(a.trace), len(b.trace)))
            ctx = a.trace[max(0, k - 3):k]
            raise Mismatch(f"seed {seed}: the traces part after {k} statements (...{ctx}): the graph runs "
                           f"{a.trace[k] if k < len(a.trace) else 'nothing more'!r}, the tree "
                           f"{b.trace[k] if k < len(b.trace) else 'nothing more'!r}")
