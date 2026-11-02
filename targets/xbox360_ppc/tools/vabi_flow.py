"""The vector ABI scan's dataflow (vector_abi.py): jump-table resolution, the blocks of a function, the forward
analysis of the vector registers, and the reads that expose an argument, a result or a post-call register."""
from vabi_state import *  # noqa: F401,F403


def resolve_jt(ins, k, addr, end):
    """Jump table for the bcctr at index k: constants built by addis/addi/ori in the 48 insns before it that point at a
    run of >=2 words that are all in-body addresses. Returns list of in-body targets or None."""
    consts, found = {}, []
    for j in range(max(0, k - 48), k):
        i = ins[j]; op = i.op
        if op == "addis":
            if i.ra == 0:
                consts[i.rd] = (i.simm << 16) & 0xFFFFFFFF
                found.append(consts[i.rd])
            elif i.ra in consts:
                consts[i.rd] = (consts[i.ra] + (i.simm << 16)) & 0xFFFFFFFF
        elif op == "addi":
            if i.ra == 0:
                consts[i.rd] = i.simm & 0xFFFFFFFF
            elif i.ra in consts:
                consts[i.rd] = (consts[i.ra] + i.simm) & 0xFFFFFFFF
                found.append(consts[i.rd])
        elif op == "ori":
            if i.rs in consts:
                consts[i.ra] = consts[i.rs] | i.uimm
                found.append(consts[i.ra])
    # byte/halfword offset tables: lbzx/lhzx rd,rT,rI (rT = table constant), bound from a cmpli, target = base + (entry << shift)
    for j in range(max(0, k - 48), k):
        i = ins[j]
        if i.op in ("lbzx", "lhzx"):
            consts2, tbl = {}, None
            for q in range(max(0, k - 48), j):
                x = ins[q]
                if x.op == "addis" and x.ra == 0: consts2[x.rd] = (x.simm << 16) & 0xFFFFFFFF
                elif x.op == "addi" and x.ra in consts2: consts2[x.rd] = (consts2[x.ra] + x.simm) & 0xFFFFFFFF
            tbl = consts2.get(i.ra)
            if tbl is None: continue
            bound = None
            for q in range(j - 1, max(-1, j - 14), -1):
                if ins[q].op == "cmpli":
                    bound = ins[q].uimm + 1; break
            if bound is None or bound > 512: continue
            shift = 0
            for q in range(j + 1, min(k, j + 4)):
                x = ins[q]
                if x.op == "rlwinm" and x.sh == 2 and x.mb == 0 and x.me == 29: shift = 2
            bases = []
            c2 = {}
            for q in range(j + 1, k):
                x = ins[q]
                if x.op == "addis" and x.ra == 0: c2[x.rd] = (x.simm << 16) & 0xFFFFFFFF
                elif x.op == "addi" and x.ra in c2: c2[x.rd] = (c2[x.ra] + x.simm) & 0xFFFFFFFF; bases.append(c2[x.rd])
            sz = 1 if i.op == "lbzx" else 2
            for B in reversed(bases):
                tg = []
                for e in range(bound):
                    off = tbl + e * sz - BASE
                    if not (0 <= off and off + sz <= len(DATA)): tg = None; break
                    v = DATA[off] if sz == 1 else struct.unpack_from(">H", DATA, off)[0]
                    t = (B + (v << shift)) & 0xFFFFFFFF
                    if not (addr <= t < end and t % 4 == 0): tg = None; break
                    tg.append(t)
                if tg: return ("table", tg)
    for c in reversed(found):
        if c % 4 or not (BASE <= c and c + 8 <= BASE + len(DATA)):
            continue
        tg = []
        a = c
        while len(tg) < 4096 and a + 4 <= BASE + len(DATA):
            v = struct.unpack_from(">I", DATA, a - BASE)[0]
            if not (addr <= v < end and v % 4 == 0):
                break
            tg.append(v)
            a += 4
        if len(tg) >= 2:
            return ("table", tg)
    # no table recovered: a switch (an indexed load feeds the jump) vs. an indirect tail call through a pointer in ctr
    for j in range(max(0, k - 16), k):
        if ins[j].op in ("lwzx", "lbzx", "lhzx", "lhax", "lwax"):
            return ("unres", None)
    return ("tail", None)


class Fn:
    pass


