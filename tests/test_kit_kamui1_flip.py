"""The kit's Kamui 1 flip (port/sdk/kamui1_scene.c kmFlipFrameBuffer) in behaviour: compiled at /W4 /WX with a
synthetic game.h and recording stand-ins for pvr/ and the runtime's vertical blank, then run.
1. A game loop that waits for the vertical blank itself, then flips at a vsync count of 1, runs one frame per blank
   (60 Hz, as on the console): the flip adds no blank of its own. A forced extra blank halved such a loop and made
   its one-blank pad edges fall between frames (Spawn's main menu dropped every other press).
2. A second flip in the blank of the last one still waits for the next blank: never two flips in one blank.
3. A vsync count of 2 holds the same loop to one frame every two blanks (30 Hz).
4. The vertex buffer layout (kmSetSystemConfiguration) with Spawn's shares (0, 6.38, 85.1, 6.38, 2.13 percent):
   without the buffer model the opaque list keeps its 0 bytes, as before; with the buffer model on and the opaque
   list direct (flags 0x10, buffer_model 0x20), the opaque list gets the translucent list's room, all scaled to fit
   the buffer (Spawn's models overran the translucent list from an empty opaque list); a direct list that has a
   share keeps it, and the model's bits without the flag change nothing.
5. kmSetUserClipping writes the TA's user tile clip parameter with the rectangle as given, in tiles (Spawn's
   View_Setup passes 0, 0, 19, 14 for the whole screen; dividing by 32 again left only the top-left tile).
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
#include <stdlib.h>
#include <string.h>
typedef s32 KMSTATUS;
KMSTATUS kmFlipFrameBuffer(void);
KMSTATUS kmSetWaitVsyncCount(s32 count);
KMSTATUS kmSetUserClipping(void *bufferDesc, s32 listType, s32 xMin, s32 yMin, s32 xMax, s32 yMax);

static u32 g_vblank, g_presents;
void port_log(const char *fmt, ...) { (void)fmt; }
void port_wait_vblank(void) { g_vblank++; }
u32 port_vblank_count(void) { return g_vblank; }
void gfx_present(void) { g_presents++; }
u32 guest_fold(u32 addr) { return addr & 0x1fffffffu; } /* the lists' P2 pointers, to this process's buffer */
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

/* the list sizes kmSetSystemConfiguration lays out (each start to the next; the last to the buffer's end) */
static int layout(u32 flags, u32 model, f32 opaque_share)
{
    static u32 buf[0x10000];
    static K1SysConfig c;
    const f32 share[K1_LISTS] = {opaque_share, 6.38f, 85.1f, 6.38f, 2.13f};
    u32 end;
    memset(&c, 0, sizeof c);
    c.flags = flags;
    c.buffer_model = model;
    c.vertex_buffer = (u32)(uintptr_t)buf;
    c.vertex_buffer_size = sizeof buf;
    memcpy(c.list_share, share, sizeof share);
    if (kmSetSystemConfiguration(&c)) return 1;
    if (kmSetUserClipping(NULL, 2, 0, 0, 19, 14)) return 1;
    {
        const u32 *clip = (const u32 *)(uintptr_t)guest_fold(k1_list_start(2));
        printf("clip %08x %u %u %u %u\n", clip[0], clip[4], clip[5], clip[6], clip[7]);
    }
    end = (((u32)(uintptr_t)buf & 0x1fffffffu) | 0xa0000000u) + (u32)sizeof buf;
    printf("sizes");
    for (u32 i = 0; i < K1_LISTS; i++) printf(" %u", (i + 1 < K1_LISTS ? k1_list_start(i + 1) : end) - k1_list_start(i));
    printf("\n");
    return 0;
}

int main(int argc, char **argv)
{
    u32 a, b, c;
    if (argc > 3) return layout((u32)strtoul(argv[1], NULL, 16), (u32)strtoul(argv[2], NULL, 16), (f32)atof(argv[3]));
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

            def sizes(flags, model, opaque):
                run = subprocess.run([str(d / "k1.exe"), flags, model, opaque], capture_output=True, text=True,
                                     timeout=60)
                self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
                self.assertIn("clip 20000000 0 0 19 14\n", run.stdout)  # the whole screen, in tiles, as given
                return [int(x) for x in run.stdout.split("sizes")[1].split()]
            room = 0x40000 - 128                      # the buffer less Kamui's head words
            off = sizes("0", "0", "0")
            self.assertEqual(off[0], 0)               # no buffer model: as before, the opaque list has no room
            self.assertEqual(sizes("0", "20", "0"), off)  # the model's bits without the flag: unchanged
            on = sizes("10", "20", "0")               # opaque direct: the translucent list's room, scaled to fit
            self.assertGreater(on[0], 0)
            self.assertLessEqual(abs(on[0] - on[2]), 32)
            self.assertLessEqual(sum(on[:4]), room)
            self.assertAlmostEqual(on[0] / room, 85.1 / (85.1 * 2 + 6.38 * 2 + 2.13), delta=0.002)
            self.assertEqual(sizes("10", "20", "5"), sizes("0", "0", "5"))  # a direct list with a share keeps it
            self.assertEqual(sizes("10", "24", "0"), off)  # punch-through direct already has room: unchanged
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
