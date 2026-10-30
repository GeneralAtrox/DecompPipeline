"""Texture packs (port/pvr/pvr_pack.c, port/tools/texture_pack.py): the renderer's real key and lookup, compiled at
/W4 /WX with the real decoder (pvr_twiddle.c) and run on synthetic textures in an emulated texture memory:
1. the C key equals texture_pack.key() for VQ, VQ + mip-map, twiddled, twiddled + mip-map (whose 6 bytes before the
   1x1 level are not part of it) and rectangle textures; any texel or codebook byte changes it, the address does not;
2. a pack hit gives the pack's image (any size), a mip-mapped guest its box-filtered chain; a miss and a pack switched
   off give nothing, so the decoder's output is what the renderer uploads, unchanged;
3. a palettized texture (pixel formats 5 and 6) is never replaced, even when the pack holds its key: its texels depend
   on palette RAM, which the key does not hash.
Only where Visual Studio's 32-bit tools are found (tests/test_lockstep.py NO_VC says how), skipped elsewhere.

build_driver() is also what a game's before/after check runs (pvr_pack_driver.exe VRAM CASES [DIR|off] [OUT]: per
case line "tcw w h stride", prints "key levels w h" and writes OUT/N_before.rgba, OUT/N_after.rgba).

usage: python -W error tests/test_pvr_pack.py
"""
import pathlib
import random
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib

TOOL = pathlib.Path(__file__).resolve().parents[1]
KIT = TOOL / "port"
sys.path.insert(0, str(KIT / "tools"))
sys.path.insert(0, str(TOOL / "tests"))

import game_config  # noqa: E402
import texture_pack as tp  # noqa: E402
from test_lockstep import HAVE_VC, NO_VC  # noqa: E402

DRIVER_C = r"""#define _CRT_SECURE_NO_WARNINGS
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "pvr_int.h"
#include "platform.h"
int pvr_pack_option(const char *arg, const char *value);
static u8 g_vram[PVRX_VRAM_SIZE];
static u32 g_out[1u << 20];
u8 *pvr_vram(void) { return g_vram; }
void pvr_todo(const char *fmt, ...) { (void)fmt; }
void port_log(const char *fmt, ...) { (void)fmt; }
void port_extra_undo(void *p, u32 size, PortUndoFn after) { (void)p, (void)size, (void)after; }
static void save(const char *dir, int n, const char *what, const u32 *px, u32 count)
{
    char path[512];
    FILE *f;
    snprintf(path, sizeof path, "%s/%d_%s.rgba", dir, n, what);
    if ((f = fopen(path, "wb")) != NULL) {
        fwrite(px, 4, count, f);
        fclose(f);
    }
}
int main(int argc, char **argv)
{
    FILE *f = fopen(argv[1], "rb");
    unsigned tcw, w, h, stride;
    int n = 0;
    if (!f) return 2;
    fread(g_vram, 1, sizeof g_vram, f);
    fclose(f);
    if (argc > 3) pvr_pack_option("--texture-pack", argv[3]);
    f = fopen(argv[2], "r");
    if (!f) return 2;
    while (fscanf(f, "%x %u %u %u", &tcw, &w, &h, &stride) == 4) {
        const u32 *px = NULL;
        u32 pw = 0, ph = 0, levels = pvr_pack_levels(tcw, w, h, stride, &px, &pw, &ph), total = 0;
        printf("%016llx %u %u %u\n", (unsigned long long)pvr_pack_key(g_vram, tcw, w, h, stride), levels, pw, ph);
        if (argc > 4) {
            u32 dl = pvr_decode(tcw, w, h, stride, g_out, (u32)(sizeof g_out / 4));
            save(argv[4], n, "before", g_out, dl ? w * h : 0);
            for (u32 i = 0; i < levels; i++) total += (pw >> i ? pw >> i : 1u) * (ph >> i ? ph >> i : 1u);
            save(argv[4], n, "after", levels ? px : g_out, levels ? total : (dl ? pvr_decode_texels(tcw, w, h) : 0));
        }
        n++;
    }
    fclose(f);
    return 0;
}
"""


