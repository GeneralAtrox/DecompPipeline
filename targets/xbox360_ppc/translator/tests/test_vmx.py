"""Vector helper tests: every xv_ helper compiled with MSVC (/O1 /fp:strict /W4 /WX, as DP builds) into one test
program with a small guest memory, and run on cases worked by hand from the AltiVec manual's operation descriptions
and the VMX128 forms' definitions. A case prints its four result words; each is compared with the expected ones.

    python -m unittest tests.test_vmx
"""
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "tools"))
from xenontranslate import vmx  # noqa: E402

PRELUDE = r"""
#include <stdio.h>
#include <stdint.h>
#include <math.h>
typedef uint8_t u8; typedef uint16_t u16; typedef uint32_t u32; typedef uint64_t u64;
typedef int8_t s8; typedef int16_t s16; typedef int32_t s32; typedef int64_t s64;
typedef float f32; typedef double f64;
static u8 mem[64];
static u32 rd8(u32 a) { return mem[a & 63u]; }
static u32 rd16(u32 a) { return ((u32)mem[a & 63u] << 8) | mem[(a + 1u) & 63u]; }
static u32 rd32(u32 a) { return (rd16(a) << 16) | rd16(a + 2u); }
static void wr8(u32 a, u8 v) { mem[a & 63u] = v; }
static void wr16(u32 a, u16 v) { wr8(a, (u8)(v >> 8)); wr8(a + 1u, (u8)v); }
static void wr32(u32 a, u32 v) { wr16(a, (u16)(v >> 16)); wr16(a + 2u, (u16)v); }
static f64 ppc_fsqrt(f64 x) { return sqrt(x); }
"""

