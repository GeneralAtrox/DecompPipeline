"""Readable C from a translator's raw C, without an agent: the shared part (the target gives its runtime's names).

lift(text, job) takes the raw C of one function (a translator's output, DP's raw-C form) and returns readable C that
includes game.h, or raises Unsupported. In order:
1. the body is parsed (core/rtx/cast) and its declarations noted; `(void)x;` lines go (a `(void)` call stays a call);
2. the raw parameters become locals initialised from the registry prototype's parameters (by storage);
3. the target's runtime state goes: the link register, the hand-over of the callee-saved registers (a C function
   keeps them; the value given back at exit must be the one taken at entry), and the stack pointer, whose every use
   becomes the entry stack pointer plus a constant;
4. each call through the runtime's call block becomes a call by name with the prototype's arguments (by storage);
5. dataflow (core/rtx/flow): forward substitution, dead stores, folding;
6. the stack frame's words become local variables (a frame address used as a value is not handled), the caller's
   stack words the stack parameters; then dataflow again;
7. memory: an access at a named global is its name, a literal-pool read its constant, any other a pointer
   dereference `*(T *)(address)`; an address used as a value is the function's or global's name;
8. the registers become locals with neutral names, declared at the top (initialised only where a read may come
   first); the static helpers the body still calls are kept.
Every step only rewrites what it can prove; anything else raises Unsupported and the function stays the agents'.
The readable judge (core/readable_check) and the gates decide, as for any readable file.
"""
from __future__ import annotations

import re

from . import cast, flow
from .cast import (Assign, Bin, Block, Call, Case, Cast, Cond, Decl, DoWhile, ExprS, For, If, Index, Member, Num, Raw,
                   Return, Switch, Unary, Var, While)
from .flow import Unsupported

GUEST_RAM = (0x8C000000, 0x8D000000)  # the Dreamcast's main RAM; a target's spec sets its own (ram)


class Job:
    """One function: its target (a TargetSpec), registry prototype pieces, the draft Names (core/draft.Names) and
    the raw signature's parameters [(raw name, storage, stack offset)]."""

    def __init__(self, spec, proto, proto_params, raw_params, raw_ret, names, kind, ret_kind):
        self.spec, self.proto, self.proto_params = spec, proto, proto_params
        self.raw_params, self.raw_ret, self.names, self.kind, self.ret_kind = raw_params, raw_ret, names, kind, ret_kind


# ---- tree helpers -----------------------------------------------------------------------------------------------

def map_exprs(stmts, fn):
    """Apply fn to every expression of every statement (in place, recursively)."""
    for k, s in enumerate(stmts):
        if isinstance(s, Assign):
            stmts[k] = Assign(s.lhs if isinstance(s.lhs, Var) else fn(s.lhs), s.op, fn(s.rhs))
        elif isinstance(s, ExprS):
            stmts[k] = ExprS(fn(s.e))
        elif isinstance(s, Decl):
            s.items = [(n, fn(i) if i is not None and not isinstance(i, Raw) else i, d) for n, i, d in s.items]
        elif isinstance(s, Block):
            map_exprs(s.body, fn)
        elif isinstance(s, If):
            s.c = fn(s.c)
            map_exprs(s.t.body, fn)
            if s.e is not None:
                map_exprs([s.e] if isinstance(s.e, If) else s.e.body, fn)
        elif isinstance(s, (While, DoWhile)):
            s.c = fn(s.c)
            map_exprs(s.body.body, fn)
        elif isinstance(s, For):
            if s.c is not None:
                s.c = fn(s.c)
            box = [x for x in (s.init, s.step) if x is not None]
            map_exprs(box, fn)
            it = iter(box)
            s.init = next(it) if s.init is not None else None
            s.step = next(it) if s.step is not None else None
            map_exprs(s.body.body, fn)
        elif isinstance(s, Switch):
            s.e = fn(s.e)
            map_exprs(s.body.body, fn)
        elif isinstance(s, Return):
            if s.e is not None:
                s.e = fn(s.e)


def rewrite(e, fn):
    """Bottom-up rewrite: fn(node) on each node after its children (returns the node or a replacement)."""
    if isinstance(e, Call):
        e = Call(e.fn if isinstance(e.fn, str) else rewrite(e.fn, fn), [rewrite(a, fn) for a in e.args])
    elif isinstance(e, Unary):
        e = Unary(e.op, rewrite(e.e, fn))
    elif isinstance(e, Cast):
        e = Cast(e.type, rewrite(e.e, fn))
    elif isinstance(e, Bin):
        e = Bin(e.op, rewrite(e.a, fn), rewrite(e.b, fn))
    elif isinstance(e, Cond):
        e = Cond(rewrite(e.c, fn), rewrite(e.a, fn), rewrite(e.b, fn))
    elif isinstance(e, Index):
        e = Index(rewrite(e.a, fn), rewrite(e.i, fn))
    elif isinstance(e, Member):
        e = Member(rewrite(e.e, fn), e.name, e.arrow)
    return fn(e)


def all_stmts(stmts):
    """Every statement, nested ones too, with the list holding it and its index."""
    for k, s in enumerate(list(stmts)):
        yield stmts, k, s
        for child in children(s):
            yield from all_stmts(child)


def children(s):
    if isinstance(s, Block):
        return [s.body]
    if isinstance(s, If):
        return [s.t.body] + ([s.e.body] if isinstance(s.e, Block) else children(s.e) if isinstance(s.e, If) else [])
    if isinstance(s, (While, DoWhile, For, Switch)):
        return [s.body.body]
    return []


def filter_stmts(stmts, keep):
    """Remove (recursively) the statements for which keep(s) is False."""
    stmts[:] = [s for s in stmts if keep(s)]
    for s in stmts:
        for c in children(s):
            filter_stmts(c, keep)


def every_expr(stmts):
    for _, _, s in all_stmts(stmts):
        if isinstance(s, (Assign, ExprS)):
            yield from flow.exprs(s)
            if isinstance(s, Assign) and isinstance(s.lhs, Var):
                yield s.lhs
        elif isinstance(s, (If, While, DoWhile)):
            yield s.c
        elif isinstance(s, For):
            if s.c is not None:
                yield s.c
            for x in (s.init, s.step):
                if x is not None:
                    yield from flow.exprs(x)
        elif isinstance(s, Switch):
            yield s.e
        elif isinstance(s, Return) and s.e is not None:
            yield s.e
        elif isinstance(s, Case) and s.v is not None:
            yield s.v


def names_used(stmts):
    out = set()
    for e in every_expr(stmts):
        for x in flow.walk(e):
            if isinstance(x, Var):
                out.add(x.name)
            elif isinstance(x, Call) and isinstance(x.fn, str):
                out.add(x.fn)
    return out


