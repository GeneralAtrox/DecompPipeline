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
    "vnmsubfp": "c0a00000 00000000 00000000 00000000",
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


if __name__ == "__main__":
    unittest.main()
