"""Dataflow passes over the translator's blocks (ir.py), from the lifted form (every register a C local, every
instruction its statements) to C a reader can follow. Each pass keeps the function's observable behaviour in
DecompPipeline's model: its return value, its writes to guest memory outside the stack window, and the target and
argument registers of each call, in order.

  propagate  global constant and copy propagation (and constant stack slots: a load from a stack address another
             store last wrote, on every path, is the stored value); branches on constants fold, unreachable blocks go
  dce        assignments nothing reads, and stores to the stack frame no load reads (only while no stack address
             escapes: is passed, stored or computed with rather than used as a load/store address)
  calls      tc_call or tc_callf, from whether r3 or f1 is read after the call
  substitute a value used once, in the same block, moves into its use (never across a store, call or load hazard)

The entry state is DecompPipeline's: every register 0 but the parameters and the fixed ones the entry block sets.
"""
from __future__ import annotations

import struct

from collections import defaultdict

from . import expr as X
from .expr import Bin, Call, Cast, Cond, Const, Un, Var
from .ir import Assign, CallS, Comment, Goto, If, Ret, Store, Switch

FLOAT_REG = "f64"


def zero(ty):
    if ty in X.FLOAT:
        return Const(0.0, ty)
    if ty in X.TRUTH:
        return Const(0, "u32") if ty == "flag" else Const(0, "bool")
    return Const(0, ty)


# ------------------------------------------------------------------------------------------- expression rebuild
def rebuild(e, fn):
    """e with fn applied to every Var (fn(var) -> expr or None to keep) and every load Call (fn(call) after its
    arguments are rebuilt), folding as it goes."""
    if isinstance(e, Var):
        r = fn(e)
        return e if r is None else r
    if isinstance(e, Const):
        return e
    if isinstance(e, Un):
        a = rebuild(e.a, fn)
        if a is e.a:
            return e
        if e.op == "~":
            return X.bnot(a)
        if e.op == "!":
            return X.lnot(a)
        if e.op == "-":
            return X.fneg(a) if e.ty in X.FLOAT else X.neg(a)
        return Un(e.op, a, e.ty)
    if isinstance(e, Cast):
        a = rebuild(e.a, fn)
        if a is e.a:
            return e
        if e.ty == "f64" and isinstance(a, Cast) and a.ty == "f32":
            return X.fround(a.a)
        return X.cast(e.ty, a)
    if isinstance(e, Bin):
        a, b = rebuild(e.a, fn), rebuild(e.b, fn)
        if a is e.a and b is e.b:
            return e
        if e.op in X.CMP:
            return X.compare(e.op, a, b)
        if e.op in ("&&", "||"):
            return X.logic(e.op, a, b)
        return X.binop(e.op, a, b, e.ty)
    if isinstance(e, Cond):
        c, a, b = rebuild(e.c, fn), rebuild(e.a, fn), rebuild(e.b, fn)
        if c is e.c and a is e.a and b is e.b:
            return e
        return X.cond(c, a, b)
    if isinstance(e, Call):
        args = [rebuild(a, fn) for a in e.args]
        c = e if all(x is y for x, y in zip(args, e.args)) else fold_call(Call(e.fn, args, e.ty, e.load))
        if isinstance(c, Call) and c.load:
            r = fn(c)
            return c if r is None else r
        return c
    raise TypeError(e)


def fold_call(c):
    """Exact identities of the helpers (no rounding or memory involved)."""
    a = c.args
    if c.fn == "px_f2b" and isinstance(a[0], Call) and a[0].fn == "px_b2f":
        return a[0].args[0]
    if c.fn == "px_b2f" and isinstance(a[0], Call) and a[0].fn == "px_f2b":
        return a[0].args[0]
    if c.fn == "px_fdiv" and isinstance(a[1], Const) and a[1].v == 0.0:
        # x / +-0 by its bits (px_fdz): MSVC /W4 rejects a division by a constant zero (C4723)
        neg = struct.pack(">d", a[1].v)[0] >> 7
        return Call("px_fdz", [a[0], Const(neg << 63, "u64")], "f64")
    if c.fn == "ppc_cntlzw" and isinstance(a[0], Const):
        v = a[0].v & 0xFFFFFFFF
        return Const(32 - v.bit_length(), "u64")
    if c.fn == "ppc_cntlzd" and isinstance(a[0], Const):
        return Const(64 - (a[0].v & 0xFFFFFFFFFFFFFFFF).bit_length(), "u64")
    return c


