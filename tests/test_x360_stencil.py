"""The Xbox 360 kit's depth-stencil state (xb_gpu_draw.h depth_for_draw): the game's stencil render states reach the host's state.

No game data: the function is cut from the kit's header and compiled with a stand-in device that records the description it is given."""
import os
import pathlib
import re
import shutil
import subprocess
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_xbox360_ppc import X360, tmpdir  # noqa: E402
from test_xbox360_glue import cl, vcvars64  # noqa: E402

HARNESS = r'''
#define COBJMACROS
#include <windows.h>
#include <d3d11.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
typedef unsigned int u32;
static unsigned rs[512];
static unsigned xb_rs(unsigned off) { return rs[off / 4]; }
static int xb_depth_on(void) { return 1; }
static void xb_stop(const char *a, const char *b, unsigned v) { (void)a; printf("stop %s %u\n", b, v); exit(3); }
static void port_log(const char *f, ...) { (void)f; }
static struct { float x, y, w, h, zmin, zmax; } s_vp = {0, 0, 1, 1, 0, 1};
static ID3D11DepthStencilState *s_depth_off = (ID3D11DepthStencilState *)1;
static D3D11_DEPTH_STENCIL_DESC last; static int nmade;
static HRESULT STDMETHODCALLTYPE create(ID3D11Device *d, const D3D11_DEPTH_STENCIL_DESC *ds, ID3D11DepthStencilState **out)
{ (void)d; last = *ds; *out = (ID3D11DepthStencilState *)(INT_PTR)(0x100 + nmade++); return 0; }
static ID3D11DeviceVtbl vt; static ID3D11Device dev = {&vt}; static ID3D11Device *s_dev = &dev;
FUNC
static void show(const char *what)
{
    ID3D11DepthStencilState *st = depth_for_draw();
    if (st == s_depth_off) { printf("%s off\n", what); return; }
    printf("%s made%d z%d w%d f%d s%d r%02x w%02x F%d%d%d%d B%d%d%d%d\n", what, nmade, last.DepthEnable, last.DepthWriteMask, last.DepthFunc, last.StencilEnable,
           last.StencilReadMask, last.StencilWriteMask, last.FrontFace.StencilFailOp, last.FrontFace.StencilDepthFailOp, last.FrontFace.StencilPassOp,
           last.FrontFace.StencilFunc, last.BackFace.StencilFailOp, last.BackFace.StencilDepthFailOp, last.BackFace.StencilPassOp, last.BackFace.StencilFunc);
}
int main(void)
{
    vt.CreateDepthStencilState = create;
    show("none");
    rs[108 / 4] = 1; rs[112 / 4] = 1; rs[120 / 4] = 6; rs[124 / 4] = 2; rs[128 / 4] = 5; rs[136 / 4] = 0xff; rs[140 / 4] = 0xff; rs[148 / 4] = 7;
    rs[152 / 4] = 0; rs[156 / 4] = 7; rs[116 / 4] = 0; rs[144 / 4] = 0;
    show("two-sided");
    show("again");
    rs[112 / 4] = 0;
    show("one-sided");
    rs[40 / 4] = 1; rs[44 / 4] = 3; rs[48 / 4] = 1;
    show("with-z");
    rs[108 / 4] = 0;
    show("z-only");
    xb_gpu_depth_word(0x36);   /* the device's word: z on, write on, less-equal; the render states say z off (an inline pre-pass) */
    rs[40 / 4] = 0; rs[48 / 4] = 0;
    show("word");
    xb_gpu_depth_word(0x60);   /* z off in the word, though render state 40 says on */
    rs[40 / 4] = 1;
    show("word-off");
    printf("dev-words %d", dev_words_on());   /* none given yet: off */
    xb_gpu_dev_words(0, 0xb, 6);
    printf(" %d %x %x %x\n", dev_words_on(), s_dw[0], s_dw[1], s_dw[2]);
    return 0;
}
'''


