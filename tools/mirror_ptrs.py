"""List the functions whose 0x0c...... RAM pointers may be compared raw (the mirror rule, docs/PROOF_GATES.md).

usage: python tools/mirror_ptrs.py --project DIR [--write] [--json FILE]

The judge takes 0x0c...... and 0x8c...... as one main-RAM address (core/compare.same_word): matched C writes a pool's
0x0c...... pointer as 0x8c......, and both reach the same RAM. They differ only where code compares the pointer itself
(cmp/eq, cmp/hs ... between two registers: a list walk's `p == head`, a range check), which the rule no longer judges.
Static and text only (packets, matched/ C): for every matched function that loads a 0x0c...... data word (not a
function address) from its pool, the pointer is followed through its own asm, then into the callees it reaches as an
argument (r4..r7, two calls deep):
- self: the function compares it with a register that doesn't hold such a pointer (two of them, as in a `p < end`
  loop over one table, see the same view);
- callee: a callee compares the argument the same way (callee, register, depth);
- unscanned: a callee that gets it has no packet to read.
The scan is a may-flow over the listing (core/sh4_asm.Flow): a register stays the pointer through mov and add until
written, and a call ends it in r0..r7.
`c` says how the matched C writes each word: 0x8c (the other view), 0x0c (as the original) or none (computed).
--write puts the review list in the workspace's state/mirror_ptrs/: review.txt (`addr reason`, the self and callee
rows whose C doesn't write every such word as 0x0c) and summary.json. It requeues nothing: the rows are for review.
"""
import argparse
import json
import pathlib
import re
import sys

TOOL = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL / "tools"))
sys.path.insert(0, str(TOOL))
from common import use_project  # noqa: E402

CMP = {"cmp/eq", "cmp/hs", "cmp/hi", "cmp/ge", "cmp/gt"}
ARGS = ("r4", "r5", "r6", "r7")
SCRATCH = tuple(f"r{i}" for i in range(8))
REG = re.compile(r"r(?:1[0-5]|[0-9])")
DEPTH = 2


def ram_word(v):
    return v >> 24 == 0x0C


def step(x, t, words, cmps, stores):
    """One instruction's effect on the set t of registers that may hold the pointer."""
    from core import sh4_asm
    ops = x.ops or []
    regs = [o for o in ops if REG.fullmatch(o)]
    dst = ops[-1] if ops else ""
    pv = x.pool_value() if x.mn == "mov.l" else None
    if pv and REG.fullmatch(dst):
        t.discard(dst)
        if pv[0] in words and pv[1] != "function":
            t.add(dst)
    elif x.mn in CMP and len(regs) == 2:
        if len(t & set(regs)) == 1:  # both: one view on both sides (p < end over one table)
            cmps.add(x.addr)
    elif x.mn == "mov" and len(regs) == 2:
        (t.add if regs[0] in t else t.discard)(regs[1])
    elif x.mn == "add" and len(regs) == 2:
        if regs[0] in t:
            t.add(dst)  # base + index: still a pointer into the same view
    elif x.mn == "add" and len(ops) == 2:
        pass  # add #k: the same pointer, moved
    elif x.mn.startswith("mov") and dst.startswith("@") and ops[0] in t:
        stores.add(x.addr)
    else:
        for r in list(t):
            if sh4_asm.uses(x, r)[1]:
                t.discard(r)


def follow(ins, tainted, words=()):
    """May-flow over ins (core/sh4_asm.Flow) of the registers that hold the pointer: `tainted` at entry, and the pool
    loads of `words`. -> (compares [addr], calls [(callee, regs)], stores [addr])."""
    from core import sh4_asm
    flow, before = sh4_asm.Flow(ins), {0: frozenset(tainted)}
    cmps, stores, calls, work = set(), set(), {}, [0]
    while work:
        i = work.pop()
        t = set(before[i])
        step(ins[i], t, words, cmps, stores)
        prev = ins[i - 1] if i else None
        if ins[i].slot and prev is not None and (prev.mn in sh4_asm.CALLS or
                                                 (prev.mn in ("bra", "jmp") and prev.call_target())):
            if prev.call_target() and t & set(ARGS):  # the delay slot runs before the call
                calls.setdefault(prev.call_target(), set()).update(t & set(ARGS))
        for j, kind in flow.succ(i) or []:
            if j is None:
                continue
            new = frozenset(t - set(SCRATCH) if kind == "call" else t) | before.get(j, frozenset())
            if new != before.get(j):
                before[j] = new
                work.append(j)
    return ([f"{a:08x}" for a in sorted(cmps)], [(c, sorted(r)) for c, r in sorted(calls.items())],
            [f"{a:08x}" for a in sorted(stores)])


