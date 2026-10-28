"""The kit's Kamui 2 calls (port/sdk/kamui2_misc.c) in behaviour: compiled at /W4 /WX with a synthetic game.h and
recording stand-ins for pvr/ and the runtime, then run. Covers what Power Stone 2 calls by the SDK's own names
(built from its disassembly, Kamui 2 at 8c2b5e80, 8c2b8620 and the slot setter 8c2ca2a0 / runner 8c2ca2e0):
1. kmSetPaletteMode keeps modes 0..3 and refuses a larger one (or a negative one), keeping the mode it had;
2. Km_SetCallback keeps slots 0 (render done) and 3 (texture memory full) for k2_run_callback, which calls each with
   its argument; a slot never set, a cleared slot and slot 1 call nothing; another slot is still reported (pvr_todo)
   and slot 8 still goes to the vertical blank hook;
3. kmSetBackGroundPlane(vertices, type, size) draws what Km_SetBackgroundPlane(vertices, type) draws: x, y, 1/w at
   +4..12 and the packed colour at +0x18 of each of the three vertices; another vertex type is reported, not drawn.
Only where Visual Studio's 32-bit tools are found (tests/test_lockstep.py NO_VC says how), skipped elsewhere.

usage: python -W error tests/test_kit_kamui2.py
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
typedef void (*KmCallbackFunc)(void *);
typedef struct { u32 w[3]; } KmVersionInfo;
"""

DRIVER_C = r"""#include "platform.h"
#include "kamui2.h"
#include <stdio.h>
#include <string.h>
typedef void (*KmCallbackFunc)(void *);
s32 Km_SetCallback(s32 slot, KmCallbackFunc func, void *arg);
s32 kmSetPaletteMode(s32 mode);
u32 Km_SetBackgroundPlane(void *plane, s32 flags);
s32 kmSetBackGroundPlane(void **vertices, s32 vertexType, s32 structSize);

static int g_todo, g_bg;
static f32 g_xyz[3][3];
static u32 g_argb[3];
static PortVblankFn g_hook;
void port_log(const char *fmt, ...) { (void)fmt; }
void pvr_todo(const char *fmt, ...) { (void)fmt; g_todo++; }
void port_set_vblank_hook(PortVblankFn fn, void *arg) { (void)arg; g_hook = fn; }
void pvr_set_background(const f32 xyz[3][3], const u32 argb[3])
{
    g_bg++;
    memcpy(g_xyz, xyz, sizeof g_xyz);
    memcpy(g_argb, argb, sizeof g_argb);
}
void pvr_set_border(u32 argb) { (void)argb; }
void pvr_set_cheap_shadow(s32 intensity) { (void)intensity; }
void pvr_set_fog_color(u32 argb) { (void)argb; }
void pvr_set_fog_density(u32 reg) { (void)reg; }
void pvr_set_fog_table(const u16 entries[128]) { (void)entries; }

static int g_calls;
static void *g_last;
static void cb(void *arg) { g_calls++; g_last = arg; }

int main(void)
{
    int a = 0, b = 0, todo;
    s32 ok, big, neg;
    u32 kept;
    u8 v[3][32];
    void *plane[3] = {v[0], v[1], v[2]};
    f32 xyz[3][3];
    u32 argb[3];
    /* 1. palette mode */
    ok = kmSetPaletteMode(2);   /* one call per statement: MSVC evaluates printf's arguments right to left */
    kept = k2_palette_mode();
    big = kmSetPaletteMode(4);
    neg = kmSetPaletteMode(-1);
    printf("pal %d %u %d %d %u\n", ok, kept, big, neg, k2_palette_mode());
    /* 2. callback slots */
    k2_run_callback(0);
    printf("unset %d\n", g_calls);
    Km_SetCallback(0, cb, &a);
    Km_SetCallback(3, cb, &b);
    k2_run_callback(0);
    printf("slot0 %d %d\n", g_calls, g_last == &a);
    k2_run_callback(3);
    printf("slot3 %d %d\n", g_calls, g_last == &b);
    k2_run_callback(1);
    k2_run_callback(5);
    Km_SetCallback(0, NULL, NULL);
    k2_run_callback(0);
    printf("none %d\n", g_calls);
    todo = g_todo;
    Km_SetCallback(5, cb, &a);
    Km_SetCallback(8, cb, &a);
    printf("other %d %d\n", g_todo - todo, g_hook == (PortVblankFn)cb);
    /* 3. the background plane, by both names */
    memset(v, 0, sizeof v);
    for (u32 i = 0; i < 3u; i++) {
        f32 f[3] = {10.0f * (f32)i + 1.0f, 20.0f * (f32)i + 2.0f, 0.5f / (f32)(i + 1u)};
        u32 c = 0xff102030u + i;
        memcpy(v[i] + 4, f, sizeof f);
        memcpy(v[i] + 0x18, &c, sizeof c);
    }
    ok = kmSetBackGroundPlane(plane, 0, 32);
    printf("bgsdk %d %d\n", ok, g_bg);
    memcpy(xyz, g_xyz, sizeof xyz);
    memcpy(argb, g_argb, sizeof argb);
    Km_SetBackgroundPlane(plane, 0);
    printf("bgsame %d %d %d %08x %08x\n", g_bg, !memcmp(xyz, g_xyz, sizeof xyz), !memcmp(argb, g_argb, sizeof argb),
           argb[0], argb[2]);
    printf("bgvals %g %g %g %g\n", (double)xyz[0][0], (double)xyz[1][1], (double)xyz[2][2], (double)xyz[1][0]);
    todo = g_todo;
    ok = kmSetBackGroundPlane(plane, 1, 64);
    printf("bgother %d %d %d\n", ok, g_bg, g_todo - todo);
    return 0;
}
"""


