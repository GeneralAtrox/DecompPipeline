"""structure.candidates' test of whether a span of a statement list is closed (no goto from outside it names a label
inside it), answered from one index per list and round instead of a walk of the span. In its own file (the size cap
on structure.py). summary: structure.summary, passed in (structure imports this file).
"""
from bisect import bisect_left
from collections import Counter
from itertools import accumulate


def list_counts(summary, us, memo):
    """(the summary of each unit of us, gotos in us by target, the first and the last unit of us with a goto to each),
    once per list and round (memo). For the root list the counts are structure.all_refs (the gotos of the function)."""
    key = ("cnt", id(us))
    if key in memo:
        return memo[key]
    sums = [summary(u, memo) for u in us]
    cnt, first, last = Counter(), {}, {}
    get = cnt.get
    for i, (_, gs, _) in enumerate(sums):
        for x, c in gs.items():
            cnt[x] = get(x, 0) + c
            if x not in first:
                first[x] = i
            last[x] = i
    memo[key] = r = (sums, cnt, first, last)
    return r


def span_index(summary, us, refs, memo):
    """For closed_at: the units of us holding a label with gotos (in order), for each the first and last unit of us
    with a goto to one of its labels, prefix counts of those with a goto from outside us, and prefix counts of loose
    breaks over all units. Built once per list and round (memo), so closed_at does not walk the span: that walk made
    structure() quadratic in a big function's statements (Too Human's 8237bd08: 66 million summaries, minutes). Only
    label units are indexed (range min / max by slices of them): a table over every unit cost more than the queries."""
    key = ("span", id(us))
    if key in memo:
        return memo[key]
    sums, cnt, first, last = list_counts(summary, us, memo)
    n = len(us)
    pos, lo, hi, bad = [], [], [], [0]
    for i, (ls, _, _) in enumerate(sums):
        if not ls:
            continue
        b, a_, z_ = 0, n, -1
        for x in ls:
            r = refs[x]
            if not r:
                continue
            if cnt.get(x, 0) != r:          # a goto to it from outside this list: no span of the list is closed
                b = 1
                continue
            a_, z_ = min(a_, first[x]), max(z_, last[x])
        pos.append(i)
        lo.append(a_)
        hi.append(z_)
        bad.append(bad[-1] + b)
    brk = list(accumulate((1 if s[2] else 0 for s in sums), initial=0))
    memo[key] = r = (pos, lo, hi, bad, brk)
    return r


def closed_at(summary, us, s, e, refs, memo, skip=None):
    """(no goto from outside us[s:e] names a label inside it, the label at us[skip] aside; a loose break inside), from
    span_index: every label inside has all its gotos inside (first >= s, last < e, none from outside the list). The
    same answer as the walk it replaced (structure.region over the span: tests/test_structure_speed.py)."""
    pos, lo, hi, bad, brk = span_index(summary, us, refs, memo)
    ok = True
    for a, z in ((s, e),) if skip is None else ((s, skip), (skip + 1, e)):
        if a < z:
            i, j = bisect_left(pos, a), bisect_left(pos, z)
            if i < j and (bad[j] != bad[i] or min(lo[i:j]) < s or max(hi[i:j]) >= e):
                ok = False
    return ok, brk[e] > brk[s] if e > s else False
