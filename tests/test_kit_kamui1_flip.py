"""The kit's Kamui 1 flip (port/sdk/kamui1_scene.c kmFlipFrameBuffer) in behaviour: compiled at /W4 /WX with a
synthetic game.h and recording stand-ins for pvr/ and the runtime's vertical blank, then run.
1. A game loop that waits for the vertical blank itself, then flips at a vsync count of 1, runs one frame per blank
   (60 Hz, as on the console): the flip adds no blank of its own. A forced extra blank halved such a loop and made
   its one-blank pad edges fall between frames (Spawn's main menu dropped every other press).
2. A second flip in the blank of the last one still waits for the next blank: never two flips in one blank.
3. A vsync count of 2 holds the same loop to one frame every two blanks (30 Hz).
Only where Visual Studio's 32-bit tools are found (tests/test_lockstep.py NO_VC says how), skipped elsewhere.

usage: python -W error tests/test_kit_kamui1_flip.py
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

GAME_H = """#include <string.h>
#include "rd_rt.h"
typedef s32 KMSTATUS;
typedef struct { u32 w[8]; } KMVERTEXBUFFDESC;
typedef struct { u32 w[10]; } KMSURFACEDESC;
"""

DRIVER_C = r"""#include "platform.h"
#include "port.h"
#include "kamui1.h"
#include "lockstep.h"
#include <stdio.h>
typedef s32 KMSTATUS;
KMSTATUS kmFlipFrameBuffer(void);
KMSTATUS kmSetWaitVsyncCount(s32 count);

static u32 g_vblank, g_presents;
void port_log(const char *fmt, ...) { (void)fmt; }
void port_wait_vblank(void) { g_vblank++; }
u32 port_vblank_count(void) { return g_vblank; }
void gfx_present(void) { g_presents++; }
u32 guest_fold(u32 addr) { return addr; }
int pvr_render(const PvrPass *passes, u32 count) { (void)passes; (void)count; return 0; }
void hw_check_scene(const PvrPass *passes, u32 count) { (void)passes; (void)count; }
void hw_check_rendered(const PvrPass *passes, u32 count) { (void)passes; (void)count; }

/* n frames of a loop that waits for the next blank, then flips; returns the blanks they took */
static u32 frames(u32 n)
{
    u32 from = g_vblank;
    for (u32 i = 0; i < n; i++) {
        port_wait_vblank();
        kmFlipFrameBuffer();
    }
    return g_vblank - from;
}

int main(void)
{
    u32 a, b, c;
    kmFlipFrameBuffer();
    a = frames(10);
    printf("count1 %u %u\n", a, g_presents);
    b = g_vblank;
    kmFlipFrameBuffer();
    printf("same %u\n", g_vblank - b);
    kmSetWaitVsyncCount(2);
    kmFlipFrameBuffer();
    c = frames(10);
    printf("count2 %u\n", c);
    return 0;
}
"""


@unittest.skipUnless(HAVE_VC, NO_VC)
class Kamui1Flip(unittest.TestCase):
    def test_flip_pacing(self):
        work = TOOL / "tests" / ".work"
        d = pathlib.Path(tempfile.mkdtemp(dir=work if work.is_dir() else None))
        try:
            (d / "port_config.h").write_text(game_config.port_config_h(
                {"name": "Fixture", "entry": {"name": "Fx_Main", "addr": "8c011000"}, "image_base": "8c010000",
                 "boot_file": "", "console": None, "id": "FIXTURE", "boot_sha256": []}), encoding="utf-8")
            (d / "game.h").write_text(GAME_H, encoding="utf-8")
            (d / "k1_main.c").write_text(DRIVER_C, encoding="utf-8")
            rd = TOOL / "targets" / "dreamcast_sh4" / "runtime_rd"
            srcs = " ".join(f'"{p}"' for p in [KIT / "sdk" / "kamui1_scene.c", d / "k1_main.c"])
            cl = (f'cl /nologo /TC /std:c11 /fp:strict /arch:SSE2 /W4 /WX /O1 /I"{d}" /I"{KIT / "runtime"}" '
                  f'/I"{KIT / "pvr"}" /I"{KIT / "sdk"}" /I"{rd}" {srcs} /Fe:k1.exe')
            vc = str(game_config.vcvars32()).replace("/", "\\")
            r = subprocess.run(f'cmd /d /s /c ""{vc}" >nul 2>&1 && {cl}"', cwd=d, capture_output=True, text=True,
                               errors="replace", timeout=300)
            self.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr[-1000:])
            self.assertNotIn("warning", r.stdout.lower())
            run = subprocess.run([str(d / "k1.exe")], capture_output=True, text=True, timeout=60)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            out = dict((ln.split(" ", 1) + [""])[:2] for ln in run.stdout.splitlines())
            self.assertEqual(out["count1"], "10 11")  # 10 frames in 10 blanks, each presented
            self.assertEqual(out["same"], "1")        # a second flip in the same blank waits for the next
            self.assertEqual(out["count2"], "20")     # vsync count 2: 30 Hz
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
