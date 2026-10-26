"""Dataflow over the tree (core/rtx/cast): liveness, forward substitution, dead stores and constant folding.

The tree is a function body after its target pass (core/rtx/lift): every variable is a plain C variable, every
memory access a call of the target's load or store functions (`rd32(a)`, `wr32(a, v)`), every call a call by name.
The passes never change what the function does:
- liveness is the usual backward may-analysis, over loops to a fixpoint (break, continue and return are followed);
- forward substitution moves `v = E;` into the one later statement of the same sequence that reads v, when nothing
  in between writes what E reads (nor, for an E that reads memory, any store or call) and v is dead after that read;
  a constant or a copy goes into every read up to the next write of either side;
- a dead store goes when its value has no call (a load may go: a read of memory has no effect);
- folding: integer +, -, &, |, ^, <<, >> of constants (u32 arithmetic), x + 0, (x + a) + b.
"""
from __future__ import annotations

from .cast import (Assign, Bin, Block, Break, Call, Case, Cast, Cond, Continue, Decl, DoWhile, ExprS, For, Goto, If,
                   Index, Label, Member, Num, Raw, Return, Switch, Unary, Var, While)

U32 = 0xFFFFFFFF


class Unsupported(Exception):
    """A form the passes do not handle: the function is left to the agents."""


class Target:
    """What the passes need to know about the target's runtime API (core/rtx/lift fills it)."""
    loads = {}        # {function name: C type} memory reads
    stores = {}       # {function name: C type} memory writes (address, value)
    pure = set()      # functions with no side effects that read no memory (bit casts, sign extension, ...)

    def is_load(self, fn):
        return fn in self.loads


# ---- what an expression reads and does --------------------------------------------------------------------------

def walk(e):
    """Every node of an expression, e first."""
    stack = [e]
    while stack:
        x = stack.pop()
        if x is None:
            continue
        yield x
        if isinstance(x, Call):
            stack += x.args
            if not isinstance(x.fn, str):
                stack.append(x.fn)
        elif isinstance(x, Unary):
            stack.append(x.e)
        elif isinstance(x, Cast):
            stack.append(x.e)
        elif isinstance(x, Bin):
            stack += [x.a, x.b]
        elif isinstance(x, Cond):
            stack += [x.c, x.a, x.b]
        elif isinstance(x, Index):
            stack += [x.a, x.i]
        elif isinstance(x, Member):
            stack.append(x.e)


def reads(e):
    return {x.name for x in walk(e) if isinstance(x, Var)}


def count(e, name):
    return sum(1 for x in walk(e) if isinstance(x, Var) and x.name == name)


def has_load(e, tg):
    return any(isinstance(x, Call) and isinstance(x.fn, str) and tg.is_load(x.fn) or
               isinstance(x, Unary) and x.op == "*" for x in walk(e))


def has_call(e, tg):
    """A call that may have an effect (a store, or any function that is not a load or pure)."""
    return any(isinstance(x, Call) and not (isinstance(x.fn, str) and (tg.is_load(x.fn) or x.fn in tg.pure))
               for x in walk(e))


def subst(e, env):
    """e with each Var in env replaced by its expression (a new tree; e is not changed)."""
    if isinstance(e, Var):
        return env.get(e.name, e)
    if isinstance(e, (Num, Raw)) or e is None:
        return e
    if isinstance(e, Call):
        return Call(e.fn if isinstance(e.fn, str) else subst(e.fn, env), [subst(a, env) for a in e.args])
    if isinstance(e, Unary):
        return Unary(e.op, subst(e.e, env))
    if isinstance(e, Cast):
        return Cast(e.type, subst(e.e, env))
    if isinstance(e, Bin):
        return Bin(e.op, subst(e.a, env), subst(e.b, env))
    if isinstance(e, Cond):
        return Cond(subst(e.c, env), subst(e.a, env), subst(e.b, env))
    if isinstance(e, Index):
        return Index(subst(e.a, env), subst(e.i, env))
    if isinstance(e, Member):
        return Member(subst(e.e, env), e.name, e.arrow)
    raise TypeError(e)


# ---- simple statements: what each one reads, writes and does ----------------------------------------------------

def simple(s):
    return isinstance(s, (Assign, ExprS))


