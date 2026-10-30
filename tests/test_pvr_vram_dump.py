"""--dump-vram SCENE (port/pvr/ta_parse.c, pvr_state.c): the real option parser, scene counter and writer, compiled at
/W4 /WX with a small driver (the batcher, log and host calls stubbed):
1. the option is taken (2 arguments) and only the asked scene writes ta_vram_SCENE.bin, once;
2. the file is the 8 MB texture memory as it is when that scene is parsed (a write after it is not in the file), and
   the log line names the scene and the vertical blank;
3. without the option no file is written, and --ta-dump still parses as before.
Only where Visual Studio's 32-bit tools are found (tests/test_lockstep.py NO_VC says how), skipped elsewhere.

usage: python -W error tests/test_pvr_vram_dump.py
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

DRIVER_C = r"""#define _CRT_SECURE_NO_WARNINGS
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "pvr_int.h"
#include "platform.h"
#include "lockstep.h"
int pvr_option(const char *arg, const char *value);
static PvrScene g_scene;
void tb_reset(PvrScene *s) { (void)s; }
void tb_poly(PvrScene *s, u32 list, u32 isp, u32 tsp, u32 tcw, u32 flags, const u16 clip[4])
{ (void)s, (void)list, (void)isp, (void)tsp, (void)tcw, (void)flags, (void)clip; }
void tb_vertex(PvrScene *s, const PvrVtx *v, int end_of_strip) { (void)s, (void)v, (void)end_of_strip; }
void tb_quad(PvrScene *s, const PvrVtx v[4]) { (void)s, (void)v; }
void tb_tri(PvrScene *s, const PvrVtx v[3]) { (void)s, (void)v; }
void pvr_tex_invalidate(u32 offset, u32 size) { (void)offset, (void)size; }
void hw_check_vram(u32 offset, u32 size) { (void)offset, (void)size; }
void port_extra_undo(void *p, u32 size, PortUndoFn after) { (void)p, (void)size, (void)after; }
void gfx_set_border(u32 argb) { (void)argb; }
u32 port_vblank_count(void) { return 4242u; }
void port_log(const char *fmt, ...)
{
    if (strstr(fmt, "texture memory")) printf("LOG %s", fmt);
}
int main(int argc, char **argv)
{
    PvrPass pass;
    memset(&pass, 0, sizeof pass);
    for (int i = 1; i + 1 < argc; i += 2) printf("TAKEN %s %d\n", argv[i], pvr_option(argv[i], argv[i + 1]));
    for (u32 scene = 0; scene < 4u; scene++) {
        memset(pvr_vram(), (int)(0x10u + scene), PVRX_VRAM_SIZE);
        pvr_vram()[PVRX_VRAM_SIZE - 1u] = 0xa5u;
        ta_parse(&g_scene, &pass, 1u);
    }
    return 0;
}
"""


@unittest.skipUnless(HAVE_VC, NO_VC)
class DumpVram(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = pathlib.Path(tempfile.mkdtemp(prefix="pvr_vram_dump_"))
        d = cls.dir
        (d / "port_config.h").write_text(game_config.port_config_h(
            {"name": "Fixture", "entry": {"name": "Fx_Main", "addr": "8c011000"}, "image_base": "8c010000",
             "boot_file": "", "console": None, "id": "FIXTURE", "boot_sha256": []}), encoding="utf-8")
        (d / "vram_main.c").write_text(DRIVER_C, encoding="utf-8")
        rd = TOOL / "targets" / "dreamcast_sh4" / "runtime_rd"
        srcs = " ".join(f'"{p}"' for p in [KIT / "pvr" / "ta_parse.c", KIT / "pvr" / "pvr_state.c", d / "vram_main.c"])
        cl = (f'cl /nologo /TC /std:c11 /W4 /WX /O2 /I"{d}" /I"{KIT / "runtime"}" /I"{KIT / "pvr"}" '
              f'/I"{KIT / "host"}" /I"{rd}" {srcs} /Fe:vram_driver.exe')
        vc = str(game_config.vcvars32()).replace("/", "\\")
        r = subprocess.run(f'cmd /d /s /c ""{vc}" >nul 2>&1 && {cl}"', cwd=d, capture_output=True, text=True,
                           errors="replace", timeout=300)
        if r.returncode or "warning" in r.stdout.lower():
            raise RuntimeError(r.stdout[-3000:] + r.stderr[-1000:])
        cls.exe = d / "vram_driver.exe"

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def run_in(self, name, *args):
        d = self.dir / name
        d.mkdir()
        r = subprocess.run([str(self.exe), *args], cwd=d, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return d, r.stdout

    def test_asked_scene_only(self):
        d, out = self.run_in("two", "--dump-vram", "2")
        self.assertIn("TAKEN --dump-vram 2", out)
        self.assertEqual(sorted(p.name for p in d.iterdir()), ["ta_vram_2.bin"])
        data = (d / "ta_vram_2.bin").read_bytes()
        self.assertEqual(len(data), 0x800000)
        self.assertEqual(data, bytes([0x12]) * (0x800000 - 1) + b"\xa5")  # scene 2's bytes, not scene 3's
        self.assertEqual(out.count("LOG "), 1)
        self.assertIn("texture memory of scene %u (vblank %u)", out)

    def test_off_and_ta_dump_unchanged(self):
        d, out = self.run_in("off", "--ta-dump", "1")
        self.assertIn("TAKEN --ta-dump 2", out)
        self.assertEqual(sorted(p.name for p in d.iterdir()), ["ta_scene_1.txt"])
        self.assertNotIn("LOG ", out)
        d, out = self.run_in("both", "--ta-dump", "3", "--dump-vram", "3")
        self.assertEqual(sorted(p.name for p in d.iterdir()), ["ta_scene_3.txt", "ta_vram_3.bin"])
        self.assertEqual((d / "ta_vram_3.bin").read_bytes()[:4], b"\x13" * 4)


if __name__ == "__main__":
    unittest.main()
