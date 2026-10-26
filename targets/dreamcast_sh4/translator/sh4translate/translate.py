"""SH-4 machine code -> C, one function at a time, in DecompPipeline's raw-C form (tc_rt.h).

Method: decode the function's reachable instructions (recursive traversal from the entry; delay slots folded into
their branch), split them into blocks at branch targets, and emit one C statement (or a small braced group) per
instruction over local u32 registers r0..r15, the T/S/M/Q bits, MACH/MACL and PR, and local f32 registers
fr0..fr15 and a u32 FPUL. Blocks are laid out in address order, so fall-through stays fall-through; branches become
gotos to labels named after their addresses (the struct pass turns them into loops and ifs).

The guest model is DP's judge model (target.json "abi", "memory"; tc_rt.h):
- memory through rd8/16/32/rdf32 and wr8/16/32/wrf32 (little endian in the Dreamcast runtime); a load or store of a
  hardware register the project lists for the function goes through tc_hw_read32/tc_hw_write32/tc_hw_write16;
- the stack is guest memory: r15 starts at the ABI's entry r15 and push/pop are ordinary stores and loads;
- PR starts at the ABI's return sentinel, so `sts.l pr,@-r15` stores what the original stores;
- a call (bsr, bsrf, jsr, and a jump that leaves the body) is tc_call with r4..r7, fr4..fr11, the 8 stack words at
  r15 and r0..r3; runtime state (r8..r14, fr12..fr15, and fr0..fr15 when the whole FPU is state) is handed over at
  every call and at exit; only r0 and fr0 (the same result bits) change across a call (DP's mocked callee);
- the FPU modes FPSCR.SZ/PR/FR are followed through the code from the ABI's entry state (all 0): a paired move
  (SZ=1) is two single moves; frchg/fschg/fpchg also update the runtime's mode word (sh4_frchg, ...);
- PC-relative literal-pool loads and mova become constants; computed jumps through a bounded table (braf/jmp after
  a cmp/hs or cmp/hi range check) become a switch over the table's targets.

Nothing here is specific to one game or address: everything comes from the machine code and the target facts.
"""
from __future__ import annotations

import dataclasses
import math
import os
import re
from dataclasses import dataclass, field

from .decode import CTRL, SYSR, SYSTEM, Insn, decode, disasm


class TranslateError(Exception):
    pass


@dataclass
class TargetFacts:
    image_base: int = 0x8C010000
    entry_sp: int = 0x8CFFE000
    sentinel: int = 0x8CFFF000
    little: bool = True     # the SH-4 runs the Dreamcast little endian
    hw_reads: frozenset = frozenset()   # hardware registers every function may read (target.json "hw_reads")

    @classmethod
    def from_target_json(cls, tj):
        a = tj.get("abi", {})
        jm = tj.get("judge_memory", {})
        base = jm.get("image_base") or tj.get("load_address", "0x8c010000")
        return cls(int(base, 16), int(a.get("entry_sp", "0x8cffe000"), 16),
                   int(a.get("return_sentinel", "0x8cfff000"), 16), True,
                   frozenset(int(k, 16) for k in tj.get("hw_reads", {})))


class Image:
    def __init__(self, data: bytes, base: int, little=True):
        self.data, self.base, self.little = data, base, little

    def has(self, a, n=1):
        return self.base <= a and a + n <= self.base + len(self.data)

    def u16(self, a):
        if not self.has(a, 2):
            raise TranslateError(f"read outside the image at {a:08x}")
        o = a - self.base
        b0, b1 = self.data[o], self.data[o + 1]
        return (b1 << 8) | b0 if self.little else (b0 << 8) | b1

    def u32(self, a):
        lo, hi = (self.u16(a), self.u16(a + 2)) if self.little else (self.u16(a + 2), self.u16(a))
        return (hi << 16) | lo

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
    fpu_state: bool = False         # the whole FPU is runtime state (meta "fpu_state": a function that switches modes)
    fr0_result: bool = False        # fr0 is compared at exit besides the result (DP's fr0_results.txt)
    hw_reads: frozenset = frozenset()   # hardware registers the project lists for this function (meta "hw_ops")
    hw_writes: frozenset = frozenset()
    sq_window: str | None = None    # the register that may point into the store-queue window (meta "sq_window")
    fpscr_state: bool = False       # FPSCR is runtime state, compared at exit and at calls (meta "fpscr_state", TcFpscr)

    @classmethod
    def from_meta(cls, meta):
        callees = None
        if "callees" in meta:
            callees = {int(c["addr"], 16): list(c.get("regs", [])) for c in meta["callees"]}
        hw = meta.get("hw_ops") or {}
        return cls(meta["name"], meta["signature"], [(p["name"], p["kind"], p["storage"]) for p in meta["params"]],
                   meta["ret"], meta.get("ret_reg"), callees, bool(meta.get("fpu_state")), bool(meta.get("fr0_result")),
                   frozenset(int(r, 16) for r in (hw.get("reads") or {})),
                   frozenset(int(r, 16) for r in (hw.get("writes") or ())), meta.get("sq_window"),
                   bool(meta.get("fpscr_state")))

    @classmethod
    def generic(cls, addr):
        name = f"fn_{addr:08x}"
        return cls(name, f"u32 {name}(u32 a0, u32 a1, u32 a2, u32 a3)",
                   [("a0", "u32", "r4"), ("a1", "u32", "r5"), ("a2", "u32", "r6"), ("a3", "u32", "r7")], "u32")


U32 = 0xFFFFFFFF
# A diagnosis aid for stale test vectors, never for judged output: fmac as a product and a sum, rounded twice (as
# vectors made before DP's judge modelled the fused fmac expect). tools/stale.py sets it.
FMAC_TWO_ROUNDINGS = os.environ.get("SH4_FMAC_TWO_ROUNDINGS") == "1"
STATE = frozenset(f"r{k}" for k in range(8, 15))
ARG_REGS = frozenset([f"r{k}" for k in range(4, 8)] + [f"fr{k}" for k in range(4, 12)])
FRS = tuple(f"fr{k}" for k in range(16))
# FPU instructions whose meaning FPSCR.PR changes (PR=1: double precision)
PR_OPS = {"fadd", "fsub", "fmul", "fdiv", "fcmp/eq", "fcmp/gt", "fneg", "fabs", "fsqrt", "float", "ftrc", "fmac",
          "fsrra", "fsca", "fipr", "ftrv", "fcnvsd", "fcnvds", "frchg", "fschg"}
# their double forms under PR=1 (fpu_modes renames them OP.dr; operands DRn = fr n : fr n+1, n even): the runtime's pair
# helpers (tc_rt.h sh4_dadd, ...). fneg and fabs change only the sign of fr n (the high word): single moves of bits.
# fcnvsd/fcnvds exist only with PR=1; fmac, fsrra, fsca, fipr, ftrv, frchg, fschg and fldi0/1 are undefined there.
DR_OPS = {"fadd": "sh4_dadd", "fsub": "sh4_dsub", "fmul": "sh4_dmul", "fdiv": "sh4_ddiv", "fsqrt": "sh4_dsqrt",
          "fcmp/eq": "sh4_dcmpeq", "fcmp/gt": "sh4_dcmpgt", "float": "sh4_dfloat", "ftrc": "sh4_dftrc",
          "fcnvsd": "sh4_fcnvsd", "fcnvds": "sh4_fcnvds"}
FPSCR_PR = 0x00080000


def F(k):
    return f"fr{k}"


def pair(k):
    """The two single registers of the paired operand k of an SZ=1 move: (bank, first, second). DRk (k even) is
    fr k (the high word) and fr k+1; XDk (k odd) is the back bank's xf k-1 and xf k."""
    return ("fr", k, k + 1) if k % 2 == 0 else ("xf", k - 1, k)


def h(v):
    v &= U32
    return f"0x{v:08x}u" if v >= 0x10000 else f"0x{v:x}u"   # addresses in full (DP's readable step reads them so)


def R(i):
    return f"r{i}"


def regkey(r):
    """Sort key of a register variable: r0..r15, then fr0..fr15."""
    return (r.startswith("f"), int(r.lstrip("fr")))


# ------------------------------------------------------------------------------------------------ register effects
def fpu_writes(i: Insn):
    """The registers an FPU or FPUL instruction writes (fr0..fr15, fpul, t, and the integer address register of a
    post-increment or pre-decrement move), or None when i is no such instruction. xf0..xf15 (XMTRX) are runtime state
    (sh4_xf), not variables."""
    o, n, m = i.op, i.n, i.m
    if o in ("fadd.dr", "fsub.dr", "fmul.dr", "fdiv.dr", "fsqrt.dr", "float.dr", "fcnvsd.dr"):
        return {F(n), F(n + 1)}
    if o in ("fcmp/eq.dr", "fcmp/gt.dr"):
        return {"t"}
    if o in ("ftrc.dr", "fcnvds.dr"):
        return {"fpul"}
    if o in ("fadd", "fsub", "fmul", "fdiv", "fmac", "fneg", "fabs", "fsqrt", "fsrra", "fldi0", "fldi1", "fsts", "float",
             "fmov", "fmov.s@_ld", "fmov.s@r0_ld"):
        return {F(n)}
    if o == "fmov.s@+_ld":
        return {F(n), R(m)}
    if o in ("fmov.s@_st", "fmov.s@r0_st", "fmov.d@_st", "fmov.d@r0_st", "fschg", "fpchg"):
        return set()
    if o in ("fmov.s@-_st", "fmov.d@-_st"):
        return {R(n)}
    if o in ("fmov.d", "fmov.d@_ld", "fmov.d@r0_ld", "fmov.d@+_ld"):
        b, x, y = pair(n)
        return ({F(x), F(y)} if b == "fr" else set()) | ({R(m)} if o == "fmov.d@+_ld" else set())
    if o in ("fcmp/eq", "fcmp/gt"):
        return {"t"}
    if o in ("flds", "ftrc", "fcnvds"):
        return {"fpul"}
    if o in ("fsca", "fcnvsd"):
        return {F(n), F(n + 1)}
    if o == "fipr":
        return {F(n + 3)}
    if o == "ftrv":
        return {F(n + k) for k in range(4)}
    if o == "frchg":
        return set(FRS)
    if o in ("lds", "lds.l") and SYSR[i.imm] in ("fpul", "fpscr"):
        return {SYSR[i.imm]} | ({R(m)} if o == "lds.l" else set())
    return None


def writes(i: Insn):
    """The registers/flags an instruction writes (r0..r15, fr0..fr15, fpul, t, s, mq, mach, macl, pr, gbr, sr)."""
    o, n, m = i.op, R(i.n), R(i.m)
    fw = fpu_writes(i)
    if fw is not None:
        return fw
    if o in ("nop", "rts", "rte", "sleep", "bra", "braf", "jmp", "bt", "bf", "bt/s", "bf/s", "trapa", "illegal", "pref",
             "ocbi", "ocbp", "ocbwb", "movca.l", "ldtlb"):
        return set()
    if o in ("bsr", "bsrf", "jsr"):
        return {"pr", "r0", "fr0"}      # the callee's result comes back in r0 and fr0
    if o in ("clrs", "sets"):
        return {"s"}
    if o in ("shad", "shld"):
        return {n}
    if o in ("stc.sys", "stc.l.sys", "ldc.sys", "ldc.l.sys"):
        return {n} if o.startswith("stc") else {m} if o.endswith(".l.sys") else set()
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
        return {SYSR[i.imm]} if o == "lds" else {CTRL[i.imm]}
    if o in ("lds.l", "ldc.l"):
        return {m, SYSR[i.imm] if o == "lds.l" else CTRL[i.imm]}
    if o in ("sts.l", "stc.l"):
        return {n}
    return {n}