def build_driver(d):
    """Compiles the driver into folder d (with a fixture port_config.h); returns its path. Raises on a warning."""
    d = pathlib.Path(d)
    (d / "port_config.h").write_text(game_config.port_config_h(
        {"name": "Fixture", "entry": {"name": "Fx_Main", "addr": "8c011000"}, "image_base": "8c010000",
         "boot_file": "", "console": None, "id": "FIXTURE", "boot_sha256": []}), encoding="utf-8")
    (d / "pack_main.c").write_text(DRIVER_C, encoding="utf-8")
    rd = TOOL / "targets" / "dreamcast_sh4" / "runtime_rd"
    srcs = " ".join(f'"{p}"' for p in [KIT / "pvr" / "pvr_pack.c", KIT / "pvr" / "pvr_twiddle.c",
                                       KIT / "pvr" / "pvr_pal.c", d / "pack_main.c"])
    cl = (f'cl /nologo /TC /std:c11 /W4 /WX /O2 /I"{d}" /I"{KIT / "runtime"}" /I"{KIT / "pvr"}" /I"{KIT / "host"}" '
          f'/I"{rd}" {srcs} /Fe:pvr_pack_driver.exe /link windowscodecs.lib ole32.lib')
    vc = str(game_config.vcvars32()).replace("/", "\\")
    r = subprocess.run(f'cmd /d /s /c ""{vc}" >nul 2>&1 && {cl}"', cwd=d, capture_output=True, text=True,
                       errors="replace", timeout=300)
    if r.returncode or "warning" in r.stdout.lower():
        raise RuntimeError(r.stdout[-3000:] + r.stderr[-1000:])
    return d / "pvr_pack_driver.exe"


def run_driver(exe, vram, cases, pack=None, out=None):
    """cases: [(tcw, w, h, stride)]; returns [(key, levels, w, h)]."""
    d = exe.parent
    (d / "vram.bin").write_bytes(bytes(vram))
    (d / "cases.txt").write_text("".join(f"{c[0]:08x} {c[1]} {c[2]} {c[3]}\n" for c in cases), encoding="ascii")
    args = [str(exe), str(d / "vram.bin"), str(d / "cases.txt")] + ([str(pack)] if pack else []) + \
        ([str(out)] if out else [])
    r = subprocess.run(args, capture_output=True, text=True, timeout=120)
    if r.returncode:
        raise RuntimeError(r.stdout + r.stderr)
    return [(int(a, 16), int(b), int(c), int(e)) for a, b, c, e in (ln.split() for ln in r.stdout.splitlines())]


def png_rgba(data):
    """The pixels of a PNG written by texture_pack.png (RGBA8, filter 0)."""
    w, h = struct.unpack(">II", data[16:24])
    raw = zlib.decompress(data[data.index(b"IDAT") + 4:data.index(b"IEND") - 8])
    return w, h, b"".join(raw[y * (w * 4 + 1) + 1:(y + 1) * (w * 4 + 1)] for y in range(h))


