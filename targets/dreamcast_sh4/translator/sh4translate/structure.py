"""Loops and if blocks for the translator's goto form (the "struct" pass, run on the finished function body).

The body is a list of statements at one level: labels (`L_x:;`), gotos (`goto L_x;`, `if (c) goto L_x;`) and other
statements (a brace group such as `{ u32 c_ = t; ... }` or a `switch` is one opaque statement). Four rewrites, each
only where nothing outside the rewritten region jumps into it, so every statement keeps its order and every path
through the code is the same:

  do-while   `L: S  if (c) goto L;`                         -> `do { S } while (c);`   (L referenced only there)
  for (;;)   `L: S  goto L;`                                -> `for (;;) { S }`
  test first `goto Lc; Lb: X  Lc: Y  if (c) goto Lb;`       -> `for (;;) { Y  if (!c) break;  X }`
                                                               (`while (c) { X }` when Y is empty)
  if-else    `if (c) goto L1; S1  goto L2; L1: S2  L2:`     -> `if (!c) { S1 } else { S2 } L2:`
  if         `if (c) goto L; S  L:`                         -> `if (!c) { S } L:`

A label no goto names any more is dropped. A loop is never made around a `break` that belongs to an enclosing loop
(it would then end the new loop instead). Smallest regions first, repeated until nothing changes; a goto stays only
where the flow does not fit these shapes (several entries into a region: irreducible flow).
"""
from __future__ import annotations

import re
from collections import Counter

LABEL = re.compile(r"^(L_\w+):;$")
GOTO = re.compile(r"^goto (L_\w+);$")
IFGOTO = re.compile(r"^if \((.+)\) goto (L_\w+);$")
GOTO_ANY = re.compile(r"\bgoto (L_\w+);")
LABEL_ANY = re.compile(r"^\s*(L_\w+):", re.M)
IDENT = re.compile(r"^!?[A-Za-z_]\w*$")


class U:
    __slots__ = ("kind", "name", "cond", "lines", "a", "b")

    def __init__(self, kind, name=None, cond=None, lines=None, a=None, b=None):
        self.kind, self.name, self.cond, self.lines, self.a, self.b = kind, name, cond, lines, a, b


def is_comment(u):
    return u.kind == "op" and all(x.strip().startswith("/*") for x in u.lines)


INVERT = {"==": "!=", "!=": "==", "<": ">=", ">=": "<", ">": "<=", "<=": ">"}


def top_ops(c):
    """[(index, operator)] of the comparison and logical operators outside any parentheses in c."""
    out, depth, k = [], 0, 0
    while k < len(c):
        ch = c[k]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif depth == 0:
            two = c[k:k + 2]
            if two in ("&&", "||", "==", "!=", "<=", ">=", "<<", ">>"):
                if two not in ("<<", ">>"):
                    out.append((k, two))
                k += 2
                continue
            if ch in "<>?":
                out.append((k, ch))
        k += 1
    return out


def wrapped(c):
    """c is one parenthesised group."""
    if not (c.startswith("(") and c.endswith(")")):
        return False
    depth = 0
    for k, ch in enumerate(c):
        depth += {"(": 1, ")": -1}.get(ch, 0)
        if depth == 0 and k < len(c) - 1:
            return False
    return True


# a condition that may compare floats: an ordered float comparison is false for NaN both ways round, so its negation
# stays !(...) (== and != are each other's negation for floats too)
FLOATY = re.compile(r"\bfr\d+\b|\b(?:rdf32|u2f|f_abs|f_dn|sh4_\w+)\s*\(|\(f32\)|\d\.\d*f\b")


def neg(c):
    """The negation of condition c (an integer comparison turns around; an ordered float one does not)."""
    c = c.strip()
    if IDENT.match(c):
        return c[1:] if c.startswith("!") else "!" + c
    if c.startswith("!") and wrapped(c[1:]):
        return c[2:-1]
    if wrapped(c):
        return neg(c[1:-1])
    ops = top_ops(c)
    logic = [(k, op) for k, op in ops if op in ("&&", "||", "?")]
    if logic and len({op for _, op in logic}) == 1 and logic[0][1] != "?":
        # De Morgan: !(A && B) = !A || !B
        parts, last = [], 0
        for k, op in logic:
            parts.append(c[last:k].strip())
            last = k + 2
        parts.append(c[last:].strip())
        other = "||" if logic[0][1] == "&&" else "&&"
        return f" {other} ".join(paren(neg(x)) for x in parts)
    if len(ops) == 1 and ops[0][1] in INVERT and not (ops[0][1] in ("<", ">", "<=", ">=") and FLOATY.search(c)):
        k, op = ops[0]
        return f"{c[:k].rstrip()} {INVERT[op]} {c[k + len(op):].lstrip()}"
    return f"!({c})"