def fpu_reads(i: Insn):
    """The registers an FPU or FPUL instruction reads (see fpu_writes), or None when i is no such instruction."""
    o, n, m = i.op, i.n, i.m
    if o in ("fadd.dr", "fsub.dr", "fmul.dr", "fdiv.dr", "fcmp/eq.dr", "fcmp/gt.dr"):
        return {F(n), F(n + 1), F(m), F(m + 1)}
    if o in ("fsqrt.dr", "ftrc.dr", "fcnvds.dr"):
        return {F(n), F(n + 1)}
    if o in ("float.dr", "fcnvsd.dr"):
        return {"fpul"}
    if o in ("fadd", "fsub", "fmul", "fdiv", "fcmp/eq", "fcmp/gt"):
        return {F(n), F(m)}
    if o == "fmac":
        return {"fr0", F(n), F(m)}
    if o in ("fneg", "fabs", "fsqrt", "fsrra", "flds", "ftrc"):
        return {F(n)}
    if o in ("fldi0", "fldi1", "fschg", "fpchg"):
        return set()
    if o == "fmov":
        return {F(m)}
    if o in ("fmov.s@_ld", "fmov.s@+_ld", "fmov.d@_ld", "fmov.d@+_ld"):
        return {R(m)}
    if o in ("fmov.s@r0_ld", "fmov.d@r0_ld"):
        return {"r0", R(m)}
    if o in ("fmov.s@_st", "fmov.s@-_st"):
        return {F(m), R(n)}
    if o == "fmov.s@r0_st":
        return {F(m), "r0", R(n)}
    if o in ("fmov.d", "fmov.d@_st", "fmov.d@-_st", "fmov.d@r0_st"):
        b, x, y = pair(m)
        src = {F(x), F(y)} if b == "fr" else set()
        return src | ({R(n)} if o != "fmov.d" else set()) | ({"r0"} if o == "fmov.d@r0_st" else set())
    if o in ("fsts", "float", "fsca", "fcnvsd"):
        return {"fpul"}
    if o == "fcnvds":
        return {F(n), F(n + 1)}
    if o == "fipr":
        return {F(n + k) for k in range(4)} | {F(m + k) for k in range(4)}
    if o == "ftrv":
        return {F(n + k) for k in range(4)}
    if o == "frchg":
        return set()        # the variables are handed over first (FunctionTranslator.handover)
    if o in ("lds", "lds.l") and SYSR[i.imm] in ("fpul", "fpscr"):
        return {R(m)}
    if o in ("sts", "sts.l") and SYSR[i.imm] in ("fpul", "fpscr"):
        return {SYSR[i.imm]} | ({R(n)} if o == "sts.l" else set())
    return None


