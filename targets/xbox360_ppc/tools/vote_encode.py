"""A small PowerPC / AltiVec / VMX128 encoder for exactly the mnemonics and operand forms the instruction test corpus
uses (tools/vote_corpus.py), so its cases can be translated without running an outside assembler.

text -> (op, fields) -> word. The op and field names are the decoder's (xenontranslate/decode.py), so the round trip
decode(encode(text)) is checked field by field and by disassembly. The primary opcodes and extended opcodes of the
standard PowerPC and AltiVec instructions are written here from the PowerPC Programming Environments manual (64-bit,
chapter 8) and the AltiVec PEM (chapter 6), NOT taken from decode.py, and table_check() holds them against decode.py's
tables. The VMX128 opcodes (Xenon only; no public manual here) are decode.py's own tables: their only independent anchor
is golden_words.json (words from the game's code on which decode.py agreed with a reference disassembler).

Simplified mnemonics are expanded as the PEM's appendix F defines them (li, lis, mr, not, subi, slwi, srwi, sldi, srdi,
clrldi, cmpw..., mtctr..., beq..., tw<cond>...). Assembler operand order where it differs from field order: fmadd-family
"fD,fA,fC,fB", fmul "fD,fA,fC", fsel "fD,fA,fC,fB", vmaddfp / vnmsubfp "vD,vA,vC,vB", vmaddfp128 / vnmsubfp128 / vsel128
"vD,vA,vB,vD", vmaddcfp128 "vD,vA,vD,vB" (the repeated vD must match), vupkd3d128 "vD,vB,UIMM" (the whole 5-bit field),
vpkd3d128 "vD,vB,type,mask,z", vrlimi128 "vD,vB,IMM,z", vpermwi128 "vD,vB,PERM".

Nearest existing: translator/tests/test_decode.py MANUAL (hand-worked words, no encoder) and decode.py (words to fields
only); no encoder existed in the repo (git grep "def encode": none for PowerPC).
"""
import re
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "translator"))
from xenontranslate import decode as D  # noqa: E402

# ------------------------------------------------------------------ opcodes written from the manuals (not decode.py)
DFORM = {"lwz": 32, "lwzu": 33, "lbz": 34, "lbzu": 35, "stw": 36, "stwu": 37, "stb": 38, "stbu": 39, "lhz": 40,
         "lhzu": 41, "lha": 42, "lhau": 43, "sth": 44, "sthu": 45, "lmw": 46, "stmw": 47, "lfs": 48, "lfsu": 49,
         "lfd": 50, "lfdu": 51, "stfs": 52, "stfsu": 53, "stfd": 54, "stfdu": 55}
DS = {"ld": (58, 0), "ldu": (58, 1), "lwa": (58, 2), "std": (62, 0), "stdu": (62, 1)}
IMM = {"addi": 14, "addis": 15, "addic": 12, "addic.": 13, "subfic": 8, "mulli": 7, "twi": 3, "tdi": 2}
LOGI = {"ori": 24, "oris": 25, "xori": 26, "xoris": 27, "andi.": 28, "andis.": 29}
XO31 = {"add": 266, "addc": 10, "adde": 138, "addme": 234, "addze": 202, "divd": 489, "divdu": 457, "divw": 491,
        "divwu": 459, "mulhd": 73, "mulhdu": 9, "mulhw": 75, "mulhwu": 11, "mulld": 233, "mullw": 235, "neg": 104,
        "subf": 40, "subfc": 8, "subfe": 136, "subfme": 232, "subfze": 200}
XO_UNARY = {"addme", "addze", "neg", "subfme", "subfze"}
X31 = {"and": 28, "andc": 60, "cmp": 0, "cmpl": 32, "cntlzd": 58, "cntlzw": 26, "dcbf": 86, "dcbst": 54, "dcbt": 278,
       "dcbtst": 246, "dcbz": 1014, "eieio": 854, "eqv": 284, "extsb": 954, "extsh": 922, "extsw": 986, "icbi": 982,
       "lbzux": 119, "lbzx": 87, "ldarx": 84, "ldbrx": 532, "ldux": 53, "ldx": 21, "lfdux": 631, "lfdx": 599,
       "lfsux": 567, "lfsx": 535, "lhaux": 375, "lhax": 343, "lhbrx": 790, "lhzux": 311, "lhzx": 279, "lvebx": 7,
       "lvehx": 39, "lvewx": 71, "lvsl": 6, "lvsr": 38, "lvx": 103, "lvxl": 359, "lwarx": 20, "lwaux": 373,
       "lwax": 341, "lwbrx": 534, "lwzux": 55, "lwzx": 23, "mcrxr": 512, "mfcr": 19, "mfmsr": 83, "mfspr": 339,
       "mftb": 371, "mtcrf": 144, "mtmsr": 146, "mtmsrd": 178, "mtspr": 467, "nand": 476, "nor": 124, "or": 444,
       "orc": 412, "slw": 24, "sld": 27, "sraw": 792, "srawi": 824, "srad": 794, "srw": 536, "srd": 539,
       "stbux": 247, "stbx": 215, "stdbrx": 660, "stdcx.": 214, "stdux": 181, "stdx": 149, "stfdux": 759,
       "stfdx": 727, "stfiwx": 983, "stfsux": 695, "stfsx": 663, "sthbrx": 918, "sthux": 439, "sthx": 407,
       "stvebx": 135, "stvehx": 167, "stvewx": 199, "stvx": 231, "stvxl": 487, "stwbrx": 662, "stwcx.": 150,
       "stwux": 183, "stwx": 151, "sync": 598, "td": 68, "tw": 4, "xor": 316, "lvlx": 519, "lvrx": 551,
       "lvlxl": 775, "lvrxl": 807, "stvlx": 647, "stvrx": 679, "stvlxl": 903, "stvrxl": 935}
