"""In-between frames from object parts (port/pvr/pvr_parts.c, --interp-parts): the real module, compiled at /W4 /WX and
run on hand-made ticks. A tick's part draws write polygon vertices into a TA list in guest RAM (0x8c000000, reserved
for the test), the parse is played by pvr_parts_vertex, and each vertex becomes one scene vertex. Each expected
number is worked by hand:
1. a part whose vertex comes from x 100 at 1/w 0.1 to x 200 at 1/w 0.2 blends at one half to x 133.33 and 1/w 0.1333,
   the point half way in the view (weights 0.5 / 0.1 and 0.5 / 0.2), not 150, the middle of its screen positions;
   t 0 gives where it was and t 1 where it is, exactly;
2. a vertex of no part (2D, text, HUD) stays where the latest tick drew it;
3. a part that is new, whose vertex count changed, or whose object's stamp changed (the pool slot was taken again)
   stays where the latest tick drew it: nothing is paired by screen position, so nothing can ghost;
4. one part drawn twice in a tick (split screen) pairs first with first and second with second;
5. --interp-truth: the in-between frame after an odd frame blends the scene two before with it, so at one half it is
   where the frame between was;
6. a scene where half the parts' vertices or more are new or gone (8 of 11 in the second tick) gets no in-between
   frames; one where every part paired does;
7. a part that turned back (x 0, 10, 0) is drawn where it was a tick before, 10, at any t; one on its way (0, 10, 20)
   blends to 15, and one that moved under a pixel a tick (0, 0.5, 0) blends to 0.25.
Only where Visual Studio's 32-bit tools are found (tests/test_lockstep.py NO_VC says how), skipped elsewhere.

usage: python -W error tests/test_pvr_parts.py
Nearest existing: tests/test_pvr_blend.py - the screen blending's test (pairs by place); this is the parts blending,
another module with its own guest RAM fixture.
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
#include <windows.h>
#include "pvr_int.h"
#include "platform.h"
void port_log(const char *fmt, ...) { (void)fmt; }
static int g_truth, g_frame;
int pvr_blend_truth_skip(void) { return g_truth && (g_frame & 1); }
f32 pvr_blend_truth_t(f32 t) { return g_truth ? 0.5f : t; }
void port_part_begin(const void *obj, const void *part, u32 stamp_at, u32 world_at);
void port_part_end(void);
void pvr_parts_lists(const u32 *ptrs);
void pvr_parts_scene(void);

#define RAM 0x8c000000u
static u32 g_ptrs[PVRX_LISTS], g_w, g_n;
static u32 g_addr[64];
static u8 g_obj[4][64], g_part[4][128]; /* stamp at obj + 0x24, world matrix at part + 0x28 */

static void begin_tick(void) { pvr_parts_scene(); g_w = 0x1000u, g_n = 0; g_ptrs[0] = RAM + g_w; }

static void vtx(f32 x, f32 y, f32 z)
{
    u32 *w = (u32 *)(uintptr_t)(RAM + g_w);
    f32 f[3] = {x, y, z};
    w[0] = 0xe0000000u;
    memcpy(&w[1], f, sizeof f);
    g_addr[g_n++] = RAM + g_w;
    g_w += 32u;
    g_ptrs[0] = RAM + g_w;
}

/* a part draw writing n vertices, x step 10 from x0, at y and 1/w z */
static void part(int obj, int prt, s16 stamp, u32 n, f32 x0, f32 y, f32 z)
{
    memcpy(&g_obj[obj][0x24], &stamp, sizeof stamp);
    port_part_begin(g_obj[obj], g_part[prt], 0x24u, 0x28u);
    for (u32 i = 0; i < n; i++) vtx(x0 + 10.0f * (f32)i, y, z);
    port_part_end();
}

static PvrVtx g_v[64];
static u32 g_seq[64];
static PvrScene g_s;

/* the render: the parse tags each vertex, one scene vertex each */
static void render(void)
{
    g_s.vtx = g_v, g_s.seq = g_seq, g_s.nvtx = g_n, g_s.vcap = g_s.seq_cap = 64u;
    for (u32 i = 0; i < g_n; i++) {
        const f32 *f = (const f32 *)(uintptr_t)(g_addr[i] + 4u);
        pvr_parts_vertex(0u, g_addr[i]);
        memset(&g_v[i], 0, sizeof g_v[i]);
        g_v[i].x = f[0], g_v[i].y = f[1], g_v[i].z = f[2];
        g_seq[i] = pvr_parts_seq();
    }
    pvr_parts_from(&g_s);
}

static void show(const char *name, f32 t)
{
    u32 n = 0;
    const PvrVtx *v = pvr_parts_blend(&g_s, t, &n);
    printf("%s %d:", name, g_s.has_from);
    for (u32 i = 0; v && i < g_s.nvtx; i++) printf(" %.4f,%.4f,%.5f", v[i].x, v[i].y, v[i].z);
    printf("\n");
}

int main(void)
{
    if (!VirtualAlloc((void *)(uintptr_t)RAM, 0x10000u, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE)) {
        printf("noram\n");
        return 0;
    }
    pvr_parts_lists(g_ptrs);
    pvr_parts_blend_option("on");
    /* tick 1: A (obj 0 part 0) one vertex; a 2D vertex; C (obj 1 part 1) 2 vertices; D (obj 2 part 2) stamp 7;
       E (obj 3 part 3) drawn twice */
    begin_tick();
    part(0, 0, 5, 1, 100.0f, 50.0f, 0.1f);
    vtx(50.0f, 50.0f, 1.0f);
    part(1, 1, 5, 2, 300.0f, 60.0f, 0.5f);
    part(2, 2, 7, 1, 400.0f, 70.0f, 0.5f);
    part(3, 3, 5, 1, 10.0f, 80.0f, 0.5f);
    part(3, 3, 5, 1, 300.0f, 80.0f, 0.5f);
    render();
    /* tick 2: A moved and nearer; the 2D vertex; C has 3 vertices now; D's stamp is 8; E twice, each 10 on; B new */
    begin_tick();
    part(0, 0, 5, 1, 200.0f, 50.0f, 0.2f);
    vtx(50.0f, 50.0f, 1.0f);
    part(1, 1, 5, 3, 310.0f, 60.0f, 0.5f);
    part(2, 2, 8, 1, 410.0f, 70.0f, 0.5f);
    part(3, 3, 5, 1, 20.0f, 80.0f, 0.5f);
    part(3, 3, 5, 1, 310.0f, 80.0f, 0.5f);
    part(0, 1, 5, 1, 500.0f, 90.0f, 0.5f);
    render();
    printf("hold %d\n", pvr_parts_hold_frame());
    show("t0", 0.0f);
    show("t05", 0.5f);
    show("t1", 1.0f);
    /* --interp-truth: frames 0..3 with A on its way from tick 2's x 200: 210, 220, 230, 240 */
    g_truth = 1;
    for (g_frame = 0; g_frame < 4; g_frame++) {
        begin_tick();
        part(0, 0, 5, 1, 210.0f + 10.0f * (f32)g_frame, 50.0f, 0.5f);
        render();
    }
    show("truth", 0.5f);
    printf("paired %d\n", pvr_parts_hold_frame());
    /* turns: F (obj 0 part 2) x 0, 10, 0; G (obj 1 part 2) 0, 10, 20; H (obj 2 part 3) 0, 0.5, 0 */
    g_truth = 0;
    for (int k = 0; k < 3; k++) {
        const f32 turn[3] = {0.0f, 10.0f, 0.0f}, wobble[3] = {0.0f, 0.5f, 0.0f};
        begin_tick();
        part(0, 2, 5, 1, turn[k], 50.0f, 0.5f);
        part(1, 2, 5, 1, 10.0f * (f32)k, 60.0f, 0.5f);
        part(2, 3, 5, 1, wobble[k], 70.0f, 0.5f);
        render();
    }
    printf("turned %d %d\n", pvr_parts_hold_frame(), pvr_parts_holds());
    show("turn", 0.5f);
    show("turn1", 1.0f);
    return 0;
}
"""