def calls_used(stmts):
    return {x.fn for e in every_expr(stmts) for x in flow.walk(e) if isinstance(x, Call) and isinstance(x.fn, str)}


# ---- the steps --------------------------------------------------------------------------------------------------

def declarations(body):
    """{variable: C type} of the body's declarations; nested declarations become assignments (their names are
    unique per type in translator output); top-level ones go."""
    types = {}

    def go(stmts, top):
        out = []
        for s in stmts:
            if isinstance(s, Decl):
                for n, init, dims in s.items:
                    if dims:
                        raise Unsupported(f"array local {n}{dims}")
                    if n in types and types[n] != s.type:
                        raise Unsupported(f"{n} declared as two types")
                    types[n] = s.type
                    if init is not None and not isinstance(init, Raw) and not top:
                        out.append(Assign(Var(n), "=", init))
                continue
            if isinstance(s, ExprS) and isinstance(s.e, Cast) and s.e.type == "void":
                if isinstance(s.e.e, Var):
                    continue  # (void)x;: only quiets a warning
                s = ExprS(s.e.e)  # (void)f(...);: the call runs, its result unused
            for c in children(s):
                c[:] = go(c, False)
            out.append(s)
        return out
    body.body = go(body.body, True)
    return types


def bind_params(body, job, types):
    """The raw parameters as locals initialised from the prototype's parameters (by storage)."""
    by_storage = {st: (t, n) for st, (t, n) in zip(job.proto_storages, job.proto_params)}
    init = []
    for raw, st, _ in job.raw_params:
        if st == "stack":
            continue
        if raw not in names_used(body.body):
            continue
        if st not in by_storage:
            raise Unsupported(f"raw parameter {raw} ({st}) has no prototype parameter")
        t, n = by_storage[st]
        job.used_params.add(n)
        types.setdefault(raw, job.raw_types.get(raw, "u32"))
        ty = types[raw]
        val = Var(n)
        fl = job.kind(t) == "f"
        if fl and ty not in ("f32", "f64"):
            # the raw C holds the float's bits in an integer: a bit copy, not a conversion
            val = Call(job.spec.f2u, [val])
            if ty != "u32":
                val = Cast(ty, val)
        elif ty == "f32" and not fl:
            val = Call(job.spec.u2f, [val if t == "u32" else Cast("u32", val)])
        elif "*" in t or t != ty:
            val = Cast(ty, val)
        if raw == n:
            if val != Var(n):
                raise Unsupported(f"raw parameter {raw} has the prototype's name but type {ty}, not {t}")
            continue  # the raw C already uses the prototype's name (a workspace with no registry names)
        init.append(Assign(Var(raw), "=", val))
    body.body[:0] = init


def strip_state(body, job, types):
    """The link register, the callee-saved hand-over and the stack pointer (step 3)."""
    sp = job.spec
    # a call block's fields as variables: a.r[0] -> __a_r0
    def fields(e):
        if isinstance(e, Index) and isinstance(e.a, Member) and isinstance(e.a.e, Var) and \
                types.get(e.a.e.name) == sp.args_type and isinstance(e.i, Num):
            return Var(f"__a_{e.a.name}{e.i.v}")
        return e
    for stmts, k, s in list(all_stmts(body.body)):
        if isinstance(s, Assign) and not isinstance(s.lhs, Var):
            lhs = rewrite(s.lhs, fields)
            stmts[k] = Assign(lhs, s.op, s.rhs)
    map_exprs(body.body, lambda e: rewrite(e, fields))

    # the link register: set at entry, saved and restored; anything else is not handled
    def no_link(s):
        if isinstance(s, Assign) and isinstance(s.lhs, Var) and s.lhs.name == sp.link:
            return False
        if isinstance(s, ExprS) and isinstance(s.e, Call) and s.e.fn in sp.stores and \
                flow.reads(s.e.args[1]) == {sp.link}:
            return False
        return True
    filter_stmts(body.body, no_link)
    if sp.link in names_used(body.body):
        raise Unsupported(f"{sp.link} is read (a computed return)")

    # the hand-over: at entry, the incoming value; after a call, unchanged; at exit, checked against the entry.
    # A target's state registers (state_regs) stay as the raw C reads and writes them.
    state = getattr(sp, "state_regs", set())
    top = body.body
    k = 0
    while k < len(top) and isinstance(top[k], Assign):
        s = top[k]
        g = _handover_get(s.rhs, sp)
        if g is not None and isinstance(s.lhs, Var) and g not in state:
            top[k] = Assign(s.lhs, "=", Var(f"__in_{g}"))
            types[f"__in_{g}"] = "f32" if g.startswith("f") else "u32"
        k += 1

    def walk_lists(stmts):
        out = []
        n = len(stmts)
        for i, s in enumerate(stmts):
            if isinstance(s, Assign) and _handover_get(s.rhs, sp) is not None:
                if _handover_get(s.rhs, sp) in state:
                    out.append(s)   # after a call: what the callee left in the state register
                continue            # after a call: the callee kept it
            if isinstance(s, ExprS) and isinstance(s.e, Call) and s.e.fn in sp.handover_set and \
                    sp.handover_reg(s.e) in state:
                out.append(s)       # the state register as the raw C writes it (before a call, at exit)
                continue
            if isinstance(s, ExprS) and isinstance(s.e, Call) and s.e.fn in sp.handover_set:
                rest = stmts[i + 1:]
                if all(isinstance(x, ExprS) and isinstance(x.e, Call) and x.e.fn in sp.handover_set for x in rest[:-1]) \
                        and (not rest or isinstance(rest[-1], Return)
                             or isinstance(rest[-1], ExprS) and isinstance(rest[-1].e, Call)
                             and rest[-1].e.fn in sp.handover_set):
                    reg = sp.handover_reg(s.e)
                    out.append(Assign(Var(f"__out_{reg}"), "=", s.e.args[1]))
                    types[f"__out_{reg}"] = "f32" if reg.startswith("f") else "u32"
                continue            # before a call: a C callee does not read it
            for c in children(s):
                c[:] = walk_lists(c)
            out.append(s)
        return out
    body.body = walk_lists(body.body)

    def left(e):
        if not (isinstance(e, Call) and e.fn in sp.handover_get | sp.handover_set):
            return False
        if not (e.args and isinstance(e.args[0], Num)):
            return True
        return (_handover_get(e, sp) if e.fn in sp.handover_get else sp.handover_reg(e)) not in state
    if any(left(x) for e in every_expr(body.body) for x in flow.walk(e)):
        raise Unsupported("hand-over left in an expression")

    # the stack pointer: entry value plus a constant at every point
    stack_pointer(body, job, types)


def _handover_get(e, sp):
    if isinstance(e, Call) and e.fn in sp.handover_get and len(e.args) == 1 and isinstance(e.args[0], Num):
        return sp.handover_name(e.fn, e.args[0].v)
    return None


