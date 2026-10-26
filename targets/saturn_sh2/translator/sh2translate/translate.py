"""SH-2 machine code -> C, one function at a time, in DecompPipeline's raw-C form (tc_rt.h).

Method: decode the function's reachable instructions (recursive traversal from the entry; delay slots folded into
their branch), split them into blocks at branch targets, and emit one C statement (or a small braced group) per
instruction over local u32 registers r0..r15, the T/S/M/Q bits, MACH/MACL and PR. Blocks are laid out in address
order, so fall-through stays fall-through; branches become gotos to labels named after their addresses.

The guest model is DP's judge model (target.json "judge_memory"/"abi"):
- memory through rd8/16/32 and wr8/16/32 (big-endian in the runtime); a load or store at a constant address in a
  hardware range goes through tc_hw_read32/tc_hw_write32/tc_hw_write16;
- the stack is guest memory: r15 starts at the ABI's entry r15 and push/pop are ordinary stores and loads;
- PR starts at the ABI's return sentinel, so `sts.l pr,@-r15` stores what the original stores;
- a call (bsr, bsrf, jsr, and a jump that leaves the body) is tc_call with r4..r7, the 8 stack words at r15 and
  r0..r3; r8..r14 are runtime state (sh4_r/sh4_set_r) handed over at every call and at exit; only r0 changes
  across a call (DP's mocked callee);
- PC-relative literal-pool loads and mova become constants; computed jumps through a bounded table (braf/jmp after
  a cmp/hs or cmp/hi range check) become a switch over the table's targets.

Nothing here is specific to one game or address: everything comes from the machine code and the target facts.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .decode import Insn, decode, disasm


class TranslateError(Exception):
    pass


@dataclass
class TargetFacts:
    image_base: int = 0x06000000
    entry_sp: int = 0x060FE000
    sentinel: int = 0x060FF000
    mmio: list = field(default_factory=lambda: [(0x00100000, 0x00200000), (0x02000000, 0x06000000),
                                               (0x20100000, 0x20200000), (0x22000000, 0x26000000),
                                               (0xFFFFFE00, 0x100000000)])

    @classmethod
    def from_target_json(cls, tj):
        a = tj.get("abi", {})
        jm = tj.get("judge_memory", {})
        mm = [(int(lo, 16), int(hi, 16)) for lo, hi in tj.get("memory", {}).get("mmio", [])]
        return cls(int(jm.get("image_base", "0x06000000"), 16), int(a.get("entry_sp", "0x060fe000"), 16),
                   int(a.get("return_sentinel", "0x060ff000"), 16), mm or cls().mmio)

    def is_mmio(self, a):
        return any(lo <= a < hi for lo, hi in self.mmio)


class Image:
    def __init__(self, data: bytes, base: int):
        self.data, self.base = data, base

    def has(self, a, n=1):
        return self.base <= a and a + n <= self.base + len(self.data)

    def u16(self, a):
        if not self.has(a, 2):
            raise TranslateError(f"read outside the image at {a:08x}")
        o = a - self.base
        return (self.data[o] << 8) | self.data[o + 1]

    def u32(self, a):
        return (self.u16(a) << 16) | self.u16(a + 2)

    def insn(self, a):
        return decode(self.u16(a), a)


@dataclass
class Signature:
    """The C signature and where each parameter lives (DP meta.json: params[].storage r0..r7 / stack)."""
    name: str
    text: str
    params: list            # [(name, kind, storage)]
    ret: str = "u32"        # void/u32/s32/u64
    ret_reg: str | None = None
    callees: dict | None = None     # {callee address: [the argument registers DP compares at a call to it]}

    @classmethod
    def from_meta(cls, meta):
        callees = None
        if "callees" in meta:
            callees = {int(c["addr"], 16): list(c.get("regs", [])) for c in meta["callees"]}
        return cls(meta["name"], meta["signature"], [(p["name"], p["kind"], p["storage"]) for p in meta["params"]],
                   meta["ret"], meta.get("ret_reg"), callees)

    @classmethod
    def generic(cls, addr):
        name = f"fn_{addr:08x}"
        return cls(name, f"u32 {name}(u32 a0, u32 a1, u32 a2, u32 a3)",
                   [("a0", "u32", "r4"), ("a1", "u32", "r5"), ("a2", "u32", "r6"), ("a3", "u32", "r7")], "u32")


U32 = 0xFFFFFFFF
STATE = frozenset(f"r{k}" for k in range(8, 15))


def h(v):
    v &= U32
    return f"0x{v:08x}u" if v >= 0x10000 else f"0x{v:x}u"   # addresses in full (DP's readable step reads them so)


def R(i):
    return f"r{i}"


# ------------------------------------------------------------------------------------------------ register effects
def writes(i: Insn):
    """The registers/flags an instruction writes (r0..r15, t, s, mq, mach, macl, pr, gbr, sr)."""
    o, n, m = i.op, R(i.n), R(i.m)
    if o in ("nop", "rts", "rte", "sleep", "bra", "braf", "jmp", "bt", "bf", "bt/s", "bf/s", "trapa", "illegal"):
        return set()
    if o in ("bsr", "bsrf", "jsr"):
        return {"pr"}
    if o in ("clrt", "sett"):
        return {"t"}
    if o == "clrmac":
        return {"mach", "macl"}
    if o == "div0u":
        return {"t", "mq"}
    if o in ("cmp/eq", "cmp/hs", "cmp/ge", "cmp/hi", "cmp/gt", "cmp/pl", "cmp/pz", "cmp/str", "tst", "cmp/eq#", "tst#",
             "tst.b@gbr"):
        return {"t"}
    if o == "div0s":
        return {"t", "mq"}
    if o == "div1":
        return {n, "t", "mq"}
    if o in ("addc", "addv", "subc", "subv", "negc", "rotcl", "rotcr", "rotl", "rotr", "shll", "shlr", "shal",
             "shar", "dt"):
        return {n, "t"}
    if o in ("mul.l", "muls.w", "mulu.w"):
        return {"macl"}
    if o in ("dmuls.l", "dmulu.l"):
        return {"mach", "macl"}
    if o in ("mac.l", "mac.w"):
        return {n, m, "mach", "macl"}
    if o == "tas.b":
        return {"t"}
    if o in ("and#", "or#", "xor#", "mova", "cmp/eq#") or o.endswith("@gbr_ld") or (o.endswith("@disp_ld")
                                                                                    and o[4] in "bw"):
        return {"r0"}
    if o in ("and.b@gbr", "or.b@gbr", "xor.b@gbr") or o.endswith("_st") and not o.endswith("@-_st"):
        return set()
    if o.endswith("@-_st"):
        return {n}
    if o.endswith("@+_ld"):
        return {n, m}
    if o == "ldc" and i.imm == 0:
        return {"sr", "t", "s", "mq"}
    if o == "ldc.l" and i.imm == 0:
        return {m, "sr", "t", "s", "mq"}
    if o in ("lds", "ldc"):
        return {["mach", "macl", "pr"][i.imm]} if o == "lds" else {["sr", "gbr", "vbr"][i.imm]}
    if o in ("lds.l", "ldc.l"):
        return {m, ["mach", "macl", "pr"][i.imm] if o == "lds.l" else ["sr", "gbr", "vbr"][i.imm]}
    if o in ("sts.l", "stc.l"):
        return {n}
    return {n}


def reads(i: Insn):
    """The registers an instruction reads (r0..r15, pr, mach, macl; flags are not tracked here)."""
    o, n, m = i.op, R(i.n), R(i.m)
    if o in ("nop", "rts", "rte", "sleep", "bra", "bsr", "bt", "bf", "bt/s", "bf/s", "trapa", "illegal", "clrt",
             "sett", "clrmac", "div0u", "mov#", "mova", "mov.l@pc", "mov.w@pc", "movt") or o.endswith("@gbr_ld"):
        return set()
    if o in ("jmp", "jsr", "braf", "bsrf", "lds", "ldc", "lds.l", "ldc.l"):
        return {m}
    if o in ("sts", "stc"):
        return {["mach", "macl", "pr"][i.imm]} if o == "sts" else set()
    if o == "sts.l":
        return {["mach", "macl", "pr"][i.imm], n}
    if o == "stc.l":
        return {n}
    if o in ("add#", "shll", "shlr", "shll2", "shlr2", "shll8", "shlr8", "shll16", "shlr16", "shal", "shar", "rotl",
             "rotr", "rotcl", "rotcr", "dt", "cmp/pl", "cmp/pz", "tas.b"):
        return {n}
    if o in ("and#", "or#", "xor#", "tst#", "cmp/eq#", "and.b@gbr", "or.b@gbr", "xor.b@gbr", "tst.b@gbr") or \
            o.endswith("@gbr_st"):
        return {"r0"}
    if o in ("mov", "not", "neg", "negc", "swap.b", "swap.w", "extu.b", "extu.w", "exts.b", "exts.w"):
        return {m}
    if o.endswith("@_ld") or o.endswith("@+_ld") or o.endswith("@disp_ld"):
        return {m}
    if o.endswith("@r0_ld"):
        return {"r0", m}
    if o.endswith("@disp_st"):
        return {"r0", m} if o[4] in "bw" else {m, n}
    if o.endswith("@r0_st"):
        return {"r0", m, n}
    if o.endswith("_st"):
        return {m, n}
    if o in ("mac.l", "mac.w"):
        return {m, n, "mach", "macl"}
    return {m, n}


# ------------------------------------------------------------------------------------------------ the translator
class FunctionTranslator:
    def __init__(self, image: Image, entry: int, sig: Signature, facts: TargetFacts, body=None, entries=None,
                 comments=True):
        self.img, self.entry, self.sig, self.f = image, entry, sig, facts
        self.body = body          # [(lo, hi)] inclusive, or None: discover
        self.entries = set(entries or ())
        self.comments = comments
        self.insns: dict[int, Insn] = {}     # every decoded instruction (primary and slots)
        self.primary: set[int] = set()       # addresses executed as an instruction of their own
        self.slots: set[int] = set()
        self.leaders: set[int] = {entry}
        self.switches: dict[int, list] = {}  # computed-jump address -> [targets]
        self.used: set[str] = set()
        self.labels_used: set[int] = set()
        self.notes: list[str] = []
        self.callees: set[int] = set()       # constant call and tail-call targets
        # Coverage build (tools/coverage.py): `CV(k);` counters at every block and on both directions of every
        # conditional branch. Off for the judged C, which is then exactly what it is without this.
        self.instrument = False
        self.cov: list[dict] = []            # counter k -> {"kind": "block" | "taken" | "fallthrough", "addr"}
        # Mutation test (tools/coverage.py): (address, function(lines) -> lines) applied to one instruction's C.
        self.mutation = None
        self.recursive = False      # the function calls itself: the body is the helper `body`, given r15 and PR
        self.call_helper = False    # calls to callees DP's meta does not list go through the tx_call helper (size)
        self.emitted: dict[int, list] = {}  # non-branch instruction -> its C lines (before any mutation)
        self.span: dict[int, list] = {}     # node -> [first, end) of its lines in the emitted items
        self.simplify = set()               # sh2translate.simplify.PASSES to apply
        self.dropped: set[int] = set()

    # ---------------------------------------------------------------------------------------------- discovery
    def local(self, a):
        if self.body is not None:
            return any(lo <= a <= hi for lo, hi in self.body)
        return a == self.entry or (a not in self.entries and self.img.has(a, 2))

    def halts_at(self, a):
        """The oracle's halting callee/loop: `bra .` with a nop in its slot (TcExit.halts)."""
        if not self.img.has(a, 4):
            return False
        i = self.img.insn(a)
        return i.op == "bra" and i.ea == a and self.img.insn(a + 2).op == "nop"

    def discover(self):
        """Traverse from the entry. Computed transfers (jsr/bsrf/jmp/braf) are decided from the instructions of their
        own block (a constant target, or a bounded table), which depends on where blocks start: traverse again until
        the decisions made during the traversal are the ones the final block layout gives."""
        decided = {}
        for _ in range(8):
            self._traverse(decided)
            final = {a: self._decide(a) for a in self.primary if self.insns[a].op in ("jsr", "bsrf", "jmp", "braf")}
            if final == self.dec_used:
                self.switches = {a: list(d[1]) for a, d in final.items() if d and d[0] == "table"}
                return
            decided = final
        raise TranslateError("computed jumps do not settle")

    def _decide(self, a):
        i = self.insns[a]
        c = self.reg_const(a, R(i.m))
        if c is not None:
            return ("const", (c + a + 4) & U32 if i.op in ("bsrf", "braf") else c)
        if i.op in ("jmp", "braf"):
            t = self.resolve_table(a)
            if t:
                return ("table", tuple(t))
        return None

    def reg_const(self, a, reg):
        v = self.symexec(self.block_start(a), a)[reg]
        return v[1] if v[0] == "c" else None

    def _traverse(self, decided):
        self.insns, self.primary, self.slots, self.leaders = {}, set(), set(), {self.entry}
        self.dec_used = {}
        self.succ = {}
        self.fall_out = {}
        self.pr_targets = set()
        work, seen = [self.entry], set()
        while work:
            a = work.pop()
            if a in seen:
                continue
            seen.add(a)
            if not self.local(a):
                raise TranslateError(f"flow reaches {a:08x} outside the body")
            i = self.img.insn(a)
            self.insns[a] = i
            self.primary.add(a)
            succ = []
            if i.op in ("illegal", "sleep", "rte", "trapa"):
                raise TranslateError(f"unsupported instruction at {a:08x}: {disasm(i)}")
            if i.delayed:
                s = self.img.insn(a + 2)
                if s.delayed or s.op in ("bt", "bf", "trapa", "illegal"):
                    raise TranslateError(f"slot-illegal instruction at {a + 2:08x}: {disasm(s)}")
                self.insns[a + 2] = s
                self.slots.add(a + 2)
            if i.op in ("jsr", "bsrf", "jmp", "braf"):
                d = decided[a] if a in decided else self._decide(a)
                self.dec_used[a] = d
            if i.delayed and self.insns[a + 2].op == "lds" and self.insns[a + 2].imm == 2:
                raise TranslateError(f"lds Rm,pr in a delay slot at {a + 2:08x}")
            if i.op == "lds" and i.imm == 2:
                # PR redirected: a later return (rts, or a callee's return after a tail call) goes to this address
                v = self.reg_const(a, R(i.m))
                if v is None or not self.local(v):
                    raise TranslateError(f"lds Rm,pr at {a:08x}: PR set to "
                                         f"{'a computed value' if v is None else f'{v:08x}, outside the body'}")
                if v not in self.pr_targets:
                    self.pr_targets.add(v)
                    self.leaders.add(v)
                    work.append(v)
            if i.op in ("bt", "bf"):
                succ = [a + 2] + ([i.ea] if self.local(i.ea) else [])
                self.leaders.add(a + 2)
                if self.local(i.ea):
                    self.leaders.add(i.ea)
            elif i.op in ("bt/s", "bf/s"):
                succ = [a + 4] + ([i.ea] if self.local(i.ea) else [])
                self.leaders.add(a + 4)
                if self.local(i.ea):
                    self.leaders.add(i.ea)
            elif i.op == "bra":
                if i.ea == a and self.insns[a + 2].op == "nop":
                    pass  # bra . : halt
                elif self.local(i.ea):
                    succ = [i.ea]
                    self.leaders.add(i.ea)
            elif i.op in ("bsr", "bsrf", "jsr"):
                tgt = i.ea if i.op == "bsr" else (d[1] if d and d[0] == "const" else None)
                if not (tgt is not None and self.halts_at(tgt)):
                    succ = [a + 4]
                    self.leaders.add(a + 4)
            elif i.op in ("braf", "jmp"):
                tg = [d[1]] if d and d[0] == "const" else list(d[1]) if d else []
                for x in tg:
                    if self.local(x):
                        succ.append(x)
                        self.leaders.add(x)
            elif i.op == "rts":
                pass
            else:
                succ = [a + 2]
            # running on into another function's entry leaves the body there: for DP's oracle that is a call of
            # that function (TcOracle.entersCallee), so it becomes a tail call, as a jump to an entry does
            out = [x for x in succ if not self.local(x) and x in self.entries]
            if out:
                self.fall_out[a] = out[0]
                succ = [x for x in succ if x not in out]
            self.succ[a] = succ
            work.extend(succ)
        # a branch into a delay slot: that slot also runs as an instruction of its own
        for a in list(self.leaders):
            if a in self.slots and a not in self.primary:
                raise TranslateError(f"branch into the delay slot at {a:08x}")

    # ---------------------------------------------------------------------------------------------- jump tables
    def block_start(self, a):
        """The leader of the straight-line run holding a (walking back over primary instructions)."""
        while a not in self.leaders and (a - 2) in self.primary and not self.insns[a - 2].delayed and \
                self.insns[a - 2].op not in ("bt", "bf"):
            a -= 2
        return a

    def symexec(self, lo, hi, st=None):
        """Symbolic values of the registers after running [lo, hi) straight-line: ('c', v) constant,
        ('x', reg, scale, add) affine in reg's value at lo, ('ld', size, base, reg, scale, add) a table load.
        st: the values at lo instead (another run's result, to carry on over a bra)."""
        st = dict(st) if st else {R(k): ("x", R(k), 1, 0) for k in range(16)}
        a = lo
        while a < hi:
            i = self.img.insn(a)
            o, n, m = i.op, R(i.n), R(i.m)
            v = None
            if o == "mov#":
                st[n] = ("c", i.imm & U32)
            elif o == "mov.l@pc":
                st[n] = ("c", self.img.u32(i.ea))
            elif o == "mov.w@pc":
                x = self.img.u16(i.ea)
                st[n] = ("c", (x - 0x10000 if x & 0x8000 else x) & U32)
            elif o == "mova":
                st["r0"] = ("c", i.ea)
            elif o == "mov":
                st[n] = st[m]
            elif o in ("shll", "shll2", "shll8", "shll16", "add#", "extu.b", "extu.w", "add"):
                x = st[n]
                k = {"shll": 2, "shll2": 4, "shll8": 256, "shll16": 65536}.get(o)
                if o in ("extu.b", "extu.w"):
                    x = st[m]  # range kept by the bound check
                    v = x
                elif k and x[0] == "x":
                    v = ("x", x[1], x[2] * k, x[3] * k)
                elif k and x[0] == "c":
                    v = ("c", (x[1] * k) & U32)
                elif o == "add#" and x[0] in "xc":
                    v = (x[0], x[1], x[2], x[3] + i.imm) if x[0] == "x" else ("c", (x[1] + i.imm) & U32)
                elif o == "add":
                    y = st[m]
                    if x[0] == "c" and y[0] == "c":
                        v = ("c", (x[1] + y[1]) & U32)
                    elif x[0] == "c" and y[0] == "x":
                        v = ("x", y[1], y[2], y[3] + x[1])
                    elif x[0] == "x" and y[0] == "c":
                        v = ("x", x[1], x[2], x[3] + y[1])
                    elif x[0] == "x" and y[0] == "x" and x[1] == y[1]:
                        v = ("x", x[1], x[2] + y[2], x[3] + y[3])     # add r1,r1: GCC's index doubling
                    elif x[0] == "ld" and y[0] == "c" and len(x) == 6:
                        v = x + (y[1],)
                    elif x[0] == "c" and y[0] == "ld" and len(y) == 6:
                        v = y + (x[1],)
                st[n] = v or ("?",)
            elif o in ("mov.w@r0_ld", "mov.l@r0_ld", "mov.b@r0_ld", "mov.w@_ld", "mov.l@_ld"):
                size = {"b": 1, "w": 2, "l": 4}[o[4]]
                if o.endswith("@r0_ld"):
                    p, q = st["r0"], st[m]
                    if p[0] == "x" and q[0] == "c":
                        p, q = q, p
                    v = ("ld", size, p[1], q[1], q[2], q[3]) if p[0] == "c" and q[0] == "x" else None
                else:
                    p = st[m]
                    v = ("ld", size, 0, p[1], p[2], p[3]) if p[0] == "x" else None
                    if v is None and p[0] == "x":
                        v = None
                st[n] = v or ("?",)
            elif o == "and#":
                x = st["r0"]
                if x[0] == "c":
                    st["r0"] = ("c", x[1] & i.imm)
                elif i.imm and i.imm & (i.imm + 1) == 0:
                    st["r0"] = ("x", f"and@{a:08x}", 1, 0)    # a value in 0..imm: bound by the mask, not a check
                else:
                    st["r0"] = ("?",)
            else:
                for w in writes(i):
                    if w in st:
                        st[w] = ("?",)
            a += 2
        return st

    def bound(self, blk, reg):
        """(N, mul, off): reg at blk is mul * v + off for a value v the unsigned range check that guards the block
        bounds to v < N (reg itself, or a copy or a doubling of it in the branch's delay slot)."""
        preds = []
        for a in self.primary:
            i = self.insns[a]
            if i.op in ("bt", "bf") and (i.ea == blk or a + 2 == blk):
                preds.append((a, i, i.ea == blk, a + 2 == blk))
            if i.op in ("bt/s", "bf/s") and (i.ea == blk or a + 4 == blk):
                preds.append((a, i, i.ea == blk, a + 4 == blk))
        if len(preds) != 1:
            return None
        a, br, taken, fall = preds[0]
        if taken and fall:
            return None
        on_true = (br.op.startswith("bt")) == taken  # the block runs when T is 1
        # the T setter: last T writer before the branch in its straight-line run
        start = self.block_start(a)
        k = a - 2
        while k >= start and "t" not in writes(self.insns[k]):
            k -= 2
        if k < start:
            return None
        c = self.insns[k]
        if c.op not in ("cmp/hs", "cmp/hi"):
            return None
        st = self.symexec(start, k)
        # reg at blk in terms of the registers at the compare: what runs between them (the branch, its delay slot)
        # may copy or double the compared register into reg
        hi = a + (4 if br.op.endswith("/s") else 2)
        if any(self.img.insn(x).op in ("extu.b", "extu.w") for x in range(k + 2, hi, 2)):
            return None     # symexec keeps an extu's operand unmasked: not a value to follow past the check
        rv = self.symexec(k + 2, hi)[reg]
        if rv[0] != "x":
            return None
        src, mul, off = int(rv[1][1:]), rv[2], rv[3]
        rn, rm = self.const_in(start, st[R(c.n)]), self.const_in(start, st[R(c.m)])
        if c.n == src and rm[0] == "c":
            N = rm[1]
            if c.op == "cmp/hs" and not on_true:
                return N, mul, off          # v < N
            if c.op == "cmp/hi" and not on_true:
                return N + 1, mul, off      # v <= N
        if c.m == src and rn[0] == "c":
            N = rn[1]
            if c.op == "cmp/hs" and on_true:
                return N + 1, mul, off      # N >= v
            if c.op == "cmp/hi" and on_true:
                return N, mul, off          # N > v
        return None

    def const_in(self, start, v):
        """v, or the constant every edge into the block at start leaves in the register v still holds unchanged
        (a range check's limit is often loaded before a loop head or in a delay slot of the block before)."""
        if v[0] != "x" or not v[1].startswith("r") or v[2] != 1 or v[3] != 0 or start == self.entry or \
                start in self.pr_targets:
            return v    # (the entry and a PR return target have edges succ does not list)
        got = set()
        for p, succ in self.succ.items():
            if start not in succ:
                continue
            if self.insns[p].op in ("jsr", "bsr", "bsrf"):
                return v    # the edge returns from a call, which may change any register
            q = self.symexec(self.block_start(p), p + (4 if self.insns[p].delayed else 2))[v[1]]
            got.add(q if q[0] == "c" else None)
        return got.pop() if len(got) == 1 and None not in got else v

    def bra_into(self, blk):
        """The bra that is the only way into the block at blk, or None."""
        if blk == self.entry or blk in self.pr_targets:
            return None
        ps = [p for p, succ in self.succ.items() if blk in succ]
        return ps[0] if len(ps) == 1 and self.insns[ps[0]].op == "bra" else None

    def resolve_table(self, a):
        """Targets of the computed jump at a (braf/jmp through a bounded table), or None."""
        i = self.insns[a]
        blk = self.block_start(a)
        st = self.symexec(blk, a)
        v = st[R(i.m)]
        p = self.bra_into(blk) if v[0] != "ld" else None
        if p is not None:   # the jump has a block of its own that a bra enters: the table load is in the bra's block
            pblk = self.block_start(p)
            v = self.symexec(blk, a, self.symexec(pblk, p + 4))[R(i.m)]
            blk = pblk
        if v[0] != "ld":
            return None
        size, base, reg, scale, add = v[1], v[2], v[3], v[4], v[5]
        extra = v[6] if len(v) > 6 else 0
        if reg.startswith("and@"):     # the index is masked (and #3 before a byte table): 0..mask, every entry used
            b = (self.img.insn(int(reg[4:], 16)).imm + 1, 1, 0)
        else:
            b = self.bound(blk, reg)
        if b is None:
            return None
        n, mul, off = b
        if n <= 0 or n > 4096 or scale * mul != size:
            return None
        out = []
        for k in range(n):
            ea = (base + size * k + scale * off + add) & U32
            if size == 2:
                e = self.img.u16(ea)
                e = e - 0x10000 if e & 0x8000 else e
            elif size == 4:
                e = self.img.u32(ea)
            else:
                e = self.img.data[ea - self.img.base]
                e = e - 0x100 if e & 0x80 else e
            t = (e + extra + (a + 4 if i.op == "braf" else 0)) & U32
            if t & 1 or not self.img.has(t, 2):
                return None
            out.append(t)
        return out

    # ---------------------------------------------------------------------------------------------- emission
    def use(self, *names):
        self.used.update(names)

    def cv(self, kind, addr):
        """A coverage counter statement (instrumented builds only)."""
        self.cov.append({"kind": kind, "addr": f"{addr:08x}"})
        return f"CV({len(self.cov) - 1});"

    def cond_goto(self, cond, a, target_lines):
        """`if (cond) <taken>` with the taken lines, and the not-taken counter after it when instrumenting."""
        if not self.instrument:
            if len(target_lines) == 1:
                return [f"if ({cond}) {target_lines[0]}"]
            return [f"if ({cond}) {{"] + ["    " + x for x in target_lines] + ["}"]
        return ([f"if ({cond}) {{", "    " + self.cv("taken", a)] + ["    " + x for x in target_lines] + ["}"] +
                [self.cv("fallthrough", a)])

    def label(self, a):
        self.labels_used.add(a)
        return f"L_{a:08x}"

    def mem_ld(self, size, addr, const):
        if size == 4 and const is not None and self.f.is_mmio(const):
            return f"tc_hw_read32({h(const)})"
        return {1: f"(u32)(s32)(s8)rd8({addr})", 2: f"(u32)(s32)(s16)rd16({addr})", 4: f"rd32({addr})"}[size]

    def mem_st(self, size, addr, val, const):
        if const is not None and self.f.is_mmio(const) and size in (2, 4):
            return f"tc_hw_write{16 if size == 2 else 32}({h(const)}, {'(u16)' if size == 2 else ''}{val});"
        return {1: f"wr8({addr}, (u8){val});", 2: f"wr16({addr}, (u16){val});", 4: f"wr32({addr}, {val});"}[size]

    ALL_ARGS = ("r4", "r5", "r6", "r7", "s0", "s1", "s2", "s3", "s4", "s5", "s6", "s7", "r0", "r1", "r2", "r3")

    def call_args(self, target_expr):
        """The TcArgs block's field assignments for a call, as the agents' C writes them (DP's draft step reads the
        arguments from these statements right before tc_call). A callee DP's meta lists gets the registers DP
        compares at the call to it (the rest stay 0); any other call gets them all: r4..r7, the 8 stack words at
        r15 and r0..r3. A stack word the callee takes is read into arg_sK first (a field's value must be free of
        calls for the draft step)."""
        cal = self.sig.callees
        if cal is None:
            regs = set(self.ALL_ARGS)
        elif target_expr.startswith("0x"):
            regs = set(cal.get(int(target_expr[:-1], 16), self.ALL_ARGS))
        else:   # a computed target: whatever any callee the original was seen calling takes
            regs = set().union(*cal.values()) if cal else set(self.ALL_ARGS)
        listed = cal is not None and target_expr.startswith("0x") and int(target_expr[:-1], 16) in cal
        pre, L = [], []
        for k in range(4):
            if f"r{4 + k}" in regs:
                L.append(f"a.r[{k}] = r{4 + k};")
                self.use(f"r{4 + k}")
        for k in range(8):
            if f"s{k}" in regs:
                self.use("r15")
                if not listed:
                    L.append(f"a.s[{k}] = rd32({'r15' if k == 0 else f'r15 + {4 * k}u'});")
                else:
                    pre.append(f"arg_s{k} = rd32({'r15' if k == 0 else f'r15 + {4 * k}u'});")
                    L.append(f"a.s[{k}] = arg_s{k};")
        for k in range(4):
            if f"r{k}" in regs:
                L.append(f"a.rlo[{k}] = r{k};")
                self.use(f"r{k}")
        return pre + L

    def call_stmt(self, target_expr, tail=False, halt=False):
        """A call from the node being emitted (self.cur). A tail call returns at once: r8..r14 are then the
        runtime's (handed over before the call), so nothing is copied back. halt: the callee never returns."""
        if target_expr.startswith("0x") and int(target_expr[:-1], 16) == self.entry and not tail and not halt:
            return [f"@SYNC_OUT:{self.cur}@"] + self.self_call() + [f"@SYNC_IN:{self.cur}@"]
        self.use("r0")
        self.has_calls = True
        if target_expr.startswith("0x"):
            self.callees.add(int(target_expr[:-1], 16))
        cal = self.sig.callees
        if self.call_helper and not (cal is not None and target_expr.startswith("0x")
                                     and int(target_expr[:-1], 16) in cal):
            self.use("r1", "r2", "r3", "r4", "r5", "r6", "r7", "r15")
            s = ["r0 = tx_call(" + target_expr + ", r0, r1, r2, r3, r4, r5, r6, r7, r15);"]
        else:
            s = self.call_args(target_expr) + [f"r0 = tc_call({target_expr}, &a);"]
        a = self.cur
        if halt:
            return [f"@SYNC_OUT:{a}@"] + s + ["tc_halt();"]
        if tail and self.pr_targets:
            # PR may hold one of this body's addresses (lds Rm,pr): the callee returns there. Every r8..r14 is
            # copied back, so the variables are the runtime's again whichever way the code goes on.
            return [f"@SYNC_OUT:{a}@"] + s + ["@SYNC_IN_ALL@", f"@RETURN_CLEAN:{a}@"]
        return [f"@SYNC_OUT:{a}@"] + s + ([f"@RETURN_CLEAN:{a}@"] if tail else [f"@SYNC_IN:{a}@"])

    def self_call(self):
        """A call of the function itself. DP's oracle runs it (its entry is inside the body, so it is no mocked
        callee): the C recurses, through the body helper with the stack pointer and the return address the call
        leaves (each level's frame below its caller's, as in the original)."""
        a = self.cur
        args = []
        for name, kind, st in self.sig.params:
            if st not in [f"r{k}" for k in range(8)]:
                raise TranslateError(f"a call of the function itself with a parameter in {st} at {a:08x}")
            args.append(st if kind == "u32" else f"({kind}){st}")
            self.use(st)
        self.use("r15")
        self.recursive = True
        call = f"body({', '.join(['r15', h(a + 4)] + args)})"
        sig = self.sig
        if sig.ret == "void":
            return [call + ";"]
        if sig.ret == "u64" or (sig.ret_reg and not sig.ret_reg.startswith("r")):
            raise TranslateError(f"a call of the function itself returning {sig.ret} at {a:08x}")
        r = sig.ret_reg or "r0"
        self.use(r)
        return [f"{r} = (u32){call};"]

    def emit_insn(self, i: Insn, consts):
        """C lines for a non-branch instruction. consts: known constant registers before it."""
        o, n, m = i.op, R(i.n), R(i.m)
        c = consts.get
        L = []
        if o not in ("nop",):
            for r in writes(i):
                if r.startswith("r"):
                    self.use(r)
        def cu(*regs):  # noqa: E306
            self.use(*regs)

        def addr_const(base_reg, off):
            v = c(base_reg)
            return None if v is None else (v + off) & U32

        def ae(base, off):
            cu(base)
            if off == 0:
                return base
            return f"{base} + {off}u"

        sz = {"b": 1, "w": 2, "l": 4}
        if o == "nop":
            return []
        if o == "mov":
            cu(m)
            L.append(f"{n} = {m};")
        elif o == "mov#":
            L.append(f"{n} = {h(i.imm)};")
        elif o == "mov.l@pc":
            L.append(f"{n} = {h(self.img.u32(i.ea))};")
        elif o == "mov.w@pc":
            x = self.img.u16(i.ea)
            L.append(f"{n} = {h(x - 0x10000 if x & 0x8000 else x)};")
        elif o == "mova":
            L.append(f"r0 = {h(i.ea)};")
        elif o.startswith("mov.") and o.endswith("_ld"):
            s = sz[o[4]]
            mode = o[5:]
            if mode == "@_ld":
                L.append(f"{n} = {self.mem_ld(s, ae(m, 0), addr_const(m, 0))};")
            elif mode == "@+_ld":
                cu(m)
                L.append(f"{n} = {self.mem_ld(s, m, c(m))};")
                if i.n != i.m:
                    L.append(f"{m} += {s}u;")
            elif mode == "@disp_ld":
                dst = "r0" if o[4] in "bw" else n
                L.append(f"{dst} = {self.mem_ld(s, ae(m, i.imm), addr_const(m, i.imm))};")
            elif mode == "@r0_ld":
                cu("r0", m)
                k = None if c("r0") is None or c(m) is None else (c("r0") + c(m)) & U32
                L.append(f"{n} = {self.mem_ld(s, f'r0 + {m}', k)};")
            elif mode == "@gbr_ld":
                L.append(f"r0 = {self.mem_ld(s, f'sh4_stc_gbr() + {i.imm}u', None)};")
        elif o.startswith("mov.") and o.endswith("_st"):
            s = sz[o[4]]
            mode = o[5:]
            if mode == "@_st":
                cu(m, n)
                L.append(self.mem_st(s, n, m, c(n)))
            elif mode == "@-_st":
                cu(m, n)
                k = None if c(n) is None else (c(n) - s) & U32
                if i.n == i.m:  # Ghidra (and the judge): decrement first, then store the decremented value
                    L.append(f"{n} -= {s}u;")
                    L.append(self.mem_st(s, n, n, k))
                else:
                    L.append(f"{n} -= {s}u;")
                    L.append(self.mem_st(s, n, m, k))
            elif mode == "@disp_st":
                if o[4] in "bw":  # mov.b/w r0,@(disp,Rn): Rn in the m field
                    cu("r0")
                    L.append(self.mem_st(s, ae(m, i.imm), "r0", addr_const(m, i.imm)))
                else:
                    cu(m)
                    L.append(self.mem_st(s, ae(n, i.imm), m, addr_const(n, i.imm)))
            elif mode == "@r0_st":
                cu("r0", n, m)
                k = None if c("r0") is None or c(n) is None else (c("r0") + c(n)) & U32
                L.append(self.mem_st(s, f"r0 + {n}", m, k))
            elif mode == "@gbr_st":
                cu("r0")
                L.append(self.mem_st(s, f"sh4_stc_gbr() + {i.imm}u", "r0", None))
        elif o in ("add", "sub", "and", "or", "xor"):
            cu(m, n)
            opc = {"add": "+", "sub": "-", "and": "&", "or": "|", "xor": "^"}[o]
            L.append(f"{n} {opc}= {m};")
        elif o == "add#":
            cu(n)
            L.append(f"{n} += {i.imm}u;" if i.imm >= 0 else f"{n} -= {-i.imm}u;")
        elif o in ("and#", "or#", "xor#"):
            cu("r0")
            L.append(f"r0 {dict([('and#', '&'), ('or#', '|'), ('xor#', '^')])[o]}= 0x{i.imm:02x}u;")
        elif o in ("and.b@gbr", "or.b@gbr", "xor.b@gbr", "tst.b@gbr"):
            cu("r0")
            op = {"and.b@gbr": "&", "or.b@gbr": "|", "xor.b@gbr": "^"}.get(o)
            if op:
                L.append(f"{{ u32 a_ = sh4_stc_gbr() + r0; wr8(a_, (u8)(rd8(a_) {op} 0x{i.imm:02x}u)); }}")
            else:
                cu("t")
                L.append(f"t = (rd8(sh4_stc_gbr() + r0) & 0x{i.imm:02x}u) == 0;")
        elif o == "tst":
            cu(m, n, "t")
            L.append(f"t = {n} == 0;" if n == m else f"t = ({n} & {m}) == 0;")
        elif o == "tst#":
            cu("r0", "t")
            L.append(f"t = (r0 & 0x{i.imm:02x}u) == 0;")
        elif o == "not":
            cu(m)
            L.append(f"{n} = ~{m};")
        elif o == "neg":
            cu(m)
            L.append(f"{n} = 0u - {m};")
        elif o == "negc":
            cu(m, "t")
            L.append(f"{{ u32 x_ = 0u - {m}; {n} = x_ - t; t = (x_ != 0) | (x_ < {n}); }}")
        elif o == "addc":
            cu(m, n, "t")
            L.append(f"{{ u32 o_ = {n}; u32 x_ = {n} + {m}; {n} = x_ + t; t = (o_ > x_) | (x_ > {n}); }}")
        elif o == "subc":
            cu(m, n, "t")
            L.append(f"{{ u32 o_ = {n}; u32 x_ = {n} - {m}; {n} = x_ - t; t = (o_ < x_) | (x_ < {n}); }}")
        elif o == "addv":
            cu(m, n, "t")
            L.append(f"{{ u32 x_ = {n} + {m}; t = ((~({n} ^ {m}) & ({n} ^ x_)) >> 31); {n} = x_; }}")
        elif o == "subv":
            cu(m, n, "t")
            L.append(f"{{ u32 x_ = {n} - {m}; t = ((({n} ^ {m}) & ({n} ^ x_)) >> 31); {n} = x_; }}")
        elif o in ("cmp/eq", "cmp/hs", "cmp/hi"):
            cu(m, n, "t")
            L.append(f"t = {n} {dict([('cmp/eq', '=='), ('cmp/hs', '>='), ('cmp/hi', '>')])[o]} {m};")
        elif o in ("cmp/ge", "cmp/gt"):
            cu(m, n, "t")
            L.append(f"t = (s32){n} {'>=' if o == 'cmp/ge' else '>'} (s32){m};")
        elif o in ("cmp/pl", "cmp/pz"):
            cu(n, "t")
            L.append(f"t = (s32){n} {'>' if o == 'cmp/pl' else '>='} 0;")
        elif o == "cmp/eq#":
            cu("r0", "t")
            L.append(f"t = r0 == {h(i.imm)};")
        elif o == "cmp/str":
            cu(m, n, "t")
            L.append(f"{{ u32 x_ = {n} ^ {m}; t = (x_ & 0xff000000u) == 0 || (x_ & 0x00ff0000u) == 0 || "
                     f"(x_ & 0x0000ff00u) == 0 || (x_ & 0x000000ffu) == 0; }}")
        elif o == "div0s":
            cu(m, n, "t", "sq", "sm")
            L.append(f"sq = {n} >> 31; sm = {m} >> 31; t = sq ^ sm;")
        elif o == "div0u":
            cu("t", "sq", "sm")
            L.append("sq = 0; sm = 0; t = 0;")
        elif o == "div1":
            cu(m, n, "t", "sq", "sm")
            L.append(f"{{ u32 d_ = {m}; u32 q_ = {n} >> 31; u32 o_ = ({n} << 1) | t; u32 c_; "
                     f"if (sq == sm) {{ {n} = o_ - d_; c_ = {n} > o_; }} else {{ {n} = o_ + d_; c_ = {n} < o_; }} "
                     f"sq = q_ ^ sm ^ c_; t = sq == sm; }}")
        elif o == "dt":
            cu(n, "t")
            L.append(f"{n} -= 1u; t = {n} == 0;")
        elif o in ("extu.b", "extu.w", "exts.b", "exts.w"):
            cu(m)
            L.append(f"{n} = " + {"extu.b": f"{m} & 0xffu", "extu.w": f"{m} & 0xffffu",
                                  "exts.b": f"(u32)(s32)(s8){m}", "exts.w": f"(u32)(s32)(s16){m}"}[o] + ";")
        elif o == "swap.b":
            cu(m)
            L.append(f"{n} = ({m} & 0xffff0000u) | (({m} & 0xffu) << 8) | (({m} >> 8) & 0xffu);")
        elif o == "swap.w":
            cu(m)
            L.append(f"{n} = ({m} << 16) | ({m} >> 16);")
        elif o == "xtrct":
            cu(m, n)
            L.append(f"{n} = ({m} << 16) | ({n} >> 16);")
        elif o == "mul.l":
            cu(m, n, "macl")
            L.append(f"macl = {n} * {m};")
        elif o == "muls.w":
            cu(m, n, "macl")
            L.append(f"macl = (u32)((s32)(s16){n} * (s32)(s16){m});")
        elif o == "mulu.w":
            cu(m, n, "macl")
            L.append(f"macl = ({n} & 0xffffu) * ({m} & 0xffffu);")
        elif o in ("dmuls.l", "dmulu.l"):
            cu(m, n, "mach", "macl")
            p = (f"(u64)((long long)(s32){n} * (long long)(s32){m})" if o == "dmuls.l" else f"(u64){n} * (u64){m}")
            L.append(f"{{ u64 p_ = {p}; mach = (u32)(p_ >> 32); macl = (u32)p_; }}")
        elif o == "mac.w":
            cu(m, n, "mach", "macl", "ss")
            L.append(f"{{ u32 an_ = {n}; s32 p_; {n} += 2u; {{ u32 am_ = {m}; {m} += 2u; "
                     f"p_ = (s32)(s16)rd16(an_) * (s32)(s16)rd16(am_); }} "
                     f"if (ss) {{ long long x_ = (long long)(s32)macl + p_; "
                     f"macl = x_ > 0x7fffffffLL ? 0x7fffffffu : x_ < -0x7fffffffLL - 1 ? 0x80000000u : (u32)x_; }} "
                     f"else {{ u64 a_ = (((u64)mach << 32) | macl) + (u64)(long long)p_; mach = (u32)(a_ >> 32); "
                     f"macl = (u32)a_; }} }}")
        elif o == "mac.l":
            cu(m, n, "mach", "macl", "ss")
            L.append(f"{{ u32 an_ = {n}; long long p_; {n} += 4u; {{ u32 am_ = {m}; {m} += 4u; "
                     f"p_ = (long long)(s32)rd32(an_) * (long long)(s32)rd32(am_); }} "
                     f"{{ u64 a_ = (((u64)mach << 32) | macl) + (u64)p_; "
                     f"if (ss) {{ long long x_ = (long long)a_; if (x_ > 0x00007fffffffffffLL) a_ = 0x00007fffffffffffull; "
                     f"else if (x_ < -0x0000800000000000LL) a_ = 0xffff800000000000ull; }} "
                     f"mach = (u32)(a_ >> 32); macl = (u32)a_; }} }}")
        elif o in ("shll", "shal"):
            cu(n, "t")
            L.append(f"t = {n} >> 31; {n} <<= 1;")
        elif o == "shlr":
            cu(n, "t")
            L.append(f"t = {n} & 1u; {n} >>= 1;")
        elif o == "shar":
            cu(n, "t")
            L.append(f"t = {n} & 1u; {n} = (u32)((s32){n} >> 1);")
        elif o == "rotl":
            cu(n, "t")
            L.append(f"t = {n} >> 31; {n} = ({n} << 1) | t;")
        elif o == "rotr":
            cu(n, "t")
            L.append(f"t = {n} & 1u; {n} = ({n} >> 1) | ({n} << 31);")
        elif o == "rotcl":
            cu(n, "t")
            L.append(f"{{ u32 c_ = {n} >> 31; {n} = ({n} << 1) | t; t = c_; }}")
        elif o == "rotcr":
            cu(n, "t")
            L.append(f"{{ u32 c_ = {n} & 1u; {n} = ({n} >> 1) | (t << 31); t = c_; }}")
        elif o in ("shll2", "shll8", "shll16", "shlr2", "shlr8", "shlr16"):
            cu(n)
            L.append(f"{n} {'<<' if o.startswith('shll') else '>>'}= {o[4:]};")
        elif o == "movt":
            cu("t")
            L.append(f"{n} = t;")
        elif o == "clrt":
            cu("t")
            L.append("t = 0;")
        elif o == "sett":
            cu("t")
            L.append("t = 1;")
        elif o == "clrmac":
            cu("mach", "macl")
            L.append("mach = 0; macl = 0;")
        elif o == "tas.b":
            cu(n, "t")
            L.append(f"{{ u32 v_ = rd8({n}); t = v_ == 0; wr8({n}, (u8)(v_ | 0x80u)); }}")
        elif o in ("sts", "sts.l"):
            src = ["mach", "macl", "pr"][i.imm]
            cu(src)
            if o == "sts":
                L.append(f"{n} = {src};")
            else:
                cu(n)
                L.append(f"{n} -= 4u;")
                L.append(self.mem_st(4, n, src, None))
        elif o in ("lds", "lds.l"):
            dst = ["mach", "macl", "pr"][i.imm]
            cu(dst)
            if o == "lds":
                cu(m)
                L.append(f"{dst} = {m};")
            else:
                cu(m)
                L.append(f"{dst} = rd32({m}); {m} += 4u;")
        elif o in ("stc", "stc.l"):
            src = ["sr", "gbr", "vbr"][i.imm]
            if src == "sr":
                cu("t", "ss", "sq", "sm")
                val = "((sh4_stc_sr() & 0xfffffcfcu) | (sm << 9) | (sq << 8) | (ss << 1) | t)"
            elif src == "gbr":
                val = "sh4_stc_gbr()"
            else:
                cu("vbr")
                val = "vbr"
            if o == "stc":
                L.append(f"{n} = {val};")
            else:
                cu(n)
                L.append(f"{n} -= 4u;")
                L.append(f"wr32({n}, {val});")
        elif o in ("ldc", "ldc.l"):
            dst = ["sr", "gbr", "vbr"][i.imm]
            cu(m)
            if o == "ldc":
                val = m
            else:
                L.append(f"{{ u32 v_ = rd32({m}); {m} += 4u;")
                val = "v_"
            if dst == "sr":
                cu("t", "ss", "sq", "sm")
                L.append(f"sh4_ldc_sr({val}); t = {val} & 1u; ss = ({val} >> 1) & 1u; sq = ({val} >> 8) & 1u; "
                         f"sm = ({val} >> 9) & 1u;")
            elif dst == "gbr":
                L.append(f"sh4_ldc_gbr({val});")
            else:
                cu("vbr")
                L.append(f"vbr = {val};")
            if o == "ldc.l":
                L.append("}")
        else:
            raise TranslateError(f"no C for {disasm(i)} at {i.addr:08x}")
        return L

    def const_step(self, i: Insn, consts):
        """Constant registers after i (forward, within a block)."""
        o, n, m = i.op, R(i.n), R(i.m)
        w = writes(i)
        new = dict(consts)
        for r in w:
            new.pop(r, None)
        if o == "mov#":
            new[n] = i.imm & U32
        elif o == "mov.l@pc":
            new[n] = self.img.u32(i.ea)
        elif o == "mov.w@pc":
            x = self.img.u16(i.ea)
            new[n] = (x - 0x10000 if x & 0x8000 else x) & U32
        elif o == "mova":
            new["r0"] = i.ea
        elif o == "mov" and m in consts:
            new[n] = consts[m]
        elif o == "add#" and n in consts:
            new[n] = (consts[n] + i.imm) & U32
        elif o == "add" and n in consts and m in consts:
            new[n] = (consts[n] + consts[m]) & U32
        return new

    def emit_branch(self, i: Insn, slot: Insn | None, consts):
        """C lines for a branch (with its slot folded in) and whether control falls through."""
        o, a = i.op, i.addr
        L = []
        slot_lines = []
        sw = writes(slot) if slot else set()
        if slot:
            slot_lines = self.emit_insn(slot, consts)
        if o in ("bt", "bf"):
            self.use("t")
            cond = "t" if o == "bt" else "!t"
            if self.local(i.ea):
                L += self.cond_goto(cond, a, [f"goto {self.label(i.ea)};"])
            else:
                L += self.cond_goto(cond, a, self.call_stmt(h(i.ea), tail=True))
            return L, a + 2
        if o in ("bt/s", "bf/s"):
            self.use("t")
            neg = "" if o == "bt/s" else "!"
            if "t" in sw:
                L.append("{ u32 c_ = t;")
                L += slot_lines
                cond = f"{neg}c_"
            else:
                L += slot_lines
                cond = f"{neg}t"
            if self.local(i.ea):
                L += self.cond_goto(cond, a, [f"goto {self.label(i.ea)};"])
            else:
                L += self.cond_goto(cond, a, self.call_stmt(h(i.ea), tail=True))
            if "t" in sw:
                L.append("}")
            return L, a + 4
        if o == "bra":
            L += slot_lines
            if i.ea == a and slot and slot.op == "nop":
                L.append(f"@SYNC_OUT:{a}@")
                L.append("tc_halt();")
            elif self.local(i.ea):
                L.append(f"goto {self.label(i.ea)};")
            else:
                if self.halts_at(i.ea):
                    L += self.call_stmt(h(i.ea), halt=True)
                else:
                    L += self.call_stmt(h(i.ea), tail=True)
            return L, None
        if o == "rts":
            L += slot_lines
            L.append(f"@RETURN:{a}@")
            return L, None
        if o in ("bsr", "bsrf", "jsr"):
            if o == "bsr":
                tgt = h(i.ea)
                const = i.ea
            else:
                reg = R(i.m)
                self.use(reg)
                d = self.dec_used.get(a)
                const = d[1] if d and d[0] == "const" else None
                expr = reg if o == "jsr" else f"{reg} + {h(a + 4)}"
                if const is not None:
                    tgt = h(const)
                elif reg in sw:
                    L.append(f"{{ u32 g_ = {expr};")
                    tgt = "g_"
                else:
                    tgt = expr
            L += slot_lines
            if self.pr_read:
                self.use("pr")
                L.append(f"@PR:{a}:{h(a + 4)}@")
            halt = const is not None and self.halts_at(const)
            L += self.call_stmt(tgt, halt=True) if halt else self.call_stmt(tgt)
            if tgt == "g_":
                L.append("}")
            return L, (None if halt else a + 4)
        if o in ("braf", "jmp"):
            reg = R(i.m)
            self.use(reg)
            d = self.dec_used.get(a)
            const = d[1] if d and d[0] == "const" else None
            expr = reg if o == "jmp" else f"{reg} + {h(a + 4)}"
            tgts = self.switches.get(a)
            if const is not None and not tgts:
                L += slot_lines
                if self.local(const):
                    L.append(f"goto {self.label(const)};")
                elif self.halts_at(const):
                    L += self.call_stmt(h(const), halt=True)
                else:
                    L += self.call_stmt(h(const), tail=True)
                return L, None
            L.append(f"{{ u32 g_ = {expr};")
            L += slot_lines
            if tgts:
                L.append("switch (g_) {")
                for x in sorted(set(tgts)):
                    if self.local(x):
                        L.append(f"case {h(x)}: goto {self.label(x)};")
                L.append("default: break;")
                L.append("}")
            L += self.call_stmt("g_", tail=True)
            L.append("}")
            return L, None
        raise TranslateError(f"not a branch: {disasm(i)}")

    def translate(self):
        self.has_calls = False
        self.discover()
        allv = [self.insns[a] for a in sorted(self.primary)] + [self.insns[a] for a in sorted(self.slots)]
        self.pr_read = any(x.op in ("sts", "sts.l") and x.imm == 2 for x in allv)
        if self.pr_targets:
            self.pr_read = True      # pr is a live value: calls set it, returns read it
            self.use("pr")
            for v in self.pr_targets:
                self.label(v)
        items = []   # ("label", addr) | ("line", text)
        falls = self.entry  # the code starts at the entry, which need not be the lowest address
        consts = {}
        prev = None
        for a in sorted(self.primary):
            if falls is not None and falls != a:
                items += self.fall_stmt(prev, falls)
                if prev is not None:
                    self.span[prev][1] = len(items)
            if a in self.leaders:
                items.append(("label", a))
                if self.instrument:
                    items.append(("line", self.cv("block", a)))
                consts = {}
            elif falls is None:
                # not a leader and nothing falls here: unreachable by layout (cannot happen in a traversal)
                raise TranslateError(f"{a:08x} reached but not laid out")
            i = self.insns[a]
            self.cur = a
            cm = f"/* {a:08x}  {disasm(i)} */" if self.comments else None
            if i.op in ("bt", "bf", "bt/s", "bf/s", "bra", "rts", "bsr", "bsrf", "jsr", "braf", "jmp"):
                slot = self.insns.get(a + 2) if i.delayed else None
                if cm and slot:
                    cm = f"/* {a:08x}  {disasm(i)}; {a + 2:08x}  {disasm(slot)} */"
                lines, falls = self.emit_branch(i, slot, consts)
                if slot:
                    consts = self.const_step(slot, consts)
                if i.op in ("bsr", "bsrf", "jsr"):
                    consts = {k: v for k, v in consts.items() if k not in ("r0", "r1", "r2", "r3", "r4", "r5", "r6",
                                                                        "r7", "pr")}
            else:
                lines = self.emit_insn(i, consts)
                self.emitted[a] = lines
                if self.mutation and self.mutation[0] == a:
                    lines = self.mutation[1](lines)
                consts = self.const_step(i, consts)
                falls = a + 2
            if cm:
                items.append(("line", cm))
            self.span[a] = [len(items), 0]
            items += [("line", x) for x in lines]
            self.span[a][1] = len(items)
            prev = a
        if falls is not None and prev in self.fall_out:
            items += self.fall_stmt(prev, falls)
            self.span[prev][1] = len(items)
            falls = None
        if falls is not None:
            raise TranslateError(f"flow runs off the end after {max(self.primary):08x}")
        if self.simplify:
            from . import simplify
            simplify.run(self, items)
        self.dataflow(items)
        return self.render(items)

    def fall_stmt(self, prev, falls):
        """Control falling from node prev to falls, which the layout does not put next."""
        if prev is not None and self.fall_out.get(prev) == falls:
            self.cur = prev
            lines = self.call_stmt(h(falls), tail=True)
            if self.insns[prev].op in ("bsr", "bsrf", "jsr"):
                lines = lines[1:]   # after a call the runtime holds r8..r14 already: nothing to hand over
            return [("line", x) for x in lines]
        return [("line", f"goto {self.label(falls)};")]

    # ---------------------------------------------------------------------------------------------- dataflow
    def node_effect(self, a):
        """(reads, writes, call) of node a, its instruction and delay slot together. call: a call that returns
        here (r8..r14 come back from the runtime after it)."""
        i = self.insns[a]
        rd, wr = set(reads(i)), set(writes(i))
        if i.delayed and a + 2 in self.insns:
            s = self.insns[a + 2]
            rd |= reads(s)      # the slot runs first (bsr/jsr set PR after it)
            wr |= writes(s)
        call = i.op in ("bsr", "bsrf", "jsr") and bool(self.succ.get(a) or a in self.fall_out)
        if self.simplify and a in self.span:   # rewritten C may read other registers than the instruction did
            from .simplify import text_reads
            rd |= text_reads(x for kind, x in self.items_ref[self.span[a][0]:self.span[a][1]])
        return rd, wr, call

    def dataflow(self, items):
        """Dirtiness (forward) of r8..r14, then liveness (backward) of r8..r14 and pr, over the instruction graph.
        r8..r14 are the runtime's at every call and at exit: a call first hands over the ones written since the
        last hand-over on some path (dirty), and after it copies back only the ones read later (live). A hand-over
        reads its variables, so it counts as a use: on a path where the register was not written, the variable
        must still hold the runtime's value."""
        st = {f"r{k}" for k in range(8, 15)} | {"pr"}
        self.items_ref = items
        rets = set()
        for kind, x in items:
            y = x.strip() if kind != "label" else ""
            if y.startswith("@RETURN:") or y.startswith("@RETURN_CLEAN:"):
                rets.add(int(y[y.index(":") + 1:-1]))
        if self.pr_targets:
            for a in rets:
                self.succ[a] = list(self.succ.get(a, [])) + sorted(self.pr_targets)
        eff = {a: self.node_effect(a) for a in self.primary}
        if self.pr_targets:
            for a in rets:
                rd, wr, call = eff[a]
                eff[a] = (rd | {"pr"}, wr, call)
        self.eff = eff
        preds = {a: [] for a in self.primary}
        for a, ss in self.succ.items():
            for b in ss:
                preds[b].append(a)
        dirty_in = {a: set() for a in self.primary}
        changed = True
        while changed:
            changed = False
            for a in sorted(self.primary):
                inn = set()
                for b in preds[a]:
                    inn |= self.dirty_out(b, dirty_in)
                if inn != dirty_in[a]:
                    dirty_in[a] = inn
                    changed = True
        self.dirty_in = dirty_in
        sync = set()
        for kind, x in items:
            y = x.strip() if kind != "label" else ""
            if y.startswith("@SYNC_OUT:") or y.startswith("@RETURN:"):
                sync.add(int(y[y.index(":") + 1:-1]))
        live_in = {a: set() for a in self.primary}
        live_out = {a: set() for a in self.primary}
        changed = True
        while changed:
            changed = False
            for a in sorted(self.primary, reverse=True):
                out = set()
                for b in self.succ.get(a, ()):
                    out |= live_in[b]
                rd, wr, call = eff[a]
                rd = (rd | dirty_in[a]) if a in sync else rd
                kill = (wr | ((st - {"pr"}) if call else set())) & st
                inn = (rd & st) | (out - kill)
                if out != live_out[a] or inn != live_in[a]:
                    live_out[a], live_in[a] = out, inn
                    changed = True
        self.live_in, self.live_out = live_in, live_out

    def dirty_out(self, a, dirty_in):
        rd, wr, call = self.eff[a]
        return set() if call else (dirty_in[a] | (wr & STATE))

    def dirty_at(self, a):
        """r8..r14 written since the last hand-over, at node a's call or exit (its slot included)."""
        rd, wr, call = self.eff[a]
        return sorted(self.dirty_in[a] | (wr & STATE), key=lambda r: int(r[1:]))

    def pr_dispatch(self):
        """A return goes to PR: to one of this body's addresses that lds Rm,pr put there, else out of the function."""
        return [f"if (pr == {h(v)}) goto {self.label(v)};" for v in sorted(self.pr_targets)]

    def render(self, items):
        sig = self.sig
        regs = sorted((x for x in self.used if x.startswith("r") and x[1:].isdigit()), key=lambda r: int(r[1:]))
        state = [r for r in regs if 8 <= int(r[1:]) <= 14]
        if sig.ret == "void":
            ret = "return;"
        elif sig.ret_reg:
            if not sig.ret_reg.startswith("r"):
                raise TranslateError(f"result register {sig.ret_reg}")
            self.use(sig.ret_reg)
            ret = f"return ({sig.ret}){sig.ret_reg};"
        elif sig.ret == "u64":
            self.use("r0", "r1")
            ret = "return ((u64)r1 << 32) | r0;"
        elif sig.ret in ("u32", "s32"):
            self.use("r0")
            ret = f"return ({sig.ret})r0;" if sig.ret == "s32" else "return r0;"
        else:
            raise TranslateError(f"result type {sig.ret}")
        regs = sorted((x for x in self.used if x.startswith("r") and x[1:].isdigit()), key=lambda r: int(r[1:]))
        body = []
        for kind, x in items:
            if kind == "label":
                if x in self.labels_used:
                    body.append(f"L_{x:08x}:;")
                continue
            ind = "    " + " " * (len(x) - len(x.lstrip()))
            y = x.strip()
            if not y:
                continue
            if y.startswith("@SYNC_OUT:"):
                regs_ = [r for r in self.dirty_at(int(y[10:-1])) if r in state]
                if regs_:
                    body.append(ind + " ".join(f"sh4_set_r({r[1:]}, {r});" for r in regs_))
            elif y.startswith("@SYNC_IN:"):
                a = int(y[9:-1])
                regs_ = sorted((r for r in self.live_out[a] if r in state), key=lambda r: int(r[1:]))
                if regs_:
                    body.append(ind + " ".join(f"{r} = sh4_r({r[1:]});" for r in regs_))
            elif y.startswith("@RETURN:"):
                regs_ = [r for r in self.dirty_at(int(y[8:-1])) if r in state]
                if regs_:
                    body.append(ind + " ".join(f"sh4_set_r({r[1:]}, {r});" for r in regs_))
                body += [ind + x for x in self.pr_dispatch()]
                body.append(ind + ret)
            elif y.startswith("@RETURN_CLEAN:"):
                body += [ind + x for x in self.pr_dispatch()]
                body.append(ind + ret)
            elif y == "@SYNC_IN_ALL@":
                if state:
                    body.append(ind + " ".join(f"{r} = sh4_r({r[1:]});" for r in state))
            elif y.startswith("@PR:"):
                a, v = y[4:-1].split(":")
                if "pr" in self.live_out[int(a)]:
                    body.append(ind + f"pr = {v};")
            else:
                body.append(ind + y)
        body = fill_call_args(body)
        out = ['#include "tc_rt.h"', ""]
        if any("tx_call(" in x for x in body):
            out += [
                "/* A call: DP's mocked callee gets r4..r7, the 8 stack words at r15 and r0..r3, and returns r0. */",
                "static u32 tx_call(u32 target, u32 r0, u32 r1, u32 r2, u32 r3, u32 r4, u32 r5, u32 r6, u32 r7,"
                " u32 r15)",
                "{",
                "    TcArgs b;",
                "    u32 i;",
                "    b.r[0] = r4; b.r[1] = r5; b.r[2] = r6; b.r[3] = r7;",
                "    b.rlo[0] = r0; b.rlo[1] = r1; b.rlo[2] = r2; b.rlo[3] = r3;",
                "    for (i = 0; i < 8; i++) { b.fr[i] = 0.0f; b.s[i] = rd32(r15 + 4u * i); }",
                "    return tc_call(target, &b);",
                "}",
                "",
            ]
        if self.recursive:
            k = sig.text.index("(")
            rest = sig.text[k + 1:]
            out.append("/* The function's body, which calls itself: sp and ret_addr are r15 and PR on entry. */")
            out.append("static " + sig.text[:k].replace(sig.name, "body") + "(u32 sp, u32 ret_addr" +
                       (")" if rest.strip() in ("void)", ")") else ", " + rest))
        else:
            out.append(sig.text)
        out.append("{")
        decl = regs + [x for x in ("t", "ss", "sq", "sm", "mach", "macl", "pr", "vbr") if x in self.used]
        import re as _re
        decl += sorted(set(_re.findall(r"\barg_s\d\b", "\n".join(body))))
        direct = {}
        if "params" in self.simplify:
            # a parameter whose register the body never writes is used by its own name (no register copy); so is
            # a u32 one whose register the body only steps from its own value (r4 += 4u, r5 = r5 << 2): the body
            # assigns to the parameter itself
            code = [x for x in body if not x.strip().startswith("/*")]
            for name, kind, st in sig.params:
                if not (kind in ("u32", "s32") and st in [f"r{k}" for k in range(8)] and st in self.sim_live_in):
                    continue
                if not any(_re.search(rf"\b{st}\s*(?:[-+*/%&|^]|<<|>>)?=(?!=)|\b{st}\s*(?:\+\+|--)", x)
                           for x in code):
                    direct[st] = name if kind == "u32" else f"(u32){name}"
                    continue
                plain = [m.group(1) for x in code for m in _re.finditer(rf"\b{st}\s*=(?!=)([^;]*);", x)]
                if kind == "u32" and all(_re.search(rf"\b{st}\b", rhs) for rhs in plain):
                    direct[st] = name
            if direct:
                pat = _re.compile(r"\b(" + "|".join(direct) + r")\b")
                body = [x if x.strip().startswith("/*") else pat.sub(lambda m: direct[m.group(1)], x) for x in body]
        if "struct" in self.simplify and not self.instrument:
            from . import structure as _st
            _st.CURRENT = f"{self.entry:08x}"     # names the function in SH2_CFG_LOG / SH2_CFG_DUMP diagnostics
            body = _st.structure(body)
            # a saved T, a switch target, the flag of a jump out of nested loops, a computed call target (only when the body names them)
            decl += ["ct_", "sw_", "fl_", "jt_"]
        if self.simplify:   # only the variables the simplified body still names
            import re as _re
            named = set(_re.findall(r"\w+", "\n".join(x for x in body if not x.strip().startswith("/*"))))
            regs = [r for r in regs if r in named]
            decl = [d for d in decl if d in named]
        if decl:
            out.append("    u32 " + ", ".join(f"{d} = 0" for d in decl) + ";")
        if any("tc_call(" in x for x in body):
            out.append("    TcArgs a = {{0, 0, 0, 0}, {0, 0, 0, 0, 0, 0, 0, 0}, {0, 0, 0, 0, 0, 0, 0, 0}, {0, 0, 0, 0}};")
        pmap = {}
        for name, kind, st in sig.params:
            if st in [f"r{k}" for k in range(8)]:
                pmap[st] = name
        for name, kind, st in sig.params:
            # (simplified: a parameter register the body overwrites before reading it is not copied in)
            if st not in regs or (self.simplify and st not in self.sim_live_in) or st in direct:
                if st in direct and _re.search(rf"\b{name}\b", "\n".join(body)):
                    continue
                out.append(f"    (void){name};")     # (also a direct one the body never names: a halt)
        for r in regs:
            k = int(r[1:])
            if r in pmap:
                if not (self.simplify and r not in self.sim_live_in) and r not in direct:
                    out.append(f"    {r} = (u32){pmap[r]};")
            elif 8 <= k <= 14 and r in self.live_in[self.entry]:
                out.append(f"    {r} = sh4_r({k});")
            elif k == 15:
                out.append(f"    r15 = {'sp' if self.recursive else h(self.f.entry_sp)};")
        if "pr" in self.used:
            out.append(f"    pr = {'ret_addr' if self.recursive else h(self.f.sentinel)};")
        if self.recursive:
            named = " ".join(out[out.index("{"):] + body)
            out += [f"    (void){x};" for x in ("sp", "ret_addr") if not re.search(rf"= {x};", named)]
        if decl:
            out.append("    " + " ".join(f"(void){d};" for d in decl))
        out += body
        out.append("}")
        if self.recursive:
            call = f"body({', '.join([h(self.f.entry_sp), h(self.f.sentinel)] + [n for n, _, _ in sig.params])})"
            out += ["", sig.text, "{", f"    {call};" if sig.ret == "void" else f"    return {call};", "}"]
        return "\n".join(out) + "\n"