@unittest.skipUnless(HAVE_VC, NO_VC)
class TexturePack(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        work = TOOL / "tests" / ".work"
        cls.dir = pathlib.Path(tempfile.mkdtemp(dir=work if work.is_dir() else None))
        cls.exe = build_driver(cls.dir)
        rnd = random.Random(7)
        cls.vram = bytearray(rnd.getrandbits(8) for _ in range(0x60000))
        cls.vram += bytes(tp.FNV_BASIS.to_bytes(8, "little")) * ((0x800000 - len(cls.vram)) // 8)
        # (tcw: format bits | address / 8, w, h, stride)
        cls.cases = [(tp.VQ | 1 << 27 | 0x0000 // 8, 64, 64, 0),                  # VQ 565
                     (tp.VQ | tp.MIP | 2 << 27 | 0x2000 // 8, 64, 64, 0),         # VQ + mip, 4444
                     (1 << 27 | 0x8000 // 8, 32, 64, 0),                          # twiddled 565
                     (tp.MIP | 0x10000 // 8, 32, 32, 0),                          # twiddled + mip, 1555
                     (tp.NON_TWIDDLED | 1 << 27 | 0x18000 // 8, 16, 8, 0)]        # rectangle

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def at(self, case):
        a = (case[0] & 0x1fffff) << 3
        return self.vram[a:a + tp.texture_bytes(case[0], case[1], case[2], case[3])]

    def test_keys_match_python(self):
        got = run_driver(self.exe, self.vram, self.cases)
        self.assertEqual([g[0] for g in got], [tp.key(c[0], c[1], c[2], self.at(c), c[3]) for c in self.cases])
        self.assertEqual(len({g[0] for g in got}), len(got))
        self.assertEqual([g[1] for g in got], [0] * len(got))  # no pack: nothing replaced

    def test_key_follows_the_bytes(self):
        vq, mip16 = self.cases[0], self.cases[3]
        base = run_driver(self.exe, self.vram, [vq, mip16])
        moved = bytearray(self.vram)
        moved[0x40000:0x40000 + 0x4800] = self.vram[0:0x4800]
        lead = bytearray(self.vram)
        lead[0x10000:0x10006] = b"\xaa" * 6           # before the 1x1 level: not texels
        book = bytearray(self.vram)
        book[5] ^= 1                                  # a codebook byte
        idx = bytearray(self.vram)
        idx[2048 + 99] ^= 0x80                        # an index byte
        self.assertEqual(run_driver(self.exe, moved, [(vq[0] & ~0x1fffff | 0x40000 // 8,) + vq[1:]])[0][0],
                         base[0][0])
        self.assertEqual(run_driver(self.exe, lead, [mip16])[0][0], base[1][0])
        self.assertNotEqual(run_driver(self.exe, book, [vq])[0][0], base[0][0])
        self.assertNotEqual(run_driver(self.exe, idx, [vq])[0][0], base[0][0])
        self.assertEqual(tp.kamui_vram(0x0201, b"xy")[:6], b"\0\0\0\0xy")
        self.assertEqual(tp.kamui_tcw(0x0301), tp.VQ | 1 << 27)

    def test_palettized_never_replaced(self):
        pal8 = (tp.MIP | 6 << 27 | 0x30 << 21 | 0x20000 // 8, 16, 16, 0)
        k_pal = run_driver(self.exe, self.vram, [pal8])[0][0]
        pack = self.dir / "pack_pal"
        p = tp.Pack(pack, "test")
        p.add(k_pal, bytes([1, 2, 3, 255]) * 64, 8, 8)
        self.assertEqual(p.close(), 1)
        self.assertEqual(run_driver(self.exe, self.vram, [pal8], pack)[0][1:], (0, 0, 0))

    def test_hit_miss_mips_and_off(self):
        pack = self.dir / "pack"
        p = tp.Pack(pack, "test")
        k_vq = tp.key(*self.cases[0][:3], self.at(self.cases[0]))
        k_mip = tp.key(*self.cases[1][:3], self.at(self.cases[1]))
        big = bytes((x * 7 + y) & 255 for y in range(128) for x in range(128) for _ in range(4))
        flat = bytes([200, 100, 50, 255]) * (8 * 4)
        p.add(k_vq, big, 128, 128)                    # a higher-resolution image (128x128 for a 64x64 texture)
        p.add(k_mip, flat, 8, 4)                      # a non-square image for a mip-mapped one
        p.add(0x1234, flat, 8, 4)                     # a key nothing has
        self.assertEqual(p.close(), 3)
        self.assertEqual(tp.main(["check", str(pack)]), 0)
        out = self.dir / "sheets"
        out.mkdir()
        got = run_driver(self.exe, self.vram, self.cases, pack, out)
        self.assertEqual([g[1:] for g in got], [(1, 128, 128), (4, 8, 4), (0, 0, 0), (0, 0, 0), (0, 0, 0)])
        after = (out / "0_after.rgba").read_bytes()
        self.assertEqual(after, png_rgba((pack / f"{k_vq:016x}.png").read_bytes())[2])
        chain = (out / "1_after.rgba").read_bytes()  # 8x4, 4x2, 2x1, 1x1 of one colour
        self.assertEqual(chain, bytes([200, 100, 50, 255]) * (32 + 8 + 2 + 1))
        for n in (2, 3, 4):                           # misses: the decoder's own output
            self.assertEqual((out / f"{n}_after.rgba").read_bytes()[:len((out / f"{n}_before.rgba").read_bytes())],
                             (out / f"{n}_before.rgba").read_bytes())
        off = self.dir / "off"
        off.mkdir()
        got = run_driver(self.exe, self.vram, self.cases, "off", off)
        self.assertEqual([g[1] for g in got], [0] * 5)
        for n in range(5):
            self.assertEqual((off / f"{n}_after.rgba").read_bytes()[:len((off / f"{n}_before.rgba").read_bytes())],
                             (out / f"{n}_before.rgba").read_bytes())
        broken = self.dir / "broken"
        broken.mkdir()
        (broken / "index.txt").write_text(f"{k_vq:016x} missing.png\nnot a line\n", encoding="utf-8")
        self.assertEqual(run_driver(self.exe, self.vram, self.cases[:1], broken)[0][1], 0)


if __name__ == "__main__":
    unittest.main()