def reads(i: Insn):
    """The registers an instruction reads (r0..r15, fr0..fr15, fpul, pr, mach, macl; flags are not tracked here)."""
    o, n, m = i.op, R(i.n), R(i.m)
    fr = fpu_reads(i)
    if fr is not None:
        return fr
    if o in ("pref", "ocbi", "ocbp", "ocbwb"):
        return {n}
    if o == "movca.l":
        return {"r0", n}
    if o in ("clrs", "sets", "ldtlb"):
        return set()
    if o in ("nop", "rts", "rte", "sleep", "bra", "bsr", "bt", "bf", "bt/s", "bf/s", "trapa", "illegal", "clrt",
             "sett", "clrmac", "div0u", "mov#", "mova", "mov.l@pc", "mov.w@pc", "movt") or o.endswith("@gbr_ld"):
        return set()
    if o in ("jmp", "jsr", "braf", "bsrf", "lds", "ldc", "lds.l", "ldc.l"):
        return {m}
    if o in ("sts", "stc"):
        return {SYSR[i.imm]} if o == "sts" else set()
    if o == "sts.l":
        return {SYSR[i.imm], n}
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
                 comments=True, ranges=None, spin=None):
        self.img, self.entry, self.sig, self.f = image, entry, sig, facts
        # the wait loops DP's project names (project.json "spin_waits": the ranges its evidence gives): a branch back
        # within one calls tc_spin() when it is taken, as the judge's reference halts there (TcSpin)
        self.spin = list(spin or ())
        self.body = body          # [(lo, hi)] inclusive, or None: discover
        # the function's ranges in the workspace's catalog ([(lo, hi)], hi exclusive: state/function_bodies.txt), for
        # the catalog's ranges of the function's body (state/function_bodies.txt) are its body too, where meta.json's
        # blocks miss some: PS2 8c17e520's last block (`rts` and its slot), Spawn 8c063f20's 8c063f30..8c063f4e
        self.ranges = list(ranges or ())
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
        self.slot_handover = set()  # branches inside the body with a frchg in the slot (it hands over the banks)
        self.direct_rets = set()    # returns by `jmp @rN` with the entry PR in rN (no dispatch on PR)
        self._entry_pr = None       # holds_entry_pr: the registers that certainly hold the entry PR
        self._frame_in = None       # frame_regs: the registers that may hold a frame address, per instruction
        self._set_in = None         # set_args: the argument registers set since the entry or the last call
        self._ident_in = None       # float_idents: the identity constants each float register may hold
        self._arith_in = None       # float_arith: the float registers that certainly hold an arithmetic result
        self._fpscr_free = None     # fpscr_free: the sts/lds fpscr that only set FPSCR's untracked bits
        self.fpscr_why = None       # fpscr_free: why it refused, where the reason is known
        self.recursive = False      # the function calls itself: the body is the helper `body`, given r15 and PR
        self.call_helper = False    # calls to callees DP's meta does not list go through the tx_call helper (size)
        self.emitted: dict[int, list] = {}  # non-branch instruction -> its C lines (before any mutation)
        self.span: dict[int, list] = {}     # node -> [first, end) of its lines in the emitted items
        self.simplify = set()               # sh4translate.simplify.PASSES to apply
        self.dropped: set[int] = set()
        self.helpers: set[str] = set()      # static helpers the body uses (u2f, f2u, f_abs)
        self.handover: dict[int, set] = {}  # node -> the state it hands over and takes back when not all of it
        self.call_reads: dict[int, set] = {}    # node -> the registers its call's TcArgs block reads (call_args)

    # ---------------------------------------------------------------------------------------------- discovery
    def local(self, a):
        if self.body is not None:
            return any(lo <= a <= hi for lo, hi in self.body) or any(lo <= a < hi for lo, hi in self.ranges)
        return a == self.entry or (a not in self.entries and self.img.has(a, 2))

    def halts_at(self, a):
        """The oracle's halting callee/loop: `bra .` with a nop in its slot (TcExit.halts)."""
        if not self.img.has(a, 4):
            return False
        i = self.img.insn(a)
        return i.op == "bra" and i.ea == a and self.img.insn(a + 2).op == "nop"

    def holds_entry_pr(self, a, reg):
        """reg at a (before the instruction) is certainly the function's own return address: a forward
        must-analysis from the entry, where PR holds it; `sts pr,rN`, `lds rN,pr` and register moves carry it, and
        a call (it sets PR) or any other write ends it. A callee returns to one of the body's PR targets with
        everything but r0 and fr0 as it left them (DP's mocked callee)."""
        if self._entry_pr is None:
            def step(e, i):
                o, n, m = i.op, R(i.n), R(i.m)
                if o == "sts" and i.imm == 2:
                    return e | {n} if "pr" in e else e - {n}
                if o == "lds" and i.imm == 2:
                    return e | {"pr"} if m in e else e - {"pr"}
                if o == "mov":
                    return e | {n} if m in e else e - {n}
                e = e - set(writes(i))
                return e - {"pr"} if o in ("bsr", "bsrf", "jsr") else e

            def node(e, x):
                i = self.insns[x]
                e = step(e, i)
                return step(e, self.insns[x + 2]) if i.delayed and x + 2 in self.insns else e
            ein = {self.entry: frozenset({"pr"})}
            work = [self.entry]
            while work:
                x = work.pop()
                out = frozenset(node(set(ein[x]), x))
                nxt = list(self.succ.get(x, ())) or sorted(self.pr_targets)
                for y in nxt:
                    if y not in self.insns:
                        continue
                    v = out if y not in ein else ein[y] & out
                    if v != ein.get(y):
                        ein[y] = v
                        work.append(y)
            self._entry_pr = ein
        return reg in self._entry_pr.get(a, ())

    def call_returns(self):
        """The return addresses of this body's own calls that PR may still hold where the body leaves (rts, a tail
        jump to another function, running on into one): a forward may-analysis of PR from the entry, where it holds
        the caller's address. A call leaves its return address there; `lds.l @Rm+,pr` gives the caller's back (the
        saved PR, as everywhere in this translator); `lds Rm,pr` a pr_targets address. DP's oracle returns from a
        mocked callee, and the SH-4 from rts, to PR: into this body when it holds one of these."""
        ENTRY = -1
        val = {self.entry: frozenset({ENTRY})}
        work = [self.entry]

        def out(x, v):
            i = self.insns[x]
            for y in [i] + ([self.insns[x + 2]] if i.delayed and x + 2 in self.insns else []):
                if y.op == "lds.l" and y.imm == 2:
                    v = frozenset({ENTRY})
                elif y.op == "lds" and y.imm == 2:
                    c = self.reg_const(x, R(y.m))
                    v = frozenset({c if c is not None else ENTRY})  # (a value from elsewhere: no call's here)
            return frozenset({x + 4}) if i.op in ("bsr", "bsrf", "jsr") else v

        leaves = set()
        while work:
            x = work.pop()
            v = out(x, val[x])
            i = self.insns[x]
            nxt = list(self.succ.get(x, ()))
            tail = (i.op == "rts" or x in self.fall_out
                    or (i.op in ("bt", "bf", "bt/s", "bf/s", "bra") and not self.local(i.ea) and not self.halts_at(i.ea))
                    or (i.op in ("braf", "jmp") and not (self.dec_used.get(x) and all(
                        self.local(t) or self.halts_at(t) for t in self._targets(self.dec_used[x])))
                        and not (i.op == "jmp" and not self.dec_used.get(x) and self.holds_entry_pr(x, R(i.m)))))
            if tail:
                leaves |= {c for c in v if c != ENTRY and self.local(c)}
                nxt += [c for c in v if c != ENTRY and self.local(c)]
            for y in nxt:
                if y not in self.insns:
                    continue
                w = v | val.get(y, frozenset())
                if w != val.get(y):
                    val[y] = w
                    work.append(y)
        return leaves

    @staticmethod
    def _targets(d):
        return [d[1]] if d[0] == "const" else list(d[1])

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
            return ("const", fold_p0((c + a + 4) & U32 if i.op in ("bsrf", "braf") else c))
        if i.op in ("jmp", "braf"):
            t = self.resolve_table(a)
            if t:
                return ("table", tuple(fold_p0(x) for x in t))
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
            if i.delayed and self.insns[a + 2].op == "lds" and self.insns[a + 2].imm == 2 and not (
                    i.op == "bra" and not self.local(i.ea)):
                # (in the slot of a tail jump PR is restored for the callee to return with: the function's own return
                # address, as with `lds.l @r15+,pr`)
                raise TranslateError(f"lds Rm,pr in a delay slot at {a + 2:08x}")
            if i.delayed and self.insns[a + 2].op == "lds" and self.insns[a + 2].imm == 2:
                # the tail jump's callee returns to the PR set here: into this body when it is one of its addresses
                v = self.reg_const(a, R(self.insns[a + 2].m))
                if v is not None and self.local(v) and v not in self.pr_targets:
                    self.pr_targets.add(v)
                    self.leaders.add(v)
                    work.append(v)
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

    def symexec(self, lo, hi):
        """Symbolic values of the registers after running [lo, hi) straight-line: ('c', v) constant,
        ('x', reg, scale, add) affine in reg's value at lo, ('ld', size, base, reg, scale, add) a table load."""
        st = {R(k): ("x", R(k), 1, 0) for k in range(16)}
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
            elif o.endswith(("@-_st", "@+_ld")) or o in ("sts.l", "stc.l"):
                # a push or pop moves its pointer by the size it moved (so r15 stays known across pushes)
                o = self.insns[a].op if a in self.insns else o
                k = 8 if o.startswith("fmov.d") else {"b": 1, "w": 2}.get(o[4:5] if o.startswith("mov.") else "", 4)
                p = n if o.endswith("@-_st") or o in ("sts.l", "stc.l") else m
                x = st[p]
                for w in writes(i):
                    if w in st:
                        st[w] = ("?",)
                if not o.endswith("@+_ld") or p != n:
                    d = -k if p == n else k
                    st[p] = (x[0], x[1], x[2], x[3] + d) if x[0] == "x" else \
                        ("c", (x[1] + d) & U32) if x[0] == "c" else ("?",)
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
        if v[0] != "x" or v[2] != 1 or v[3] != 0 or start == self.entry or start in self.pr_targets:
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

    def resolve_table(self, a):
        """Targets of the computed jump at a (braf/jmp through a bounded table), or None."""
        i = self.insns[a]
        blk = self.block_start(a)
        st = self.symexec(blk, a)
        v = st[R(i.m)]
        if v[0] != "ld":
            return None
        size, base, reg, scale, add = v[1], v[2], v[3], v[4], v[5]
        extra = v[6] if len(v) > 6 else 0
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
        """`if (cond) <taken>` with the taken lines, and the not-taken counter after it when instrumenting. A branch
        back that repeats one of the project's wait loops (self.spin) halts there when taken: tc_spin()."""
        m = SPIN_GOTO.match(target_lines[0]) if len(target_lines) == 1 else None
        if m and any(lo <= int(m.group(1), 16) <= a <= hi for lo, hi in self.spin):
            return [f"if ({cond}) tc_spin();"] + self._cond_goto(cond, a, target_lines)
        return self._cond_goto(cond, a, target_lines)

    def _cond_goto(self, cond, a, target_lines):
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
        if size == 4 and const is not None and const in self.sig.hw_reads:
            return f"tc_hw_read32({h(const)})"
        return {1: f"(u32)(s32)(s8)rd8({addr})", 2: f"(u32)(s32)(s16)rd16({addr})", 4: f"rd32({addr})"}[size]

    def mem_st(self, size, addr, val, const):
        if const is not None and const in self.sig.hw_writes and size in (2, 4):
            return f"tc_hw_write{16 if size == 2 else 32}({h(const)}, {'(u16)' if size == 2 else ''}{val});"
        return {1: f"wr8({addr}, (u8){val});", 2: f"wr16({addr}, (u16){val});", 4: f"wr32({addr}, {val});"}[size]

    ALL_ARGS = ("r4", "r5", "r6", "r7", "fr4", "fr5", "fr6", "fr7", "fr8", "fr9", "fr10", "fr11",
                "s0", "s1", "s2", "s3", "s4", "s5", "s6", "s7", "r0", "r1", "r2", "r3")

    def call_args(self, target_expr):
        """The TcArgs block's field assignments for a call, as the agents' C writes them (DP's draft step reads the
        arguments from these statements right before tc_call). A callee DP's meta lists gets the registers DP
        compares at the call to it (the rest stay 0); any other call gets them all: r4..r7, the 8 stack words at
        r15 and r0..r3. A computed target gets what any callee the original was seen calling takes, and every
        argument register the function set since its entry or last call (set_args): the target may be one meta
        does not list, which takes those. A stack word the callee takes is read into arg_sK first (a field's value
        must be free of calls for the draft step)."""
        cal = self.sig.callees
        if cal is None:
            regs = set(self.ALL_ARGS)
        elif target_expr.startswith("0x"):
            regs = set(cal.get(int(target_expr[:-1], 16), self.ALL_ARGS))
        else:
            regs = set().union(*cal.values()) | self.set_args(self.cur) if cal else set(self.ALL_ARGS)
        listed = cal is not None and target_expr.startswith("0x") and int(target_expr[:-1], 16) in cal
        if cal is not None:
            # an address in the function's own frame goes along even where DP does not compare it (its R12 rule):
            # the callee may read or fill the local there
            regs |= self.frame_regs(self.cur) & {"r4", "r5", "r6", "r7"}
        self.call_reads.setdefault(self.cur, set()).update(r for r in regs if not r.startswith("s"))
        pre, L = [], []
        for k in range(4):
            if f"r{4 + k}" in regs:
                L.append(f"a.r[{k}] = r{4 + k};")
                self.use(f"r{4 + k}")
        for k in range(8):
            if f"fr{4 + k}" in regs:
                L.append(f"a.fr[{k}] = fr{4 + k};")
                self.use(f"fr{4 + k}")
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

    def frame_regs(self, a):
        """The registers that may hold an address in the function's own stack frame (r15 plus or minus something)
        right before the call at a takes its arguments (after its delay slot): a forward may-analysis over the
        body from the entry, where r15 is one."""
        if self._frame_in is None:
            def step(f, i):
                o, n, m = i.op, R(i.n), R(i.m)
                if o == "mov":
                    return f | {n} if m in f else f - {n}
                if o == "add":
                    return f | {n} if m in f or n in f else f
                if o == "sub":      # (r15 less a size: an area made on the stack; two frame addresses: a distance)
                    return f if n in f and m not in f else f - {n}
                if o == "add#" or o.endswith("@-_st") or o in ("sts.l", "stc.l", "lds.l", "ldc.l", "mac.l", "mac.w"):
                    return f    # the pointer moves along its frame
                if o.endswith("@+_ld"):
                    return (f - {n}) | ({m} & f if m != n else set())
                return f - set(writes(i))

            def node(f, a):
                i = self.insns[a]
                f = step(f, i)
                return step(f, self.insns[a + 2]) if i.delayed and a + 2 in self.insns else f
            fin = {self.entry: frozenset({"r15"})}
            work = [self.entry]
            while work:
                x = work.pop()
                out = frozenset(node(set(fin[x]), x))
                for y in self.succ.get(x, ()):
                    if y in self.insns and not out <= fin.get(y, frozenset()):
                        fin[y] = fin.get(y, frozenset()) | out
                        work.append(y)
            self._frame_in = fin
            self._frame_step = node
        return set(self._frame_step(set(self._frame_in.get(a, ())), a))

    def set_args(self, a):
        """The argument registers (r4..r7, fr4..fr11) the function may have set since its entry (its parameters) or
        its last call, right before the call at a takes its arguments (after its delay slot): a forward
        may-analysis. At a call DP's meta does not list, these are what the original hands over and what DP's judge
        compares (the oracle's CA: TcTrack); a callee leaves them undefined."""
        if self._set_in is None:
            def step(f, i):
                return f | (set(writes(i)) & ARG_REGS)

            def node(f, a):
                i = self.insns[a]
                f = step(f, i)
                return step(f, self.insns[a + 2]) if i.delayed and a + 2 in self.insns else f
            par = set()
            for _, _, st in self.sig.params:    # (drN and fvN are two and four float registers, as TcTrack splits)
                st = st.lower()
                k = int(st[2:]) if st[:2] in ("dr", "fv") and st[2:].isdigit() else None
                par |= {st} if k is None else {F(k + j) for j in range(2 if st[:2] == "dr" else 4)}
            fin = {self.entry: frozenset(par & ARG_REGS)}
            fin.update({x: ARG_REGS for x in self.pr_targets})     # a return into the body: from anywhere
            work = list(fin)
            while work:
                x = work.pop()
                out = frozenset() if self.insns[x].op in ("bsr", "bsrf", "jsr") else frozenset(node(set(fin[x]), x))
                for y in self.succ.get(x, ()):
                    if y in self.insns and (y not in fin or not out <= fin[y]):     # (after a call: empty)
                        fin[y] = fin.get(y, frozenset()) | out
                        work.append(y)
            self._set_in = fin
            self._set_step = node
        return set(self._set_step(set(self._set_in.get(a, ())), a)) & ARG_REGS

    def float_idents(self, a):
        """{float register: the identity constants (1.0, -1.0, 0.0, -0.0) it may hold} right before the instruction at
        a (a delay slot included): a forward may-analysis from fldi0/fldi1 through fmov, fneg, fabs and arithmetic
        on such constants. A C compiler that sees one of them in `x * 1.0f` (x / 1.0f, x - 0.0f, x + -0.0f) drops the
        operation as exact, and with it the SH-4's DN flush of a denormal x (the runner's SSE DAZ acts only on an
        operation that runs): emit_fpu flushes x itself there."""
        if self._ident_in is None:
            vals = {"1": 1.0, "-1": -1.0, "0": 0.0, "-0": -0.0}

            def name(v):
                if v == 0.0:
                    return "-0" if math.copysign(1.0, v) < 0 else "0"
                return {1.0: "1", -1.0: "-1"}.get(v)

            def step(f, i):
                o, fn, fm = i.op, F(i.n), F(i.m)
                if o in ("fldi0", "fldi1"):
                    return {**f, fn: {"0" if o == "fldi0" else "1"}}
                if o == "fneg" and fn in f:
                    return {**f, fn: {name(-vals[v]) for v in f[fn]}}
                if o == "fabs" and fn in f:
                    return {**f, fn: {name(abs(vals[v])) for v in f[fn]}}
                if o == "fmov":
                    g = {k: v for k, v in f.items() if k != fn}
                    return {**g, fn: f[fm]} if fm in f else g
                ops = {"fadd": lambda x, y: x + y, "fsub": lambda x, y: x - y, "fmul": lambda x, y: x * y,
                       "fdiv": lambda x, y: x / y if y else None}
                if o in ops and fn in f and fm in f:
                    r = {name(z) for x in f[fn] for y in f[fm] if (z := ops[o](vals[x], vals[y])) is not None}
                    r.discard(None)
                    g = {k: v for k, v in f.items() if k != fn}
                    return {**g, fn: r} if r else g
                w = fpu_writes(i)
                return {k: v for k, v in f.items() if k not in (w or ())}

            def node(f, a):
                i = self.insns[a]
                slot = self.insns.get(a + 2) if i.delayed else None
                if i.op in ("bsr", "bsrf", "jsr"):
                    f = step(f, slot) if slot else f
                    return {k: v for k, v in f.items() if k != "fr0"}  # a callee hands back fr0 only
                f = step(f, i)
                return step(f, slot) if slot else f

            def join(x, y):
                return {k: x.get(k, set()) | y.get(k, set()) for k in set(x) | set(y)}
            fin = {self.entry: {}}
            work = [self.entry]
            while work:
                x = work.pop()
                out = node(fin[x], x)
                for y in self.succ.get(x, ()):
                    if y not in self.insns:
                        continue
                    old = fin.get(y)
                    new = out if old is None else join(old, out)
                    if new != old:
                        fin[y] = new
                        work.append(y)
            self._ident_in = fin
            self._ident_step = step
        if a in self._ident_in:
            return self._ident_in[a]
        if a - 2 in self._ident_in and a - 2 in self.insns and self.insns[a - 2].delayed:
            return self._ident_step(self._ident_in[a - 2], self.insns[a - 2])
        return {}

    def float_arith(self, a):
        """The float registers that hold an arithmetic result on every path to the instruction at a (a delay slot
        included): a forward must-analysis from fadd, fsub, fmul, fdiv, fmac, fsqrt, fsrra, fipr and ftrv through
        register moves. A NaN such a result holds is, in the judge's reference, the one quiet NaN 0x7fc00000 (its
        emulator keeps no payload), where the PC's FPU makes 0xffc00000 or keeps an operand's: flds of one (its bits
        to FPUL, then to an integer register, where the judge compares bits) takes it as the reference does."""
        if self._arith_in is None:
            made = {"fadd", "fsub", "fmul", "fdiv", "fmac", "fsqrt", "fsrra"}

            def step(f, i):
                o, fn = i.op, F(i.n)
                w = fpu_writes(i) or set()
                g = f - w
                if o in made:
                    return g | {fn}
                if o == "fipr":
                    return g | {F(i.n + 3)}
                if o == "ftrv":
                    return g | {F(i.n + k) for k in range(4)}
                if o == "fmov" and F(i.m) in f:
                    return g | {fn}
                if o == "fmov.d":
                    (bn, xn, yn), (bm, xm, ym) = pair(i.n), pair(i.m)
                    if bn == bm == "fr":
                        return g | {F(x) for x, y in ((xn, xm), (yn, ym)) if F(y) in f}
                return g

            def node(f, a):
                i = self.insns[a]
                slot = self.insns.get(a + 2) if i.delayed else None
                if slot:
                    f = step(f, slot)
                if i.op in ("bsr", "bsrf", "jsr"):
                    return f - {"fr0"}      # a callee hands back fr0 only
                return step(f, i)
            fin = {self.entry: frozenset()}
            work = [self.entry]
            while work:
                x = work.pop()
                out = frozenset(node(set(fin[x]), x))
                for y in self.succ.get(x, ()):
                    if y not in self.insns:
                        continue
                    old = fin.get(y)
                    new = out if old is None else old & out
                    if new != old:
                        fin[y] = new
                        work.append(y)
            self._arith_in = fin
            self._arith_step = step
        if a in self._arith_in:
            return set(self._arith_in[a])
        if a - 2 in self._arith_in and a - 2 in self.insns and self.insns[a - 2].delayed:
            return self._arith_step(set(self._arith_in[a - 2]), self.insns[a - 2])
        return set()

    def fpscr_free(self):
        """The addresses of `sts fpscr,Rn` and `lds Rm,fpscr` that leave out nothing DP's judge compares: the value
        read goes only through `and`/`or` with constants that keep FR, SZ and PR (and RM and DN, where the function
        rounds anything) as they are, and register moves, into an lds of FPSCR; it is never stored, compared,
        handed to a callee that DP compares it on, returned or left in r8..r14. DP's runtime keeps FPSCR's FR, SZ
        and PR only, so such a read and write change nothing (TC 8c0611d8 sets the exception enables, Spawn
        8c010080 clears RM before its own main loop). An `and` may also clear SZ and PR where the function has no
        fschg/fpchg: they are then 0 throughout, the mode the code is translated in (Spawn's library goes back to
        single precision before its calls). An `or` that sets either is double precision or paired moves, which
        DP's raw C (f32 only) cannot hold: `fpscr_why` says so."""
        if self._fpscr_free is not None:
            return self._fpscr_free
        stss = [a for a, i in self.insns.items() if i.op == "sts" and SYSR.get(i.imm) == "fpscr"]
        ldss = {a for a, i in self.insns.items() if i.op == "lds" and SYSR.get(i.imm) == "fpscr"}
        rounds = any(i.op.removesuffix(".dr") in ("fadd", "fsub", "fmul", "fdiv", "fmac", "fsqrt", "float", "fipr",
                                                  "ftrv", "fcnvsd", "fcnvds") for i in self.insns.values())
        keep = 0x00380000 | (0x00040003 if rounds else 0)   # FR, SZ, PR (and DN, RM)
        modes0 = not any(i.op in ("fschg", "fpchg") for i in self.insns.values())   # SZ = PR = 0 throughout
        if not stss or any(self.insns[a].op in ("sts.l", "lds.l") and SYSR.get(self.insns[a].imm) == "fpscr"
                           for a in self.insns):
            self._fpscr_free = set()
            return self._fpscr_free

        def konst(i, c):
            o, n, m = i.op, R(i.n), R(i.m)
            c2 = self.const_step(i, c)
            if o in ("shll", "shll2", "shll8", "shll16") and n in c:
                c2[n] = (c[n] << {"shll": 1, "shll2": 2, "shll8": 8, "shll16": 16}[o]) & U32
            elif o == "not" and m in c:
                c2[n] = ~c[m] & U32
            return c2

        class Bad(Exception):
            pass

        used = set()

        def step(st, i):
            may, must, c = st
            o, n, m = i.op, R(i.n), R(i.m)
            rd = set(reads(i))
            if o == "sts" and SYSR.get(i.imm) == "fpscr":
                return may | {n}, must | {n}, konst(i, c)
            if o == "lds" and SYSR.get(i.imm) == "fpscr":
                if i.addr in ldss and m in must:
                    used.add(i.addr)
                    return may - {m}, must - {m}, c       # its value is spent (a later read of m is ordinary)
                raise Bad
            if rd & may:
                if o in ("and", "or") and n in may and m not in may and m in c:
                    k = c[m]
                    if self.sig.fpscr_state and modes0:
                        # PR is followed (fpu_modes: double precision code) and the runtime's FPSCR takes it (lds)
                        k = k & ~FPSCR_PR if o == "or" else k | FPSCR_PR
                    if o == "or" and k & 0x00180000:
                        self.fpscr_why = (f"it sets FPSCR.{'PR (double precision)' if k & FPSCR_PR else 'SZ'} "
                                          f"at {i.addr:08x}, which DP's raw C cannot follow")
                    if o == "and" and modes0:
                        k |= 0x00180000     # clearing SZ or PR keeps them 0 (PR: as fpu_modes follows it)
                    if (k & keep) != (keep if o == "and" else 0):
                        raise Bad
                    return may, must, konst(i, c)
                if o == "mov" and m in may:
                    return may | {n}, (must | {n}) if m in must else must - {n}, konst(i, c)
                raise Bad
            w = set(writes(i))
            return may - w, must - w, konst(i, c)

        def node(st, a):
            i = self.insns[a]
            slot = self.insns.get(a + 2) if i.delayed else None
            if slot:
                st = step(st, slot)
            if i.op in ("bsr", "bsrf", "jsr"):
                may = st[0]
                tgt = i.ea if i.op == "bsr" else st[2].get(R(i.n)) if i.op == "jsr" else None
                regs = (self.sig.callees or {}).get(tgt) if tgt is not None else None
                if may & {"r4", "r5", "r6", "r7"} and (regs is None or may & set(regs)):
                    raise Bad
                if may & {"r0", "r1", "r2", "r3"} and self.call_helper:
                    raise Bad
                lost = {f"r{k}" for k in range(8)} | {"pr"}
                return st[0] - lost, st[1] - lost, {k: v for k, v in st[2].items() if k not in lost}
            if not self.succ.get(a):        # an exit: a return or a tail call
                if st[0] & ({f"r{k}" for k in range(8, 15)} | ({"r0", "r1"} if self.sig.ret != "void" else set())):
                    raise Bad
            return step(st, i)

        def join(x, y):
            return x[0] | y[0], x[1] & y[1], {k: v for k, v in x[2].items() if y[2].get(k) == v}
        fin = {self.entry: (frozenset(), frozenset(), {})}
        work = [self.entry]
        try:
            while work:
                x = work.pop()
                may, must, c = node((set(fin[x][0]), set(fin[x][1]), dict(fin[x][2])), x)
                out = (frozenset(may), frozenset(must), c)
                for y in self.succ.get(x, ()):
                    if y not in self.insns:
                        continue
                    old = fin.get(y)
                    new = out if old is None else join(old, out)
                    new = (frozenset(new[0]), frozenset(new[1]), new[2])
                    if new != old:
                        fin[y] = new
                        work.append(y)
        except Bad:
            self._fpscr_free = set()
            return self._fpscr_free
        self._fpscr_free = set(stss) | used
        return self._fpscr_free

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
                                     and int(target_expr[:-1], 16) in cal) and \
                not any(r.startswith("fr") for r in self.set_args(self.cur)):
            # (the helper hands over r4..r7 and no float register: a call with a float argument set takes the block)
            self.use("r1", "r2", "r3", "r4", "r5", "r6", "r7", "r15")
            s = ["r0 = tx_call(" + target_expr + ", r0, r1, r2, r3, r4, r5, r6, r7, r15);"]
        else:
            s = self.call_args(target_expr) + [f"r0 = tc_call({target_expr}, &a);"]
        a = self.cur
        if not tail and not halt:
            s.append(f"@FR0:{a}@")
            q = self.sig.sq_window
            if q and q in STATE:
                # a callee that moves the queue pointer up through the window filled the blocks it passed
                self.helpers.add("sq")
                s = [f"sq_p = sh4_r({q[1:]});"] + s + [f"sq_moved(&sq, sq_p, sh4_r({q[1:]}));"]
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
            if st.startswith("fr"):
                if kind != "f32":
                    self.helpers.add("f2u")
                args.append(st if kind == "f32" else f"f2u({st})")
            elif st in [f"r{k}" for k in range(8)]:
                if kind == "f32":
                    self.helpers.add("u2f")
                args.append(f"u2f({st})" if kind == "f32" else st if kind == "u32" else f"({kind}){st}")
            else:
                raise TranslateError(f"a call of the function itself with a parameter in {st} at {a:08x}")
            self.use(st)
        self.use("r15")
        self.recursive = True
        call = f"body({', '.join(['r15', h(a + 4)] + args)})"
        sig = self.sig
        if sig.ret == "void":
            return [call + ";"]
        if sig.ret == "f32":
            self.use("fr0")
            return [f"fr0 = {call};"]
        if sig.ret == "u64" or (sig.ret_reg and not sig.ret_reg.startswith("r")):
            raise TranslateError(f"a call of the function itself returning {sig.ret} at {a:08x}")
        r = sig.ret_reg or "r0"
        self.use(r)
        return [f"{r} = (u32){call};"]

    def emit_insn(self, i: Insn, consts):
        """C lines for a non-branch instruction. consts: known constant registers before it. In a function that may
        store through the store-queue window (Signature.sq_window), every store also marks its bytes in the queues
        and pref sends a queue (the SQ_HELPERS model)."""
        L = self.emit_plain(i, consts)
        if self.sig.sq_window:
            st = store_ea(i)
            if st and st[0] != "r15" and not st[0].startswith("r15 "):
                self.helpers.add("sq")
                L = [SQ_WRITE.sub(r"sq_wr\1(&sq, ", x) for x in L]
            if i.op == "pref":
                self.helpers.add("sq")
                L = L + [f"sq_pref(&sq, {R(i.n)});"]
        return L

    def emit_plain(self, i: Insn, consts):
        """C lines for a non-branch instruction, without the store-queue bookkeeping."""
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
        fl = self.emit_fpu(i)
        if fl is not None:
            return fl
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
            src = SYSR[i.imm]
            cu(src)
            if o == "sts":
                L.append(f"{n} = {src};")
            else:
                cu(n)
                L.append(f"{n} -= 4u;")
                L.append(self.mem_st(4, n, src, None))
        elif o in ("lds", "lds.l"):
            dst = SYSR[i.imm]
            cu(dst)
            if o == "lds":
                cu(m)
                L.append(f"{dst} = {m};")
            else:
                cu(m)
                L.append(f"{dst} = rd32({m}); {m} += 4u;")
        elif o in ("stc", "stc.l"):
            src = CTRL[i.imm]
            if src in ("ssr", "spc"):
                raise TranslateError(f"system register at {i.addr:08x}: {disasm(i)}")
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
            dst = CTRL[i.imm]
            if dst in ("ssr", "spc"):
                raise TranslateError(f"system register at {i.addr:08x}: {disasm(i)}")
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
        elif o in ("shad", "shld"):
            cu(m, n)
            # the manual: Rm >= 0 shifts left by Rm & 31; Rm < 0 shifts right by 32 - (Rm & 31), or by 32 when
            # Rm & 31 is 0 (all sign bits for shad, 0 for shld)
            if o == "shad":
                far, right = f"(u32)((s32){n} >> 31)", f"(u32)((s32){n} >> (32u - ({m} & 31u)))"
            else:
                far, right = "0u", f"{n} >> (32u - ({m} & 31u))"
            L.append(f"{n} = ({m} & 0x80000000u) == 0 ? {n} << ({m} & 31u) : ({m} & 31u) == 0 ? {far} : {right};")
        elif o in ("clrs", "sets"):
            cu("ss")
            L.append(f"ss = {0 if o == 'clrs' else 1};")
        elif o == "movca.l":
            cu("r0", n)
            L.append(self.mem_st(4, n, "r0", c(n)))
        elif o == "pref":
            cu(n)       # a cache prefetch: nothing the program can see
        elif o in ("ocbi", "ocbp", "ocbwb"):
            cu(n)
            L.append(f"sh4_{o}({n});")
        elif o in SYSTEM or o in ("stc.sys", "stc.l.sys", "ldc.sys", "ldc.l.sys"):
            raise TranslateError(f"system instruction at {i.addr:08x}: {disasm(i)}")
        else:
            raise TranslateError(f"no C for {disasm(i)} at {i.addr:08x}")
        return L

    def emit_fpu(self, i: Insn):
        """C lines for an FPU or FPUL instruction (None for any other). fr0..fr15 are f32 variables and FPUL a u32
        one; XMTRX (xf0..xf15) is the runtime's (sh4_xf, sh4_set_xf). Each float operation is one C operation on f32
        values, in the original's order, so it rounds as the original does in the runtime's mode (tc_rt.h)."""
        o, n, m = i.op, i.n, i.m
        if fpu_writes(i) is None and fpu_reads(i) is None:
            return None
        if o in ("fcnvsd", "fcnvds"):
            raise TranslateError(f"{o} with FPSCR.PR=0 (undefined) at {i.addr:08x}: {disasm(i)}")
        rn, rm = R(n), R(m)
        fn, fm = F(n), F(m)
        self.use(*((fpu_writes(i) or set()) | (fpu_reads(i) or set())))
        L = []
        if o.endswith(".dr"):   # double precision (fpu_modes): DRn = fn : fr n+1, the runtime's pair helpers
            fn1, fm1, fx = F(n + 1), F(m + 1), DR_OPS[o[:-3]]
            if o in ("fadd.dr", "fsub.dr", "fmul.dr", "fdiv.dr"):
                L.append(f"{fx}(&{fn}, &{fn1}, {fm}, {fm1});")
            elif o in ("fcmp/eq.dr", "fcmp/gt.dr"):
                L.append(f"t = {fx}({fn}, {fn1}, {fm}, {fm1});")
            elif o == "fsqrt.dr":
                L.append(f"{fx}(&{fn}, &{fn1});")
            elif o in ("float.dr", "fcnvsd.dr"):
                L.append(f"{fx}(&{fn}, &{fn1}, fpul);")
            elif o == "ftrc.dr":
                L.append(f"fpul = (u32){fx}({fn}, {fn1});")
            else:
                L.append(f"fpul = {fx}({fn}, {fn1});")
            return L
        arith = {"fadd": "+", "fsub": "-", "fmul": "*", "fdiv": "/"}
        if o in arith:
            # an operand a compiler may know to be an identity (x * 1.0f): the other is DN-flushed explicitly
            k = self.float_idents(i.addr)
            cn, cm = k.get(fn, set()), k.get(fm, set())
            drop_n = {"fmul": {"1", "-1"}, "fadd": {"-0"}, "fsub": {"-0"}}.get(o, set()) & cn
            drop_m = {"fmul": {"1", "-1"}, "fdiv": {"1", "-1"}, "fadd": {"-0"}, "fsub": {"0"}}[o] & cm
            xn, xm = fn, fm
            if drop_m and fn != fm:
                self.helpers |= {"u2f", "f2u", "f_dn"}
                xn = f"f_dn({fn})"
            if drop_n and fn != fm:
                self.helpers |= {"u2f", "f2u", "f_dn"}
                xm = f"f_dn({fm})"
            L.append(f"{fn} = {xn} {arith[o]} {xm};")
        elif o == "fcmp/eq":
            L.append(f"t = {fn} == {fm};")
        elif o == "fcmp/gt":
            L.append(f"t = {fn} > {fm};")
        elif o == "fmac":
            if FMAC_TWO_ROUNDINGS:
                L.append(f"{fn} = fr0 * {fm} + {fn};")
            else:
                L.append(f"{fn} = sh4_fmac(fr0, {fm}, {fn});")
        elif o == "fneg":
            L.append(f"{fn} = -{fn};")
        elif o == "fabs":
            self.helpers |= {"u2f", "f2u", "f_abs"}
            L.append(f"{fn} = f_abs({fn});")
        elif o == "fsqrt":
            L.append(f"{fn} = sh4_fsqrt_rm({fn});")
        elif o == "fsrra":
            L.append(f"{fn} = sh4_fsrra({fn});")
        elif o in ("fldi0", "fldi1"):
            L.append(f"{fn} = {'0.0f' if o == 'fldi0' else '1.0f'};")
        elif o == "fsts":
            self.helpers.add("u2f")
            L.append(f"{fn} = u2f(fpul);")
        elif o == "flds":
            self.helpers.add("f2u")
            if fn in self.float_arith(i.addr):
                self.helpers.add("f_bits")
                L.append(f"fpul = f_bits({fn});")
            else:
                L.append(f"fpul = f2u({fn});")
        elif o == "float":
            L.append(f"{fn} = (f32)(s32)fpul;")
        elif o == "ftrc":
            L.append(f"fpul = (u32)sh4_ftrc({fn});")
        elif o == "fsca":
            L.append(f"sh4_fsca(fpul, &{fn}, &{F(n + 1)});")
        elif o == "fipr":
            a_ = " ".join(f"a_[{k}] = {F(m + k)};" for k in range(4))
            b_ = " ".join(f"b_[{k}] = {F(n + k)};" for k in range(4))
            L.append(f"{{ f32 a_[4]; f32 b_[4]; {a_} {b_} {F(n + 3)} = sh4_fipr(a_, b_); }}")
        elif o == "ftrv":
            v_ = " ".join(f"v_[{k}] = {F(n + k)};" for k in range(4))
            back = " ".join(f"{F(n + k)} = v_[{k}];" for k in range(4))
            L.append(f"{{ f32 v_[4]; {v_} sh4_ftrv(v_); {back} }}")
        elif o == "fschg":
            L.append("sh4_fschg();")
        elif o == "fpchg":
            L.append("sh4_fpchg();")
        elif o == "frchg":
            # the banks swap in the runtime: the variables go there first and come back swapped
            L += [f"@SYNC_OUT:{i.addr}@", "sh4_frchg();", f"@SYNC_IN:{i.addr}@"]
        elif o == "fmov":
            L.append(f"{fn} = {fm};")
        elif o.startswith("fmov.s@"):
            mode = o[6:]
            if mode == "@_ld":
                L.append(f"{fn} = rdf32({rm});")
            elif mode == "@+_ld":
                L += [f"{fn} = rdf32({rm});", f"{rm} += 4u;"]
            elif mode == "@r0_ld":
                L.append(f"{fn} = rdf32(r0 + {rm});")
            elif mode == "@_st":
                L.append(f"wrf32({rn}, {fm});")
            elif mode == "@-_st":
                L += [f"{rn} -= 4u;", f"wrf32({rn}, {fm});"]
            elif mode == "@r0_st":
                L.append(f"wrf32(r0 + {rn}, {fm});")
        elif o == "fmov.d" or o.startswith("fmov.d@"):
            L += self.emit_pair_move(i)
        elif o in ("lds", "lds.l"):             # Rm -> FPUL
            if SYSR[i.imm] == "fpscr" and i.addr in self.fpscr_free() and self.sig.fpscr_state:
                L.append(f"sh4_lds_fpscr({rm});")     # (FR, SZ and PR as they were: fpscr_free)
            elif SYSR[i.imm] == "fpscr" and i.addr in self.fpscr_free():
                L.append("/* FPSCR back with FR, SZ and PR as they were: the bits it changes are not DP's state */")
            elif SYSR[i.imm] == "fpscr":
                raise TranslateError(f"lds fpscr at {i.addr:08x}: DP's runtime has no FPSCR to write"
                                     + (f": {self.fpscr_why}" if self.fpscr_why else ""))
            else:
                L += [f"fpul = {rm};"] if o == "lds" else [f"fpul = rd32({rm});", f"{rm} += 4u;"]
        elif o in ("sts", "sts.l"):             # FPUL -> Rn
            if SYSR[i.imm] == "fpscr" and i.addr in self.fpscr_free() and self.sig.fpscr_state:
                L.append(f"{rn} = sh4_sts_fpscr();")
            elif SYSR[i.imm] == "fpscr" and i.addr in self.fpscr_free():
                L += [f"{rn} = 0u; /* FPSCR, read only to change bits that are not DP's state (fpscr_free) */"]
            elif SYSR[i.imm] == "fpscr":
                raise TranslateError(f"sts fpscr at {i.addr:08x}: DP's runtime has no FPSCR to read"
                                     + (f": {self.fpscr_why}" if self.fpscr_why else ""))
            else:
                L += [f"{rn} = fpul;"] if o == "sts" else [f"{rn} -= 4u;", f"wr32({rn}, fpul);"]
        else:
            raise TranslateError(f"no C for {disasm(i)} at {i.addr:08x}")
        return L

    def emit_pair_move(self, i: Insn):  # noqa: C901
        """An fmov with FPSCR.SZ=1: 64 bits as two single registers, the lower-numbered one at the lower address
        (DRk: fr k then fr k+1; XDk: xf k-1 then xf k), as DP's judge models it. A register move copies the two in
        that order. Two pairs are either the same or disjoint, so no copy reads what it has already written."""
        o, n, m = i.op, i.n, i.m
        rn, rm = R(n), R(m)

        def words(k):
            """Pair k read as (word at +0, word at +4)."""
            b, x, y = pair(k)
            return (F(x), F(y)) if b == "fr" else (f"sh4_xf({x})", f"sh4_xf({y})")

        def put(k, lo, hi):
            """Statements setting pair k from its word at +0 (lo) and at +4 (hi)."""
            b, x, y = pair(k)
            if b == "fr":
                return [f"{F(x)} = {lo};", f"{F(y)} = {hi};"]
            return [f"sh4_set_xf({x}, {lo});", f"sh4_set_xf({y}, {hi});"]

        if o == "fmov.d":
            return [] if pair(m) == pair(n) else put(n, *words(m))
        mode = o[6:]
        if mode in ("@_ld", "@+_ld"):
            return put(n, f"rdf32({rm})", f"rdf32({rm} + 4u)") + ([f"{rm} += 8u;"] if mode == "@+_ld" else [])
        if mode == "@r0_ld":
            return put(n, f"rdf32(r0 + {rm})", f"rdf32(r0 + {rm} + 4u)")
        lo, hi = words(m)
        if mode == "@-_st":
            return [f"{rn} -= 8u;", f"wrf32({rn}, {lo});", f"wrf32({rn} + 4u, {hi});"]
        if mode == "@r0_st":
            return [f"wrf32(r0 + {rn}, {lo});", f"wrf32(r0 + {rn} + 4u, {hi});"]
        return [f"wrf32({rn}, {lo});", f"wrf32({rn} + 4u, {hi});"]

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
        """C lines for a branch (with its slot folded in) and whether control falls through. A frchg in the slot
        swaps the banks before the branch takes effect: a call's (or tail call's) hand-over moves ahead of the
        swap; on a branch inside the body the slot hands over and takes back the banks itself."""
        L, nxt = self.emit_branch_body(i, slot, consts)
        if "@SLOT_FRCHG@" not in L:
            return L, nxt
        a = i.addr
        k = L.index("@SLOT_FRCHG@")
        sync = f"@SYNC_OUT:{a}@"
        rest = L[k + 1:]
        if any(x.startswith(("@RETURN", "@SYNC_IN_ALL")) for x in rest) and sync not in rest:
            raise TranslateError(f"frchg in the delay slot of a return at {a + 2:08x}")
        if i.op in ("bsr", "bsrf", "jsr", "bra", "braf", "jmp") and sync in rest:
            j = k + 1 + rest.index(sync)
            return L[:k] + [sync, "sh4_frchg();"] + L[k + 1:j] + L[j + 1:], nxt
        if sync in rest:
            raise TranslateError(f"frchg in the delay slot of a conditional call at {a + 2:08x}")
        self.slot_handover.add(a)
        return L[:k] + [sync, "sh4_frchg();", f"@SYNC_IN:{a}@"] + rest, nxt

    def emit_branch_body(self, i: Insn, slot: Insn | None, consts):
        o, a = i.op, i.addr
        L = []
        slot_lines = []
        sw = writes(slot) if slot else set()
        if slot and slot.op == "frchg" and o != "rts":
            slot_lines = ["@SLOT_FRCHG@"]
        elif slot:
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
                # a slot with its own c_ (rotcl, rotcr, div1) would hide the saved T (C4456): it takes k_ instead
                L += [re.sub(r"\bc_\b", "k_", s) for s in slot_lines]
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
            if slot and slot.op == "frchg":
                # the banks swap on the way out: everything is handed over first, and nothing comes back
                if self.sig.ret == "f32":
                    raise TranslateError(f"a float result with frchg in the return's delay slot at {a + 2:08x}")
                return [f"@SYNC_OUT:{a}@", "sh4_frchg();", f"@RETURN_SWAPPED:{a}@"], None
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
            if o == "jmp" and not tgts and self.holds_entry_pr(a, reg):
                # a return through the copy of PR the function kept (`sts pr,rN` ... `jmp @rN`): to the caller,
                # whatever PR now holds
                L += slot_lines
                self.direct_rets.add(a)
                L.append(f"@RETURN:{a}@")
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

    def fpu_modes(self):
        """FPSCR.SZ and PR at every instruction, followed along the control flow from the ABI's entry state (both 0;
        a callee returns with them as they were, DP's mocked callee). With SZ=1 an fmov moves a 64-bit pair: it is
        renamed fmov.d (fmov.d@_ld, ...). PR also changes at an `lds Rm,FPSCR` of a function whose FPSCR is state
        (fpscr_free: Rm is FPSCR read by sts and changed by `or`/`and` with constants; Spawn's library sets PR around
        its double code and clears it before its calls): the PR it loads is followed through those. With PR=1 the
        instructions with a double form are renamed OP.dr (DR_OPS: the runtime's pair helpers); the others are
        refused (undefined with PR=1). Paths may meet with PR different (Spawn 8c5f6ef0: one clears it, the other
        does at the join), or an lds loads a PR this does not follow (TC 8c0611d8, just before its rts): PR is then
        unknown (None) until an lds sets it again, and any instruction whose meaning PR changes is refused there."""
        ldss = {a for a, i in self.insns.items() if i.op == "lds" and SYSR.get(i.imm) == "fpscr"}
        if not any(self.insns[a].op in ("fschg", "fpchg") for a in self.insns) and not (ldss and self.sig.fpscr_state):
            if any(i.op in ("fcnvsd", "fcnvds") for i in self.insns.values()):
                raise TranslateError("fcnvsd/fcnvds with FPSCR.PR=0 (undefined) or set where DP cannot follow it")
            return
        free = self.fpscr_free() if ldss else set()
        self._fpscr_free = None     # (worked out again where it is emitted, after the renaming below)

        def step(st, x):
            sz, pr, fp, c = st
            o, n, m = x.op, R(x.n), R(x.m)
            if o == "fschg" and not pr:
                sz ^= 1
            elif o == "fpchg" and pr is not None:
                pr ^= 1
            fp2 = {k: v for k, v in fp.items() if k not in writes(x)}
            if o == "sts" and SYSR.get(x.imm) == "fpscr":
                fp2[n] = pr
            elif o in ("or", "and") and n in fp and m in c:
                k = bool(c[m] & FPSCR_PR)    # PR set (or) or cleared (and) is known whatever it was
                fp2[n] = (1 if k else fp[n]) if o == "or" else (fp[n] if k else 0)
            elif o == "mov" and m in fp:
                fp2[n] = fp[m]
            elif o == "lds" and SYSR.get(x.imm) == "fpscr" and x.addr in free:
                pr = fp.get(m)  # None: not known here (TC 8c0611d8 or-s a shifted constant), as where paths meet
            return sz, pr, fp2, self.const_step(x, c)

        mode = {self.entry: (0, 0, {}, {})}
        at = {}
        work = [self.entry]
        while work:
            a = work.pop()
            st = mode[a]
            i = self.insns[a]
            for x in [i] + ([self.insns[a + 2]] if i.delayed and a + 2 in self.insns else []):
                at[x.addr] = st[:2]
                st = step(st, x)
            sz, pr, fp, c = st
            if i.op in ("bsr", "bsrf", "jsr"):     # the callee returns with the modes as they were (DP's mock)
                lost = {f"r{k}" for k in range(8)} | {"pr"}
                fp = {k: v for k, v in fp.items() if k not in lost}
                c = {k: v for k, v in c.items() if k not in lost}
            nxt = list(self.succ.get(a, ()))
            if i.op == "lds" and i.imm == 2:
                nxt += sorted(self.pr_targets)
            for b in nxt:
                if b not in self.insns:
                    continue
                if b not in mode:
                    mode[b] = (sz, pr, fp, c)
                    work.append(b)
                    continue
                osz, opr, ofp, oc = mode[b]
                if osz != sz:
                    raise TranslateError(f"FPU modes (FPSCR.SZ) differ where paths meet at {b:08x}")
                npr = opr if opr == pr else None
                nfp = {k: (v if fp.get(k) == v else None) for k, v in ofp.items() if k in fp}
                nc = {k: v for k, v in oc.items() if c.get(k) == v}
                if (npr, nfp, nc) != (opr, ofp, oc):
                    mode[b] = (sz, npr, nfp, nc)
                    work.append(b)
        for a, i in list(self.insns.items()):
            sz, pr = at.get(a, (0, 0))
            if pr is None and (i.op in PR_OPS and not (i.op in ("fneg", "fabs") and i.n % 2 == 0)
                               or i.op in ("fldi0", "fldi1")):
                raise TranslateError(f"FPSCR.PR not known (paths meet with it different, or loaded) at {a:08x}: {disasm(i)}")
            if pr and i.op in DR_OPS:
                regs = [i.n] + ([i.m] if i.op in ("fadd", "fsub", "fmul", "fdiv", "fcmp/eq", "fcmp/gt") else [])
                if any(k % 2 for k in regs):
                    raise TranslateError(f"double precision (FPSCR.PR=1) at {a:08x} on an odd register: {disasm(i)}")
                i = self.insns[a] = dataclasses.replace(i, op=i.op + ".dr")
            elif pr and i.op in ("fneg", "fabs") and i.n % 2 == 0:
                pass    # DRn's sign is fr n's: the single form on fr n does the same
            elif pr and (i.op in PR_OPS or i.op in ("fldi0", "fldi1")):
                raise TranslateError(f"double precision (FPSCR.PR=1) at {a:08x}: {disasm(i)}")
            elif i.op in ("fcnvsd", "fcnvds"):
                raise TranslateError(f"{i.op} with FPSCR.PR=0 (undefined) at {a:08x}")
            if sz and (i.op == "fmov" or i.op.startswith("fmov.s@")):
                self.insns[a] = dataclasses.replace(i, op=i.op.replace("fmov.s", "fmov.d") if "@" in i.op else "fmov.d")

    def translate(self):
        self.has_calls = False
        self.discover()
        self.fpu_modes()
        rets = self.call_returns() - self.pr_targets
        if rets:
            self.pr_targets |= rets
            self._entry_pr = None   # (holds_entry_pr follows the returns to pr_targets)
        self.fpu_all = self.sig.fpu_state or any(self.insns[a].op == "frchg" for a in self.insns)
        # runtime state: r8..r14 and fr12..fr15 always (what a callee may read and what the exit leaves), the whole
        # fr bank when the FPU is state
        self.state = set(STATE) | {F(k) for k in (range(16) if self.fpu_all else range(12, 16))}
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
            elif self.insns[prev].op == "frchg":
                # the frchg handed its float registers over and swapped the banks: the call takes them from the
                # runtime (the variables are the other bank's), and only the rest of the state is handed over
                lines = [f"@SWAPPED_OUT:{prev}@"] + lines[1:]
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
        if i.op != "frchg":     # (a frchg's call is the one it runs on into: after the swap, fall_stmt)
            rd |= self.call_reads.get(a, set())     # a call reads the arguments it hands over
        if i.op == "frchg":
            self.handover[a] = {r for r in self.state if r.startswith("fr")}
        slot_frchg = i.delayed and a + 2 in self.insns and self.insns[a + 2].op == "frchg"
        if a in self.slot_handover:
            self.handover[a] = {r for r in self.state if r.startswith("fr")}
        # what the node writes before its hand-over (a call's own results come after it)
        self.pre_wr[a] = (set() if slot_frchg or i.op == "frchg" else
                          set(writes(self.insns[a + 2])) if i.delayed and a + 2 in self.insns else set(writes(i)))
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
        st = self.state | {"pr", "fr0"}     # fr0: the callee's result is copied in only where it is read
        self.pre_wr = {}
        self.tail_rets = set()
        self.items_ref = items
        rets = set()
        for kind, x in items:
            y = x.strip() if kind != "label" else ""
            if y.startswith(("@RETURN:", "@RETURN_CLEAN:", "@RETURN_SWAPPED:")):
                rets.add(int(y[y.index(":") + 1:-1]))
            if y.startswith("@RETURN_CLEAN:"):
                self.tail_rets.add(int(y[y.index(":") + 1:-1]))
        if self.pr_targets:
            for a in rets:
                self.succ[a] = list(self.succ.get(a, [])) + sorted(self.pr_targets)
        eff = {a: self.node_effect(a) for a in self.primary}
        ret_reads = {"fr0"} if self.sig.ret == "f32" or self.sig.fr0_result else set()
        for a in rets:
            rd, wr, call = eff[a]
            # (after a tail call the result is the callee's r0)
            eff[a] = (rd | (set() if a in self.tail_rets else ret_reads) | ({"pr"} if self.pr_targets else set()),
                      wr, call)
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
        for a in self.handover:
            sync.add(a)
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
                rd = (rd | (dirty_in[a] & self.handover.get(a, st))) if a in sync else rd
                kill = (wr | ((st - {"pr"}) if call else self.handover.get(a, set()))) & st
                inn = (rd & st) | (out - kill)
                if out != live_out[a] or inn != live_in[a]:
                    live_out[a], live_in[a] = out, inn
                    changed = True
        self.live_in, self.live_out = live_in, live_out

    def dirty_out(self, a, dirty_in):
        rd, wr, call = self.eff[a]
        if call:
            return set()
        if a in self.handover:
            return dirty_in[a] - self.handover[a]
        return dirty_in[a] | (wr & self.state)

    def dirty_at(self, a):
        """The state written since the last hand-over, at node a's call, mode switch or exit (its slot included)."""
        d = self.dirty_in[a] | (self.pre_wr[a] & self.state)
        return sorted(d & self.handover.get(a, d), key=regkey)

    def pr_dispatch(self):
        """A return goes to PR: to one of this body's addresses that lds Rm,pr put there, else out of the function."""
        return [f"if (pr == {h(v)}) goto {self.label(v)};" for v in sorted(self.pr_targets)]

    def render(self, items):
        sig = self.sig
        regs = sorted((x for x in self.used if x.startswith("r") and x[1:].isdigit()), key=regkey)
        frs = sorted((x for x in self.used if x.startswith("fr") and x[2:].isdigit()), key=regkey)
        state = [r for r in regs + frs if r in self.state]

        def put(r):
            return f"sh4_set_fr({r[2:]}, {r});" if r.startswith("fr") else f"sh4_set_r({r[1:]}, {r});"

        def get(r):
            return f"{r} = sh4_fr({r[2:]});" if r.startswith("fr") else f"{r} = sh4_r({r[1:]});"
        ret_clean = None
        if sig.ret == "void":
            ret = "return;"
        elif sig.ret == "f32":
            self.use("fr0")
            ret = "return fr0;"
            self.helpers.add("u2f")
            ret_clean = "return u2f(r0);"       # after a tail call: the callee's fr0 is its r0's bits
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
                    body.append(ind + " ".join(put(r) for r in regs_))
            elif y.startswith("@SWAPPED_OUT:"):
                a = int(y[13:-1])
                regs_ = [r for r in sorted(self.dirty_out(a, self.dirty_in), key=regkey) if r in state]
                regs_ = [put(r) for r in regs_] + [get(r) for r in state if r in self.handover.get(a, ())]
                if regs_:
                    body.append(ind + " ".join(regs_))
            elif y.startswith("@SYNC_IN:"):
                a = int(y[9:-1])
                regs_ = sorted((r for r in self.live_out[a] & self.handover.get(a, self.state) if r in state),
                               key=regkey)
                if regs_:
                    body.append(ind + " ".join(get(r) for r in regs_))
            elif y.startswith("@FR0:"):
                a = int(y[5:-1])
                if "fr0" in self.live_out[a] and "fr0" not in self.state:
                    self.helpers.add("u2f")
                    self.use("fr0")
                    if "fr0" not in frs:
                        frs = sorted(frs + ["fr0"], key=regkey)
                    body.append(ind + "fr0 = u2f(r0);")
            elif y.startswith("@RETURN:"):
                regs_ = [r for r in self.dirty_at(int(y[8:-1])) if r in state]
                if sig.fr0_result and "fr0" not in self.state:
                    regs_.append("fr0")         # fr0 is compared at exit too (DP's fr0_results.txt)
                if regs_:
                    body.append(ind + " ".join(put(r) for r in regs_))
                if int(y[8:-1]) not in self.direct_rets:
                    body += [ind + x for x in self.pr_dispatch()]
                body.append(ind + ret)
            elif y.startswith("@RETURN_CLEAN:"):
                body += [ind + x for x in self.pr_dispatch()]
                body.append(ind + (ret_clean or ret))
            elif y.startswith("@RETURN_SWAPPED:"):
                body += [ind + x for x in self.pr_dispatch()]
                body.append(ind + ret)
            elif y == "@SYNC_IN_ALL@":
                if state:
                    body.append(ind + " ".join(get(r) for r in state))
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
        out.append("@HELPERS@")
        if self.recursive:
            k = sig.text.index("(")
            rest = sig.text[k + 1:]
            out.append("/* The function's body, which calls itself: sp and ret_addr are r15 and PR on entry. */")
            out.append("static " + sig.text[:k].replace(sig.name, "body") + "(u32 sp, u32 ret_addr" +
                       (")" if rest.strip() in ("void)", ")") else ", " + rest))
        else:
            out.append(sig.text)
        out.append("{")
        decl = regs + [x for x in ("t", "ss", "sq", "sm", "mach", "macl", "pr", "vbr", "fpul") if x in self.used]
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
            _st.CURRENT = f"{self.entry:08x}"     # names the function in SH4_CFG_LOG / SH4_CFG_DUMP diagnostics
            body = _st.structure(body)
            # a saved T, a switch target, the flag of a jump out of nested loops, a computed call target (only when the body names them)
            decl += ["ct_", "sw_", "fl_", "jt_"]
        body = sink_steps(body)
        if self.simplify:   # only the variables the simplified body still names
            import re as _re
            named = set(_re.findall(r"\w+", "\n".join(x for x in body if not x.strip().startswith("/*"))))
            regs = [r for r in regs if r in named]
            decl = [d for d in decl if d in named]
            frs = [r for r in frs if r in named]
        if decl:
            out.append("    u32 " + ", ".join(f"{d} = 0" for d in decl) + ";")
        if frs:
            out.append("    f32 " + ", ".join(f"{d} = 0.0f" for d in frs) + ";")
        if any("tc_call(" in x for x in body):
            out.append("    TcArgs a = {{0, 0, 0, 0}, {0, 0, 0, 0, 0, 0, 0, 0}, {0, 0, 0, 0, 0, 0, 0, 0}, {0, 0, 0, 0}};")
        if "sq" in self.helpers:
            q = self.sig.sq_window
            out.append("    SqQueues sq;")
            if any(re.search(r"\bsq_p\b", x) for x in body):
                out.append("    u32 sq_p = 0;")
            # the stream before the queue pointer went out through the queues (a pointer the function builds itself:
            # empty queues)
            out.append(f"    sq_start(&sq, {'sh4_r(' + q[1:] + ')' if q in STATE else '0u'});")
        pmap = {}
        for name, kind, st in sig.params:
            if st in [f"r{k}" for k in range(8)]:
                pmap[st] = name
        live0 = self.sim_live_in if self.simplify else None
        for name, kind, st in sig.params:
            # (simplified: a parameter register the body overwrites before reading it is not copied in)
            if st.startswith("fr"):
                if st not in frs or (live0 is not None and st not in live0):
                    out.append(f"    (void){name};")
                continue
            if st not in regs or (self.simplify and st not in self.sim_live_in) or st in direct:
                if st in direct and _re.search(rf"\b{name}\b", "\n".join(body)):
                    continue
                out.append(f"    (void){name};")     # (also a direct one the body never names: a halt)
        for r in regs:
            k = int(r[1:])
            if r in pmap:
                if not (self.simplify and r not in self.sim_live_in) and r not in direct:
                    kind = next(kd for nm, kd, st in sig.params if st == r)
                    if kind == "f32":
                        self.helpers.add("f2u")
                    out.append(f"    {r} = {'f2u(' + pmap[r] + ')' if kind == 'f32' else '(u32)' + pmap[r]};")
            elif 8 <= k <= 14 and r in self.live_in[self.entry]:
                out.append(f"    {r} = sh4_r({k});")
            elif k == 15:
                out.append(f"    r15 = {'sp' if self.recursive else h(self.f.entry_sp)};")
        fparams = {st: (name, kind) for name, kind, st in sig.params if st.startswith("fr")}
        for r in frs:
            if r in fparams:
                if live0 is None or r in live0:
                    name, kind = fparams[r]
                    if kind != "f32":
                        self.helpers.add("u2f")
                    out.append(f"    {r} = {name if kind == 'f32' else 'u2f(' + name + ')'};")
            elif r in self.state and r in self.live_in[self.entry]:
                out.append(f"    {r} = sh4_fr({r[2:]});")
        if "pr" in self.used and "pr" in decl:
            out.append(f"    pr = {'ret_addr' if self.recursive else h(self.f.sentinel)};")
        if self.recursive:
            named = " ".join(out[out.index("{"):] + body)
            out += [f"    (void){x};" for x in ("sp", "ret_addr") if not re.search(rf"= {x};", named)]
        if decl or frs:
            out.append("    " + " ".join(f"(void){d};" for d in decl + frs))
        out += body
        out.append("}")
        if self.recursive:
            call = f"body({', '.join([h(self.f.entry_sp), h(self.f.sentinel)] + [n for n, _, _ in sig.params])})"
            out += ["", sig.text, "{", f"    {call};" if sig.ret == "void" else f"    return {call};", "}"]
        k = out.index("@HELPERS@")
        out[k:k + 1] = self.helper_lines()
        return "\n".join(out) + "\n"

    def helper_lines(self):
        """The static helpers the function uses: the bits of a float as an integer and back."""
        hs = []
        if "u2f" in self.helpers:
            hs.append("static f32 u2f(u32 x) { union { u32 u; f32 f; } v; v.u = x; return v.f; }")
        if "f2u" in self.helpers:
            hs.append("static u32 f2u(f32 x) { union { u32 u; f32 f; } v; v.f = x; return v.u; }")
        if "f_abs" in self.helpers:
            hs.append("static f32 f_abs(f32 x) { return u2f(f2u(x) & 0x7fffffffu); }")
        if "f_dn" in self.helpers:
            hs.append("/* A denormal as the SH-4's FPU reads it (FPSCR.DN=1): a signed zero. */")
            hs.append("static f32 f_dn(f32 x) { u32 u = f2u(x); return (u & 0x7f800000u) ? x : u2f(u & 0x80000000u); }")
        if "f_bits" in self.helpers:
            hs.append("/* The bits of an arithmetic result, a NaN as the judge's reference makes it (0x7fc00000). */")
            hs.append("static u32 f_bits(f32 x) { u32 u = f2u(x); "
                      "return (u & 0x7fffffffu) > 0x7f800000u ? 0x7fc00000u : u; }")
        if hs:
            hs = ["/* The bits of a float as an integer and back (the SH-4's FPUL and register moves, fabs). */"] + hs
        if "sq" in self.helpers:
            hs = SQ_HELPERS + hs
        return hs + [""] if hs else hs