def subst_vars(e, m):
    return rebuild(e, lambda v: m.get(v.name) if isinstance(v, Var) else None)


# ------------------------------------------------------------------------------------------------------- graph
class Fn:
    def __init__(self, blocks, types, stack, protect=()):
        self.blocks = blocks
        self.types = types              # var name -> type
        self.stack = stack              # (lo, hi): the stack window's addresses
        self.protect = set(protect)     # vars never removed (none by default)

    def index(self):
        self.by = {b.label: b for b in self.blocks}
        self.preds = defaultdict(list)
        for b in self.blocks:
            for s in b.term.succs():
                self.preds[s].append(b.label)

    def ty(self, name):
        return self.types.get(name, "u32")


def stmt_exprs(st):
    return st.exprs()


def all_exprs(fn):
    for b in fn.blocks:
        for st in b.stmts:
            yield from st.exprs()
        yield from b.term.exprs()
        if isinstance(b.term, Switch):
            for st in b.term.default:
                yield from st.exprs()


def remove_unreachable(fn):
    fn.index()
    seen, work = set(), [fn.blocks[0].label]
    while work:
        lab = work.pop()
        if lab in seen:
            continue
        seen.add(lab)
        work += fn.by[lab].term.succs()
    n = len(fn.blocks)
    fn.blocks = [b for b in fn.blocks if b.label in seen]
    fn.index()
    return len(fn.blocks) != n


def fold_branches(fn):
    changed = False
    for b in fn.blocks:
        t = b.term
        if isinstance(t, If):
            if isinstance(t.cond, Const):
                b.term = Goto(t.yes if t.cond.v else t.no)
                changed = True
            elif t.yes == t.no:
                b.term = Goto(t.yes)
                changed = True
        elif isinstance(t, Switch) and isinstance(t.e, Const):
            hit = [lab for v, lab in t.cases if v == t.e.v]
            if hit:
                b.term = Goto(hit[0])
                changed = True
    return changed


def merge_blocks(fn):
    """A block that only goes to a block nothing else reaches takes that block's code."""
    fn.index()
    changed = False
    entry = fn.blocks[0].label
    gone = set()
    for b in fn.blocks:
        if b.label in gone:
            continue
        while isinstance(b.term, Goto):
            c = fn.by[b.term.label]
            if c is b or c.label == entry or c.label in gone or len(fn.preds[c.label]) != 1:
                break
            b.stmts += c.stmts
            b.term = c.term
            gone.add(c.label)
            for s in c.term.succs():
                fn.preds[s] = [b.label if x == c.label else x for x in fn.preds[s]]
            changed = True
    if gone:
        fn.blocks = [b for b in fn.blocks if b.label not in gone]
        fn.index()
    return changed


# ---------------------------------------------------------------------------------------------- stack escapes
def in_stack(fn, v):
    return fn.stack[0] <= (v & 0xFFFFFFFF) < fn.stack[1]


def stack_escapes(fn):
    """True when a stack address is used other than as the whole address of a load or store (passed to a callee,
    stored, compared, indexed), or r1 is not a constant everywhere (a frame of run-time size): then any memory
    access may reach the frame, and stack stores all stay."""
    if any("r1" in e.vars() for e in all_exprs(fn)):
        return True
    def bad(e, as_addr=False):
        if isinstance(e, Const):
            return (not as_addr) and e.ty in ("u32", "u64") and in_stack(fn, e.v)
        if isinstance(e, Var):
            return False
        if isinstance(e, Call):
            if e.load:
                return bad(e.args[0], True)
            return any(bad(a) for a in e.args)
        if isinstance(e, (Un, Cast)):
            return bad(e.a)
        if isinstance(e, Bin):
            return bad(e.a) or bad(e.b)
        if isinstance(e, Cond):
            return bad(e.c) or bad(e.a) or bad(e.b)
        return False

    framed = []         # a stack address stored in the frame itself (the back chain stwu writes)

    def stmts(sts):
        for st in sts:
            if isinstance(st, Store):
                if bad(st.addr, True):
                    return True
                if bad(st.val):
                    a = const_addr(st.addr)
                    if a is None or not in_stack(fn, a) or not isinstance(st.val, Const):
                        return True
                    framed.append(st)
            elif isinstance(st, CallS):
                if bad(st.target) or any(bad(e) for _, _, e in st.args):
                    return True
            else:
                if any(bad(e) for e in st.exprs()):
                    return True
        return False
    for b in fn.blocks:
        if stmts(b.stmts) or any(bad(e) for e in b.term.exprs()):
            return True
        if isinstance(b.term, Switch) and stmts(b.term.default):
            return True
    # a stack address kept in the frame can come back through a load the forwarding did not answer, and then
    # be used as an address no constant shows
    if framed:
        spans = [(const_addr(s.addr), const_addr(s.addr) + s.size()) for s in framed]
        for e in all_exprs(fn):
            for c in loads_in(e):
                a = const_addr(c.args[0])
                if a is not None:
                    la, lb = span(c.fn, a)
                    if any(lo < lb and la < hi for lo, hi in spans):
                        return True
    return False