def paren(c):
    """c as an operand of && or ||."""
    return f"({c})" if any(op in ("&&", "||", "?") for _, op in top_ops(c)) else c


def parse(lines):
    out, i = [], 0
    while i < len(lines):
        x = lines[i]
        y = x.strip()
        m = LABEL.match(y)
        if m:
            out.append(U("label", m.group(1)))
            i += 1
            continue
        m = GOTO.match(y)
        if m:
            out.append(U("goto", m.group(1)))
            i += 1
            continue
        m = IFGOTO.match(y)
        if m and "{" not in y:
            out.append(U("goto", m.group(2), m.group(1)))
            i += 1
            continue
        group = [x]
        depth = y.count("{") - y.count("}")
        while depth > 0 and i + 1 < len(lines):
            i += 1
            group.append(lines[i])
            depth += lines[i].count("{") - lines[i].count("}")
        if depth != 0:
            raise ValueError("unbalanced braces")
        out.append(U("op", lines=[g[4:] if g.startswith("    ") else g for g in group]))
        i += 1
    return out


def summary(u, memo):
    """(labels inside, gotos inside by target, a break inside that is not bound to a loop inside u)."""
    k = id(u)
    if k in memo:
        return memo[k]
    if u.kind == "label":
        r = ({u.name}, Counter(), False)
    elif u.kind == "goto":
        r = (set(), Counter([u.name]), False)
    elif u.kind == "op":
        text = "\n".join(u.lines)
        r = (set(LABEL_ANY.findall(text)), Counter(GOTO_ANY.findall(text)), u.lines == ["@BREAK"])
    else:
        labels, gotos, brk = set(), Counter(), False
        for v in (u.a or []) + (u.b or []):
            ls, gs, b = summary(v, memo)
            labels |= ls
            gotos += gs
            brk = brk or b
        if u.kind in ("do", "loop", "while"):
            brk = False         # its breaks end it
        r = (labels, gotos, brk)
    memo[k] = r
    return r


def region(us, memo):
    labels, gotos, brk = set(), Counter(), False
    for u in us:
        ls, gs, b = summary(u, memo)
        labels |= ls
        gotos += gs
        brk = brk or b
    return labels, gotos, brk


def closed(us, refs, memo, skip=()):
    """No goto from outside us names a label inside it (labels in skip are the caller's to check)."""
    labels, gotos, brk = region(us, memo)
    return all(gotos[x] == refs[x] for x in labels if x not in skip), brk


def candidates(us, refs, memo, found):
    pos = {u.name: k for k, u in enumerate(us) if u.kind == "label"}
    for g, u in enumerate(us):
        if u.kind != "goto":
            continue
        p = pos.get(u.name, -1)
        if u.cond is not None:
            # two conditional gotos in a row: `if (A) goto L; if (B) goto L;` -> `if (A || B) goto L;` and
            # `if (A) goto L1; if (B) goto L2; L1:` -> `if (!A && B) goto L2; L1:` (B is evaluated only when A
            # is false, as before)
            h = g + 1
            while h < len(us) and is_comment(us[h]):
                h += 1
            v = us[h] if h < len(us) else None
            if v is not None and v.kind == "goto" and v.cond is not None:
                a_, b_ = u.cond, v.cond
                if v.name == u.name:
                    found.append((h - g, us, g, h + 1, lambda us, s, e, a_=a_, b_=b_, n=u.name:
                                  us[s + 1:e - 1] + [U("goto", n, f"{paren(a_)} || {paren(b_)}")]))
                    continue
                if p > h and all(w.kind == "label" or is_comment(w) for w in us[h + 1:p]):
                    found.append((h - g, us, g, h + 1, lambda us, s, e, a_=a_, b_=b_, n=v.name:
                                  us[s + 1:e - 1] + [U("goto", n, f"{paren(neg(a_))} && {paren(b_)}")]))
                    continue
        if p < 0:
            continue
        if p > g and all(v.kind == "label" or is_comment(v) for v in us[g + 1:p]):
            # a goto to the next statement (its condition is a flag: no effect)
            found.append((0, us, g, g + 1, lambda us, s, e: []))
            continue
        if p < g:
            if refs[u.name] != 1:
                continue
            ok, brk = closed(us[p + 1:g], refs, memo)
            if ok and not brk:
                if u.cond is None:
                    found.append((g - p, us, p, g + 1, lambda us, s, e: [U("loop", a=us[s + 1:e - 1])]))
                else:
                    c = u.cond
                    found.append((g - p, us, p, g + 1,
                                  lambda us, s, e, c=c: [U("do", cond=c, a=us[s + 1:e - 1])]))
                continue
            # test first: goto Lc; Lb: X  Lc: Y  if (c) goto Lb;
            if p >= 1 and u.cond is not None and us[p - 1].kind == "goto" and us[p - 1].cond is None:
                lc = us[p - 1].name
                m = pos.get(lc)
                if m is None or not (p < m < g) or refs[lc] != 1:
                    continue
                ok, brk = closed(us[p + 1:g], refs, memo, skip=(lc,))
                if not ok or brk:
                    continue
                c = u.cond

                def build(us, s, e, c=c, m=m, p=p):
                    x, y = us[p + 1:m], us[m + 1:e - 1]
                    if not y:
                        return [U("while", cond=c, a=x)]
                    return [U("loop", a=y + [U("op", lines=["@BREAK"], cond=neg(c))] + x)]
                found.append((g - p + 1, us, p - 1, g + 1, build))
        else:
            if u.cond is None or p == g + 1:
                continue
            c = u.cond
            # if-else: if (c) goto L1; S1  goto L2; L1: S2  L2:
            prev = us[p - 1]
            if refs[u.name] == 1 and prev.kind == "goto" and prev.cond is None and prev.name in pos:
                q = pos[prev.name]
                if q > p + 1 and p - 1 > g + 1:
                    ok1, _ = closed(us[g + 1:p - 1], refs, memo)
                    ok2, _ = closed(us[p + 1:q], refs, memo)
                    if ok1 and ok2:
                        found.append((q - g, us, g, q,
                                      lambda us, s, e, c=c, p=p: [U("if", cond=neg(c), a=us[s + 1:p - 1],
                                                                    b=us[p + 1:e])]))
                        continue
            ok, _ = closed(us[g + 1:p], refs, memo)
            if ok:
                found.append((p - g, us, g, p, lambda us, s, e, c=c: [U("if", cond=neg(c), a=us[s + 1:e])]))
    for u in us:
        for sub in (u.a, u.b):
            if sub:
                candidates(sub, refs, memo, found)