def scan_callee(rp, callee, regs, depth, memo):
    """[(callee, regs, depth, compares)] for the callees, from `callee` down, that compare the pointer; and the
    callees with no packet."""
    key = (callee, tuple(regs))
    if key in memo:
        return memo[key]
    memo[key] = ([], [])
    ins = rp.insns(callee)
    if not ins:
        memo[key] = ([], [callee])
        return memo[key]
    cmps, calls, _ = follow(ins, regs)
    hits, unscanned = ([(callee, list(regs), depth, cmps)] if cmps else []), []
    if depth < DEPTH:
        for c, r in calls:
            h, u = scan_callee(rp, c, r, depth + 1, memo)
            hits += h
            unscanned += u
    memo[key] = (hits, unscanned)
    return memo[key]


def how_written(v, text):
    lo = text.lower()
    if f"0x{v:08x}" in lo:
        return "0x0c"
    return "0x8c" if f"0x{v | 0x80000000:08x}" in lo else "none"


def scan(ws):
    from core import paths, ret_use
    rp = ret_use.Project(ws)
    rows, memo = [], {}
    for matched in sorted(paths.MATCHED.glob("*.c")):
        a = matched.stem
        ins = rp.insns(a)
        if not ins:
            continue
        words = {pv[0] for x in ins if x.mn == "mov.l" for pv in [x.pool_value()]
                 if pv and pv[1] != "function" and ram_word(pv[0])}
        if not words:
            continue
        cmps, calls, stores = follow(ins, (), words)
        hits, unscanned = [], []
        for c, r in calls:
            h, u = scan_callee(rp, c, r, 1, memo)
            hits += h
            unscanned += u
        text = matched.read_text(encoding="utf-8", errors="replace")
        c = {f"{v:08x}": how_written(v, text) for v in sorted(words)}
        rows.append({"addr": a, "c": c, "self": cmps, "stores": stores,
                     "callee": [{"callee": h[0], "regs": h[1], "depth": h[2], "compares": h[3]} for h in hits],
                     "unscanned": sorted(set(unscanned))})
    return rows


def review(rows):
    """{addr: [reason]}: the self and callee rows whose C doesn't write every pointer word as the original does."""
    out = {}
    for r in rows:
        if all(v == "0x0c" for v in r["c"].values()):
            continue
        if r["self"]:
            out.setdefault(r["addr"], []).append("self compares the pointer at " + ", ".join(r["self"]))
        for h in r["callee"]:
            out.setdefault(r["addr"], []).append(
                f"callee {h['callee']} ({'/'.join(h['regs'])}, depth {h['depth']}) compares it at "
                + ", ".join(h["compares"]))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--project", required=True)
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--json")
    a = ap.parse_args()
    ws = use_project(a.project)
    rows = scan(ws)
    rev = review(rows)
    summary = {"project": str(ws), "functions": len(rows),
               "written_other_view": sum(any(v == "0x8c" for v in r["c"].values()) for r in rows),
               "self": sum(bool(r["self"]) for r in rows), "callee": sum(bool(r["callee"]) for r in rows),
               "review": len(rev), "unscanned_callees": len({u for r in rows for u in r["unscanned"]})}
    print(json.dumps(summary))
    for addr, why in sorted(rev.items()):
        print(f"  {addr}  " + "; ".join(why))
    if a.write:
        out = ws / "state" / "mirror_ptrs"
        out.mkdir(parents=True, exist_ok=True)
        (out / "review.txt").write_text("".join(f"{addr} {'; '.join(why)}\n" for addr, why in sorted(rev.items())),
                                        encoding="utf-8")
        (out / "summary.json").write_text(json.dumps(dict(summary, rows=rows), indent=1), encoding="utf-8")
        print(f"wrote {out}")
    if a.json:
        pathlib.Path(a.json).write_text(json.dumps(dict(summary, rows=rows), indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