MAIN = r"""
static xv V4(u32 a, u32 b, u32 c, u32 d) { xv r; r.w[0] = a; r.w[1] = b; r.w[2] = c; r.w[3] = d; return r; }
static void P(const char *n, xv r) { printf("%s %08x %08x %08x %08x\n", n, r.w[0], r.w[1], r.w[2], r.w[3]); }
int main(void)
{
    xv inc = V4(0x00010203u, 0x04050607u, 0x08090a0bu, 0x0c0d0e0fu);
    xv inc2 = V4(0x10111213u, 0x14151617u, 0x18191a1bu, 0x1c1d1e1fu);
    xv w = V4(0x11111111u, 0x22222222u, 0x33333333u, 0x44444444u);
    xv d = V4(0xaaaaaaaau, 0xbbbbbbbbu, 0xccccccccu, 0xddddddddu);
    xv z = V4(0, 0, 0, 0);
    int k;
    for (k = 0; k < 64; k++) mem[k] = (u8)(0x40 + k);
    P("vperm", xv_vperm(inc, inc2, V4(0x1f1e1d1cu, 0x1b1a1918u, 0x00011011u, 0x0f0e0302u)));
    P("vsldoi", xv_vsldoi(inc, inc2, 4));
    P("vpermwi", xv_vpermwi(w, 0xe4));
    P("vpermwi2", xv_vpermwi(w, 0x1b));
    P("vrlimi", xv_vrlimi(d, w, 8, 1));
    P("vrlimi2", xv_vrlimi(d, w, 5, 0));
    /* VMX_JUDGE_PLAN P2, the game's own immediates (TcVmxConfirmed.java): 82e90d48's cross-product shuffles, and
       827be458's lvlx + vrlimi128 4,3 / 4,3 / 3,2 assembling (arg2, arg3, arg4, 0.0) from 0x50..0x5c(r1) */
    P("vpermwi_135", xv_vpermwi(w, 135));
    P("vpermwi_99", xv_vpermwi(w, 99));
    P("vrlimi_827be654", xv_vrlimi(xv_vrlimi(V4(0x22222222u, 0x44444444u, 0x5a5a5a5au, 0), V4(0x33333333u, 0x22222222u,
        0x44444444u, 0x5a5a5a5au), 4, 3), xv_vrlimi(V4(0x44444444u, 0x5a5a5a5au, 0, 0), V4(0x5a5a5a5au, 0, 0, 0), 4, 3),
        3, 2));
    P("vmrghw", xv_vmrghw(w, d));
    P("vmrglw", xv_vmrglw(w, d));
    P("vmrghh", xv_vmrghh(inc, inc2));
    P("vmrglb", xv_vmrglb(inc, inc2));
    P("vspltw", xv_vspltw(w, 2));
    P("vsplth", xv_vsplth(inc, 3));
    P("vspltb", xv_vspltb(inc, 9));
    P("vaddshs", xv_vaddshs(V4(0x7fff0001u, 0x80000005u, 0, 0), V4(0x00010001u, 0xffff0003u, 0, 0)));
    P("vsubuhs", xv_vsubuhs(V4(0x00050010u, 0, 0, 0), V4(0x00060001u, 0, 0, 0)));
    P("vadduwm", xv_vadduwm(V4(0xffffffffu, 1, 2, 3), V4(2, 2, 2, 2)));
    P("vaddsws", xv_vaddsws(V4(0x7fffffffu, 0x80000000u, 5, 0), V4(1, 0xffffffffu, 0xfffffffbu, 0)));
    P("vpkswss", xv_vpkswss(V4(0x00010000u, 0xfffeee90u, 5, 0xffffffffu), V4(0x7fffu, 0xffff8000u, 0, 0x8000u)));
    P("vpkshus", xv_vpkshus(V4(0x01ff0080u, 0xff800100u, 0x00010002u, 0x00030004u), z));
    P("vpkuwum", xv_vpkuwum(V4(0x12345678u, 0x9abcdef0u, 1, 2), V4(3, 4, 5, 6)));
    P("vupkhsb", xv_vupkhsb(V4(0x807f01ffu, 0x02030405u, 0, 0)));
    P("vupklsh", xv_vupklsh(V4(0, 0, 0x8000ffffu, 0x7fff0001u)));
    P("vupkhsh", xv_vupkhsh(V4(0x8000ffffu, 0x7fff0001u, 0, 0)));
    P("vslw", xv_vslw(V4(1, 1, 1, 0x80000001u), V4(31, 32, 33, 1)));
    P("vsraw", xv_vsraw(V4(0x80000000u, 0x80000000u, 0x40000000u, 1), V4(4, 31, 1, 0)));
    P("vrlw", xv_vrlw(V4(0x80000001u, 0x12345678u, 1, 1), V4(1, 8, 0, 32)));
    P("vsl", xv_vsl(V4(0x12345678u, 0x9abcdef0u, 0x0fedcba9u, 0x87654321u), V4(0, 0, 0, 4)));
    P("vsro", xv_vsro(inc, V4(0, 0, 0, 0x10)));
    P("vslo", xv_vslo(inc, V4(0, 0, 0, 0x08)));
    P("vsel", xv_vsel(V4(0xffff0000u, 0, 0, 0), V4(0x00ffff00u, 0xffffffffu, 0, 0), V4(0x0f0f0f0fu, 0xffffffffu, 0, 0)));
    /* floats: 1.0 = 3f800000, 2.0 = 40000000, 3.0 = 40400000, 4.0 = 40800000, 10.0 = 41200000 */
    P("vaddfp", xv_vaddfp(V4(0x3f800000u, 0x00000001u, 0x7f800000u, 0x7fa00000u), V4(0x40000000u, 0, 0xff800000u, 0x3f800000u)));
    P("vmulfp", xv_vmulfp(V4(0x40000000u, 0x80000000u, 0x00400000u, 0x3f800000u), V4(0x40400000u, 0x3f800000u, 0x3f800000u, 0x7fc12345u)));
    /* (1 + 2^-12)^2 + 2^-60 = 1 + 2^-11 + 2^-24 + 2^-60: rounded once that is 1 + 2^-11 + 2^-23 (3f801001),
       rounded through double it would be the tie 1 + 2^-11 + 2^-24 and then even (3f801000) */
    P("vmaddfp", xv_vmaddfp(V4(0x3f800800u, 0x40000000u, 0, 0), V4(0x3f800800u, 0x40400000u, 0, 0), V4(0x21800000u, 0x3f800000u, 0, 0)));
    P("vnmsubfp", xv_vnmsubfp(V4(0x40000000u, 0, 0, 0), V4(0x40400000u, 0, 0, 0), V4(0x3f800000u, 0, 0, 0)));
    P("vmsum4fp", xv_vmsum4fp(V4(0x3f800000u, 0x40000000u, 0x40400000u, 0x40800000u), V4(0x3f800000u, 0x3f800000u, 0x3f800000u, 0x3f800000u)));
    P("vmsum3fp", xv_vmsum3fp(V4(0x3f800000u, 0x40000000u, 0x40400000u, 0x40800000u), V4(0x3f800000u, 0x3f800000u, 0x3f800000u, 0x3f800000u)));
    P("vmaxfp", xv_vmaxfp(V4(0x80000000u, 0x3f800000u, 0x7fc00000u, 0xbf800000u), V4(0, 0x40000000u, 0x3f800000u, 0xc0000000u)));
    P("vminfp", xv_vminfp(V4(0x80000000u, 0x3f800000u, 0x3f800000u, 0xbf800000u), V4(0, 0x40000000u, 0x7fa00000u, 0xc0000000u)));
    /* 2.5 = 40200000, 3.5 = 40600000, -2.5 = c0200000, 1.5 = 3fc00000 */
    P("vrfin", xv_vrfin(V4(0x40200000u, 0x40600000u, 0xc0200000u, 0x3fc00000u)));
    P("vrfiz", xv_vrfiz(V4(0x40200000u, 0xc0600000u, 0x3f000000u, 0xbf000000u)));
    P("vrfip", xv_vrfip(V4(0x40200000u, 0xc0600000u, 0x3f000000u, 0xbf000000u)));
    P("vrfim", xv_vrfim(V4(0x40200000u, 0xc0600000u, 0x3f000000u, 0xbf000000u)));
    P("vcfsx", xv_vcfsx(V4(3, 0xfffffffeu, 0, 1), 1));
    P("vcfux", xv_vcfux(V4(3, 0xffffffffu, 0, 8), 2));
    P("vctsxs", xv_vctsxs(V4(0x4f32d05eu, 0xcf32d05eu, 0x7fc00000u, 0xc0200000u), 0));
    P("vctuxs", xv_vctuxs(V4(0x4f32d05eu, 0xbf800000u, 0x40200000u, 0x3f800000u), 1));
    P("vcmpgefp", xv_vcmpgefp(V4(0x3f800000u, 0x3f800000u, 0x7fc00000u, 0x80000000u), V4(0x3f800000u, 0x40000000u, 0x3f800000u, 0)));
    P("vcmpbfp", xv_vcmpbfp(V4(0x3f800000u, 0x40400000u, 0xc0400000u, 0x7fc00000u), V4(0x40000000u, 0x40000000u, 0x40000000u, 0x40000000u)));
    P("vcmpgtsh", xv_vcmpgtsh(V4(0x7fff8000u, 0x00010001u, 0, 0), V4(0x80007fffu, 0x00010000u, 0, 0)));
    P("vrefp", xv_vrefp(V4(0x40000000u, 0x3e800000u, 0, 0x80000000u)));
    P("vrsqrtefp", xv_vrsqrtefp(V4(0x40800000u, 0x3e800000u, 0xbf800000u, 0)));
    P("vexptefp", xv_vexptefp(V4(0x40400000u, 0xbf800000u, 0, 0x3f000000u)));
    P("vlogefp", xv_vlogefp(V4(0x41000000u, 0x3f000000u, 0x3f800000u, 0)));
    P("lvsl", xv_lvsl(0x13u));
    P("lvsr", xv_lvsr(0x13u));
    P("ld", xv_ld(0x10u));
    P("ldl", xv_ldl(0x13u));
    P("ldr", xv_ldr(0x13u));
    P("ldl0", xv_ldl(0x10u));
    P("ldr0", xv_ldr(0x10u));
    P("ldew", xv_ldew(0x18u));
    xv_st(0x00u, w);
    xv_stl(0x23u, d);
    xv_str(0x33u, d);
    P("mem0", xv_ld(0x00u));
    P("mem2", xv_ld(0x20u));
    P("mem3", xv_ld(0x30u));
    P("vspltis", xv_vspltis(0xfffffff0u));
    return 0;
}
"""