def stack_pointer(body, job, types):
    sp = job.spec
    r = sp.sp_reg

    def sp_at(off):
        return flow.offset(Var("__sp"), off)

    def seq(stmts, off):
        """off at the end of the list (None: the list does not fall through)."""
        out = []
        for s in stmts:
            if isinstance(s, Assign) and isinstance(s.lhs, Var) and s.lhs.name == r:
                rhs = s.rhs
                if s.op in ("+=", "-=") and flow.is_int(rhs):
                    off = (off + (rhs.v if s.op == "+=" else -rhs.v)) & flow.U32
                    continue
                if s.op == "=" and flow.is_int(rhs) and rhs.v == sp.sp_init:
                    off = 0
                    continue
                b, c = flow.linear(rhs)
                if s.op == "=" and isinstance(b, Var) and b.name == r:
                    off = (off + c) & flow.U32
                    continue
                raise Unsupported(f"{r} set to a computed value")
            s = _sp_subst(s, r, off, sp_at)
            if isinstance(s, If):
                a = seq(s.t.body, off)
                b = seq(s.e.body if isinstance(s.e, Block) else [s.e], off) if s.e is not None else off
                ends = {x for x in (a, b) if x is not None}
                if len(ends) > 1:
                    raise Unsupported(f"{r} differs after an if")
                out.append(s)
                if not ends:
                    return out_set(stmts, out, None)
                off = ends.pop()
                continue
            if isinstance(s, (While, DoWhile, For)):
                end = seq(s.body.body, off)
                if end is not None and end != off:
                    raise Unsupported(f"{r} changes in a loop")
                out.append(s)
                continue
            if isinstance(s, Switch):
                end = seq(s.body.body, off)
                if end is not None and end != off:
                    raise Unsupported(f"{r} changes in a switch")
                out.append(s)
                continue
            if isinstance(s, Block):
                end = seq(s.body, off)
                out.append(s)
                if end is None:
                    return out_set(stmts, out, None)
                off = end
                continue
            out.append(s)
            if isinstance(s, (cast.Return, cast.Break, cast.Continue)):
                return out_set(stmts, out, None)
        return out_set(stmts, out, off)

    def out_set(stmts, out, off):
        stmts[:] = out
        return off
    seq(body.body, 0)
    # the uses that are still `r` were never set (no frame): the entry value
    map_exprs(body.body, lambda e: rewrite(e, lambda x: Var("__sp") if isinstance(x, Var) and x.name == r else x))
    types.pop(r, None)


def _sp_subst(s, r, off, sp_at):
    env = {r: sp_at(off)}
    if isinstance(s, (Assign, ExprS)):
        return flow._subst_stmt(s, env) if r in flow.uses(s) or (isinstance(s, Assign) and not isinstance(
            s.lhs, Var) and r in flow.reads(s.lhs)) else s
    if isinstance(s, If):
        s.c = flow.subst(s.c, env)
    elif isinstance(s, Switch):
        s.e = flow.subst(s.e, env)
    elif isinstance(s, Return) and s.e is not None:
        s.e = flow.subst(s.e, env)
    elif isinstance(s, (While, DoWhile)):
        s.c = flow.subst(s.c, env)
    elif isinstance(s, For) and s.c is not None:
        s.c = flow.subst(s.c, env)
    return s


def calls(body, job, types):
    """Step 4: tc_call(T, &a) -> Name(arguments by storage)."""
    sp = job.spec

    def conv(e):
        if not (isinstance(e, Call) and e.fn in sp.call_fns):
            return e
        target = e.args[0]
        if not flow.is_int(target):
            raise Unsupported("a computed call target")
        a = f"{target.v:08x}"
        c = job.names.callees.get(a)
        if c is None:
            raise Unsupported(f"callee {a} has no prototype placement")
        name, place, rkind, variadic = c
        if variadic:
            raise Unsupported(f"variadic callee {name}")
        ptypes = job.callee_types.get(a)
        if ptypes is None or len(ptypes) != len(place):
            raise Unsupported(f"callee {a}: prototype and placement differ")
        args = []
        for st, pt in zip(place, ptypes):
            v = sp.arg_var(st)
            if v is None:
                raise Unsupported(f"callee {a}: argument storage {st}")
            types.setdefault(v, sp.storage_type(st) if hasattr(sp, "storage_type") else
                             "f32" if st.startswith("fr") else "u32")
            val = Var(v)
            if job.kind(pt) == "f" and types[v] not in FLOATS:
                # a float passed in an integer slot (a stack word): its bits, not its value converted
                val = Call(sp.u2f, [val])
                if pt != "f32":
                    val = Cast(pt, val)
            elif pt != types[v]:
                val = Cast(pt, val)
            args.append(val)
        call = Call(name, args)
        return Call("__result", [call, Num(0 if rkind == "v" else 1 if rkind == "f" else 2), Raw(e.fn)])
    map_exprs(body.body, lambda e: rewrite(e, conv))

    # the result: r0 = Name(...) (an integer), fr0's bits for a float, nothing for void
    def res(e):
        if isinstance(e, Call) and e.fn == "__result":
            call, kind, fn = e.args
            if kind.v == 0:
                return Call("__void", [call])
            if kind.v == 1:
                return Call(sp.f2u, [call]) if fn.text == sp.call_fns[0] else call
            rt = job.callee_ret.get(call.fn, "u32")
            return call if rt == "u32" else Cast("u32", call)
        return e
    map_exprs(body.body, lambda e: rewrite(e, res))
    # last first: an insertion must not move the statements still to visit in the same list
    for stmts, k, s in reversed(list(all_stmts(body.body))):
        if isinstance(s, Assign) and isinstance(s.rhs, Call) and s.rhs.fn == "__void":
            stmts[k] = ExprS(s.rhs.args[0])
            # what the original reads of that register afterwards is the caller's own value: not modelled
            stmts.insert(k + 1, Assign(s.lhs, "=", Var("__void_result")))
        elif isinstance(s, ExprS) and isinstance(s.e, Call) and s.e.fn == "__void":
            stmts[k] = ExprS(s.e.args[0])
        elif isinstance(s, Return) and isinstance(s.e, Call) and s.e.fn == "__void":
            stmts[k] = ExprS(s.e.args[0])
            stmts.insert(k + 1, Return(Var("__void_result")))
    if "__void" in calls_used(body.body):
        raise Unsupported("a void call's result in an expression")


