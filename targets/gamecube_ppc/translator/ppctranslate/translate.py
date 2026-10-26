"""PowerPC 750CL (Gekko) machine code -> C, one function at a time, in DecompPipeline's GameCube raw-C form (tc_rt.h).

Method: walk the function's reachable instructions from its entry inside its body, cut them into blocks at branch
targets, and lift every instruction into typed statements over C locals that stand for the registers (r0..r31 u32,
f0..f31 f64 for ps0 and p0..p31 for ps1, one u32 0/1 flag per CR bit, XER's CA/SO/OV, CTR, LR, ...). The passes
(passes.py) then propagate constants and copies, drop what nothing observes and fold single-use values into their
use; emit.py prints C and lays the flow out as loops and ifs (structure.py / cfgstruct.py, from sh2-translate).

The guest model is DecompPipeline's judge model (targets/gamecube_ppc: target.json "abi", TcOracle):
- memory through rd8..rdf64 / wr8..wrf64 (big-endian guest MEM1); the stack is guest memory, r1 starts at the ABI's
  entry SP, LR at the return sentinel, r2/r13 and the GQRs at the project's fixed values; every other register is 0;
- a call (bl, bctrl, blrl, and a branch that leaves the body: a tail call) is tc_call / tc_callf with the callee's
  argument registers in TcArgs; afterwards r3 (or f1) is the result and r0, r4..r12, f0, f2..f13 and CTR are 0, as
  the oracle's mocked callee leaves them; CR, XER, r14..r31 and f14..f31 are kept;
- CodeWarrior's register save/restore helpers (_savegpr_NN, _restgpr_NN and family) run inline, as in the oracle;
- a computed branch through a table of addresses inside the body (bctr after lwzx from a constant table) is a switch;
- single-precision results are (f64)(f32)(double operation); stfs stores with the 750CL's conversion (wrf32d);
  fctiw/fctiwz leave the 750CL's upper word; fmadds and family use DP's helpers (the oracle's two roundings).

Nothing here is specific to one game or address: everything comes from the machine code, the signature and the
target facts.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import expr as X
from .decode import Insn, disasm
from .expr import Call, Const, Var, ZERO, FZERO, u, s, load
from .ir import Assign, Block, CallS, Comment, Goto, If, Ret, Store, Switch


class TranslateError(Exception):
    pass


@dataclass
class Facts:
    entry_sp: int = 0x817FE000
    sentinel: int = 0x817FF000
    stack_lo: int = 0x817F0000
    ram: tuple = (0x80000000, 0x81800000)
    fixed: dict = field(default_factory=dict)       # "r2" -> value, "gqr0" -> value

    @classmethod
    def from_target(cls, tj, fixed):
        a = tj.get("abi", {})
        m = tj.get("memory", {})
        st = m.get("stack", ["0x817f0000"])
        ram = m.get("ram", ["0x80000000", "0x81800000"])
        return cls(int(a.get("entry_sp", "0x817fe000"), 16), int(a.get("return_sentinel", "0x817ff000"), 16),
                   int(st[0], 16), (int(ram[0], 16), int(ram[1], 16)),
                   {k.lower(): int(v, 16) for k, v in (fixed or {}).items()})


@dataclass
class Signature:
    """The C signature and where each parameter lives (DP meta.json: params[].storage r3..r10 / f1..f8 / stack)."""
    name: str
    text: str
    params: list                    # [(name, kind, storage, stack_offset)]
    ret: str = "u32"                # void / u32 / s32 / u64 / f32 / f64
    callees: dict | None = None     # {callee address: [the registers DP compares at a call to it]}
    generic: bool = False
    returns: dict | None = None     # {callee address: 'r' / 'f' / 'v'}: where its result is (meta, then returns.py)

    @classmethod
    def from_meta(cls, meta):
        callees = None
        rets = {}
        if "callees" in meta:
            callees = {int(c["addr"], 16): list(c.get("regs", [])) for c in meta["callees"]}
            for c in meta["callees"]:
                r = (c.get("signature") or "").split(" ", 1)[0]
                if r:
                    rets[int(c["addr"], 16)] = "v" if r == "void" else "f" if r in ("f32", "f64") else "r"
        return cls(meta["name"], meta["signature"],
                   [(p["name"], p["kind"], p["storage"], p.get("stack_offset", -1)) for p in meta["params"]],
                   meta["ret"], callees, returns=rets)

    @classmethod
    def generic_for(cls, addr):
        """No meta: every argument register is a candidate parameter (the passes keep those the code reads)."""
        name = f"fn_{addr:08x}"
        params = [(f"arg_r{k}", "u32", f"r{k}", -1) for k in range(3, 11)]
        params += [(f"arg_f{k}", "f64", f"f{k}", -1) for k in range(1, 9)]
        return cls(name, "", params, "u32", None, True)


CRB = ("lt", "gt", "eq", "so")
U64 = "u64"


def crname(bit):
    return f"c{bit // 4}{CRB[bit % 4]}"


def vtype(name):
    if re.fullmatch(r"[fp]\d+", name):
        return "f64"
    if re.fullmatch(r"c\d(lt|gt|eq|so)", name) or name in ("ca", "so", "ov"):
        return "flag"
    return "u32"


def R(n):
    return Var(f"r{n}", "u32")


def F(n):
    return Var(f"f{n}", "f64")


def P(n):
    return Var(f"p{n}", "f64")


def CR(bit):
    return Var(crname(bit), "flag")


def CA():
    return Var("ca", "flag")


def SO():
    return Var("so", "flag")


def OV():
    return Var("ov", "flag")


def RA0(n):
    return ZERO if n == 0 else R(n)


def mask32(mb, me):
    a = 0xFFFFFFFF >> mb
    b = (0xFFFFFFFF << (31 - me)) & 0xFFFFFFFF
    return (a & b) if mb <= me else (a | b)


def rotmask(x, sh, m):
    """((x rotl sh) & m) written with plain shifts where the mask allows."""
    sh &= 31
    if sh == 0:
        return X.band(x, u(m))
    low = (1 << sh) - 1
    if m & low == 0:
        return X.band(X.shl(x, sh), u(m))
    if m & ~low & 0xFFFFFFFF == 0:
        return X.band(X.shr(x, 32 - sh), u(m))
    return X.band(X.bor(X.shl(x, sh), X.shr(x, 32 - sh)), u(m))


def fbits(e):
    return Call("px_f2b", [e], U64)


def bitsf(e):
    return Call("px_b2f", [e], "f64")


def single(e):
    return X.fround(e)


GPR_D = {"lwz", "lwzu", "lbz", "lbzu", "lhz", "lhzu", "lha", "lhau", "lwzx", "lwzux", "lbzx", "lbzux", "lhzx",
         "lhzux", "lhax", "lhaux", "lwbrx", "lhbrx", "lwarx", "addi", "addis", "addic", "addic.", "subfic", "mulli",
         "add", "addc", "adde", "addme", "addze", "divw", "divwu", "mullw", "neg", "subf", "subfc", "subfe",
         "subfme", "subfze", "mulhw", "mulhwu", "mfspr", "mftb", "mfcr", "mfmsr", "mfsr", "mfsrin"}
GPR_A = {"ori", "oris", "xori", "xoris", "andi.", "andis.", "rlwimi", "rlwinm", "rlwnm", "slw", "cntlzw", "and",
         "andc", "nor", "eqv", "xor", "orc", "or", "nand", "srw", "sraw", "srawi", "extsh", "extsb"}
UPDATE = re.compile(r"^(l|st)[a-z]*u x?$".replace(" ", ""))


def gpr_writes(i: Insn):
    o = i.op
    out = set()
    if o in GPR_D:
        out.add(i.rd)
    if o in GPR_A:
        out.add(i.ra)
    if o.startswith(("l", "st", "psq_")) and (o.endswith("u") or o.endswith("ux")) and o not in ("lmw", "stmw"):
        out.add(i.ra)
    if o == "lmw":
        out |= set(range(i.rd, 32))
    return out


class FunctionTranslator:
    def __init__(self, dol, entry, sig: Signature, facts: Facts, body, entries=None, comments=False):
        self.dol, self.entry, self.sig, self.facts = dol, entry, sig, facts
        self.body = sorted(body)              # [(lo, hi_exclusive)]
        self.entries = entries or set()
        self.comments = comments
        self.blocks: list[Block] = []
        self.ntemp = 0
        self.temps = {}                       # temp name -> type
        self.helpers = set()                  # px_ helpers the C needs
        self.notes = []                       # what the translation could not do exactly
        self.gqr_written = set()
        self.extra = []                       # synthetic blocks, laid out after the real ones
        self.nsyn = 0
        self.recursive = False                # bl to its own entry: the body is px_rec(sp_, ...), called for real

    # ------------------------------------------------------------------------------------------------- body
    def in_body(self, a):
        return any(lo <= a < hi for lo, hi in self.body)

    def insn(self, a):
        if not self.dol.has(a, 4):
            raise TranslateError(f"code outside the image at {a:08x}")
        return self.dol.insn(a)

    def is_helper(self, t):
        """CodeWarrior's register save/restore helpers (DP TcFlow.isSaveHelper's rule): only (r11)-based GPR/FPR/
        paired-single moves, mtlr r0 and mr r1,r11, ending in blr."""
        if self.in_body(t) or not self.dol.has(t, 4):
            return None
        out = []
        a = t
        for n in range(40):
            if not self.dol.has(a, 4):
                return None
            i = self.dol.insn(a)
            if i.op == "bclr" and (i.bo & 0x14) == 0x14 and not i.lk:
                return out if n > 0 else None
            if i.op in ("stw", "stfd", "lwz", "lfd", "psq_st", "psq_l") and i.ra == 11:
                out.append(i)
            elif i.op == "mtspr" and i.spr == 8 and i.rd == 0:
                out.append(i)
            elif i.op == "or" and i.ra == 1 and i.rs == 11 and i.rb == 11:
                out.append(i)
            else:
                return None
            a += 4
        return None

    def explore(self):
        seen, leaders, work = set(), {self.entry}, [self.entry]
        self.tables = {}
        while work:
            a = work.pop()
            while a not in seen:
                if not self.in_body(a):
                    break
                seen.add(a)
                i = self.insn(a)
                if i.op == "illegal":
                    raise TranslateError(f"invalid instruction word {i.word:08x} at {a:08x}")
                nxt, ends = self.successors(i)
                for t in nxt:
                    if self.in_body(t) and t not in seen:
                        work.append(t)
                    if self.in_body(t) and (ends or t != a + 4):
                        leaders.add(t)
                if ends:
                    if self.in_body(a + 4):
                        leaders.add(a + 4)
                    if a + 4 not in nxt:
                        break
                a += 4
        self.reach = seen
        self.leaders = leaders

    def successors(self, i: Insn):
        """(in-body successors, ends_block)."""
        a, o = i.addr, i.op
        if o == "b":
            if i.lk:
                h = self.is_helper(i.target)
                if h is not None and any(x.op == "mtspr" for x in h):
                    return [], True          # the restore helper's blr returns from this function
                return [a + 4], False
            return ([i.target] if self.in_body(i.target) else []), True
        if o == "bc":
            always = (i.bo & 0x14) == 0x14
            if i.lk:
                return [a + 4], True
            out = [] if always else [a + 4]
            if self.in_body(i.target):
                out.append(i.target)
            return out, True
        if o == "bclr":
            always = (i.bo & 0x14) == 0x14
            if i.lk:
                return [a + 4], True
            return ([] if always else [a + 4]), True
        if o == "bcctr":
            always = (i.bo & 0x14) == 0x14
            if i.lk:
                return [a + 4], True
            if always:
                tab = self.jump_table(i)
                if tab is not None:
                    self.tables[a] = tab
                    return sorted(set(tab)), True
                return [], True
            return [a + 4], True
        if o == "rfi" or (o in ("tw", "twi") and i.to == 31):
            return [], True
        return [a + 4], False

    # ------------------------------------------------------------------------------------------- jump tables
    def trace_back(self, a, n=32):
        """The instructions before a (newest first), back to a call or a branch other than a conditional one."""
        out = []
        p = a - 4
        while len(out) < n and self.in_body(p) and self.dol.has(p, 4):
            i = self.dol.insn(p)
            if i.op in ("b", "bclr", "bcctr") or (i.op == "bc" and i.lk):
                break
            out.append(i)
            p -= 4
        return out

    def resolve(self, reg, tr, k=0):
        """The constant value of GPR reg before tr[k] (tr: trace_back), or None."""
        for j in range(k, len(tr)):
            i = tr[j]
            if reg not in gpr_writes(i):
                continue
            o = i.op
            if o == "addi":
                b = 0 if i.ra == 0 else self.resolve(i.ra, tr, j + 1)
                return None if b is None else (b + i.simm) & 0xFFFFFFFF
            if o == "addis":
                b = 0 if i.ra == 0 else self.resolve(i.ra, tr, j + 1)
                return None if b is None else (b + (i.simm << 16)) & 0xFFFFFFFF
            if o == "ori":
                b = self.resolve(i.rs, tr, j + 1)
                return None if b is None else b | i.uimm
            if o == "or" and i.rs == i.rb:
                return self.resolve(i.rs, tr, j + 1)
            return None
        return None

    def jump_table(self, i):
        tr = self.trace_back(i.addr)
        src = None
        for j, x in enumerate(tr):
            if x.op == "mtspr" and x.spr == 9:
                src, k = x.rd, j + 1
                break
            if 9 in gpr_writes(x):
                return None
        if src is None:
            return None
        base = None
        idx_reg = None
        for j in range(k, len(tr)):
            x = tr[j]
            if src not in gpr_writes(x):
                continue
            if x.op == "lwzx":
                for b, ix in ((x.ra, x.rb), (x.rb, x.ra)):
                    if b == 0:
                        continue
                    v = self.resolve(b, tr, j + 1)
                    if v is not None:
                        base, idx_reg = v, ix
                        break
            elif x.op == "lwz" and x.ra != 0:
                v = self.resolve(x.ra, tr, j + 1)
                if v is not None:
                    base = (v + x.d) & 0xFFFFFFFF
            break
        if base is None:
            return None
        bound = None
        for x in tr:
            if x.op == "cmpli":
                bound = x.uimm + 1
                break
        out = []
        limit = bound if bound is not None else 4096
        for n in range(limit):
            a = base + 4 * n
            if not self.dol.has(a, 4):
                break
            t = self.dol.u32(a)
            if t & 3 or not self.in_body(t):
                break
            out.append(t)
        if not out or (bound is not None and len(out) < bound):
            return out or None
        return out

    # -------------------------------------------------------------------------------------------- helpers
    def temp(self, ty):
        n = f"t{self.ntemp}"
        self.ntemp += 1
        self.temps[n] = ty
        return n

    def label(self, a):
        return f"L_{a:08x}"

    def synth(self, stmts, term, hint):
        self.nsyn += 1
        b = Block(f"L_{hint}_{self.nsyn}")
        b.stmts, b.term = stmts, term
        self.extra.append(b)
        return b.label

    def ret_expr(self):
        r = self.sig.ret
        if r == "void":
            return None
        if self.recursive:                    # px_rec returns the register; the exported wrapper converts it
            return F(1) if r in ("f32", "f64") else R(3)
        if r == "u32":
            return R(3)
        if r == "s32":
            return X.to_s32(R(3))
        if r == "f64":
            return F(1)
        if r == "f32":
            return X.cast("f32", F(1))
        if r == "u64":
            return X.binop("|", X.shl(X.cast(U64, R(3)), Const(32, U64)), X.cast(U64, R(4)), U64)
        raise TranslateError(f"return type {r}")

    def call(self, target, addr, tail=False):
        """The statements of a call: TcArgs, tc_call/tc_callf, then the mocked callee's clobbers."""
        auto = True
        args = []
        if isinstance(target, Const) and self.sig.callees is not None:
            regs = self.sig.callees.get(target.v & 0xFFFFFFFF)
            if regs is not None:
                auto = False
                for rg in regs:
                    k = int(rg[1:])
                    if rg[0] == "r":
                        args.append(("r", k - 3, R(k)))
                    elif rg[0] == "f":
                        args.append(("f", k - 1, F(k)))
                    elif rg[0] == "s":
                        args.append(("s", k, load("rd32", X.add(R(1), u(8 + 4 * k)))))
        out = [CallS(target, args, addr, auto=auto)]
        out.append(Clobber("r0", ZERO))
        for k in range(4, 13):
            out.append(Clobber(f"r{k}", ZERO))
        out.append(Clobber("f0", FZERO))
        for k in range(2, 14):
            out.append(Clobber(f"f{k}", FZERO))
        out.append(Clobber("ctr", ZERO))
        if not tail:
            out.append(Assign("lr", u(addr + 4)))
        return out

    def can_recurse(self):
        """A self-call can be a C call of px_rec: every parameter in a register, and an r3 or f1 result."""
        return (not self.sig.generic and self.sig.ret in ("void", "u32", "s32", "f32", "f64")
                and all(re.fullmatch(r"r([3-9]|10)|f[1-8]", stg) for _, _, stg, _ in self.sig.params))

    def self_call(self, addr):
        """bl to its own entry. The oracle runs it (it never leaves the body), so the C calls px_rec with the
        parameter registers and the current r1; the callee's clobbers are the same as a mocked call's."""
        out = self.call(u(self.entry), addr)
        kind = "f" if self.sig.ret in ("f32", "f64") else "r"
        c = CallS(u(self.entry), [("p", n, R(int(stg[1:])) if stg[0] == "r" else F(int(stg[1:])))
                                  for n, _, stg, _ in self.sig.params] + [("p", "sp_", R(1))], addr, kind=kind)
        c.selfc = True
        c.note = "dead" if self.sig.ret == "void" else None
        out[0] = c
        return out

    def tail(self, target, addr):
        return self.call(target, addr, tail=True), Ret(self.ret_expr())

    # ------------------------------------------------------------------------------------------------ build
    def build(self):
        self.explore()
        addrs = sorted(self.reach)
        self.recursive = self.can_recurse() and any(
            self.dol.insn(a).op == "b" and self.dol.insn(a).lk and self.dol.insn(a).target == self.entry for a in addrs)
        for a in addrs:
            i = self.dol.insn(a)
            if i.op == "mtspr" and 912 <= i.spr <= 919:
                self.gqr_written.add(i.spr - 912)
        entry = Block("L_entry")
        st = entry.stmts
        st.append(Assign("r1", Var("sp_", "u32") if self.recursive else u(self.facts.entry_sp)))
        for r in ("r2", "r13"):
            if r in self.facts.fixed:
                st.append(Assign(r, u(self.facts.fixed[r])))
        st.append(Assign("lr", u(self.facts.sentinel)))
        for k in range(8):
            if f"gqr{k}" in self.facts.fixed:
                st.append(Assign(f"gqr{k}", u(self.facts.fixed[f"gqr{k}"])))
        for name, kind, stg, off in self.sig.params:
            pv = Var(name, kind)
            if re.fullmatch(r"r([3-9]|10)", stg):
                st.append(Assign(stg, X.to_u32(pv)))
            elif re.fullmatch(r"f[1-8]", stg):
                st.append(Assign(stg, X.cast("f64", pv)))
            elif stg == "stack":
                pass                          # read from the caller's frame like the original does
            else:
                raise TranslateError(f"parameter storage {stg}")
        entry.term = Goto(self.label(self.entry))
        self.blocks = [entry]
        cur = None
        last = None
        for a in addrs:
            if cur is None or a in self.leaders or a - 4 not in self.reach:
                if cur is not None and cur.term is None:
                    self.fall_out(cur, last)
                cur = Block(self.label(a), a)
                self.blocks.append(cur)
            last = a
            i = self.insn(a)
            if self.comments:
                cur.stmts.append(Comment(f"{a:08x}  {disasm(i)}"))
            term = self.lift_control(i, cur)
            if term is not None:
                cur.term = term
                cur = None
                continue
        if cur is not None and cur.term is None:
            self.fall_out(cur, last)
        for b in self.blocks:
            if b.term is None:
                raise TranslateError(f"block {b.label} without terminator")
        self.blocks += self.extra
        return self.blocks

    def fall_out(self, cur, last):
        """cur ends without a branch after the instruction at last: it goes on at last + 4."""
        a = last + 4
        if a in self.reach:
            cur.term = Goto(self.label(a))
        else:
            st2, t = self.tail(u(a), a)
            cur.stmts += st2
            cur.term = t

    def fall(self, i):
        a = i.addr + 4
        if self.in_body(a):
            return self.label(a)
        st, t = self.tail(u(a), a)
        return self.synth(st, t, f"{a:08x}_fall")

    def bc_cond(self, i, stmts, ctr_ok=True):
        bo, bi = i.bo, i.bi
        c = Const(1, "bool")
        if not bo & 4:
            stmts.append(Assign("ctr", X.sub(Var("ctr", "u32"), X.ONE)))
            z = X.compare("==" if bo & 2 else "!=", Var("ctr", "u32"), ZERO)
            c = z
        if not bo & 0x10:
            b = CR(bi) if bo & 8 else X.lnot(CR(bi))
            c = X.logic("&&", c, X.truthy(b)) if not isinstance(c, Const) else X.truthy(b)
        return c

    def lift_control(self, i: Insn, cur: Block):
        """Lift i into cur; a terminator when i ends the block."""
        o, a = i.op, i.addr
        st = cur.stmts
        if o == "b":
            if i.lk:
                h = self.is_helper(i.target)
                if h is not None:
                    returns = False
                    for x in h:
                        st += self.lift(x)
                        if x.op == "mtspr":
                            returns = True
                    if returns:
                        return Ret(self.ret_expr())
                    return None
                if i.target == a + 4:
                    st.append(Assign("lr", u(a + 4)))
                    return None
                if self.in_body(i.target) and i.target != self.entry:     # (to the entry: recursion, a call)
                    raise TranslateError(f"bl into its own body at {a:08x}")
                if self.recursive and i.target == self.entry:
                    st += self.self_call(a)
                    return None
                st += self.call(u(i.target), a)
                return None
            if self.in_body(i.target):
                return Goto(self.label(i.target))
            s2, t = self.tail(u(i.target), a)
            st += s2
            return t
        if o == "bc":
            always = (i.bo & 0x14) == 0x14
            if i.lk:
                c = self.bc_cond(i, st)
                st.append(Assign("lr", u(a + 4)))
                if always:
                    st += self.call(u(i.target), a)
                    return Goto(self.fall(i))
                nxt = self.fall(i)
                lab = self.synth(self.call(u(i.target), a), Goto(nxt), f"{a:08x}_call")
                return If(c, lab, nxt)
            c = self.bc_cond(i, st)
            if self.in_body(i.target):
                yes = self.label(i.target)
            else:
                s2, t = self.tail(u(i.target), a)
                if always:
                    st += s2
                    return t
                yes = self.synth(s2, t, f"{a:08x}_tail")
            if always:
                return Goto(yes)
            return If(c, yes, self.fall(i))
        if o == "bclr":
            always = (i.bo & 0x14) == 0x14
            if i.lk:
                tgt = self.temp("u32")
                st.append(Assign(tgt, Var("lr", "u32")))
                c = self.bc_cond(i, st)
                st.append(Assign("lr", u(a + 4)))
                calls = self.call(Var(tgt, "u32"), a)
                if always:
                    st += calls
                    return Goto(self.fall(i))
                nxt = self.fall(i)
                return If(c, self.synth(calls, Goto(nxt), f"{a:08x}_call"), nxt)
            c = self.bc_cond(i, st)
            if always:
                return Ret(self.ret_expr())
            return If(c, self.synth([], Ret(self.ret_expr()), f"{a:08x}_ret"), self.fall(i))
        if o == "bcctr":
            always = (i.bo & 0x14) == 0x14
            if not i.bo & 4:
                raise TranslateError(f"bcctr decrementing CTR at {a:08x} (invalid form)")
            c = self.bc_cond(i, st)
            ctr = Var("ctr", "u32")
            if i.lk:
                tgt = self.temp("u32")
                st.append(Assign(tgt, ctr))
                st.append(Assign("lr", u(a + 4)))
                calls = self.call(Var(tgt, "u32"), a)
                if always:
                    st += calls
                    return Goto(self.fall(i))
                nxt = self.fall(i)
                return If(c, self.synth(calls, Goto(nxt), f"{a:08x}_call"), nxt)
            if always and a in self.tables:
                cases = []
                for t in dict.fromkeys(self.tables[a]):
                    cases.append((t, self.label(t)))
                s2, t = self.tail(Var("g_", "u32"), a)
                return Switch(ctr, cases, s2 + [t])
            s2, t = self.tail(ctr, a)
            if always:
                st += s2
                return t
            return If(c, self.synth(s2, t, f"{a:08x}_tail"), self.fall(i))
        if o == "rfi":
            self.notes.append(f"rfi at {a:08x}: returns")
            return Ret(self.ret_expr())
        if o in ("tw", "twi") and i.to == 31:
            self.notes.append(f"trap at {a:08x}: ends the function")
            return Ret(self.ret_expr())
        st += self.lift(i)
        return None

    # ------------------------------------------------------------------------------------------------- lift
    def cr0(self, x):
        return [Assign("c0lt", X.compare("<", X.to_s32(x), s(0))), Assign("c0gt", X.compare(">", X.to_s32(x), s(0))),
                Assign("c0eq", X.compare("==", x, ZERO)), Assign("c0so", SO())]

    def cr1_fp(self):
        fp = Var("fpscr", "u32")
        return [Assign(f"c1{CRB[k]}", X.band(X.shr(fp, 31 - k), X.ONE)) for k in range(4)]

    def setcmp(self, crf, lt, gt, eq, so=None):
        return [Assign(f"c{crf}lt", lt), Assign(f"c{crf}gt", gt), Assign(f"c{crf}eq", eq),
                Assign(f"c{crf}so", so if so is not None else SO())]

    def ea_d(self, i):
        return X.add(RA0(i.ra), u(i.d))

    def ea_x(self, i):
        return X.add(RA0(i.ra), R(i.rb))

    def lift(self, i: Insn):
        o = i.op
        m = getattr(self, "op_" + o.replace(".", "_dot"), None)
        if m is not None:
            return m(i)
        if o in ARITH_XO:
            return self.xo_arith(i)
        if o in LOGIC:
            return self.logic(i)
        if o in INT_LOADS:
            return self.int_load(i)
        if o in INT_STORES:
            return self.int_store(i)
        if o in FP_LOADS:
            return self.fp_load(i)
        if o in FP_STORES:
            return self.fp_store(i)
        if o in FP_A or o in FP_X:
            return self.fp_op(i)
        if o.startswith("ps_"):
            return self.ps_op(i)
        if o.startswith("psq_"):
            return self.psq(i)
        if o in NOPS:
            return [Comment(o)] if self.comments else []
        raise TranslateError(f"unsupported instruction {o} at {i.addr:08x} ({disasm(i)})")

    # integer --------------------------------------------------------------------------------------------
    def op_addi(self, i):
        return [Assign(f"r{i.rd}", X.add(RA0(i.ra), u(i.simm)))]

    def op_addis(self, i):
        return [Assign(f"r{i.rd}", X.add(RA0(i.ra), u(i.simm << 16)))]

    def op_addic(self, i):
        a = R(i.ra)
        return [Assign("ca", X.compare("<", X.add(a, u(i.simm)), a)), Assign(f"r{i.rd}", X.add(a, u(i.simm)))]

    def op_addic_dot(self, i):
        return self.op_addic(i) + self.cr0(R(i.rd))

    def op_subfic(self, i):
        a = R(i.ra)
        return [Assign("ca", X.compare("<=", a, u(i.simm))), Assign(f"r{i.rd}", X.sub(u(i.simm), a))]

    def op_mulli(self, i):
        return [Assign(f"r{i.rd}", X.binop("*", R(i.ra), u(i.simm), "u32"))]

    def carry_chain(self, d, x, y, cin):
        """rD = x + y + cin with CA = the carry out (x, y, cin: u32 / flag expressions)."""
        t = self.temp(U64)
        tv = Var(t, U64)
        tot = X.binop("+", X.binop("+", X.cast(U64, x), X.cast(U64, y), U64), X.cast(U64, cin), U64)
        return t, [Assign(t, tot), Assign("ca", X.cast("u32", X.shr(tv, Const(32, U64)))),
                   Assign(f"r{d}", X.cast("u32", tv))]

    def xo_arith(self, i):
        o, d = i.op, i.rd
        a, b = R(i.ra), R(i.rb)
        out = []
        ov = None
        dv = R(d)
        if o == "add":
            r = X.add(a, b)
            ov = (a, b, r)
            out.append(Assign(f"r{d}", r))
        elif o == "addc":
            r = X.add(a, b)
            ov = (a, b, r)
            out += [Assign("ca", X.compare("<", r, a)), Assign(f"r{d}", r)]
        elif o in ("adde", "addme", "addze", "subfe", "subfme", "subfze"):
            x = X.bnot(a) if o.startswith("subf") else a
            y = b if o in ("adde", "subfe") else u(0xFFFFFFFF) if o.endswith("me") else ZERO
            r = X.add(X.add(x, y), CA())
            ov = (x, y, r)
            ovp = self.ov_expr(*ov) if i.oe else None
            _, st = self.carry_chain(d, x, y, CA())
            out += ([Assign("ov", ovp)] if ovp is not None else []) + st
            ov = None
            if i.oe:
                out.append(Assign("so", X.flag_or(SO(), OV())))
        elif o == "subf":
            r = X.sub(b, a)
            ov = (X.bnot(a), b, r)
            out.append(Assign(f"r{d}", r))
        elif o == "subfc":
            r = X.sub(b, a)
            ov = (X.bnot(a), b, r)
            out += [Assign("ca", X.compare("<=", a, b)), Assign(f"r{d}", r)]
        elif o == "neg":
            if i.oe:
                out += [Assign("ov", X.compare("==", a, u(0x80000000))), Assign("so", X.flag_or(SO(), OV()))]
            out.append(Assign(f"r{d}", X.neg(a)))
        elif o == "mullw":
            if i.oe:
                p = X.binop("*", X.cast("s64", X.to_s32(a)), X.cast("s64", X.to_s32(b)), "s64")
                out += [Assign("ov", X.compare("!=", p, X.cast("s64", X.to_s32(X.binop("*", a, b, "u32"))))),
                        Assign("so", X.flag_or(SO(), OV()))]
            out.append(Assign(f"r{d}", X.binop("*", a, b, "u32")))
        elif o == "mulhw":
            out.append(Assign(f"r{d}", Call("ppc_mulhw", [X.to_s32(a), X.to_s32(b)], "u32")))
        elif o == "mulhwu":
            out.append(Assign(f"r{d}", Call("ppc_mulhwu", [a, b], "u32")))
        elif o == "divw":
            if i.oe:
                bad = X.flag_or(X.compare("==", b, ZERO),
                                X.flag_and(X.compare("==", a, u(0x80000000)), X.compare("==", b, u(0xFFFFFFFF))))
                out += [Assign("ov", bad), Assign("so", X.flag_or(SO(), OV()))]
            out.append(Assign(f"r{d}", X.to_u32(Call("ppc_divw", [X.to_s32(a), X.to_s32(b)], "s32"))))
        elif o == "divwu":
            if i.oe:
                out += [Assign("ov", X.compare("==", b, ZERO)), Assign("so", X.flag_or(SO(), OV()))]
            out.append(Assign(f"r{d}", Call("ppc_divwu", [a, b], "u32")))
        else:
            raise TranslateError(o)
        if ov is not None and i.oe:
            out = [Assign("ov", self.ov_expr(*ov)), Assign("so", X.flag_or(SO(), OV()))] + out
        if i.rc:
            out += self.cr0(dv)
        return out

    def ov_expr(self, x, y, r):
        """Signed overflow of r = x + y (+ carry): x and y have one sign and r the other."""
        return X.cast("u32", X.shr(X.band(X.bxor(x, r), X.bxor(y, r)), 31))

    def logic(self, i):
        o = i.op
        rs, d = R(i.rs), i.ra
        out = []
        if o in ("ori", "oris", "xori", "xoris", "andi.", "andis."):
            imm = i.uimm << 16 if o in ("oris", "xoris", "andis.") else i.uimm
            fn = {"o": X.bor, "x": X.bxor, "a": X.band}[o[0]]
            out.append(Assign(f"r{d}", fn(rs, u(imm))))
        elif o == "srawi":
            n = i.sh
            if n == 0:
                out += [Assign("ca", ZERO), Assign(f"r{d}", rs)]
            else:
                out += [Assign("ca", X.flag_and(X.compare("<", X.to_s32(rs), s(0)),
                                                 X.compare("!=", X.band(rs, u((1 << n) - 1)), ZERO))),
                        Assign(f"r{d}", X.to_u32(X.binop(">>", X.to_s32(rs), s(n), "s32")))]
        elif o in ("cntlzw", "extsb", "extsh"):
            e = {"cntlzw": lambda: Call("ppc_cntlzw", [rs], "u32"),
                 "extsb": lambda: X.to_u32(X.cast("s8", rs)),
                 "extsh": lambda: X.to_u32(X.cast("s16", rs))}[o]()
            out.append(Assign(f"r{d}", e))
        else:
            rb = R(i.rb)
            if o == "slw":
                e = X.cond(X.compare("!=", X.band(rb, u(0x20)), ZERO), ZERO, X.shl(rs, X.band(rb, u(31))))
            elif o == "srw":
                e = X.cond(X.compare("!=", X.band(rb, u(0x20)), ZERO), ZERO, X.shr(rs, X.band(rb, u(31))))
            elif o == "sraw":
                big = X.compare("!=", X.band(rb, u(0x20)), ZERO)
                sign = X.to_u32(X.binop(">>", X.to_s32(rs), s(31), "s32"))
                sh = X.to_u32(X.binop(">>", X.to_s32(rs), X.to_s32(X.band(rb, u(31))), "s32"))
                lost = X.cond(big, rs, X.band(rs, X.sub(X.shl(X.ONE, X.band(rb, u(31))), X.ONE)))
                out.append(Assign("ca", X.flag_and(X.compare("<", X.to_s32(rs), s(0)),
                                                    X.compare("!=", lost, ZERO))))
                e = X.cond(big, sign, sh)
            else:
                e = {"and": lambda: X.band(rs, rb), "or": lambda: X.bor(rs, rb) if i.rs != i.rb else rs,
                     "xor": lambda: X.bxor(rs, rb), "nand": lambda: X.bnot(X.band(rs, rb)),
                     "nor": lambda: X.bnot(X.bor(rs, rb)), "eqv": lambda: X.bnot(X.bxor(rs, rb)),
                     "andc": lambda: X.band(rs, X.bnot(rb)), "orc": lambda: X.bor(rs, X.bnot(rb))}[o]()
            out.append(Assign(f"r{d}", e))
        if i.rc:
            out += self.cr0(R(d))
        return out

    def op_rlwinm(self, i):
        out = [Assign(f"r{i.ra}", rotmask(R(i.rs), i.sh, mask32(i.mb, i.me)))]
        return out + (self.cr0(R(i.ra)) if i.rc else [])

    def op_rlwnm(self, i):
        e = Call("ppc_rlwinm", [R(i.rs), X.to_s32(X.band(R(i.rb), u(31))), s(i.mb), s(i.me)], "u32")
        return [Assign(f"r{i.ra}", e)] + (self.cr0(R(i.ra)) if i.rc else [])

    def op_rlwimi(self, i):
        m = mask32(i.mb, i.me)
        e = X.bor(X.band(R(i.ra), u(~m & 0xFFFFFFFF)), rotmask(R(i.rs), i.sh, m))
        return [Assign(f"r{i.ra}", e)] + (self.cr0(R(i.ra)) if i.rc else [])

    def op_cmp(self, i):
        a, b = X.to_s32(R(i.ra)), X.to_s32(R(i.rb))
        return self.setcmp(i.crfd, X.compare("<", a, b), X.compare(">", a, b), X.compare("==", R(i.ra), R(i.rb)))

    def op_cmpi(self, i):
        a, b = X.to_s32(R(i.ra)), s(i.simm)
        return self.setcmp(i.crfd, X.compare("<", a, b), X.compare(">", a, b), X.compare("==", R(i.ra), u(i.simm)))

    def op_cmpl(self, i):
        a, b = R(i.ra), R(i.rb)
        return self.setcmp(i.crfd, X.compare("<", a, b), X.compare(">", a, b), X.compare("==", a, b))

    def op_cmpli(self, i):
        a, b = R(i.ra), u(i.uimm)
        return self.setcmp(i.crfd, X.compare("<", a, b), X.compare(">", a, b), X.compare("==", a, b))

    # CR / XER / SPR ----------------------------------------------------------------------------------------
    def crlogic(self, i):
        a, b = CR(i.crba), CR(i.crbb)
        o = i.op
        e = {"crand": lambda: X.flag_and(a, b), "cror": lambda: X.flag_or(a, b), "crxor": lambda: X.flag_xor(a, b),
             "crnand": lambda: X.lnot(X.flag_and(a, b)), "crnor": lambda: X.lnot(X.flag_or(a, b)),
             "creqv": lambda: X.lnot(X.flag_xor(a, b)), "crandc": lambda: X.flag_and(a, X.lnot(b)),
             "crorc": lambda: X.flag_or(a, X.lnot(b))}[o]()
        if i.crba == i.crbb and o in ("crxor", "creqv"):
            e = Const(0 if o == "crxor" else 1, "bool")
        return [Assign(crname(i.crbd), e)]

    op_crand = op_cror = op_crxor = op_crnand = op_crnor = op_creqv = op_crandc = op_crorc = crlogic

    def op_mcrf(self, i):
        if i.crfd == i.crfs:
            return []
        return [Assign(f"c{i.crfd}{x}", Var(f"c{i.crfs}{x}", "flag")) for x in CRB]

    def op_mfcr(self, i):
        e = ZERO
        for k in range(32):
            e = X.bor(e, X.shl(X.cast("u32", CR(k)), 31 - k)) if k != 31 else X.bor(e, X.cast("u32", CR(k)))
        return [Assign(f"r{i.rd}", e)]

    def op_mtcrf(self, i):
        out = []
        for fld in range(8):
            if i.crm & (0x80 >> fld):
                for j in range(4):
                    out.append(Assign(crname(4 * fld + j), X.band(X.shr(R(i.rs), 31 - (4 * fld + j)), X.ONE)))
        return out

    def op_mcrxr(self, i):
        return self.setcmp(i.crfd, SO(), OV(), CA(), ZERO)[:3] + [Assign(f"c{i.crfd}so", ZERO)] + [
            Assign("so", ZERO), Assign("ov", ZERO), Assign("ca", ZERO)]

    def spr_var(self, spr):
        if spr == 8:
            return "lr"
        if spr == 9:
            return "ctr"
        if 912 <= spr <= 919:
            return f"gqr{spr - 912}"
        if spr in (268, 284):
            return "tbl"
        if spr in (269, 285):
            return "tbu"
        return f"spr{spr}"

    def op_mfspr(self, i):
        if i.spr == 1:
            e = X.bor(X.bor(X.bor(X.shl(X.cast("u32", SO()), 31), X.shl(X.cast("u32", OV()), 30)),
                            X.shl(X.cast("u32", CA()), 29)), Var("xbc", "u32"))
            return [Assign(f"r{i.rd}", e)]
        return [Assign(f"r{i.rd}", Var(self.spr_var(i.spr), "u32"))]

    op_mftb = op_mfspr

    def op_mtspr(self, i):
        rs = R(i.rd)
        if i.spr == 1:
            return [Assign("so", X.band(X.shr(rs, 31), X.ONE)), Assign("ov", X.band(X.shr(rs, 30), X.ONE)),
                    Assign("ca", X.band(X.shr(rs, 29), X.ONE)), Assign("xbc", X.band(rs, u(0x7F)))]
        v = self.spr_var(i.spr)
        if v.startswith("gqr"):
            self.gqr_written.add(int(v[3:]))
        return [Assign(v, rs)]

    def op_mfmsr(self, i):
        return [Assign(f"r{i.rd}", Var("msr", "u32"))]

    def op_mtmsr(self, i):
        return [Assign("msr", R(i.rs))]

    def op_mfsr(self, i):
        return [Assign(f"r{i.rd}", Var(f"sr{i.sr}", "u32"))]

    def op_mtsr(self, i):
        return [Assign(f"sr{i.sr}", R(i.rd))]

    def op_mfsrin(self, i):
        return [Assign(f"r{i.rd}", Var("srin", "u32"))]

    def op_mtsrin(self, i):
        return [Assign("srin", R(i.rd))]

    def op_sc(self, i):
        self.notes.append(f"sc at {i.addr:08x}: a call to the system call vector 0x80000c00")
        return self.call(u(0x80000C00), i.addr)

    def op_tw(self, i):
        self.notes.append(f"conditional trap at {i.addr:08x}: not modelled")
        return [Comment("trap (not modelled)")]

    op_twi = op_tw

    # memory -----------------------------------------------------------------------------------------------
    def int_load(self, i):
        o = i.op
        upd = o.endswith("u") or o.endswith("ux")
        ea = self.ea_x(i) if o.endswith("x") else self.ea_d(i)
        out = []
        if upd:
            out.append(Assign(f"r{i.ra}", ea))
            ea = R(i.ra)
        base = o.rstrip("x").rstrip("u") if o not in ("lwarx", "lwbrx", "lhbrx") else o
        if base in ("lwz", "lwarx"):
            e = load("rd32", ea)
        elif base == "lbz":
            e = X.to_u32(load("rd8", ea))
        elif base == "lhz":
            e = X.to_u32(load("rd16", ea))
        elif base == "lha":
            e = X.to_u32(X.cast("s16", load("rd16", ea)))
        elif base == "lhbrx":
            t = self.temp("u32")
            out.append(Assign(t, X.to_u32(load("rd16", ea))))
            tv = Var(t, "u32")
            e = X.bor(X.band(X.shl(tv, 8), u(0xFF00)), X.shr(tv, 8))
        elif base == "lwbrx":
            t = self.temp("u32")
            out.append(Assign(t, load("rd32", ea)))
            tv = Var(t, "u32")
            e = X.bor(X.bor(X.shl(tv, 24), X.band(X.shl(tv, 8), u(0xFF0000))),
                      X.bor(X.band(X.shr(tv, 8), u(0xFF00)), X.shr(tv, 24)))
        else:
            raise TranslateError(o)
        out.append(Assign(f"r{i.rd}", e))
        return out

    def int_store(self, i):
        o = i.op
        upd = o.endswith("u") or o.endswith("ux")
        ea = self.ea_x(i) if o.endswith("x") else self.ea_d(i)
        rs = R(i.rs if "rs" in i.f else i.rd)
        base = o.rstrip("x").rstrip("u") if o not in ("stwcx.", "stwbrx", "sthbrx") else o
        out = []
        if base in ("stw", "stwcx."):
            out.append(Store("wr32", ea, rs))
        elif base == "sth":
            out.append(Store("wr16", ea, X.cast("u16", rs)))
        elif base == "stb":
            out.append(Store("wr8", ea, X.cast("u8", rs)))
        elif base == "sthbrx":
            out.append(Store("wr16", ea, X.cast("u16", X.bor(X.band(X.shl(rs, 8), u(0xFF00)),
                                                              X.band(X.shr(rs, 8), u(0xFF))))))
        elif base == "stwbrx":
            out.append(Store("wr32", ea, X.bor(X.bor(X.shl(rs, 24), X.band(X.shl(rs, 8), u(0xFF0000))),
                                               X.bor(X.band(X.shr(rs, 8), u(0xFF00)), X.shr(rs, 24)))))
        else:
            raise TranslateError(o)
        if upd:
            out.append(Assign(f"r{i.ra}", ea))
        if o == "stwcx.":
            out += self.setcmp(0, X.ZERO, X.ZERO, X.ONE)
        return out

    def op_lmw(self, i):
        t = self.temp("u32")
        out = [Assign(t, self.ea_d(i))]
        for k, r in enumerate(range(i.rd, 32)):
            out.append(Assign(f"r{r}", load("rd32", X.add(Var(t, "u32"), u(4 * k)))))
        return out

    def op_stmw(self, i):
        t = self.temp("u32")
        out = [Assign(t, self.ea_d(i))]
        for k, r in enumerate(range(i.rd, 32)):
            out.append(Store("wr32", X.add(Var(t, "u32"), u(4 * k)), R(r)))
        return out

    def op_lswi(self, i):
        nb = i.nb or 32
        t = self.temp("u32")
        out = [Assign(t, RA0(i.ra))]
        r = i.rd - 1
        for k in range(nb):
            if k % 4 == 0:
                r = (r + 1) % 32
                out.append(Assign(f"r{r}", ZERO))
            out.append(Assign(f"r{r}", X.bor(R(r), X.shl(X.to_u32(load("rd8", X.add(Var(t, "u32"), u(k)))),
                                                        24 - 8 * (k % 4)))))
        return out

    def op_stswi(self, i):
        nb = i.nb or 32
        t = self.temp("u32")
        out = [Assign(t, RA0(i.ra))]
        r = i.rd - 1
        for k in range(nb):
            if k % 4 == 0:
                r = (r + 1) % 32
            out.append(Store("wr8", X.add(Var(t, "u32"), u(k)), X.cast("u8", X.shr(R(r), 24 - 8 * (k % 4)))))
        return out

    def op_dcbz(self, i):
        t = self.temp("u32")
        out = [Assign(t, X.band(self.ea_x(i), u(0xFFFFFFE0)))]
        for k in range(8):
            out.append(Store("wr32", X.add(Var(t, "u32"), u(4 * k)), ZERO))
        return out

    def op_dcbz_l(self, i):
        self.notes.append(f"dcbz_l at {i.addr:08x}: zeroes the line as dcbz (locked cache not modelled)")
        return self.op_dcbz(i)

    def fp_load(self, i):
        o = i.op
        upd = o.endswith("u") or o.endswith("ux")
        ea = self.ea_x(i) if o.endswith("x") else self.ea_d(i)
        out = []
        if upd:
            out.append(Assign(f"r{i.ra}", ea))
            ea = R(i.ra)
        if o.startswith("lfs"):
            out += [Assign(f"f{i.rd}", X.cast("f64", load("rdf32", ea))), Assign(f"p{i.rd}", F(i.rd))]
        else:
            out.append(Assign(f"f{i.rd}", load("rdf64", ea)))
        return out

    def fp_store(self, i):
        o = i.op
        upd = o.endswith("u") or o.endswith("ux")
        ea = self.ea_x(i) if o.endswith("x") else self.ea_d(i)
        fs = F(i.rs if "rs" in i.f else i.rd)
        if o == "stfiwx":
            out = [Store("wr32", ea, X.cast("u32", fbits(fs)))]
            self.helpers.add("bits")
        elif o.startswith("stfs"):
            out = [Store("wrf32d", ea, fs)]
        else:
            out = [Store("wrf64", ea, fs)]
        if upd:
            out.append(Assign(f"r{i.ra}", ea))
        return out

    # floating point --------------------------------------------------------------------------------------
    def fp_op(self, i):
        o = i.op
        d = i.f.get("frd")
        a = F(i.fra) if "fra" in i.f else None
        b = F(i.frb) if "frb" in i.f else None
        c = F(i.frc) if "frc" in i.f else None
        out = []
        sgl = False
        if o in ("fadd", "fadds"):
            e, sgl = X.binop("+", a, b, "f64"), o.endswith("s")
        elif o in ("fsub", "fsubs"):
            e, sgl = X.binop("-", a, b, "f64"), o.endswith("s")
        elif o in ("fmul", "fmuls"):
            e, sgl = X.binop("*", a, c, "f64"), o.endswith("s")
        elif o in ("fdiv", "fdivs"):
            e, sgl = Call("px_fdiv", [a, b], "f64"), o.endswith("s")
            self.helpers.add("fdiv")
        elif o == "fmadd":
            e = X.binop("+", X.binop("*", a, c, "f64"), b, "f64")
        elif o == "fmsub":
            e = X.binop("-", X.binop("*", a, c, "f64"), b, "f64")
        elif o == "fnmadd":
            e = X.fneg(X.binop("+", X.binop("*", a, c, "f64"), b, "f64"))
        elif o == "fnmsub":
            e = X.fneg(X.binop("-", X.binop("*", a, c, "f64"), b, "f64"))
        elif o in ("fmadds", "fmsubs", "fnmadds", "fnmsubs"):
            e = Call("ppc_" + o, [a, c, b], "f64")
            sgl = None              # already single: ps1 takes it too
        elif o == "fres":
            self.notes.append(f"fres at {i.addr:08x}: exact reciprocal (the 750CL's estimate table is not modelled)")
            e, sgl = Call("px_fdiv", [Const(1.0, "f64"), b], "f64"), True
            self.helpers.add("fdiv")
        elif o == "frsqrte":
            self.notes.append(f"frsqrte at {i.addr:08x}: 1/sqrt to double precision (the estimate is not modelled)")
            e = Call("px_rsqrt", [b], "f64")
            self.helpers.add("rsqrt")
        elif o == "fsel":
            e = Call("ppc_fsel", [a, c, b], "f64")
        elif o == "frsp":
            e, sgl = b, True
        elif o in ("fctiw", "fctiwz"):
            e = Call("px_ctiw", [b, Call("ppc_" + o, [b], "s32")], "f64")
            self.helpers.add("ctiw")
        elif o == "fmr":
            e = b
        elif o == "fneg":
            e = X.fneg(b)
        elif o == "fabs":
            e = Call("ppc_fabs", [b], "f64")
        elif o == "fnabs":
            e = Call("ppc_fnabs", [b], "f64")
        elif o in ("fcmpu", "fcmpo"):
            un = X.flag_or(X.compare("!=", a, a), X.compare("!=", b, b))
            return self.setcmp(i.crfd, X.compare("<", a, b), X.compare(">", a, b), X.compare("==", a, b), un)
        elif o == "mffs":
            self.helpers.add("bits")
            e = bitsf(X.binop("|", Const(0xFFF8000000000000, U64), X.cast(U64, Var("fpscr", "u32")), U64))
        elif o == "mtfsf":
            self.helpers.add("bits")
            mk = 0
            for k in range(8):
                if i.fm & (0x80 >> k):
                    mk |= 0xF << (28 - 4 * k)
            fp = Var("fpscr", "u32")
            out = [Assign("fpscr", X.bor(X.band(fp, u(~mk & 0xFFFFFFFF)),
                                         X.band(X.cast("u32", fbits(b)), u(mk))))]
            self.notes.append(f"mtfsf at {i.addr:08x}: FPSCR kept, rounding mode not modelled")
            return out + (self.cr1_fp() if i.rc else [])
        elif o == "mtfsfi":
            sh = 28 - 4 * i.crfd
            fp = Var("fpscr", "u32")
            return [Assign("fpscr", X.bor(X.band(fp, u(~(0xF << sh) & 0xFFFFFFFF)), u(i.imm << sh)))] + (
                self.cr1_fp() if i.rc else [])
        elif o in ("mtfsb0", "mtfsb1"):
            fp = Var("fpscr", "u32")
            bit = 1 << (31 - i.crbd)
            e2 = X.bor(fp, u(bit)) if o == "mtfsb1" else X.band(fp, u(~bit & 0xFFFFFFFF))
            return [Assign("fpscr", e2)] + (self.cr1_fp() if i.rc else [])
        elif o == "mcrfs":
            fp = Var("fpscr", "u32")
            return [Assign(f"c{i.crfd}{CRB[j]}", X.band(X.shr(fp, 31 - (4 * i.crfs + j)), X.ONE)) for j in range(4)]
        elif o == "fsqrt" or o == "fsqrts":
            raise TranslateError(f"{o} at {i.addr:08x}: not a 750CL instruction")
        else:
            raise TranslateError(f"unsupported instruction {o} at {i.addr:08x}")
        if sgl:
            e = X.fround(e)
        out.append(Assign(f"f{d}", e))
        if sgl or sgl is None:
            out.append(Assign(f"p{d}", F(d)))       # single-precision results fill ps1 too (750CL paired-single mode)
        if i.rc:
            out += self.cr1_fp()
        return out

    def ps_op(self, i):
        o = i.op
        d = i.f.get("frd")
        a0 = F(i.fra) if "fra" in i.f else None
        a1 = P(i.fra) if "fra" in i.f else None
        b0 = F(i.frb) if "frb" in i.f else None
        b1 = P(i.frb) if "frb" in i.f else None
        c0 = F(i.frc) if "frc" in i.f else None
        c1 = P(i.frc) if "frc" in i.f else None
        rnd = X.fround

        def ar(op, x, y):
            return rnd(X.binop(op, x, y, "f64"))

        def div(x, y):
            self.helpers.add("fdiv")
            return rnd(Call("px_fdiv", [x, y], "f64"))

        def fma(fn, x, y, z):
            return Call("ppc_" + fn, [x, y, z], "f64")

        if o.startswith("ps_cmp"):
            x, y = (a0, b0) if o.endswith("0") else (a1, b1)
            un = X.flag_or(X.compare("!=", x, x), X.compare("!=", y, y))
            return self.setcmp(i.crfd, X.compare("<", x, y), X.compare(">", x, y), X.compare("==", x, y), un)
        e0 = e1 = None
        if o == "ps_add":
            e0, e1 = ar("+", a0, b0), ar("+", a1, b1)
        elif o == "ps_sub":
            e0, e1 = ar("-", a0, b0), ar("-", a1, b1)
        elif o == "ps_mul":
            e0, e1 = ar("*", a0, c0), ar("*", a1, c1)
        elif o == "ps_div":
            e0, e1 = div(a0, b0), div(a1, b1)
        elif o == "ps_muls0":
            e0, e1 = ar("*", a0, c0), ar("*", a1, c0)
        elif o == "ps_muls1":
            e0, e1 = ar("*", a0, c1), ar("*", a1, c1)
        elif o in ("ps_madd", "ps_msub", "ps_nmadd", "ps_nmsub"):
            fn = "f" + o[3:] + "s"
            e0, e1 = fma(fn, a0, c0, b0), fma(fn, a1, c1, b1)
        elif o == "ps_madds0":
            e0, e1 = fma("fmadds", a0, c0, b0), fma("fmadds", a1, c0, b1)
        elif o == "ps_madds1":
            e0, e1 = fma("fmadds", a0, c1, b0), fma("fmadds", a1, c1, b1)
        elif o == "ps_sum0":
            e0, e1 = ar("+", a0, b1), c1
        elif o == "ps_sum1":
            e0, e1 = c0, ar("+", a0, b1)
        elif o == "ps_sel":
            e0, e1 = Call("ppc_fsel", [a0, c0, b0], "f64"), Call("ppc_fsel", [a1, c1, b1], "f64")
        elif o == "ps_res":
            self.notes.append(f"ps_res at {i.addr:08x}: exact reciprocal (estimate not modelled)")
            e0, e1 = div(Const(1.0, "f64"), b0), div(Const(1.0, "f64"), b1)
        elif o == "ps_rsqrte":
            self.notes.append(f"ps_rsqrte at {i.addr:08x}: 1/sqrt in single (estimate not modelled)")
            self.helpers.add("rsqrt")
            e0, e1 = rnd(Call("px_rsqrt", [b0], "f64")), rnd(Call("px_rsqrt", [b1], "f64"))
        elif o == "ps_neg":
            e0, e1 = X.fneg(b0), X.fneg(b1)
        elif o == "ps_abs":
            e0, e1 = Call("ppc_fabs", [b0], "f64"), Call("ppc_fabs", [b1], "f64")
        elif o == "ps_nabs":
            e0, e1 = Call("ppc_fnabs", [b0], "f64"), Call("ppc_fnabs", [b1], "f64")
        elif o == "ps_mr":
            e0, e1 = b0, b1
        elif o.startswith("ps_merge"):
            e0 = a0 if o[-2] == "0" else a1
            e1 = b0 if o[-1] == "0" else b1
        else:
            raise TranslateError(f"unsupported instruction {o} at {i.addr:08x}")
        t0, t1 = self.temp("f64"), self.temp("f64")
        out = [Assign(t0, e0), Assign(t1, e1), Assign(f"f{d}", Var(t0, "f64")), Assign(f"p{d}", Var(t1, "f64"))]
        if i.rc:
            out += self.cr1_fp()
        return out

    def gqr(self, idx):
        if idx not in self.gqr_written and f"gqr{idx}" in self.facts.fixed:
            return self.facts.fixed[f"gqr{idx}"]
        return None

    def psq(self, i):
        o = i.op
        upd = o.endswith("u") or o.endswith("ux")
        ea = self.ea_x(i) if o.endswith("x") else self.ea_d(i)
        out = []
        if upd:
            out.append(Assign(f"r{i.ra}", ea))
            ea = R(i.ra)
        else:
            t = self.temp("u32")
            out.append(Assign(t, ea))
            ea = Var(t, "u32")
        g = self.gqr(i.i)
        if o.startswith("psq_l"):
            if g is None:
                self.helpers.add("psq")
                gv = Var(f"gqr{i.i}", "u32")
                e0 = Call("px_dq", [gv, ea], "f64")
                e1 = Const(1.0, "f64") if i.w else Call("px_dq", [gv, X.add(ea, Call("px_qsize", [
                    X.band(X.shr(gv, 16), u(7))], "u32"))], "f64")
            else:
                typ, sc = (g >> 16) & 7, (g >> 24) & 0x3F
                sc = sc - 64 if sc & 0x20 else sc
                size = {4: 1, 6: 1, 5: 2, 7: 2}.get(typ, 4)
                e0 = self.dq(typ, sc, ea)
                e1 = Const(1.0, "f64") if i.w else self.dq(typ, sc, X.add(ea, u(size)))
            t0, t1 = self.temp("f64"), self.temp("f64")
            out += [Assign(t0, e0), Assign(t1, e1), Assign(f"f{i.frd}", Var(t0, "f64")),
                    Assign(f"p{i.frd}", Var(t1, "f64"))]
            return out
        # store
        if g is None:
            self.helpers.add("psq")
            gv = Var(f"gqr{i.i}", "u32")
            out.append(_QStore(gv, ea, F(i.frd)))
            if not i.w:
                out.append(_QStore(gv, X.add(ea, Call("px_qsize", [X.band(gv, u(7))], "u32")), P(i.frd)))
            return out
        typ, sc = g & 7, (g >> 8) & 0x3F
        sc = sc - 64 if sc & 0x20 else sc
        size = {4: 1, 6: 1, 5: 2, 7: 2}.get(typ, 4)
        out += self.q(typ, sc, ea, F(i.frd))
        if not i.w:
            out += self.q(typ, sc, X.add(ea, u(size)), P(i.frd))
        return out

    def dq(self, typ, sc, ea):
        if typ not in (4, 5, 6, 7):
            return X.cast("f64", load("rdf32", ea))
        raw = {4: lambda: load("rd8", ea), 5: lambda: load("rd16", ea),
               6: lambda: X.cast("s8", load("rd8", ea)), 7: lambda: X.cast("s16", load("rd16", ea))}[typ]()
        v = X.cast("f64", raw)
        if sc:
            v = X.binop("*", v, Const(2.0 ** -sc, "f64"), "f64")
        return X.fround(v)

    def q(self, typ, sc, ea, v):
        if typ not in (4, 5, 6, 7):
            return [Store("wrf32d", ea, v)]
        lo, hi = {4: (0, 255), 5: (0, 65535), 6: (-128, 127), 7: (-32768, 32767)}[typ]
        self.helpers.add("quant")
        e = Call("px_quant", [v, Const(2.0 ** sc, "f64"), s(lo), s(hi)], "s32")
        if typ in (4, 6):
            return [Store("wr8", ea, X.cast("u8", e))]
        return [Store("wr16", ea, X.cast("u16", e))]