EXPECT = {
    "vperm": "1f1e1d1c 1b1a1918 00011011 0f0e0302",
    "vsldoi": "04050607 08090a0b 0c0d0e0f 10111213",
    "vpermwi": "44444444 33333333 22222222 11111111",
    "vpermwi2": "11111111 22222222 33333333 44444444",
    "vrlimi": "22222222 bbbbbbbb cccccccc dddddddd",
    "vrlimi2": "aaaaaaaa 22222222 cccccccc 44444444",
    "vpermwi_135": "33333333 11111111 22222222 44444444",
    "vpermwi_99": "22222222 33333333 11111111 44444444",
    "vrlimi_827be654": "22222222 33333333 44444444 5a5a5a5a",
    "vmrghw": "11111111 aaaaaaaa 22222222 bbbbbbbb",
    "vmrglw": "33333333 cccccccc 44444444 dddddddd",
    "vmrghh": "00011011 02031213 04051415 06071617",
    "vmrglb": "08180919 0a1a0b1b 0c1c0d1d 0e1e0f1f",
    "vspltw": "33333333 33333333 33333333 33333333",
    "vsplth": "06070607 06070607 06070607 06070607",
    "vspltb": "09090909 09090909 09090909 09090909",
    "vaddshs": "7fff0002 80000008 00000000 00000000",
    "vsubuhs": "0000000f 00000000 00000000 00000000",
    "vadduwm": "00000001 00000003 00000004 00000005",
    "vaddsws": "7fffffff 80000000 00000000 00000000",
    "vpkswss": "7fff8000 0005ffff 7fff8000 00007fff",
    "vpkshus": "ff8000ff 01020304 00000000 00000000",
    "vpkuwum": "5678def0 00010002 00030004 00050006",
    "vupkhsb": "ff80007f 0001ffff 00020003 00040005",
    "vupklsh": "ffff8000 ffffffff 00007fff 00000001",
    "vupkhsh": "ffff8000 ffffffff 00007fff 00000001",
    "vslw": "80000000 00000001 00000002 00000002",
    "vsraw": "f8000000 ffffffff 20000000 00000001",
    "vrlw": "00000003 34567812 00000001 00000001",
    "vsl": "23456789 abcdef00 fedcba98 76543210",
    "vsro": "00000001 02030405 06070809 0a0b0c0d",
    "vslo": "01020304 05060708 090a0b0c 0d0e0f00",
    "vsel": "f0ff0f00 ffffffff 00000000 00000000",
    "vaddfp": "40400000 00000000 7fc00000 7fe00000",
    "vmulfp": "40c00000 80000000 00000000 7fc12345",
    "vmaddfp": "3f801001 40e00000 00000000 00000000",
    "vnmsubfp": "c0a00000 80000000 80000000 80000000",
    "vmsum4fp": "41200000 41200000 41200000 41200000",
    "vmsum3fp": "40c00000 40c00000 40c00000 40c00000",
    "vmaxfp": "00000000 40000000 7fc00000 bf800000",
    "vminfp": "80000000 3f800000 7fe00000 c0000000",
    "vrfin": "40000000 40800000 c0000000 40000000",
    "vrfiz": "40000000 c0400000 00000000 80000000",
    "vrfip": "40400000 c0400000 3f800000 80000000",
    "vrfim": "40000000 c0800000 00000000 bf800000",
    "vcfsx": "3fc00000 bf800000 00000000 3f000000",
    "vcfux": "3f400000 4e800000 00000000 40000000",
    "vctsxs": "7fffffff 80000000 00000000 fffffffe",
    "vctuxs": "ffffffff 00000000 00000005 00000002",
    "vcmpgefp": "ffffffff 00000000 00000000 ffffffff",
    "vcmpbfp": "00000000 80000000 40000000 c0000000",
    "vcmpgtsh": "ffff0000 0000ffff 00000000 00000000",
    "vrefp": "3f000000 40800000 7f800000 ff800000",
    "vrsqrtefp": "3f000000 40000000 7fc00000 7f800000",
    "vexptefp": "41000000 3f000000 3f800000 3fb504f3",
    "vlogefp": "40400000 bf800000 00000000 ff800000",
    "lvsl": "03040506 0708090a 0b0c0d0e 0f101112",
    "lvsr": "0d0e0f10 11121314 15161718 191a1b1c",
    "ld": "50515253 54555657 58595a5b 5c5d5e5f",
    "ldl": "53545556 5758595a 5b5c5d5e 5f000000",
    "ldr": "00000000 00000000 00000000 00505152",
    "ldl0": "50515253 54555657 58595a5b 5c5d5e5f",
    "ldr0": "00000000 00000000 00000000 00000000",
    "ldew": "00000000 00000000 58595a5b 00000000",
    "mem0": "11111111 22222222 33333333 44444444",
    "mem2": "606162aa aaaaaabb bbbbbbcc ccccccdd",
    "mem3": "dddddd73 74757677 78797a7b 7c7d7e7f",
    "vspltis": "fffffff0 fffffff0 fffffff0 fffffff0",
}
# vpkshus: halfwords 01ff 0080 ff80 0100 0001 0002 0003 0004, then b's zeros -> bytes ff 80 00 ff 01 02 03 04 0 ...
# stvlx at 0x23 writes d's bytes 0..12 to 0x23..0x2f; stvrx at 0x33 writes d's bytes 13..15 to 0x30..0x32
# vsro / vslo: the octet count is bits 1..4 of vB's byte 15 (0x10 -> 2, 0x08 -> 1)


