"""The Xbox 360 kit's fast texture decode (runtime/xb_d3d_tex.h decode_image_fast) gives the reference route's bytes exactly.

No game data: the decode functions are cut out of the header and compiled with a small harness on random guest memory. Every format the
decoder has a case for, every byte order, tiled and not, several swizzles and sizes (1x1, 33x17, 256x256, 1280x720 and odd shapes);
the fast output must equal decode_image_ref's, byte for byte."""
import os
import pathlib
import shutil
import subprocess
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_xbox360_ppc import X360, tmpdir  # noqa: E402
from test_xbox360_glue import cl, vcvars64  # noqa: E402

HEADER = X360 / "port" / "runtime" / "xb_d3d_tex.h"

PREAMBLE = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <intrin.h>
typedef unsigned int u32;
typedef unsigned char u8;
static u8 *tc_base;
static __forceinline u32 rd32(u32 a) { return _byteswap_ulong(*(const u32 *)(tc_base + a)); }
'''

MAIN = r'''
static u32 s_seed = 12345u;
static u32 rnd(void) { s_seed ^= s_seed << 13; s_seed ^= s_seed >> 17; s_seed ^= s_seed << 5; return s_seed; }

int main(void)
{
    static const u32 fmts[] = {2, 3, 4, 10, 15, 6, 7, 18, 19, 20, 49};
    static const u32 swzs[] = {0x688, 0x60a, 0x920, 0xb6d, 0x2f1};
    static const u32 sizes[][2] = {{1, 1}, {33, 17}, {7, 100}, {100, 5}, {256, 256}, {1280, 720}};
    const size_t mem = 48u << 20;
    unsigned cases = 0, si, fi, e, tiled, wi;
    size_t i;
    tc_base = (u8 *)malloc(mem);
    if (!tc_base) return 2;
    for (i = 0; i < mem; i++) tc_base[i] = (u8)(rnd() >> 11);
    for (si = 0; si < sizeof sizes / sizeof sizes[0]; si++) {
        u32 w = sizes[si][0], h = sizes[si][1], pitch = (w + 31) & ~31u;
        u32 *a = (u32 *)malloc((size_t)w * h * 4), *b = (u32 *)malloc((size_t)w * h * 4);
        unsigned nswz = (size_t)w * h > 100000 ? 2 : 5;
        for (fi = 0; fi < sizeof fmts / sizeof fmts[0]; fi++) {
            u32 fmt = fmts[fi], bw = fmt >= 18 ? 4 : 1;
            u32 bpb = fmt == 2 ? 1 : (fmt == 3 || fmt == 4 || fmt == 10 || fmt == 15) ? 2 : (fmt == 6 || fmt == 7) ? 4 : fmt == 18 ? 8 : 16;
            for (e = 0; e < 4; e++)
                for (tiled = 0; tiled < 2; tiled++)
                    for (wi = 0; wi < nswz; wi++) {
                        u32 base = 0x1000u + 0x1000u * (rnd() & 7);
                        memset(a, 0xAA, (size_t)w * h * 4);
                        memset(b, 0x55, (size_t)w * h * 4);
                        decode_image_ref(a, base, w, h, pitch, tiled, fmt, e, swzs[wi], bw, bpb);
                        decode_image_fast(b, base, w, h, pitch, tiled, fmt, e, swzs[wi], bw, bpb);
                        cases++;
                        if (memcmp(a, b, (size_t)w * h * 4)) {
                            printf("MISMATCH %ux%u fmt %u endian %u tiled %u swz %03x\n", w, h, fmt, e, tiled, swzs[wi]);
                            return 1;
                        }
                    }
        }
        free(a);
        free(b);
    }
    printf("ok %u\n", cases);
    return 0;
}
'''


def cut():
    """The header's tiled_block .. dxt_alpha block and its decode functions (up to xb_slot_fetch)."""
    s = HEADER.read_text(encoding="utf-8")
    a0 = s.index("/* The block of (x, y) in a tiled surface")
    a1 = s.index("/* The host copy of the texture in fetch slot")
    b0 = s.index("/* One face (or the only image)")
    b1 = s.index("u32 xb_slot_fetch[16][4];")
    return s[a0:a1] + "\n" + s[b0:b1]


@unittest.skipUnless(os.name == "nt" and vcvars64(), "no Visual Studio tools on this machine")
class TexFast(unittest.TestCase):
    def test_fast_equals_reference(self):
        d = tmpdir("x360tf_")
        try:
            (d / "t.c").write_text(PREAMBLE + cut() + MAIN, encoding="utf-8")
            rc, out = cl(d, "/wd4505 /wd4701 /wd4703 /wd4127 /wd4100 /Fet.exe t.c", timeout=300)
            self.assertEqual(rc, 0, out[-3000:])
            r = subprocess.run([str(d / "t.exe")], capture_output=True, text=True, timeout=600)
        finally:
            shutil.rmtree(d, ignore_errors=True)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertTrue(r.stdout.startswith("ok "), r.stdout)


def test_switch_is_there():
    src = HEADER.read_text(encoding="utf-8")
    assert "XB_TEX_FAST" in src and "decode_image_ref" in src and "decode_image_fast" in src