def build(a):
    f = FUNCS[a]
    size = f["size"]
    n = size // 4
    words = struct.unpack_from(">%dI" % n, DATA, a - BASE)
    ins = [decode(w, a + 4 * k) for k, w in enumerate(words)]
    end = a + size
    fn = Fn(); fn.addr, fn.ins, fn.n, fn.end = a, ins, n, end
    fn.falls_off = False
    inb = lambda t: a <= t < end and t % 4 == 0
    idx = lambda t: (t - a) // 4
    leaders = {0}
    jt = {}      # k -> list of target indexes, or None when unresolved
    nbctr = 0
    for k, i in enumerate(ins):
        op = i.op
        if op == "b":
            if not i.f["lk"]:
                if inb(i.f["target"]): leaders.add(idx(i.f["target"]))
                leaders.add(k + 1)
        elif op == "bc":
            if inb(i.f["target"]) and not i.f["lk"]: leaders.add(idx(i.f["target"]))
            leaders.add(k + 1)
        elif op == "bclr":
            if not ((i.f["bo"] & 0x14) == 0x14 and i.f["lk"]): leaders.add(k + 1)
        elif op == "bcctr":
            if i.f["lk"]:
                if (i.f["bo"] & 0x14) != 0x14: leaders.add(k + 1)
            else:
                nbctr += 1
                kind, t = resolve_jt(ins, k, a, end)
                jt[k] = sorted({idx(x) for x in t}) if kind == "table" else kind
                if kind == "table":
                    leaders.update(jt[k])
                leaders.add(k + 1)
    fn.nbctr, fn.nbctr_res = nbctr, sum(1 for v in jt.values() if isinstance(v, list))
    fn.nbctr_tail = sum(1 for v in jt.values() if v == "tail")
    ls = sorted(x for x in leaders if x < n)
    fn.bstart = ls
    bidx = {s: b for b, s in enumerate(ls)}
    blocks = []
    nonentry = list(range(1, len(ls)))
    for b, s in enumerate(ls):
        e = ls[b + 1] if b + 1 < len(ls) else n
        ops, succ = [], []
        fall = bidx.get(e) if e < n else None
        for k in range(s, e):
            i = ins[k]; op = i.op
            r, w = vrw(i)
            if r or w:
                ops.append(("v", r, w, k, op.startswith("st")))
            last = k == e - 1
            if op == "b":
                t = i.f["target"]
                if i.f["lk"]:
                    if not in_helper(t):
                        ops.append(("call", t, k))
                elif inb(t):
                    succ.append(bidx[idx(t)])
                elif in_helper(t):
                    ops.append(("ret", k, "helper"))
                else:
                    ops.append(("tail", t, k))
            elif op == "bc":
                t = i.f["target"]
                if i.f["lk"]:
                    if t != i.addr + 4:
                        ops.append(("call", t, k))
                else:
                    if inb(t): succ.append(bidx[idx(t)])
                    elif in_helper(t): ops.append(("ret", k, "helper"))
                    else: ops.append(("tail", t, k))
            elif op == "bclr":
                if i.f["lk"]:
                    ops.append(("call", None, k))
                else:
                    ops.append(("ret", k, "blr"))
            elif op == "bcctr":
                if i.f["lk"]:
                    ops.append(("call", None, k))
                else:
                    t = jt.get(k)
                    if isinstance(t, list):
                        succ.extend(bidx[x] for x in t)
                    elif t == "tail":
                        ops.append(("tail", None, k))               # indirect tail call through ctr: leaves the function
                    else:
                        ops.append(("tail", None, k))               # unresolved switch: may also go anywhere in the body
                        succ.extend(nonentry)
        # fall-through edge: any block whose last insn is not an always-taken, non-linked branch
        li = ins[e - 1]
        lop = li.op
        always_nolink = False
        if lop in ("b", "bc", "bclr", "bcctr"):
            always_nolink = ((lop == "b") or (li.f["bo"] & 0x14) == 0x14) and not li.f["lk"]
        if not always_nolink:
            if fall is not None: succ.append(fall)
            else: fn.falls_off = True
        blocks.append((s, e, ops, sorted(set(succ))))
    fn.blocks = blocks
    return fn


def step_ops(ops, st, ev):
    E, L, C = st
    for o in ops:
        t = o[0]
        if t == "v":
            _, r, w, k, isst = o
            if ev is not None and r:
                ea = r & E
                if ea: ev.append(("A", k, ea, isst))
                dc = r & C
                if dc & V0_13: ev.append(("D", k, dc & V0_13))
                if dc & V32_63: ev.append(("Dhi", k, dc & V32_63))
            if w:
                E &= ~w; C &= ~w; L |= w
        elif t == "call":
            _, tgt, k = o
            if ev is not None:
                ev.append(("call", k, tgt, L & ARGS, E & ARGS))
            E &= ~VOL; L &= ~VOL; C |= VOL
        elif t == "tail":
            _, tgt, k = o
            if ev is not None:
                ev.append(("tail", k, tgt, L & ARGS, E & ARGS, L >> 1 & 3))
        elif t == "ret":
            _, k, how = o
            if ev is not None:
                ev.append(("ret", k, how, L >> 1 & 3, C >> 1 & 3))
    return E, L, C