def msvc():
    import compile_all
    out = compile_all.HERE / "out"
    out.mkdir(exist_ok=True)
    env = compile_all.msvc_env(out)
    path = next((v for k, v in env.items() if k.upper() == "PATH"), "")
    for d in path.split(";"):
        if d and (pathlib.Path(d) / "cl.exe").exists():
            return str(pathlib.Path(d) / "cl.exe"), env
    raise unittest.SkipTest("cl.exe not found")


class TestVmx(unittest.TestCase):
    def test_helpers(self):
        cl, env = msvc()
        src = PRELUDE + vmx.TYPEDEF + "\n" + "\n\n".join(vmx.helper_text(sorted(vmx.H))) + "\n" + MAIN
        with tempfile.TemporaryDirectory() as t:
            c = pathlib.Path(t) / "vt.c"
            c.write_text(src, encoding="utf-8")
            r = subprocess.run([cl, "/nologo", "/O1", "/fp:strict", "/W4", "/WX", "/TC", "/wd4505", str(c),
                                f"/Fe{t}\\vt.exe", f"/Fo{t}\\"], capture_output=True, text=True, timeout=300,
                               env=env, cwd=t)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            p = subprocess.run([os.path.join(t, "vt.exe")], capture_output=True, text=True, timeout=60)
        got = {ln.split()[0]: " ".join(ln.split()[1:]) for ln in p.stdout.splitlines() if ln.strip()}
        bad = [f"{k}: got {got.get(k)} want {v}" for k, v in EXPECT.items() if got.get(k) != v]
        self.assertEqual(bad, [], "\n" + "\n".join(bad))


# ------------------------------------------------------------------------- the port form's SSE helpers against the scalar ones
# Each helper in vmx.SIMD runs on the same operands with xb_vmx_mode = 1 (the scalar body, the answer key), 0 (its SSE version,
# runtime/xv_simd.h) and, for an eighth of the cases, 2 (check mode: must return the scalar result, and xb_vmx_check must see
# no difference); a case is a mismatch if any result word differs. Operands: random words, lanes drawn from edge values
# (saturation limits and sign boundaries of bytes, halfwords and words; shift counts 0, 1, 15, 16, 31, 32 and above), and for
# vperm each control class in turn (random bytes, a only, b only, identity, reversed, one byte splatted, bits 5..7 set, and
# every byte value 0..255 at every position). vsldoi takes every shift 0..15, the splats every element number 0..31.
# Each helper also has a plant, a wrong answer the test must catch: its saturating or modulo twin, its signed or unsigned
# twin, merge or unpack high for low, the shift count not masked, vperm's bits 5..7 taken as "from a", vsldoi's shift 15 as
# 0, a splat's element number not masked, or (compares for equality) one bit flipped in one lane at an edge value.
SIMD_N = int(os.environ.get("XV_SIMD_N", "1000000"))   # cases per helper

TWIN = {}
for _k in ("b", "h", "w"):
    _u = {"b": "ub", "h": "uh", "w": "uw"}[_k]
    _s = {"b": "sb", "h": "sh", "w": "sw"}[_k]
    TWIN.update({f"vadd{_u}m": f"vadd{_u}s", f"vsub{_u}m": f"vsub{_u}s", f"vadd{_u}s": f"vadd{_u}m", f"vsub{_u}s": f"vsub{_u}m",
                 f"vadd{_s}s": f"vadd{_u}m", f"vsub{_s}s": f"vsub{_u}m", f"vmax{_u}": f"vmax{_s}", f"vmax{_s}": f"vmax{_u}",
                 f"vmin{_u}": f"vmin{_s}", f"vmin{_s}": f"vmin{_u}", f"vavg{_u}": f"vavg{_s}", f"vavg{_s}": f"vavg{_u}",
                 f"vcmpgtu{_k}": f"vcmpgts{_k}", f"vcmpgts{_k}": f"vcmpgtu{_k}"})
TWIN.update({"vpkuhum": "vpkuhus", "vpkuhus": "vpkuhum", "vpkshus": "vpkuhum", "vpkshss": "vpkshus", "vpkuwum": "vpkuwus",
             "vpkuwus": "vpkuwum", "vpkswus": "vpkuwum", "vpkswss": "vpkswus", "vmrghb": "vmrglb", "vmrglb": "vmrghb",
             "vmrghh": "vmrglh", "vmrglh": "vmrghh", "vupkhsb": "vupklsb", "vupklsb": "vupkhsb", "vupkhsh": "vupklsh",
             "vupklsh": "vupkhsh", "vmaddfp": "vnmsubfp", "vnmsubfp": "vmaddfp", "vmsum3fp": "vmsum4fp",
             "vmsum4fp": "vmsum3fp"})


def plant(name):
    """The C expression of helper `name`'s plant (xb_vmx_mode is 1 while it runs, so every helper call is scalar)."""
    n = name[3:]
    args = ", ".join(vmx.SIMD[name])
    if n in TWIN:
        return f"xv_{TWIN[n]}({args})"
    if n in ("vslh", "vsrh", "vsrah"):
        return "P_SH16(s_, b)"
    if n in ("vslw", "vsrw", "vsraw"):
        return "P_SH32(s_, b)"
    if n == "vperm":
        return "xv_vperm(a, b, P_HI(c))"
    if n == "vsldoi":
        return "xv_vsldoi(a, b, sh == 15 ? 0 : sh)"
    if n == "vspltb":
        return "xv_vspltb(b, n ^ ((n >> 4) & 1))"
    if n == "vsplth":
        return "xv_vsplth(b, n ^ ((n >> 3) & 1))"
    if n.startswith("vcmpequ"):
        return "P_EDGE(s_, a)"
    raise KeyError(f"no plant for {name}")


