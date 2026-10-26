"""Structured C for any reducible control flow: the "struct" pass's second method, for the functions whose goto
shapes the pattern rewrites (structure.py) do not all fit.

The body's statements are cut into basic blocks (at the labels gotos name; a block ends at a goto, a conditional
goto, a switch over a jump table, or a return), and the graph is laid out by its dominator tree (Ramsey, "Beyond
Relooper", ICFP 2022):

  - a loop header's code is a `for (;;)` around the loop; a jump back to it is a `continue`;
  - a block reached by forward jumps from two places (a join), or one that leaves a loop, comes after the code of
    the block that dominates it (after the loop it leaves), and a jump to it is an exit to there;
  - a block with one way in is written where that jump is.

An exit to where control goes anyway disappears. The rest become, in this order of preference:
  `if (c) { A } else { rest }` for a forward skip out of nested ifs; `break` when the innermost loop or switch
  ends right where the exit goes (or `continue` to the innermost loop); a copy of the code there when it is at
  most 3 statements ending in a return; a `do { ... } while (0);` that the exit breaks out of; and as a last resort
  a flag (`fl_ = K; break;`, then `if (fl_ == K)` after the loop) for a jump out of more than one loop.
A jump-table switch becomes a C `switch` whose cases hold the code of their targets.

Every statement of the input appears exactly once (or, for a tail copy, also in the copy), in the same order along
every path. Irreducible flow, or any shape this module does not model, returns None (the caller keeps its own form).
"""
from __future__ import annotations

import re

from .structure import GOTO, GOTO_ANY, IFGOTO, LABEL, LABEL_ANY, neg, paren, top_ops

IFOPEN = re.compile(r"^if \((.+)\) \{$")
CASE = re.compile(r"^case (0x[0-9a-f]+u): goto (L_\w+);$")
SMALL_TAIL = 3


class Fail(Exception):
    pass


# --------------------------------------------------------------------------------------------------------- parse

def code_lines(lines):
    return [x.strip() for x in lines if x.strip() and not x.strip().startswith("/*")]


def op_ends(lines):
    c = code_lines(lines)
    if len(c) >= 2 and c[-1] == "}":
        # a plain scope `{ u32 g_ = ...; ... return r0; }` at the end ends as its last statement does
        depth = 0
        for k in range(len(c) - 1, -1, -1):
            depth += c[k].count("}") - c[k].count("{")
            if depth == 0:
                break
        if depth != 0 or not c[k].startswith("{") or not one_group(c[k:]):
            return False
        c = c[:-1]
    return bool(c) and (c[-1].startswith("return") or c[-1] == "tc_halt();")


def dedent(x, base):
    return x[len(base):] if x.startswith(base) else x.lstrip()


def one_group(group):
    """The lines form one brace group that closes on its last line (no `} else {` in between)."""
    depth = 0
    for k, x in enumerate(group):
        for ch in x:
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0 and k != len(group) - 1:
                    return False
    return depth == 0


def switch_op(group, base):
    """(prefix, [(value, label)], suffix) of the translator's jump-table form:
    `{ u32 g_ = E;` slot lines `switch (g_) {` `case V: goto L;`... `default: break;` `}` tail call lines `}`."""
    ys = [x.strip() for x in group]
    if not ys[0].startswith("{ u32 g_ = ") or ys[-1] != "}" or "switch (g_) {" not in ys:
        return None
    s = ys.index("switch (g_) {")
    cases, k = [], s + 1
    while k < len(ys) and CASE.match(ys[k]):
        m = CASE.match(ys[k])
        cases.append((m.group(1), m.group(2)))
        k += 1
    if not cases or k + 1 >= len(ys) or ys[k] != "default: break;" or ys[k + 1] != "}":
        return None
    prefix = [dedent(x, base) for x in group[:s]]
    suffix = [dedent(x, base) for x in group[k + 2:-1]]
    text = "\n".join(prefix + suffix)
    if GOTO_ANY.search(text) or LABEL_ANY.search(text) or not op_ends(suffix):
        return None
    if any("{" in x or "}" in x for x in prefix[1:] + suffix):
        return None
    return prefix, cases, suffix


