"""Frame offsets and the stores of an arithmetic result whose frame word is read back as an integer
(translate.FunctionTranslator; moved from translate.py unchanged, self as t)."""


def frame_offs(t):
    """{instruction: {register: ("s", k) the entry r15 plus k, or ("c", v) the constant v}} right before it (a
    delay slot included): a forward must-analysis through mov#, mov, add#, add and the pushes and pops that move
    a pointer along. A return into the body (a local subroutine's) starts from nothing known."""
    from .translate import R, U32, writes
    def step(f, i):
        o, n, m = i.op, R(i.n), R(i.m)
        g = dict(f)
        for w in writes(i):
            g.pop(w, None)
        if o == "mov#":
            g[n] = ("c", i.imm & U32)
        elif o == "mov" and m in f:
            g[n] = f[m]
        elif o == "add#" and n in f:
            g[n] = (f[n][0], (f[n][1] + i.imm) & U32)
        elif o == "add" and n in f and m in f and "c" in (f[n][0], f[m][0]):
            g[n] = (f[n][0] if f[m][0] == "c" else f[m][0], (f[n][1] + f[m][1]) & U32)
        elif (o.endswith("@-_st") or o in ("sts.l", "stc.l")) and n in f:
            k = 8 if o.startswith("fmov.d") else {"b": 1, "w": 2}.get(o[4:5], 4) if o.startswith("mov.") else 4
            g[n] = (f[n][0], (f[n][1] - k) & U32)
        elif (o.endswith("@+_ld") or o in ("lds.l", "ldc.l")) and m in f and \
                not (o.endswith("@+_ld") and n == m):
            k = 8 if o.startswith("fmov.d") else {"b": 1, "w": 2}.get(o[4:5], 4) if o.startswith("mov.") else 4
            g[m] = (f[m][0], (f[m][1] + k) & U32)
        return g

    if t._nan_st is None:
        fin = {t.entry: {"r15": ("s", 0)}}
        fin.update({x: {} for x in t.pr_targets})
        work = list(fin)
        while work:
            x = work.pop()
            i = t.insns[x]
            out = step(fin[x], i)
            if i.delayed and x + 2 in t.insns:
                fin[x + 2] = out    # (one way in: the branch above it)
                out = step(out, t.insns[x + 2])
            for y in t.succ.get(x, ()):
                if y not in t.insns:
                    continue
                old = fin.get(y)
                new = dict(out) if old is None else {k: v for k, v in old.items() if out.get(k) == v}
                if new != old:
                    fin[y] = new
                    work.append(y)
        t._nan_st = fin
    return t._nan_st

def nan_stores(t):
    """The fmov.s stores of an arithmetic result (float_arith) to a word of the own frame that the function also
    reads with an integer load (mov, lds.l: the bits, compared or tested, as TC 8c09a518 tests a NaN's sign): those
    store the bits the judge's reference holds (f_bits), as flds takes them. A store elsewhere, or to a word only
    float loads read, is left as it is (the memory compare takes a NaN word as a NaN)."""
    from .translate import F, R, U32
    if getattr(t, "_nan_set", None) is not None:
        return t._nan_set
    fo = frame_offs(t)

    def at(a, base, plus=0, r0=False):
        f = fo.get(a, {})
        v = f.get(base)
        if r0:
            c = f.get("r0")
            if v is None or c is None or "c" not in (v[0], c[0]):
                return None
            return (v[0] if c[0] == "c" else c[0], (v[1] + c[1]) & U32)
        return None if v is None else (v[0], (v[1] + plus) & U32)

    reads, stores = [], []
    for a, i in t.insns.items():
        o, n, m = i.op, R(i.n), R(i.m)
        if o.startswith("mov.") and o.endswith("_ld") and "@pc" not in o and "@gbr" not in o:
            size = {"b": 1, "w": 2}.get(o[4:5], 4)
            base = m
            w = at(a, base, i.imm) if "@disp" in o else at(a, base, r0=True) if "@r0" in o else at(a, base)
            reads.append((w, size))
        elif o in ("lds.l", "ldc.l"):
            reads.append((at(a, m), 4))
        elif o.startswith("fmov.s@") and o.endswith("_st") and F(i.m) in t.float_arith(a):
            w = at(a, n, -4) if "@-" in o else at(a, n, r0=True) if "@r0" in o else at(a, n)
            if w is not None and w[0] == "s":
                stores.append((a, w[1]))
    t._nan_set = set()
    for a, k in stores:
        for w, size in reads:
            if w is not None and w[0] == "s" and ((w[1] - k) % (1 << 32) < 4 or (k - w[1]) % (1 << 32) < size):
                t._nan_set.add(a)
    return t._nan_set
