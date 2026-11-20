"""The Xbox 360 kit's piecewise-linear gamma (runtime/xb_xenos_gamma.h): which of a texture's channels the converted shader turns to linear,
the curve itself, the display's gamma ramp, and the HLSL that does both on the host.

No game data: the C helpers are compiled with a small harness, the HLSL is compiled with the system's Direct3D compiler."""
import ctypes
import os
import pathlib
import re
import shutil
import subprocess
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "targets" / "xbox360_ppc" / "port" / "tools"))
from test_xbox360_ppc import X360, tmpdir  # noqa: E402
from test_xbox360_glue import cl, vcvars64  # noqa: E402
import shader_convert as sc  # noqa: E402

RUNTIME = X360 / "port" / "runtime"

HARNESS = r'''
#include <stdio.h>
#include "xb_xenos_gamma.h"
int main(void)
{
    unsigned pairs[384], i, ch, bad = 0;
    /* type 2, sign x y z gamma (3), w unsigned: Too Human's 3330 */
    unsigned f0 = 2u | 3u << 2 | 3u << 4 | 3u << 6;
    printf("%x %x %x %x %x\n", xenos_gamma_mask(f0, 0x688), xenos_gamma_mask(f0, 0x60a), xenos_gamma_mask(f0, 0x920),
           xenos_gamma_mask(2u | 3u << 8, 0x60a), xenos_gamma_mask(f0, 0xb6d));   /* identity; z y x w; x from x, rest constant 0; only w gamma; all constants */
    printf("%g %g %g %g %g %g %g\n", xenos_pwl_to_linear(0) * 1024, xenos_pwl_to_linear(63) * 1024, xenos_pwl_to_linear(64) * 1024,
           xenos_pwl_to_linear(96) * 1024, xenos_pwl_to_linear(128) * 1024, xenos_pwl_to_linear(192) * 1024, xenos_pwl_to_linear(255) * 1024);
    for (ch = 0; ch < 3; ch++)   /* an identity ramp: entry i base 8i, delta 8, each shifted left 6 */
        for (i = 0; i < 128; i++) pairs[ch * 128 + i] = (8u * i) << 22 | 8u << 6;
    for (i = 0; i < 1024; i++) bad += xenos_ramp_eval(pairs, 1, i) != i;
    pairs[256 + 4] = 100u << 22 | 40u << 6;   /* blue entry 4 (x 32..39): base 100, delta 40 */
    printf("%u %u %u %u\n", bad, xenos_ramp_eval(pairs, 2, 32), xenos_ramp_eval(pairs, 2, 36), xenos_ramp_eval(pairs, 0, 36));
    return 0;
}
'''


def d3d_compile(text, entry, profile="ps_5_0"):
    """Compiles with d3dcompiler_47 (the runtime's compiler); returns '' or the error text."""
    d3dc = ctypes.WinDLL("d3dcompiler_47.dll")
    code, err = ctypes.c_void_p(), ctypes.c_void_p()
    src = text.encode()
    hr = d3dc.D3DCompile(src, len(src), b"t", None, None, entry.encode(), profile.encode(), 0, 0, ctypes.byref(code), ctypes.byref(err))
    if hr == 0:
        return ""
    if not err.value:
        return "HRESULT %08x" % (hr & 0xffffffff)
    vt = ctypes.cast(err, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    ptr = ctypes.WINFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p)(vt[3])(err)
    return ctypes.string_at(ptr).decode(errors="replace")


def blit_source():
    """runtime/xb_gpu.c's blit_src, its C string pieces joined."""
    src = (RUNTIME / "xb_gpu.c").read_text(encoding="utf-8")
    body = src[src.index("static const char *const blit_src ="):]
    body = body[:body.index(";\n")]
    return "".join(re.findall(r'"((?:[^"\\]|\\.)*)"', body)).replace("\\n", "\n")


def pwl(v):
    return v if v < 64 else 2 * v - 64 if v < 96 else 4 * v - 256 if v < 192 else 8 * v - 1024


def test_converter_wraps_every_texture_fetch():
    src = (X360 / "port" / "tools" / "shader_convert.py").read_text(encoding="utf-8")
    assert 'xb_tg(tex%d.Sample(smp%d, %s), %du)' in src
    assert "uint4 xb_gam;" in src
    # the HLSL curve has the C helper's breakpoints
    assert "v < 64.0 ? v : v < 96.0 ? 2.0 * v - 64.0 : v < 192.0 ? 4.0 * v - 256.0 : 8.0 * v - 1024.0" in sc.GAMMA_FN
    assert [pwl(v) for v in (0, 63, 64, 96, 128, 192, 255)] == [0, 63, 64, 128, 256, 512, 1016]


@unittest.skipUnless(os.name == "nt", "the Direct3D compiler is Windows only")
class Hlsl(unittest.TestCase):
    def test_gamma_fn_compiles(self):
        text = "\n".join([
            "cbuffer Kit : register(b2) { float4 xb_vte; float4 xb_at; uint4 xb_gam; };",
            sc.GAMMA_FN,
            "Texture2D tex9 : register(t9); SamplerState smp9 : register(s9);",
            "float4 main(float4 p : SV_Position) : SV_Target0 { float4 r0 = 0; { float4 t = xb_tg(tex9.Sample(smp9, p.xy), 9u); r0.xyz = t.xyz; } return r0; }",
        ])
        self.assertEqual(d3d_compile(text, "main"), "")

    def test_present_ramp_compiles(self):
        blit = blit_source()
        self.assertIn("cbuffer Ramp : register(b4)", blit)
        self.assertEqual(d3d_compile(blit, "ramp"), "")
        self.assertEqual(d3d_compile(blit, "ps"), "")


@unittest.skipUnless(os.name == "nt" and vcvars64(), "no Visual Studio tools on this machine")
class Helpers(unittest.TestCase):
    def test_helpers(self):
        d = tmpdir("x360tg_")
        try:
            (d / "t.c").write_text(HARNESS, encoding="utf-8")
            rc, out = cl(d, '/I"%s" /Fet.exe t.c' % RUNTIME, timeout=300)
            self.assertEqual(rc, 0, out[-3000:])
            got = subprocess.run([str(d / "t.exe")], capture_output=True, text=True, timeout=60).stdout.split("\n")
        finally:
            shutil.rmtree(d, ignore_errors=True)
        self.assertEqual(got[0], "7 7 1 8 0")
        self.assertEqual(got[1], "0 63 64 128 256 512 1016")
        self.assertEqual(got[2], "0 100 120 36")   # the identity ramp holds; blue 36 = 100 + 40 * 4 / 8; red untouched
