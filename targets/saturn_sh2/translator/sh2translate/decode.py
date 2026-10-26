"""SH-2 (SH7604) instruction decoder, written from the Hitachi SH-1/SH-2 programming manual's encoding tables.

decode(word, pc) -> Insn. Every 16-bit word decodes to exactly one Insn; words that are no SH-2 instruction
decode to op "illegal". Immediates are sign-extended and displacements scaled, so the emitter never re-derives
them; PC-relative operands carry their effective address in `ea` (computed from the instruction's own address,
as Ghidra's SLEIGH does, also inside a delay slot).
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Insn:
    addr: int
    word: int
    op: str          # e.g. "add", "mov.l@disp_ld", see OPS below
    n: int = 0       # Rn field
    m: int = 0       # Rm field
    imm: int = 0     # immediate / scaled displacement (signed where the manual says so)
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


def _s8(v):
    return v - 0x100 if v & 0x80 else v


def _s12(v):
    return v - 0x1000 if v & 0x800 else v


def decode(w, pc):
    hi, n, m, lo = w >> 12, (w >> 8) & 15, (w >> 4) & 15, w & 15
    i8 = w & 0xFF

    def I(op, **k):
        return Insn(pc, w, op, **k)

    if hi == 0x0:
        if lo == 0x2:
            return {0: I("stc", n=n, imm=0), 1: I("stc", n=n, imm=1), 2: I("stc", n=n, imm=2)}.get(m, I("illegal"))
        if lo == 0x3:
            return {0: I("bsrf", m=n), 2: I("braf", m=n)}.get(m, I("illegal"))
        if lo in (0x4, 0x5, 0x6):
            return I("mov." + "bwl"[lo - 4] + "@r0_st", n=n, m=m)
        if lo == 0x7:
            return I("mul.l", n=n, m=m)
        if lo == 0x8 and n == 0:
            return {0: I("clrt"), 1: I("sett"), 2: I("clrmac")}.get(m, I("illegal"))
        if lo == 0x9:
            if n == 0 and m == 0:
                return I("nop")
            if n == 0 and m == 1:
                return I("div0u")
            if m == 2:
                return I("movt", n=n)
            return I("illegal")
        if lo == 0xA:
            return {0: I("sts", n=n, imm=0), 1: I("sts", n=n, imm=1), 2: I("sts", n=n, imm=2)}.get(m, I("illegal"))
        if lo == 0xB and n == 0:
            return {0: I("rts"), 1: I("sleep"), 2: I("rte")}.get(m, I("illegal"))
        if lo in (0xC, 0xD, 0xE):
            return I("mov." + "bwl"[lo - 0xC] + "@r0_ld", n=n, m=m)
        if lo == 0xF:
            return I("mac.l", n=n, m=m)
        return I("illegal")
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
        if lo == 0xF:
            return I("mac.w", n=n, m=m)
        k = w & 0xFF
        table = {
            0x00: ("shll", "n"), 0x01: ("shlr", "n"), 0x02: ("sts.l", "n", 0), 0x03: ("stc.l", "n", 0),
            0x04: ("rotl", "n"), 0x05: ("rotr", "n"), 0x06: ("lds.l", "m", 0), 0x07: ("ldc.l", "m", 0),
            0x08: ("shll2", "n"), 0x09: ("shlr2", "n"), 0x0A: ("lds", "m", 0), 0x0B: ("jsr", "m"),
            0x0E: ("ldc", "m", 0),
            0x10: ("dt", "n"), 0x11: ("cmp/pz", "n"), 0x12: ("sts.l", "n", 1), 0x13: ("stc.l", "n", 1),
            0x15: ("cmp/pl", "n"), 0x16: ("lds.l", "m", 1), 0x17: ("ldc.l", "m", 1), 0x18: ("shll8", "n"),
            0x19: ("shlr8", "n"), 0x1A: ("lds", "m", 1), 0x1B: ("tas.b", "n"), 0x1E: ("ldc", "m", 1),
            0x20: ("shal", "n"), 0x21: ("shar", "n"), 0x22: ("sts.l", "n", 2), 0x23: ("stc.l", "n", 2),
            0x24: ("rotcl", "n"), 0x25: ("rotcr", "n"), 0x26: ("lds.l", "m", 2), 0x27: ("ldc.l", "m", 2),
            0x28: ("shll16", "n"), 0x29: ("shlr16", "n"), 0x2A: ("lds", "m", 2), 0x2B: ("jmp", "m"),
            0x2E: ("ldc", "m", 2),
        }
        e = table.get(k)
        if not e:
            return I("illegal")
        op, which = e[0], e[1]
        imm = e[2] if len(e) > 2 else 0
        return I(op, n=n, imm=imm) if which == "n" else I(op, m=n, imm=imm)
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
    return I("mov#", n=n, imm=_s8(i8))  # 0xE


CTRL = ["sr", "gbr", "vbr"]
SYSR = ["mach", "macl", "pr"]


def disasm(i):
    """A GNU-style disassembly line (for comments and listings)."""
    o, n, m = i.op, f"r{i.n}", f"r{i.m}"
    if o in ("nop", "rts", "rte", "sleep", "clrt", "sett", "clrmac", "div0u"):
        return o
    if o in ("bt", "bf", "bt/s", "bf/s", "bra", "bsr"):
        return f"{o} 0x{i.ea:08x}"
    if o in ("braf", "bsrf"):
        return f"{o} {m}"
    if o in ("jmp", "jsr"):
        return f"{o} @{m}"
    if o == "stc":
        return f"stc {CTRL[i.imm]},{n}"
    if o == "stc.l":
        return f"stc.l {CTRL[i.imm]},@-{n}"
    if o == "ldc":
        return f"ldc {m},{CTRL[i.imm]}"
    if o == "ldc.l":
        return f"ldc.l @{m}+,{CTRL[i.imm]}"
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
    if o == "illegal":
        return f".word 0x{i.word:04x}"
    return f"{o} {m},{n}"