def defs(s):
    if isinstance(s, Assign) and isinstance(s.lhs, Var):
        return {s.lhs.name}
    return set()


def uses(s):
    if isinstance(s, Assign):
        u = reads(s.rhs)
        if not isinstance(s.lhs, Var):
            u |= reads(s.lhs)
        elif s.op != "=":
            u.add(s.lhs.name)
        return u
    if isinstance(s, ExprS):
        return reads(s.e)
    raise TypeError(s)


def exprs(s):
    """The expressions a simple statement evaluates."""
    if isinstance(s, Assign):
        return [s.rhs] + ([] if isinstance(s.lhs, Var) else [s.lhs])
    return [s.e]


def operands(s):
    """The expressions evaluated before the statement's own action: a call's (or store's) arguments, else exprs."""
    out = []
    for e in exprs(s):
        out += e.args if isinstance(e, Call) else [e]
    return out


def effect(s, tg):
    """Whether the statement stores to memory or calls something with an effect."""
    return (isinstance(s, Assign) and not isinstance(s.lhs, Var)) or any(has_call(e, tg) for e in exprs(s))


def has_continue(s):
    """Whether a continue in s (outside loops nested in it) continues the loop around s."""
    if isinstance(s, Continue):
        return True
    if isinstance(s, (While, DoWhile, For)):
        return False
    if isinstance(s, Block):
        return any(has_continue(x) for x in s.body)
    if isinstance(s, If):
        return has_continue(s.t) or (s.e is not None and has_continue(s.e))
    if isinstance(s, Switch):
        return has_continue(s.body)
    return False


def normalize(s):
    """`v op= e` as `v = v op e`."""
    if isinstance(s, Assign) and s.op != "=" and isinstance(s.lhs, Var):
        return Assign(s.lhs, "=", Bin(s.op[:-1], Var(s.lhs.name), s.rhs))
    return s


# ---- liveness ---------------------------------------------------------------------------------------------------

class Liveness:
    """live-after sets of every statement (by id), and for an if / switch / do-while the set live right after its
    condition (head)."""

    def __init__(self):
        self.after, self.head = {}, {}

    def run(self, body, out=frozenset()):
        return self.block(body.body, set(out), {"brk": None, "cont": None})

    def block(self, stmts, out, ctx):
        live = set(out)
        for s in reversed(stmts):
            live = self.stmt(s, live, ctx)
        return live

    def stmt(self, s, out, ctx):
        self.after[id(s)] = set(out)
        if simple(s):
            return (out - defs(s)) | uses(s)
        if isinstance(s, Decl):
            live = set(out)
            for n, init, _ in s.items:
                live.discard(n)
                if init is not None and not isinstance(init, Raw):
                    live |= reads(init)
            return live
        if isinstance(s, Block):
            return self.block(s.body, out, ctx)
        if isinstance(s, If):
            t = self.stmt(s.t, out, ctx)
            e = self.stmt(s.e, out, ctx) if s.e is not None else set(out)
            self.head[id(s)] = t | e
            return t | e | reads(s.c)
        if isinstance(s, While):
            head = set(out) | reads(s.c)
            while True:
                body = self.stmt(s.body, head, {"brk": out, "cont": head})
                new = head | body
                if new == head:
                    return head
                head = new
        if isinstance(s, DoWhile):
            cond = set(out) | reads(s.c)
            while True:
                body = self.stmt(s.body, cond, {"brk": out, "cont": cond})
                new = set(out) | reads(s.c) | body
                if new == cond:
                    self.head[id(s)] = set(out) | body
                    return body
                cond = new
        if isinstance(s, For):
            head = set(out) | (reads(s.c) if s.c is not None else set())
            if s.c is None:
                head = set()
            while True:
                step_in = self.stmt(s.step, head, ctx) if s.step is not None else head
                body = self.stmt(s.body, step_in, {"brk": out, "cont": step_in})
                new = head | body
                if new == head:
                    break
                head = new
            if s.init is not None:
                return self.stmt(s.init, head, ctx)
            return head
        if isinstance(s, Switch):
            live = set(out)
            cases = []
            inner = {"brk": out, "cont": ctx["cont"]}
            stmts = s.body.body if isinstance(s.body, Block) else [s.body]
            for x in reversed(stmts):
                if isinstance(x, Case):
                    self.after[id(x)] = set(live)
                    cases.append((x, set(live)))
                    continue
                live = self.stmt(x, live, inner)
            head = set().union(*[c for _, c in cases]) if cases else set()
            if not any(c.v is None for c, _ in cases):
                head |= out
            self.head[id(s)] = head
            return head | reads(s.e)
        if isinstance(s, Return):
            return reads(s.e) if s.e is not None else set()
        if isinstance(s, Break):
            if ctx["brk"] is None:
                raise Unsupported("break outside a loop")
            return set(ctx["brk"])
        if isinstance(s, Continue):
            if ctx["cont"] is None:
                raise Unsupported("continue outside a loop")
            return set(ctx["cont"])
        if isinstance(s, Case):
            return set(out)
        if isinstance(s, (Goto, Label)):
            raise Unsupported("goto")
        raise TypeError(s)


