"""The vector ABI scan's inputs and constants (vector_abi.py): the workspace named on the command line, its image and
catalog, the register masks, and the register-set printers. Reads sys.argv at import."""
import sys, json, struct, collections, time, os, pathlib
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "translator"))
sys.path.insert(0, str(HERE))
from xenontranslate.decode import decode, disasm, VECTOR_OPS
import ppcscan

TH = sys.argv[1]
OUT = sys.argv[2] if len(sys.argv) > 2 and not sys.argv[2].startswith("--") else os.path.join(TH, "vabi")
ij = json.load(open(os.path.join(TH, 'image.json')))
BASE = ij.get('load_address', 0x82000000)
DATA = open(os.path.join(TH, 'image.bin'), 'rb').read()
cat = json.load(open(os.path.join(TH, 'catalog_inputs.json')))
HR = [(lo, hi, name) for name, lo, hi in cat['helper_ranges']]
THUNKS = {e['thunk'] for e in cat['imports'] if e.get('thunk')}
FUNCS = {int(f['addr'], 16): f for f in cat['functions']}


def in_helper(a):
    return any(lo <= a < hi for lo, hi, _ in HR)


ALL = (1 << 128) - 1
ARGS = sum(1 << r for r in range(1, 14))      # v1..v13 tracked; the spec (v2..v13) is a masked view
SPEC = sum(1 << r for r in range(2, 14))
V0_13 = (1 << 14) - 1
V32_63 = sum(1 << r for r in range(32, 64))
VOL = V0_13 | V32_63
NV = ALL & ~VOL
READS_VD = {"vmaddfp128", "vmaddcfp128", "vnmsubfp128", "vsel128", "vrlimi128", "vpkd3d128"}
VEST = {"vrefp", "vrsqrtefp", "vexptefp", "vlogefp", "vrefp128", "vrsqrtefp128", "vexptefp128", "vlogefp128"}
NOREG = {"dst", "dstst", "dss"}
IDIOM = {"vxor", "vxor128", "vandc", "vandc128", "vsububm", "vsubuhm", "vsubuwm", "vcmpequb", "vcmpequh", "vcmpequw", "vcmpequw128"}


def vrw(i):
    op = i.op
    if op in NOREG or (op not in VECTOR_OPS and op not in ("mfvscr", "mtvscr")):
        return 0, 0
    f = i.f
    r = w = 0
    if op in IDIOM and f.get("va") == f.get("vb"):      # vxor v,v,v (zero) / vcmpequw v,v,v (ones): value independent of the register
        return 0, 1 << f["vd"]
    mem = op.startswith("lv") or op.startswith("stv")
    if "vs" in f:
        r |= 1 << f["vs"]
    if "vd" in f:
        w |= 1 << f["vd"]
    if not mem:
        for k in ("va", "vb", "vc"):
            if k in f:
                r |= 1 << f[k]
        if op in READS_VD:
            r |= 1 << f["vd"]
    return r, w


def bits_list(m):
    return [r for r in range(128) if m >> r & 1]


def regs_str(m):
    return ",".join("v%d" % r for r in bits_list(m))
