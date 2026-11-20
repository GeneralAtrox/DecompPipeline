"""Jump tables the function's own body holds: the translated C switches on the index and never reads the table.

MSVC for the Xbox 360 puts a switch's table of target addresses right after the `bctr` (lwzx r0,r12,r0; mtctr r0; bctr;
then the words), inside the function's code range. Reading it is a read of machine code to the judge (rule R8:
runtime tc_rt.c fails the vector). The translator already knows every entry (translate.jump_table runs the
straight-line code for each index the bounding compare allows), so the C need not read the table: the switch is on the
index register, and each case sets what the table load and the code after it would have left (the loaded registers and
CTR, constants per index) before going to its target. The table load itself becomes a placeholder the cases overwrite.
First: Too Human's 82e0a108, 82c6f098, 82ccf058, 82d89388 and 82dcde50 (R8 at the table's first word: the oracle,
TcBank.getChunk, takes a read within 16 bytes of the pc for an instruction fetch, so index 0 was a valid vector there and
a code read in the C; every other index faults R8 in the oracle too and is dropped).

Only when it is exact: every instruction from the first table load to the bctr is one mini_eval models (no store, no
compare, no record form), every load there reads inside the body for every index, every register they write comes out
a constant per index (the same with the index's upper word set), and the index register is not written after the
compare. Otherwise the old form stays (a switch on the loaded target). A table outside the body is data and still read.

Nearest existing: translate.jump_table / joined_table - they find the targets but leave the load in the C.
"""
from __future__ import annotations

from . import translate as T
from .expr import q
from .ir import Assign, Goto, Switch

PURE = {"addi", "addis", "ori", "oris", "xori", "or", "add", "rlwinm", "rldicl", "extsw", "mtspr"}
LOADS = {"lwzx", "lbzx", "lhzx", "lhax", "lwz", "lbz", "lhz", "lha"}
HIGH = 0xA5A5A5A5 << 32          # an index whose upper word is set: what the cases set must not depend on it


def plan(ft, code, at, reg, targets):
    """{reg, writes, cases: [(target, values of writes)], loads} for the table, or None (keep the old form)."""
    runs = []
    for idx, t in enumerate(targets):
        tr, hi = {"loads": []}, {"loads": []}
        if ft.mini_eval(code, at, reg, idx, tr) != t or ft.mini_eval(code, at, reg, idx | HIGH, hi) != t:
            return None
        runs.append((tr, hi))
    pos = {x.addr: j for j, x in enumerate(code)}
    inside = [pos[p] for tr, _ in runs for p, ea in tr["loads"] if _inside(ft, ea)]
    if not inside or min(inside) < at:
        return None
    first = min(inside)
    tail = code[first:]
    loads = {x.addr for x in tail if x.op in LOADS}
    for x in tail:
        if x.rc or not (x.op in LOADS or x.op in PURE) or (x.op == "mtspr" and x.spr != 9) \
                or (x.op == "or" and x.rs != x.rb):
            return None
    for run in runs:
        for tr in run:                   # each load from the first on, for every index: known, and inside the body
            late = [(p, ea) for p, ea in tr["loads"] if pos[p] >= first]
            if {p for p, _ in late} != loads or not all(_inside(ft, ea) for _, ea in late):
                return None
    if any(reg in T.gpr_writes(x) for x in code[at:]):
        return None
    writes = sorted({r for x in tail for r in T.gpr_writes(x)})
    cases = []
    for (tr, hi), t in zip(runs, targets):
        vals = tuple(tr["rv"].get(r) for r in writes)
        if None in vals or vals != tuple(hi["rv"].get(r) for r in writes):
            return None
        cases.append((t, vals))
    return {"reg": reg, "writes": writes, "cases": cases, "loads": loads}


def _inside(ft, ea):
    return ea is not None and ft.in_body(ea)


def keep(ft, i, code, at, reg, targets):
    """targets, after recording the plan for the bctr at i (translate.jump_table / joined_table)."""
    p = plan(ft, code, at, reg, targets)
    if p is not None:
        ft.jt_plans[i.addr] = p
        ft.jt_loads |= p["loads"]
    return targets


def lift_load(ft, i):
    """A table load's statements: a placeholder every case overwrites (the table is not read), or None."""
    if i.addr not in ft.jt_loads:
        return None
    return [Assign(f"r{i.rd}", q(0))]


def switch(ft, a, default):
    """The Switch for the planned bctr at a: on the index, each case setting the registers and CTR, then its target."""
    p = ft.jt_plans[a]
    labels, cases = {}, []
    for idx, (t, vals) in enumerate(p["cases"]):
        if (t, vals) not in labels:
            st = [Assign(f"r{r}", q(v)) for r, v in zip(p["writes"], vals)] + [Assign("ctr", q(t))]
            labels[(t, vals)] = ft.synth(st, Goto(ft.label(t)), f"{a:08x}_case")
        cases.append((idx, labels[(t, vals)]))
    return Switch(T.lo32(T.R(p["reg"])), cases, default)