# ---- folding ----------------------------------------------------------------------------------------------------

def is_int(e):
    return isinstance(e, Num) and isinstance(e.v, int)


def fold(e):
    """Constant folding (u32), bottom up."""
    if isinstance(e, Bin):
        a, b = fold(e.a), fold(e.b)
        e = Bin(e.op, a, b)
        if is_int(a) and is_int(b):
            x, y = a.v & U32, b.v & U32
            r = {"+": lambda: x + y, "-": lambda: x - y, "&": lambda: x & y, "|": lambda: x | y,
                 "^": lambda: x ^ y, "<<": lambda: x << y if y < 32 else None,
                 ">>": lambda: x >> y if y < 32 else None, "*": lambda: x * y}.get(e.op, lambda: None)()
            if r is not None:
                return Num(r & U32)
            # a comparison of two constants the raw C compares unsigned (u suffixes, or both small); == and != of
            # any two: the same 32 bits whether they compare signed or unsigned
            if e.op in ("==", "!=") or e.op in ("<", ">", "<=", ">=") and (
                    _unsigned(a) and _unsigned(b) or 0 <= a.v < 0x80000000 and 0 <= b.v < 0x80000000):
                r = {"==": x == y, "!=": x != y, "<": x < y, ">": x > y, "<=": x <= y, ">=": x >= y}[e.op]
                return Num(int(r))
        if e.op in ("+", "-") and is_int(b):
            if b.v == 0:
                return a
            # (x + c1) +- c2, (x - c1) +- c2 -> x + c
            if isinstance(a, Bin) and a.op in ("+", "-") and is_int(a.b):
                c = (a.b.v if a.op == "+" else -a.b.v) + (b.v if e.op == "+" else -b.v)
                return offset(a.a, c)
            if e.op == "+" and b.v & 0x80000000 and b.v != 0x80000000:
                return Bin("-", a, Num((-b.v) & U32))
        if e.op == "+" and is_int(a) and not is_int(b):
            return fold(Bin("+", b, a))
        if e.op in ("|", "^") and is_int(b) and b.v == 0:
            return a
        if e.op in ("<<", ">>") and is_int(b) and b.v == 0:
            return a
        return e
    if isinstance(e, Unary):
        return Unary(e.op, fold(e.e))
    if isinstance(e, Cast):
        return Cast(e.type, fold(e.e))
    if isinstance(e, Call):
        return Call(e.fn if isinstance(e.fn, str) else fold(e.fn), [fold(a) for a in e.args])
    if isinstance(e, Cond):
        return Cond(fold(e.c), fold(e.a), fold(e.b))
    if isinstance(e, Index):
        return Index(fold(e.a), fold(e.i))
    if isinstance(e, Member):
        return Member(fold(e.e), e.name, e.arrow)
    return e


def _unsigned(n):
    return isinstance(n.text, str) and n.text.rstrip("lL").endswith(("u", "U"))


