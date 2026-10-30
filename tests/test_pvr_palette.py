"""Palettized textures (port/pvr/pvr_twiddle.c with palette RAM, port/pvr/pvr_pal.c): the real decoder, compiled at
/W4 /WX and run on small hand-made textures and palettes in an emulated texture memory. Each expected pixel is
worked by hand from the PowerVR's rules (twiddled order with y in the even bits; 4-bit indices low nibble first; the
TCW's palette selector in bits 26..21: entries selector * 16.. for 4-bit, (selector & 0x30) * 16.. for 8-bit; a
16-bit palette format in each entry's low half, its channels widened by repeating their top bits; mip levels
smallest first, the 1x1 level at texel 3):
1. 8-bit, ARGB8888, selector 0x30 (entries 768..): pixel (x, y) is entry 768 + its twiddled index;
2. 4-bit, ARGB1555, selector 5 (entries 80..95): the nibbles, the 1555 alpha bit and the widened channels;
3. 8-bit mip-mapped 4x4, RGB565, selector 0: the 4x4, 2x2 and 1x1 levels from texels 8, 4 and 3; 24 bytes read;
4. 4-bit, ARGB4444, selector 0x3f: TCW bits 26 and 25 are selector bits there, not the rectangle and stride bits,
   so the texture is still read twiddled;
5. the palette stamps the texture cache (pvr_tex.c) compares: a write changes only the 16-entry banks it touches,
   rewriting the same entries changes nothing, and a format change changes every bank.
6. VQ-compressed palettized textures are refused (0 levels).
Only where Visual Studio's 32-bit tools are found (tests/test_lockstep.py NO_VC says how), skipped elsewhere.

usage: python -W error tests/test_pvr_palette.py
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
#include <string.h>
#include "pvr_int.h"
#include "platform.h"
static u8 g_vram[PVRX_VRAM_SIZE];
static u32 g_out[4096], g_pal[1024];
u8 *pvr_vram(void) { return g_vram; }
void pvr_todo(const char *fmt, ...) { (void)fmt; }
void port_log(const char *fmt, ...) { (void)fmt; }
void port_extra_undo(void *p, u32 size, PortUndoFn after) { (void)p, (void)size, (void)after; }
static void px(const char *name, u32 w, u32 x, u32 y) { printf("%s %u %u %08x\n", name, x, y, g_out[y * w + x]); }
int main(void)
{
    u32 i, levels, s0, s1, s2, s3;
    /* 1. 8-bit 8x8 at 0x1000, ARGB8888, entries 768 + i = 0x400c5a00 | i (A 40, R 0c, G 5a, B i); others junk */
    for (i = 0; i < 1024u; i++) g_pal[i] = i >= 768u ? 0x400c5a00u | (i - 768u) : 0xdeadbeefu;
    pvr_set_palette(0, g_pal, 1024);
    pvr_set_palette_format(3);
    for (i = 0; i < 64u; i++) g_vram[0x1000 + i] = (u8)i;
    levels = pvr_decode(6u << 27 | 0x30u << 21 | 0x1000u / 8u, 8, 8, 0, g_out, 4096);
    printf("pal8 %u %u\n", levels, pvr_texture_bytes(6u << 27 | 0x30u << 21, 8, 8, 0));
    px("pal8", 8, 0, 0), px("pal8", 8, 1, 0), px("pal8", 8, 0, 1), px("pal8", 8, 2, 0), px("pal8", 8, 3, 5);
    /* 2. 4-bit 8x8 at 0x2000, ARGB1555, selector 5: entry 80 + j = 0xffff0000 (junk high half) | a=j&1, r=2j, g=31-j, b=j;
       byte b holds nibbles 2b (low) and 2b+1 (high), each a texel's twiddled index & 15 */
    for (i = 0; i < 16u; i++) g_pal[80 + i] = 0xffff0000u | (i & 1u) << 15 | (2u * i) << 10 | (31u - i) << 5 | i;
    pvr_set_palette(0, g_pal, 1024);
    pvr_set_palette_format(0);
    for (i = 0; i < 32u; i++) g_vram[0x2000 + i] = (u8)(((2u * i + 1u) & 15u) << 4 | ((2u * i) & 15u));
    levels = pvr_decode(5u << 27 | 5u << 21 | 0x2000u / 8u, 8, 8, 0, g_out, 4096);
    printf("pal4 %u %u %02x\n", levels, pvr_texture_bytes(5u << 27 | 5u << 21, 8, 8, 0), g_vram[0x2001]);
    px("pal4", 8, 1, 1), px("pal4", 8, 2, 0), px("pal4", 8, 3, 5);
    /* 3. 8-bit mip-mapped 4x4 at 0x3000, RGB565, selector 0: entry k = k << 11 | (63 - k) << 5 | k; byte t = t */
    for (i = 0; i < 64u; i++) g_pal[i] = i << 11 | (63u - i) << 5 | i;
    pvr_set_palette(0, g_pal, 1024);
    pvr_set_palette_format(1);
    for (i = 0; i < 24u; i++) g_vram[0x3000 + i] = (u8)i;
    levels = pvr_decode(1u << 31 | 6u << 27 | 0x3000u / 8u, 4, 4, 0, g_out, 4096);
    printf("mip %u %u %u\n", levels, pvr_texture_bytes(1u << 31 | 6u << 27, 4, 4, 0),
           pvr_decode_texels(1u << 31 | 6u << 27, 4, 4));
    px("mip4", 4, 3, 3);
    printf("mip2 %08x\nmip1 %08x\n", g_out[16 + 1], g_out[20]);
    /* 4. 4-bit 8x8 at 0x2000 again, ARGB4444, selector 0x3f (entries 1008..): entry 1008 + j = 0xf000 | j << 8 | j << 4 | (15 - j) */
    for (i = 0; i < 16u; i++) g_pal[1008 + i] = 0xf000u | i << 8 | i << 4 | (15u - i);
    pvr_set_palette(0, g_pal, 1024);
    pvr_set_palette_format(2);
    levels = pvr_decode(5u << 27 | 0x3fu << 21 | 0x2000u / 8u, 8, 8, 0, g_out, 4096);
    printf("sel %u\n", levels);
    px("sel", 8, 1, 0);
    /* 5. stamps */
    s0 = pvr_palette_stamp(0, 16), s1 = pvr_palette_stamp(16, 16), s2 = pvr_palette_stamp(768, 256);
    g_pal[20] ^= 1u;
    pvr_set_palette(0, g_pal, 1024);
    printf("stamp %d %d %d\n", pvr_palette_stamp(0, 16) == s0, pvr_palette_stamp(16, 16) == s1,
           pvr_palette_stamp(768, 256) == s2);
    s1 = pvr_palette_stamp(16, 16);
    pvr_set_palette(16, g_pal + 16, 16);
    s3 = pvr_palette_stamp(16, 16);
    pvr_set_palette_format(0);
    printf("stamp2 %d %d %d %08x\n", s3 == s1, pvr_palette_stamp(0, 16) == s0, pvr_palette_stamp(768, 256) == s2,
           pvr_palette_entry(20));
    /* 6. VQ palettized */
    printf("vq %u\n", pvr_decode(1u << 30 | 6u << 27 | 0x1000u / 8u, 8, 8, 0, g_out, 4096));
    return 0;
}
"""