def loads_in(e, out=None):
    out = [] if out is None else out
    if isinstance(e, Call) and e.load:
        out.append(e)
    for c in children(e):
        loads_in(c, out)
    return out


def const_addr(e):
    return e.v & 0xFFFFFFFF if isinstance(e, Const) and e.ty not in X.FLOAT else None


# loads a stored value answers exactly: (store fn, store size, load fn, offset) -> how to read the stored value
def forward(sfn, sval, lfn, off):
    """The value a load at (store address + off) of kind lfn reads from a store of kind sfn of value sval, or None
    when the bytes are not a simple function of it."""
    if sfn == "wr32" and lfn == "rd32" and off == 0:
        return sval
    if sfn == "wrf64" and lfn == "rdf64" and off == 0:
        return sval
    if sfn == "wr16" and lfn == "rd16" and off == 0:
        return X.cast("u16", sval)
    if sfn == "wr8" and lfn == "rd8" and off == 0:
        return X.cast("u8", sval)
    if sfn == "wr64" and lfn == "rd64" and off == 0:
        return sval
    if sfn == "wr64" and lfn == "rd32" and off in (0, 4):
        return X.cast("u32", X.shr(sval, Const(32, "u64")) if off == 0 else sval)
    if sfn == "wr64" and lfn == "rdf64" and off == 0:
        return Call("px_b2f", [sval], "f64")
    if sfn == "wrf64" and lfn == "rd64" and off == 0:
        return Call("px_f2b", [sval], "u64")
    if sfn == "xv_st" and lfn == "xv_ld" and off == 0:
        return sval
    if sfn == "wrf64" and lfn == "rd32" and off in (0, 4):
        b = Call("px_f2b", [sval], "u64")
        return X.cast("u32", X.shr(b, Const(32, "u64")) if off == 0 else b)
    if sfn == "wrf32d" and lfn == "rd32" and off == 0:
        return Call("ppc_single_bits", [sval], "u32")
    if sfn == "wr32" and lfn == "rdf32" and off == 0:
        return Call("px_w2f", [sval], "f32")
    if sfn == "wrf32d" and lfn == "rdf32" and off == 0:
        return Call("px_w2f", [Call("ppc_single_bits", [sval], "u32")], "f32")
    return None


SIZES = {"wr8": 1, "wr16": 2, "wr32": 4, "wr64": 8, "wrf32d": 4, "wrf64": 8, "rd8": 1, "rd16": 2, "rd32": 4,
         "rd64": 8, "rdf32": 4, "rdf64": 8, "rd16r": 2, "rd32r": 4, "wr16r": 2, "wr32r": 4,
         "xv_ld": 16, "xv_st": 16, "xv_ldew": 4, "xv_stew": 4, "xv_steh": 2, "xv_steb": 1}
# vector loads/stores of part of the aligned quadword holding the address (lvlx, lvrx, stvlx, stvrx): they touch
# bytes on both sides of it, so their span is the whole quadword
PARTIAL = {"xv_ldl", "xv_ldr", "xv_stl", "xv_str"}


def span(fn_name, a):
    """(lo, hi) of the bytes an access of kind fn_name at address a may touch."""
    if fn_name in PARTIAL:
        b = a & ~15
        return b, b + 16
    return a, a + SIZES[fn_name]