class Clobber(Assign):
    """A register a mocked callee leaves 0 (not a value of this function's own)."""
    __slots__ = ()


class _QStore(Store):
    """A quantized store through a GQR only known at run time: px_q(gqr, ea, value)."""
    __slots__ = ("g",)

    def __init__(self, g, addr, val):
        super().__init__("px_q", addr, val)
        self.g = g

    def reads(self):
        return super().reads() | self.g.vars()

    def exprs(self):
        return [self.addr, self.val, self.g]

    def set_exprs(self, es):
        self.addr, self.val, self.g = es

    def size(self):
        return 4


ARITH_XO = {"add", "addc", "adde", "addme", "addze", "divw", "divwu", "mullw", "neg", "subf", "subfc", "subfe",
            "subfme", "subfze", "mulhw", "mulhwu"}
LOGIC = {"ori", "oris", "xori", "xoris", "andi.", "andis.", "slw", "srw", "sraw", "srawi", "cntlzw", "extsb", "extsh",
         "and", "or", "xor", "nand", "nor", "eqv", "andc", "orc"}
INT_LOADS = {"lwz", "lwzu", "lwzx", "lwzux", "lbz", "lbzu", "lbzx", "lbzux", "lhz", "lhzu", "lhzx", "lhzux", "lha",
             "lhau", "lhax", "lhaux", "lwbrx", "lhbrx", "lwarx"}