def analyze(fn):
    nb = len(fn.blocks)
    IN = [(0, 0, 0)] * nb
    IN[0] = (ALL, 0, 0)
    work = collections.deque([0]); inq = {0}
    while work:
        b = work.popleft(); inq.discard(b)
        s, e, ops, succ = fn.blocks[b]
        out = step_ops(ops, IN[b], None)
        for q in succ:
            old = IN[q]
            new = (old[0] | out[0], old[1] | out[1], old[2] | out[2])
            if new != old:
                IN[q] = new
                if q not in inq: work.append(q); inq.add(q)
    reach = [IN[b] != (0, 0, 0) or b == 0 for b in range(nb)]
    ev = []
    for b in range(nb):
        if reach[b]:
            step_ops(fn.blocks[b][2], IN[b], ev)
    fn.ev = ev
    fn.reach = reach
    fn.unreach_insns = sum(e - s for b, (s, e, _, _) in enumerate(fn.blocks) if not reach[b])
    Aargs = Aargs1 = Avol = Anv = Aspill = 0
    D = Dhi = 0
    calls = []; tails = []
    Cb = Ct = Cb1 = Ct1 = RP1 = RP2 = False
    for x in ev:
        if x[0] == "A":
            m, st_ = x[2], x[3]
            Aargs |= m & SPEC; Aargs1 |= m & ARGS; Avol |= m & (VOL & ~SPEC & ~2)
            if st_: Aspill |= m & NV
            else: Anv |= m & NV
        elif x[0] == "D":
            D |= x[2]
        elif x[0] == "Dhi":
            Dhi |= x[2]
        elif x[0] == "call":
            calls.append(x)
        elif x[0] == "tail":
            tails.append(x); Ct = Ct or bool(x[5] & 2); Ct1 = Ct1 or bool(x[5] & 1)
        elif x[0] == "ret":
            Cb = Cb or bool(x[3] & 2); Cb1 = Cb1 or bool(x[3] & 1)
            RP1 = RP1 or bool(x[4] & 1); RP2 = RP2 or bool(x[4] & 2)
    fn.Aargs, fn.Avol, fn.Anv, fn.Aspill, fn.D, fn.Dhi = Aargs, Avol, Anv, Aspill, D, Dhi
    fn.calls, fn.tails, fn.Cb, fn.Ct = calls, tails, Cb, Ct
    rets, used, ordn = rd_reg(fn, 4)
    fn.Cstrong = any(st & ~used for st in rets)
    rets, used, ordn = rd_reg(fn, 2)
    fn.Cstrong1 = any(st & ~used for st in rets)
    fn.Cunread = rd_pending(fn, 4)
    fn.Cunread1 = rd_pending(fn, 2)
    fn.RP1, fn.RP2 = RP1, RP2
    fn.Cb1, fn.Ct1, fn.Aargs1 = Cb1, Ct1, Aargs1
    fn.Bmask1 = 0
    for x in calls: fn.Bmask1 |= x[3]
    for x in tails: fn.Bmask1 |= x[3]
    fn.Bmask = fn.Bmask1 & SPEC
    return fn


def rd_reg(fn, bit):
    """Reaching definitions of v2 (def ids = instruction index). Returns (ret_states, used_mask, ordn): a def is 'used' when some
    instruction reads v2 with it reaching, or it reaches a call/tail call (maybe an argument). Calls kill v2 defs."""
    nb = len(fn.blocks)
    ordn = {}
    for (_, _, ops, _) in fn.blocks:
        for o in ops:
            if o[0] == "v" and o[2] & bit: ordn[o[3]] = len(ordn)

    def stepb(b, st, rec):
        for o in fn.blocks[b][2]:
            t = o[0]
            if t == "v":
                if o[1] & bit and rec is not None: rec[0] |= st
                if o[2] & bit: st = 1 << ordn[o[3]]
            elif t == "call":
                if rec is not None: rec[0] |= st
                st = 0
            elif t == "tail":
                if rec is not None: rec[0] |= st
            elif t == "ret":
                if rec is not None: rec[1].append(st)
        return st
    IN = [0] * nb
    work = collections.deque([0]); inq = {0}
    while work:
        b = work.popleft(); inq.discard(b)
        out = stepb(b, IN[b], None)
        for q in fn.blocks[b][3]:
            new = IN[q] | out
            if new != IN[q]:
                IN[q] = new
                if q not in inq: work.append(q); inq.add(q)
    rec = [0, []]
    for b in range(nb):
        if fn.reach[b]: stepb(b, IN[b], rec)
    return rec[1], rec[0], ordn


def rd_pending(fn, bit):
    """True when, at some blr-type return, a local definition of the register (bit 2 = v1, 4 = v2) reaches it with no read of the
    register on some path from the definition to the return (calls count as a read: the register may be an argument)."""
    nb = len(fn.blocks)
    ordn = {}
    for (_, _, ops, _) in fn.blocks:
        for o in ops:
            if o[0] == "v" and o[2] & bit: ordn[o[3]] = len(ordn)

    def stepb(b, st, rec):
        for o in fn.blocks[b][2]:
            t = o[0]
            if t == "v":
                if o[1] & bit: st = 0
                if o[2] & bit: st = 1 << ordn[o[3]]
            elif t == "call" or t == "tail":
                st = 0
            elif t == "ret" and rec is not None and st:
                rec.append(1)
        return st
    IN = [0] * nb
    work = collections.deque([0]); inq = {0}
    while work:
        b = work.popleft(); inq.discard(b)
        out = stepb(b, IN[b], None)
        for q in fn.blocks[b][3]:
            new = IN[q] | out
            if new != IN[q]:
                IN[q] = new
                if q not in inq: work.append(q); inq.add(q)
    rec = []
    for b in range(nb):
        if fn.reach[b]: stepb(b, IN[b], rec)
    return bool(rec)
