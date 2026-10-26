"""Xenon (Xbox 360 PPE) instruction decoder: 32-bit big-endian words -> Insn.

Written from the instruction encodings of the PowerPC architecture books (PowerPC Microprocessor Family: The
Programming Environments for 64-Bit Microprocessors, chapter 8 "Instruction Set"; the 64-bit forms DS, MD, MDS and
XS), the AltiVec Technology Programming Environments Manual (the vector unit: VA, VX and VC forms of opcode 4 and the
opcode-31 vector loads and stores), and the Xenon's VMX128 extension (opcodes 4, 5 and 6: 128 vector registers whose
numbers are split across the word; REFERENCES.md). Every user-level instruction the Xenon implements decodes, and so
do the supervisor ones a title's code may still hold, so a whole executable can be walked. A word no form matches is
op "illegal". Xenon has no paired singles: opcode 4 is the vector unit.

Field names:
  rd/rs (6-10), ra (11-15), rb (16-20), simm/uimm/d (16-31), ds (16-29, times 4), bo (6-10), bi (11-15), crfd (6-8),
  crbd/crba/crbb (6-10, 11-15, 16-20), sh/mb/me (rotates; 6-bit for the 64-bit forms), spr (11-20, halves swapped),
  crm (12-19), fm (7-14), rc (31), oe (21), lk (31), aa (30), l (10: the 64-bit compare bit).
  Vectors: vd/vs, va, vb, vc (0..31 for AltiVec, 0..127 for VMX128), uimm/simm (the 5-bit vector immediates), sh
  (vsldoi), perm (vpermwi128), z (vrlimi128 / vpkd3d128 shift), imm (vrlimi128 mask, vupkd3d128 / vpkd3d128 type).
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


# D-form opcodes
D_LOADSTORE = {
    32: "lwz", 33: "lwzu", 34: "lbz", 35: "lbzu", 36: "stw", 37: "stwu", 38: "stb", 39: "stbu",
    40: "lhz", 41: "lhzu", 42: "lha", 43: "lhau", 44: "sth", 45: "sthu", 46: "lmw", 47: "stmw",
    48: "lfs", 49: "lfsu", 50: "lfd", 51: "lfdu", 52: "stfs", 53: "stfsu", 54: "stfd", 55: "stfdu",
}
DS58 = {0: "ld", 1: "ldu", 2: "lwa"}
DS62 = {0: "std", 1: "stdu"}

OP19 = {0: "mcrf", 16: "bclr", 18: "rfid", 33: "crnor", 129: "crandc", 150: "isync", 193: "crxor", 225: "crnand",
        257: "crand", 274: "hrfid", 289: "creqv", 417: "crorc", 449: "cror", 528: "bcctr"}
CR_LOGIC = {"crnor", "crandc", "crxor", "crnand", "crand", "creqv", "crorc", "cror"}

# Opcode 31, XO-form arithmetic (9-bit XO in bits 22-30, OE in bit 21)
OP31_XO = {266: "add", 10: "addc", 138: "adde", 234: "addme", 202: "addze", 491: "divw", 459: "divwu",
           235: "mullw", 104: "neg", 40: "subf", 8: "subfc", 136: "subfe", 232: "subfme", 200: "subfze",
           75: "mulhw", 11: "mulhwu", 233: "mulld", 73: "mulhd", 9: "mulhdu", 489: "divd", 457: "divdu"}
NO_OE = {"mulhw", "mulhwu", "mulhd", "mulhdu"}
# Opcode 31, X-form (10-bit XO in bits 21-30)
OP31_X = {
    0: "cmp", 4: "tw", 6: "lvsl", 7: "lvebx", 19: "mfcr", 20: "lwarx", 21: "ldx", 23: "lwzx", 24: "slw",
    26: "cntlzw", 27: "sld", 28: "and", 32: "cmpl", 38: "lvsr", 39: "lvehx", 53: "ldux", 54: "dcbst", 55: "lwzux",
    58: "cntlzd", 60: "andc", 68: "td", 71: "lvewx", 83: "mfmsr", 84: "ldarx", 86: "dcbf", 87: "lbzx",
    103: "lvx", 119: "lbzux", 124: "nor", 135: "stvebx", 144: "mtcrf", 146: "mtmsr", 149: "stdx", 150: "stwcx.",
    151: "stwx", 167: "stvehx", 178: "mtmsrd", 181: "stdux", 183: "stwux", 199: "stvewx", 210: "mtsr", 214: "stdcx.",
    215: "stbx", 231: "stvx", 242: "mtsrin", 246: "dcbtst", 247: "stbux", 274: "tlbiel", 278: "dcbt", 279: "lhzx",
    284: "eqv", 306: "tlbie", 310: "eciwx", 311: "lhzux", 316: "xor", 339: "mfspr", 341: "lwax", 342: "dst",
    343: "lhax", 359: "lvxl", 371: "mftb", 373: "lwaux", 374: "dstst", 375: "lhaux", 402: "slbmte", 407: "sthx",
    412: "orc", 434: "slbie", 438: "ecowx", 439: "sthux", 444: "or", 467: "mtspr", 470: "dcbi", 476: "nand",
    487: "stvxl", 498: "slbia", 512: "mcrxr", 519: "lvlx", 533: "lswx", 534: "lwbrx", 535: "lfsx", 536: "srw",
    539: "srd", 551: "lvrx", 566: "tlbsync", 567: "lfsux", 595: "mfsr", 597: "lswi", 598: "sync", 599: "lfdx",
    631: "lfdux", 647: "stvlx", 659: "mfsrin", 661: "stswx", 662: "stwbrx", 663: "stfsx", 679: "stvrx",
    695: "stfsux", 725: "stswi", 727: "stfdx", 759: "stfdux", 775: "lvlxl", 790: "lhbrx", 792: "sraw",
    794: "srad", 807: "lvrxl", 822: "dss", 824: "srawi", 851: "slbmfev", 854: "eieio", 903: "stvlxl", 915: "slbmfee",
    918: "sthbrx", 922: "extsh", 935: "stvrxl", 954: "extsb", 982: "icbi", 983: "stfiwx", 986: "extsw",
    1014: "dcbz",
}
# X-form ops whose first register field is the source (rS) and second the destination (rA)
LOGIC_X = {"slw", "cntlzw", "and", "andc", "nor", "eqv", "xor", "orc", "or", "nand", "srw", "sraw", "srawi",
           "extsh", "extsb", "extsw", "sld", "srd", "srad", "cntlzd"}
INDEXED_LOAD = {"lwarx", "ldarx", "lwzx", "lwzux", "lbzx", "lbzux", "lhzx", "lhzux", "lhax", "lhaux", "lwbrx",
                "lhbrx", "lfsx", "lfsux", "lfdx", "lfdux", "eciwx", "ldx", "ldux", "lwax", "lwaux"}
INDEXED_STORE = {"stwcx.", "stdcx.", "stwx", "stwux", "stbx", "stbux", "sthx", "sthux", "stwbrx", "sthbrx", "stfsx",
                 "stfsux", "stfdx", "stfdux", "stfiwx", "ecowx", "stdx", "stdux"}
VEC_LOAD31 = {"lvsl", "lvsr", "lvebx", "lvehx", "lvewx", "lvx", "lvxl", "lvlx", "lvrx", "lvlxl", "lvrxl"}
VEC_STORE31 = {"stvebx", "stvehx", "stvewx", "stvx", "stvxl", "stvlx", "stvrx", "stvlxl", "stvrxl"}
CACHE = {"dcbst", "dcbf", "dcbtst", "dcbt", "dcbi", "icbi", "dcbz", "dcbz128"}

OP59 = {18: "fdivs", 20: "fsubs", 21: "fadds", 22: "fsqrts", 24: "fres", 25: "fmuls", 28: "fmsubs", 29: "fmadds",
        30: "fnmsubs", 31: "fnmadds"}
OP63_A = {18: "fdiv", 20: "fsub", 21: "fadd", 22: "fsqrt", 23: "fsel", 25: "fmul", 26: "frsqrte", 28: "fmsub",
          29: "fmadd", 30: "fnmsub", 31: "fnmadd"}
OP63_X = {0: "fcmpu", 12: "frsp", 14: "fctiw", 15: "fctiwz", 32: "fcmpo", 38: "mtfsb1", 40: "fneg", 64: "mcrfs",
          70: "mtfsb0", 72: "fmr", 134: "mtfsfi", 136: "fnabs", 264: "fabs", 583: "mffs", 711: "mtfsf",
          814: "fctid", 815: "fctidz", 846: "fcfid"}

# A-form float operand use: which of fA, fB, fC an op reads
A_USES = {
    "fdiv": "ab", "fsub": "ab", "fadd": "ab", "fsqrt": "b", "fsel": "abc", "fmul": "ac", "frsqrte": "b",
    "fmsub": "abc", "fmadd": "abc", "fnmsub": "abc", "fnmadd": "abc", "fres": "b",
}
for _k in list(A_USES):
    A_USES[_k + "s"] = A_USES[_k]

# ------------------------------------------------------------------------------------------- AltiVec (opcode 4)
# VA-form: 6-bit XO in bits 26-31
VA4 = {32: "vmhaddshs", 33: "vmhraddshs", 34: "vmladduhm", 36: "vmsumubm", 37: "vmsummbm", 38: "vmsumuhm",
       39: "vmsumuhs", 40: "vmsumshm", 41: "vmsumshs", 42: "vsel", 43: "vperm", 44: "vsldoi", 46: "vmaddfp",
       47: "vnmsubfp"}
# VC-form compares: 10-bit XO in bits 22-31 (Rc in bit 21)
VC4 = {6: "vcmpequb", 70: "vcmpequh", 134: "vcmpequw", 198: "vcmpeqfp", 454: "vcmpgefp", 518: "vcmpgtub",
       582: "vcmpgtuh", 646: "vcmpgtuw", 710: "vcmpgtfp", 774: "vcmpgtsb", 838: "vcmpgtsh", 902: "vcmpgtsw",
       966: "vcmpbfp"}
# VX-form: 11-bit XO in bits 21-31
VX4 = {
    0: "vaddubm", 2: "vmaxub", 4: "vrlb", 8: "vmuloub", 10: "vaddfp", 12: "vmrghb", 14: "vpkuhum",
    64: "vadduhm", 66: "vmaxuh", 68: "vrlh", 72: "vmulouh", 74: "vsubfp", 76: "vmrghh", 78: "vpkuwum",
    128: "vadduwm", 130: "vmaxuw", 132: "vrlw", 140: "vmrghw", 142: "vpkuhus", 206: "vpkuwus",
    258: "vmaxsb", 260: "vslb", 264: "vmulosb", 266: "vrefp", 268: "vmrglb", 270: "vpkshus",
    322: "vmaxsh", 324: "vslh", 328: "vmulosh", 330: "vrsqrtefp", 332: "vmrglh", 334: "vpkswus",
    384: "vaddcuw", 386: "vmaxsw", 388: "vslw", 394: "vexptefp", 396: "vmrglw", 398: "vpkshss",
    452: "vsl", 458: "vlogefp", 462: "vpkswss",
    512: "vaddubs", 514: "vminub", 516: "vsrb", 520: "vmuleub", 522: "vrfin", 524: "vspltb", 526: "vupkhsb",
    576: "vadduhs", 578: "vminuh", 580: "vsrh", 584: "vmuleuh", 586: "vrfiz", 588: "vsplth", 590: "vupkhsh",
    640: "vadduws", 642: "vminuw", 644: "vsrw", 650: "vrfip", 652: "vspltw", 654: "vupklsb",
    708: "vsr", 714: "vrfim", 718: "vupklsh",
    768: "vaddsbs", 770: "vminsb", 772: "vsrab", 776: "vmulesb", 778: "vcfux", 780: "vspltisb", 782: "vpkpx",
    832: "vaddshs", 834: "vminsh", 836: "vsrah", 840: "vmulesh", 842: "vcfsx", 844: "vspltish", 846: "vupkhpx",
    896: "vaddsws", 898: "vminsw", 900: "vsraw", 906: "vctuxs", 908: "vspltisw", 970: "vctsxs", 974: "vupklpx",
    1024: "vsububm", 1026: "vavgub", 1028: "vand", 1034: "vmaxfp", 1036: "vslo",
    1088: "vsubuhm", 1090: "vavguh", 1092: "vandc", 1098: "vminfp", 1100: "vsro",
    1152: "vsubuwm", 1154: "vavguw", 1156: "vor", 1220: "vxor", 1282: "vavgsb", 1284: "vnor", 1346: "vavgsh",
    1408: "vsubcuw", 1410: "vavgsw", 1536: "vsububs", 1540: "mfvscr", 1544: "vsum4ubs", 1600: "vsubuhs",
    1604: "mtvscr", 1608: "vsum4shs", 1664: "vsubuws", 1672: "vsum2sws", 1792: "vsubsbs", 1800: "vsum4sbs",
    1856: "vsubshs", 1920: "vsubsws", 1928: "vsumsws",
}
VX_UNARY = {"vrefp", "vrsqrtefp", "vexptefp", "vlogefp", "vrfin", "vrfiz", "vrfip", "vrfim", "vupkhsb", "vupkhsh",
            "vupklsb", "vupklsh", "vupkhpx", "vupklpx"}
VX_UIMM = {"vspltb", "vsplth", "vspltw", "vcfux", "vcfsx", "vctuxs", "vctsxs"}
VX_SIMM = {"vspltisb", "vspltish", "vspltisw"}

# ------------------------------------------------------------------------------------------------ VMX128
# The VMX128 register numbers (0..127) are split across the word (LSB bit numbers):
#   VD128 = bits 21-25 | bits 2-3 << 5;   VB128 = bits 11-15 | bits 0-1 << 5;
#   VA128 = bits 16-20 | bit 5 << 5 | bit 10 << 6;   VC (vperm128) = bits 6-8 (v0..v7).
# Opcode 4, VX128_1 (loads and stores, rA/rB address): matched by word & 0x7F3.
VX128_1 = {0x003: "lvsl128", 0x043: "lvsr128", 0x083: "lvewx128", 0x0C3: "lvx128", 0x183: "stvewx128",
           0x1C3: "stvx128", 0x2C3: "lvxl128", 0x3C3: "stvxl128", 0x403: "lvlx128", 0x443: "lvrx128",
           0x503: "stvlx128", 0x543: "stvrx128", 0x603: "lvlxl128", 0x643: "lvrxl128", 0x703: "stvlxl128",
           0x743: "stvrxl128"}
# Opcode 5, VX128 (three registers) matched by word & 0x3D0; vperm128 (VX128_2) by word & 0x210 == 0.
VX128_5 = {0x010: "vaddfp128", 0x050: "vsubfp128", 0x090: "vmulfp128", 0x0D0: "vmaddfp128",
           0x110: "vmaddcfp128", 0x150: "vnmsubfp128", 0x190: "vmsum3fp128", 0x1D0: "vmsum4fp128",
           0x200: "vpkshss128", 0x210: "vand128", 0x240: "vpkshus128", 0x250: "vandc128", 0x280: "vpkswss128",
           0x290: "vnor128", 0x2C0: "vpkswus128", 0x2D0: "vor128", 0x300: "vpkuhum128", 0x310: "vxor128",
           0x340: "vpkuhus128", 0x350: "vsel128", 0x380: "vpkuwum128", 0x390: "vslo128", 0x3C0: "vpkuwus128",
           0x3D0: "vsro128"}
# Opcode 6: compares (VX128_R, word & 0x3B0, Rc in bit 6), three-register ops (VX128, word & 0x3D0), unary ops
# with a 5-bit immediate (VX128_3, word & 0x7F0), vpermwi128 (VX128_P, word & 0x630), vrlimi128 / vpkd3d128
# (VX128_4, word & 0x730).
VX128_6R = {0x000: "vcmpeqfp128", 0x080: "vcmpgefp128", 0x100: "vcmpgtfp128", 0x180: "vcmpbfp128",
            0x200: "vcmpequw128"}
VX128_6 = {0x050: "vrlw128", 0x0D0: "vslw128", 0x150: "vsraw128", 0x1D0: "vsrw128", 0x280: "vmaxfp128",
           0x2C0: "vminfp128", 0x300: "vmrghw128", 0x340: "vmrglw128", 0x380: "vupkhsb128", 0x3C0: "vupklsb128"}
VX128_6U = {0x230: "vcfpsxws128", 0x270: "vcfpuxws128", 0x2B0: "vcsxwfp128", 0x2F0: "vcuxwfp128",
            0x330: "vrfim128", 0x370: "vrfin128", 0x3B0: "vrfip128", 0x3F0: "vrfiz128", 0x630: "vrefp128",
            0x670: "vrsqrtefp128", 0x6B0: "vexptefp128", 0x6F0: "vlogefp128", 0x730: "vspltw128",
            0x770: "vspltisw128", 0x7F0: "vupkd3d128"}
VX128_6_UNARY = {"vupkhsb128", "vupklsb128"}

VECTOR_OPS = (set(VA4.values()) | set(VC4.values()) | set(VX4.values()) | VEC_LOAD31 | VEC_STORE31 |
              set(VX128_1.values()) | set(VX128_5.values()) | set(VX128_6R.values()) | set(VX128_6.values()) |
              set(VX128_6U.values()) | {"vperm128", "vsldoi128", "vpermwi128", "vrlimi128", "vpkd3d128", "dst",
                                        "dstst", "dss"})


def vd128(w):
    return ((w >> 21) & 31) | (((w >> 2) & 3) << 5)


def va128(w):
    return ((w >> 16) & 31) | (((w >> 5) & 1) << 5) | (((w >> 10) & 1) << 6)


def vb128(w):
    return ((w >> 11) & 31) | ((w & 3) << 5)


def decode(w: int, addr: int = 0) -> Insn:
    w &= 0xFFFFFFFF
    opcd = w >> 26
    r6, r11, r16, r21 = bits(w, 6, 10), bits(w, 11, 15), bits(w, 16, 20), bits(w, 21, 25)
    rc = w & 1

    def mk(op, **f):
        return Insn(addr, w, op, f)

    if opcd in D_LOADSTORE:
        return mk(D_LOADSTORE[opcd], rd=r6, ra=r11, d=sext(w, 16))
    if opcd == 58 or opcd == 62:
        op = (DS58 if opcd == 58 else DS62).get(w & 3)
        if op is None:
            return mk("illegal")
        return mk(op, rd=r6, ra=r11, d=sext(w & 0xFFFC, 16))
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
        if bits(w, 9, 9):
            return mk("illegal")
        l = bits(w, 10, 10)
        if opcd == 11:
            return mk("cmpi", crfd=bits(w, 6, 8), l=l, ra=r11, simm=sext(w, 16))
        return mk("cmpli", crfd=bits(w, 6, 8), l=l, ra=r11, uimm=w & 0xFFFF)
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
    if opcd == 30:
        return _decode30(w, mk, r6, r11, r16, rc)
    if opcd == 18:
        li = sext(w & 0x03FFFFFC, 26)
        aa, lk = (w >> 1) & 1, w & 1
        return mk("b", target=(li if aa else addr + li) & 0xFFFFFFFF, aa=aa, lk=lk)
    if opcd == 16:
        bd = sext(w & 0xFFFC, 16)
        aa, lk = (w >> 1) & 1, w & 1
        return mk("bc", bo=r6, bi=r11, target=(bd if aa else addr + bd) & 0xFFFFFFFF, aa=aa, lk=lk)
    if opcd == 17:
        return mk("sc", lev=bits(w, 20, 26)) if (w & 2) else mk("illegal")
    if opcd == 3:
        return mk("twi", to=r6, ra=r11, simm=sext(w, 16))
    if opcd == 2:
        return mk("tdi", to=r6, ra=r11, simm=sext(w, 16))
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
        return _decode4(w, mk, r6, r11, r16, r21)
    if opcd == 5:
        return _decode5(w, mk)
    if opcd == 6:
        return _decode6(w, mk)
    return mk("illegal")


def _decode30(w, mk, r6, r11, r16, rc):
    """MD-form (rldicl, rldicr, rldic, rldimi: 3-bit XO in bits 27-29) and MDS-form (rldcl, rldcr: 4-bit XO in
    bits 27-30). The 6-bit sh is bits 16-20 with bit 30 as its high bit; the 6-bit mb/me is bits 21-25 with bit 26
    as its high bit."""
    m6 = ((w >> 6) & 31) | (w & 0x20)
    xo3 = (w >> 2) & 7
    if xo3 < 4:
        op = ("rldicl", "rldicr", "rldic", "rldimi")[xo3]
        sh = r16 | (((w >> 1) & 1) << 5)
        f = dict(rs=r6, ra=r11, sh=sh, rc=rc)
        f["me" if op == "rldicr" else "mb"] = m6
        return mk(op, **f)
    xo4 = (w >> 1) & 15
    if xo4 in (8, 9):
        op = "rldcl" if xo4 == 8 else "rldcr"
        f = dict(rs=r6, ra=r11, rb=r16, rc=rc)
        f["mb" if op == "rldcl" else "me"] = m6
        return mk(op, **f)
    return mk("illegal")


def _decode31(w, addr, mk, r6, r11, r16, rc):
    xo9 = bits(w, 22, 30)
    if xo9 in OP31_XO:
        op = OP31_XO[xo9]
        oe = bits(w, 21, 21)
        if op in NO_OE and oe:
            return mk("illegal")
        return mk(op, rd=r6, ra=r11, rb=r16, rc=rc, oe=oe)
    xo = bits(w, 21, 30)
    if xo in (826, 827):                    # XS-form sradi: 9-bit XO 413, bit 30 is sh's high bit
        return mk("sradi", rs=r6, ra=r11, sh=r16 | ((xo & 1) << 5), rc=rc)
    op = OP31_X.get(xo)
    if op is None:
        return mk("illegal")
    if op in ("cmp", "cmpl"):
        if bits(w, 9, 9):
            return mk("illegal")
        return mk(op, crfd=bits(w, 6, 8), l=bits(w, 10, 10), ra=r11, rb=r16)
    if op == "srawi":
        return mk(op, rs=r6, ra=r11, sh=r16, rc=rc)
    if op in LOGIC_X:
        return mk(op, rs=r6, ra=r11, rb=r16, rc=rc)
    if op in INDEXED_LOAD:
        return mk(op, rd=r6, ra=r11, rb=r16)
    if op in INDEXED_STORE:
        return mk(op, rs=r6, ra=r11, rb=r16, rc=1 if op.endswith(".") else 0)
    if op in VEC_LOAD31:
        return mk(op, vd=r6, ra=r11, rb=r16)
    if op in VEC_STORE31:
        return mk(op, vs=r6, ra=r11, rb=r16)
    if op == "dcbz" and bits(w, 10, 10):
        return mk("dcbz128", ra=r11, rb=r16)
    if op in CACHE:
        return mk(op, ra=r11, rb=r16)
    if op in ("dst", "dstst"):
        return mk(op, ra=r11, rb=r16, strm=bits(w, 9, 10), t=bits(w, 6, 6))
    if op == "dss":
        return mk(op, strm=bits(w, 9, 10), all=bits(w, 6, 6))
    if op in ("mfspr", "mtspr", "mftb"):
        spr = (r16 << 5) | r11
        return mk(op, rd=r6, spr=spr)
    if op == "mfcr":
        if bits(w, 11, 11):
            return mk("mfocrf", rd=r6, crm=bits(w, 12, 19))
        return mk(op, rd=r6)
    if op == "mfmsr":
        return mk(op, rd=r6)
    if op == "mtcrf":
        if bits(w, 11, 11):
            return mk("mtocrf", rs=r6, crm=bits(w, 12, 19))
        return mk(op, rs=r6, crm=bits(w, 12, 19))
    if op in ("mtmsr", "mtmsrd"):
        return mk(op, rs=r6, l=bits(w, 15, 15))
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
    if op in ("tw", "td"):
        return mk(op, to=r6, ra=r11, rb=r16)
    if op == "sync":
        return mk("lwsync" if bits(w, 9, 10) == 1 else "sync")
    if op in ("tlbie", "tlbiel", "slbie", "slbmte", "slbmfev", "slbmfee"):
        return mk(op, rd=r6, rb=r16)
    return mk(op)


def _decode4(w, mk, r6, r11, r16, r21):
    """Opcode 4: AltiVec (VA, VC, VX forms) and the VMX128 forms that share it (VX128_1 loads/stores, vsldoi128)."""
    low6 = w & 0x3F
    if low6 & 0x10:                                         # VX128_5: vsldoi128 vD,vA,vB,SH (SH in bits 6-9)
        return mk("vsldoi128", vd=vd128(w), va=va128(w), vb=vb128(w), sh=(w >> 6) & 15)
    if low6 & 0x20:                                         # VA-form (XO 32..47; none has bit 4 set)
        op = VA4.get(low6)
        if op is None:
            return mk("illegal")
        if op == "vsldoi":
            if bits(w, 21, 21):
                return mk("illegal")
            return mk(op, vd=r6, va=r11, vb=r16, sh=bits(w, 22, 25))
        return mk(op, vd=r6, va=r11, vb=r16, vc=r21)
    if (w & 3) == 3:                                        # VX128_1: vector loads/stores through rA/rB
        op = VX128_1.get(w & 0x7F3)
        if op is None:
            return mk("illegal")
        reg = "vs" if op.startswith("st") else "vd"
        return mk(op, **{reg: vd128(w)}, ra=r11, rb=r16)
    xo10 = w & 0x3FF
    if xo10 in VC4:
        return mk(VC4[xo10], vd=r6, va=r11, vb=r16, rc=bits(w, 21, 21))
    op = VX4.get(w & 0x7FF)
    if op is None:
        return mk("illegal")
    if op == "mfvscr":
        return mk(op, vd=r6)
    if op == "mtvscr":
        return mk(op, vb=r16)
    if op in VX_UNARY:
        return mk(op, vd=r6, vb=r16)
    if op in VX_UIMM:
        return mk(op, vd=r6, vb=r16, uimm=r11)
    if op in VX_SIMM:
        return mk(op, vd=r6, simm=sext(r11, 5))
    return mk(op, vd=r6, va=r11, vb=r16)


def _decode5(w, mk):
    if w & 0x210 == 0:                                      # VX128_2: vperm128 vD,vA,vB,vC (vC in v0..v7)
        return mk("vperm128", vd=vd128(w), va=va128(w), vb=vb128(w), vc=(w >> 6) & 7)
    op = VX128_5.get(w & 0x3D0)
    if op is None:
        return mk("illegal")
    return mk(op, vd=vd128(w), va=va128(w), vb=vb128(w))


def _decode6(w, mk):
    if (w & 0x10) == 0:                                     # VX128_R compares (Rc in bit 6), else VX128 below
        op = VX128_6R.get(w & 0x390)
        if op is not None:
            return mk(op, vd=vd128(w), va=va128(w), vb=vb128(w), rc=(w >> 6) & 1)
    if (w & 0x730) == 0x710:                                # VX128_4: vrlimi128 vD,vB,IMM,z
        return mk("vrlimi128", vd=vd128(w), vb=vb128(w), imm=(w >> 16) & 31, z=(w >> 6) & 3)
    if (w & 0x730) == 0x610:                                # VX128_4: vpkd3d128 vD,vB,type,mask,z
        imm = (w >> 16) & 31
        return mk("vpkd3d128", vd=vd128(w), vb=vb128(w), imm=imm >> 2, mask=imm & 3, z=(w >> 6) & 3)
    op = VX128_6U.get(w & 0x7F0)
    if op is not None:                                      # VX128_3: vD,vB,UIMM (vspltisw128: SIMM)
        u = (w >> 16) & 31
        if op == "vspltisw128":
            return mk(op, vd=vd128(w), simm=sext(u, 5))
        if op == "vupkd3d128":
            return mk(op, vd=vd128(w), vb=vb128(w), imm=u >> 2, mask=u & 3)
        if op in ("vcfpsxws128", "vcfpuxws128", "vcsxwfp128", "vcuxwfp128", "vspltw128"):
            return mk(op, vd=vd128(w), vb=vb128(w), uimm=u)
        return mk(op, vd=vd128(w), vb=vb128(w))
    if (w & 0x630) == 0x210:                                # VX128_P: vpermwi128 vD,vB,PERM
        return mk("vpermwi128", vd=vd128(w), vb=vb128(w), perm=((w >> 16) & 31) | (((w >> 6) & 7) << 5))
    op = VX128_6.get(w & 0x3D0)
    if op is not None:
        if op in VX128_6_UNARY:
            return mk(op, vd=vd128(w), vb=vb128(w))
        return mk(op, vd=vd128(w), va=va128(w), vb=vb128(w))
    return mk("illegal")


# ------------------------------------------------------------------------------------------------- disassembly
SPR_NAMES = {1: "xer", 8: "lr", 9: "ctr", 256: "vrsave", 268: "tbl", 269: "tbu", 26: "srr0", 27: "srr1",
             272: "sprg0", 273: "sprg1", 274: "sprg2", 275: "sprg3", 22: "dec", 18: "dsisr", 19: "dar",
             1008: "hid0", 1009: "hid1", 1023: "pir"}


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
    if o in D_LOADSTORE.values() or o in DS58.values() or o in DS62.values():
        reg = "f" if o.startswith(("lf", "stf")) else "r"
        return f"{o} {reg}{f['rd']},{_h(f['d'])}(r{f['ra']})"
    if o in ("addi", "addis", "addic", "addic.", "subfic", "mulli"):
        return f"{o} r{f['rd']},r{f['ra']},{_h(f['simm'])}"
    if o in ("cmpi", "cmpli"):
        return f"{o} cr{f['crfd']},{f['l']},r{f['ra']},{_h(f.get('simm', f.get('uimm', 0)))}"
    if o in ("cmp", "cmpl"):
        return f"{o} cr{f['crfd']},{f['l']},r{f['ra']},r{f['rb']}"
    if o in ("ori", "oris", "xori", "xoris", "andi.", "andis."):
        return f"{o} r{f['ra']},r{f['rs']},0x{f['uimm']:x}"
    if o in ("rlwinm", "rlwimi"):
        return f"{o}{dot} r{f['ra']},r{f['rs']},{f['sh']},{f['mb']},{f['me']}"
    if o == "rlwnm":
        return f"{o}{dot} r{f['ra']},r{f['rs']},r{f['rb']},{f['mb']},{f['me']}"
    if o in ("rldicl", "rldic", "rldimi"):
        return f"{o}{dot} r{f['ra']},r{f['rs']},{f['sh']},{f['mb']}"
    if o == "rldicr":
        return f"{o}{dot} r{f['ra']},r{f['rs']},{f['sh']},{f['me']}"
    if o in ("rldcl", "rldcr"):
        return f"{o}{dot} r{f['ra']},r{f['rs']},r{f['rb']},{f.get('mb', f.get('me'))}"
    if o in ("srawi", "sradi"):
        return f"{o}{dot} r{f['ra']},r{f['rs']},{f['sh']}"
    if o in OP31_XO.values():
        if o in ("addme", "addze", "neg", "subfme", "subfze"):
            return f"{o}{oe}{dot} r{f['rd']},r{f['ra']}"
        return f"{o}{oe}{dot} r{f['rd']},r{f['ra']},r{f['rb']}"
    if o in LOGIC_X:
        if o in ("cntlzw", "cntlzd", "extsh", "extsb", "extsw"):
            return f"{o}{dot} r{f['ra']},r{f['rs']}"
        return f"{o}{dot} r{f['ra']},r{f['rs']},r{f['rb']}"
    if o in INDEXED_LOAD or o in INDEXED_STORE:
        reg = "f" if o.startswith(("lf", "stf")) else "r"
        return f"{o} {reg}{f.get('rd', f.get('rs'))},r{f['ra']},r{f['rb']}"
    if o in VEC_LOAD31 or o in VEC_STORE31 or o in VX128_1.values():
        return f"{o} v{f.get('vd', f.get('vs'))},r{f['ra']},r{f['rb']}"
    if o in CACHE:
        return f"{o} r{f['ra']},r{f['rb']}"
    if o in ("mfspr", "mtspr", "mftb"):
        return f"{o} r{f['rd']},{SPR_NAMES.get(f['spr'], f['spr'])}"
    if o in CR_LOGIC:
        return f"{o} {f['crbd']},{f['crba']},{f['crbb']}"
    if o in ("mcrf", "mcrfs"):
        return f"{o} cr{f['crfd']},cr{f['crfs']}"
    if o in ("fcmpu", "fcmpo"):
        return f"{o} cr{f['crfd']},f{f['fra']},f{f['frb']}"
    if o in A_USES:
        uses = A_USES[o]
        ops = [f"f{f['frd']}"] + [f"f{f['fr' + u]}" for u in uses]
        return f"{o}{dot} " + ",".join(ops)
    if "frd" in f and "frb" in f:
        return f"{o}{dot} f{f['frd']},f{f['frb']}"
    if o == "mffs":
        return f"{o}{dot} f{f['frd']}"
    if o == "mtfsf":
        return f"{o}{dot} 0x{f['fm']:x},f{f['frb']}"
    if o in ("mfcr", "mfmsr"):
        return f"{o} r{f['rd']}"
    if o in ("mtcrf", "mtocrf", "mfocrf"):
        return f"{o} 0x{f['crm']:x},r{f.get('rs', f.get('rd'))}"
    if o in ("lswi", "stswi"):
        return f"{o} r{f['rd']},r{f['ra']},{f['nb']}"
    if o in ("twi", "tdi"):
        return f"{o} {f['to']},r{f['ra']},{_h(f['simm'])}"
    if o in ("tw", "td"):
        return f"{o} {f['to']},r{f['ra']},r{f['rb']}"
    if o in VECTOR_OPS or o in ("mfvscr", "mtvscr"):
        parts = []
        for k in ("vd", "va", "vb", "vc"):
            if k in f:
                parts.append(f"v{f[k]}")
        for k in ("sh", "uimm", "simm", "perm", "imm", "mask", "z"):
            if k in f:
                parts.append(str(f[k]))
        return f"{o}{dot} " + ",".join(parts)
    if o == "illegal":
        return f".word 0x{i.word:08x}"
    return o + ("" if not f else " " + ",".join(f"{k}={v}" for k, v in f.items() if k not in ("rc", "oe")))