LOGIC = {"and", "andc", "eqv", "nand", "nor", "or", "orc", "slw", "sld", "sraw", "srad", "srw", "srd", "xor"}
LOGIC1 = {"cntlzd", "cntlzw", "extsb", "extsh", "extsw"}
LOADX = {"lbzux", "lbzx", "ldarx", "ldbrx", "ldux", "ldx", "lfdux", "lfdx", "lfsux", "lfsx", "lhaux", "lhax", "lhbrx",
         "lhzux", "lhzx", "lwarx", "lwaux", "lwax", "lwbrx", "lwzux", "lwzx"}
STOREX = {"stbux", "stbx", "stdbrx", "stdcx.", "stdux", "stdx", "stfdux", "stfdx", "stfiwx", "stfsux", "stfsx",
          "sthbrx", "sthux", "sthx", "stwbrx", "stwcx.", "stwux", "stwx"}
VLS31 = {"lvebx", "lvehx", "lvewx", "lvsl", "lvsr", "lvx", "lvxl", "lvlx", "lvrx", "lvlxl", "lvrxl", "stvebx",
         "stvehx", "stvewx", "stvx", "stvxl", "stvlx", "stvrx", "stvlxl", "stvrxl"}
CACHE = {"dcbf", "dcbst", "dcbt", "dcbtst", "dcbz", "icbi"}
X19 = {"mcrf": 0, "crnor": 33, "crandc": 129, "isync": 150, "crxor": 193, "crnand": 225, "crand": 257, "creqv": 289,
       "crorc": 417, "cror": 449, "bclr": 16, "bcctr": 528}
A59 = {"fdivs": 18, "fsubs": 20, "fadds": 21, "fsqrts": 22, "fres": 24, "fmuls": 25, "fmsubs": 28, "fmadds": 29,
       "fnmsubs": 30, "fnmadds": 31}
A63 = {"fdiv": 18, "fsub": 20, "fadd": 21, "fsqrt": 22, "fsel": 23, "fmul": 25, "frsqrte": 26, "fmsub": 28,
       "fmadd": 29, "fnmsub": 30, "fnmadd": 31}
X63 = {"fcmpu": 0, "frsp": 12, "fctiw": 14, "fctiwz": 15, "fcmpo": 32, "mtfsb1": 38, "fneg": 40, "mcrfs": 64,
       "mtfsb0": 70, "fmr": 72, "mtfsfi": 134, "fnabs": 136, "fabs": 264, "mffs": 583, "mtfsf": 711, "fctid": 814,
       "fctidz": 815, "fcfid": 846}
VA = {"vmhaddshs": 32, "vmhraddshs": 33, "vmladduhm": 34, "vmsumubm": 36, "vmsummbm": 37, "vmsumuhm": 38,
      "vmsumuhs": 39, "vmsumshm": 40, "vmsumshs": 41, "vsel": 42, "vperm": 43, "vsldoi": 44, "vmaddfp": 46,
      "vnmsubfp": 47}
VC = {"vcmpequb": 6, "vcmpequh": 70, "vcmpequw": 134, "vcmpeqfp": 198, "vcmpgefp": 454, "vcmpgtub": 518,
      "vcmpgtuh": 582, "vcmpgtuw": 646, "vcmpgtfp": 710, "vcmpgtsb": 774, "vcmpgtsh": 838, "vcmpgtsw": 902,
      "vcmpbfp": 966}