def parse_items(lines, base, items, fresh):
    i = 0
    while i < len(lines):
        x = lines[i]
        y = x.strip()
        if not y:
            i += 1
            continue
        m = LABEL.match(y)
        if m:
            items.append(("label", m.group(1)))
            i += 1
            continue
        m = GOTO.match(y)
        if m:
            items.append(("goto", m.group(1), None))
            i += 1
            continue
        m = IFGOTO.match(y)
        if m and "{" not in y:
            items.append(("goto", m.group(2), m.group(1)))
            i += 1
            continue
        group = [x]
        depth = y.count("{") - y.count("}")
        while depth > 0 and i + 1 < len(lines):
            i += 1
            group.append(lines[i])
            depth += lines[i].count("{") - lines[i].count("}")
        if depth != 0:
            raise Fail("unbalanced braces")
        i += 1
        text = "\n".join(group)
        if not (GOTO_ANY.search(text) or LABEL_ANY.search(text)):
            items.append(("op", [dedent(g, base) for g in group]))
            continue
        if y == "{ u32 c_ = t;" and group[-1].strip() == "}" and one_group(group):
            # a delayed branch whose slot writes T: `{ u32 c_ = t; <slot> if (c_) goto L; }`. The jump leaves the
            # scope, so the saved T goes in a function-level ct_ instead.
            inner = group[1:-1]
            uses = [g for g in inner if re.search(r"\bc_\b", g)]
            if len(uses) != 1 or not re.match(r"^if \(!?c_\) goto L_\w+;$", uses[0].strip()):
                raise Fail("a T-saving delayed branch of another form")
            items.append(("op", ["ct_ = t;"]))
            parse_items([re.sub(r"\bc_\b", "ct_", g) for g in inner], base, items, fresh)
            continue
        m = IFOPEN.match(y)
        if m and len(group) >= 2 and group[-1].strip() == "}" and one_group(group):
            # an if block with a goto inside: `if (!c) goto skip; <its lines> skip:`
            skip = fresh()
            items.append(("goto", skip, neg(m.group(1))))
            inner = group[1:-1]
            ind = min((len(g) - len(g.lstrip()) for g in inner if g.strip()), default=len(base))
            parse_items(inner, " " * ind, items, fresh)
            items.append(("label", skip))
            continue
        sw = switch_op(group, base)
        if sw is not None:
            items.append(("switch", sw))
            continue
        m = re.match(r"^\{ u32 g_ = ([^;{}]+);$", y)
        if m and len(group) >= 2 and group[-1].strip() == "}" and one_group(group):
            # a computed call whose return may come back here: `{ u32 g_ = x; ... if (pr == R) goto L; return r0; }`.
            # The jump leaves the scope, so the target goes in a function-level jt_ instead.
            items.append(("op", [dedent(re.sub(r"\bg_\b", "jt_", y[2:].replace("u32 ", "", 1)), base)]))
            parse_items([re.sub(r"\bg_\b", "jt_", g) for g in group[1:-1]], base, items, fresh)
            continue
        raise Fail("a brace group with a jump inside")


# ---------------------------------------------------------------------------------------------------- the graph

class Node:
    __slots__ = ("id", "labels", "stmts", "term")

    def __init__(self, i):
        self.id, self.labels, self.stmts, self.term = i, [], [], None


def build_graph(items):
    refs = set()
    for it in items:
        if it[0] == "goto":
            refs.add(it[1])
        elif it[0] == "switch":
            refs |= {lab for _, lab in it[1][1]}
    nodes = [Node(0)]
    cur, dead, nfall = nodes[0], False, [0]

    def new():
        n = Node(len(nodes))
        nodes.append(n)
        return n

    def fall_label():
        nfall[0] += 1
        return f"L_f{nfall[0]}"

    for it in items:
        kind = it[0]
        if kind == "label":
            if it[1] not in refs:
                continue
            if not dead and cur.term is None and not cur.stmts:
                cur.labels.append(it[1])
                continue
            if not dead and cur.term is None:
                cur.term = ("goto", it[1])
            cur = new()
            cur.labels.append(it[1])
            dead = False
            continue
        if dead:
            continue
        if kind == "op":
            cur.stmts += it[1]
            if op_ends(it[1]):
                cur.term = ("end",)
                dead = True
        elif kind == "goto":
            if it[2] is None:
                cur.term = ("goto", it[1])
                dead = True
            else:
                f = fall_label()
                cur.term = ("cond", it[2], it[1], f)
                cur = new()
                cur.labels.append(f)
        elif kind == "switch":
            cur.term = ("switch", it[1])
            dead = True
    if not dead and cur.term is None:
        cur.term = ("off",)       # runs off the end of the body
    where = {lab: n.id for n in nodes for lab in n.labels}
    succ = {}
    for n in nodes:
        t = n.term
        if t[0] == "goto":
            s = [t[1]]
        elif t[0] == "cond":
            s = [t[2], t[3]]
        elif t[0] == "switch":
            s = []
            for _, lab in t[1][1]:
                if lab not in s:
                    s.append(lab)
        else:
            s = []
        if any(x not in where for x in s):
            raise Fail("a goto to a label that is not in the body")
        succ[n.id] = [where[x] for x in s]
    return nodes, succ, where


def rpo_order(succ):
    seen, post = {0}, []
    stack = [(0, iter(reversed(succ[0])))]
    while stack:
        n, it = stack[-1]
        for s in it:
            if s not in seen:
                seen.add(s)
                stack.append((s, iter(reversed(succ[s]))))
                break
        else:
            post.append(n)
            stack.pop()
    order = post[::-1]
    return order, {n: k for k, n in enumerate(order)}


def dominators(order, rpo, preds):
    idom = {order[0]: order[0]}

    def inter(a, b):
        while a != b:
            while rpo[a] > rpo[b]:
                a = idom[a]
            while rpo[b] > rpo[a]:
                b = idom[b]
        return a
    changed = True
    while changed:
        changed = False
        for n in order[1:]:
            ps = [p for p in preds[n] if p in idom]
            if not ps:
                continue
            d = ps[0]
            for p in ps[1:]:
                d = inter(p, d)
            if idom.get(n) != d:
                idom[n] = d
                changed = True
    return idom


def dominates(idom, a, b):
    while True:
        if a == b:
            return True
        if idom[b] == b:
            return False
        b = idom[b]


# ----------------------------------------------------------------------------------------------------- the tree

class S:
    """A statement of the structured tree. k: ops (a = lines), if (c, a, b), loop (x = header, a; c = shape),
    block (x = node it is followed by, a; c = True when written as do { } while (0)), switch (x = (prefix, suffix),
    a = [(values, body)]), jump (x = target: ("exit", node) | ("cont", header) | ("after", S); c = the flag value
    a flag hop's jump carries), brk, cnt."""
    __slots__ = ("k", "a", "b", "c", "x")

    def __init__(self, k, a=None, b=None, c=None, x=None):
        self.k, self.a, self.b, self.c, self.x = k, a, b, c, x