# --------------------------------------------------------------------------------------- constant propagation
class State:
    """Var -> Const/Var value; stack slot address -> (store fn, value)."""
    __slots__ = ("v", "m")

    def __init__(self, v=None, m=None):
        self.v = dict(v or {})
        self.m = dict(m or {})

    def copy(self):
        return State(self.v, self.m)

    def meet(self, o):
        v = {k: x for k, x in self.v.items() if k in o.v and o.v[k] == x}
        m = {k: x for k, x in self.m.items() if k in o.m and o.m[k][0] == x[0] and o.m[k][1] == x[1]}
        return State(v, m)

    def __eq__(self, o):
        return self.v == o.v and self.m == o.m

    def kill(self, name):
        self.v.pop(name, None)
        for k in [k for k, x in self.v.items() if name in x.vars()]:
            del self.v[k]
        for k in [k for k, (_, x) in self.m.items() if name in x.vars()]:
            del self.m[k]


def simple(e):
    return isinstance(e, (Const, Var)) or (isinstance(e, Cast) and isinstance(e.a, Var) and e.ty not in X.FLOAT
                                           and e.a.ty not in X.FLOAT)


class Propagator:
    def __init__(self, fn, entry_vals, track_stack):
        self.fn, self.entry_vals, self.track = fn, entry_vals, track_stack

    def read(self, st: State, e):
        def f(x):
            if isinstance(x, Var):
                return st.v.get(x.name)
            if self.track and x.load:
                a = const_addr(x.args[0])
                if a is None or not in_stack(self.fn, a):
                    return None
                if x.fn not in SIZES:
                    return None
                n = SIZES[x.fn]
                for sa, (sfn, sval) in st.m.items():
                    off = a - sa
                    if 0 <= off and off + n <= SIZES[sfn]:
                        return forward(sfn, sval, x.fn, off)
                if n == 8 and x.fn in ("rd64", "rdf64"):
                    hi, lo = st.m.get(a), st.m.get(a + 4)
                    if hi and lo and hi[0] == "wr32" and lo[0] == "wr32":
                        w = X.bor(X.shl(X.cast("u64", hi[1]), Const(32, "u64")), X.cast("u64", lo[1]))
                        w = X.binop("|", X.binop("<<", X.cast("u64", hi[1]), Const(32, "u64"), "u64"),
                                    X.cast("u64", lo[1]), "u64")
                        return w if x.fn == "rd64" else Call("px_b2f", [w], "f64")
                return None
            return None
        return rebuild(e, f)

    def store(self, st: State, s: Store):
        a = const_addr(s.addr)
        if a is None:
            if s.fn == "px_q":
                st.m.clear()
            return
        lo, hi = span(s.fn, a) if (s.fn in SIZES or s.fn in PARTIAL) else (a, a + s.size())
        for sa in [k for k, (sfn, _) in st.m.items() if k < hi and lo < k + SIZES[sfn]]:
            del st.m[sa]
        if self.track and in_stack(self.fn, a) and simple(s.val) and s.fn in SIZES:
            st.m[a] = (s.fn, s.val)

    def transfer(self, st: State, sts, rewrite):
        for s in sts:
            if isinstance(s, Comment):
                continue
            new = [self.read(st, e) for e in s.exprs()]
            if rewrite:
                s.set_exprs(new)
            if isinstance(s, Assign):
                st.kill(s.dst)
                val = new[0]
                if isinstance(val, Const) or (simple(val) and s.dst not in val.vars()
                                              and X.INT_BITS.get(val.ty, 64) == X.INT_BITS.get(self.fn.ty(s.dst), 64)
                                              and (val.ty in X.FLOAT) == (self.fn.ty(s.dst) in X.FLOAT)):
                    st.v[s.dst] = val
            elif isinstance(s, Store):
                self.store(st, s)
            elif isinstance(s, CallS):
                for w in s.writes():
                    st.kill(w)

    def run(self):
        fn = self.fn
        fn.index()
        order = [b.label for b in fn.blocks]
        ins = {order[0]: State(self.entry_vals)}
        outs = {}
        work = list(order)
        inq = set(order)
        rounds = 0
        while work:
            rounds += 1
            if rounds > 200000:
                raise RuntimeError("propagation does not settle")
            lab = work.pop(0)
            inq.discard(lab)
            b = fn.by[lab]
            if lab == order[0]:
                st = State(self.entry_vals)
                for p in fn.preds[lab]:
                    if p in outs:
                        st = st.meet(outs[p])
            else:
                ps = [outs[p] for p in fn.preds[lab] if p in outs]
                if not ps:
                    continue
                st = ps[0]
                for p in ps[1:]:
                    st = st.meet(p)
            ins[lab] = st
            out = st.copy()
            self.transfer(out, b.stmts, False)
            if lab not in outs or outs[lab] != out:
                outs[lab] = out
                for s in b.term.succs():
                    if s not in inq:
                        work.append(s)
                        inq.add(s)
        for b in fn.blocks:
            if b.label not in ins:
                continue
            st = ins[b.label].copy()
            self.transfer(st, b.stmts, True)
            b.term.set_exprs([self.read(st, e) for e in b.term.exprs()])
            if isinstance(b.term, Switch):
                st2 = st.copy()
                st2.kill("g_")
                self.transfer(st2, b.term.default, True)