def constant_ifs(stmts):
    """if (constant): the arm that runs, in place. Whether any was."""
    changed = False
    k = 0
    while k < len(stmts):
        s = stmts[k]
        if isinstance(s, If):
            c = fold(s.c)
            if isinstance(c, Unary) and c.op == "!" and is_int(c.e):
                c = Num(int(c.e.v == 0))
            if is_int(c):
                arm = s.t if c.v else s.e
                body = [] if arm is None else arm.body if isinstance(arm, Block) else [arm]
                stmts[k:k + 1] = body
                changed = True
                continue
        if isinstance(s, If):
            if isinstance(s.t, Block):
                changed |= constant_ifs(s.t.body)
            if isinstance(s.e, Block):
                changed |= constant_ifs(s.e.body)
            elif isinstance(s.e, If):
                tmp = [s.e]
                if constant_ifs(tmp):
                    changed = True
                    s.e = None if not tmp else tmp[0] if len(tmp) == 1 and isinstance(tmp[0], If) else Block(tmp)
        elif isinstance(s, Block):
            changed |= constant_ifs(s.body)
        elif isinstance(s, (While, DoWhile, For, Switch)):
            changed |= constant_ifs(s.body.body)
        k += 1
    return changed


def offset(x, c):
    """x + c (c an int, negative written as a subtraction)."""
    c = c & U32
    if c == 0:
        return x
    if c & 0x80000000:
        return Bin("-", x, Num((-c) & U32))
    return Bin("+", x, Num(c))


def linear(e):
    """(base expression or None, constant) when e is base + c, base - c or a constant."""
    if is_int(e):
        return None, e.v & U32
    if isinstance(e, Bin) and e.op in ("+", "-") and is_int(e.b):
        b, c = linear(e.a)
        return b, (c + (e.b.v if e.op == "+" else -e.b.v)) & U32
    if isinstance(e, Bin) and e.op == "+" and is_int(e.a):
        b, c = linear(e.b)
        return b, (c + e.a.v) & U32
    return e, 0


# ---- the passes over the whole body -----------------------------------------------------------------------------

def sequences(stmts):
    """(start, end) of each run of simple statements in a statement list."""
    i = 0
    while i < len(stmts):
        if simple(stmts[i]):
            j = i
            while j < len(stmts) and simple(stmts[j]):
                j += 1
            yield i, j
            i = j
        else:
            i += 1


def lists(node):
    """Every statement list of the tree, with the statement right after it that reads a condition once its run is
    over (if, switch, return; or a do-while's own condition for the end of its body), else None."""
    out = []

    def go(stmts, after):
        out.append((stmts, after))
        for k, s in enumerate(stmts):
            for child, tail in children(s):
                go(child, tail)

    def children(s):
        if isinstance(s, Block):
            return [(s.body, None)]
        if isinstance(s, If):
            return [(s.t.body, None)] + ([(s.e.body, None)] if isinstance(s.e, Block) else
                                         children(s.e) if isinstance(s.e, If) else [])
        if isinstance(s, (While, For)):
            return [(s.body.body, None)]
        if isinstance(s, DoWhile):
            return [(s.body.body, s)]
        if isinstance(s, Switch):
            return [(s.body.body, None)]
        return []
    go(node.body, None)
    return out


def head_expr(s):
    """The expression a compound statement evaluates once when reached (if/switch/return), else None."""
    if isinstance(s, If):
        return s.c
    if isinstance(s, Switch):
        return s.e
    if isinstance(s, Return):
        return s.e
    return None


def set_head(s, e):
    if isinstance(s, If):
        s.c = e
    elif isinstance(s, Switch):
        s.e = e
    elif isinstance(s, Return):
        s.e = e
    elif isinstance(s, DoWhile):
        s.c = e


def trivial(e):
    """A value cheap and safe to copy into every read: a constant, a variable, a variable plus a constant, or a cast
    of one of those."""
    if isinstance(e, (Num, Var)):
        return True
    if isinstance(e, Cast):
        return isinstance(e.e, Var) or isinstance(e.e, Cast) and isinstance(e.e.e, Var)
    if isinstance(e, Bin) and e.op in ("+", "-") and is_int(e.b) and (isinstance(e.a, Var) or isinstance(
            e.a, Cast) and isinstance(e.a.e, Var)):
        return True
    return False


def forward(body, tg, keep=frozenset()):
    """One round of forward substitution over every sequence. Returns whether anything changed. keep: variables
    whose assignments stay (their value is the result, e.g. a hand-over at exit)."""
    lv = Liveness()
    lv.run(body)
    lv.old = []
    changed = False
    for stmts, owner in lists(body):
        for i0, i1 in reversed(list(sequences(stmts))):  # last first: a deletion leaves the earlier runs in place
            changed |= _forward_run(stmts, i0, i1, owner, lv, tg, keep)
    return changed