def generate(nodes, succ, order, rpo, idom, preds, orig=None, inline=frozenset()):
    """The tree for the graph. inline: original ids of loop exits (one way in) to write where the jump to them is,
    inside the loop, instead of after the loop. Returns (tree, statements of the code so moved into loops)."""
    orig = orig or {n: n for n in succ}
    reach = set(order)
    back = [(u, v) for u in reach for v in succ[u] if rpo[v] <= rpo[u]]
    for u, v in back:
        if not dominates(idom, v, u):
            raise Fail("irreducible")
    loops = {}
    for u, v in back:
        body = loops.setdefault(v, {v})
        work = [u]
        while work:
            n = work.pop()
            if n not in body:
                body.add(n)
                work += [p for p in preds[n] if p in reach]
    fwd_in = {n: 0 for n in reach}
    for u in reach:
        for v in succ[u]:
            if rpo[v] > rpo[u]:
                fwd_in[v] += 1
    # a switch case whose block is also reached by running off the end of the previous case's block (a chain of
    # cases each doing one more step) is written as that case, and the previous case falls through into it
    case_next = {}
    for x in reach:
        if nodes[x].term[0] != "switch":
            continue
        ts = succ[x]
        for prev, y in zip(ts, ts[1:]):
            fp = [u for u in preds[y] if u in reach and rpo[u] < rpo[y]]
            if (sorted(fp) == sorted([x, prev]) and nodes[prev].term[0] == "goto" and succ[prev] == [y]
                    and idom[y] == x and not any(y in b for b in loops.values())):
                case_next[y] = prev
    hoisted, merge, placed, moved = {}, {}, set(), []
    for z in order[1:]:
        d = idom[z]
        owners = [h for h, b in loops.items() if d in b and z not in b]
        if z in case_next:
            continue
        if owners and fwd_in[z] == 1 and orig[z] in inline:
            moved.append(z)
        elif owners:
            h = max(owners, key=lambda h: len(loops[h]))
            hoisted.setdefault(h, []).append(z)
            placed.add(z)
        elif fwd_in[z] >= 2:
            merge.setdefault(d, []).append(z)
            placed.add(z)
    emitted = set()

    def branch(x, y):
        if y in case_next and x == case_next[y]:
            return [S("fall")]
        if rpo[y] <= rpo[x]:
            return [S("jump", x=("cont", y))]
        if y in placed:
            return [S("jump", x=("exit", y))]
        return code_for(y)

    def own(x):
        n = nodes[x]
        out = [S("ops", a=list(n.stmts))] if n.stmts else []
        t = n.term
        if t[0] == "goto":
            out += branch(x, succ[x][0])
        elif t[0] == "cond":
            out.append(S("if", c=t[1], a=branch(x, succ[x][0]), b=branch(x, succ[x][1])))
        elif t[0] == "switch":
            prefix, cases, suffix = t[1]
            by = {}
            for v, lab in cases:
                by.setdefault(lab, []).append(v)
            arms = []
            for lab, y in zip(dict.fromkeys(lab for _, lab in cases), succ[x]):
                arms.append((by[lab], branch(x, y)))
            out.append(S("switch", a=arms, x=(prefix, suffix)))
        elif t[0] == "off":
            raise Fail("control runs off the end of the body")
        return out

    def code_for(x):
        if x in emitted:
            raise Fail("a block placed twice")
        emitted.add(x)

        def within(ys):
            if not ys:
                return own(x)
            return [S("block", x=ys[0], a=within(ys[1:]))] + code_for(ys[0])
        core = within(sorted(merge.get(x, []), key=lambda n: -rpo[n]))
        if x in loops:
            core = [S("loop", x=x, a=core)]

        def wrap(fs):
            if not fs:
                return core
            return [S("block", x=fs[0], a=wrap(fs[1:]))] + code_for(fs[0])
        return wrap(sorted(hoisted.get(x, []), key=lambda n: -rpo[n]))
    root = code_for(order[0])
    if emitted != reach:
        raise Fail("a block was not placed")
    size = 0
    for z in moved:
        size += sum(sum(x.count(";") for x in code_lines(nodes[n].stmts)) + 1
                    for n in reach if dominates(idom, z, n))
    return root, size


# ------------------------------------------------------------------------------------------------ tree helpers

def subs(s):
    if s.k == "if":
        return [s.a, s.b]
    if s.k in ("loop", "block"):
        return [s.a]
    if s.k == "switch":
        return [body for _, body in s.a]
    return []


def walk(seq, stack, fn):
    """fn(seq, index, stmt, stack) for every statement, outermost first (stack: the enclosing statements)."""
    for i, s in enumerate(list(seq)):
        fn(seq, i, s, stack)
        for sub in subs(s):
            walk(sub, stack + [s], fn)


def breakable(s):
    return s.k in ("loop", "switch") or (s.k == "block" and s.c)


def breaks_out(s):
    """s holds a break of the breakable around it (one not inside a breakable of its own)."""
    if s.k == "brk":
        return True
    return not breakable(s) and any(breaks_out(x) for sub in subs(s) for x in sub)


def follows(root):
    """{id(stmt): where control goes when it falls off the end of stmt}: ("exit", node), ("cont", header) or None
    (the next statement)."""
    out = {}

    def ann(seq, follow):
        for i, s in enumerate(seq):
            f = follow if i == len(seq) - 1 else None
            out[id(s)] = f
            if s.k == "if":
                ann(s.a, f)
                ann(s.b, f)
            elif s.k == "block":
                ann(s.a, ("exit", s.x))
            elif s.k == "loop":
                ann(s.a, ("cont", s.x))
            elif s.k == "switch":
                for _, body in s.a:
                    ann(body, None)
    ann(root, None)
    return out