NARROW_RD = re.compile(r"\brd(?:8|16)\(")
STEP_LINE = re.compile(r"^(r1[0-5]|r[0-9]) [+-]= (?:0x[0-9a-f]+u|\d+u);$")
PLAIN_STMT = re.compile(r"^(?:[A-Za-z_][\w.\[\]]* (?:[-+&|^]|<<|>>)?= [^{}]*|[a-z_]\w*\([^{}]*\));$")
CALL_IN = re.compile(r"\b\w+\(")
NAME = re.compile(r"\b[A-Za-z_]\w*\b(?!\s*\()")
LHS = re.compile(r"^([A-Za-z_][\w.\[\]]*) (?:[-+&|^]|<<|>>)?= ")


def sink_steps(body):
    """In a run of plain statements, a call that follows `rN += K;` (itself after an 8- or 16-bit read)
    and does not depend on the step moves above it, with the statements that do depend on it staying in order
    after the call. The judge's compiler (MSVC /O1) miscompiles the first form: it gives the read's address (rN plus
    something else) an induction variable of its own and steps it before the read instead of after (PS2 8c02cc20
    read each byte 7 too far: `r3 = rd8(r6 + r5); r5 += 7u; wr8(r4, (u8)r3);` in a counted loop; a 32-bit read, or
    the step after the call, compiles right; 8c126500 likewise through a copy, `r0 = r4 << 1; ... rd16(r0 + r5)`).
    Only statements that touch nothing the others touch change places, and never two memory accesses or calls."""
    out = list(body)

    def plain(x):           # a comment, or assignments and calls only (no if, break, label, brace, return)
        y = x.strip()
        return y.startswith("/*") or (all(PLAIN_STMT.match(z.strip() + ";") for z in y[:-1].split(";"))
                                      and y.endswith(";") and not y.startswith(("return", "goto", "break", "continue")))

    def ind(x):
        return len(x) - len(x.lstrip())

    def code(x):
        return not x.strip().startswith("/*")

    def names(x):
        return set(NAME.findall(re.sub(r"\b0x[0-9a-f]+u\b|\b\d+u?\b", "", x))) - {"u32", "s32", "u16", "s16", "u8",
                                                                                  "s8", "f32"}

    def writes(x):
        out = set()
        for z in x.strip().split(";"):
            m = LHS.match(z.strip())
            if m:
                out.add(m.group(1).split("[")[0].split(".")[0])
        return out
    i = 0
    while i < len(out):
        m = STEP_LINE.match(out[i].strip())
        if not m:
            i += 1
            continue
        reg, lv = m.group(1), ind(out[i])

        def same(k):
            return 0 <= k < len(out) and plain(out[k]) and ind(out[k]) == lv
        j = i - 1          # an 8- or 16-bit read since reg was last written, in this run
        hit = False
        while same(j) and not (code(out[j]) and reg in writes(out[j])):
            hit |= code(out[j]) and bool(NARROW_RD.search(out[j]))    # (through reg or a copy of it: r0 = r4 << 1)
            j -= 1
        if not hit:
            i += 1
            continue
        # units: a statement with the comments right before it
        k, units, cur = i, [], []
        while same(k):
            cur.append(out[k])
            if code(out[k]):
                units.append(cur)
                cur = []
            k += 1
        tail = cur              # comments at the end of the run stay there
        dep, free = [units[0]], []
        dn, dw = names(units[0][-1]), writes(units[0][-1])
        dcall = bool(CALL_IN.search(units[0][-1]))
        for u in units[1:]:
            x = u[-1]
            xn, xw, xc = names(x), writes(x), bool(CALL_IN.search(x))
            # u crosses the dependent statements before it: it reads none of their results, writes nothing they
            # name, and is not a second memory access or call
            if not (xn & dw) and not (xw & dn) and not (xc and dcall):
                free.append(u)
            else:
                dep.append(u)
                dn |= xn
                dw |= xw
                dcall |= xc
        if any(CALL_IN.search(u[-1]) for u in free):
            new = [y for u in free + dep for y in u] + tail
            out[i:k] = new
        i += 1
    return out