def _replace(stmts, j, new, lv):
    """stmts[j] = new, keeping its live-after set (a substitution into it changes nothing live after it)."""
    old = stmts[j]
    lv.old.append(old)
    lv.after[id(new)] = lv.after[id(old)]
    stmts[j] = new


def _tail(stmts, i1, owner):
    """The compound statement whose head is evaluated right after the run ends, and the set live after that head."""
    if i1 < len(stmts) and head_expr(stmts[i1]) is not None:
        return stmts[i1]
    if i1 == len(stmts) and isinstance(owner, DoWhile) and not has_continue(owner.body):
        return owner
    return None


def _forward_run(stmts, i0, i1, owner, lv, tg, keep):
    changed = False
    i = i0
    while i < i1:
        s = stmts[i]
        if not (isinstance(s, Assign) and isinstance(s.lhs, Var) and s.op == "=") or s.lhs.name in keep:
            i += 1
            continue
        v, E = s.lhs.name, s.rhs
        er = reads(E)
        load, call = has_load(E, tg), has_call(E, tg)
        tail = _tail(stmts, i1, owner)
        # the statements after i up to (and including) the next write of v or of what E reads
        targets = []
        for j in range(i + 1, i1):
            targets.append(j)
            if defs(stmts[j]) & ({v} | er):
                break
        else:
            targets.append("tail")
        if trivial(E) and not call and not load and v not in er:   # v = v + 1: later reads see the new v
            done = False
            for j in targets:
                if j == "tail":
                    if tail is not None:
                        h = tail.c if isinstance(tail, DoWhile) else head_expr(tail)
                        if h is not None and count(h, v):
                            set_head(tail, subst(h, {v: E}))
                            done = True
                    continue
                t = stmts[j]
                if v in uses(t):
                    _replace(stmts, j, _subst_stmt(t, {v: E}), lv)
                    done = True
            if done:
                changed = True
            i += 1
            continue
        # one read, then dead
        uses_at = []
        for j in targets:
            if j == "tail":
                if tail is not None:
                    h = tail.c if isinstance(tail, DoWhile) else head_expr(tail)
                    if h is not None and count(h, v):
                        uses_at.append(("tail", count(h, v)))
                continue
            n = sum(count(x, v) for x in exprs(stmts[j])) + (1 if isinstance(stmts[j], Assign) and isinstance(
                stmts[j].lhs, Var) and stmts[j].op != "=" and stmts[j].lhs.name == v else 0)
            if n:
                uses_at.append((j, n))
        if len(uses_at) != 1 or uses_at[0][1] != 1:
            i += 1
            continue
        j = uses_at[0][0]
        if j == "tail":
            after = lv.head.get(id(tail), None) if not isinstance(tail, Return) else set()
            if isinstance(tail, DoWhile):
                after = lv.head.get(id(tail))
            if after is None or v in after:
                i += 1
                continue
            between = stmts[i + 1:i1]
            if not _clear(between, er, load, call, tg):
                i += 1
                continue
            h = tail.c if isinstance(tail, DoWhile) else head_expr(tail)
            if (load or call) and _other_effects(h, v, tg, call) or call and _conditional(h, v):
                i += 1
                continue
            set_head(tail, subst(h, {v: E}))
        else:
            t = stmts[j]
            dead_after = v not in lv.after[id(t)] or v in defs(t)
            if not dead_after:
                i += 1
                continue
            if not _clear(stmts[i + 1:j], er, load, call, tg):
                i += 1
                continue
            if (load or call) and any(_other_effects(x, v, tg, call) for x in operands(t)):
                i += 1
                continue
            if call and any(_conditional(x, v) for x in exprs(t)):
                i += 1
                continue
            if call and isinstance(t, Assign) and not isinstance(t.lhs, Var) and (has_load(t.lhs, tg) or
                                                                                    has_call(t.lhs, tg)):
                i += 1
                continue
            _replace(stmts, j, _subst_stmt(t, {v: E}), lv)
        del stmts[i]
        i1 -= 1
        changed = True
    return changed


def _conditional(e, v):
    """Whether v is read where e may not evaluate it: the right of && or ||, an arm of ?: (a call moved there
    would not always run)."""
    if isinstance(e, Bin) and e.op in ("&&", "||"):
        return _conditional(e.a, v) or count(e.b, v) > 0
    if isinstance(e, Cond):
        return _conditional(e.c, v) or count(e.a, v) > 0 or count(e.b, v) > 0
    return any(_conditional(x, v) for x in _kids(e))