def frame(body, job, types):
    """Step 6: the frame's words as locals, the caller's words as the stack parameters."""
    sp = job.spec
    _frame_locals(body, job, types)
    slots = {}          # offset -> set of access types

    def scan(e):
        for x in flow.walk(e):
            if isinstance(x, Call) and isinstance(x.fn, str) and (x.fn in sp.loads or x.fn in sp.stores):
                b, c = flow.linear(x.args[0])
                if isinstance(b, Var) and b.name == "__sp":
                    slots.setdefault(c, set()).add(sp.loads.get(x.fn) or sp.stores[x.fn])
    for e in every_expr(body.body):
        scan(e)
    if not slots and "__sp" not in names_used(body.body):
        return
    stack_params = {off: raw for raw, st, off in job.raw_params if st == "stack"}
    names = {}
    for off, kinds in sorted(slots.items()):
        signed = off - (1 << 32) if off & 0x80000000 else off
        if len(kinds) > 1 and kinds != {"u32", "f32"}:
            raise Unsupported(f"stack slot {signed} read and written with different widths")
        if signed >= 0:
            if signed not in stack_params:
                raise Unsupported(f"caller stack word {signed} that is no parameter")
            names[off] = ("param", stack_params[signed], kinds)
            continue
        if signed % 4 and kinds & {"u32", "f32"}:
            raise Unsupported("an unaligned stack word")
        v = f"__slot{-signed}"
        types[v] = "f32" if kinds == {"f32"} else kinds.pop() if len(kinds) == 1 else "u32"
        names[off] = ("slot", v, kinds)
    # overlapping slots (a word at -8 and a byte at -6) are not handled
    spans = sorted((off - (1 << 32) if off & 0x80000000 else off, _size(types.get(n[1], "u32")))
                   for off, n in names.items() if n[0] == "slot")
    for (a, sa), (b, _) in zip(spans, spans[1:]):
        if a + sa > b:
            raise Unsupported("overlapping stack slots")

    def access(e):
        if isinstance(e, Call) and isinstance(e.fn, str) and e.fn in sp.loads:
            b, c = flow.linear(e.args[0])
            if isinstance(b, Var) and b.name == "__sp":
                kind, n, _ = names[c]
                if kind == "param":
                    p, want = job.stack_param(n), sp.loads[e.fn]
                    if job.stack_param_kind(n) == "f":
                        return p if want == "f32" else Call(sp.f2u, [p]) if want == "u32" else Cast(want, Call(
                            sp.f2u, [p]))
                    return p if want == "u32" else Call(sp.u2f, [p]) if want == "f32" else Cast(want, p)
                want, have = sp.loads[e.fn], types[n]
                if want == have:
                    return Var(n)
                if want == "f32":
                    return Call(sp.u2f, [Var(n)])
                if want == "u32" and have == "f32":
                    return Call(sp.f2u, [Var(n)])
                return Var(n)
        return e

    for stmts, k, s in list(all_stmts(body.body)):
        if isinstance(s, ExprS) and isinstance(s.e, Call) and s.e.fn in sp.stores:
            b, c = flow.linear(s.e.args[0])
            if isinstance(b, Var) and b.name == "__sp":
                kind, n, _ = names[c]
                if kind == "param":
                    raise Unsupported("a store to a stack parameter")
                val = s.e.args[1]
                want, have = sp.stores[s.e.fn], types[n]
                if want == "f32" and have == "u32":
                    val = Call(sp.f2u, [val])
                elif want == "u32" and have == "f32":
                    val = Call(sp.u2f, [val])
                stmts[k] = Assign(Var(n), "=", val)
    map_exprs(body.body, lambda e: rewrite(e, access))
    if "__sp" in names_used(body.body):
        raise Unsupported("a stack address used as a value (a local passed by pointer)")


SCALAR_LOCAL = {"u16": 2, "s16": 2, "u32": 4, "s32": 4, "int": 4, "f32": 4}


def _frame_locals(body, job, types):
    """A frame address the code uses as a T * (passed to a callee, mostly), T a struct the headers lay out or a
    16/32-bit scalar: a local of type T there. Every frame address inside it becomes (u32)&local + off, so its words
    stay memory (loads and stores the dataflow never moves across a call) until the field pass names them."""
    sp = job.spec
    found, slot_type, slot_value = {}, {}, {}

    def at_sp(e):
        b, c = flow.linear(e)
        if isinstance(b, Var) and b.name == "__sp":
            return c - (1 << 32) if c & 0x80000000 else c
        return None

    def look(e):
        for x in flow.walk(e):
            if isinstance(x, Cast) and x.type.rstrip().endswith("*"):
                t = re.sub(r"\s+", " ", re.sub(r"\b(?:const|volatile)\b", "", x.type.rstrip()[:-1]).strip())
                s = at_sp(x.e)
                if s is not None:
                    found.setdefault(s, set()).add(t)
                elif isinstance(x.e, Call) and sp.loads.get(x.e.fn) == "u32" and at_sp(x.e.args[0]) is not None:
                    # a stack argument: the call reads its word back as a T *
                    slot_type.setdefault(at_sp(x.e.args[0]), set()).add(t)
            if isinstance(x, Call) and sp.stores.get(x.fn) == "u32" and at_sp(x.args[0]) is not None:
                s = at_sp(x.args[1])
                if s is not None:
                    slot_value.setdefault(at_sp(x.args[0]), set()).add(s)
    for e in every_expr(body.body):
        look(e)
    for w, vals in slot_value.items():
        for s in vals:
            for t in slot_type.get(w, ()):
                found.setdefault(s, set()).add(t)
    if not found:
        return
    regions = []
    for s, ts in sorted(found.items()):
        if s >= 0:
            continue
        t = ts.pop() if len(ts) == 1 else None
        if t is None:
            raise Unsupported(f"frame word {s} used as {len(ts)} pointer types")
        st = job.types.struct(t) if job.types is not None and "*" not in t else None
        size = st.size if st is not None else SCALAR_LOCAL.get(t, 4 if t.endswith("*") else None)
        if not size:
            raise Unsupported(f"frame word {s} used as a {t} *")
        if s + size > 0:
            raise Unsupported(f"a {t} at frame word {s} runs past the frame")
        if regions and s < regions[-1][1]:
            if s + size <= regions[-1][1]:
                continue        # inside the one before: its member
            raise Unsupported("frame locals that overlap")
        regions.append((s, s + size, t))
    if not regions:
        return
    # a scalar local passed by pointer whose next frame word is read here but never written (a save aside): the
    # callee fills it too, so the local is an array (not handled)
    reads, writes = set(), set()
    for e in every_expr(body.body):
        for x in flow.walk(e):
            if isinstance(x, Call) and x.fn in sp.loads and at_sp(x.args[0]) is not None:
                reads.add(at_sp(x.args[0]))
            if isinstance(x, Call) and x.fn in sp.stores and at_sp(x.args[0]) is not None and not (
                    isinstance(x.args[1], Var) and x.args[1].name.startswith("__in_")):
                writes.add(at_sp(x.args[0]))
    starts = {lo for lo, _, _ in regions}
    for lo, hi, t in regions:
        if t in SCALAR_LOCAL and hi < 0 and hi not in starts and hi in reads and hi not in writes:
            raise Unsupported(f"a {t} at frame word {lo} passed by pointer: the callee fills the words after it "
                              "(an array local)")
    names = {}
    for lo, hi, t in regions:
        v = f"__frame{-lo}"
        types[v] = t
        names[lo] = v

    def sub(e):
        if isinstance(e, (Var, Bin)):
            b, c = flow.linear(e)
            if isinstance(b, Var) and b.name == "__sp":
                s = c - (1 << 32) if c & 0x80000000 else c
                for lo, hi, t in regions:
                    if lo <= s < hi:
                        base = Cast("u32", Unary("&", Var(names[lo])))
                        return base if s == lo else Bin("+", base, Num(s - lo, str(s - lo)))
        return e
    map_exprs(body.body, lambda e: topdown(e, sub))