FIELD_LINE = re.compile(r"^\s*a\.(r|s|rlo)\[(\d)\] = (.*);$")
FIELD_ORDER = [("r", k) for k in range(4)] + [("s", k) for k in range(8)] + [("rlo", k) for k in range(4)]


def fill_call_args(body):
    """Every call writes the same TcArgs fields: those any call of the function writes, 0 where its callee takes
    nothing there. (DP's draft step takes a field's value from the assignments right before the call, or from the
    block's initializer only when no statement ever writes that field.)"""
    used = {(m.group(1), int(m.group(2))) for x in body for m in [FIELD_LINE.match(x)] if m}
    if not used:
        return body
    want = [f for f in FIELD_ORDER if f in used]
    out = []
    for x in body:
        if "tc_call(" not in x:
            out.append(x)
            continue
        got = {}
        while out and FIELD_LINE.match(out[-1]):
            m = FIELD_LINE.match(out.pop())
            got.setdefault((m.group(1), int(m.group(2))), m.group(3))
        ind = x[:len(x) - len(x.lstrip())]
        out += [f"{ind}a.{f}[{k}] = {got.get((f, k), '0')};" for f, k in want]
        out.append(x)
    return out


def translate_function(image, entry, sig, facts=None, body=None, entries=None, comments=True, simplify=None):
    """simplify: the sh2translate.simplify passes to apply (None: all of them; () for none)."""
    from .simplify import PASSES
    passes = set(PASSES if simplify is None else simplify)

    def make(cm, helper=False):
        ft = FunctionTranslator(image, entry, sig, facts or TargetFacts(), body, entries, cm)
        ft.simplify = passes
        ft.call_helper = helper
        return ft.translate()
    return fit(make, comments)