def _kids(e):
    if isinstance(e, Call):
        return ([] if isinstance(e.fn, str) else [e.fn]) + list(e.args)
    if isinstance(e, (Unary, Cast)):
        return [e.e]
    if isinstance(e, Bin):
        return [e.a, e.b]
    if isinstance(e, Index):
        return [e.a, e.i]
    if isinstance(e, Member):
        return [e.e]
    return []


def _clear(between, er, load, call, tg):
    """Nothing in between writes what E reads; when E reads memory, nothing in between stores or calls; when E
    calls, nothing in between reads memory either."""
    for b in between:
        if defs(b) & er:
            return False
        if (load or call) and effect(b, tg):
            return False
        if call and any(has_load(x, tg) for x in exprs(b)):
            return False
    return True


def _other_effects(e, v, tg, call):
    """Whether e does something else that must keep its order against E: a call (for an E that loads), or a load or
    call (for an E that calls)."""
    rest = _without(e, v)
    others = [x for x in rest if isinstance(x, Call) and not (isinstance(x.fn, str) and x.fn in tg.pure)]
    if not call:
        return any(not (isinstance(x.fn, str) and tg.is_load(x.fn)) for x in others)
    return bool(others) or any(isinstance(x, Unary) and x.op == "*" for x in rest)


def _without(e, v):
    """The nodes of e outside the subtrees that read v (what is evaluated besides v's own new expression)."""
    out, stack = [], [e]
    while stack:
        x = stack.pop()
        if x is None or (isinstance(x, Var) and x.name == v):
            continue
        out.append(x)
        for c in _kids(x):
            stack.append(c)
    return out


def _kids(x):
    if isinstance(x, Call):
        return list(x.args) + ([] if isinstance(x.fn, str) else [x.fn])
    if isinstance(x, (Unary, Cast)):
        return [x.e]
    if isinstance(x, Bin):
        return [x.a, x.b]
    if isinstance(x, Cond):
        return [x.c, x.a, x.b]
    if isinstance(x, Index):
        return [x.a, x.i]
    if isinstance(x, Member):
        return [x.e]
    return []


def _subst_stmt(s, env):
    if isinstance(s, Assign):
        lhs = s.lhs if isinstance(s.lhs, Var) else subst(s.lhs, env)
        if isinstance(s.lhs, Var) and s.op != "=" and s.lhs.name in env:
            return Assign(s.lhs, "=", Bin(s.op[:-1], env[s.lhs.name], subst(s.rhs, env)))
        return Assign(lhs, s.op, subst(s.rhs, env))
    return ExprS(subst(s.e, env))


def dead(body, tg, keep=frozenset()):
    """Remove assignments to variables dead after them (values without calls). Returns whether anything changed."""
    lv = Liveness()
    lv.run(body)
    changed = False
    for stmts, _ in lists(body):
        k = 0
        while k < len(stmts):
            s = stmts[k]
            if (isinstance(s, Assign) and isinstance(s.lhs, Var) and s.lhs.name not in keep
                    and s.lhs.name not in lv.after[id(s)] and not has_call(s.rhs, tg)):
                del stmts[k]
                changed = True
                continue
            if (isinstance(s, Assign) and isinstance(s.lhs, Var) and s.lhs.name not in keep
                    and s.lhs.name not in lv.after[id(s)] and _bare_call(s.rhs)):
                stmts[k] = ExprS(_bare_call(s.rhs))
                changed = True
                continue
            if isinstance(s, ExprS) and has_call(s.e, tg):
                inner = s.e
                while isinstance(inner, Cast) or isinstance(inner, Call) and inner.fn in tg.pure and len(
                        inner.args) == 1:
                    inner = inner.e if isinstance(inner, Cast) else inner.args[0]
                if inner is not s.e and isinstance(inner, Call):
                    stmts[k] = ExprS(inner)
                    changed = True
            if isinstance(s, ExprS) and not has_call(s.e, tg):
                del stmts[k]
                changed = True
                continue
            k += 1
    return changed


def _bare_call(e):
    """The call a value is, under casts only (a dead result: the call alone stays)."""
    while isinstance(e, Cast):
        e = e.e
    return e if isinstance(e, Call) and isinstance(e.fn, str) else None


