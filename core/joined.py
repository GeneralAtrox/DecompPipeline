"""Stale jump-in parents: functions whose body a catalog run grew over a function it removed.

TcJumpIn (core/ghidra._fixups) makes a jump-in continuation part of the function whose frame it pops: the continuation
stops being a function and its code joins that function's body. A parent matched before the join was judged on its old
body, and its matched C still calls the continuation as a function of its own (Spawn ovl 8c5a70c0 calls its own
epilogue 8c5a73a2; PS2 main 8c17cb20 the `sts.l pr` 8c17cb34 in the middle of its prologue). decomp.py setup, catalog
and seed requeue such a parent with a note (tools/requeue.py): its next job judges the matched C again on the new
body first, and an agent runs only if that fails.

The test, on the catalog before and after the run: an entry that is no function any more now lies in the body of a
function that was there before and did not hold it (grown), and that function's matched C still names the entry
(stale). Only that C is stale: a body can grow over code its C already did inline (Spawn ovl 8c5946b2, 8c5a9a00 and
8c5ccc20 stay matched). On the game repos' history this finds the 10 parents requeued by hand on 2026-10-21 and the 4
requeued on 2026-10-19 (Spawn ovl 8c595160, 8c5aa732; Spawn main 8c021282, PS2 main 8c17c080), and nothing else.
"""
import re

# A mention of the entry: a 0x literal (tc_call, tc_callf, tc_int_call, a local helper's argument, a comment) or a name
# ending in it (fn_, FUN_, sub_, LAB_). PS2 8c17c080 called 8c17c0e0 by tc_callf, Spawn ovl 8c5a73e0 8c5a79d8 by
# its own call_fn.
NAME = re.compile(r"(?:0x|\b[A-Za-z]+_)([0-9a-fA-F]{8})(?![0-9a-fA-F])")


def read_bodies(text):
    """state/function_bodies.txt ("entry lo hi" per body range, hi exclusive) -> {entry: [(lo, hi), ...]}."""
    out = {}
    for line in (text or "").splitlines():
        w = line.split()
        if len(w) == 3:
            out.setdefault(w[0].lower(), []).append((int(w[1], 16), int(w[2], 16)))
    return out


def grown(before, before_bodies, after, after_bodies):
    """{parent: [removed entries its body took]} for parents in both catalogs. before and after: function address
    sets; *_bodies: read_bodies of each run's function_bodies.txt."""
    out = {}
    for g in sorted(set(before) - set(after)):
        x = int(g, 16)
        for f, ranges in after_bodies.items():
            if f in before and any(lo <= x < hi for lo, hi in ranges) and not any(
                    lo <= x < hi for lo, hi in before_bodies.get(f, [])):
                out.setdefault(f, []).append(g)
    return out


def stale(grown_parents, matched_c):
    """{parent: [removed entries its matched C names]}. matched_c(addr): the parent's matched C text, None when it is
    not matched."""
    out = {}
    for p, kids in sorted(grown_parents.items()):
        text = matched_c(p)
        names = {a.lower() for a in NAME.findall(text)} if text else set()
        hit = [k for k in kids if k in names]
        if hit:
            out[p] = hit
    return out