def fit(make, comments=True):
    """The translation under DP's 60 KB work.c cap: without comments, then compact, then with the calls to
    callees DP's meta does not list through the tx_call helper (make(comments, helper) translates)."""
    src = make(comments)
    if comments and len(src) > 58000:
        src = make(False)
    if len(src) > 58000:
        src = compact(src)
    if len(src) > 58000:
        src = compact(make(False, True))
    return src


def compact(src, helper_min=5):
    """The same C without optional whitespace. A space is kept only between two identifier/number characters (`u32
    r0`, `goto L_x`) or between two operator characters (where `a - -b` must not become `a--b`), so no token changes.
    (The output has no string literals.)"""
    def idc(c):
        return c.isalnum() or c == "_"
    ops = set("+-*/%&|^<>=!~")
    out = []
    for ln in src.splitlines():
        ln = ln.strip()
        if ln.startswith("#"):
            out.append(ln)      # the #include line stays as it is
            continue
        r = []
        for k, ch in enumerate(ln):
            if ch == " " and r and k + 1 < len(ln):
                left, right = r[-1], ln[k + 1]
                if not (idc(left) and idc(right)) and not (left in ops and right in ops):
                    continue
            r.append(ch)
        out.append("".join(r))
    # Sign-extending loads through a static helper: `(u32)(s32)(s16)rd16(x)` is a call whose result is cast, so
    # `sx16(x)` with sx16 returning that same expression is the same value.
    helpers = []
    for bits, cast in ((8, "(u32)(s32)(s8)rd8("), (16, "(u32)(s32)(s16)rd16(")):
        uses = sum(x.count(cast) for x in out)
        if uses >= helper_min:
            out = [x.replace(cast, f"sx{bits}(") for x in out]
            helpers.append(f"static u32 sx{bits}(u32 a){{return {cast}a);}}")
    if helpers:
        k = next(j for j, x in enumerate(out) if x.startswith("#include"))
        out[k + 1:k + 1] = helpers
    # Several statements to a line (never the #include line).
    joined, cur = [], ""
    for x in out:
        if x.startswith("#") or not x:
            if cur:
                joined.append(cur)
            joined.append(x)
            cur = ""
        elif cur and len(cur) + len(x) <= 160:
            cur += (" " if idc(cur[-1]) and idc(x[0]) else "") + x
        else:
            if cur:
                joined.append(cur)
            cur = x
    if cur:
        joined.append(cur)
    return "\n".join(joined) + "\n"
