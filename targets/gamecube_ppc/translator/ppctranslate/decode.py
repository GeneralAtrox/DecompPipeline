"""PowerPC 750CL (Gekko) instruction decoder: 32-bit big-endian words -> Insn.

Written from the instruction encodings in the PowerPC Microprocessor Family: The Programming Environments for
32-Bit Microprocessors (chapter 8, "Instruction Set") and the IBM PowerPC 750CL User's Manual (the paired-single
and quantized load/store instructions, opcode 4 and 56..61). Every architected user-level instruction of the 750CL
decodes; the supervisor ones the games use (mtmsr, mfsr, tlbie, rfi, ...) decode too, so a whole executable can be
walked. A word no form matches is op "illegal".

Field names (big-endian bit numbers as in the manuals):
  rd/rs/frd/frs (6-10), ra/fra (11-15), rb/frb (16-20), frc (21-25), simm/uimm/d (16-31), bo (6-10), bi (11-15),
  crfd (6-8), crfs (11-13), crbd/crba/crbb (6-10, 11-15, 16-20), sh/mb/me (16-20, 21-25, 26-30), spr (11-20,
  halves swapped), crm (12-19), fm (7-14), w/i (paired-single W and GQR index), rc (31), oe (21), lk (31), aa (30).
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Insn:
    addr: int
    word: int
    op: str
    f: dict = field(default_factory=dict)

    def __getattr__(self, k):
        try:
            return self.__dict__["f"][k]
        except KeyError:
            raise AttributeError(k) from None

    @property
    def rc(self):
        return self.f.get("rc", 0)

    @property
    def oe(self):
        return self.f.get("oe", 0)


def bits(w, a, b):
    """Big-endian bits a..b (inclusive) of a 32-bit word."""
    return (w >> (31 - b)) & ((1 << (b - a + 1)) - 1)


def sext(v, n):
    v &= (1 << n) - 1
    return v - (1 << n) if v & (1 << (n - 1)) else v


# D-form opcodes: (op, kind)
D_LOADSTORE = {
    32: "lwz", 33: "lwzu", 34: "lbz", 35: "lbzu", 36: "stw", 37: "stwu", 38: "stb", 39: "stbu",
    40: "lhz", 41: "lhzu", 42: "lha", 43: "lhau", 44: "sth", 45: "sthu", 46: "lmw", 47: "stmw",
    48: "lfs", 49: "lfsu", 50: "lfd", 51: "lfdu", 52: "stfs", 53: "stfsu", 54: "stfd", 55: "stfdu",
}

OP19 = {0: "mcrf", 16: "bclr", 33: "crnor", 50: "rfi", 129: "crandc", 150: "isync", 193: "crxor", 225: "crnand",
        257: "crand", 289: "creqv", 417: "crorc", 449: "cror", 528: "bcctr"}
CR_LOGIC = {"crnor", "crandc", "crxor", "crnand", "crand", "creqv", "crorc", "cror"}

# Opcode 31, XO-form arithmetic (9-bit XO, OE in bit 21)
OP31_XO = {266: "add", 10: "addc", 138: "adde", 234: "addme", 202: "addze", 491: "divw", 459: "divwu",
           235: "mullw", 104: "neg", 40: "subf", 8: "subfc", 136: "subfe", 232: "subfme", 200: "subfze",
           75: "mulhw", 11: "mulhwu"}
# Opcode 31, X-form (10-bit XO)
OP31_X = {
    0: "cmp", 4: "tw", 19: "mfcr", 20: "lwarx", 23: "lwzx", 24: "slw", 26: "cntlzw", 28: "and", 32: "cmpl",
    54: "dcbst", 55: "lwzux", 60: "andc", 83: "mfmsr", 86: "dcbf", 87: "lbzx", 119: "lbzux", 124: "nor",
    144: "mtcrf", 146: "mtmsr", 150: "stwcx.", 151: "stwx", 183: "stwux", 210: "mtsr", 215: "stbx", 242: "mtsrin",
    246: "dcbtst", 247: "stbux", 278: "dcbt", 279: "lhzx", 284: "eqv", 306: "tlbie", 310: "eciwx", 311: "lhzux",
    316: "xor", 339: "mfspr", 343: "lhax", 371: "mftb", 375: "lhaux", 407: "sthx", 412: "orc", 438: "ecowx",
    439: "sthux", 444: "or", 467: "mtspr", 470: "dcbi", 476: "nand", 512: "mcrxr", 533: "lswx", 534: "lwbrx",
    535: "lfsx", 536: "srw", 566: "tlbsync", 567: "lfsux", 595: "mfsr", 597: "lswi", 598: "sync", 599: "lfdx",
    631: "lfdux", 659: "mfsrin", 661: "stswx", 662: "stwbrx", 663: "stfsx", 695: "stfsux", 725: "stswi",
    727: "stfdx", 759: "stfdux", 790: "lhbrx", 792: "sraw", 824: "srawi", 854: "eieio", 918: "sthbrx",
    922: "extsh", 954: "extsb", 982: "icbi", 983: "stfiwx", 1014: "dcbz",
}
# X-form ops whose first register field is the source (rS) and second the destination (rA)
LOGIC_X = {"slw", "cntlzw", "and", "andc", "nor", "eqv", "xor", "orc", "or", "nand", "srw", "sraw", "srawi",
           "extsh", "extsb"}
INDEXED_LOAD = {"lwarx", "lwzx", "lwzux", "lbzx", "lbzux", "lhzx", "lhzux", "lhax", "lhaux", "lwbrx", "lhbrx",
                "lfsx", "lfsux", "lfdx", "lfdux", "eciwx"}
INDEXED_STORE = {"stwcx.", "stwx", "stwux", "stbx", "stbux", "sthx", "sthux", "stwbrx", "sthbrx", "stfsx",
                 "stfsux", "stfdx", "stfdux", "stfiwx", "ecowx"}
CACHE = {"dcbst", "dcbf", "dcbtst", "dcbt", "dcbi", "icbi", "dcbz", "dcbz_l"}

OP59 = {18: "fdivs", 20: "fsubs", 21: "fadds", 24: "fres", 25: "fmuls", 28: "fmsubs", 29: "fmadds",
        30: "fnmsubs", 31: "fnmadds"}
OP63_A = {18: "fdiv", 20: "fsub", 21: "fadd", 22: "fsqrt", 23: "fsel", 25: "fmul", 26: "frsqrte", 28: "fmsub",
          29: "fmadd", 30: "fnmsub", 31: "fnmadd"}
OP63_X = {0: "fcmpu", 12: "frsp", 14: "fctiw", 15: "fctiwz", 32: "fcmpo", 38: "mtfsb1", 40: "fneg", 64: "mcrfs",
          70: "mtfsb0", 72: "fmr", 134: "mtfsfi", 136: "fnabs", 264: "fabs", 583: "mffs", 711: "mtfsf"}
# Gekko paired singles (opcode 4)
OP4_A = {10: "ps_sum0", 11: "ps_sum1", 12: "ps_muls0", 13: "ps_muls1", 14: "ps_madds0", 15: "ps_madds1",
         18: "ps_div", 20: "ps_sub", 21: "ps_add", 23: "ps_sel", 24: "ps_res", 25: "ps_mul", 26: "ps_rsqrte",
         28: "ps_msub", 29: "ps_madd", 30: "ps_nmsub", 31: "ps_nmadd"}
OP4_Q = {6: "psq_lx", 7: "psq_stx", 38: "psq_lux", 39: "psq_stux"}
OP4_X = {0: "ps_cmpu0", 32: "ps_cmpo0", 40: "ps_neg", 64: "ps_cmpu1", 72: "ps_mr", 96: "ps_cmpo1",
         136: "ps_nabs", 264: "ps_abs", 528: "ps_merge00", 560: "ps_merge01", 592: "ps_merge10",
         624: "ps_merge11", 1014: "dcbz_l"}
PSQ_D = {56: "psq_l", 57: "psq_lu", 60: "psq_st", 61: "psq_stu"}

# A-form float operand use: which of fA, fB, fC an op reads
A_USES = {
    "fdiv": "ab", "fsub": "ab", "fadd": "ab", "fsqrt": "b", "fsel": "abc", "fmul": "ac", "frsqrte": "b",
    "fmsub": "abc", "fmadd": "abc", "fnmsub": "abc", "fnmadd": "abc", "fres": "b",
}
for _k in list(A_USES):
    A_USES[_k + "s"] = A_USES[_k]
A_USES.update({"ps_sum0": "abc", "ps_sum1": "abc", "ps_muls0": "ac", "ps_muls1": "ac", "ps_madds0": "abc",
               "ps_madds1": "abc", "ps_div": "ab", "ps_sub": "ab", "ps_add": "ab", "ps_sel": "abc", "ps_res": "b",
               "ps_mul": "ac", "ps_rsqrte": "b", "ps_msub": "abc", "ps_madd": "abc", "ps_nmsub": "abc",
               "ps_nmadd": "abc"})


def decode(w: int, addr: int = 0) -> Insn:
    w &= 0xFFFFFFFF
    opcd = w >> 26
    r6, r11, r16, r21 = bits(w, 6, 10), bits(w, 11, 15), bits(w, 16, 20), bits(w, 21, 25)
    rc = w & 1

    def mk(op, **f):
        return Insn(addr, w, op, f)

    if opcd in D_LOADSTORE:
        op = D_LOADSTORE[opcd]
        return mk(op, rd=r6, ra=r11, d=sext(w, 16))
    if opcd == 14:
        return mk("addi", rd=r6, ra=r11, simm=sext(w, 16))
    if opcd == 15:
        return mk("addis", rd=r6, ra=r11, simm=sext(w, 16))
    if opcd == 12:
        return mk("addic", rd=r6, ra=r11, simm=sext(w, 16))
    if opcd == 13:
        return mk("addic.", rd=r6, ra=r11, simm=sext(w, 16), rc=1)
    if opcd == 8:
        return mk("subfic", rd=r6, ra=r11, simm=sext(w, 16))
    if opcd == 7:
        return mk("mulli", rd=r6, ra=r11, simm=sext(w, 16))
    if opcd in (10, 11):
        if bits(w, 9, 9) or bits(w, 10, 10):
            return mk("illegal")           # reserved bit / L = 1 (64-bit compare) is invalid on a 32-bit core
        if opcd == 11:
            return mk("cmpi", crfd=bits(w, 6, 8), ra=r11, simm=sext(w, 16))
        return mk("cmpli", crfd=bits(w, 6, 8), ra=r11, uimm=w & 0xFFFF)
    if opcd in (24, 25, 26, 27, 28, 29):
        op = {24: "ori", 25: "oris", 26: "xori", 27: "xoris", 28: "andi.", 29: "andis."}[opcd]
        return mk(op, rs=r6, ra=r11, uimm=w & 0xFFFF, rc=1 if opcd >= 28 else 0)
    if opcd in (20, 21, 23):
        op = {20: "rlwimi", 21: "rlwinm", 23: "rlwnm"}[opcd]
        f = dict(rs=r6, ra=r11, mb=bits(w, 21, 25), me=bits(w, 26, 30), rc=rc)
        if opcd == 23:
            f["rb"] = r16
        else:
            f["sh"] = r16
        return mk(op, **f)
    if opcd == 18:
        li = sext(w & 0x03FFFFFC, 26)
        aa, lk = (w >> 1) & 1, w & 1
        return mk("b", target=(li if aa else addr + li) & 0xFFFFFFFF, aa=aa, lk=lk)
    if opcd == 16:
        bd = sext(w & 0xFFFC, 16)
        aa, lk = (w >> 1) & 1, w & 1
        return mk("bc", bo=r6, bi=r11, target=(bd if aa else addr + bd) & 0xFFFFFFFF, aa=aa, lk=lk)
    if opcd == 17:
        return mk("sc") if (w & 2) else mk("illegal")
    if opcd == 3:
        return mk("twi", to=r6, ra=r11, simm=sext(w, 16))
    if opcd == 19:
        xo = bits(w, 21, 30)
        op = OP19.get(xo)
        if op is None:
            return mk("illegal")
        if op in ("bclr", "bcctr"):
            return mk(op, bo=r6, bi=r11, lk=w & 1)
        if op in CR_LOGIC:
            return mk(op, crbd=r6, crba=r11, crbb=r16)
        if op == "mcrf":
            return mk(op, crfd=bits(w, 6, 8), crfs=bits(w, 11, 13))
        return mk(op)
    if opcd == 31:
        return _decode31(w, addr, mk, r6, r11, r16, rc)
    if opcd == 59:
        op = OP59.get(bits(w, 26, 30))
        if op is None:
            return mk("illegal")
        return mk(op, frd=r6, fra=r11, frb=r16, frc=r21, rc=rc)
    if opcd == 63:
        xo5 = bits(w, 26, 30)
        if xo5 in OP63_A:
            return mk(OP63_A[xo5], frd=r6, fra=r11, frb=r16, frc=r21, rc=rc)
        op = OP63_X.get(bits(w, 21, 30))
        if op is None:
            return mk("illegal")
        if op in ("fcmpu", "fcmpo"):
            return mk(op, crfd=bits(w, 6, 8), fra=r11, frb=r16)
        if op == "mcrfs":
            return mk(op, crfd=bits(w, 6, 8), crfs=bits(w, 11, 13))
        if op in ("mtfsb0", "mtfsb1"):
            return mk(op, crbd=r6, rc=rc)
        if op == "mtfsfi":
            return mk(op, crfd=bits(w, 6, 8), imm=bits(w, 16, 19), rc=rc)
        if op == "mtfsf":
            return mk(op, fm=bits(w, 7, 14), frb=r16, rc=rc)
        if op == "mffs":
            return mk(op, frd=r6, rc=rc)
        return mk(op, frd=r6, frb=r16, rc=rc)
    if opcd == 4:
        xo5 = bits(w, 26, 30)
        if xo5 in OP4_A:
            return mk(OP4_A[xo5], frd=r6, fra=r11, frb=r16, frc=r21, rc=rc)
        xo6 = bits(w, 25, 30)
        if xo6 in OP4_Q:
            return mk(OP4_Q[xo6], frd=r6, ra=r11, rb=r16, w=bits(w, 21, 21), i=bits(w, 22, 24))
        op = OP4_X.get(bits(w, 21, 30))
        if op is None:
            return mk("illegal")
        if op.startswith("ps_cmp"):
            return mk(op, crfd=bits(w, 6, 8), fra=r11, frb=r16)
        if op == "dcbz_l":
            return mk(op, ra=r11, rb=r16)
        if op.startswith("ps_merge"):
            return mk(op, frd=r6, fra=r11, frb=r16, rc=rc)
        return mk(op, frd=r6, frb=r16, rc=rc)
    if opcd in PSQ_D:
        return mk(PSQ_D[opcd], frd=r6, ra=r11, w=bits(w, 16, 16), i=bits(w, 17, 19), d=sext(w, 12))
    return mk("illegal")


def _decode31(w, addr, mk, r6, r11, r16, rc):
    xo9 = bits(w, 22, 30)
    if xo9 in OP31_XO:
        op = OP31_XO[xo9]
        oe = bits(w, 21, 21)
        if op in ("mulhw", "mulhwu") and oe:
            return mk("illegal")
        return mk(op, rd=r6, ra=r11, rb=r16, rc=rc, oe=oe)
    xo = bits(w, 21, 30)
    op = OP31_X.get(xo)
    if op is None:
        return mk("illegal")
    if op in ("cmp", "cmpl"):
        if bits(w, 9, 10):
            return mk("illegal")
        return mk(op, crfd=bits(w, 6, 8), ra=r11, rb=r16)
    if op == "srawi":
        return mk(op, rs=r6, ra=r11, sh=r16, rc=rc)
    if op in LOGIC_X:
        return mk(op, rs=r6, ra=r11, rb=r16, rc=rc)
    if op in INDEXED_LOAD:
        return mk(op, rd=r6, ra=r11, rb=r16)
    if op in INDEXED_STORE:
        return mk(op, rs=r6, ra=r11, rb=r16, rc=1 if op == "stwcx." else 0)
    if op in CACHE:
        return mk(op, ra=r11, rb=r16)
    if op in ("mfspr", "mtspr", "mftb"):
        spr = (r16 << 5) | r11
        return mk(op, rd=r6, spr=spr)
    if op == "mfcr" or op == "mfmsr":
        return mk(op, rd=r6)
    if op == "mtcrf":
        return mk(op, rs=r6, crm=bits(w, 12, 19))
    if op == "mtmsr":
        return mk(op, rs=r6)
    if op == "mcrxr":
        return mk(op, crfd=bits(w, 6, 8))
    if op in ("mfsr", "mtsr"):
        return mk(op, rd=r6, sr=bits(w, 12, 15))
    if op in ("mfsrin", "mtsrin"):
        return mk(op, rd=r6, rb=r16)
    if op in ("lswi", "stswi"):
        return mk(op, rd=r6, ra=r11, nb=r16)
    if op in ("lswx", "stswx"):
        return mk(op, rd=r6, ra=r11, rb=r16)
    if op == "tw":
        return mk(op, to=r6, ra=r11, rb=r16)
    if op == "tlbie":
        return mk(op, rb=r16)
    return mk(op)


# ------------------------------------------------------------------------------------------------- disassembly
SPR_NAMES = {1: "xer", 8: "lr", 9: "ctr", 268: "tbl", 269: "tbu", 912: "gqr0", 913: "gqr1", 914: "gqr2",
             915: "gqr3", 916: "gqr4", 917: "gqr5", 918: "gqr6", 919: "gqr7", 920: "hid2", 1008: "hid0",
             1009: "hid1", 26: "srr0", 27: "srr1", 272: "sprg0", 273: "sprg1", 274: "sprg2", 275: "sprg3",
             22: "dec", 18: "dsisr", 19: "dar", 1017: "l2cr", 922: "dmau", 923: "dmal", 921: "wpar"}


def _h(v):
    return f"-0x{-v:x}" if v < 0 else f"0x{v:x}"


def disasm(i: Insn) -> str:
    """A plain (non-simplified) assembly text for comments and diagnostics."""
    o, f = i.op, i.f
    dot = "." if f.get("rc") and not o.endswith(".") else ""
    oe = "o" if f.get("oe") else ""
    if o == "b":
        return f"b{'l' if f['lk'] else ''}{'a' if f['aa'] else ''} 0x{f['target']:08x}"
    if o == "bc":
        return f"bc{'l' if f['lk'] else ''}{'a' if f['aa'] else ''} {f['bo']},{f['bi']},0x{f['target']:08x}"
    if o in ("bclr", "bcctr"):
        return f"{o}{'l' if f['lk'] else ''} {f['bo']},{f['bi']}"
    if o in D_LOADSTORE.values():
        reg = "f" if o.startswith(("lf", "stf")) else "r"
        return f"{o} {reg}{f['rd']},{_h(f['d'])}(r{f['ra']})"
    if o in ("psq_l", "psq_lu", "psq_st", "psq_stu"):
        return f"{o} f{f['frd']},{_h(f['d'])}(r{f['ra']}),{f['w']},{f['i']}"
    if o in OP4_Q.values():
        return f"{o} f{f['frd']},r{f['ra']},r{f['rb']},{f['w']},{f['i']}"
    if o in ("addi", "addis", "addic", "addic.", "subfic", "mulli"):
        return f"{o} r{f['rd']},r{f['ra']},{_h(f['simm'])}"
    if o in ("cmpi", "cmpli"):
        return f"{o} cr{f['crfd']},r{f['ra']},{_h(f.get('simm', f.get('uimm', 0)))}"
    if o in ("cmp", "cmpl"):
        return f"{o} cr{f['crfd']},r{f['ra']},r{f['rb']}"
    if o in ("ori", "oris", "xori", "xoris", "andi.", "andis."):
        return f"{o} r{f['ra']},r{f['rs']},0x{f['uimm']:x}"
    if o in ("rlwinm", "rlwimi"):
        return f"{o}{dot} r{f['ra']},r{f['rs']},{f['sh']},{f['mb']},{f['me']}"
    if o == "rlwnm":
        return f"{o}{dot} r{f['ra']},r{f['rs']},r{f['rb']},{f['mb']},{f['me']}"
    if o == "srawi":
        return f"{o}{dot} r{f['ra']},r{f['rs']},{f['sh']}"
    if o in OP31_XO.values():
        if o in ("addme", "addze", "neg", "subfme", "subfze"):
            return f"{o}{oe}{dot} r{f['rd']},r{f['ra']}"
        return f"{o}{oe}{dot} r{f['rd']},r{f['ra']},r{f['rb']}"
    if o in LOGIC_X:
        if o in ("cntlzw", "extsh", "extsb"):
            return f"{o}{dot} r{f['ra']},r{f['rs']}"
        return f"{o}{dot} r{f['ra']},r{f['rs']},r{f['rb']}"
    if o in INDEXED_LOAD or o in INDEXED_STORE:
        reg = "f" if o.startswith(("lf", "stf")) else "r"
        return f"{o} {reg}{f.get('rd', f.get('rs'))},r{f['ra']},r{f['rb']}"
    if o in CACHE:
        return f"{o} r{f['ra']},r{f['rb']}"
    if o in ("mfspr", "mtspr", "mftb"):
        return f"{o} r{f['rd']},{SPR_NAMES.get(f['spr'], f['spr'])}"
    if o in CR_LOGIC:
        return f"{o} {f['crbd']},{f['crba']},{f['crbb']}"
    if o in ("mcrf", "mcrfs"):
        return f"{o} cr{f['crfd']},cr{f['crfs']}"
    if o in ("fcmpu", "fcmpo") or o.startswith("ps_cmp"):
        return f"{o} cr{f['crfd']},f{f['fra']},f{f['frb']}"
    if o in A_USES:
        uses = A_USES[o]
        ops = [f"f{f['frd']}"] + [f"f{f['fr' + u]}" for u in uses]
        return f"{o}{dot} " + ",".join(ops)
    if o.startswith("ps_merge"):
        return f"{o}{dot} f{f['frd']},f{f['fra']},f{f['frb']}"
    if "frd" in f and "frb" in f:
        return f"{o}{dot} f{f['frd']},f{f['frb']}"
    if o == "mffs":
        return f"{o}{dot} f{f['frd']}"
    if o == "mtfsf":
        return f"{o}{dot} 0x{f['fm']:x},f{f['frb']}"
    if o in ("mfcr", "mfmsr"):
        return f"{o} r{f['rd']}"
    if o == "mtcrf":
        return f"{o} 0x{f['crm']:x},r{f['rs']}"
    if o in ("lswi", "stswi"):
        return f"{o} r{f['rd']},r{f['ra']},{f['nb']}"
    if o == "twi":
        return f"{o} {f['to']},r{f['ra']},{_h(f['simm'])}"
    if o == "illegal":
        return f".word 0x{i.word:08x}"
    return o + ("" if not f else " " + ",".join(f"{k}={v}" for k, v in f.items() if k not in ("rc", "oe")))