SIMD_MAIN = r"""
static u64 rs = 0x9e3779b97f4a7c15ull;
static u32 rnd(void) { rs ^= rs << 13; rs ^= rs >> 7; rs ^= rs << 17; return (u32)(rs >> 32); }
static const u32 E16[] = {0, 1, 2, 0x0f, 0x10, 0x11, 0x1f, 0x20, 0x7e, 0x7f, 0x80, 0x81, 0xfe, 0xff, 0x100, 0x7f7f, 0x8080,
                          0x7ffe, 0x7fff, 0x8000, 0x8001, 0xff7f, 0xff80, 0xfffe, 0xffff};
static const u32 E32[] = {0, 1, 0x0f, 0x10, 0x1f, 0x20, 0x21, 0x3f, 0x7fffffffu, 0x80000000u, 0x80000001u, 0xffffffffu,
                          0x0000ffffu, 0x00010000u, 0x00007fffu, 0x00008000u, 0xffff8000u, 0xffff7fffu, 0x7fff8000u,
                          0x80007fffu, 0x00ff00ffu, 0xff00ff00u, 0x7f807f80u, 0x807f807fu};
#define NE16 (sizeof E16 / sizeof E16[0])
#define NE32 (sizeof E32 / sizeof E32[0])
static xv operand(u32 i)
{
    xv r;
    int k;
    for (k = 0; k < 4; k++) {
        u32 m = (i + (u32)k) & 3u;
        r.w[k] = m == 0 || m == 1 ? rnd() : m == 2 ? (E16[rnd() % NE16] << 16) | E16[rnd() % NE16] : E32[rnd() % NE32];
    }
    return r;
}
static xv control(u32 i)
{
    xv r;
    int k, cls = (int)(i % 8u), sp = (int)(rnd() & 31u);
    u32 t = i / 8u;
    if (cls == 0) return operand(i);
    for (k = 0; k < 4; k++) {
        u32 w = 0;
        int j;
        for (j = 0; j < 4; j++) {
            int b = 4 * k + j;
            u32 v = cls == 1 ? rnd() & 15u : cls == 2 ? 16u + (rnd() & 15u) : cls == 3 ? (u32)b : cls == 4 ? (u32)(31 - b)
                  : cls == 5 ? (u32)sp : cls == 6 ? (rnd() & 31u) | 0xe0u : t + 17u * (u32)b;
            w = (w << 8) | (v & 0xffu);
        }
        r.w[k] = w;
    }
    return r;
}
static int same(xv x, xv y) { return x.w[0] == y.w[0] && x.w[1] == y.w[1] && x.w[2] == y.w[2] && x.w[3] == y.w[3]; }
static u32 chk_bad;
void xb_vmx_check(int id, const char *name, const u32 *f, const u32 *s)
{
    (void)id; (void)name;
    if (f[0] != s[0] || f[1] != s[1] || f[2] != s[2] || f[3] != s[3]) chk_bad++;
}
/* plants: a shift whose count is not masked (lanes shifted by the lane width or more come out 0), vperm with a control
   byte's bits 5..7 meaning "from a", one bit flipped where lane 7 of a is 0x8000 */
static xv P_SH16(xv s, xv b) { int k; for (k = 0; k < 8; k++) if (xv_gh(b, k) & 0xfff0u) xv_sh(&s, k, 0); return s; }
static xv P_SH32(xv s, xv b) { int k; for (k = 0; k < 4; k++) if (b.w[k] & ~31u) s.w[k] = 0; return s; }
static xv P_HI(xv c) { int k; for (k = 0; k < 16; k++) if (xv_gb(c, k) >= 0x20u) xv_sb(&c, k, xv_gb(c, k) & 15u); return c; }
static xv P_EDGE(xv s, xv a) { if ((a.w[3] & 0xffffu) == 0x8000u) s.w[3] ^= 1u; return s; }
#define CHECK(name, call, pcall) do { \
    u32 i, bad = 0, caught = 0, before = chk_bad; \
    for (i = 0; i < N; i++) { \
        xv a = operand(i), b = operand(i >> 2), c = control(i), s_, f_, p_; int sh = (int)(i & 15u), n = (int)(i & 31u); \
        (void)a; (void)b; (void)c; (void)sh; (void)n; \
        xb_vmx_mode = 1; s_ = call; xb_vmx_mode = 0; f_ = call; \
        if (!same(s_, f_)) { if (bad < 3) printf("# %s case %u\n", name, i); bad++; } \
        if (i % 8u == 0) { xv k_; xb_vmx_mode = 2; k_ = call; if (!same(k_, s_)) bad++; } \
        xb_vmx_mode = 1; p_ = pcall; if (!same(p_, f_)) caught++; \
    } \
    printf("%s %u %u\n", name, bad + (chk_bad - before), caught); } while (0)
int main(void)
{
    const u32 N = %N%;
%CHECKS%
    return 0;
}
"""