INT_STORES = {"stw", "stwu", "stwx", "stwux", "stb", "stbu", "stbx", "stbux", "sth", "sthu", "sthx", "sthux",
              "stwbrx", "sthbrx", "stwcx."}
FP_LOADS = {"lfs", "lfsu", "lfsx", "lfsux", "lfd", "lfdu", "lfdx", "lfdux"}
FP_STORES = {"stfs", "stfsu", "stfsx", "stfsux", "stfd", "stfdu", "stfdx", "stfdux", "stfiwx"}
FP_A = {"fadd", "fadds", "fsub", "fsubs", "fmul", "fmuls", "fdiv", "fdivs", "fmadd", "fmadds", "fmsub", "fmsubs",
        "fnmadd", "fnmadds", "fnmsub", "fnmsubs", "fres", "frsqrte", "fsel", "fsqrt", "fsqrts"}
FP_X = {"fcmpu", "fcmpo", "frsp", "fctiw", "fctiwz", "fneg", "fmr", "fnabs", "fabs", "mffs", "mtfsf", "mtfsfi",
        "mtfsb0", "mtfsb1", "mcrfs"}
NOPS = {"sync", "isync", "eieio", "dcbf", "dcbi", "dcbst", "dcbt", "dcbtst", "icbi", "tlbie", "tlbsync"}