def landing(s, fol):
    """Where a break out of breakable s goes."""
    if s.k == "block":
        return ("exit", s.x)
    return fol.get(id(s))


def tail_clean(seq, follow):
    """Drop jumps to where control goes anyway; returns whether anything changed."""
    changed = False
    for s in seq:
        if s.k == "block":
            changed |= tail_clean(s.a, ("exit", s.x))
        elif s.k == "loop":
            changed |= tail_clean(s.a, ("cont", s.x))
        elif s.k == "switch":
            for _, body in s.a:
                changed |= tail_clean(body, None)
    if seq:
        last = seq[-1]
        if last.k == "jump" and follow is not None and last.x == follow:
            seq.pop()
            return True
        if last.k == "if":
            changed |= tail_clean(last.a, follow)
            changed |= tail_clean(last.b, follow)
    for s in seq[:-1]:
        if s.k == "if":
            changed |= tail_clean(s.a, None)
            changed |= tail_clean(s.b, None)
    return changed


def has_jump_to(seq, target):
    for s in seq:
        if s.k == "jump" and s.x == target:
            return True
        if any(has_jump_to(sub, target) for sub in subs(s)):
            return True
    return False


def jump_targets(seq, memo):
    """The targets of every jump in seq, nested ones included. memo: {id(stmt): (stmt, its targets)}, valid while
    no jump is removed (splicing only moves statements)."""
    out = set()
    for s in seq:
        if s.k == "jump":
            out.add(s.x)
            continue
        if id(s) not in memo:
            memo[id(s)] = (s, set().union(*(jump_targets(sub, memo) for sub in subs(s))))
        out |= memo[id(s)][1]
    return out


def splice(seq, memo=None):
    """A block nothing exits any more is just its statements (and a loop's body likewise loses them). The spliced
    statements were already spliced inside the block, so the walk goes on after them."""
    memo = {} if memo is None else memo
    changed = False
    k = 0
    while k < len(seq):
        s = seq[k]
        for sub in subs(s):
            changed |= splice(sub, memo)
        if s.k == "block":
            tg = jump_targets(s.a, memo)
            if ("exit", s.x) not in tg and ("after", s) not in tg:
                seq[k:k + 1] = s.a
                k += len(s.a)
                changed = True
                continue
        k += 1
    return changed


def if_push(seq, follow):
    """`if (c) { A; exit } rest` falling to that exit's target -> `if (c) { A } else { rest }`."""
    changed = False
    for s in seq:
        if s.k == "block":
            changed |= if_push(s.a, ("exit", s.x))
        elif s.k == "loop":
            changed |= if_push(s.a, ("cont", s.x))
        elif s.k == "switch":
            for _, body in s.a:
                changed |= if_push(body, None)
    if follow is not None and follow[0] == "exit":
        for i, s in enumerate(seq[:-1]):
            if s.k != "if":
                continue
            ta = bool(s.a) and s.a[-1].k == "jump" and s.a[-1].x == follow
            tb = bool(s.b) and s.b[-1].k == "jump" and s.b[-1].x == follow
            if ta == tb:
                continue
            rest = seq[i + 1:]
            del seq[i + 1:]
            if ta:
                s.a.pop()
                s.b += rest
            else:
                s.b.pop()
                s.a += rest
            changed = True
            break
    for s in seq:
        if s.k == "if":
            f = follow if s is seq[-1] else None
            changed |= if_push(s.a, f)
            changed |= if_push(s.b, f)
    return changed


def jumps_in(seq, out):
    for s in seq:
        if s.k == "jump":
            out.append(s)
        for sub in subs(s):
            jumps_in(sub, out)
    return out


def constructs_in(seq, out):
    for s in seq:
        if s.k in ("block", "loop"):
            out.append((("exit" if s.k == "block" else "cont"), s.x))
        for sub in subs(s):
            constructs_in(sub, out)
    return out


def tail_at(root, y, single):
    """(statements, the list holding block y, its index) when the code after block y can stand in for the exit
    to y: the only exit to y may take that code itself when it ends in a return (and jumps only within itself);
    one of several exits gets a copy of it when it is at most SMALL_TAIL statements ending in a return."""
    found = []

    def fn(seq, i, s, stack):
        if s.k == "block" and s.x == y:
            found.append((seq, i))
    walk(root, [], fn)
    if len(found) != 1:
        return None
    seq, i = found[0]
    rest = seq[i + 1:]
    if not rest or rest[-1].k != "ops" or not op_ends(rest[-1].a):
        return None
    if single:
        own = set(constructs_in(rest, []))
        if all(j.x in own for j in jumps_in(rest, [])):
            return rest, seq, i
    if any(s.k != "ops" for s in rest):
        return None
    lines = [x for s in rest for x in s.a if x.strip() and not x.strip().startswith("/*")]
    if sum(x.count(";") for x in lines) > SMALL_TAIL or any("{" in x for x in lines):
        return None
    return [S("ops", a=lines)], seq, i


# ------------------------------------------------------------------------------------------------------- lower