class TestVmxSimd(unittest.TestCase):
    def test_simd_matches_scalar(self):
        cl, env = msvc()
        rt = HERE.parent.parent / "runtime"
        checks = [f'    CHECK("{n}", {n}({", ".join(a)}), {plant(n)});' for n, a in sorted(vmx.SIMD.items())]
        main = SIMD_MAIN.replace("%N%", f"{SIMD_N}u").replace("%CHECKS%", "\n".join(checks))
        names = sorted(set(vmx.SIMD) | {f"xv_{t}" for t in TWIN.values()} | {"xv_gh", "xv_sh", "xv_gb", "xv_sb"})
        src = (PRELUDE + vmx.SIMD_INCLUDE + "\n" + vmx.TYPEDEF + "\n" + "\n\n".join(vmx.helper_text(names, port=True))
               + "\n" + main)
        with tempfile.TemporaryDirectory() as t:
            c = pathlib.Path(t) / "vs.c"
            c.write_text(src, encoding="utf-8")
            r = subprocess.run([cl, "/nologo", "/O1", "/fp:strict", "/W4", "/WX", "/TC", "/wd4505", f"/I{rt}", str(c),
                                "/Fe" + os.path.join(t, "vs.exe"), "/Fo" + t + os.sep], capture_output=True, text=True,
                               timeout=300, env=env, cwd=t)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            p = subprocess.run([os.path.join(t, "vs.exe")], capture_output=True, text=True, timeout=1800)
        rows = [ln.split() for ln in p.stdout.splitlines() if ln.strip() and not ln.startswith("#")]
        got = {k: int(v) for k, v, _ in rows}
        caught = {k: int(c) for k, _, c in rows}
        print(f"simd vs scalar: {len(got)} helpers x {SIMD_N} cases, mismatches {sum(got.values())}; "
              f"plants caught {sum(1 for v in caught.values() if v)}/{len(caught)}", file=sys.stderr)
        self.assertEqual(sorted(got), sorted(vmx.SIMD), p.stdout[-2000:])
        self.assertEqual({k: v for k, v in got.items() if v}, {}, p.stdout[-2000:])
        self.assertEqual([k for k, v in caught.items() if not v], [], "plants not caught")