VX = {"vaddubm": 0, "vadduhm": 64, "vadduwm": 128, "vaddcuw": 384, "vaddubs": 512, "vadduhs": 576, "vadduws": 640,
      "vaddsbs": 768, "vaddshs": 832, "vaddsws": 896, "vsububm": 1024, "vsubuhm": 1088, "vsubuwm": 1152,
      "vsubcuw": 1408, "vsububs": 1536, "vsubuhs": 1600, "vsubuws": 1664, "vsubsbs": 1792, "vsubshs": 1856,
      "vsubsws": 1920, "vmaxub": 2, "vmaxuh": 66, "vmaxuw": 130, "vmaxsb": 258, "vmaxsh": 322, "vmaxsw": 386,
      "vminub": 514, "vminuh": 578, "vminuw": 642, "vminsb": 770, "vminsh": 834, "vminsw": 898, "vavgub": 1026,
      "vavguh": 1090, "vavguw": 1154, "vavgsb": 1282, "vavgsh": 1346, "vavgsw": 1410, "vrlb": 4, "vrlh": 68,
      "vrlw": 132, "vslb": 260, "vslh": 324, "vslw": 388, "vsl": 452, "vsrb": 516, "vsrh": 580, "vsrw": 644,
      "vsr": 708, "vsrab": 772, "vsrah": 836, "vsraw": 900, "vand": 1028, "vandc": 1092, "vor": 1156, "vxor": 1220,
      "vnor": 1284, "vslo": 1036, "vsro": 1100, "vaddfp": 10, "vsubfp": 74, "vrefp": 266, "vrsqrtefp": 330,
      "vexptefp": 394, "vlogefp": 458, "vrfin": 522, "vrfiz": 586, "vrfip": 650, "vrfim": 714, "vcfux": 778,
      "vcfsx": 842, "vctuxs": 906, "vctsxs": 970, "vmaxfp": 1034, "vminfp": 1098, "vmrghb": 12, "vmrghh": 76,
      "vmrghw": 140, "vmrglb": 268, "vmrglh": 332, "vmrglw": 396, "vspltb": 524, "vsplth": 588, "vspltw": 652,
      "vspltisb": 780, "vspltish": 844, "vspltisw": 908, "vpkuhum": 14, "vpkuwum": 78, "vpkuhus": 142,
      "vpkuwus": 206, "vpkshus": 270, "vpkswus": 334, "vpkshss": 398, "vpkswss": 462, "vpkpx": 782,
      "vupkhsb": 526, "vupkhsh": 590, "vupklsb": 654, "vupklsh": 718, "vupkhpx": 846, "vupklpx": 974,
      "mfvscr": 1540, "mtvscr": 1604}
VX_UN = {"vrefp", "vrsqrtefp", "vexptefp", "vlogefp", "vrfin", "vrfiz", "vrfip", "vrfim", "vupkhsb", "vupkhsh",
         "vupklsb", "vupklsh", "vupkhpx", "vupklpx"}
VX_UI = {"vspltb", "vsplth", "vspltw", "vcfux", "vcfsx", "vctuxs", "vctsxs"}
VX_SI = {"vspltisb", "vspltish", "vspltisw"}
# VMX128: decode.py's tables (see the docstring)
V128_LS = {v: k for k, v in D.VX128_1.items()}
V128_5 = {v: k for k, v in D.VX128_5.items()}
V128_6R = {v: k for k, v in D.VX128_6R.items()}
V128_6 = {v: k for k, v in D.VX128_6.items()}
V128_6U = {v: k for k, v in D.VX128_6U.items()}
SPR = {"xer": 1, "lr": 8, "ctr": 9}
TO = {"lt": 16, "le": 20, "eq": 4, "ge": 12, "gt": 8, "nl": 12, "ne": 24, "ng": 20, "llt": 2, "lle": 6, "lge": 5,
      "lgt": 1, "lnl": 5, "lng": 6}
BCOND = {"lt": (12, 0), "ge": (4, 0), "nl": (4, 0), "gt": (12, 1), "le": (4, 1), "ng": (4, 1), "eq": (12, 2),
         "ne": (4, 2), "so": (12, 3), "ns": (4, 3)}


class EncodeError(Exception):
    pass


def num(t):
    t = t.strip()
    neg = t.startswith("-")
    t = t[1:] if neg else t
    v = int(t, 16) if t.lower().startswith("0x") else int(t, 10)
    return -v if neg else v


def reg(t, k):
    m = re.fullmatch(k + r"(\d+)", t.strip())
    if not m:
        if k in ("r", "cr") and re.fullmatch(r"\d+", t.strip()):
            return int(t)
        raise EncodeError(f"expected {k}N, got {t!r}")
    return int(m.group(1))


