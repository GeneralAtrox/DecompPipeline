"""structure()'s rounds: the rewrites applied round after round until none applies, on pieces of the function that
settle alone where it has them. In its own file (the size cap on structure.py); it reads structure's functions at call
time (structure imports this file).
"""


def settle_pieces(us, lines):
    """us (parse(lines)) settled: piece by piece where segments cuts it (the same answer, far fewer rounds), else whole.
    A piece whose cut stops holding, or a round count where the whole's cap of 10000 could bind, starts the whole again."""
    from . import structure as S
    segs, rounds = segments(us), 0
    if len(segs) > 1:
        for k, seg in enumerate(segs):
            n = settle(seg, k < len(segs) - 1)
            if n is None or rounds + n + len(segs) >= 10000:
                break
            rounds += n
        else:
            return [u for seg in segs for u in seg]
        us = S.parse(lines)         # the pieces changed us's statements: the whole again, from the start
    settle(us)
    return us


def settle(us, cut=False):
    """structure's rewrites on us, round after round until none applies (at most 10000): the rounds run. cut: us is a
    piece before a cut (segments); None, us half done, once its last statement no longer blocks."""
    from . import structure as S
    for n in range(1, 10001):
        memo = {}
        refs = S.all_refs(us, memo)
        found = []
        S.candidates(us, refs, memo, found)
        if not found:
            if S.cleanup(us) | S.thread(us) | S.prune(us, refs, memo):
                if cut and not (us and blocking(us[-1])):
                    return None
                continue
            S.tail_candidates(us, found)     # only where no shape above fits: copying code is the last resort
        if not found:
            return n
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
        if cut and not (us and blocking(us[-1])):
            return None
    return 10000


def blocking(u):
    """No rewrite of structure() looks past u to the statements after it, or back past them to u: a conditional goto
    (the shapes need its label on its own side), a block, or an opaque statement holding a goto or label that does not
    end (the early-return copy stops at it; dropping unreachable code starts only after an ending one)."""
    from . import structure as S
    if u.kind in ("if", "do", "while", "loop"):
        return True
    if u.kind == "goto":
        return u.cond is not None
    if u.kind != "op" or u.lines == ["@BREAK"] or S.ends(u):
        return False
    return any(S.GOTO_ANY.search(x) or S.LABEL_ANY.search(x) for x in u.lines)


def segments(us):
    """us (a function's statement list) cut where no goto at any depth crosses and the statement before the cut is
    blocking: the pieces settle alone, as they do inside the whole (a goto names a label on its own side, and no rewrite
    looks across). A deep piece then takes its many rounds on its own statements, not on the whole function's (Too
    Human's 8237bd08: about 1000 rounds over 63,000 statements)."""
    from . import structure as S
    n, memo, span = len(us), {}, {}
    for i, u in enumerate(us):
        ls, gs, _ = S.summary(u, memo)
        for x in (*ls, *gs):
            a, b = span.get(x, (i, i))
            span[x] = (min(a, i), max(b, i))
    cover = [0] * (n + 1)
    for a, b in span.values():
        if a < b:
            cover[a] += 1
            cover[b] -= 1
    out, s, c = [], 0, 0
    for i in range(n - 1):
        c += cover[i]
        if c == 0 and blocking(us[i]):
            out.append(us[s:i + 1])
            s = i + 1
    out.append(us[s:])
    return out


def too_deep(out):
    """More nested blocks than the compiler takes (MSVC's C1061 came at about 260; Too Human's deepest that compiles has 247):
    the goto form stays for such a function, flat at any size. A function under the limit is written as before."""
    depth = most = 0
    for x in out:
        for c in x:
            if c == "{":
                depth += 1
                most = max(most, depth)
            elif c == "}":
                depth -= 1
    return most > 250
