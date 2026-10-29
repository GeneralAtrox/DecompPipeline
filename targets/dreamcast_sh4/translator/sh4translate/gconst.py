"""Constants across blocks and calls, for the computed transfers of FunctionTranslator (translate.py).

GCC's code (Vigilante 8) loads a callee's address or a range check's limit once, into a register a call keeps
(r8..r14), and uses it blocks and calls later: `mov.l @(pool),r11` before a loop, `jsr @r11` inside it, which may be
the function itself (a recursion DP's oracle runs, not a mocked callee), or `mov #3,r8` before a call and
`cmp/hi r8,r0` before a jump table after it. The block-local symexec sees neither, so the call went out through a
computed tc_call and the jump table through a computed tail call, where the original calls elsewhere or jumps inside
its own body. Here a forward must-analysis over the whole body gives each node the registers that hold one constant
on every path into it. A call that returns keeps r8..r15 (the ABI, and DP's oracle: a mocked callee leaves them, a
recursion restores them) and loses r0..r7 (its result and the strict judge's junk); a local subroutine (bsrl) loses
all. A body with PR return targets (lds Rm,pr) has edges succ does not list: nothing is constant there.

Also here: the sign of a jump table's entries. `mov.b @(r0,rN),r1; extu.b r1,r1; braf r1` reads its byte offsets
unsigned (Vigilante 8 8c07ac60: offset 0xc6 is +198, not -58)."""

CALLS = ("jsr", "bsr", "bsrf")


def _step(tr, st, i):
    """st (register -> constant) after instruction i, as FunctionTranslator.symexec values constants."""
    from .translate import U32, R, writes
    o, n, m = i.op, R(i.n), R(i.m)
    v = None
    if o == "mov#":
        v = i.imm & U32
    elif o == "mov.l@pc" and not tr.pool_written(i):
        v = tr.img.u32(i.ea)
    elif o == "mov.w@pc" and not tr.pool_written(i):
        x = tr.img.u16(i.ea)
        v = (x - 0x10000 if x & 0x8000 else x) & U32
    elif o == "mova":
        n, v = "r0", i.ea
    elif o == "mov":
        v = st.get(m)
    elif o == "add#" and n in st:
        v = (st[n] + i.imm) & U32
    elif o == "add" and n in st and m in st:
        v = (st[n] + st[m]) & U32
    for w in writes(i):
        st.pop(w, None)
    if v is not None and o in ("mov#", "mov.l@pc", "mov.w@pc", "mova", "mov", "add#", "add"):
        st[n] = v
    return st


def _node(tr, st, a):
    st = dict(st)
    i = tr.insns[a]
    if i.op != "bsrl":
        st = _step(tr, st, i)
    if i.delayed and a + 2 in tr.insns:
        st = _step(tr, st, tr.insns[a + 2])
    if i.op == "bsrl":
        return {}
    if i.op in CALLS:
        for k in range(8):
            st.pop(f"r{k}", None)
    return st


def consts_in(tr):
    """{node: {register: constant}} on entry to each node of the body, for the traversal's current graph."""
    key = (frozenset(tr.primary), tuple(sorted((a, tuple(s)) for a, s in tr.succ.items())), frozenset(tr.pr_targets))
    if getattr(tr, "_gconst", (None,))[0] == key:
        return tr._gconst[1]
    res = {}
    if not tr.pr_targets and tr.entry in tr.insns:
        res = {tr.entry: {}}
        work = [tr.entry]
        while work:
            a = work.pop()
            out = _node(tr, res[a], a)
            for b in tr.succ.get(a, ()):
                if b not in tr.insns:
                    continue
                new = out if b not in res else {r: c for r, c in res[b].items() if out.get(r) == c}
                if b not in res or new != res[b]:
                    res[b] = new
                    work.append(b)
    tr._gconst = (key, res)
    return res


def wide_const(tr, a, reg):
    """reg's value right before node a: the block-local one (reg_const), else the one every path brings."""
    c = tr.reg_const(a, reg)
    return c if c is not None else consts_in(tr).get(a, {}).get(reg)


def in_const(tr, start, v):
    """FunctionTranslator.const_in, and where that finds nothing for a register unchanged since the block's start,
    the constant every path brings there."""
    r = tr.const_in(start, v)
    if r[0] == "x" and r[2] == 1 and r[3] == 0:
        c = consts_in(tr).get(start, {}).get(r[1])
        if c is not None:
            return ("c", c)
    return r


def table_unsigned(tr, lo, a, reg):
    """Whether the table entry the jump at a takes from reg was zero-extended (extu.b/extu.w) after its load in
    [lo, a)."""
    from .translate import R
    uns = {}
    for p in range(lo, a, 2):
        i = tr.img.insn(p)
        o, n, m = i.op, R(i.n), R(i.m)
        if o in ("mov.b@r0_ld", "mov.w@r0_ld", "mov.b@_ld", "mov.w@_ld"):
            uns[n] = False
        elif o in ("extu.b", "extu.w") and m in uns:
            uns[n] = True
        elif o == "mov" and m in uns:
            uns[n] = uns[m]
        elif o not in ("add", "add#", "shll", "shll2", "nop"):
            from .translate import writes
            for w in writes(i):
                uns.pop(w, None)
    return uns.get(reg, False)