@unittest.skipUnless(HAVE_VC, NO_VC)
class PartsBlend(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        work = TOOL / "tests" / ".work"
        d = pathlib.Path(tempfile.mkdtemp(dir=work if work.is_dir() else None))
        cls.dir = d
        (d / "port_config.h").write_text(game_config.port_config_h(
            {"name": "Fixture", "entry": {"name": "Fx_Main", "addr": "8c011000"}, "image_base": "8c010000",
             "boot_file": "", "console": None, "id": "FIXTURE", "boot_sha256": []}), encoding="utf-8")
        (d / "parts_main.c").write_text(DRIVER_C, encoding="utf-8")
        rd = TOOL / "targets" / "dreamcast_sh4" / "runtime_rd"
        srcs = " ".join(f'"{p}"' for p in [KIT / "pvr" / "pvr_parts.c", d / "parts_main.c"])
        cl = (f'cl /nologo /TC /std:c11 /W4 /WX /O2 /I"{d}" /I"{KIT / "runtime"}" /I"{KIT / "pvr"}" '
              f'/I"{KIT / "base"}" /I"{rd}" {srcs} /Fe:parts.exe /link /LARGEADDRESSAWARE')
        vc = str(game_config.vcvars32()).replace("/", "\\")
        r = subprocess.run(f'cmd /d /s /c ""{vc}" >nul 2>&1 && {cl}"', cwd=d, capture_output=True, text=True,
                           errors="replace", timeout=300)
        if r.returncode or "warning" in r.stdout.lower():
            shutil.rmtree(d, ignore_errors=True)
            raise RuntimeError(r.stdout[-3000:] + r.stderr[-1000:])
        run = subprocess.run([str(d / "parts.exe")], capture_output=True, text=True, timeout=60)
        if run.returncode:
            raise RuntimeError(run.stdout + run.stderr)
        if run.stdout.strip() == "noram":
            raise unittest.SkipTest("guest RAM's address 0x8c000000 is taken in this process")
        cls.out = {ln.split(" ", 1)[0]: ln.split(" ", 1)[1] for ln in run.stdout.splitlines()}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def verts(self, name):
        """(has_from, [(x, y, 1/w)]) of a line the driver printed."""
        head, body = self.out[name].split(":", 1)
        return int(head), [tuple(round(float(c), 4) for c in item.split(",")) for item in body.split()]

    def test_a_part_moves_in_perspective(self):
        self.assertEqual(self.verts("t05")[0], 1)
        self.assertEqual(self.verts("t05")[1][0], (133.3333, 50.0, 0.1333))  # not 150: the view half way
        self.assertEqual(self.verts("t0")[1][0], (100.0, 50.0, 0.1))
        self.assertEqual(self.verts("t1")[1][0], (200.0, 50.0, 0.2))

    def test_no_part_stays(self):
        self.assertEqual(self.verts("t0")[1][1], (50.0, 50.0, 1.0))

    def test_new_changed_or_restamped_parts_stay(self):
        v = self.verts("t0")[1]
        self.assertEqual(v[2:5], [(310.0, 60.0, 0.5), (320.0, 60.0, 0.5), (330.0, 60.0, 0.5)])  # 2 vertices, now 3
        self.assertEqual(v[5], (410.0, 70.0, 0.5))  # the stamp changed
        self.assertEqual(v[8], (500.0, 90.0, 0.5))  # new

    def test_a_part_drawn_twice_pairs_in_order(self):
        self.assertEqual([p[0] for p in self.verts("t05")[1][6:8]], [15.0, 305.0])

    def test_truth_blends_the_scene_two_before(self):
        self.assertEqual(self.verts("truth")[1][0][0], 230.0)  # frames 1 and 3 at one half: where frame 2 was

    def test_a_scene_mostly_new_or_gone_holds(self):
        self.assertEqual(self.out["hold"], "1")  # 8 of 11 part vertices new or gone
        self.assertEqual(self.out["paired"], "0")

    def test_a_part_that_turned_back_stays_where_it_was(self):
        self.assertEqual(self.out["turned"], "0 1")  # one part held, not the scene
        for name in ("turn", "turn1"):
            self.assertEqual(self.verts(name)[1][0], (10.0, 50.0, 0.5))  # 0, 10, 0: where it was, at any t
        self.assertEqual(self.verts("turn")[1][1][0], 15.0)  # 0, 10, 20: on its way
        self.assertEqual(self.verts("turn")[1][2][0], 0.25)  # 0, 0.5, 0: under a pixel a tick, blended


if __name__ == "__main__":
    unittest.main()