def _size(t):
    return {"u8": 1, "s8": 1, "u16": 2, "s16": 2}.get(t, 4)


def exit_handover(body, job, types):
    """The values given back at exit must be the entry ones (then nothing is written); __in_ reads left are the
    runtime's register."""
    sp = job.spec
    for stmts, k, s in list(all_stmts(body.body)):
        if isinstance(s, Assign) and isinstance(s.lhs, Var) and s.lhs.name.startswith("__out_"):
            reg = s.lhs.name[6:]
            if isinstance(s.rhs, Var) and s.rhs.name == f"__in_{reg}":
                stmts[k] = None
            else:
                raise Unsupported(f"{reg} differs at exit from its entry value")
    filter_stmts(body.body, lambda s: s is not None)
    for n in [x for x in names_used(body.body) if x.startswith("__in_")]:
        get, idx = sp.handover_call(n[5:])
        map_exprs(body.body, lambda e, n=n, get=get, idx=idx: rewrite(
            e, lambda x: Call(get, [Num(idx, str(idx))]) if isinstance(x, Var) and x.name == n else x))


def memory(body, job):
    """Step 7: accesses at named globals and pool words by name or value, every other access by a pointer."""
    sp, names = job.spec, job.names
    ram = getattr(sp, "ram", GUEST_RAM)
    mirrors = getattr(sp, "mirrors", {})

    def unmirror(v):
        """(canonical address, op, k) of a mirror form of a RAM address (target.json address_mirrors): the mirror
        is `canonical op k`, the same memory. None for any other value."""
        m = mirrors.get(v >> 24)
        if m is None:
            return None
        c = (m[0] << 24) | (v & 0xFFFFFF)
        return (c,) + m[1:] if ram[0] <= c < ram[1] else None

    def lit_addr(a):
        if not flow.is_int(a):
            return None
        m = unmirror(a.v)
        return m[0] if m else a.v

    def load(e):
        if isinstance(e, Call) and isinstance(e.fn, str) and e.fn in sp.loads:
            t = sp.loads[e.fn]
            v = lit_addr(e.args[0])
            if v is not None:
                w = sp.width[e.fn]
                nm = names.access(w, v, pointer_ok=True)
                if nm:
                    hit = names.global_at(v)[1]
                    if "*" in hit[1] or getattr(job, "pointer", lambda _: False)(hit[1]):
                        return Cast(t, Var(nm))
                    return Cast(t, Var(nm)) if names.kind(hit[1]) in "ch" and t != hit[1] else Var(nm)
                from ..draft import pool_value
                pv = pool_value(names, w, v)
                if pv:
                    return cast.parse_expr(pv, job.typedefs)
                inside = interior(v)
                if inside is not None:
                    return Unary("*", Cast(t + " *", inside))
                raise Unsupported(f"a read at 0x{v:08x}: no global or pool word there")
            return Unary("*", Cast(t + " *", topdown(e.args[0], load)))
        if flow.is_int(e) and ram[0] <= e.v < ram[1]:
            nm = names.value(e.v)
            if nm is None:
                inside = interior(e.v)
                if inside is None:
                    raise Unsupported(f"address 0x{e.v:08x} as a value: no name")
                return inside
            return Cast("u32", cast.parse_expr(nm, job.typedefs))
        m = unmirror(e.v) if flow.is_int(e) else None
        if m:  # a mirror form as a value: the same bits from the name (a number when nothing is named there)
            c, op, k = m
            nm = names.value(c)
            base = Cast("u32", cast.parse_expr(nm, job.typedefs)) if nm else interior(c)
            return Bin(op, base, Num(k, f"0x{k:x}u")) if base is not None else e
        return e

    def interior(v):
        """An address inside a struct global: (u32)&g + off, which the field pass names as a member; inside an
        array global: (u32)g + off."""
        hit = names.global_at(v)
        if not hit or hit[0] == v:
            return None
        base, (name, ctype, dims) = hit
        if not dims and job.types.struct(ctype) is None:
            return None
        return Bin("+", Cast("u32", Var(name) if dims else Unary("&", Var(name))), Num(v - base, f"0x{v - base:x}"))

    for stmts, k, s in list(all_stmts(body.body)):
        if isinstance(s, ExprS) and isinstance(s.e, Call) and s.e.fn in sp.stores:
            t = sp.stores[s.e.fn]
            addr, val = s.e.args
            v = lit_addr(addr)
            val = topdown(val, load)
            if hasattr(sp, "store_value"):
                val = sp.store_value(s.e.fn, val)
            if t not in ("u32", "f32"):
                val = Cast(t, val)
            if v is not None:
                nm = names.access(sp.width[s.e.fn], v, pointer_ok=True)
                if not nm:
                    raise Unsupported(f"a write at 0x{v:08x}: no global there")
                hit = names.global_at(v)[1]
                if hit[1] != t and "*" not in hit[1]:
                    val = Cast(hit[1], val)
                elif "*" in hit[1]:
                    val = Cast(hit[1], val)
                stmts[k] = Assign(cast.parse_expr(nm, job.typedefs), "=", val)
            else:
                stmts[k] = Assign(Unary("*", Cast(t + " *", topdown(addr, load))), "=", val)
    map_exprs(body.body, lambda e: topdown(e, load))


def topdown(e, fn):
    """fn on each node before its children (a node fn replaces is not visited again)."""
    new = fn(e)
    if new is not e:
        return new
    if isinstance(e, Call):
        return Call(e.fn if isinstance(e.fn, str) else topdown(e.fn, fn), [topdown(a, fn) for a in e.args])
    if isinstance(e, Unary):
        return Unary(e.op, topdown(e.e, fn))
    if isinstance(e, Cast):
        return Cast(e.type, topdown(e.e, fn))
    if isinstance(e, Bin):
        return Bin(e.op, topdown(e.a, fn), topdown(e.b, fn))
    if isinstance(e, Cond):
        return Cond(topdown(e.c, fn), topdown(e.a, fn), topdown(e.b, fn))
    if isinstance(e, Index):
        return Index(topdown(e.a, fn), topdown(e.i, fn))
    if isinstance(e, Member):
        return Member(topdown(e.e, fn), e.name, e.arrow)
    return e