@unittest.skipUnless(HAVE_VC, NO_VC)
class PaletteTextures(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        work = TOOL / "tests" / ".work"
        d = pathlib.Path(tempfile.mkdtemp(dir=work if work.is_dir() else None))
        cls.dir = d
        (d / "port_config.h").write_text(game_config.port_config_h(
            {"name": "Fixture", "entry": {"name": "Fx_Main", "addr": "8c011000"}, "image_base": "8c010000",
             "boot_file": "", "console": None, "id": "FIXTURE", "boot_sha256": []}), encoding="utf-8")
        (d / "pal_main.c").write_text(DRIVER_C, encoding="utf-8")
        rd = TOOL / "targets" / "dreamcast_sh4" / "runtime_rd"
        srcs = " ".join(f'"{p}"' for p in [KIT / "pvr" / "pvr_twiddle.c", KIT / "pvr" / "pvr_pal.c", d / "pal_main.c"])
        cl = (f'cl /nologo /TC /std:c11 /W4 /WX /O2 /I"{d}" /I"{KIT / "runtime"}" /I"{KIT / "pvr"}" '
              f'/I"{KIT / "host"}" /I"{rd}" {srcs} /Fe:pal.exe')
        vc = str(game_config.vcvars32()).replace("/", "\\")
        r = subprocess.run(f'cmd /d /s /c ""{vc}" >nul 2>&1 && {cl}"', cwd=d, capture_output=True, text=True,
                           errors="replace", timeout=300)
        if r.returncode or "warning" in r.stdout.lower():
            shutil.rmtree(d, ignore_errors=True)
            raise RuntimeError(r.stdout[-3000:] + r.stderr[-1000:])
        run = subprocess.run([str(d / "pal.exe")], capture_output=True, text=True, timeout=60)
        if run.returncode:
            raise RuntimeError(run.stdout + run.stderr)
        cls.out = run.stdout.splitlines()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def test_8bit_argb8888_selector(self):
        # twiddled index of (x, y): y in the even bits, x in the odd ones; (3, 5) = 1 + 2 + 8 + 16 = 27.
        # Entry 0x400c5a00 | i is A 40, R 0c, G 5a, B i: as RGBA8 bytes (R | G << 8 | B << 16 | A << 24) 0x40ii5a0c
        self.assertIn("pal8 1 64", self.out)  # one level, 64 bytes
        for line in ("pal8 0 0 40005a0c", "pal8 1 0 40025a0c", "pal8 0 1 40015a0c", "pal8 2 0 40085a0c",
                     "pal8 3 5 401b5a0c"):
            self.assertIn(line, self.out)

    def test_4bit_argb1555(self):
        self.assertIn("pal4 1 32 32", self.out)  # 32 bytes; byte 1 holds texel 2 (low nibble) and 3 (high)
        # (1, 1): texel 3, entry 83: a 1, r 6 -> 0x31, g 28 -> 0xe7, b 3 -> 0x18
        self.assertIn("pal4 1 1 ff18e731", self.out)
        # (2, 0): texel 8, entry 88: a 0, r 16 -> 0x84, g 23 -> 0xbd, b 8 -> 0x42
        self.assertIn("pal4 2 0 0042bd84", self.out)
        # (3, 5): texel 27, nibble 11, entry 91: a 1, r 22 -> 0xb5, g 20 -> 0xa5, b 11 -> 0x5a
        self.assertIn("pal4 3 5 ff5aa5b5", self.out)

    def test_8bit_mips_rgb565(self):
        self.assertIn("mip 3 24 21", self.out)  # 3 levels; texels 3 + 4 + 16 = 24 bytes; 16 + 4 + 1 out
        # 4x4 (3, 3): texel 8 + 15 = 23: r 23 -> 0xbd, g 40 -> 0xa2, b 23 -> 0xbd
        self.assertIn("mip4 3 3 ffbda2bd", self.out)
        # 2x2 (1, 0): texel 4 + 2 = 6: r 6 -> 0x31, g 57 -> 0xe7, b 6 -> 0x31
        self.assertIn("mip2 ff31e731", self.out)
        # 1x1: texel 3: r 3 -> 0x18, g 60 -> 0xf3, b 3 -> 0x18
        self.assertIn("mip1 ff18f318", self.out)

    def test_selector_bits_are_not_layout_bits(self):
        self.assertIn("sel 1", self.out)
        # (1, 0) twiddled is texel 2 (a rectangle would read texel 1): entry 1010: a f, r 2, g 2, b 13 -> ffdd2222
        self.assertIn("sel 1 0 ffdd2222", self.out)

    def test_stamps(self):
        self.assertIn("stamp 1 0 1", self.out)  # entry 20 changed: only bank 16..31 is new
        self.assertIn("stamp2 1 0 0 0000a575", self.out)  # same entries again: unchanged; a format change: all new

    def test_vq_refused(self):
        self.assertIn("vq 0", self.out)


if __name__ == "__main__":
    unittest.main()