def lower(root):
    """Turn every jump into break / continue / a copy / a do-while(0) block / a flag hop."""
    flag, flag_of, relayed = [0], {}, set()
    stats = {"flags": 0, "targets": set()}
    entries = {}            # block target -> the paths that still reach the code after it (jumps there and
    for j in jumps_in(root, []):    # fall-throughs left by dropped jumps count alike; a copy takes one away)
        entries[j.x] = entries.get(j.x, 0) + 1
    for _ in range(2000):
        while tail_clean(root, None) | splice(root) | if_push(root, None):
            pass
        fol = follows(root)
        jumps = []
        walk(root, [], lambda seq, i, s, stack: jumps.append((seq, s, stack)) if s.k == "jump" else None)
        plan, mutated = [], False
        for seq, j, stack in jumps:
            kind, tgt = j.x
            if kind == "after":
                at = next((k for k, s in enumerate(stack) if s is tgt), None)
            else:
                at = next((k for k, s in enumerate(stack)
                           if s.k == ("block" if kind == "exit" else "loop") and s.x == tgt), None)
            if at is None:
                raise Fail("a jump outside its target")
            inner = stack[at + 1:]
            brks = [s for s in inner if breakable(s)]
            if kind == "after":
                if not brks:
                    plan.append((j, "brk"))
                    continue
            elif kind == "cont":
                if not [s for s in inner if s.k == "loop" or (s.k == "block" and s.c)]:
                    plan.append((j, "cnt"))
                    continue
                if brks and landing(brks[-1], fol) == j.x:
                    plan.append((j, "brk"))
                    continue
            else:
                if brks and landing(brks[-1], fol) == j.x:
                    plan.append((j, "brk"))
                    continue
                if not brks and stack[at].c:
                    plan.append((j, "brk"))
                    continue
                single = entries.get(j.x, 0) == 1
                got = tail_at(root, tgt, single)
                if got is not None:
                    stmts, bseq, bi = got
                    if single:
                        del bseq[bi + 1:]          # the code moves to the only exit that reaches it
                    entries[j.x] -= 1
                    k = seq.index(j)
                    seq[k:k + 1] = stmts
                    mutated = True
                    break
                if not brks:
                    stack[at].c = True      # the block becomes do { } while (0)
                    stats["targets"].add(tgt)
                    mutated = True
                    break
                stats["targets"].add(tgt)
            # a flag hop out of the innermost breakable, and a test of the flag right after it (one value and one
            # test for every jump out of the same breakable to the same place)
            b = brks[-1]
            i = seq.index(j)
            if kind == "after":
                # a flag hop's jump that now leaves one more breakable (a block became do { } while (0) around it):
                # it keeps its value, tested again right after that breakable, so the test after its own target
                # still sees it (a second value would overwrite the first)
                k = j.c
                seq[i] = S("jump", x=("after", b), c=k)
                if (id(b), j.x, k) not in relayed:
                    relayed.add((id(b), j.x, k))
                    stats["flags"] += 1
                    parent = None

                    def find(sq, i, s, st):
                        nonlocal parent
                        if s is b:
                            parent = sq
                    walk(root, [], find)
                    parent.insert(parent.index(b) + 1, S("if", c=f"fl_ == {k}u", a=[S("jump", x=j.x, c=k)], b=[]))
                mutated = True
                break
            if (id(b), j.x) in flag_of:
                k = flag_of[(id(b), j.x)]
                seq[i:i + 1] = [S("ops", a=[f"fl_ = {k}u;"]), S("jump", x=("after", b), c=k)]
                mutated = True
                break
            stats["flags"] += 1
            flag[0] += 1
            k = flag_of[(id(b), j.x)] = flag[0]
            seq[i:i + 1] = [S("ops", a=[f"fl_ = {k}u;"]), S("jump", x=("after", b), c=k)]
            parent = None

            def find(sq, i, s, st):
                nonlocal parent
                if s is b:
                    parent = sq
            walk(root, [], find)
            parent.insert(parent.index(b) + 1, S("if", c=f"fl_ == {k}u", a=[S("ops", a=["fl_ = 0u;"]),
                                                                              S("jump", x=j.x)], b=[]))
            mutated = True
            break
        if mutated:
            continue
        for j, how in plan:
            j.k = how
        blocks = []
        walk(root, [], lambda seq, i, s, stack: blocks.append((seq, s)) if s.k == "block" and s.c else None)
        for seq, d in reversed(blocks):     # innermost first: do { starts at the first statement that breaks it
            k = next((k for k, s in enumerate(d.a) if breaks_out(s)), len(d.a))
            if k:
                at = seq.index(d)
                seq[at:at] = d.a[:k]
                del d.a[:k]
                if not d.a:
                    seq.remove(d)
        stats["doblocks"] = len(blocks)
        return stats
    raise Fail("lowering did not settle")


# ------------------------------------------------------------------------------------------------------- shape

def ends(seq):
    """Control never runs off the end of seq."""
    if not seq:
        return False
    s = seq[-1]
    if s.k in ("brk", "cnt"):
        return True
    if s.k == "ops":
        return op_ends(s.a) or (not code_lines(s.a) and len(seq) > 1 and ends(seq[:-1]))
    if s.k == "if":
        return ends(s.a) and ends(s.b)
    if s.k == "loop":
        return not s.c and not direct_brk(s.a)
    return False


def direct_brk(seq):
    """A break in seq that ends the loop or block seq is the body of."""
    for s in seq:
        if s.k == "brk":
            return True
        if s.k == "if" and (direct_brk(s.a) or direct_brk(s.b)):
            return True
        if s.k == "block" and not s.c and direct_brk(s.a):
            return True
    return False


def is_comment(s):
    return s.k == "ops" and not code_lines(s.a)


def has_side_effects(c):
    return bool(re.search(r"\b(?!u32|s32|u16|s16|u8|s8)[A-Za-z_]\w*\s*\(", c))