# ------------------------------------------------------------------- the float multiply-adds and dot products (vmx.SIMD_MASK)
# The fast route (xb_vmx_mode 0: FMA3 multiply-adds, SSE2-double dot products, runtime/xv_simd.h group 4) against the scalar
# helpers (mode 1, the answer key) on float operands by class. A row counts calls, words the fast route kept, words it left
# to the scalar body, and mismatching words in each (a dot product is one word per call). Classes:
#   edge     lanes from a table (signed zeros, denormals, 2^-126 and its neighbours, 1, FLT_MAX, infinities, quiet and
#            signalling NaNs with payloads and both signs) or random normals, in every operand position
#   words    random 32-bit words
#   wide     random normal floats over the whole exponent range
#   mid      random normal floats, exponents 2^-40..2^40
#   tie      products (1 + i 2^-12)(1 + j 2^-12) with ij odd (an exact single tie) plus 0, a tiny term either side, or a random
#            addend, scaled from below 2^-126 to past 2^127
#   tiny     results around 2^-126: products near 2^-150..2^-120 against addends of 0, +-2^-126 and its neighbours, including
#            the exact value 2^-126 - 2^-150 - 2^-186 (the scalar body gives 2^-126, one rounding a denormal)
#   ovf      products and addends near FLT_MAX
#   cancel   dot products whose products cancel exactly or but for one bit, plus a small remainder
#   spread   dot products with lane exponents far apart (2^+-100), mixed with the above
#   geom     synthetic game data: unit-vector components, positions to +-4096, scales 2^-8..2^8, w of 0 or 1
#   game     lanes drawn from the game's own float constants (XV_GAME_FLOATS: a file of little-endian words), when given
# XV_FP_N cases per class and helper (default 200000). Must be 0 mismatches in every row. With XV_FP_TIME set the program
# also prints ns per call of each helper, scalar (mode 1) and fast (mode 0), on the geom (or game) operands.
FP_N = int(os.environ.get("XV_FP_N", "200000"))
FP_NAMES = ("xv_vmaddfp", "xv_vnmsubfp", "xv_vmsum3fp", "xv_vmsum4fp")
FP_MAIN = r"""
#include <stdlib.h>
#include <string.h>
#include <windows.h>
static u64 rs = 0x2545f4914f6cdd1dull;
static u32 rnd(void) { rs ^= rs << 13; rs ^= rs >> 7; rs ^= rs << 17; return (u32)(rs >> 32); }
static u32 fbits(f32 f) { u32 w; memcpy(&w, &f, 4); return w; }
static f32 bitsf(u32 w) { f32 f; memcpy(&f, &w, 4); return f; }
static const u32 EF[] = {0, 0x80000000u, 1, 0x80000001u, 0x007fffffu, 0x807fffffu, 0x00400000u, 0x80400000u, 0x00800000u,
    0x80800000u, 0x00800001u, 0x80800001u, 0x00ffffffu, 0x3f800000u, 0xbf800000u, 0x3fc00000u, 0x3f800001u, 0x3f7fffffu,
    0x1f800000u, 0x9f800000u, 0x20000000u, 0x1f7fffffu, 0x7f7fffffu, 0xff7fffffu, 0x7f000000u, 0x5f800000u, 0x5f7fffffu,
    0x7f800000u, 0xff800000u, 0x7fc00000u, 0xffc00000u, 0x7fc12345u, 0xffc00001u, 0x7f800001u, 0xff800001u, 0x7fa00000u,
    0x7fbfffffu, 0xffbfffffu, 0x7fffffffu, 0x0d000000u, 0x0c800000u};
#define NEF (sizeof EF / sizeof EF[0])
static u32 *GF; static u32 NGF;
static u32 sgn(void) { return (rnd() & 1u) << 31; }
static u32 normal(int lo, int hi) { return sgn() | ((u32)(lo + (int)(rnd() % (u32)(hi - lo + 1))) << 23) | (rnd() & 0x7fffffu); }
static u32 scaled(u32 m, int e) { int x = (int)((m >> 23) & 0xffu) + e; return x < 1 || x > 254 ? 0u : (m & 0x807fffffu) | ((u32)x << 23); }
static u32 geom(void)
{
    u32 k = rnd() % 5u;
    f32 f = (f32)((s32)rnd()) / 2147483648.0f;               /* -1..1: a unit-vector component */
    if (k == 1) f *= 4096.0f;                                /* a position */
    if (k == 2) f = bitsf(normal(119, 135));                 /* a scale */
    if (k == 3) f = (rnd() & 1u) ? 1.0f : 0.0f;              /* w */
    return fbits(f);
}
static u32 lane(int cls)
{
    switch (cls) {
    case 0: return (rnd() & 1u) ? EF[rnd() % NEF] : normal(100, 154);
    case 1: return rnd();
    case 2: return normal(1, 254);
    case 3: return normal(87, 167);
    case 9: return geom();
    case 10: return NGF ? GF[rnd() % NGF] : geom();
    default: return normal(100, 154);
    }
}
/* operands for helper kind (0 fma, 1 dot) and class; c is the addend of a multiply-add */
static void make(int dot, int cls, xv *a, xv *b, xv *c)
{
    int k;
    for (k = 0; k < 4; k++) { a->w[k] = lane(cls); b->w[k] = lane(cls); c->w[k] = lane(cls); }
    if (cls == 4) {                                          /* tie */
        for (k = 0; k < 4; k++) {
            u32 i = rnd() & 4095u, j = (rnd() & 4095u) | 1u, e = rnd() % 300u;
            int e1, e2;
            if (!(i & 1u)) i |= 1u;
            e1 = (int)(e / 2u) - 75; e2 = (int)e - 150 - e1;
            if (e1 < -120) e1 = -120;
            a->w[k] = scaled(sgn() | 0x3f800000u | (i << 11), e1);
            b->w[k] = scaled(sgn() | 0x3f800000u | (j << 11), e2);
            switch (rnd() % 4u) {
            case 0: c->w[k] = sgn(); break;
            case 1: c->w[k] = scaled(sgn() | 0x3f800000u, e1 + e2 - 40 - (int)(rnd() % 60u)); break;
            case 2: c->w[k] = scaled(sgn() | 0x3f800000u | (rnd() & 0x7fffffu), e1 + e2 + (int)(rnd() % 7u) - 3); break;
            default: c->w[k] = scaled(sgn() | 0x3f800000u | ((rnd() & 0x7ffu) << 12), e1 + e2); break;
            }
            if (dot && k > 0 && (rnd() & 1u)) a->w[k] = sgn();
        }
    } else if (cls == 5) {                                   /* tiny */
        for (k = 0; k < 4; k++) {
            int e = -(int)(rnd() % 32u) - 120, e1 = e / 2;
            a->w[k] = scaled(sgn() | 0x3f800000u | (rnd() & 0x7fffffu), e1);
            b->w[k] = scaled(sgn() | 0x3f800000u | (rnd() & 0x7fffffu), e - e1);
            switch (rnd() % 4u) {
            case 0: c->w[k] = sgn(); break;
            case 1: c->w[k] = sgn() | 0x00800000u; break;
            default: c->w[k] = sgn() | (0x00800000u + (rnd() % 5u)); break;
            }
        }
        if ((rnd() & 7u) == 0) {                             /* -(1 + 2^-12)(1 - 4095 2^-24) 2^-150 + 2^-126 */
            k = (int)(rnd() & 3u);
            a->w[k] = 0x9a000800u; b->w[k] = 0x19fff001u; c->w[k] = 0x00800000u;
            if (dot) { a->w[(k + 1) & 3] = 0x20000000u; b->w[(k + 1) & 3] = 0x1f800000u; }   /* 2^-63 * 2^-64: 2^-127 */
        }
    } else if (cls == 6) {                                   /* ovf */
        for (k = 0; k < 4; k++) {
            a->w[k] = sgn() | (0x5f000000u + (rnd() & 0x00ffffffu));
            b->w[k] = sgn() | (0x5f000000u + (rnd() & 0x00ffffffu));
            c->w[k] = (rnd() & 1u) ? sgn() | (0x7f7fffffu - (rnd() & 0xffffu)) : normal(200, 254);
        }
    } else if (cls == 7 || cls == 8) {                       /* cancel, spread */
        u32 x = normal(100, 154), y = normal(100, 154);
        a->w[0] = x; b->w[0] = y; a->w[1] = x; b->w[1] = y ^ 0x80000000u;
        if (rnd() & 1u) b->w[1] ^= 1u + (rnd() & 1u);         /* cancel but for the last bit or two */
        a->w[2] = normal(60, 120); b->w[2] = normal(60, 120);
        a->w[3] = (rnd() & 1u) ? sgn() : normal(60, 120);
        if (cls == 8) for (k = 0; k < 4; k++) if (rnd() & 1u) a->w[k] = normal(20, 234);
        for (k = 3; k > 0; k--) {                            /* the cancelling pair in any lanes */
            int j = (int)(rnd() % (u32)(k + 1));
            u32 t = a->w[k]; a->w[k] = a->w[j]; a->w[j] = t;
            t = b->w[k]; b->w[k] = b->w[j]; b->w[j] = t;
        }
    }
}
static int same1(u32 x, u32 y) { return x == y; }
static u32 gone;
void xb_vmx_check(int id, const char *name, const u32 *f, const u32 *s) { (void)id; (void)name; (void)f; (void)s; }
#define NCLS 11
static const char *CN[NCLS] = {"edge", "words", "wide", "mid", "tie", "tiny", "ovf", "cancel", "spread", "geom", "game"};
static xv call(int h, xv a, xv b, xv c)
{
    switch (h) {
    case 0: return xv_vmaddfp(a, b, c);
    case 1: return xv_vnmsubfp(a, b, c);
    case 2: return xv_vmsum3fp(a, b);
    default: return xv_vmsum4fp(a, b);
    }
}
static int mask(int h, xv a, xv b, xv c)
{
    xv t;
    switch (h) {
    case 0: return xs_vmaddfp(t.w, a.w, b.w, c.w);
    case 1: return xs_vnmsubfp(t.w, a.w, b.w, c.w);
    case 2: return xs_vmsum3fp(t.w, a.w, b.w);
    default: return xs_vmsum4fp(t.w, a.w, b.w);
    }
}
static const char *HN[4] = {"vmaddfp", "vnmsubfp", "vmsum3fp", "vmsum4fp"};
int main(void)
{
    const u32 N = %N%;
    int h, cls;
    char *gp = getenv("XV_GAME_FLOATS");
    if (gp) {
        FILE *f = fopen(gp, "rb");
        if (f) {
            GF = (u32 *)malloc(1u << 26);
            NGF = (u32)fread(GF, 4, (1u << 24), f);
            fclose(f);
        }
    }
    printf("# fma3 %d games %u\n", xs_fma_ok(), NGF);
    for (h = 0; h < 4; h++) {
        for (cls = 0; cls < NCLS; cls++) {
            u32 i, fast = 0, slow = 0, mf = 0, ms = 0;
            if (cls == 10 && !NGF) continue;
            for (i = 0; i < N; i++) {
                xv a, b, c, o, n;
                int m, k;
                make(h >= 2, cls, &a, &b, &c);
                xb_vmx_mode = 1; o = call(h, a, b, c);
                xb_vmx_mode = 0; n = call(h, a, b, c);
                m = mask(h, a, b, c);
                for (k = 0; k < (h >= 2 ? 1 : 4); k++) {
                    int f = !(m & (1 << k)), d = !same1(o.w[k], n.w[k]);
                    if (h >= 2) d = !same1(o.w[0], n.w[0]) || !same1(o.w[1], n.w[1]) || !same1(o.w[2], n.w[2]) || !same1(o.w[3], n.w[3]);
                    if (f) { fast++; mf += (u32)d; } else { slow++; ms += (u32)d; }
                    if (d && mf + ms < 4) printf("# %s %s %08x %08x %08x %08x | %08x %08x %08x %08x | %08x %08x %08x %08x -> %08x want %08x\n",
                        HN[h], CN[cls], a.w[0], a.w[1], a.w[2], a.w[3], b.w[0], b.w[1], b.w[2], b.w[3], c.w[0], c.w[1], c.w[2], c.w[3], n.w[k], o.w[k]);
                }
            }
            printf("%s %s %u %u %u %u %u\n", HN[h], CN[cls], N, fast, slow, mf, ms);
        }
    }
    {   /* no FMA3: every multiply-add word goes to the scalar body */
        u32 i, bad = 0;
        int keep = xb_vmx_fma;
        xb_vmx_fma = 0;
        for (i = 0; i < 1000u; i++) {
            xv a, b, c;
            make(0, 3, &a, &b, &c);
            if (mask(0, a, b, c) != 15 || mask(1, a, b, c) != 15) bad++;
        }
        xb_vmx_fma = keep;
        printf("nofma %u\n", bad);
    }
    if (getenv("XV_FP_TIME")) {
        enum { M = 4096, R = 2000 };
        static xv A[M], B[M], C[M];
        LARGE_INTEGER q0, q1, fq;
        int src = NGF ? 10 : 9, mode, i, r;
        QueryPerformanceFrequency(&fq);
        for (i = 0; i < M; i++) make(0, src, &A[i], &B[i], &C[i]);
        for (h = 0; h < 4; h++) {
            for (mode = 1; mode >= 0; mode--) {
                u32 acc = 0;
                xb_vmx_mode = mode;
                QueryPerformanceCounter(&q0);
                for (r = 0; r < R; r++)
                    for (i = 0; i < M; i++) { xv v = call(h, A[i], B[i], C[i]); acc ^= v.w[r & 3]; }
                QueryPerformanceCounter(&q1);
                gone ^= acc;
                printf("time %s %s %.2f\n", HN[h], mode ? "scalar" : "fast",
                       1e9 * (double)(q1.QuadPart - q0.QuadPart) / (double)fq.QuadPart / ((double)M * R));
            }
        }
        printf("# %u\n", gone);
    }
    return 0;
}
"""