# -------------------------------------------------------------------------------------------------- liveness
def stmt_reads(s):
    return s.reads()


def liveness(fn):
    """live-in per block label."""
    fn.index()
    use, kill = {}, {}
    for b in fn.blocks:
        live = set(b.term.reads())
        for s in reversed(b.stmts):
            live -= s.writes()
            live |= s.reads()
        use[b.label] = live
    live_in = {b.label: set() for b in fn.blocks}
    changed = True
    rounds = 0
    while changed:
        rounds += 1
        changed = False
        for b in reversed(fn.blocks):
            out = set()
            for s in b.term.succs():
                out |= live_in[s]
            live = out | b.term.reads()
            for s in reversed(b.stmts):
                live -= s.writes()
                live |= s.reads()
            if live != live_in[b.label]:
                live_in[b.label] = live
                changed = True
        if rounds > 10000:
            raise RuntimeError("liveness does not settle")
    return live_in


def live_out(fn, live_in, b):
    out = set()
    for s in b.term.succs():
        out |= live_in[s]
    return out | b.term.reads()


def dce(fn):
    changed = False
    live_in = liveness(fn)
    for b in fn.blocks:
        live = live_out(fn, live_in, b)
        keep = []
        for s in reversed(b.stmts):
            if isinstance(s, Assign) and s.dst not in live and s.dst not in fn.protect:
                changed = True
                continue
            if isinstance(s, Comment):
                keep.append(s)
                continue
            live -= s.writes()
            live |= s.reads()
            keep.append(s)
        keep.reverse()
        b.stmts = keep
    return changed


def stack_dce(fn):
    """Stores to the stack window that no load can read (the frame never escapes, so every load from it has a
    constant address)."""
    if stack_escapes(fn):
        return False
    loads = []

    def find(e):
        if isinstance(e, Call) and e.load:
            loads.append(e)
        for c in children(e):
            find(c)
    for e in all_exprs(fn):
        find(e)
    spans = []
    for c in loads:
        a = const_addr(c.args[0])
        if a is None:
            if in_stack_possible(fn, c.args[0]):
                return False
            continue
        spans.append(span(c.fn, a))
    changed = False
    for b in fn.blocks:
        keep = []
        for s in b.stmts:
            if isinstance(s, Store) and (s.fn in SIZES or s.fn in PARTIAL):
                a = const_addr(s.addr)
                sl, sh = span(s.fn, a) if a is not None else (0, 0)
                if a is not None and in_stack(fn, a) and not any(lo < sh and sl < hi for lo, hi in spans):
                    changed = True
                    continue
            keep.append(s)
        b.stmts = keep
    return changed


def in_stack_possible(fn, e):
    """A non-constant address can only reach the frame through an escaped stack address (checked first), so never."""
    return False


def children(e):
    if isinstance(e, (Un, Cast)):
        return [e.a]
    if isinstance(e, Bin):
        return [e.a, e.b]
    if isinstance(e, Cond):
        return [e.c, e.a, e.b]
    if isinstance(e, Call):
        return list(e.args)
    return []


