"""The texture decoder (port/pvr/pvr_twiddle.c) gives the same texels as the decoder it replaced, for every layout it
reads. The faster one (bit-spread twiddling, one palette decode a level, YUV products from tables, one loop a
format) is compiled next to tests/fixtures/pvr_twiddle_reference.c, the decoder as it was before (symbols renamed
with /D), and both decode the same random texture memory under random palettes:
1. twiddled 16-bit textures (ARGB1555, RGB565, ARGB4444), square and 2:1 / 1:2 rectangles, 8x8 to 1024x1024;
2. mip-mapped square ones (every level) and VQ-compressed ones, also mip-mapped;
3. palettized 4-bit and 8-bit, each palette format (16-bit ones and ARGB8888), with the selector bits set;
4. the rectangle and stride layouts in the three 16-bit formats and YUV422, with widths, strides and addresses that
   put the YUV pairs on and across row ends.
Each case compares the return value and every texel written. Only where Visual Studio's 32-bit tools are found
(tests/test_lockstep.py NO_VC says how), skipped elsewhere.

usage: python -W error tests/test_pvr_decode_same.py
Nearest existing: tests/test_pvr_palette.py - it works a few textures by hand; this one compares the whole decoder
against its predecessor on thousands of random ones, which is what a rewrite for speed needs.
"""
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[1]
KIT = TOOL / "port"
sys.path.insert(0, str(KIT / "tools"))
sys.path.insert(0, str(TOOL / "tests"))

import game_config  # noqa: E402
from test_lockstep import HAVE_VC, NO_VC  # noqa: E402

DRIVER_C = r"""#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "pvr_int.h"
#include "platform.h"
static u8 g_vram[PVRX_VRAM_SIZE];
static u32 g_fmt, g_seed = 12345u;
static u32 g_a[1400000], g_b[1400000];
u8 *pvr_vram(void) { return g_vram; }
void pvr_todo(const char *fmt, ...) { (void)fmt; }
void port_log(const char *fmt, ...) { (void)fmt; }
void port_extra_undo(void *p, u32 size, PortUndoFn after) { (void)p, (void)size, (void)after; }
u32 pvr_palette_entry(u32 index) { return (index + 1u) * 2654435761u ^ (index >> 3) * 40503u; }
u32 pvr_palette_format(void) { return g_fmt; }
u32 ref_decode(u32 tcw, u32 w, u32 h, u32 stride, u32 *out, u32 out_texels);
u32 ref_decode_texels(u32 tcw, u32 w, u32 h);
u32 ref_texture_bytes(u32 tcw, u32 w, u32 h, u32 stride);
static u32 rnd(void)
{
    g_seed ^= g_seed << 13;
    g_seed ^= g_seed >> 17;
    g_seed ^= g_seed << 5;
    return g_seed;
}
static u32 cases, bad;
static void one(u32 tcw, u32 w, u32 h, u32 stride, const char *what)
{
    u32 n = ref_decode_texels(tcw, w, h), ra, rb;
    memset(g_a, 0x5a, n * 4u);
    memset(g_b, 0x5a, n * 4u);
    ra = ref_decode(tcw, w, h, stride, g_a, n);
    rb = pvr_decode(tcw, w, h, stride, g_b, n);
    cases++;
    if (ra != rb || memcmp(g_a, g_b, n * 4u) || ref_decode_texels(tcw, w, h) != pvr_decode_texels(tcw, w, h) ||
        ref_texture_bytes(tcw, w, h, stride) != pvr_texture_bytes(tcw, w, h, stride)) {
        bad++;
        if (bad < 20u) printf("MISMATCH %s tcw %08x %ux%u stride %u (levels %u vs %u)\n", what, tcw, w, h, stride, ra, rb);
    }
}
#define MIP (1u << 31)
#define VQ (1u << 30)
#define FMT(f) ((u32)(f) << 27)
#define RECT (1u << 26)
#define STRIDE (1u << 25)
int main(void)
{
    static const u32 sizes[] = {8, 16, 32, 64, 128, 256, 512, 1024};
    for (u32 i = 0; i < PVRX_VRAM_SIZE; i++) g_vram[i] = (u8)(rnd() >> 11);
    for (u32 si = 0; si < 8u; si++) {
        u32 s = sizes[si];
        for (u32 f = 0; f < 3u; f++) {
            u32 addr = rnd() & 0xfffffu;
            g_fmt = f;
            one(FMT(f) | addr, s, s, 0, "twiddled");
            if (si < 7u) {
                one(FMT(f) | addr, s * 2u, s, 0, "twiddled 2:1");
                one(FMT(f) | addr, s, s * 2u, 0, "twiddled 1:2");
            }
            if (s <= 256u) {
                one(MIP | FMT(f) | addr, s, s, 0, "mip");
                one(VQ | FMT(f) | addr, s, s, 0, "vq");
                one(MIP | VQ | FMT(f) | addr, s, s, 0, "vq mip");
            }
        }
        for (u32 pf = 0; pf < 4u; pf++)
            for (u32 sel = 0; sel < 64u; sel += 9u) {
                u32 addr = rnd() & 0xfffffu;
                g_fmt = pf;
                one(FMT(5) | sel << 21 | addr, s, s, 0, "pal4");
                one(FMT(6) | sel << 21 | addr, s, s, 0, "pal8");
                if (s <= 256u) {
                    one(MIP | FMT(5) | sel << 21 | addr, s, s, 0, "pal4 mip");
                    one(MIP | FMT(6) | sel << 21 | addr, s, s, 0, "pal8 mip");
                }
            }
        for (u32 f = 0; f < 4u; f++)
            for (u32 k = 0; k < 6u; k++) {
                u32 addr = (rnd() & 0xfffffu) / 8u, w = s - (k == 4u ? 2u : 0u), h = s > 8u ? s / 2u + k : s, stride = w + k * 3u;
                g_fmt = f;
                one(RECT | FMT(f) | addr, w, h, 0, "rect");
                one(RECT | STRIDE | FMT(f) | addr, w, h, stride, "stride");
                one(RECT | STRIDE | FMT(f) | addr, w, h, stride | 1u, "stride odd");
            }
    }
    printf("cases %u mismatches %u\n", cases, bad);
    return bad != 0;
}
"""