def fold_p0(a):
    """A jump or call target in its 0x8c... form: main RAM is also visible through its P0 mirror 0x0c000000..0x0cffffff,
    and DP's oracle runs a jump there as one to the 0x8c... address (TcBase.fold; only P0 folds)."""
    return a | 0x80000000 if 0x0c000000 <= a < 0x0d000000 else a


def store_ea(i: Insn):
    """(address expression, size) of the memory an instruction stores to, as its C reads after the instruction's own
    statements (a pre-decrement has happened), or None for an instruction that stores nothing."""
    o, n, m = i.op, R(i.n), R(i.m)
    if o == "tas.b":
        return n, 1
    if o == "movca.l":
        return n, 4
    if o in ("sts.l", "stc.l"):
        return n, 4
    if o.startswith("fmov.s@") and o.endswith("_st") or o.startswith("fmov.d@") and o.endswith("_st"):
        k = 4 if o.startswith("fmov.s") else 8
        return (f"r0 + {n}" if o.endswith("@r0_st") else n), k
    if o.startswith("mov.") and o.endswith("_st"):
        k = {"b": 1, "w": 2, "l": 4}[o[4]]
        mode = o[5:]
        if mode == "@disp_st":
            base = m if o[4] in "bw" else n
            return (f"{base} + {i.imm}u" if i.imm else base), k
        if mode == "@r0_st":
            return f"r0 + {n}", k
        if mode == "@gbr_st":
            return f"sh4_stc_gbr() + {i.imm}u", k
        return n, k
    if o in ("and.b@gbr", "or.b@gbr", "xor.b@gbr"):
        return "sh4_stc_gbr() + r0", 1
    return None