# ------------------------------------------------------------------------------------------------------ calls
def choose_calls(fn, notes, returns=None):
    """tc_call (r3) or tc_callf (f1) for each call: the callee's known result register (DP's meta, or the
    executable-wide returns.analyse), else whichever is read after it, r3 when both are."""
    live_in = liveness(fn)
    for b in fn.blocks:
        live = live_out(fn, live_in, b)
        for s in reversed(b.stmts):
            if isinstance(s, CallS) and s.kind is None:
                r, f = "r3" in live, "f1" in live
                known = (returns or {}).get(s.target.v & 0xFFFFFFFF) if isinstance(s.target, Const) else None
                if known in ("r", "f"):
                    s.kind = known
                    s.note = None if (r if known == "r" else f) else "dead"
                    live -= s.writes()
                    live |= s.reads()
                    continue
                if r and f:
                    notes.append(f"call at {s.addr:08x}: both r3 and f1 are read after it; only r3 is the result")
                s.kind = "f" if f and not r else "r"
                s.note = "dead" if not (r or f) else None
            live -= s.writes()
            live |= s.reads()


# ------------------------------------------------------------------------------------------------ substitution
def count_uses(fn):
    n = defaultdict(int)

    def walk(e):
        if isinstance(e, Var):
            n[e.name] += 1
        for c in children(e):
            walk(c)
    for e in all_exprs(fn):
        walk(e)
    return n


def defs(fn):
    d = defaultdict(int)
    for b in fn.blocks:
        for s in b.stmts:
            for w in s.writes():
                d[w] += 1
        if isinstance(b.term, Switch):
            for s in b.term.default:
                for w in s.writes():
                    d[w] += 1
    return d


def has_call(e):
    if isinstance(e, Call) and not e.load and e.fn.startswith("tc_"):
        return True
    return any(has_call(c) for c in children(e))


def occurrences(e, name):
    if isinstance(e, Var):
        return int(e.name == name)
    return sum(occurrences(c, name) for c in children(e))


def dead_after(sts, j, term, name, live):
    """name is not read again after statement j of this block (j == len(sts): the terminator) before it is
    written, nor live out of the block."""
    if j < len(sts) and name in sts[j].writes():
        return True
    for k in range(j + 1, len(sts)):
        if name in sts[k].reads():
            return False
        if name in sts[k].writes():
            return True
    if j < len(sts) and name in term.reads():
        return False
    return name not in live


def substitute(fn, max_size=40):
    """x = e; ... use(x) -> use(e) when the use is the next read of x in this block, reads x once, and x is dead
    after it; nothing between them writes what e reads, and (when e loads) nothing between them stores or calls."""
    changed = False
    live_in = liveness(fn)
    for b in fn.blocks:
        sts = b.stmts
        live = set()
        for x in b.term.succs():
            live |= live_in[x]
        i = 0
        while i < len(sts):
            s = sts[i]
            if not (isinstance(s, Assign) and s.e.size() <= max_size) or s.dst in s.e.vars():
                i += 1
                continue
            reads = s.e.vars()
            loads = s.e.has_load()
            target = None
            for j in range(i + 1, len(sts) + 1):
                cand = sts[j] if j < len(sts) else b.term
                if s.dst in cand.reads():
                    target = j
                    break
                if j == len(sts):
                    break
                w = cand.writes()
                if w & reads or s.dst in w:
                    break
                if loads and isinstance(cand, (Store, CallS)):
                    break
            if target is None:
                i += 1
                continue
            cand = sts[target] if target < len(sts) else b.term
            if target == len(sts) and isinstance(cand, Switch):
                i += 1
                continue
            if sum(occurrences(e, s.dst) for e in cand.exprs()) != 1 or not dead_after(sts, target, b.term, s.dst,
                                                                                         live):
                i += 1
                continue
            m = {s.dst: s.e}
            cand.set_exprs([subst_vars(e, m) for e in cand.exprs()])
            del sts[i]          # (the moved value's own uses are unchanged in number)
            changed = True
    return changed


# ---------------------------------------------------------------------------------------------------- driver
def optimise(fn, entry_vals, notes, returns=None, rounds=30):
    for _ in range(rounds):
        changed = False
        track = not stack_escapes(fn)
        Propagator(fn, entry_vals, track).run()
        changed |= fold_branches(fn)
        changed |= remove_unreachable(fn)
        changed |= merge_blocks(fn)
        changed |= dce(fn)
        changed |= stack_dce(fn)
        if not changed:
            break
    choose_calls(fn, notes, returns)
    dce(fn)
    for _ in range(rounds):
        if not substitute(fn):
            break
    dce(fn)