def tidy(seq):
    k = 0
    while k < len(seq):
        s = seq[k]
        for sub in subs(s):
            tidy(sub)
        if s.k == "if":
            if not s.a and not s.b:
                seq[k:k + 1] = [S("ops", a=[f"(void)({s.c});"])] if has_side_effects(s.c) else []
                continue
            if not s.a:
                s.c, s.a, s.b = neg(s.c), s.b, []
            if s.b and ends(s.a):
                seq[k + 1:k + 1] = s.b
                s.b = []
            elif s.b and ends(s.b) and not ends(s.a):
                rest = s.a
                s.c, s.a, s.b = neg(s.c), s.b, []
                seq[k + 1:k + 1] = rest
            inner = [v for v in s.a if not is_comment(v)]
            if not s.b and len(inner) == 1 and inner[0].k == "if" and not inner[0].b:
                v = inner[0]
                s.c = f"{paren(s.c)} && {paren(v.c)}"
                s.a = [w for w in s.a if is_comment(w)] + v.a
        k += 1


def direct_cnt(seq):
    """A continue in seq that belongs to the loop seq is the body of."""
    for s in seq:
        if s.k == "cnt":
            return True
        if s.k == "if" and (direct_cnt(s.a) or direct_cnt(s.b)):
            return True
        if s.k == "switch" and any(direct_cnt(b) for _, b in s.a):
            return True
        if s.k == "block" and not s.c and direct_cnt(s.a):
            return True
    return False


def single_break(s):
    return s.k == "if" and not s.b and [v.k for v in s.a if not is_comment(v)] == ["brk"]


def shape_loops(seq):
    for s in seq:
        for sub in subs(s):
            shape_loops(sub)
        if s.k != "loop":
            continue
        body = s.a
        first = next((k for k, v in enumerate(body) if not is_comment(v)), None)
        if first is not None and single_break(body[first]):
            s.c = ("while", neg(body[first].c))
            s.a = body[:first] + body[first + 1:]
            continue
        last = len(body) - 1
        if last >= 0 and single_break(body[last]) and not direct_cnt(body[:last]):
            s.c = ("do", neg(body[last].c))
            s.a = body[:last] + [v for v in body[last].a if is_comment(v)]


# ------------------------------------------------------------------------------------------------------ render

def render(seq, ind, out):
    for s in seq:
        if s.k == "ops":
            out += [ind + x for x in s.a]
        elif s.k == "brk":
            out.append(ind + "break;")
        elif s.k == "cnt":
            out.append(ind + "continue;")
        elif s.k == "if":
            if not s.b and len(s.a) == 1 and s.a[0].k in ("brk", "cnt"):
                out.append(f"{ind}if ({s.c}) {'break' if s.a[0].k == 'brk' else 'continue'};")
                continue
            out.append(f"{ind}if ({s.c}) {{")
            render(s.a, ind + "    ", out)
            b = s.b
            while b:
                if len(b) == 1 and b[0].k == "if":
                    out.append(f"{ind}}} else if ({b[0].c}) {{")
                    render(b[0].a, ind + "    ", out)
                    b = b[0].b
                    continue
                out.append(f"{ind}}} else {{")
                render(b, ind + "    ", out)
                break
            out.append(f"{ind}}}")
        elif s.k == "loop":
            shape = s.c[0] if s.c else None
            if shape == "while":
                out.append(f"{ind}while ({s.c[1]}) {{")
            else:
                out.append(f"{ind}{'do' if shape == 'do' else 'for (;;)'} {{")
            render(s.a, ind + "    ", out)
            out.append(f"{ind}}} while ({s.c[1]});" if shape == "do" else f"{ind}}}")
        elif s.k == "block":
            if s.c:
                out.append(f"{ind}do {{")
                render(s.a, ind + "    ", out)
                out.append(f"{ind}}} while (0);")
            else:
                render(s.a, ind, out)
        elif s.k == "switch":
            # the computed target goes in a function-level sw_ (no scope around the cases: a case's own computed
            # jump declares g_ again, and a hidden declaration is warning C4456)
            prefix, suffix = s.x
            sw = [re.sub(r"\bg_\b", "sw_", x) for x in prefix + suffix]
            first = sw[0].lstrip("{ ").replace("u32 ", "", 1)
            out.append(ind + first)
            out += [ind + x for x in sw[1:len(prefix)]]
            out.append(f"{ind}switch (sw_) {{")
            for vals, body in s.a:
                out += [f"{ind}case {v}:" for v in vals]
                if body and body[-1].k == "fall":
                    body = body[:-1]        # runs on into the next case (C's fall-through)
                elif not ends(body):
                    raise Fail("a case runs into the next")
                render(body, ind + "    ", out)
            out.append(f"{ind}default:")
            out += [ind + "    " + x for x in sw[len(prefix):]]
            out.append(f"{ind}}}")
        else:
            raise Fail(f"unlowered {s.k}")
    return out


MERGED_COND_MAX = 240


def join(a, op, b):
    """a op b (op && or ||), parenthesising an operand only when it has the other logical operator at its top."""
    def part(x):
        ops = {o for _, o in top_ops(x) if o in ("&&", "||", "?")}
        return x if not ops or ops == {op} else f"({x})"
    return f"{part(a)} {op} {part(b)}"