class TestVmxFloat(unittest.TestCase):
    def test_fast_float_matches_scalar(self):
        cl, env = msvc()
        rt = HERE.parent.parent / "runtime"
        src = (PRELUDE + vmx.SIMD_INCLUDE + "\n" + vmx.TYPEDEF + "\n" + "\n\n".join(vmx.helper_text(list(FP_NAMES), port=True))
               + "\n" + FP_MAIN.replace("%N%", f"{FP_N}u"))
        with tempfile.TemporaryDirectory() as t:
            c = pathlib.Path(t) / "vf.c"
            c.write_text(src, encoding="utf-8")
            r = subprocess.run([cl, "/nologo", "/O1", "/fp:strict", "/W4", "/WX", "/TC", "/wd4505", "/wd4996", f"/I{rt}", str(c),
                                "/Fe" + os.path.join(t, "vf.exe"), "/Fo" + t + os.sep], capture_output=True, text=True,
                               timeout=300, env=env, cwd=t)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            p = subprocess.run([os.path.join(t, "vf.exe")], capture_output=True, text=True, timeout=3000)
        print(p.stdout, file=sys.stderr)
        rows = [ln.split() for ln in p.stdout.splitlines() if ln.strip() and not ln.startswith(("#", "time", "nofma"))]
        self.assertEqual(p.returncode, 0, p.stdout[-2000:])
        self.assertEqual(sorted({r[0] for r in rows}), sorted(n[3:] for n in FP_NAMES))
        self.assertEqual([r for r in rows if r[5] != "0" or r[6] != "0"], [], p.stdout[-3000:])
        self.assertIn("nofma 0", p.stdout)
        if "# fma3 1" in p.stdout:   # the fast route is taken on most ordinary operands
            fast = {(r[0], r[1]): int(r[3]) / max(1, int(r[3]) + int(r[4])) for r in rows}
            self.assertGreater(fast[("vmaddfp", "mid")], 0.9)
        fast = {(r[0], r[1]): int(r[3]) / max(1, int(r[3]) + int(r[4])) for r in rows}
        self.assertGreater(fast[("vmsum4fp", "mid")], 0.9)


if __name__ == "__main__":
    unittest.main()