# ------------------------------------------------------------------------------------------------------- driver
ARG_REGS = [f"r{k}" for k in range(3, 11)] + [f"f{k}" for k in range(1, 9)]


def all_stmts(blocks):
    for b in blocks:
        yield from b.stmts
        if isinstance(b.term, Switch):
            yield from b.term.default


def fill_auto_args(ft, blocks):
    """A call whose callee DP's meta does not list (indirect, or no meta) passes every argument register the
    function may have set: written by its own code (not a callee's clobber) or a parameter."""
    defined = {stg for _, _, stg, _ in ft.sig.params}
    for s in all_stmts(blocks[1:]):
        if not isinstance(s, (Clobber, CallS)):
            defined |= s.writes()
    regs = [r for r in ARG_REGS if r in defined]
    for s in all_stmts(blocks):
        if isinstance(s, CallS) and s.auto:
            s.args = [("r", int(r[1:]) - 3, R(int(r[1:]))) if r[0] == "r" else ("f", int(r[1:]) - 1, F(int(r[1:])))
                      for r in regs]


def var_types(ft, blocks):
    from .passes import children
    types = {n: k for n, k, _, _ in ft.sig.params}
    types.update(ft.temps)

    def walk(e):
        if isinstance(e, Var):
            types.setdefault(e.name, e.ty)
        for c in children(e):
            walk(c)
    for b in blocks:
        sts = list(b.stmts) + (list(b.term.default) if isinstance(b.term, Switch) else [])
        for s in sts:
            for w in s.writes():
                types.setdefault(w, ft.temps.get(w) or vtype(w))
            for e in s.exprs():
                walk(e)
        for e in b.term.exprs():
            walk(e)
    types["g_"] = "u32"
    return types