def merge_conditions(nodes, succ):
    """Two tests in a row with nothing between them become one test with && or || (C evaluates the second only
    when the first does not decide, as the branches did): u `if (c1) -> A else v`, v (no statements, reached from
    u alone) `if (c2) -> C else D`: when C is A, u tests c1 || c2 (to A, else D); when D is A, c1 || !c2 (to A,
    else C). Likewise through u's taken side with &&."""
    changed = True
    while changed:
        changed = False
        order, _ = rpo_order(succ)
        preds = {n: [] for n in succ}
        for u in order:
            for v in succ[u]:
                preds[v].append(u)
        for u in order:
            nu = nodes[u]
            if nu.term[0] != "cond":
                continue
            for side in (1, 0):        # 1: through u's fall-through side, 0: through its taken side
                v = succ[u][side]
                nv = nodes[v]
                if v == u or nv.term[0] != "cond" or preds[v] != [u] or code_lines(nv.stmts) or v == 0:
                    continue
                c1, c2 = nu.term[1], nv.term[1]
                other = succ[u][1 - side]
                C, D = succ[v]
                if side == 1 and C == other:
                    c, t, f = join(c1, "||", c2), other, D
                elif side == 1 and D == other:
                    c, t, f = join(c1, "||", neg(c2)), other, C
                elif side == 0 and D == other:
                    c, t, f = join(c1, "&&", c2), C, other
                elif side == 0 and C == other:
                    c, t, f = join(c1, "&&", neg(c2)), D, other
                else:
                    continue
                if len(c) > MERGED_COND_MAX:
                    continue
                nu.stmts = nu.stmts + nv.stmts
                nu.term = ("cond", c, None, None)
                succ[u] = [t, f]
                succ[v] = []
                nv.term = ("end",)
                changed = True
                break
            if changed:
                break


SEARCH_TRIES = 60
SPLIT_BUDGET = 300          # statements split_irreducible may copy into one function
FLAG_COST, DOBLOCK_COST, MOVED_COST = 8, 4, 0.5


def small(n):
    """A block short enough to copy into each jump to it (at most SMALL_TAIL statements)."""
    c = code_lines(n.stmts)
    return (n.id != 0 and sum(x.count(";") for x in c) <= SMALL_TAIL and not any("{" in x for x in c)
            and n.term[0] in ("goto", "end", "cond"))


def duplicate(nodes, succ, dups):
    """The graph with each block in dups copied once per jump into it (the first jump keeps the block). A loop
    header is never copied. Returns (nodes, succ, original id of each node, statements added)."""
    order, rpo = rpo_order(succ)
    reach = set(order)
    nodes, succ = list(nodes), {k: list(v) for k, v in succ.items()}
    orig = {n.id: n.id for n in nodes}
    added = 0
    for y in sorted(dups):
        edges = [(p, i) for p in order for i, v in enumerate(succ[p]) if v == y]
        if y not in reach or any(rpo[p] >= rpo[y] for p, _ in edges):
            continue
        for p, i in edges[1:]:
            c = Node(len(nodes))
            c.stmts, c.term = nodes[y].stmts, nodes[y].term
            nodes.append(c)
            succ[c.id] = list(succ[y])
            orig[c.id] = y
            succ[p][i] = c.id
            added += sum(x.count(";") for x in code_lines(c.stmts))
    return nodes, succ, orig, added


def sccs(members, succ):
    """The strongly connected components of the graph restricted to members (Tarjan's, without recursion), each
    with a cycle (more than one block, or a block that jumps to itself)."""
    index, low, on, stack, out, k = {}, {}, set(), [], [], [0]
    for s in sorted(members):
        if s in index:
            continue
        work = [(s, iter([x for x in succ[s] if x in members]))]
        index[s] = low[s] = k[0]
        k[0] += 1
        stack.append(s)
        on.add(s)
        while work:
            v, it = work[-1]
            for w in it:
                if w not in index:
                    index[w] = low[w] = k[0]
                    k[0] += 1
                    stack.append(w)
                    on.add(w)
                    work.append((w, iter([x for x in succ[w] if x in members])))
                    break
                if w in on:
                    low[v] = min(low[v], index[w])
            else:
                work.pop()
                if work:
                    low[work[-1][0]] = min(low[work[-1][0]], low[v])
                if low[v] == index[v]:
                    c = set()
                    while True:
                        w = stack.pop()
                        on.discard(w)
                        c.add(w)
                        if w == v:
                            break
                    if len(c) > 1 or v in succ[v]:
                        out.append(c)
    return out


def split_irreducible(nodes, succ, budget):
    """The graph with each loop that has two ways in made into one with one: for a cycle region entered at h (the
    first reached) and also at e, the blocks reachable from e before h are copied, and the jumps into them from
    outside the region go to the copies (node splitting). Regions inside a loop, without its header, likewise.
    Copies are the same statements (cfgcheck traces them as the original). Fail beyond budget statements."""
    nodes, succ = list(nodes), {k: list(v) for k, v in succ.items()}
    for _ in range(64):
        order, rpo = rpo_order(succ)
        reach = set(order)
        preds = {x: set() for x in reach}
        for u in order:
            for v in succ[u]:
                if v in preds:
                    preds[v].add(u)
        found, regions = None, [(reach, True)]
        while regions and found is None:
            members, top = regions.pop()
            for c in sccs(members, succ):
                entries = sorted((n for n in c if preds[n] - c or (top and n == 0)), key=rpo.get)
                if len(entries) > 1:
                    found = (c, entries[0], entries[1])
                    break
                if entries:
                    regions.append((c - {entries[0]}, False))
        if found is None:
            return nodes, succ
        c, h, e = found
        part, work = set(), [e]
        while work:
            x = work.pop()
            if x in part or x == h or x not in c:
                continue
            part.add(x)
            work += succ[x]
        budget -= sum(x.count(";") for n in part for x in code_lines(nodes[n].stmts))
        if budget < 0:
            raise Fail("irreducible")
        copy = {}
        for n in sorted(part):
            k = Node(len(nodes))
            k.stmts, k.term = nodes[n].stmts, nodes[n].term
            nodes.append(k)
            copy[n] = k.id
        for n in sorted(part):
            succ[copy[n]] = [copy.get(x, x) for x in succ[n]]
        for p in reach - c:
            succ[p] = [copy.get(x, x) for x in succ[p]]
    raise Fail("irreducible")