def tail_candidates(root, found, limit=4):
    """A goto (from any depth) to a label whose code is a few statements ending in a return or halt: the goto
    becomes a copy of them (an early return; the code at the label stays for whatever else reaches it)."""
    where = {}

    def walk(us):
        for k, u in enumerate(us):
            if u.kind == "label":
                where[u.name] = (us, k)
            for sub in (u.a, u.b):
                if sub:
                    walk(sub)
    walk(root)
    tails = {}
    for name, (us, k) in where.items():
        lines = []
        for v in us[k + 1:]:
            if v.kind == "label":
                continue
            if v.kind != "op" or v.lines == ["@BREAK"] or any(GOTO_ANY.search(x) or LABEL_ANY.search(x)
                                                               for x in v.lines):
                break
            lines += [x for x in v.lines if not x.strip().startswith("/*")]
            if len(lines) > limit:
                break
            last = lines[-1].strip() if lines else ""
            if last.startswith("return") or last == "tc_halt();":
                tails[name] = lines
                break

    def gotos(us):
        for g, u in enumerate(us):
            if u.kind == "goto" and u.name in tails:
                body = [U("op", lines=list(tails[u.name]))]
                if u.cond is None:
                    found.append((1, us, g, g + 1, lambda us, s, e, body=body: body))
                else:
                    found.append((1, us, g, g + 1, lambda us, s, e, body=body, c=u.cond: [U("if", cond=c, a=body)]))
            for sub in (u.a, u.b):
                if sub:
                    gotos(sub)
    gotos(root)


def lead_labels(us):
    """The labels at the start of us (before its first statement), and whether us is nothing but labels."""
    out = set()
    for v in us:
        if is_comment(v):
            continue
        if v.kind != "label":
            return out, False
        out.add(v.name)
    return out, True


def cleanup(us, fall=frozenset()):
    """Gotos that do nothing: to where control goes anyway (the next statement, also from the end of an if
    block), and a goto to a label that only says `goto M` goes to M (jump threading). fall: the labels control
    reaches by running off the end of us."""
    changed = False
    k = 0
    while k < len(us):
        u = us[k]
        after, only = lead_labels(us[k + 1:])
        reach = after | (fall if only else set())
        if u.kind == "goto" and u.name in reach:
            del us[k]
            changed = True
            continue
        if u.kind == "if":
            for sub in (u.a, u.b):
                if sub and cleanup(sub, frozenset(reach)):
                    changed = True
            inner = [v for v in u.a if not is_comment(v)]
            if not u.b and len(inner) == 1 and inner[0].kind == "if" and not inner[0].b:
                # if (A) { if (B) { X } } -> if (A && B) { X }
                v = inner[0]
                u.cond = f"{paren(u.cond)} && {paren(v.cond)}"
                u.a = [w for w in u.a if is_comment(w)] + v.a
                u.b = None
                changed = True
        elif u.kind in ("do", "while", "loop"):
            if cleanup(u.a):
                changed = True
        k += 1
    return changed