def cut_function():
    src = (X360 / "port" / "runtime" / "xb_gpu_draw.h").read_text(encoding="utf-8")
    start = src.rindex("\n", 0,src.index(" u32 s_dc;")) + 1   # the declaration line (thread-local since 2026-11-13)
    end = src.index("\n}\n", src.index("static ID3D11DepthStencilState *depth_for_draw(void)")) + 3
    return src[start:end]


def test_function_reads_every_stencil_state():
    body = cut_function()
    assert "s_dc >> 1 & 1u" in body and "s_dc >> 4 & 7u" in body
    for off in (108, 112, 116, 120, 124, 128, 136, 140, 144, 148, 152, 156):
        assert f"xb_rs({off})" in body
    draw = (X360 / "port" / "runtime" / "xb_gpu_draw.h").read_text(encoding="utf-8")
    assert re.search(r"OMSetDepthStencilState\(s_ctx, depth_for_draw\(\), xb_rs\(132\) & 255u\)", draw)


@unittest.skipUnless(os.name == "nt" and vcvars64(), "no Visual Studio tools on this machine")
class Stencil(unittest.TestCase):
    def test_states(self):
        d = tmpdir("x360st_")
        try:
            (d / "t.c").write_text(HARNESS.replace("FUNC", cut_function()), encoding="utf-8")
            rc, out = cl(d, "/wd4201 /Fet.exe t.c", timeout=300)   # 4201: the SDK headers' nameless unions
            self.assertEqual(rc, 0, out[-3000:])
            # The words are on by default again since 2026-11-14: got is the default (neither switch set), dflt the switches at 0
            on = {k: v for k, v in os.environ.items() if k not in ("XB_DEPTH_WORD", "XB_DEV_WORDS")}
            off = dict(os.environ, XB_DEPTH_WORD="0", XB_DEV_WORDS="0")
            got = subprocess.run([str(d / "t.exe")], capture_output=True, text=True, timeout=60, env=on).stdout.split("\n")
            dflt = subprocess.run([str(d / "t.exe")], capture_output=True, text=True, timeout=60, env=off).stdout.split("\n")
        finally:
            shutil.rmtree(d, ignore_errors=True)
        self.assertEqual(got[0], "none off")
        # z off, stencil two-sided: front z-fail increment (6 -> host 7), pass replace (2 -> 3), not-equal (5 -> 6); back z-fail decrement (7 -> 8), always (7 -> 8)
        self.assertEqual(got[1], "two-sided made1 z0 w0 f8 s1 rff wff F1736 B1818")
        self.assertEqual(got[2], "again made1 z0 w0 f8 s1 rff wff F1736 B1818")   # cached, not made again
        self.assertEqual(got[3], "one-sided made2 z0 w0 f8 s1 rff wff F1736 B1736")   # back takes the front's states
        self.assertEqual(got[4], "with-z made3 z1 w1 f4 s1 rff wff F1736 B1736")   # less-equal 3 -> host 4
        self.assertEqual(got[5], "z-only made4 z1 w1 f4 s0 r00 w00 F0000 B0000")
        self.assertEqual(got[6], "word made4 z1 w1 f4 s0 r00 w00 F0000 B0000")   # the word's state, the same as z-only: cached
        self.assertEqual(got[7], "word-off off")
        self.assertEqual(got[8], "dev-words 0 1 0 b 6")   # the colour mask, colour control and mode control words, kept as given
        # XB_DEPTH_WORD=0 XB_DEV_WORDS=0: render states 40, 44 and 48 decide; the device words are kept but not used
        self.assertEqual(dflt[:6], got[:6])
        self.assertEqual(dflt[6], "word off")   # states 40 and 48 say z off; the word's z on is not read
        self.assertTrue(dflt[7].startswith("word-off made5 z1 w0 f4 s0"), dflt[7])   # state 40 on, 44 less-equal, 48 write off
        self.assertEqual(dflt[8], "dev-words 0 0 0 b 6")