def attempt(nodes, succ, dups, inline):
    """(cost, tree, lowering stats with targets as original block ids) of the graph with dups copied and the loop
    exits in inline written inside their loops."""
    nodes, succ, orig, added = duplicate(nodes, succ, dups)
    order, rpo = rpo_order(succ)
    preds = {x: [] for x in succ}
    for u in order:
        for v in succ[u]:
            preds[v].append(u)
    idom = dominators(order, rpo, preds)
    root, moved = generate(nodes, succ, order, rpo, idom, preds, orig, inline)
    st = lower(root)
    st["targets"] = {orig[t] for t in st["targets"]}
    cost = FLAG_COST * st["flags"] + DOBLOCK_COST * st["doblocks"] + added + MOVED_COST * moved
    return cost, root, st


# graph_key -> the (copies, inlined exits) search chose. The search reads no comment, so a second translation of the
# same function (fit: without comments, then compact) takes the same choice in one attempt instead of searching again.
CHOICES = {}


def graph_key(nodes, succ):
    return (tuple((tuple(n.labels), tuple(code_lines(n.stmts)), repr(n.term)) for n in nodes),
            tuple(sorted((k, tuple(v)) for k, v in succ.items())))


def search(nodes, succ):
    """The cheapest layout found: start with no copies and every loop exit after its loop, then add the changes
    (copy a small block / write a loop exit inside the loop) that the jumps needing a flag or a do-while(0) point
    at, one at a time, or two at a time when no single one helps, while that lowers the cost."""
    gk = graph_key(nodes, succ)
    if gk in CHOICES:
        try:
            return attempt(nodes, succ, *CHOICES[gk])[1]
        except Fail:
            pass
    dups, inl = frozenset(), frozenset()
    best = attempt(nodes, succ, dups, inl)
    order, rpo = rpo_order(succ)
    preds = {n: [] for n in succ}
    for u in order:
        for v in succ[u]:
            if rpo[v] > rpo[u]:
                preds[v].append(u)
    joins = [n for n in order if len(preds[n]) >= 2 and small(nodes[n])]
    tries = 0
    seen = set()
    while tries < SEARCH_TRIES and best[0] > 0:
        acts = []
        tg = sorted(best[2]["targets"])
        for y in tg:
            if small(nodes[y]):
                acts += [(frozenset([y]), frozenset()), (frozenset([y]), frozenset([y]))]
            acts.append((frozenset(), frozenset([y])))
        near = [p for y in tg for p in preds[y] if p in joins]
        acts += [(frozenset([p]), frozenset()) for p in near + joins]
        acts = list(dict.fromkeys(a for a in acts if not (a[0] <= dups and a[1] <= inl)))
        combos = acts + [(a[0] | b[0], a[1] | b[1]) for k, a in enumerate(acts[:5]) for b in acts[k + 1:5]]
        improved = None
        for k, (d, i) in enumerate(combos):
            key = (dups | d, inl | i)
            if key in seen:
                continue
            if k == len(acts) and improved is not None:
                break               # pairs only when no single change helps
            seen.add(key)
            tries += 1
            if tries > SEARCH_TRIES:
                break
            try:
                r = attempt(nodes, succ, *key)
            except Fail:
                continue
            if r[0] < best[0] and (improved is None or r[0] < improved[0][0]):
                improved = (r, key)
        if improved is None:
            break
        best, (dups, inl) = improved
    if len(CHOICES) >= 16:
        CHOICES.clear()
    CHOICES[gk] = (dups, inl)
    return best[1]


def structure_cfg(lines):
    """The body lines (4-space base indent) without gotos, or None when the flow is irreducible or not modelled."""
    import os
    from . import structure as _st
    if os.environ.get("SH4_CFG_DUMP"):
        d = os.environ["SH4_CFG_DUMP"]
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, f"{getattr(_st, 'CURRENT', 'x')}.txt"), "w") as f:
            f.write("\n".join(lines) + "\n")
    n = [0]

    def fresh():
        n[0] += 1
        return f"L_s{n[0]}"
    try:
        items = []
        parse_items(lines, "    ", items, fresh)
        nodes, succ, _ = build_graph(items)
        if os.environ.get("SH4_CFG_CHECK"):
            pristine = [Node(x.id) for x in nodes]
            for c, x in zip(pristine, nodes):
                c.labels, c.stmts, c.term = list(x.labels), list(x.stmts), x.term
            psucc = {k: list(v) for k, v in succ.items()}
        merge_conditions(nodes, succ)
        nodes, succ = split_irreducible(nodes, succ, SPLIT_BUDGET)
        root = search(nodes, succ)
        tidy(root)
        shape_loops(root)
        out = render(root, "    ", [])
        if os.environ.get("SH4_CFG_CHECK"):     # only a tree that renders is used
            from . import cfgcheck
            cfgcheck.check(pristine, psucc, root, op_ends)
    except Fail as e:
        if os.environ.get("SH4_CFG_LOG"):
            with open(os.environ["SH4_CFG_LOG"], "a") as f:
                f.write(f"{getattr(_st, 'CURRENT', '?')} {e}\n")
        return None
    if any(GOTO_ANY.search(x) for x in out):
        return None
    return out
