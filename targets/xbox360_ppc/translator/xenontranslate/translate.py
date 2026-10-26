"""Xenon (Xbox 360) PowerPC machine code -> C, one function at a time, in DecompPipeline's Xbox 360 raw-C form
(targets/xbox360_ppc/runtime/tc_rt.h on DP's xbox360-ppc branch).

Method: walk the function's reachable instructions from its entry inside its body, cut them into blocks at branch
targets, and lift every instruction into typed statements over C locals that stand for the registers (r0..r31 u64,
f0..f31 f64, v0..v127 the 128-bit vector struct xv, one u32 0/1 flag per CR bit, XER's CA/SO/OV, CTR, LR, ...). The
passes (passes.py) then propagate constants and copies, drop what nothing observes and fold single-use values into
their use; emit.py prints C and lays the flow out as loops and ifs (structure.py / cfgstruct.py).

The guest model is DecompPipeline's Xbox 360 judge model (target.json "abi", TcOracle, DESIGN.md):
- general registers are 64-bit and the title runs in 64-bit mode: 32-bit operations (the w forms, rlwinm, lwz, ...)
  take the low word and fill the upper word as the architecture says; CR0 of a dotted operation and the carry come
  from the 64-bit result; guest addresses are the low 32 bits of the effective address (rd8..rdf64, wr8..wrf64);
- r1 starts at the ABI's entry SP, LR at the return sentinel, r13 at the project's fixed value; every other register
  is 0;
- a call (bl, bctrl, a branch that leaves the body: a tail call) is tc_call / tc_callf with r3..r10, f1..f13 and the
  eight stack doublewords at r1+0x50 in TcArgs; afterwards r3 (or f1) is the result and r0, r4..r12, f0, f2..f13 and
  CTR are 0, as the oracle's mocked callee leaves them; CR, XER, the vector registers, r14..r31 and f14..f31 are kept;
- the compiler's register save/restore helpers (__savegprlr_N, __restgprlr_N and the FPR / VMX families: the
  catalog's helper_ranges) run inline, as in the oracle; a branch (not a call) to a helper returns afterwards;
- a computed branch through a table inside the body (bctr after a bounded index and a table load) is a switch;
- single-precision results are (f64)(f32)(double operation); stfs stores with the architecture's conversion (wrf32d);
  the multiply-adds use DP's helpers (the build's fma switch); the vector unit is vmx.py.

Nothing here is specific to one game or address: everything comes from the machine code, the signature and the
target facts.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import expr as X
from .decode import Insn, VECTOR_OPS, disasm
from .expr import Call, Const, Var, ZERO, FZERO, u, s, q, load
from .ir import Assign, Block, CallS, Comment, Goto, If, Ret, Store, Switch


class TranslateError(Exception):
    pass


@dataclass
class Facts:
    entry_sp: int = 0x700FE000
    sentinel: int = 0x700FF000
    stack_lo: int = 0x70000000
    stack_hi: int = 0x70100000
    fixed: dict = field(default_factory=dict)       # "r13" -> value
    helpers: list = field(default_factory=list)     # [(family, lo, hi_exclusive)]: frame helpers
    stack_args: int = 0x50

    @classmethod
    def from_target(cls, tj, fixed, helper_ranges=()):
        a = tj.get("abi", {})
        m = tj.get("memory", {})
        st = m.get("stack", ["0x70000000", "0x70100000"])
        fx = {k.lower(): int(v, 16) for k, v in (a.get("fixed_values") or {}).items()}
        fx.update({k.lower(): int(v, 16) for k, v in (fixed or {}).items()})
        sa = a.get("stack_args", {}).get("offset", 0x50)
        return cls(int(a.get("entry_sp", "0x700fe000"), 16), int(a.get("return_sentinel", "0x700ff000"), 16),
                   int(st[0], 16), int(st[1], 16), fx, [(n, lo, hi) for n, lo, hi in helper_ranges], sa)


INT_KINDS = ("u32", "s32", "u64", "s64", "u16", "s16", "u8", "s8")


@dataclass
class Signature:
    """The C signature and where each parameter lives (DP meta.json: params[].storage r3..r10 / f1..f13 / stack)."""
    name: str
    text: str
    params: list                    # [(name, kind, storage, stack_offset)]
    ret: str = "u64"                # void / u32 / s32 / u64 / s64 / f32 / f64
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
        params = [(f"arg_r{k}", "u64", f"r{k}", -1) for k in range(3, 11)]
        params += [(f"arg_f{k}", "f64", f"f{k}", -1) for k in range(1, 14)]
        return cls(name, "", params, "u64", None, True)


CRB = ("lt", "gt", "eq", "so")
U64 = "u64"


def crname(bit):
    return f"c{bit // 4}{CRB[bit % 4]}"


def vtype(name):
    if re.fullmatch(r"f\d+", name):
        return "f64"
    if re.fullmatch(r"v\d+", name):
        return "xv"
    if re.fullmatch(r"c\d(lt|gt|eq|so)", name) or name in ("ca", "so", "ov"):
        return "flag"
    if name in ("fpscr", "xbc", "vrsave"):
        return "u32"
    return "u64"


def R(n):
    return Var(f"r{n}", U64)


def F(n):
    return Var(f"f{n}", "f64")


def CR(bit):
    return Var(crname(bit), "flag")


def CA():
    return Var("ca", "flag")


def SO():
    return Var("so", "flag")


def OV():
    return Var("ov", "flag")


def RA0(n):
    return q(0) if n == 0 else R(n)


def lo32(e):
    return X.cast("u32", e)


def s32(e):
    return X.cast("s32", e)


def s64(e):
    return X.cast("s64", e)


def zext(e):
    """A 32-bit value as a register: the upper word 0."""
    return X.cast(U64, X.cast("u32", e) if X.INT_BITS.get(e.ty) != 32 or e.ty in X.SIGNED else e)


def sext(e):
    """A 32-bit value as a register, sign-extended."""
    return X.cast(U64, X.cast("s32", e))


def mask32(mb, me):
    a = 0xFFFFFFFF >> mb
    b = (0xFFFFFFFF << (31 - me)) & 0xFFFFFFFF
    return (a & b) if mb <= me else (a | b)


M64 = 0xFFFFFFFFFFFFFFFF


def mask64(mb, me):
    a = M64 >> mb
    b = (M64 << (63 - me)) & M64
    return (a & b) if mb <= me else (a | b)


def _rotmask(x, sh, m, n, c):
    """((x rotl sh) & m) in n bits, written with plain shifts where the mask allows (c: the constant maker)."""
    full = (1 << n) - 1
    sh %= n
    if sh == 0:
        return X.band(x, c(m))
    low = (1 << sh) - 1
    if m & low == 0:                            # only x << sh reaches the mask
        e = X.shl(x, sh)
        return e if (m | low) == full else X.band(e, c(m))
    if m & ~low & full == 0:                    # only x >> (n - sh) does
        e = X.shr(x, n - sh)
        return e if m == low else X.band(e, c(m))
    return X.band(X.bor(X.shl(x, sh), X.shr(x, n - sh)), c(m))


def rotmask(x, sh, m):
    return _rotmask(x, sh, m, 32, u)


def rotmask64(x, sh, m):
    return _rotmask(x, sh, m, 64, q)


def fbits(e):
    return Call("px_f2b", [e], U64)


def bitsf(e):
    return Call("px_b2f", [e], "f64")


GPR_D = {"lwz", "lwzu", "lbz", "lbzu", "lhz", "lhzu", "lha", "lhau", "lwzx", "lwzux", "lbzx", "lbzux", "lhzx",
         "lhzux", "lhax", "lhaux", "lwbrx", "lhbrx", "lwarx", "ld", "ldu", "ldx", "ldux", "lwa", "lwax", "lwaux",
         "ldarx", "addi", "addis", "addic", "addic.", "subfic", "mulli", "add", "addc", "adde", "addme", "addze",
         "divw", "divwu", "mullw", "neg", "subf", "subfc", "subfe", "subfme", "subfze", "mulhw", "mulhwu", "mulld",
         "mulhd", "mulhdu", "divd", "divdu", "mfspr", "mftb", "mfcr", "mfocrf", "mfmsr"}
GPR_A = {"ori", "oris", "xori", "xoris", "andi.", "andis.", "rlwimi", "rlwinm", "rlwnm", "slw", "cntlzw", "and",
         "andc", "nor", "eqv", "xor", "orc", "or", "nand", "srw", "sraw", "srawi", "extsh", "extsb", "extsw", "sld",
         "srd", "srad", "sradi", "cntlzd", "rldicl", "rldicr", "rldic", "rldimi", "rldcl", "rldcr"}


def gpr_writes(i: Insn):
    o = i.op
    out = set()
    if o in GPR_D:
        out.add(i.rd)
    if o in GPR_A:
        out.add(i.ra)
    if o.startswith(("l", "st")) and (o.endswith("u") or o.endswith("ux")) and o not in ("lmw", "stmw"):
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
        self.vhelpers = set()                 # xv_ helpers (vmx.py)
        self.notes = []                       # what the translation could not do exactly
        self.extra = []                       # synthetic blocks, laid out after the real ones
        self.nsyn = 0

    # ------------------------------------------------------------------------------------------------- body
    def in_body(self, a):
        return any(lo <= a < hi for lo, hi in self.body)

    def insn(self, a):
        if not self.dol.has(a, 4):
            raise TranslateError(f"code outside the image at {a:08x}")
        return self.dol.insn(a)

    def helper_family(self, t):
        for name, lo, hi in self.facts.helpers:
            if lo <= t < hi:
                return name
        return None

    def is_helper(self, t):
        """A frame helper's instructions from t to its blr (the catalog's helper_ranges), or None."""
        if self.in_body(t) or self.helper_family(t) is None:
            return None
        out = []
        a = t
        for _ in range(300):
            if not self.dol.has(a, 4):
                return None
            i = self.dol.insn(a)
            if i.op == "bclr" and (i.bo & 0x14) == 0x14 and not i.lk:
                return out
            if i.op in ("b", "bc", "bcctr", "bclr", "illegal"):
                return None
            out.append(i)
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
        if o in ("rfid", "hrfid") or (o in ("tw", "twi", "td", "tdi") and i.to == 31):
            return [], True
        return [a + 4], False

    # ------------------------------------------------------------------------------------------- jump tables
    def trace_back(self, a, n=48):
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

    def jump_table(self, i):
        """The targets of a bctr through a table, by running the straight-line code before it for every index the
        bounding compare allows (cmplwi rI,N then a branch away when above): each index's CTR, all in the body."""
        tr = self.trace_back(i.addr)
        k = next((j for j, x in enumerate(tr) if x.op == "cmpli" and x.l == 0), None)
        if k is None:
            return None
        cmpi = tr[k]
        n = cmpi.uimm + 1
        if n > 4096:
            return None
        code = list(reversed(tr))            # oldest first
        at = len(tr) - 1 - k                 # the compare's index in code
        out = []
        for idx in range(n):
            ctr = self.mini_eval(code, at, cmpi.ra, idx)
            if ctr is None or ctr & 3 or not self.in_body(ctr):
                return None
            out.append(ctr)
        return out

    def mini_eval(self, code, at, reg, idx):
        """CTR after code (straight-line, oldest first) with GPR reg = idx from code[at] on; None when it does not
        come out a constant. Loads read the image."""
        rv = {}
        ctr = None
        img = self.dol

        def g(r):
            return rv.get(r)

        def ea(ra, off):
            b = 0 if ra == 0 else g(ra)
            return None if b is None or off is None else (b + off) & 0xFFFFFFFF

        for j, x in enumerate(code):
            if j == at:
                rv[reg] = idx
            o = x.op
            if x.op in ("bc", "cmpi", "cmpli", "cmp", "cmpl") or o.startswith(("st", "dcb")):
                continue
            w = gpr_writes(x)
            val = None
            try:
                if o == "addi":
                    val = ea(x.ra, x.simm)
                elif o == "addis":
                    val = ea(x.ra, x.simm << 16)
                elif o in ("ori", "oris", "xori"):
                    b = g(x.rs)
                    imm = x.uimm << 16 if o == "oris" else x.uimm
                    val = None if b is None else (b ^ imm if o == "xori" else b | imm)
                elif o == "or" and x.rs == x.rb:
                    val = g(x.rs)
                elif o == "add":
                    a_, b_ = g(x.ra), g(x.rb)
                    val = None if a_ is None or b_ is None else (a_ + b_) & M64
                elif o == "rlwinm":
                    b = g(x.rs)
                    if b is not None:
                        v = b & 0xFFFFFFFF
                        sh = x.sh
                        r = ((v << sh) | (v >> (32 - sh))) & 0xFFFFFFFF if sh else v
                        val = r & mask32(x.mb, x.me) if x.mb <= x.me else None
                elif o == "rldicl":
                    b = g(x.rs)
                    if b is not None:
                        sh = x.sh
                        r = ((b << sh) | (b >> (64 - sh))) & M64 if sh else b
                        val = r & (M64 >> x.mb)
                elif o == "extsw":
                    b = g(x.rs)
                    val = None if b is None else (b & 0xFFFFFFFF) - ((b & 0x80000000) << 1) & M64
                elif o in ("lwzx", "lbzx", "lhzx", "lhax", "lwz", "lbz", "lhz", "lha"):
                    addr = ea(x.ra, g(x.rb)) if o.endswith("x") else ea(x.ra, x.d)
                    if addr is not None:
                        if o.startswith("lw"):
                            val = img.u32(addr)
                        elif o.startswith("lb"):
                            val = img.u8(addr)
                        else:
                            val = img.u16(addr)
                            if o.startswith("lha") and val & 0x8000:
                                val = (val - 0x10000) & M64
                elif o == "mtspr" and x.spr == 9:
                    ctr = g(x.rd)
                    continue
            except Exception:  # noqa: BLE001  (a read outside the image: no table)
                return None
            for r in w:
                if val is None:
                    rv.pop(r, None)
                else:
                    rv[r] = val & M64
        return ctr

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
        if r in ("u64", "s64", "u32", "s32"):
            return X.cast(r, R(3))
        if r == "f64":
            return F(1)
        if r == "f32":
            return X.cast("f32", F(1))
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
                        args.append(("s", k, self.stack_arg(k)))
        out = [CallS(target, args, addr, auto=auto)]
        out.append(Clobber("r0", q(0)))
        for k in range(4, 13):
            out.append(Clobber(f"r{k}", q(0)))
        out.append(Clobber("f0", FZERO))
        for k in range(2, 14):
            out.append(Clobber(f"f{k}", FZERO))
        out.append(Clobber("ctr", q(0)))
        if not tail:
            out.append(Assign("lr", q(addr + 4)))
        return out

    def stack_arg(self, k):
        return load("rd64", lo32(X.add(R(1), q(self.facts.stack_args + 8 * k))))

    def tail(self, target, addr):
        return self.call(target, addr, tail=True), Ret(self.ret_expr())

    # ------------------------------------------------------------------------------------------------ build
    def build(self):
        self.explore()
        addrs = sorted(self.reach)
        entry = Block("L_entry")
        st = entry.stmts
        st.append(Assign("r1", q(self.facts.entry_sp)))
        for r in ("r2", "r13"):
            if r in self.facts.fixed:
                st.append(Assign(r, q(self.facts.fixed[r])))
        st.append(Assign("lr", q(self.facts.sentinel)))
        for name, kind, stg, off in self.sig.params:
            pv = Var(name, kind)
            if re.fullmatch(r"r([3-9]|10)", stg):
                st.append(Assign(stg, X.cast(U64, pv)))
            elif re.fullmatch(r"f([1-9]|1[0-3])", stg):
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
            st2, t = self.tail(q(a), a)
            cur.stmts += st2
            cur.term = t

    def fall(self, i):
        a = i.addr + 4
        if self.in_body(a):
            return self.label(a)
        st, t = self.tail(q(a), a)
        return self.synth(st, t, f"{a:08x}_fall")

    def bc_cond(self, i, stmts):
        bo, bi = i.bo, i.bi
        c = Const(1, "bool")
        if not bo & 4:
            stmts.append(Assign("ctr", X.sub(Var("ctr", U64), q(1))))
            c = X.compare("==" if bo & 2 else "!=", Var("ctr", U64), q(0))
        if not bo & 0x10:
            b = CR(bi) if bo & 8 else X.lnot(CR(bi))
            c = X.logic("&&", c, X.truthy(b)) if not isinstance(c, Const) else X.truthy(b)
        return c

    def lift_control(self, i: Insn, cur: Block):
        """Lift i into cur; a terminator when i ends the block."""
        o, a = i.op, i.addr
        st = cur.stmts
        if o == "b":
            h = self.is_helper(i.target)
            if h is not None:
                for x in h:
                    st += self.lift(x)
                if i.lk:
                    return None
                return Ret(self.ret_expr())   # a branch to a helper: its blr returns from this function
            if i.lk:
                if i.target == a + 4:
                    st.append(Assign("lr", q(a + 4)))
                    return None
                if self.in_body(i.target) and i.target != self.entry:     # (to the entry: recursion, a call)
                    raise TranslateError(f"bl into its own body at {a:08x}")
                st += self.call(q(i.target), a)
                return None
            if self.in_body(i.target):
                return Goto(self.label(i.target))
            s2, t = self.tail(q(i.target), a)
            st += s2
            return t
        if o == "bc":
            always = (i.bo & 0x14) == 0x14
            if i.lk:
                c = self.bc_cond(i, st)
                st.append(Assign("lr", q(a + 4)))
                if always:
                    st += self.call(q(i.target), a)
                    return Goto(self.fall(i))
                nxt = self.fall(i)
                lab = self.synth(self.call(q(i.target), a), Goto(nxt), f"{a:08x}_call")
                return If(c, lab, nxt)
            c = self.bc_cond(i, st)
            if self.in_body(i.target):
                yes = self.label(i.target)
            else:
                s2, t = self.tail(q(i.target), a)
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
                tgt = self.temp(U64)
                st.append(Assign(tgt, Var("lr", U64)))
                c = self.bc_cond(i, st)
                st.append(Assign("lr", q(a + 4)))
                calls = self.call(Var(tgt, U64), a)
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
            ctr = Var("ctr", U64)
            if i.lk:
                tgt = self.temp(U64)
                st.append(Assign(tgt, ctr))
                st.append(Assign("lr", q(a + 4)))
                calls = self.call(Var(tgt, U64), a)
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
        if o in ("rfid", "hrfid"):
            self.notes.append(f"{o} at {a:08x}: returns")
            return Ret(self.ret_expr())
        if o in ("tw", "twi", "td", "tdi") and i.to == 31:
            self.notes.append(f"trap at {a:08x}: ends the function")
            return Ret(self.ret_expr())
        st += self.lift(i)
        return None

    # ------------------------------------------------------------------------------------------------- lift
    def cr0(self, x):
        """CR0 of a dotted operation: from the 64-bit result (64-bit mode)."""
        return [Assign("c0lt", X.compare("<", s64(x), Const(0, "s64"))),
                Assign("c0gt", X.compare(">", s64(x), Const(0, "s64"))),
                Assign("c0eq", X.compare("==", x, q(0))), Assign("c0so", SO())]

    def cr1_fp(self):
        fp = Var("fpscr", "u32")
        return [Assign(f"c1{CRB[k]}", X.band(X.shr(fp, 31 - k), X.ONE)) for k in range(4)]

    def setcmp(self, crf, lt, gt, eq, so=None):
        return [Assign(f"c{crf}lt", lt), Assign(f"c{crf}gt", gt), Assign(f"c{crf}eq", eq),
                Assign(f"c{crf}so", so if so is not None else SO())]

    def sum_d(self, i):
        return X.add(RA0(i.ra), q(i.d))

    def sum_x(self, i):
        return X.add(RA0(i.ra), R(i.rb))

    def lift(self, i: Insn):
        o = i.op
        if o in VECTOR_OPS or o in ("mfvscr", "mtvscr"):
            from . import vmx
            return vmx.lift(self, i)
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
        if o in NOPS:
            return [Comment(o)] if self.comments else []
        raise TranslateError(f"unsupported instruction {o} at {i.addr:08x} ({disasm(i)})")

    # integer --------------------------------------------------------------------------------------------
    def op_addi(self, i):
        return [Assign(f"r{i.rd}", X.add(RA0(i.ra), q(i.simm)))]

    def op_addis(self, i):
        return [Assign(f"r{i.rd}", X.add(RA0(i.ra), q(i.simm << 16)))]

    def op_addic(self, i):
        a = R(i.ra)
        return [Assign("ca", X.compare("<", X.add(a, q(i.simm)), a)), Assign(f"r{i.rd}", X.add(a, q(i.simm)))]

    def op_addic_dot(self, i):
        return self.op_addic(i) + self.cr0(R(i.rd))

    def op_subfic(self, i):
        a = R(i.ra)
        return [Assign("ca", X.compare("<=", a, q(i.simm))), Assign(f"r{i.rd}", X.sub(q(i.simm), a))]

    def op_mulli(self, i):
        return [Assign(f"r{i.rd}", X.binop("*", R(i.ra), q(i.simm), U64))]

    def carry_chain(self, d, x, y, cin):
        """rD = x + y + cin with CA = the carry out of the 64-bit sum (x, y: u64; cin: flag)."""
        t1, t2 = self.temp(U64), self.temp(U64)
        v1, v2 = Var(t1, U64), Var(t2, U64)
        return [Assign(t1, X.add(x, y)), Assign(t2, X.add(v1, X.cast(U64, cin))),
                Assign("ca", X.flag_or(X.compare("<", v1, x), X.compare("<", v2, v1))), Assign(f"r{d}", v2)]

    def xo_arith(self, i):
        o, d = i.op, i.rd
        a, b = R(i.ra), R(i.rb)
        out = []
        ov = None
        if i.oe and o not in ("add", "subf", "neg", "addc", "subfc"):
            raise TranslateError(f"{o}o at {i.addr:08x}: overflow (OE) not modelled")
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
            y = b if o in ("adde", "subfe") else q(-1) if o.endswith("me") else q(0)
            out += self.carry_chain(d, x, y, CA())
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
                out += [Assign("ov", X.compare("==", a, q(1 << 63))), Assign("so", X.flag_or(SO(), OV()))]
            out.append(Assign(f"r{d}", X.neg(a)))
        elif o == "mulld":
            out.append(Assign(f"r{d}", X.binop("*", a, b, U64)))
        elif o in ("mullw", "mulhw", "mulhwu", "mulhd", "mulhdu", "divw", "divwu", "divd", "divdu"):
            out.append(Assign(f"r{d}", Call("ppc_" + o, [a, b], U64)))
        else:
            raise TranslateError(o)
        if ov is not None and i.oe:
            out = [Assign("ov", self.ov_expr(*ov)), Assign("so", X.flag_or(SO(), OV()))] + out
        if i.rc:
            out += self.cr0(R(d))
        return out

    def ov_expr(self, x, y, r):
        """Signed overflow of the 64-bit r = x + y: x and y have one sign and r the other."""
        return X.cast("u32", X.shr(X.band(X.bxor(x, r), X.bxor(y, r)), Const(63, U64)))

    def logic(self, i):
        o = i.op
        rs, d = R(i.rs), i.ra
        out = []
        if o in ("ori", "oris", "xori", "xoris", "andi.", "andis."):
            imm = i.uimm << 16 if o in ("oris", "xoris", "andis.") else i.uimm
            fn = {"o": X.bor, "x": X.bxor, "a": X.band}[o[0]]
            out.append(Assign(f"r{d}", fn(rs, q(imm))))
        elif o == "srawi":
            n = i.sh
            if n == 0:
                out += [Assign("ca", ZERO), Assign(f"r{d}", sext(rs))]
            else:
                out += [Assign("ca", X.flag_and(X.compare("<", s32(rs), s(0)),
                                                 X.compare("!=", X.band(lo32(rs), u((1 << n) - 1)), ZERO))),
                        Assign(f"r{d}", X.cast(U64, X.binop(">>", s32(rs), s(n), "s32")))]
        elif o == "sradi":
            n = i.sh
            if n == 0:
                out += [Assign("ca", ZERO), Assign(f"r{d}", rs)]
            else:
                out += [Assign("ca", X.flag_and(X.compare("<", s64(rs), Const(0, "s64")),
                                                 X.compare("!=", X.band(rs, q((1 << n) - 1)), q(0)))),
                        Assign(f"r{d}", X.cast(U64, X.binop(">>", s64(rs), Const(n, "s64"), "s64")))]
        elif o in ("cntlzw", "cntlzd"):
            out.append(Assign(f"r{d}", Call("ppc_" + o, [rs], U64)))
        elif o in ("extsb", "extsh", "extsw"):
            out.append(Assign(f"r{d}", X.cast(U64, X.cast({"extsb": "s8", "extsh": "s16", "extsw": "s32"}[o], rs))))
        else:
            rb = R(i.rb)
            if o in ("slw", "srw", "sld", "srd"):
                e = Call("ppc_" + o, [rs, rb], U64)
            elif o == "sraw":
                big = X.compare("!=", X.band(rb, q(0x20)), q(0))
                lost = X.cond(big, lo32(rs), X.band(lo32(rs), X.sub(X.shl(X.ONE, lo32(X.band(rb, q(31)))), X.ONE)))
                out.append(Assign("ca", X.flag_and(X.compare("<", s32(rs), s(0)), X.compare("!=", lost, ZERO))))
                e = Call("ppc_sraw", [rs, rb, s(0)], U64)
            elif o == "srad":
                big = X.compare("!=", X.band(rb, q(0x40)), q(0))
                lost = X.cond(big, rs, X.band(rs, X.sub(X.shl(q(1), X.band(rb, q(63))), q(1))))
                out.append(Assign("ca", X.flag_and(X.compare("<", s64(rs), Const(0, "s64")),
                                                    X.compare("!=", lost, q(0)))))
                e = Call("ppc_srad", [rs, rb, s(0)], U64)
            else:
                e = {"and": lambda: X.band(rs, rb), "or": lambda: X.bor(rs, rb) if i.rs != i.rb else rs,
                     "xor": lambda: X.bxor(rs, rb), "nand": lambda: X.bnot(X.band(rs, rb)),
                     "nor": lambda: X.bnot(X.bor(rs, rb)), "eqv": lambda: X.bnot(X.bxor(rs, rb)),
                     "andc": lambda: X.band(rs, X.bnot(rb)), "orc": lambda: X.bor(rs, X.bnot(rb))}[o]()
            out.append(Assign(f"r{d}", e))
        if i.rc:
            out += self.cr0(R(d))
        return out

    # rotates: the 32-bit ones rotate the low word doubled (ROTL32(low || low)) under a 64-bit mask
    # MASK(mb + 32, me + 32); with mb <= me the mask stays in the low word, so the result is the 32-bit rotate,
    # zero-extended. A wrapping mask (mb > me) also takes upper-word bits: DP's helper.
    def op_rlwinm(self, i):
        if i.mb <= i.me:
            e = X.cast(U64, rotmask(lo32(R(i.rs)), i.sh, mask32(i.mb, i.me)))
        else:
            e = Call("ppc_rlwinm", [R(i.rs), s(i.sh), s(i.mb), s(i.me)], U64)
        return [Assign(f"r{i.ra}", e)] + (self.cr0(R(i.ra)) if i.rc else [])

    def op_rlwnm(self, i):
        e = Call("ppc_rlwnm", [R(i.rs), R(i.rb), s(i.mb), s(i.me)], U64)
        return [Assign(f"r{i.ra}", e)] + (self.cr0(R(i.ra)) if i.rc else [])

    def op_rlwimi(self, i):
        if i.mb <= i.me:
            m = mask32(i.mb, i.me)
            e = X.bor(X.band(R(i.ra), q(~m & M64)), X.cast(U64, rotmask(lo32(R(i.rs)), i.sh, m)))
        else:
            e = Call("ppc_rlwimi", [R(i.ra), R(i.rs), s(i.sh), s(i.mb), s(i.me)], U64)
        return [Assign(f"r{i.ra}", e)] + (self.cr0(R(i.ra)) if i.rc else [])

    def op_rldicl(self, i):
        e = rotmask64(R(i.rs), i.sh, M64 >> i.mb)
        return [Assign(f"r{i.ra}", e)] + (self.cr0(R(i.ra)) if i.rc else [])

    def op_rldicr(self, i):
        e = rotmask64(R(i.rs), i.sh, (M64 << (63 - i.me)) & M64)
        return [Assign(f"r{i.ra}", e)] + (self.cr0(R(i.ra)) if i.rc else [])

    def op_rldic(self, i):
        e = rotmask64(R(i.rs), i.sh, mask64(i.mb, 63 - i.sh))
        return [Assign(f"r{i.ra}", e)] + (self.cr0(R(i.ra)) if i.rc else [])

    def op_rldimi(self, i):
        m = mask64(i.mb, 63 - i.sh)
        e = X.bor(X.band(R(i.ra), q(~m & M64)), rotmask64(R(i.rs), i.sh, m))
        return [Assign(f"r{i.ra}", e)] + (self.cr0(R(i.ra)) if i.rc else [])

    def op_rldcl(self, i):
        e = Call("ppc_rldcl", [R(i.rs), R(i.rb), s(i.mb)], U64)
        return [Assign(f"r{i.ra}", e)] + (self.cr0(R(i.ra)) if i.rc else [])

    def op_rldcr(self, i):
        e = Call("ppc_rldcr", [R(i.rs), R(i.rb), s(i.me)], U64)
        return [Assign(f"r{i.ra}", e)] + (self.cr0(R(i.ra)) if i.rc else [])

    # compares: L=0 compares the low words (cmpw, cmplw), L=1 the doublewords (cmpd, cmpld)
    def op_cmp(self, i):
        c = s64 if i.l else s32
        a, b = c(R(i.ra)), c(R(i.rb))
        eq = X.compare("==", R(i.ra), R(i.rb)) if i.l else X.compare("==", lo32(R(i.ra)), lo32(R(i.rb)))
        return self.setcmp(i.crfd, X.compare("<", a, b), X.compare(">", a, b), eq)

    def op_cmpi(self, i):
        if i.l:
            a, b = s64(R(i.ra)), Const(i.simm, "s64")
            eq = X.compare("==", R(i.ra), q(i.simm))
        else:
            a, b = s32(R(i.ra)), s(i.simm)
            eq = X.compare("==", lo32(R(i.ra)), u(i.simm))
        return self.setcmp(i.crfd, X.compare("<", a, b), X.compare(">", a, b), eq)

    def op_cmpl(self, i):
        a, b = (R(i.ra), R(i.rb)) if i.l else (lo32(R(i.ra)), lo32(R(i.rb)))
        return self.setcmp(i.crfd, X.compare("<", a, b), X.compare(">", a, b), X.compare("==", a, b))

    def op_cmpli(self, i):
        a, b = (R(i.ra), q(i.uimm)) if i.l else (lo32(R(i.ra)), u(i.uimm))
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
        return [Assign(f"r{i.rd}", X.cast(U64, e))]

    def op_mfocrf(self, i):
        self.notes.append(f"mfocrf at {i.addr:08x}: the whole CR (the other fields are undefined on hardware)")
        return self.op_mfcr(i)

    def op_mtcrf(self, i):
        out = []
        for fld in range(8):
            if i.crm & (0x80 >> fld):
                for j in range(4):
                    out.append(Assign(crname(4 * fld + j), X.band(X.shr(lo32(R(i.rs)), 31 - (4 * fld + j)), X.ONE)))
        return out

    op_mtocrf = op_mtcrf

    def op_mcrxr(self, i):
        return [Assign(f"c{i.crfd}lt", SO()), Assign(f"c{i.crfd}gt", OV()), Assign(f"c{i.crfd}eq", CA()),
                Assign(f"c{i.crfd}so", ZERO), Assign("so", ZERO), Assign("ov", ZERO), Assign("ca", ZERO)]

    def spr_var(self, spr):
        if spr == 8:
            return "lr"
        if spr == 9:
            return "ctr"
        if spr == 256:
            return "vrsave"
        if spr in (268, 284):
            return "tb"
        if spr in (269, 285):
            return "tbu"
        return f"spr{spr}"

    def op_mfspr(self, i):
        if i.spr == 1:
            e = X.bor(X.bor(X.bor(X.shl(X.cast("u32", SO()), 31), X.shl(X.cast("u32", OV()), 30)),
                            X.shl(X.cast("u32", CA()), 29)), Var("xbc", "u32"))
            return [Assign(f"r{i.rd}", X.cast(U64, e))]
        v = self.spr_var(i.spr)
        if v in ("tb", "tbu"):
            self.notes.append(f"time base read at {i.addr:08x}: a variable the function never sets (0)")
        if v == "vrsave":
            return [Assign(f"r{i.rd}", X.cast(U64, Var(v, "u32")))]
        return [Assign(f"r{i.rd}", Var(v, U64))]

    op_mftb = op_mfspr

    def op_mtspr(self, i):
        rs = R(i.rd)
        if i.spr == 1:
            w = lo32(rs)
            return [Assign("so", X.band(X.shr(w, 31), X.ONE)), Assign("ov", X.band(X.shr(w, 30), X.ONE)),
                    Assign("ca", X.band(X.shr(w, 29), X.ONE)), Assign("xbc", X.band(w, u(0x7F)))]
        v = self.spr_var(i.spr)
        if v == "vrsave":
            return [Assign(v, lo32(rs))]
        return [Assign(v, rs)]

    def op_mfmsr(self, i):
        self.notes.append(f"mfmsr at {i.addr:08x}: MSR is a variable (0 on entry)")
        return [Assign(f"r{i.rd}", Var("msr", U64))]

    def op_mtmsrd(self, i):
        self.notes.append(f"mtmsrd at {i.addr:08x}: MSR is a variable (interrupt state not modelled)")
        return [Assign("msr", R(i.rs))]

    op_mtmsr = op_mtmsrd

    def op_sc(self, i):
        raise TranslateError(f"sc at {i.addr:08x}: system call")

    def op_tw(self, i):
        self.notes.append(f"conditional trap at {i.addr:08x}: not modelled (a vector that fires it is invalid)")
        return [Comment("trap (not modelled)")]

    op_twi = op_td = op_tdi = op_tw

    # memory -----------------------------------------------------------------------------------------------
    def int_load(self, i):
        o = i.op
        upd = o.endswith("u") or o.endswith("ux")
        sm = self.sum_x(i) if "rb" in i.f else self.sum_d(i)
        out = []
        if upd:
            out.append(Assign(f"r{i.ra}", sm))
            sm = R(i.ra)
        ea = lo32(sm)
        base = o if o in ("lwarx", "ldarx", "lwbrx", "lhbrx") else o.rstrip("x").rstrip("u")
        if base in ("lwz", "lwarx"):
            e = X.cast(U64, load("rd32", ea))
        elif base in ("ld", "ldarx"):
            e = load("rd64", ea)
        elif base == "lwa":
            e = X.cast(U64, X.cast("s32", load("rd32", ea)))
        elif base == "lbz":
            e = X.cast(U64, load("rd8", ea))
        elif base == "lhz":
            e = X.cast(U64, load("rd16", ea))
        elif base == "lha":
            e = X.cast(U64, X.cast("s16", load("rd16", ea)))
        elif base == "lhbrx":
            e = X.cast(U64, Call("rd16r", [ea], "u16", load=True))
        elif base == "lwbrx":
            e = X.cast(U64, Call("rd32r", [ea], "u32", load=True))
        else:
            raise TranslateError(o)
        if base in ("lwarx", "ldarx"):
            self.notes.append(f"{base} at {i.addr:08x}: a plain load (the reservation is not modelled)")
        out.append(Assign(f"r{i.rd}", e))
        return out

    def int_store(self, i):
        o = i.op
        upd = o.endswith("u") or o.endswith("ux")
        sm = self.sum_x(i) if "rb" in i.f else self.sum_d(i)
        ea = lo32(sm)
        rs = R(i.rs if "rs" in i.f else i.rd)
        base = o if o in ("stwcx.", "stdcx.", "stwbrx", "sthbrx") else o.rstrip("x").rstrip("u")
        out = []
        if base in ("stw", "stwcx."):
            out.append(Store("wr32", ea, lo32(rs)))
        elif base in ("std", "stdcx."):
            out.append(Store("wr64", ea, rs))
        elif base == "sth":
            out.append(Store("wr16", ea, X.cast("u16", rs)))
        elif base == "stb":
            out.append(Store("wr8", ea, X.cast("u8", rs)))
        elif base == "sthbrx":
            out.append(Store("wr16r", ea, X.cast("u16", rs)))
        elif base == "stwbrx":
            out.append(Store("wr32r", ea, lo32(rs)))
        else:
            raise TranslateError(o)
        if upd:
            out.append(Assign(f"r{i.ra}", sm))
        if base in ("stwcx.", "stdcx."):
            self.notes.append(f"{base} at {i.addr:08x}: always succeeds (the reservation is not modelled)")
            out += self.setcmp(0, ZERO, ZERO, X.ONE)
        return out

    def op_lmw(self, i):
        t = self.temp("u32")
        out = [Assign(t, lo32(self.sum_d(i)))]
        for k, r in enumerate(range(i.rd, 32)):
            out.append(Assign(f"r{r}", X.cast(U64, load("rd32", X.add(Var(t, "u32"), u(4 * k))))))
        return out

    def op_stmw(self, i):
        t = self.temp("u32")
        out = [Assign(t, lo32(self.sum_d(i)))]
        for k, r in enumerate(range(i.rd, 32)):
            out.append(Store("wr32", X.add(Var(t, "u32"), u(4 * k)), lo32(R(r))))
        return out

    def zero_line(self, i, size):
        t = self.temp("u32")
        out = [Assign(t, X.band(lo32(self.sum_x(i)), u((~(size - 1)) & 0xFFFFFFFF)))]
        for k in range(size // 8):
            out.append(Store("wr64", X.add(Var(t, "u32"), u(8 * k)), q(0)))
        return out

    def op_dcbz(self, i):
        self.notes.append(f"dcbz at {i.addr:08x}: zeroes 32 bytes")
        return self.zero_line(i, 32)

    def op_dcbz128(self, i):
        return self.zero_line(i, 128)

    def fp_load(self, i):
        o = i.op
        upd = o.endswith("u") or o.endswith("ux")
        sm = self.sum_x(i) if "rb" in i.f else self.sum_d(i)
        out = []
        if upd:
            out.append(Assign(f"r{i.ra}", sm))
            sm = R(i.ra)
        ea = lo32(sm)
        if o.startswith("lfs"):
            out.append(Assign(f"f{i.rd}", X.cast("f64", load("rdf32", ea))))
        else:
            out.append(Assign(f"f{i.rd}", load("rdf64", ea)))
        return out

    def fp_store(self, i):
        o = i.op
        upd = o.endswith("u") or o.endswith("ux")
        sm = self.sum_x(i) if "rb" in i.f else self.sum_d(i)
        ea = lo32(sm)
        fs = F(i.rs if "rs" in i.f else i.rd)
        if o == "stfiwx":
            out = [Store("wr32", ea, X.cast("u32", fbits(fs)))]
        elif o.startswith("stfs"):
            out = [Store("wrf32d", ea, fs)]
        else:
            out = [Store("wrf64", ea, fs)]
        if upd:
            out.append(Assign(f"r{i.ra}", sm))
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
        elif o in ("fmadd", "fmsub", "fnmadd", "fnmsub", "fmadds", "fmsubs", "fnmadds", "fnmsubs"):
            e = Call("ppc_" + o, [a, c, b], "f64")
        elif o in ("fsqrt", "fsqrts"):
            e = Call("ppc_" + o, [b], "f64")
        elif o == "fres":
            self.notes.append(f"fres at {i.addr:08x}: exact reciprocal (the estimate is not modelled)")
            e, sgl = Call("px_fdiv", [Const(1.0, "f64"), b], "f64"), True
        elif o == "frsqrte":
            self.notes.append(f"frsqrte at {i.addr:08x}: 1/sqrt to double precision (the estimate is not modelled)")
            e = Call("px_rsqrt", [b], "f64")
        elif o == "fsel":
            e = Call("ppc_fsel", [a, c, b], "f64")
        elif o == "frsp":
            e, sgl = b, True
        elif o in ("fctiw", "fctiwz"):
            e = bitsf(Call("ppc_fctiw_fpr", [Call("ppc_" + o, [b], "s32")], U64))
        elif o in ("fctid", "fctidz"):
            e = bitsf(X.cast(U64, Call("ppc_" + o, [b], "s64")))
        elif o == "fcfid":
            e = Call("ppc_fcfid", [fbits(b)], "f64")
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
            self.notes.append(f"mffs at {i.addr:08x}: FPSCR is a variable (exception bits not modelled)")
            e = bitsf(X.binop("|", Const(0xFFF8000000000000, U64), X.cast(U64, Var("fpscr", "u32")), U64))
        elif o == "mtfsf":
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
        else:
            raise TranslateError(f"unsupported instruction {o} at {i.addr:08x}")
        if sgl:
            e = X.fround(e)
        out.append(Assign(f"f{d}", e))
        if i.rc:
            out += self.cr1_fp()
        return out


class Clobber(Assign):
    """A register a mocked callee leaves 0 (not a value of this function's own)."""
    __slots__ = ()


ARITH_XO = {"add", "addc", "adde", "addme", "addze", "divw", "divwu", "mullw", "neg", "subf", "subfc", "subfe",
            "subfme", "subfze", "mulhw", "mulhwu", "mulld", "mulhd", "mulhdu", "divd", "divdu"}
LOGIC = {"ori", "oris", "xori", "xoris", "andi.", "andis.", "slw", "srw", "sraw", "srawi", "cntlzw", "extsb", "extsh",
         "and", "or", "xor", "nand", "nor", "eqv", "andc", "orc", "extsw", "sld", "srd", "srad", "sradi", "cntlzd"}
INT_LOADS = {"lwz", "lwzu", "lwzx", "lwzux", "lbz", "lbzu", "lbzx", "lbzux", "lhz", "lhzu", "lhzx", "lhzux", "lha",
             "lhau", "lhax", "lhaux", "lwbrx", "lhbrx", "lwarx", "ld", "ldu", "ldx", "ldux", "lwa", "lwax", "lwaux",
             "ldarx"}
INT_STORES = {"stw", "stwu", "stwx", "stwux", "stb", "stbu", "stbx", "stbux", "sth", "sthu", "sthx", "sthux",
              "stwbrx", "sthbrx", "stwcx.", "std", "stdu", "stdx", "stdux", "stdcx."}
FP_LOADS = {"lfs", "lfsu", "lfsx", "lfsux", "lfd", "lfdu", "lfdx", "lfdux"}
FP_STORES = {"stfs", "stfsu", "stfsx", "stfsux", "stfd", "stfdu", "stfdx", "stfdux", "stfiwx"}
FP_A = {"fadd", "fadds", "fsub", "fsubs", "fmul", "fmuls", "fdiv", "fdivs", "fmadd", "fmadds", "fmsub", "fmsubs",
        "fnmadd", "fnmadds", "fnmsub", "fnmsubs", "fres", "frsqrte", "fsel", "fsqrt", "fsqrts"}
FP_X = {"fcmpu", "fcmpo", "frsp", "fctiw", "fctiwz", "fctid", "fctidz", "fcfid", "fneg", "fmr", "fnabs", "fabs",
        "mffs", "mtfsf", "mtfsfi", "mtfsb0", "mtfsb1", "mcrfs"}
NOPS = {"sync", "lwsync", "isync", "eieio", "dcbf", "dcbi", "dcbst", "dcbt", "dcbtst", "icbi", "tlbsync"}


# ------------------------------------------------------------------------------------------------------- driver
ARG_REGS = [f"r{k}" for k in range(3, 11)] + [f"f{k}" for k in range(1, 14)]


def all_stmts(blocks):
    for b in blocks:
        yield from b.stmts
        if isinstance(b.term, Switch):
            yield from b.term.default


def fill_auto_args(ft, blocks):
    """A call whose callee DP's meta does not list (indirect, or no meta) passes every argument register the
    function may have set: written by its own code (not a callee's clobber) or a parameter; and the stack argument
    doublewords when the function writes its outgoing argument area."""
    defined = {stg for _, _, stg, _ in ft.sig.params}
    for s_ in all_stmts(blocks[1:]):
        if not isinstance(s_, (Clobber, CallS)):
            defined |= s_.writes()
    regs = [r for r in ARG_REGS if r in defined]
    for s_ in all_stmts(blocks):
        if isinstance(s_, CallS) and s_.auto:
            s_.args = [("r", int(r[1:]) - 3, R(int(r[1:]))) if r[0] == "r" else ("f", int(r[1:]) - 1, F(int(r[1:])))
                       for r in regs]
            s_.args += [("s", k, ft.stack_arg(k)) for k in range(8)]


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
        for s_ in sts:
            for w in s_.writes():
                types.setdefault(w, ft.temps.get(w) or vtype(w))
            for e in s_.exprs():
                walk(e)
        for e in b.term.exprs():
            walk(e)
    types["g_"] = "u32"
    return types


def generic_ret(blocks):
    w = set()
    for s_ in all_stmts(blocks[1:]):
        if not isinstance(s_, Clobber):
            w |= s_.writes() if not isinstance(s_, CallS) else {"r3"}
    return "u64" if "r3" in w else "f64" if "f1" in w else "void"


def translate_function(dol, entry, sig, facts, body, comments=False, structured=True, optimise=True, returns=None):
    """(C text, FunctionTranslator) for the function at entry whose body is [(lo, hi_exclusive)]. returns: the
    whole executable's return kinds (returns.analyse), for callees DP's meta does not describe."""
    from . import emit, passes
    if returns:
        sig.returns = {**returns, **(sig.returns or {})}
    ft = FunctionTranslator(dol, entry, sig, facts, body, comments=comments)
    blocks = ft.build()
    if sig.generic:
        r = {"r": "u64", "f": "f64", "v": "void"}.get((returns or {}).get(entry)) or generic_ret(blocks)
        if r != sig.ret:
            sig.ret = r
            ft = FunctionTranslator(dol, entry, sig, facts, body, comments=comments)
            blocks = ft.build()
    fill_auto_args(ft, blocks)
    types = var_types(ft, blocks)
    fn = passes.Fn(blocks, types, (facts.stack_lo, facts.stack_hi))
    params = {n for n, _, _, _ in sig.params}
    entry_vals = {n: passes.zero(t) for n, t in types.items()
                  if n not in params and n not in ft.temps and n != "g_" and t != "xv"}
    if optimise:
        passes.optimise(fn, entry_vals, ft.notes, returns=sig.returns)
    else:
        passes.remove_unreachable(fn)
        passes.choose_calls(fn, ft.notes, sig.returns)
    src = emit.function_text(ft, fn, structured)
    if len(src) > 58000:                # DP's work.c cap is 60 KB: the short calls, then no optional whitespace
        src = emit.compact(emit.function_text(ft, fn, structured, short=True))
    return src, ft