SIMPLIFY_PURE = {}


def peephole(body, sp):
    """u2f(f2u(x)) -> x and back; (u32)(u32)x -> (u32)x."""
    def p(e):
        if isinstance(e, Call) and e.fn in (sp.u2f, sp.f2u) and len(e.args) == 1 and isinstance(e.args[0], Call) and \
                e.args[0].fn == (sp.f2u if e.fn == sp.u2f else sp.u2f):
            return e.args[0].args[0]
        if isinstance(e, Cast) and isinstance(e.e, Cast) and e.type == e.e.type:
            return e.e
        return e
    map_exprs(body.body, lambda e: rewrite(e, p))


INT32 = {"u32", "s32", "int", "unsigned", "unsigned int"}


def type_of(e, job, types):
    """The C type of an expression when it is plain (a variable, parameter, cast, call or member), else None."""
    from .fields import typeof
    t = typeof(e, job, types)
    return t if isinstance(t, str) else None


def _int32(job, t):
    if t is None:
        return False
    if t in INT32:
        return True
    try:
        return "*" not in t and job.kind(t) == "i" and job.size(t) == 4 and not getattr(
            job, "pointer", lambda _: False)(t)
    except Exception:  # noqa: BLE001
        return False


def tidy(body, job, types):
    """Casts the lift added that change nothing: (T)(u32)x for an x of type T (T 32 bits: an integer or a
    pointer), (s32)n of a constant, and an integer argument cast to the parameter's 32-bit integer type."""
    def same(t, u):
        return t is not None and u is not None and re.sub(r"\s+", "", t) == re.sub(r"\s+", "", u)

    def ptr(t):
        return t is not None and ("*" in t or getattr(job, "pointer", lambda _: False)(t))

    def t1(e):
        if isinstance(e, Cast) and isinstance(e.e, Cast) and e.e.type in INT32 and e.type != e.e.type:
            inner = type_of(e.e.e, job, types)
            # (T)(u32)x is (T)x for T an integer or pointer (the same bits); for a float T, only when the middle
            # cast keeps x's signedness ((f32)(s32)x of a u32 x converts another value)
            if inner is not None and (ptr(inner) or _int32(job, inner)) and \
                    (e.type not in FLOATS or same(inner, e.e.type)):
                e = Cast(e.type, e.e.e)
                if same(inner, e.type):
                    return e.e
        if isinstance(e, Bin) and e.op in ("==", "!=") and isinstance(e.a, Cast) and e.a.type in INT32 and \
                flow.is_int(e.b) and e.b.v == 0 and ptr(type_of(e.a.e, job, types)):
            return Bin(e.op, e.a.e, Var("NULL"))
        # x == (u32)y for x and y both signed 32-bit: the same bits compared, no signed/unsigned mismatch
        if isinstance(e, Bin) and e.op in ("==", "!="):
            for c, o, flip in ((e.a, e.b, False), (e.b, e.a, True)):
                if isinstance(c, Cast) and c.type == "u32" and type_of(c.e, job, types) in ("s32", "int") and \
                        type_of(o, job, types) in ("s32", "int") and not flow.is_int(o):
                    return Bin(e.op, o, c.e) if flip else Bin(e.op, c.e, o)
        if isinstance(e, Cast) and isinstance(e.e, Cast) and e.e.type == "u32":
            inner = type_of(e.e.e, job, types)
            if same(inner, e.type) and (ptr(e.type) or _int32(job, e.type)):
                return e.e.e
        if isinstance(e, Cast) and flow.is_int(e.e) and _int32(job, e.type):
            v = e.e.v & flow.U32
            if e.type in ("s32", "int") or (e.type not in INT32):
                v = v - (1 << 32) if v & 0x80000000 else v
                return Num(v, str(v))
            return e.e
        if isinstance(e, Cast) and same(type_of(e.e, job, types), e.type):
            return e.e
        # a narrow cast of a constant: the value it gives (an int either way, by the integer promotions)
        if isinstance(e, Cast) and flow.is_int(e.e) and e.type in NARROW:
            bits, signed = NARROW[e.type]
            v = e.e.v & ((1 << bits) - 1)
            if signed and v >> (bits - 1):
                v -= 1 << bits
            return Num(v, str(v) if -10 < v < 10 else f"0x{v:x}" if v > 0 else str(v))
        # a bit copy of a word read from memory: the word read as the other type (u2f(*(u32 *)p) is *(f32 *)p)
        if isinstance(e, Call) and e.fn in bitcopy and len(e.args) == 1 and isinstance(e.args[0], Unary) and \
                e.args[0].op == "*" and isinstance(e.args[0].e, Cast) and e.args[0].e.type == bitcopy[e.fn][0]:
            return Unary("*", Cast(bitcopy[e.fn][1], e.args[0].e.e))
        # !(a == b) is a != b (floats too: a NaN is unequal both ways)
        if isinstance(e, Unary) and e.op == "!" and isinstance(e.e, Bin) and e.e.op in ("==", "!="):
            return Bin("!=" if e.e.op == "==" else "==", e.e.a, e.e.b)
        # a function's address as a function-pointer type: no u32 between
        if isinstance(e, Cast) and isinstance(e.e, Cast) and e.e.type == "u32" and isinstance(e.e.e, Var) and \
                e.e.e.name in functions and getattr(job, "pointer", lambda _: False)(e.type):
            return Cast(e.type, e.e.e)
        # (u32)x == c for a 32-bit signed x: x == c, the same bits compared
        if isinstance(e, Bin) and e.op in ("==", "!=") and isinstance(e.a, Cast) and e.a.type == "u32" and \
                flow.is_int(e.b) and type_of(e.a.e, job, types) in ("s32", "int"):
            v = e.b.v & flow.U32
            v = v - (1 << 32) if v & 0x80000000 else v
            return Bin(e.op, e.a.e, Num(v, str(v) if -10 < v < 10 else f"0x{v:x}" if v > 0 else str(v)))
        if isinstance(e, Call) and isinstance(e.fn, str) and e.fn in job.callee_ret:
            args = []
            for a in e.args:
                if isinstance(a, Cast) and _int32(job, a.type) and _int32(job, type_of(a.e, job, types)):
                    a = a.e
                elif isinstance(a, Cast) and implicit(type_of(a.e, job, types), a.type):
                    a = a.e
                args.append(a)
            return Call(e.fn, args)
        return e

    functions = set(job.names.functions.values()) if getattr(job, "names", None) is not None else set()
    bitcopy = {job.spec.u2f: ("u32 *", "f32 *"), job.spec.f2u: ("f32 *", "u32 *")}

    def implicit(have, want):
        """A pointer conversion C makes by itself: T * to const T *, any object pointer to (const) void *."""
        if not (isinstance(have, str) and isinstance(want, str) and "*" in have and want.rstrip().endswith("*")):
            return False
        w = re.sub(r"\s+", "", want)
        if w in ("void*", "constvoid*"):
            return True
        return re.sub(r"\s+", "", "const " + have) == w or re.sub(r"\bconst\b|\s+", "", have) == re.sub(
            r"\bconst\b|\s+", "", want) and "const" not in have
    map_exprs(body.body, lambda e: rewrite(e, t1))
    for stmts, k, s in list(all_stmts(body.body)):
        if isinstance(s, Return) and s.e is not None:
            s.e = plain(rewrite(s.e, t1))
        elif isinstance(s, Assign):
            s.rhs = plain(s.rhs)
            # v = v op x: v op= x
            r = s.rhs
            if s.op == "=" and isinstance(s.lhs, Var) and isinstance(r, Bin) and r.op in COMPOUND and \
                    isinstance(r.a, Var) and r.a.name == s.lhs.name:
                stmts[k] = Assign(s.lhs, r.op + "=", plain(r.b))

    def args(e):
        if isinstance(e, Call) and isinstance(e.fn, str) and e.fn in job.callee_ret:
            return Call(e.fn, [plain(a) for a in e.args])
        return e
    map_exprs(body.body, lambda e: rewrite(e, args))