@unittest.skipUnless(HAVE_VC, NO_VC)
class DecoderMatchesPredecessor(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        work = TOOL / "tests" / ".work"
        d = pathlib.Path(tempfile.mkdtemp(dir=work if work.is_dir() else None))
        cls.dir = d
        (d / "port_config.h").write_text(game_config.port_config_h(
            {"name": "Fixture", "entry": {"name": "Fx_Main", "addr": "8c011000"}, "image_base": "8c010000",
             "boot_file": "", "console": None, "id": "FIXTURE", "boot_sha256": []}), encoding="utf-8")
        (d / "dec_main.c").write_text(DRIVER_C, encoding="utf-8")
        rd = TOOL / "targets" / "dreamcast_sh4" / "runtime_rd"
        inc = (f'/I"{d}" /I"{KIT / "runtime"}" /I"{KIT / "pvr"}" /I"{KIT / "base"}" /I"{rd}"')
        ren = " ".join(f"/D{n}=ref_{n[4:]}" for n in ("pvr_decode", "pvr_decode_texels", "pvr_texture_bytes",
                                                       "pvr_tcw_layout", "pvr_palette_range"))
        vc = str(game_config.vcvars32()).replace("/", "\\")
        opts = f"cl /nologo /TC /std:c11 /W4 /WX /O2 {inc}"
        cl = (f'{opts} "{KIT / "pvr" / "pvr_twiddle.c"}" /Fo:new_twiddle.obj /c && '
              f'{opts} "{d / "dec_main.c"}" /Fo:dec_main.obj /c && '
              f'{opts} {ren} "{TOOL / "tests" / "fixtures" / "pvr_twiddle_reference.c"}" /Fo:ref_twiddle.obj /c && '
              f'cl /nologo new_twiddle.obj dec_main.obj ref_twiddle.obj /Fe:dec.exe')
        r = subprocess.run(f'cmd /d /s /c ""{vc}" >nul 2>&1 && {cl}"', cwd=d, capture_output=True, text=True,
                           errors="replace", timeout=600)
        if r.returncode or "warning" in r.stdout.lower():
            shutil.rmtree(d, ignore_errors=True)
            raise RuntimeError(r.stdout[-3000:] + r.stderr[-1000:])
        run = subprocess.run([str(d / "dec.exe")], capture_output=True, text=True, timeout=600)
        cls.out = run.stdout.splitlines()
        cls.code = run.returncode

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def test_same_texels(self):
        self.assertEqual(self.code, 0, "\n".join(self.out))
        last = self.out[-1].split()
        self.assertGreater(int(last[1]), 500)  # the cases that ran
        self.assertEqual(last[3], "0")


if __name__ == "__main__":
    unittest.main()