@unittest.skipUnless(HAVE_VC, NO_VC)
class Kamui2Misc(unittest.TestCase):
    def test_calls(self):
        work = TOOL / "tests" / ".work"
        d = pathlib.Path(tempfile.mkdtemp(dir=work if work.is_dir() else None))
        try:
            (d / "port_config.h").write_text(game_config.port_config_h(
                {"name": "Fixture", "entry": {"name": "Fx_Main", "addr": "8c011000"}, "image_base": "8c010000",
                 "boot_file": "", "console": None, "id": "FIXTURE", "boot_sha256": []}), encoding="utf-8")
            (d / "game.h").write_text(GAME_H, encoding="utf-8")
            (d / "k2_main.c").write_text(DRIVER_C, encoding="utf-8")
            rd = TOOL / "targets" / "dreamcast_sh4" / "runtime_rd"
            srcs = " ".join(f'"{p}"' for p in [KIT / "sdk" / "kamui2_misc.c", d / "k2_main.c"])
            cl = (f'cl /nologo /TC /std:c11 /fp:strict /arch:SSE2 /W4 /WX /O1 /I"{d}" /I"{KIT / "runtime"}" '
                  f'/I"{KIT / "pvr"}" /I"{KIT / "sdk"}" /I"{rd}" {srcs} /Fe:k2.exe')
            vc = str(game_config.vcvars32()).replace("/", "\\")
            r = subprocess.run(f'cmd /d /s /c ""{vc}" >nul 2>&1 && {cl}"', cwd=d, capture_output=True, text=True,
                               errors="replace", timeout=300)
            self.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr[-1000:])
            self.assertNotIn("warning", r.stdout.lower())
            run = subprocess.run([str(d / "k2.exe")], capture_output=True, text=True, timeout=60)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            out = dict((ln.split(" ", 1) + [""])[:2] for ln in run.stdout.splitlines())
            self.assertEqual(out["pal"], "0 2 -1 -1 2")
            self.assertEqual(out["unset"], "0")
            self.assertEqual(out["slot0"], "1 1")
            self.assertEqual(out["slot3"], "2 1")
            self.assertEqual(out["none"], "2")          # slots 1, 5 and a cleared slot 0 call nothing
            self.assertEqual(out["other"], "1 1")       # slot 5 reported; slot 8 is the vertical blank hook
            self.assertEqual(out["bgsdk"], "0 1")
            self.assertEqual(out["bgsame"], "2 1 1 ff102030 ff102032")
            self.assertEqual(out["bgvals"], "1 22 0.166667 11")
            self.assertEqual(out["bgother"], "0 2 1")   # another vertex type: reported, nothing drawn
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