# The SH-4 store queues as DP's judge models them (DP's TcSq; tc_rt.h maps the window onto RAM for these functions):
# a store in the window 0xe0000000..0xe3ffffff lands in RAM at once and fills its bytes of queue SQ[address bit 5]
# (at the address' bits 0..4: a block 0x40 on shares them); `pref @a` sends a's queue to a's 32-byte RAM block: the
# bytes stored since, and the rest from the block that queue last held (when that is a's own block, those bytes stay
# as they are, unread: it may be machine code, which the C must not read, R8; a store hands its bytes to the queue for
# the same reason). At entry the stream before the pointer is taken as sent (the queues hold the blocks 0x40 and 0x20
# before it), and a callee that moves the pointer up through the window sends each block it passes.
SPIN_GOTO = re.compile(r"^goto L_([0-9a-f]{8});$")
SQ_WRITE = re.compile(r"\bwr(8|16|32|f32)\(")
SQ_HELPERS = [
    "/* The SH-4 store queues: a store in the window 0xe0000000..0xe3ffffff lands in RAM and fills its bytes of queue",
    " * (address bit 5); pref @a sends a's queue to a's RAM block: the bytes stored since, the rest from the block the",
    " * queue last held. */",
    "typedef struct { u32 from[2]; u32 marked[2]; u8 bytes[2][32]; } SqQueues;",
    "static u32 sq_in(u32 a) { return a - 0xe0000000u < 0x04000000u; }",
    "static void sq_held(SqQueues *q, u32 b)",
    "{",
    "    if (sq_in(b)) { q->from[(b >> 5) & 1u] = 0x8c000000u | (b & 0x00ffffe0u); q->marked[(b >> 5) & 1u] = 0; }",
    "}",
    "static void sq_start(SqQueues *q, u32 p)",
    "{",
    "    q->from[0] = 0; q->from[1] = 0; q->marked[0] = 0; q->marked[1] = 0;",
    "    p &= 0xffffffe0u;",
    "    if (sq_in(p)) { sq_held(q, p - 0x40u); sq_held(q, p - 0x20u); }",
    "}",
    "static void sq_put(SqQueues *q, u32 a, u32 n, u32 v)",
    "{",
    "    u32 i;",
    "    for (i = 0; i < n; i++)",
    "        if (sq_in(a + i)) {",
    "            q->marked[((a + i) >> 5) & 1u] |= 1u << ((a + i) & 31u);",
    "            q->bytes[((a + i) >> 5) & 1u][(a + i) & 31u] = (u8)(v >> (8u * i));",
    "        }",
    "}",
    "/* A store that also fills its bytes of queue. */",
    "static void sq_wr8(SqQueues *q, u32 a, u8 v) { wr8(a, v); sq_put(q, a, 1u, v); }",
    "static void sq_wr16(SqQueues *q, u32 a, u16 v) { wr16(a, v); sq_put(q, a, 2u, v); }",
    "static void sq_wr32(SqQueues *q, u32 a, u32 v) { wr32(a, v); sq_put(q, a, 4u, v); }",
    "static void sq_wrf32(SqQueues *q, u32 a, f32 v) { union { u32 u; f32 f; } b; b.f = v; wrf32(a, v); "
    "sq_put(q, a, 4u, b.u); }",
    "static void sq_pref(SqQueues *q, u32 a)",
    "{",
    "    u8 b[32];",
    "    u32 k, to, from, j;",
    "    if (!sq_in(a)) return;",
    "    k = (a >> 5) & 1u;",
    "    to = 0x8c000000u | (a & 0x00ffffe0u);",
    "    from = q->from[k] ? q->from[k] : to;",
    "    for (j = 0; j < 32u; j++)",
    "        b[j] = ((q->marked[k] >> j) & 1u) ? q->bytes[k][j] : from != to ? rd8(from + j) : 0;",
    "    for (j = 0; j < 32u; j++)",
    "        if (((q->marked[k] >> j) & 1u) || from != to) wr8(to + j, b[j]);   /* a's own bytes stay as they are */",
    "    q->from[k] = to;",
    "    q->marked[k] = 0;",
    "}",
    "static void sq_moved(SqQueues *q, u32 p, u32 r)",
    "{",
    "    u32 b;",
    "    if (!sq_in(p) || r <= p || r - p > 0x1000u) return;",
    "    for (b = p & 0xffffffe0u; b < r; b += 0x20u) sq_held(q, b);",
    "}",
]


