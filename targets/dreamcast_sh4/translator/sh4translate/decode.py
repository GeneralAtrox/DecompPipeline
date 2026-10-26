"""SH-4 (SH7750) instruction decoder, written from the Renesas SH-4 software manual's encoding tables.

decode(word, pc) -> Insn. Every 16-bit word decodes to exactly one Insn; words that are no SH-4 instruction
decode to op "illegal". Immediates are sign-extended and displacements scaled, so the emitter never re-derives
them; PC-relative operands carry their effective address in `ea` (computed from the instruction's own address,
as Ghidra's SLEIGH does, also inside a delay slot).

The SH-4 is the SH-2 instruction set plus: the FPU (opcode group 0xF, and lds/sts with FPUL and FPSCR), cache and
prefetch operations (pref, ocbi, ocbp, ocbwb, movca.l), the dynamic shifts shad/shld, clrs/sets, and the
privileged control registers (ssr, spc, sgr, dbr, the banked r0..r7, ldtlb). An FPU instruction's meaning depends
on FPSCR.SZ and FPSCR.PR at run time; the decoder only names the encoding (the translator tracks the modes).
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Insn:
    addr: int
    word: int
    op: str          # e.g. "add", "mov.l@disp_ld", "fmov.s@+_ld", see decode() below
    n: int = 0       # Rn / FRn field (bits 8..11), or the FVn / DRn number already scaled to its first register
    m: int = 0       # Rm / FRm field (bits 4..7), or FVm scaled
    imm: int = 0     # immediate / scaled displacement (signed where the manual says so); the register index of
                     # stc/ldc/sts/lds (CTRL / SYSR below)
    ea: int = 0      # PC-relative effective address (mov.w/mov.l @(disp,PC), mova, branch targets)

    @property
    def delayed(self):
        return self.op in DELAYED

    @property
    def text(self):
        return disasm(self)


DELAYED = {"bra", "bsr", "braf", "bsrf", "jmp", "jsr", "rts", "rte", "bt/s", "bf/s"}
# Instructions that may not sit in a delay slot (the manual's "slot illegal instruction" list).
SLOT_ILLEGAL = DELAYED | {"bt", "bf", "trapa"}
# Privileged or system instructions DP's runtime has no model for (a function using one is not judged).
SYSTEM = {"rte", "sleep", "trapa", "ldtlb", "stc.sys", "stc.l.sys", "ldc.sys", "ldc.l.sys"}

# stc/ldc register index (imm): the m field of stc Rm,Rn / ldc Rm,Rn
CTRL = {0: "sr", 1: "gbr", 2: "vbr", 3: "ssr", 4: "spc"}
# sts/lds register index (imm): the m field of sts / lds
SYSR = {0: "mach", 1: "macl", 2: "pr", 5: "fpul", 6: "fpscr"}


def _s8(v):
    return v - 0x100 if v & 0x80 else v


def _s12(v):
    return v - 0x1000 if v & 0x800 else v


def _group0(I, w, n, m, lo):
    if lo == 0x2:
        if m in CTRL:
            return I("stc", n=n, imm=m)
        if m & 8:
            return I("stc.sys", n=n, imm=0x10 | (m & 7))   # stc Rm_BANK,Rn
        return I("illegal")
    if lo == 0x3:
        return {0: I("bsrf", m=n), 2: I("braf", m=n), 8: I("pref", n=n), 9: I("ocbi", n=n), 0xA: I("ocbp", n=n),
                0xB: I("ocbwb", n=n), 0xC: I("movca.l", n=n)}.get(m, I("illegal"))
    if lo in (0x4, 0x5, 0x6):
        return I("mov." + "bwl"[lo - 4] + "@r0_st", n=n, m=m)
    if lo == 0x7:
        return I("mul.l", n=n, m=m)
    if lo == 0x8 and n == 0:
        return {0: I("clrt"), 1: I("sett"), 2: I("clrmac"), 3: I("ldtlb"), 4: I("clrs"),
                5: I("sets")}.get(m, I("illegal"))
    if lo == 0x9:
        if n == 0 and m == 0:
            return I("nop")
        if n == 0 and m == 1:
            return I("div0u")
        if m == 2:
            return I("movt", n=n)
        return I("illegal")
    if lo == 0xA:
        if m in SYSR:
            return I("sts", n=n, imm=m)
        if m in (3, 0xF):
            return I("stc.sys", n=n, imm=m)         # stc sgr,Rn / stc dbr,Rn
        return I("illegal")
    if lo == 0xB and n == 0:
        return {0: I("rts"), 1: I("sleep"), 2: I("rte")}.get(m, I("illegal"))
    if lo in (0xC, 0xD, 0xE):
        return I("mov." + "bwl"[lo - 0xC] + "@r0_ld", n=n, m=m)
    if lo == 0xF:
        return I("mac.l", n=n, m=m)
    return I("illegal")


# 0100nnnn xxxxxxxx: the low byte names the operation ("n": the register is Rn; "m": it is Rm, in bits 8..11)
_G4 = {
    0x00: ("shll", "n"), 0x01: ("shlr", "n"), 0x04: ("rotl", "n"), 0x05: ("rotr", "n"),
    0x08: ("shll2", "n"), 0x09: ("shlr2", "n"), 0x0B: ("jsr", "m"),
    0x10: ("dt", "n"), 0x11: ("cmp/pz", "n"), 0x15: ("cmp/pl", "n"), 0x18: ("shll8", "n"), 0x19: ("shlr8", "n"),
    0x1B: ("tas.b", "n"),
    0x20: ("shal", "n"), 0x21: ("shar", "n"), 0x24: ("rotcl", "n"), 0x25: ("rotcr", "n"), 0x28: ("shll16", "n"),
    0x29: ("shlr16", "n"), 0x2B: ("jmp", "m"),
}


def _group4(I, w, n, m, lo):
    if lo == 0xF:
        return I("mac.w", n=n, m=m)
    if lo == 0xC:
        return I("shad", n=n, m=m)
    if lo == 0xD:
        return I("shld", n=n, m=m)
    k = w & 0xFF
    if k in _G4:
        op, which = _G4[k]
        return I(op, n=n) if which == "n" else I(op, m=n)
    sub = k >> 4
    bank = 0x10 | (sub & 7)
    if lo == 0x2:
        if sub in SYSR:
            return I("sts.l", n=n, imm=sub)
        if sub in (3, 0xF):
            return I("stc.l.sys", n=n, imm=sub)       # stc.l sgr / dbr,@-Rn
    if lo == 0x6:
        if sub in SYSR:
            return I("lds.l", m=n, imm=sub)
        if sub == 0xF:
            return I("ldc.l.sys", m=n, imm=sub)       # ldc.l @Rm+,dbr
    if lo == 0xA:
        if sub in SYSR:
            return I("lds", m=n, imm=sub)
        if sub == 0xF:
            return I("ldc.sys", m=n, imm=sub)         # ldc Rm,dbr
    if lo == 0x3:
        if sub in CTRL:
            return I("stc.l", n=n, imm=sub)
        if sub & 8:
            return I("stc.l.sys", n=n, imm=bank)      # stc.l Rm_BANK,@-Rn
    if lo == 0x7:
        if sub in CTRL:
            return I("ldc.l", m=n, imm=sub)
        if sub & 8:
            return I("ldc.l.sys", m=n, imm=bank)      # ldc.l @Rm+,Rn_BANK
    if lo == 0xE:
        if sub in CTRL:
            return I("ldc", m=n, imm=sub)
        if sub & 8:
            return I("ldc.sys", m=n, imm=bank)        # ldc Rm,Rn_BANK
    return I("illegal")


def _groupF(I, w, n, m, lo):
    ops = {0x0: "fadd", 0x1: "fsub", 0x2: "fmul", 0x3: "fdiv", 0x4: "fcmp/eq", 0x5: "fcmp/gt",
           0x6: "fmov.s@r0_ld", 0x7: "fmov.s@r0_st", 0x8: "fmov.s@_ld", 0x9: "fmov.s@+_ld", 0xA: "fmov.s@_st",
           0xB: "fmov.s@-_st", 0xC: "fmov", 0xE: "fmac"}
    if lo in ops:
        return I(ops[lo], n=n, m=m)
    if lo != 0xD:
        return I("illegal")
    one = {0x0: "fsts", 0x1: "flds", 0x2: "float", 0x3: "ftrc", 0x4: "fneg", 0x5: "fabs", 0x6: "fsqrt",
           0x7: "fsrra", 0x8: "fldi0", 0x9: "fldi1"}
    if m in one:
        return I(one[m], n=n)
    if m == 0xA and not n & 1:
        return I("fcnvsd", n=n)                    # fcnvsd FPUL,DRn
    if m == 0xB and not n & 1:
        return I("fcnvds", n=n)                    # fcnvds DRm,FPUL (the register is in bits 9..11)
    if m == 0xE:
        return I("fipr", n=n & 0xC, m=(n & 3) * 4)  # 1111nnmm11101101: fipr FVm,FVn
    if m == 0xF:
        if not n & 1:
            return I("fsca", n=n)                  # 1111nnn011111101: fsca FPUL,DRn
        if n & 3 == 1:
            return I("ftrv", n=n & 0xC)            # 1111nn0111111101: ftrv XMTRX,FVn
        if n == 0x3:
            return I("fschg")
        if n == 0xB:
            return I("frchg")
        if n == 0x7:
            return I("fpchg")
    return I("illegal")


def decode(w, pc):
    hi, n, m, lo = w >> 12, (w >> 8) & 15, (w >> 4) & 15, w & 15
    i8 = w & 0xFF

    def I(op, **k):
        return Insn(pc, w, op, **k)

    if hi == 0x0:
        return _group0(I, w, n, m, lo)
    if hi == 0x1:
        return I("mov.l@disp_st", n=n, m=m, imm=lo * 4)
    if hi == 0x2:
        return {0: I("mov.b@_st", n=n, m=m), 1: I("mov.w@_st", n=n, m=m), 2: I("mov.l@_st", n=n, m=m),
                4: I("mov.b@-_st", n=n, m=m), 5: I("mov.w@-_st", n=n, m=m), 6: I("mov.l@-_st", n=n, m=m),
                7: I("div0s", n=n, m=m), 8: I("tst", n=n, m=m), 9: I("and", n=n, m=m), 10: I("xor", n=n, m=m),
                11: I("or", n=n, m=m), 12: I("cmp/str", n=n, m=m), 13: I("xtrct", n=n, m=m),
                14: I("mulu.w", n=n, m=m), 15: I("muls.w", n=n, m=m)}.get(lo, I("illegal"))
    if hi == 0x3:
        return {0: I("cmp/eq", n=n, m=m), 2: I("cmp/hs", n=n, m=m), 3: I("cmp/ge", n=n, m=m), 4: I("div1", n=n, m=m),
                5: I("dmulu.l", n=n, m=m), 6: I("cmp/hi", n=n, m=m), 7: I("cmp/gt", n=n, m=m), 8: I("sub", n=n, m=m),
                10: I("subc", n=n, m=m), 11: I("subv", n=n, m=m), 12: I("add", n=n, m=m), 13: I("dmuls.l", n=n, m=m),
                14: I("addc", n=n, m=m), 15: I("addv", n=n, m=m)}.get(lo, I("illegal"))
    if hi == 0x4:
        return _group4(I, w, n, m, lo)
    if hi == 0x5:
        return I("mov.l@disp_ld", n=n, m=m, imm=lo * 4)
    if hi == 0x6:
        ops = ["mov.b@_ld", "mov.w@_ld", "mov.l@_ld", "mov", "mov.b@+_ld", "mov.w@+_ld", "mov.l@+_ld", "not",
               "swap.b", "swap.w", "negc", "neg", "extu.b", "extu.w", "exts.b", "exts.w"]
        return I(ops[lo], n=n, m=m)
    if hi == 0x7:
        return I("add#", n=n, imm=_s8(i8))
    if hi == 0x8:
        if n == 0x0:
            return I("mov.b@disp_st", m=m, imm=lo)          # mov.b R0,@(disp,Rn): Rn in the m slot here
        if n == 0x1:
            return I("mov.w@disp_st", m=m, imm=lo * 2)
        if n == 0x4:
            return I("mov.b@disp_ld", m=m, imm=lo)
        if n == 0x5:
            return I("mov.w@disp_ld", m=m, imm=lo * 2)
        if n == 0x8:
            return I("cmp/eq#", imm=_s8(i8))
        if n in (0x9, 0xB, 0xD, 0xF):
            op = {9: "bt", 0xB: "bf", 0xD: "bt/s", 0xF: "bf/s"}[n]
            return I(op, imm=_s8(i8), ea=(pc + 4 + _s8(i8) * 2) & 0xFFFFFFFF)
        return I("illegal")
    if hi == 0x9:
        return I("mov.w@pc", n=n, imm=i8 * 2, ea=pc + 4 + i8 * 2)
    if hi == 0xA:
        d = _s12(w & 0xFFF)
        return I("bra", imm=d, ea=(pc + 4 + d * 2) & 0xFFFFFFFF)
    if hi == 0xB:
        d = _s12(w & 0xFFF)
        return I("bsr", imm=d, ea=(pc + 4 + d * 2) & 0xFFFFFFFF)
    if hi == 0xC:
        sub = n
        if sub in (0, 1, 2):
            return I("mov." + "bwl"[sub] + "@gbr_st", imm=i8 << sub)
        if sub == 3:
            return I("trapa", imm=i8)
        if sub in (4, 5, 6):
            return I("mov." + "bwl"[sub - 4] + "@gbr_ld", imm=i8 << (sub - 4))
        if sub == 7:
            return I("mova", imm=i8 * 4, ea=(pc & ~3) + 4 + i8 * 4)
        return I(["tst#", "and#", "xor#", "or#", "tst.b@gbr", "and.b@gbr", "xor.b@gbr", "or.b@gbr"][sub - 8], imm=i8)
    if hi == 0xD:
        return I("mov.l@pc", n=n, imm=i8 * 4, ea=(pc & ~3) + 4 + i8 * 4)
    if hi == 0xE:
        return I("mov#", n=n, imm=_s8(i8))
    return _groupF(I, w, n, m, lo)


def _sysname(i):
    """The register of a privileged stc/ldc (stc.sys, ldc.sys and their .l forms)."""
    k = i.imm
    return f"r{k & 7}_bank" if k & 0x10 else {3: "sgr", 0xF: "dbr"}[k]


def disasm(i):
    """A GNU-style disassembly line (for comments and listings)."""
    o, n, m = i.op, f"r{i.n}", f"r{i.m}"
    fn, fm = f"fr{i.n}", f"fr{i.m}"
    if o in ("nop", "rts", "rte", "sleep", "clrt", "sett", "clrmac", "div0u", "clrs", "sets", "ldtlb", "fschg",
             "frchg", "fpchg"):
        return o
    if o in ("bt", "bf", "bt/s", "bf/s", "bra", "bsr"):
        return f"{o} 0x{i.ea:08x}"
    if o in ("braf", "bsrf"):
        return f"{o} {m}"
    if o in ("jmp", "jsr"):
        return f"{o} @{m}"
    if o in ("pref", "ocbi", "ocbp", "ocbwb"):
        return f"{o} @{n}"
    if o == "movca.l":
        return f"movca.l r0,@{n}"
    if o == "stc":
        return f"stc {CTRL[i.imm]},{n}"
    if o == "stc.l":
        return f"stc.l {CTRL[i.imm]},@-{n}"
    if o == "ldc":
        return f"ldc {m},{CTRL[i.imm]}"
    if o == "ldc.l":
        return f"ldc.l @{m}+,{CTRL[i.imm]}"
    if o == "stc.sys":
        return f"stc {_sysname(i)},{n}"
    if o == "stc.l.sys":
        return f"stc.l {_sysname(i)},@-{n}"
    if o == "ldc.sys":
        return f"ldc {m},{_sysname(i)}"
    if o == "ldc.l.sys":
        return f"ldc.l @{m}+,{_sysname(i)}"
    if o == "sts":
        return f"sts {SYSR[i.imm]},{n}"
    if o == "sts.l":
        return f"sts.l {SYSR[i.imm]},@-{n}"
    if o == "lds":
        return f"lds {m},{SYSR[i.imm]}"
    if o == "lds.l":
        return f"lds.l @{m}+,{SYSR[i.imm]}"
    if o == "add#":
        return f"add #{i.imm},{n}"
    if o == "mov#":
        return f"mov #{i.imm},{n}"
    if o == "cmp/eq#":
        return f"cmp/eq #{i.imm},r0"
    if o in ("tst#", "and#", "xor#", "or#"):
        return f"{o[:-1]} #0x{i.imm:02x},r0"
    if o.endswith("@gbr") and o[0] in "taxo":
        return f"{o[:-4]} #0x{i.imm:02x},@(r0,gbr)"
    if o == "trapa":
        return f"trapa #0x{i.imm:02x}"
    if o == "mova":
        return f"mova @(0x{i.ea:08x}),r0"
    if o == "mov.w@pc":
        return f"mov.w @(0x{i.ea:08x}),{n}"
    if o == "mov.l@pc":
        return f"mov.l @(0x{i.ea:08x}),{n}"
    if o == "fmov.d" or o.startswith("fmov.d@"):    # FPSCR.SZ=1 (the translator renames fmov for that mode)
        def dx(k):
            return f"dr{k}" if k % 2 == 0 else f"xd{k - 1}"
        if o == "fmov.d":
            return f"fmov {dx(i.m)},{dx(i.n)}"
        mode = o[6:]
        return {"@r0_ld": f"fmov @(r0,{m}),{dx(i.n)}", "@r0_st": f"fmov {dx(i.m)},@(r0,{n})",
                "@_ld": f"fmov @{m},{dx(i.n)}", "@+_ld": f"fmov @{m}+,{dx(i.n)}", "@_st": f"fmov {dx(i.m)},@{n}",
                "@-_st": f"fmov {dx(i.m)},@-{n}"}[mode]
    if o.endswith(".dr"):     # FPSCR.PR=1 (the translator renames the double forms for that mode)
        o = o[:-3]
        if o in ("fsts", "float", "fcnvsd"):
            return f"{o} fpul,dr{i.n}"
        if o in ("ftrc", "fcnvds"):
            return f"{o} dr{i.n},fpul"
        return f"{o} dr{i.n}" if o == "fsqrt" else f"{o} dr{i.m},dr{i.n}"
    if o.startswith("fmov.s@"):
        mode = o[6:]
        return {"@r0_ld": f"fmov.s @(r0,{m}),{fn}", "@r0_st": f"fmov.s {fm},@(r0,{n})", "@_ld": f"fmov.s @{m},{fn}",
                "@+_ld": f"fmov.s @{m}+,{fn}", "@_st": f"fmov.s {fm},@{n}", "@-_st": f"fmov.s {fm},@-{n}"}[mode]
    if o.startswith("mov.") and "@" in o:
        sz, mode = o[:5], o[5:]
        return {"@r0_st": f"{sz} {m},@(r0,{n})", "@r0_ld": f"{sz} @(r0,{m}),{n}",
                "@disp_st": (f"{sz} r0,@({i.imm},{m})" if o[4] in "bw" else f"{sz} {m},@({i.imm},{n})"),
                "@disp_ld": f"{sz} @({i.imm},{m}),r0" if o[4] in "bw" else f"{sz} @({i.imm},{m}),{n}",
                "@_st": f"{sz} {m},@{n}", "@-_st": f"{sz} {m},@-{n}", "@_ld": f"{sz} @{m},{n}",
                "@+_ld": f"{sz} @{m}+,{n}", "@gbr_st": f"{sz} r0,@({i.imm},gbr)",
                "@gbr_ld": f"{sz} @({i.imm},gbr),r0"}[mode]
    if o in ("shll", "shlr", "shll2", "shlr2", "shll8", "shlr8", "shll16", "shlr16", "shal", "shar", "rotl", "rotr",
             "rotcl", "rotcr", "dt", "cmp/pz", "cmp/pl", "movt"):
        return f"{o} {n}"
    if o == "tas.b":
        return f"tas.b @{n}"
    if o in ("mac.l", "mac.w"):
        return f"{o} @{m}+,@{n}+"
    if o in ("fadd", "fsub", "fmul", "fdiv", "fcmp/eq", "fcmp/gt", "fmov"):
        return f"{o} {fm},{fn}"
    if o == "fmac":
        return f"fmac fr0,{fm},{fn}"
    if o in ("fneg", "fabs", "fsqrt", "fsrra", "fldi0", "fldi1"):
        return f"{o} {fn}"
    if o in ("fsts", "float"):
        return f"{o} fpul,{fn}"
    if o in ("flds", "ftrc"):
        return f"{o} {fn},fpul"
    if o == "fcnvsd":
        return f"fcnvsd fpul,dr{i.n}"
    if o == "fcnvds":
        return f"fcnvds dr{i.n},fpul"
    if o == "fsca":
        return f"fsca fpul,dr{i.n}"
    if o == "fipr":
        return f"fipr fv{i.m},fv{i.n}"
    if o == "ftrv":
        return f"ftrv xmtrx,fv{i.n}"
    if o == "illegal":
        return f".word 0x{i.word:04x}"
    return f"{o} {m},{n}"
