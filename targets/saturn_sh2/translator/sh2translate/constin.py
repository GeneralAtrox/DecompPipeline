"""The constant a register holds at the start of a block, when every way into the block leaves the same one (a range
check's limit is often loaded before a loop head, in a delay slot, or blocks earlier: GCC keeps `mov #9,r11` in a
callee-saved register across the loop and its calls). Moved out of translate.FunctionTranslator.const_in, which may not
grow, and followed back through blocks that leave the register alone.

Nearest existing: translate.FunctionTranslator.const_in (it looked one block back only, and stopped at every call).
r8..r14 stay across a call here because the C does keep them (translate.py: only r0 changes across DP's mocked callee).
A way into a block that nothing known leads to (a case of a jump table not yet resolved: its own jump comes from the
check being asked about) or that only goes round a loop already on the way says nothing new.
"""
DEAD = ("dead",)


def const_in(tr, start, v, seen=()):
    """v, or the constant every edge into the block at start leaves in the register v still holds unchanged."""
    r = _walk(tr, start, v, seen)
    return v if r == DEAD else r


def _walk(tr, start, v, seen):
    if v[0] != "x" or not v[1].startswith("r") or v[2] != 1 or v[3] != 0 or start == tr.entry or \
            start in tr.pr_targets:
        return v    # (the entry and a PR return target have edges succ does not list)
    got = set()
    for p, succ in tr.succ.items():
        if start not in succ:
            continue
        if tr.insns[p].op in ("jsr", "bsr", "bsrf") and not 8 <= int(v[1][1:]) <= 14:
            return v    # the edge returns from a call, which may change any register
        b = tr.block_start(p)
        q = tr.symexec(b, p + (4 if tr.insns[p].delayed else 2))[v[1]]
        if q == v:
            q = DEAD if b in seen + (start,) else _walk(tr, b, v, seen + (start,))  # the block leaves it alone
        if q != DEAD:
            got.add(q if q[0] == "c" else None)
    return DEAD if not got else got.pop() if len(got) == 1 and None not in got else v


def hw_in(tr, start):
    """{register: hardware address} that a block starts with, for its accesses to reach tc_hw_* (SMPC's `mov.l SF,r1`
    before a polling loop; a DMA base kept across branches): rd8/wr8 fault on hardware in the test runtime. One forward
    pass over the function, kept to hardware addresses so other C does not move (const_in per block and register took
    minutes on large functions)."""
    if getattr(tr, "hw_entry", None) is None:
        tr.hw_entry = _hw_entry(tr)
    return tr.hw_entry.get(start, {})


def _hw_entry(tr):
    def hw_only(consts):
        return {r: x for r, x in consts.items() if tr.f.is_mmio(x)}
    if not any(hw_only(tr.const_step(i, {})) for i in tr.insns.values()):
        return {}
    fixed = {tr.entry, *tr.pr_targets}      # edges succ does not list reach these: nothing is known there
    ins, work = {a: {} for a in fixed}, list(fixed)
    while work:
        a = work.pop()
        i = tr.insns.get(a)
        if i is None:
            continue
        out = hw_only(tr.const_step(i, ins[a]))
        if i.delayed and a + 2 in tr.insns:
            out = hw_only(tr.const_step(tr.insns[a + 2], out))
        if i.op in ("bsr", "bsrf", "jsr"):
            out = {r: x for r, x in out.items() if r not in ("r0", "r1", "r2", "r3", "r4", "r5", "r6", "r7")}
        for b in tr.succ.get(a, ()):
            if b in fixed:
                continue
            new = dict(out) if b not in ins else {r: x for r, x in ins[b].items() if out.get(r) == x}
            if b not in ins or new != ins[b]:
                ins[b] = new
                work.append(b)
    return ins


SIGNED = {"cmp/ge": "cmp/hs", "cmp/gt": "cmp/hi"}    # a signed limit check is the unsigned one once the value is >= 0


def nonneg(tr, start, n):
    """True when the block at start runs only after `cmp/pz Rn; bf out` (GCC's check of a signed table index: the
    second check `cmp/ge Rm,Rn; bt out` then bounds it like cmp/hs). Nothing between may write Rn or T."""
    ps = [p for p, succ in tr.succ.items() if start in succ]
    if len(ps) != 1 or ps[0] + 2 != start or tr.insns[ps[0]].op != "bf":
        return False
    k = ps[0] - 2
    while k in tr.insns and tr.insns[k].op in ("mov#", "mov", "mov.l@pc", "mova") and tr.insns[k].n != n:
        k -= 2
    return k in tr.insns and tr.insns[k].op == "cmp/pz" and tr.insns[k].n == n