COMPOUND = ("+", "-", "*", "&", "|", "^", "<<", ">>")
FLOATS = ("f32", "f64", "float", "double")
NARROW = {"u8": (8, False), "s8": (8, True), "u16": (16, False), "s16": (16, True)}


def plain(e):
    """A constant converted to its target's type (returned, assigned, passed): no suffix, small ones in decimal."""
    if isinstance(e, Num) and isinstance(e.v, int) and 0 <= e.v <= 0x7FFFFFFF:
        return Num(e.v, str(e.v) if e.v < 10 else f"0x{e.v:x}")
    return e


# ---- locals: names, declarations, initialisation ---------------------------------------------------------------

def assigned_before_use(body):
    """The variables some read may reach before any write (they get an initializer)."""
    maybe = set()
    ALL = None  # 'unreachable': every variable counts as assigned

    def inter(a, b):
        if a is ALL:
            return b
        if b is ALL:
            return a
        return a & b

    def use(e, have):
        if have is ALL:
            return
        for n in flow.reads(e):
            if n not in have:
                maybe.add(n)

    def block(stmts, have, ctx):
        for s in stmts:
            have = stmt(s, have, ctx)
        return have

    def stmt(s, have, ctx):
        if isinstance(s, (Assign, ExprS)):
            for e in flow.exprs(s):
                use(e, have)
            if isinstance(s, Assign) and isinstance(s.lhs, Var) and s.op != "=":
                use(s.lhs, have)
            return have if have is ALL else have | flow.defs(s)
        if isinstance(s, Block):
            return block(s.body, have, ctx)
        if isinstance(s, If):
            use(s.c, have)
            a = stmt(s.t, have, ctx)
            b = stmt(s.e, have, ctx) if s.e is not None else have
            return inter(a, b)
        if isinstance(s, While):
            use(s.c, have)
            brk = []
            stmt(s.body, have, {"brk": brk})
            return have
        if isinstance(s, For):
            if s.init is not None:
                have = stmt(s.init, have, ctx)
            if s.c is not None:
                use(s.c, have)
            brk = []
            end = stmt(s.body, have, {"brk": brk})
            if s.step is not None:
                stmt(s.step, end if end is not ALL else have, ctx)
            if s.c is None:
                out = ALL
                for b in brk:
                    out = inter(out, b)
                return out
            return have
        if isinstance(s, DoWhile):
            brk = []
            end = stmt(s.body, have, {"brk": brk})
            use(s.c, end if end is not ALL else have)
            out = end
            for b in brk:
                out = inter(out, b)
            return have if out is ALL else out
        if isinstance(s, Switch):
            use(s.e, have)
            brk = []
            block(s.body.body, have, {"brk": brk})
            return have
        if isinstance(s, Return):
            if s.e is not None:
                use(s.e, have)
            return ALL
        if isinstance(s, cast.Break):
            if ctx.get("brk") is not None:
                ctx["brk"].append(have)
            return ALL
        if isinstance(s, cast.Continue):
            return ALL
        if isinstance(s, Case):
            return have
        return have
    block(body.body, set(), {})
    return maybe


GAP_NAME = re.compile(r"r\d{1,2}|fr\d{1,2}|f(?:[0-9]|1[0-5])|dr\d{1,2}|xf\d{1,2}|fpul|macl|mach|gbr|args|"
                      r"[a-z]{1,4}Var\d+|in_\w+|unaff_\w+|extraout_\w+|local_[0-9a-f]+|param_\w+")
C_WORDS = {"int", "char", "short", "long", "float", "double", "void", "struct", "union", "enum", "if", "else", "for",
           "while", "do", "switch", "case", "default", "break", "continue", "return", "goto", "sizeof", "static",
           "const", "register", "auto", "extern", "volatile", "signed", "unsigned", "typedef"}


def name_locals(body, types, params, avoid=frozenset()):
    """Registers and slots get neutral names: n1, n2.. (integers), x1.. (floats), in order of first appearance."""
    order = []
    for e in every_expr(body.body):
        for x in flow.walk(e):
            if isinstance(x, Var) and x.name in types and x.name not in order:
                order.append(x.name)
    taken = set(params)
    avoid = set(avoid) | C_WORDS | names_used(body.body)
    ren, k = {}, {"n": 0, "x": 0}
    for v in order:
        t = types[v]
        if "*" in t or re.fullmatch(r"[A-Z]\w*", t):
            base = re.sub(r"\b(?:const|struct|union|volatile)\b|\*", "", t).strip()
            stem = (base[:1].lower() + base[1:]) if re.fullmatch(r"[A-Z]\w*", base) else "p"
            nm, i = stem, 1
            while nm in taken or nm in avoid or GAP_NAME.fullmatch(nm):
                i += 1
                nm = f"{stem}{i}"
            ren[v] = nm
            taken.add(nm)
            continue
        p = "x" if t == "f32" else "n"
        while True:
            k[p] += 1
            nm = f"{p}{k[p]}"
            if nm not in taken:
                break
        ren[v] = nm
        taken.add(nm)

    def rn(e):
        return Var(ren[e.name]) if isinstance(e, Var) and e.name in ren else e
    for stmts, k2, s in list(all_stmts(body.body)):
        if isinstance(s, Assign) and isinstance(s.lhs, Var) and s.lhs.name in ren:
            stmts[k2] = Assign(Var(ren[s.lhs.name]), s.op, s.rhs)
    map_exprs(body.body, lambda e: rewrite(e, rn))
    return {ren[v]: types[v] for v in order}