def memop(t):
    m = re.fullmatch(r"(-?(?:0x[0-9A-Fa-f]+|\d+))\((r\d+)\)", t.replace(" ", ""))
    if not m:
        raise EncodeError(f"expected d(rA), got {t!r}")
    return num(m.group(1)), reg(m.group(2), "r")


def split(text):
    text = text.strip()
    p = text.split(None, 1)
    ops = [o.strip() for o in p[1].split(",")] if len(p) > 1 else []
    return p[0], ops


def expand(m, o):
    """Simplified mnemonics -> (base mnemonic, operands)."""
    if m in ("li", "lis"):
        return ("addi" if m == "li" else "addis"), [o[0], "r0", o[1]]
    if m in ("subi", "subis"):
        return ("addi" if m == "subi" else "addis"), [o[0], o[1], str(-num(o[2]))]
    if m in ("mr", "mr."):
        return "or" + m[2:], [o[0], o[1], o[1]]
    if m in ("not", "not."):
        return "nor" + m[3:], [o[0], o[1], o[1]]
    if m in ("slwi", "srwi", "sldi", "srdi", "clrldi"):
        n = num(o[2])
        f = {"slwi": ("rlwinm", [n, 0, 31 - n]), "srwi": ("rlwinm", [(32 - n) % 32, n, 31]),
             "sldi": ("rldicr", [n, 63 - n]), "srdi": ("rldicl", [(64 - n) % 64, n]), "clrldi": ("rldicl", [0, n])}[m]
        return f[0], [o[0], o[1]] + [str(x) for x in f[1]]
    mm = re.fullmatch(r"cmp(l?)(w|d)(i?)", m)
    if mm:
        cr = o[0] if len(o) == 3 else "cr0"
        rest = o[1:] if len(o) == 3 else o
        return f"cmp{mm.group(1)}{mm.group(3)}", [cr, "1" if mm.group(2) == "d" else "0"] + rest
    if m in ("mtctr", "mtlr", "mtxer"):
        return "mtspr", [m[2:], o[0]]
    if m in ("mfctr", "mflr", "mfxer"):
        return "mfspr", [o[0], m[2:]]
    if m in ("blr", "blrl", "bctr", "bctrl"):
        return {"blr": "bclr", "blrl": "bclrl", "bctr": "bcctr", "bctrl": "bcctrl"}[m], ["20", "0"]
    mm = re.fullmatch(r"b(lt|ge|nl|gt|le|ng|eq|ne|so|ns)(l?)", m)
    if mm:
        bo, bit = BCOND[mm.group(1)]
        cr = reg(o[0], "cr") if len(o) == 2 else 0
        return "bc" + mm.group(2), [str(bo), str(4 * cr + bit), o[-1]]
    mm = re.fullmatch(r"(tw|td)(lt|le|eq|ge|gt|nl|ne|ng|llt|lle|lge|lgt|lnl|lng)(i?)", m)
    if mm:
        return mm.group(1) + mm.group(3), [str(TO[mm.group(2)])] + o
    if m == "lwsync":
        return "sync", ["1"]
    return m, o


def b(v, a, z):
    """v placed in big-endian bits a..z."""
    n = z - a + 1
    return (v & ((1 << n) - 1)) << (31 - z)


def vd128(v):
    return ((v & 31) << 21) | (((v >> 5) & 3) << 2)


def va128(v):
    return ((v & 31) << 16) | (((v >> 5) & 1) << 5) | (((v >> 6) & 1) << 10)


def vb128(v):
    return ((v & 31) << 11) | ((v >> 5) & 3)


def target(t, pc, labels):
    t = t.strip()
    if t.startswith("."):
        return num(t[1:].replace("+", "")) if t != "." else 0
    if labels and t in labels:
        return labels[t] - pc
    return num(t) - pc