def fold_all(body):
    for stmts, _ in lists(body):
        for k, s in enumerate(stmts):
            if isinstance(s, Assign):
                stmts[k] = Assign(s.lhs if isinstance(s.lhs, Var) else fold(s.lhs), s.op, fold(s.rhs))
            elif isinstance(s, ExprS):
                stmts[k] = ExprS(fold(s.e))
            else:
                h = head_expr(s)
                if h is not None:
                    set_head(s, fold(h))
                if isinstance(s, (While, DoWhile)):
                    s.c = fold(s.c)
                if isinstance(s, For) and s.c is not None:
                    s.c = fold(s.c)


def _all_simple(stmts, top=True):
    """(statement, whether it is a top-level one, top-level index) of every simple statement, and of every
    expression a compound statement evaluates (as None statements)."""
    out = []
    for k, s in enumerate(stmts):
        if simple(s):
            out.append((s, k))
            continue
        for child in _kid_lists(s):
            out += [(x, k) for x, _ in _all_simple(child, False)]
        if isinstance(s, For):
            out += [(x, k) for x in (s.init, s.step) if x is not None]
    return out


def _kid_lists(s):
    if isinstance(s, Block):
        return [s.body]
    if isinstance(s, If):
        return [s.t.body] + ([s.e.body] if isinstance(s.e, Block) else _kid_lists(s.e) if isinstance(s.e, If) else [])
    if isinstance(s, (While, DoWhile, For, Switch)):
        return [s.body.body]
    return []


def _compound_reads(stmts):
    """{top-level index: names} read by the conditions of compound statements (and returns)."""
    out = {}

    def go(s, k):
        r = set()
        for e in (getattr(s, "c", None), s.e if isinstance(s, (Switch, Return)) else None):
            if e is not None and not isinstance(e, (Block, If)):
                r |= reads(e)
        if isinstance(s, Case) and s.v is not None:
            r |= reads(s.v)
        out.setdefault(k, set()).update(r)
        for child in _kid_lists(s):
            for x in child:
                go(x, k)
    for k, s in enumerate(stmts):
        go(s, k)
    return out


def invariants(body, tg, keep=frozenset()):
    """A variable with one assignment, at the top level, of a value that reads only variables nothing assigns (and
    no memory, no call), and that nothing reads up to that assignment: every read of it is that value."""
    top = body.body
    sims = _all_simple(top)
    assigned = {}
    for st, k in sims:
        for d in defs(st):
            assigned.setdefault(d, []).append((st, k))
    creads = _compound_reads(top)

    def once_before(n, k):
        """n has one assignment, a top-level one before index k: every read from k on sees that value."""
        w = assigned[n]
        return len(w) == 1 and w[0][1] < k and any(w[0][0] is x for x in top)
    env = {}
    for v, where in assigned.items():
        if len(where) != 1 or v in keep:
            continue
        st, k = where[0]
        if not (isinstance(st, Assign) and st.op == "=" and any(st is x for x in top)):
            continue
        e = st.rhs
        if has_load(e, tg) or has_call(e, tg) or not trivial(e) or any(
                n in assigned and not once_before(n, k) for n in reads(e)):
            continue
        if any(v in uses(x) for x, kk in sims if kk < k or x is st) or any(v in creads.get(kk, ()) for kk in range(k)):
            continue
        env[v] = e
    # a value naming another invariant waits for the next round
    env = {v: e for v, e in env.items() if not reads(e) & set(env)}
    if not env:
        return False
    from .lift import map_exprs, rewrite  # noqa: PLC0415
    for v in env:
        for i, s in enumerate(top):
            if isinstance(s, Assign) and isinstance(s.lhs, Var) and s.lhs.name == v:
                del top[i]
                break
    map_exprs(top, lambda x: subst(x, env))
    return True


def simplify(body, tg, keep=frozenset(), rounds=50):
    """forward, dead, invariants and fold to a fixpoint."""
    for _ in range(rounds):
        fold_all(body)
        a = forward(body, tg, keep)
        b = dead(body, tg, keep)
        c = invariants(body, tg, keep) | constant_ifs(body.body)
        if not (a or b or c):
            fold_all(body)
            return
    fold_all(body)