def thread(root):
    """Retarget gotos to a label followed (after other labels) by `goto M` straight to M."""
    to = {}

    def walk(us):
        for k, u in enumerate(us):
            if u.kind == "label":
                for v in us[k + 1:]:
                    if v.kind == "label" or is_comment(v):
                        continue
                    if v.kind == "goto" and v.cond is None and v.name != u.name:
                        to[u.name] = v.name
                    break
            for sub in (u.a, u.b):
                if sub:
                    walk(sub)
    walk(root)
    if not to:
        return False

    def final(x):
        seen = set()
        while x in to and x not in seen:
            seen.add(x)
            x = to[x]
        return x
    changed = False

    def fix(us):
        nonlocal changed
        for u in us:
            if u.kind == "goto" and u.name in to:
                y = final(u.name)
                if y != u.name:
                    u.name = y
                    changed = True
            for sub in (u.a, u.b):
                if sub:
                    fix(sub)
    fix(root)
    return changed


def ends(u):
    """Control never goes on past u."""
    if u.kind == "goto":
        return u.cond is None
    if u.kind == "op" and u.lines != ["@BREAK"]:
        from .cfgstruct import op_ends  # cfgstruct imports this module
        return op_ends(u.lines)          # a return or halt, or a scope `{ u32 g_ = ...; ... return r0; }` ending so
    return False


def prune(us, refs, memo):
    """Drop code nothing reaches any more (after a return, halt or goto, up to a label something names): left
    behind when a tail copy took the last goto to its label."""
    changed = False
    k = 0
    while k < len(us):
        if ends(us[k]):
            j = k + 1
            while j < len(us) and not any(refs[x] for x in summary(us[j], memo)[0]):
                j += 1
            if j > k + 1 and any(v.kind != "label" for v in us[k + 1:j]):
                del us[k + 1:j]
                changed = True
        k += 1
    for u in us:
        for sub in (u.a, u.b):
            if sub and prune(sub, refs, memo):
                changed = True
    return changed


def all_refs(us, memo):
    return region(us, memo)[1]


def render(us, ind, refs, out):
    for u in us:
        if u.kind == "label":
            if refs[u.name]:
                out.append(f"{ind[4:]}{u.name}:;")
        elif u.kind == "goto":
            out.append(f"{ind}goto {u.name};" if u.cond is None else f"{ind}if ({u.cond}) goto {u.name};")
        elif u.kind == "op":
            if u.lines == ["@BREAK"]:
                out.append(f"{ind}if ({u.cond}) break;")
            else:
                out += [ind + x for x in u.lines]
        elif u.kind == "if":
            out.append(f"{ind}if ({u.cond}) {{")
            render(u.a, ind + "    ", refs, out)
            if u.b:
                out.append(f"{ind}}} else {{")
                render(u.b, ind + "    ", refs, out)
            out.append(f"{ind}}}")
        elif u.kind == "do":
            out.append(f"{ind}do {{")
            render(u.a, ind + "    ", refs, out)
            out.append(f"{ind}}} while ({u.cond});")
        elif u.kind == "while":
            out.append(f"{ind}while ({u.cond}) {{")
            render(u.a, ind + "    ", refs, out)
            out.append(f"{ind}}}")
        elif u.kind == "loop":
            out.append(f"{ind}for (;;) {{")
            render(u.a, ind + "    ", refs, out)
            out.append(f"{ind}}}")
    return out


def structure(lines):
    """The body lines (4-space base indent, labels at column 0) with loops and if blocks for the goto shapes."""
    try:
        us = parse(lines)
    except ValueError:
        return lines        # not a shape this pass reads: the goto form stays
    for _ in range(10000):
        memo = {}
        refs = all_refs(us, memo)
        found = []
        candidates(us, refs, memo, found)
        if not found:
            if cleanup(us) | thread(us) | prune(us, refs, memo):
                continue
            tail_candidates(us, found)     # only where no shape above fits: copying code is the last resort
        if not found:
            break
        found.sort(key=lambda f: f[0])
        taken = {}
        for span, lst, s, e, build in found:
            rs = taken.setdefault(id(lst), (lst, []))[1]
            if any(s < e2 and s2 < e for s2, e2, _ in rs):
                continue
            rs.append((s, e, build))
        for lst, rs in taken.values():
            for s, e, build in sorted(rs, key=lambda r: -r[0]):
                lst[s:e] = build(lst, s, e)
    memo = {}
    out = render(us, "    ", all_refs(us, memo), [])
    if any(GOTO_ANY.search(x) for x in out):
        # shapes the rewrites above do not fit: lay the whole function out from its control flow graph instead
        from .cfgstruct import structure_cfg
        alt = structure_cfg(lines)
        if alt is not None:
            return alt
    return out