def encode(text, pc=0, labels=None):
    """(word, op, fields): fields are the decoder's names and values this text sets."""
    m0, o = split(text)
    if m0 == ".long":
        w = num(o[0]) & 0xFFFFFFFF
        return w, None, {}
    m, o = expand(m0, o)
    rc = 1 if m.endswith(".") and m not in ("andi.", "andis.", "addic.", "stwcx.", "stdcx.") else 0
    base = m[:-1] if rc else m
    oe = 0
    if base not in XO31 and base.endswith("o") and base[:-1] in XO31:
        base, oe = base[:-1], 1
    f = {}
    R = lambda i: reg(o[i], "r")  # noqa: E731
    F_ = lambda i: reg(o[i], "f")  # noqa: E731
    V = lambda i: reg(o[i], "v")  # noqa: E731
    if base in DFORM:
        d, ra = memop(o[1])
        f = dict(rd=reg(o[0], "f" if base.startswith(("lf", "stf")) else "r"), ra=ra, d=d)
        return b(DFORM[base], 0, 5) | b(f["rd"], 6, 10) | b(ra, 11, 15) | b(d, 16, 31), base, f
    if base in DS:
        d, ra = memop(o[1])
        p, x = DS[base]
        f = dict(rd=R(0), ra=ra, d=d)
        return b(p, 0, 5) | b(f["rd"], 6, 10) | b(ra, 11, 15) | b(d >> 2, 16, 29) | x, base, f
    if base in IMM:
        if base in ("twi", "tdi"):
            f = dict(to=num(o[0]), ra=R(1), simm=num(o[2]))
            return b(IMM[base], 0, 5) | b(f["to"], 6, 10) | b(f["ra"], 11, 15) | b(f["simm"], 16, 31), base, f
        f = dict(rd=R(0), ra=R(1), simm=D.sext(num(o[2]), 16))
        return b(IMM[base], 0, 5) | b(f["rd"], 6, 10) | b(f["ra"], 11, 15) | b(f["simm"], 16, 31), base, f
    if base in LOGI:
        f = dict(ra=R(0), rs=R(1), uimm=num(o[2]) & 0xFFFF)
        return b(LOGI[base], 0, 5) | b(f["rs"], 6, 10) | b(f["ra"], 11, 15) | f["uimm"], base, f
    if base in ("cmp", "cmpl", "cmpi", "cmpli"):
        f = dict(crfd=reg(o[0], "cr"), l=num(o[1]), ra=R(2))
        w = b(f["crfd"], 6, 8) | b(f["l"], 10, 10) | b(f["ra"], 11, 15)
        if base in ("cmp", "cmpl"):
            f["rb"] = R(3)
            return w | b(31, 0, 5) | b(f["rb"], 16, 20) | b(X31[base], 21, 30), base, f
        if base == "cmpi":
            f["simm"] = D.sext(num(o[3]), 16)
            return w | b(11, 0, 5) | b(f["simm"], 16, 31), base, f
        f["uimm"] = num(o[3]) & 0xFFFF
        return w | b(10, 0, 5) | f["uimm"], base, f
    if base in XO31:
        f = dict(rd=R(0), ra=R(1), rb=0 if base in XO_UNARY else R(2), rc=rc, oe=oe)
        return (b(31, 0, 5) | b(f["rd"], 6, 10) | b(f["ra"], 11, 15) | b(f["rb"], 16, 20) | b(oe, 21, 21)
                | b(XO31[base], 22, 30) | rc), base, f
    if base in ("srawi", "sradi"):
        f = dict(ra=R(0), rs=R(1), sh=num(o[2]), rc=rc)
        w = b(31, 0, 5) | b(f["rs"], 6, 10) | b(f["ra"], 11, 15) | b(f["sh"] & 31, 16, 20) | rc
        if base == "srawi":
            return w | b(824, 21, 30), base, f
        return w | b(413, 21, 29) | b(f["sh"] >> 5, 30, 30), base, f
    if base in ("rlwinm", "rlwimi", "rlwnm"):
        f = dict(ra=R(0), rs=R(1), mb=num(o[3]), me=num(o[4]), rc=rc)
        f["rb" if base == "rlwnm" else "sh"] = R(2) if base == "rlwnm" else num(o[2])
        p = {"rlwimi": 20, "rlwinm": 21, "rlwnm": 23}[base]
        return (b(p, 0, 5) | b(f["rs"], 6, 10) | b(f["ra"], 11, 15) | b(f.get("rb", f.get("sh")), 16, 20)
                | b(f["mb"], 21, 25) | b(f["me"], 26, 30) | rc), base, f
    if base in ("rldicl", "rldicr", "rldic", "rldimi", "rldcl", "rldcr"):
        mx = num(o[3])
        f = dict(ra=R(0), rs=R(1), rc=rc)
        f["me" if base in ("rldicr", "rldcr") else "mb"] = mx
        w = b(30, 0, 5) | b(f["rs"], 6, 10) | b(f["ra"], 11, 15) | b(mx & 31, 21, 25) | b(mx >> 5, 26, 26) | rc
        if base in ("rldcl", "rldcr"):
            f["rb"] = R(2)
            return w | b(f["rb"], 16, 20) | b(8 if base == "rldcl" else 9, 27, 30), base, f
        f["sh"] = num(o[2])
        xo = ("rldicl", "rldicr", "rldic", "rldimi").index(base)
        return w | b(f["sh"] & 31, 16, 20) | b(xo, 27, 29) | b(f["sh"] >> 5, 30, 30), base, f
    if base in LOGIC or base in LOGIC1:
        f = dict(ra=R(0), rs=R(1), rb=R(2) if base in LOGIC else 0, rc=rc)
        return (b(31, 0, 5) | b(f["rs"], 6, 10) | b(f["ra"], 11, 15) | b(f["rb"], 16, 20) | b(X31[base], 21, 30)
                | rc), base, f
    if base in LOADX or base in STOREX:
        k = "f" if base.startswith(("lf", "stf")) else "r"
        f = {("rd" if base in LOADX else "rs"): reg(o[0], k), "ra": R(1), "rb": R(2)}
        if base in STOREX:
            f["rc"] = 1 if base.endswith(".") else 0
        return (b(31, 0, 5) | b(reg(o[0], k), 6, 10) | b(f["ra"], 11, 15) | b(f["rb"], 16, 20) | b(X31[base], 21, 30)
                | (1 if base.endswith(".") else 0)), base, f
    if base in VLS31:
        f = {("vs" if base.startswith("st") else "vd"): V(0), "ra": R(1), "rb": R(2)}
        return b(31, 0, 5) | b(V(0), 6, 10) | b(f["ra"], 11, 15) | b(f["rb"], 16, 20) | b(X31[base], 21, 30), base, f
    if base in CACHE or base == "dcbz128":
        f = dict(ra=R(0), rb=R(1))
        return (b(31, 0, 5) | b(1 if base == "dcbz128" else 0, 10, 10) | b(f["ra"], 11, 15) | b(f["rb"], 16, 20)
                | b(X31["dcbz" if base == "dcbz128" else base], 21, 30)), base, f
    if base in ("tw", "td"):
        f = dict(to=num(o[0]), ra=R(1), rb=R(2))
        return b(31, 0, 5) | b(f["to"], 6, 10) | b(f["ra"], 11, 15) | b(f["rb"], 16, 20) | b(X31[base], 21, 30), base, f
    if base in ("sync", "eieio"):
        lv = num(o[0]) if o else 0
        return b(31, 0, 5) | b(lv, 9, 10) | b(X31[base], 21, 30), ("lwsync" if lv == 1 else base), {}
    if base in ("mfspr", "mtspr", "mftb"):
        rd, s = (R(0), o[1]) if base != "mtspr" else (R(1), o[0])
        n = SPR[s] if s in SPR else num(s)
        f = dict(rd=rd, spr=n)
        return b(31, 0, 5) | b(rd, 6, 10) | b(n & 31, 11, 15) | b(n >> 5, 16, 20) | b(X31[base], 21, 30), base, f
    if base in ("mfcr", "mfmsr"):
        f = dict(rd=R(0))
        return b(31, 0, 5) | b(f["rd"], 6, 10) | b(X31[base], 21, 30), base, f
    if base == "mfocrf":
        f = dict(rd=R(0), crm=num(o[1]))
        return b(31, 0, 5) | b(f["rd"], 6, 10) | b(1, 11, 11) | b(f["crm"], 12, 19) | b(19, 21, 30), base, f
    if base == "mtcrf":
        f = dict(rs=R(1), crm=num(o[0]))
        return b(31, 0, 5) | b(f["rs"], 6, 10) | b(f["crm"], 12, 19) | b(144, 21, 30), base, f
    if base in ("mtmsr", "mtmsrd"):
        f = dict(rs=R(0), l=num(o[1]) if len(o) > 1 else 0)
        return b(31, 0, 5) | b(f["rs"], 6, 10) | b(f["l"], 15, 15) | b(X31[base], 21, 30), base, f
    if base == "mcrxr":
        f = dict(crfd=reg(o[0], "cr"))
        return b(31, 0, 5) | b(f["crfd"], 6, 8) | b(512, 21, 30), base, f
    if base in ("mcrf", "mcrfs"):
        f = dict(crfd=reg(o[0], "cr"), crfs=reg(o[1], "cr"))
        p, x = (19, 0) if base == "mcrf" else (63, 64)
        return b(p, 0, 5) | b(f["crfd"], 6, 8) | b(f["crfs"], 11, 13) | b(x, 21, 30), base, f
    if base in X19 and base.startswith("cr"):
        f = dict(crbd=num(o[0]), crba=num(o[1]), crbb=num(o[2]))
        return b(19, 0, 5) | b(f["crbd"], 6, 10) | b(f["crba"], 11, 15) | b(f["crbb"], 16, 20) | b(X19[base], 21, 30), base, f
    if base == "isync":
        return b(19, 0, 5) | b(150, 21, 30), base, {}
    if base in ("bclr", "bcctr", "bclrl", "bcctrl"):
        lk = 1 if base in ("bclrl", "bcctrl") else 0
        op = base[:-1] if lk else base
        f = dict(bo=num(o[0]), bi=num(o[1]), lk=lk)
        return b(19, 0, 5) | b(f["bo"], 6, 10) | b(f["bi"], 11, 15) | b(X19[op], 21, 30) | lk, op, f
    if base in ("b", "bl"):
        off = target(o[0], pc, labels)
        lk = 1 if base == "bl" else 0
        return b(18, 0, 5) | (off & 0x03FFFFFC) | lk, "b", dict(target=(pc + off) & 0xFFFFFFFF, aa=0, lk=lk)
    if base in ("bc", "bcl"):
        off = target(o[2], pc, labels)
        lk = 1 if base == "bcl" else 0
        f = dict(bo=num(o[0]), bi=num(o[1]), target=(pc + off) & 0xFFFFFFFF, aa=0, lk=lk)
        return b(16, 0, 5) | b(f["bo"], 6, 10) | b(f["bi"], 11, 15) | (off & 0xFFFC) | lk, "bc", f
    if base in A59 or base in A63:
        p, x = (59, A59[base]) if base in A59 else (63, A63[base])
        core = base[:-1] if base in A59 else base
        if core in ("fadd", "fsub", "fdiv"):
            f = dict(frd=F_(0), fra=F_(1), frb=F_(2), frc=0)
        elif core == "fmul":
            f = dict(frd=F_(0), fra=F_(1), frb=0, frc=F_(2))
        elif core in ("fsqrt", "fre", "frsqrte"):
            f = dict(frd=F_(0), fra=0, frb=F_(1), frc=0)
        else:                                   # fmadd family, fsel: fD,fA,fC,fB
            f = dict(frd=F_(0), fra=F_(1), frc=F_(2), frb=F_(3))
        f["rc"] = rc
        return (b(p, 0, 5) | b(f["frd"], 6, 10) | b(f["fra"], 11, 15) | b(f["frb"], 16, 20) | b(f["frc"], 21, 25)
                | b(x, 26, 30) | rc), base, f
    if base in X63:
        x = X63[base]
        w = b(63, 0, 5) | b(x, 21, 30) | rc
        if base in ("fcmpu", "fcmpo"):
            f = dict(crfd=reg(o[0], "cr"), fra=F_(1), frb=F_(2))
            return w | b(f["crfd"], 6, 8) | b(f["fra"], 11, 15) | b(f["frb"], 16, 20), base, f
        if base == "mffs":
            f = dict(frd=F_(0), rc=rc)
            return w | b(f["frd"], 6, 10), base, f
        if base == "mtfsf":
            f = dict(fm=num(o[0]), frb=F_(1), rc=rc)
            return w | b(f["fm"], 7, 14) | b(f["frb"], 16, 20), base, f
        if base == "mtfsfi":
            f = dict(crfd=reg(o[0], "cr"), imm=num(o[1]), rc=rc)
            return w | b(f["crfd"], 6, 8) | b(f["imm"], 16, 19), base, f
        if base in ("mtfsb0", "mtfsb1"):
            f = dict(crbd=num(o[0]), rc=rc)
            return w | b(f["crbd"], 6, 10), base, f
        f = dict(frd=F_(0), frb=F_(1), rc=rc)
        return w | b(f["frd"], 6, 10) | b(f["frb"], 16, 20), base, f
    return encode_vector(base, rc, o, V)