def generic_ret(blocks):
    w = set()
    for s in all_stmts(blocks[1:]):
        if not isinstance(s, Clobber):
            w |= s.writes() if not isinstance(s, CallS) else {"r3"}
    return "u32" if "r3" in w else "f64" if "f1" in w else "void"


def translate_function(dol, entry, sig, facts, body, comments=False, structured=True, optimise=True, returns=None):
    """(C text, FunctionTranslator) for the function at entry whose body is [(lo, hi_exclusive)]. returns: the
    whole executable's return kinds (returns.analyse), for callees DP's meta does not describe."""
    from . import emit, passes
    if returns:
        sig.returns = {**returns, **(sig.returns or {})}
    ft = FunctionTranslator(dol, entry, sig, facts, body, comments=comments)
    blocks = ft.build()
    if sig.generic:
        r = {"r": "u32", "f": "f64", "v": "void"}.get((returns or {}).get(entry)) or generic_ret(blocks)
        if r != sig.ret:
            sig.ret = r
            ft = FunctionTranslator(dol, entry, sig, facts, body, comments=comments)
            blocks = ft.build()
    fill_auto_args(ft, blocks)
    types = var_types(ft, blocks)
    fn = passes.Fn(blocks, types, (facts.stack_lo, facts.ram[1]))
    params = {n for n, _, _, _ in sig.params}
    entry_vals = {n: passes.zero(t) for n, t in types.items()
                  if n not in params and n not in ft.temps and n not in ("g_", "sp_")}
    if optimise:
        passes.optimise(fn, entry_vals, ft.notes, returns=sig.returns)
    else:
        passes.remove_unreachable(fn)
        passes.choose_calls(fn, ft.notes, sig.returns)
    src = emit.function_text(ft, fn, structured)
    if len(src) > 58000:                # DP's work.c cap is 60 KB: the lean call form, then compact
        src = emit.function_text(ft, fn, structured, lean=True)
        if len(src) > 58000:
            src = emit.compact(src)
        if len(src) > 58000:
            src = emit.compact(emit.function_text(ft, fn, structured, dense=True))
    return src, ft