FIELD_LINE = re.compile(r"^\s*a\.(r|fr|s|rlo)\[(\d)\] = (.*);$")
FIELD_ORDER = [("r", k) for k in range(4)] + [("fr", k) for k in range(8)] + [("s", k) for k in range(8)] + [("rlo", k) for k in range(4)]


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


def translate_function(image, entry, sig, facts=None, body=None, entries=None, comments=True, simplify=None,
                       ranges=None, spin=None):
    """simplify: the sh4translate.simplify passes to apply (None: all of them; () for none). ranges: the function's
    catalog ranges (FunctionTranslator.ranges). spin: its wait loops' ranges (FunctionTranslator.spin, spin_ranges)."""
    from .simplify import PASSES
    passes = set(PASSES if simplify is None else simplify)

    def make(cm, helper=False):
        try:
            return made(cm, helper, body)
        except TranslateError as e:
            # Ghidra's body can stop short of the machine code's own flow (a branch into a delay slot that then runs
            # on past it): the flow is followed from the entry instead, up to the other functions' entries
            if body is None or not str(e).startswith("flow reaches"):
                raise
            return made(cm, helper, None)

    def made(cm, helper, extent):
        ft = FunctionTranslator(image, entry, sig, facts or TargetFacts(), extent, entries, cm, ranges, spin)
        ft.simplify = passes
        ft.call_helper = helper
        return ft.translate()
    return fit(make, comments)


def spin_ranges(project):
    """{entry: [(lo, hi)]} of the wait loops a DP project.json names ("spin_waits": {address: evidence}), the address
    ranges `8c0119d4..8c0119ee` its evidence gives."""
    out = {}
    for a, why in (project.get("spin_waits") or {}).items():
        out[int(a, 16)] = [(int(lo, 16), int(hi, 16)) for lo, hi in SPIN_RANGE.findall(why or "")]
    return out


SPIN_RANGE = re.compile(r"(?<![0-9a-fA-F])([0-9a-fA-F]{8})\.\.([0-9a-fA-F]{8})(?![0-9a-fA-F])")


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
    # Several statements to a line (never the #include line). A call ends its line, so a line holds the TcArgs
    # statements of one call only (DP's R12 lint reads them line by line: Spawn 8c5ad9ec).
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
        if "tc_call" in x and cur:
            joined.append(cur)
            cur = ""
    if cur:
        joined.append(cur)
    return "\n".join(joined) + "\n"
