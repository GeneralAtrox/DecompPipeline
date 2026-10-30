"""The kit's Kamui 2 calls (port/sdk/kamui2_misc.c) in behaviour: compiled at /W4 /WX with a synthetic game.h and
recording stand-ins for pvr/ and the runtime, then run. Covers what Power Stone 2 calls by the SDK's own names
(built from its disassembly, Kamui 2 at 8c2b5e80, 8c2b8620 and the slot setter 8c2ca2a0 / runner 8c2ca2e0):
1. kmSetPaletteMode keeps modes 0..3 and refuses a larger one (or a negative one), keeping the mode it had;
2. Km_SetCallback keeps slots 0 (render done) and 3 (texture memory full) for k2_run_callback, which calls each with
   its argument; a slot never set, a cleared slot and slot 1 call nothing; another slot is still reported (pvr_todo)
   and slot 8 still goes to the vertical blank hook;
3. kmSetBackGroundPlane(vertices, type, size) draws what Km_SetBackgroundPlane(vertices, type) draws: x, y, 1/w at
   +4..12 and the packed colour at +0x18 of each of the three vertices; another vertex type is reported, not drawn.
4. kmSetPaletteMode hands an accepted mode to pvr/ as PAL_RAM_CTRL (a refused one not), and kmSetPaletteData all
   1024 entries from the game's table as PAL_RAM entries 0..1023 (no table: an error, nothing written);
5. k2_vertex_buffer_size (sdk/kamui2.h, kamui2_scene.c's banks) keeps a vertex buffer inside main RAM: Power Stone
   2's 4 MB default at 0x0cd00000 (its pass sizing 8c1780f0 not running) becomes 3 MB, so bank 1 no longer wraps
   onto 0x8c000000; a buffer that fits keeps its size, in any of RAM's addresses.
6. kmBeginScene (kamui2_scene.c) does not set the system up again, as Kamui's (PS2 8c2b6d40) does not: a texture made
   after kmSetSystemConfiguration keeps its texture memory when the game begins scenes with the same config through
   another pointer (PS2: 0x0c83cf90 at set-up, 0x8c83cf90 per scene), so the next texture lands after it, not on
   it; a real second kmSetSystemConfiguration still forgets every texture (8c2c8ba0 clears Kamui's lists too); a
   game that never set it up is set up by its first kmBeginScene.
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
s32 kmSetPaletteData(const u32 *palette);
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
static u32 g_pal_format = 99u, g_pal_first = 99u, g_pal_count, g_pal_writes;
static const u32 *g_pal_from;
void pvr_set_palette_format(u32 format) { g_pal_format = format; }
void pvr_set_palette(u32 first, const u32 *entries, u32 count)
{
    g_pal_writes++;
    g_pal_first = first;
    g_pal_from = entries;
    g_pal_count = count;
}

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
    printf("palfmt %u\n", g_pal_format);
    {
        static u32 table[1024];
        s32 none = kmSetPaletteData(NULL);
        u32 none_writes = g_pal_writes;
        ok = kmSetPaletteData(table);
        printf("paldata %d %u %d %u %u %d\n", none, none_writes, ok, g_pal_first, g_pal_count, g_pal_from == table);
    }
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
            self.assertEqual(out["palfmt"], "2")             # modes 4 and -1 refused: PAL_RAM_CTRL stays 2
            self.assertEqual(out["paldata"], "-1 0 0 0 1024 1")
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


SIZE_C = r"""#include "platform.h"
#include "kamui2.h"
#include <stdio.h>
int main(void)
{
    printf("ps2 %08x\n", k2_vertex_buffer_size(0xacd00000u, 0x00400000u));
    printf("fits %08x %08x\n", k2_vertex_buffer_size(0xacc00000u, 0x00400000u),
           k2_vertex_buffer_size(0x8c800000u, 0x00200000u));
    printf("edge %08x %08x\n", k2_vertex_buffer_size(0x0cfffff0u, 0x100u), k2_vertex_buffer_size(0xac000000u, 0x2000000u));
    return 0;
}
"""


@unittest.skipUnless(HAVE_VC, NO_VC)
class Kamui2VertexBuffer(unittest.TestCase):
    def test_inside_ram(self):
        work = TOOL / "tests" / ".work"
        d = pathlib.Path(tempfile.mkdtemp(dir=work if work.is_dir() else None))
        try:
            (d / "port_config.h").write_text(game_config.port_config_h(
                {"name": "Fixture", "entry": {"name": "Fx_Main", "addr": "8c011000"}, "image_base": "8c010000",
                 "boot_file": "", "console": None, "id": "FIXTURE", "boot_sha256": []}), encoding="utf-8")
            (d / "game.h").write_text(GAME_H, encoding="utf-8")
            (d / "k2_size.c").write_text(SIZE_C, encoding="utf-8")
            rd = TOOL / "targets" / "dreamcast_sh4" / "runtime_rd"
            cl = (f'cl /nologo /TC /std:c11 /fp:strict /arch:SSE2 /W4 /WX /O1 /I"{d}" /I"{KIT / "runtime"}" '
                  f'/I"{KIT / "pvr"}" /I"{KIT / "sdk"}" /I"{rd}" "{d / "k2_size.c"}" /Fe:k2s.exe')
            vc = str(game_config.vcvars32()).replace("/", "\\")
            r = subprocess.run(f'cmd /d /s /c ""{vc}" >nul 2>&1 && {cl}"', cwd=d, capture_output=True, text=True,
                               errors="replace", timeout=300)
            self.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr[-1000:])
            run = subprocess.run([str(d / "k2s.exe")], capture_output=True, text=True, timeout=60)
            out = dict((ln.split(" ", 1) + [""])[:2] for ln in run.stdout.splitlines())
            self.assertEqual(out["ps2"], "00300000")             # 0x0cd00000 + 3 MB ends at RAM's end
            self.assertEqual(out["fits"], "00400000 00200000")
            self.assertEqual(out["edge"], "00000010 01000000")
        finally:
            shutil.rmtree(d, ignore_errors=True)


SCENE_GAME_H = """#include <string.h>
#include "rd_rt.h"
typedef struct KMSURFACEDESC KMSURFACEDESC;
typedef struct KMSYSTEMCONFIGSTRUCT KMSYSTEMCONFIGSTRUCT;
typedef struct KMVERTEXBUFFDESC KMVERTEXBUFFDESC;
typedef struct { s32 vsync_wait, vsync_wait_neg; } KmDriverState;
extern KmDriverState g_kmDriverState;
"""

SCENE_C = r"""#include "game.h"
#include "platform.h"
#include "kamui2.h"
#include "lockstep.h"
#include <stdio.h>
KmDriverState g_kmDriverState;
static u8 g_vram[PVRX_VRAM_SIZE];
static u32 g_vblank;
void port_log(const char *fmt, ...) { (void)fmt; }
void pvr_todo(const char *fmt, ...) { (void)fmt; }
u8 *pvr_vram(void) { return g_vram; }
void pvr_vram_written(u32 offset, u32 size) { (void)offset; (void)size; }
void pvr_set_stride(u32 width) { (void)width; }
void pvr_set_clip(u32 x0, u32 y0, u32 x1, u32 y1) { (void)x0; (void)y0; (void)x1; (void)y1; }
int pvr_render(const PvrPass *passes, u32 count) { (void)passes; (void)count; return 1; }
int pvr_read_frame(void *rgb565, u32 width, u32 height) { (void)rgb565; (void)width; (void)height; return 0; }
void hw_check_scene(const struct PvrPass *passes, u32 count) { (void)passes; (void)count; }
void hw_check_rendered(const struct PvrPass *passes, u32 count) { (void)passes; (void)count; }
void lockstep_scene(void) {}
void port_extra_state(void *p, u32 size) { (void)p; (void)size; }
void port_extra_undo(void *p, u32 size, PortUndoFn after) { (void)p; (void)size; (void)after; }
int port_extra_drawing(void) { return 0; }
int port_extra_begin(u32 vblanks) { (void)vblanks; return 0; }
u32 port_extra_count(void) { return 0; }
void port_wait_vblank(void) { g_vblank++; }
void port_wait_vblank_extra(void) { g_vblank++; }
u32 port_vblank_count(void) { return g_vblank; }
void gfx_present(void) {}
void gfx_frame_done(void) {}
void gfx_show(void) {}
void gfx_capture_extra(void) {}
u32 k2_frame_lines(void) { return 480u; }
void k2_run_callback(s32 slot) { (void)slot; }
s32 kmCreateTextureSurface(KMSURFACEDESC *surface, u32 width, u32 height, u32 type);
s32 kmBeginScene(KMSYSTEMCONFIGSTRUCT *config);