# ---- the whole function -----------------------------------------------------------------------------------------

def _trace(job, step, body):
    if getattr(job, "trace", None):
        job.trace(step, body)


def _split(body, types):
    from . import webs
    names = {n for n in types if not n.startswith(("__in_", "__out_", "__sp", "__void")) and types[n] != "TcArgs"}
    webs.split(body, names, types)


def lift(text, job):
    from ..raw_names import mask
    from ..sync_text import exported
    found = exported(mask(text)[0])
    if not found:
        raise Unsupported("no exported function")
    start, close, brace, end = found
    helpers = text[:start]
    helpers = re.sub(r'#\s*include\s+"[^"]*"\s*\n', "", helpers)
    body = cast.parse_body(text[brace:end + 1], job.typedefs)
    sp = job.spec
    sp.check(body)
    if hasattr(sp, "prepare"):  # the target's raw C in the form the passes take (e.g. a constant stack pointer)
        sp.prepare(body)
    types = declarations(body)
    job.raw_types = {raw: _raw_type(text[start:brace], raw) for raw, _, _ in job.raw_params}
    if names_used(body.body) & {"goto"}:
        raise Unsupported("goto")
    bind_params(body, job, types)
    _trace(job, "bind_params", body)
    strip_state(body, job, types)
    _trace(job, "strip_state", body)
    calls(body, job, types)
    _trace(job, "calls", body)
    for stmts, k, s in list(all_stmts(body.body)):
        stmts[k] = flow.normalize(s) if isinstance(s, Assign) else s
    keep = {n for n in types if n.startswith("__out_")}
    flow.simplify(body, sp, keep)
    _trace(job, "flow.simplify", body)
    _split(body, types)
    flow.simplify(body, sp, keep)
    frame(body, job, types)
    _trace(job, "frame", body)
    peephole(body, sp)
    flow.simplify(body, sp, keep)
    _trace(job, "flow.simplify", body)
    exit_handover(body, job, types)
    _trace(job, "exit_handover", body)
    peephole(body, sp)
    _split(body, types)
    flow.simplify(body, sp)
    _trace(job, "flow.simplify", body)
    returns(body, job, types)
    _trace(job, "returns", body)
    memory(body, job)
    _trace(job, "memory", body)
    peephole(body, sp)
    tidy(body, job, types)
    _trace(job, "tidy", body)
    if getattr(job, "types", None) is not None:
        from . import fields
        for _ in range(10):
            a = fields.fields(body, job, types)
            b = fields.pointer_locals(body, job, types) | fields.indexes(body, job, types)
            tidy(body, job, types)
            if not (a or b):
                break
        fields.constants(body, job, types)
        _trace(job, "fields", body)
    left = {n for n in names_used(body.body) if n.startswith(("__in_", "__out_", "__sp", "__void"))}
    if left:
        raise Unsupported(f"left: {sorted(left)}")
    if calls_used(body.body) & (set(sp.loads) | set(sp.stores) | set(sp.call_fns)):
        raise Unsupported("raw runtime calls left")
    params = [n for _, n in job.proto_params]
    local_types = name_locals(body, {n: t for n, t in types.items() if n not in params}, params,
                              getattr(job, "avoid", ()))
    init = assigned_before_use(body)
    decl = []
    for t in sorted({t for t in local_types.values()}, key=lambda t: ("*" in t, t != "u32", t)):
        vs = [v for v, tt in local_types.items() if tt == t]
        if "*" in t:
            base = t.rstrip()[:-1].rstrip()
            decl += [f"    {base} *{v}{' = NULL' if v in init else ''};" for v in vs]
            continue
        aggregate = job.types is not None and job.types.struct(t) is not None
        items = [(v, (cast.Num(0.0, "0.0f") if t == "f32" else Num(0, "0")) if v in init and not aggregate else None,
                  "") for v in vs]
        decl += cast.stmt_lines(Decl(t, items))
    unused = [n for n in params if n not in names_used(body.body)]
    lines = [f"    (void){n};" for n in unused]
    lines += decl
    if decl:
        lines.append("")
    lines += cast.body_lines(body.body, "    ")
    while lines and lines[-1].strip() == "return;":
        lines.pop()
    used = calls_used(body.body)
    sources = getattr(sp, "helper_src", {})
    # the bit casts the lift added that the raw C does not define, first (a kept helper may call them)
    missing = [n for n in sorted(sources) if not re.search(rf"^static\s[^;{{]*\b{n}\s*\(", helpers, re.M)]
    out_helpers = keep_helpers("".join(sources[n] + "\n" for n in missing) + helpers, used)
    return ("#include \"game.h\"\n\n" + out_helpers + job.proto.strip().rstrip(";") + "\n{\n" + "\n".join(lines) +
            "\n}\n")


def _raw_type(header, name):
    m = re.search(rf"\b([A-Za-z_]\w*(?:\s*\*)?)\s+{re.escape(name)}\b", header)
    return m.group(1) if m else "u32"


def returns(body, job, types):
    """return E as the prototype returns it."""
    rk = job.ret_kind
    for stmts, k, s in reversed(list(all_stmts(body.body))):
        if isinstance(s, Return):
            if rk == "v":
                if s.e is not None and flow.has_call(s.e, job.spec):
                    stmts[k] = ExprS(s.e)
                    stmts.insert(k + 1, Return(None))
                else:
                    s.e = None
            elif s.e is None:
                raise Unsupported("the prototype returns a value the raw C does not")
            else:
                pt = job.proto_ret
                if pt != job.raw_ret:
                    s.e = Cast(pt, s.e)


def keep_helpers(text, used):
    """The static helpers among text the body calls (and the helpers they call), unchanged."""
    from ..raw_names import mask
    code = mask(text)[0]
    found = {}
    for m in re.finditer(r"^static\s+[^;{]*?\b([A-Za-z_]\w*)\s*\([^;{]*\)\s*\{", code, re.M):
        depth, j = 0, m.end() - 1
        while j < len(code):
            depth += {"{": 1, "}": -1}.get(code[j], 0)
            if depth == 0:
                break
            j += 1
        found[m.group(1)] = (m.start(), j + 1)
    need, todo = set(), [n for n in used if n in found]
    while todo:
        n = todo.pop()
        if n in need:
            continue
        need.add(n)
        a, b = found[n]
        todo += [x for x in re.findall(r"\b([A-Za-z_]\w*)\s*\(", code[a:b]) if x in found and x not in need]
    parts = [text[a:b].strip() for n, (a, b) in sorted(found.items(), key=lambda kv: kv[1][0]) if n in need]
    parts = [re.sub(r"/\*.*?\*/\s*", "", p, flags=re.S) for p in parts]
    return "".join(p + "\n" for p in parts) + ("\n" if parts else "")