def encode_vector(base, rc, o, V):
    if base in VA:
        w = b(4, 0, 5) | b(VA[base], 26, 31)
        if base == "vsldoi":
            f = dict(vd=V(0), va=V(1), vb=V(2), sh=num(o[3]))
            return w | b(f["vd"], 6, 10) | b(f["va"], 11, 15) | b(f["vb"], 16, 20) | b(f["sh"], 22, 25), base, f
        if base in ("vmaddfp", "vnmsubfp"):
            f = dict(vd=V(0), va=V(1), vc=V(2), vb=V(3))
        else:
            f = dict(vd=V(0), va=V(1), vb=V(2), vc=V(3))
        return w | b(f["vd"], 6, 10) | b(f["va"], 11, 15) | b(f["vb"], 16, 20) | b(f["vc"], 21, 25), base, f
    if base in VC:
        f = dict(vd=V(0), va=V(1), vb=V(2), rc=rc)
        return (b(4, 0, 5) | b(f["vd"], 6, 10) | b(f["va"], 11, 15) | b(f["vb"], 16, 20) | b(rc, 21, 21)
                | b(VC[base], 22, 31)), base, f
    if base in VX:
        w = b(4, 0, 5) | b(VX[base], 21, 31)
        if base == "mfvscr":
            f = dict(vd=V(0))
        elif base == "mtvscr":
            f = dict(vb=V(0))
        elif base in VX_UN:
            f = dict(vd=V(0), vb=V(1))
        elif base in VX_UI:
            f = dict(vd=V(0), vb=V(1), uimm=num(o[2]))
        elif base in VX_SI:
            f = dict(vd=V(0), simm=num(o[1]))
            return w | b(f["vd"], 6, 10) | b(f["simm"], 11, 15), base, f
        else:
            f = dict(vd=V(0), va=V(1), vb=V(2))
        return w | b(f.get("vd", 0), 6, 10) | b(f.get("va", f.get("uimm", 0)), 11, 15) | b(f.get("vb", 0), 16, 20), base, f
    if base in V128_LS:
        f = {("vs" if base.startswith("st") else "vd"): V(0), "ra": reg(o[1], "r"), "rb": reg(o[2], "r")}
        return b(4, 0, 5) | vd128(V(0)) | (f["ra"] << 16) | (f["rb"] << 11) | V128_LS[base], base, f
    if base == "vsldoi128":
        f = dict(vd=V(0), va=V(1), vb=V(2), sh=num(o[3]))
        return b(4, 0, 5) | vd128(f["vd"]) | va128(f["va"]) | vb128(f["vb"]) | (f["sh"] & 15) << 6 | 0x10, base, f
    if base == "vperm128":
        f = dict(vd=V(0), va=V(1), vb=V(2), vc=V(3))
        return b(5, 0, 5) | vd128(f["vd"]) | va128(f["va"]) | vb128(f["vb"]) | (f["vc"] & 7) << 6, base, f
    if base in V128_5:
        if base in ("vmaddfp128", "vnmsubfp128", "vsel128") and len(o) == 4:
            if V(3) != V(0):
                raise EncodeError(f"{base}: the fourth operand is vD")
        if base == "vmaddcfp128" and len(o) == 4:
            if V(2) != V(0):
                raise EncodeError(f"{base}: the third operand is vD")
            o = [o[0], o[1], o[3]]
        f = dict(vd=V(0), va=V(1), vb=reg(o[2], "v"))
        return b(5, 0, 5) | vd128(f["vd"]) | va128(f["va"]) | vb128(f["vb"]) | V128_5[base], base, f
    if base in V128_6R:
        f = dict(vd=V(0), va=V(1), vb=V(2), rc=rc)
        return b(6, 0, 5) | vd128(f["vd"]) | va128(f["va"]) | vb128(f["vb"]) | V128_6R[base] | rc << 6, base, f
    if base in V128_6:
        if base in D.VX128_6_UNARY:     # the VA field is fixed at 0x60 (golden_words.json: 1bc0ffa5, 1bc0b7e4)
            f = dict(vd=V(0), vb=V(1))
            return b(6, 0, 5) | vd128(f["vd"]) | va128(0x60) | vb128(f["vb"]) | V128_6[base], base, f
        f = dict(vd=V(0), va=V(1), vb=V(2))
        return b(6, 0, 5) | vd128(f["vd"]) | va128(f["va"]) | vb128(f["vb"]) | V128_6[base], base, f
    if base in V128_6U:
        w = b(6, 0, 5) | V128_6U[base]
        if base == "vspltisw128":
            f = dict(vd=V(0), simm=D.sext(num(o[1]), 5))
            return w | vd128(f["vd"]) | (f["simm"] & 31) << 16, base, f
        f = dict(vd=V(0), vb=V(1))
        u = num(o[2]) if len(o) > 2 else 0
        if base == "vupkd3d128":
            f.update(imm=u >> 2, mask=u & 3)
        elif len(o) > 2:
            f["uimm"] = u
        return w | vd128(f["vd"]) | vb128(f["vb"]) | (u & 31) << 16, base, f
    if base == "vpermwi128":
        f = dict(vd=V(0), vb=V(1), perm=num(o[2]))
        p = f["perm"]
        return b(6, 0, 5) | vd128(f["vd"]) | vb128(f["vb"]) | (p & 31) << 16 | ((p >> 5) & 7) << 6 | 0x210, base, f
    if base == "vrlimi128":
        f = dict(vd=V(0), vb=V(1), imm=num(o[2]), z=num(o[3]))
        return b(6, 0, 5) | vd128(f["vd"]) | vb128(f["vb"]) | (f["imm"] & 31) << 16 | (f["z"] & 3) << 6 | 0x710, base, f
    if base == "vpkd3d128":
        f = dict(vd=V(0), vb=V(1), imm=num(o[2]), mask=num(o[3]), z=num(o[4]))
        u = (f["imm"] << 2) | f["mask"]
        return b(6, 0, 5) | vd128(f["vd"]) | vb128(f["vb"]) | (u & 31) << 16 | (f["z"] & 3) << 6 | 0x610, base, f
    raise EncodeError(f"mnemonic {base!r} not covered by the encoder")