static K2SysConfig g_cfg, g_mirror, g_fresh;
static K2VertexBuffDesc g_desc;

static u32 tex(void)
{
    K2Surface s;
    memset(&s, 0, sizeof s);
    return kmCreateTextureSurface((KMSURFACEDESC *)&s, 256u, 256u, 0x0100u) == K2_OK ? s.address : 0u;
}

int main(void)
{
    u32 a, b, c, d;
    s32 ok;
    g_cfg.frame_buffer_count = 2u;
    g_cfg.texture_memory_size = 0x400000u;
    g_cfg.buffer_desc = &g_desc;
    g_cfg.vertex_banks = 2u;
    g_cfg.vertex_buffer = 0x0cd00000u;
    g_cfg.vertex_buffer_size = 0x100000u;
    g_cfg.pass_depth = 1u;
    g_mirror = g_cfg; /* the same config through another pointer (another address mirror in the game) */
    g_fresh = g_cfg;
    printf("setup %d\n", kmSetSystemConfiguration(&g_cfg));
    a = tex();
    ok = kmBeginScene((KMSYSTEMCONFIGSTRUCT *)&g_mirror); /* one call per statement (printf's argument order) */
    printf("scene %d %d\n", ok, g_k2_config == &g_cfg);
    b = tex();
    printf("kept %05x %05x\n", a, b);
    kmSetSystemConfiguration(&g_cfg);
    c = tex();
    printf("again %05x\n", c);
    g_k2_config = NULL; /* a game that never called kmSetSystemConfiguration */
    ok = kmBeginScene((KMSYSTEMCONFIGSTRUCT *)&g_fresh);
    printf("first %d %d\n", ok, g_k2_config == &g_fresh);
    d = tex();
    printf("fresh %05x\n", d);
    return 0;
}
"""


@unittest.skipUnless(HAVE_VC, NO_VC)
class Kamui2SceneKeepsTextures(unittest.TestCase):
    def test_begin_scene_no_setup(self):
        work = TOOL / "tests" / ".work"
        d = pathlib.Path(tempfile.mkdtemp(dir=work if work.is_dir() else None))
        try:
            (d / "port_config.h").write_text(game_config.port_config_h(
                {"name": "Fixture", "entry": {"name": "Fx_Main", "addr": "8c011000"}, "image_base": "8c010000",
                 "boot_file": "", "console": None, "id": "FIXTURE", "boot_sha256": []}), encoding="utf-8")
            (d / "game.h").write_text(SCENE_GAME_H, encoding="utf-8")
            (d / "k2_scene.c").write_text(SCENE_C, encoding="utf-8")
            rd = TOOL / "targets" / "dreamcast_sh4" / "runtime_rd"
            srcs = " ".join(f'"{p}"' for p in [KIT / "sdk" / "kamui2_scene.c", KIT / "sdk" / "kamui2_tex.c",
                                               d / "k2_scene.c"])
            cl = (f'cl /nologo /TC /std:c11 /fp:strict /arch:SSE2 /W4 /WX /O1 /I"{d}" /I"{KIT / "runtime"}" '
                  f'/I"{KIT / "pvr"}" /I"{KIT / "sdk"}" /I"{rd}" {srcs} /Fe:k2sc.exe')
            vc = str(game_config.vcvars32()).replace("/", "\\")
            r = subprocess.run(f'cmd /d /s /c ""{vc}" >nul 2>&1 && {cl}"', cwd=d, capture_output=True, text=True,
                               errors="replace", timeout=300)
            self.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr[-1000:])
            self.assertNotIn("warning", r.stdout.lower())
            run = subprocess.run([str(d / "k2sc.exe")], capture_output=True, text=True, timeout=60)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            out = dict((ln.split(" ", 1) + [""])[:2] for ln in run.stdout.splitlines())
            self.assertEqual(out["setup"], "0")
            self.assertEqual(out["scene"], "0 1")           # the set-up's config stays the one in use
            self.assertEqual(out["kept"], "130000 150000")  # after two 640x480 frame buffers; b after a, not on it
            self.assertEqual(out["again"], "130000")        # a real set-up forgets them, as Kamui's 8c2c8ba0 does
            self.assertEqual(out["first"], "0 1")
            self.assertEqual(out["fresh"], "130000")
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
