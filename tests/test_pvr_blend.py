"""Scene blending (port/pvr/pvr_blend.c, the in-between frames of --interp-scene): the real module, compiled at
/W4 /WX and run on small hand-made scenes. Each expected number is worked by hand:
1. one triangle moved (+10, +20) with a colour change, blended at t 0, 0.5 and 1: the old, the middle (a position
   mixed linearly, a colour mixed per channel and rounded) and the current vertices; the batch index stays the current's;
2. a vertex that moved more than 80 pixels, a depth that changed by more than half, a texture coordinate that moved by
   more than half a texture, a side that changed length or a colour far off: that triangle is drawn as it is (a cut), and
   the count of blended triangles says so; the same triangles in another order pair by nearness (a sorted mesh); a triangle with no pair moves with the
   neighbours it shares vertices with, so a mesh cannot crack;
3. a batch with another vertex count, another texture or another render state does not match the old one: drawn as it is;
   one where an old batch with another state was matches none (menu buttons 61 apart, the highlight moved down one);
4. a batch that was not in the old scene does not stop the later ones matching theirs (old [A, B], current [X, A, B]);
5. no old scene, an empty one, or another number of passes: nothing to blend (NULL);
6. an old batch matches one current batch only (old [A], current [A, A]: the second is drawn as it is);
7. pvr_blend_keep copies: changing the source scene afterwards does not change the old scene;
8. alpha 1 gives the current scene exactly, whatever matched.
9. --interp-early: the pairs worked out for a scene before it is current (pvr_blend_pair_next), then taken, blend to the
   same vertices as the pairs the drawing finds itself; dropped, nothing blends until the next scene is taken.
10. --interp-persp: a triangle the camera comes 2 nearer to, from 10, blends half way to the view at 9 (x/z, y/z,
   1/z), not to the middle of its screen positions.
Only where Visual Studio's 32-bit tools are found (tests/test_lockstep.py NO_VC says how), skipped elsewhere.
usage: python -W error tests/test_pvr_blend.py
Nearest existing: tests/test_pvr_palette.py - same build and driver pattern, a different module (this one blends scenes).
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
void port_log(const char *fmt, ...) { (void)fmt; }
int g_xcost; /* frame_cost.h's */ u64 xcost_now(void) { return 0; }
void xcost_add_slow(int part, u64 us) { (void)part; (void)us; }
int g_blend_persp; /* pvr_blend_opt.c's */
int pvr_blend_truth_skip(void) { return 0; }
f32 pvr_blend_truth_t(f32 t) { return t; }
const PvrVtx *pvr_parts_blend(const PvrScene *c, f32 t, u32 *n) { return (void)c, (void)t, (void)n, NULL; }
int pvr_parts_hold_frame(void) { return 0; }
/* each call here is a new current scene */
#define pvr_blend_vertices(s, t, n) (pvr_blend_new_scene(), pvr_blend_vertices(s, t, n))

#define MAXV 64
#define MAXB 8
typedef struct { PvrScene s; PvrVtx v[MAXV]; PvrBatch b[MAXB]; } Fix;

static void begin(Fix *f, u32 passes)
{
    memset(f, 0, sizeof *f);
    f->s.vtx = f->v;
    f->s.batch = f->b;
    f->s.vcap = MAXV;
    f->s.bcap = MAXB;
    f->s.passes = passes;
}
/* a batch of n triangles in pass 0, list 0, texture tcw; triangle k has its corner 0 at (x + k, y), the others
 * (x + 100 + k, y) and (x + k, y + 100); every vertex z, uv 0, base base */
static void batch(Fix *f, u32 n, u32 tcw, f32 x, f32 y, f32 z, u32 base)
{
    PvrBatch *b = &f->b[f->s.nbatch];
    b->list = 0;
    b->first = f->s.nvtx;
    b->count = n * 3u;
    b->tcw = tcw;
    b->isp = 1;
    for (u32 k = 0; k < n; k++) {
        f32 px[3] = {x + (f32)k, x + 100.0f + (f32)k, x + (f32)k}, py[3] = {y, y, y + 100.0f};
        for (u32 i = 0; i < 3u; i++) {
            PvrVtx *v = &f->v[f->s.nvtx++];
            v->x = px[i], v->y = py[i], v->z = z, v->u = 0.0f, v->v = 0.0f, v->base = base, v->offset = 0;
            v->batch = f->s.nbatch;
        }
    }
    if (!f->s.range[0][0][1]) f->s.range[0][0][0] = f->s.nbatch;
    f->s.nbatch++;
    f->s.range[0][0][1] = f->s.nbatch;
}
static void show(const char *name, const PvrVtx *v, u32 n, u32 blended)
{
    if (!v) { printf("%s NULL\n", name); return; }
    printf("%s blended %u:", name, blended);
    for (u32 i = 0; i < n; i++) printf(" %.2f,%.2f,%.4f,%08x,b%u", v[i].x, v[i].y, v[i].z, v[i].base, v[i].batch);
    printf("\n");
}
int main(void)
{
    static Fix a, c;
    const PvrVtx *o;
    u32 n;

    /* 5. nothing kept yet; an empty scene is not kept */
    begin(&c, 1);
    batch(&c, 1, 7, 100, 100, 0.5f, 0xff000000u);
    o = pvr_blend_vertices(&c.s, 0.5f, &n); show("none", o, 0, 0);
    begin(&a, 1);
    pvr_blend_keep(&a.s);
    printf("empty have %d\n", pvr_blend_have());
    o = pvr_blend_vertices(&c.s, 0.5f, &n); show("emptyold", o, 0, 0);

    /* 1. a moved, recoloured triangle */
    begin(&a, 1);
    batch(&a, 1, 7, 100, 100, 0.5f, 0xff000000u);
    pvr_blend_keep(&a.s);
    printf("have %d\n", pvr_blend_have());
    begin(&c, 1);
    batch(&c, 1, 7, 110, 120, 0.5f, 0xff3c3c3cu);
    o = pvr_blend_vertices(&c.s, 0.0f, &n); show("t0", o, 3, n);
    o = pvr_blend_vertices(&c.s, 0.5f, &n); show("t05", o, 3, n);
    o = pvr_blend_vertices(&c.s, 1.0f, &n); show("t1", o, 3, n);

    /* 6. keep copied: spoil the source */
    a.v[0].x = 9999.0f;
    o = pvr_blend_vertices(&c.s, 0.5f, &n); show("copied", o, 1, n);

    /* 2. cut guards: 200 pixels, depth ratio, texture coordinate */
    begin(&c, 1);
    batch(&c, 1, 7, 300, 100, 0.5f, 0xff000000u);
    o = pvr_blend_vertices(&c.s, 0.5f, &n); show("far", o, 3, n);
    begin(&c, 1);
    batch(&c, 1, 7, 100, 100, 5.0f, 0xff000000u);
    o = pvr_blend_vertices(&c.s, 0.5f, &n); show("deep", o, 3, n);
    begin(&c, 1);
    batch(&c, 1, 7, 100, 100, 0.5f, 0xff000000u);
    c.v[1].u = 0.75f;
    o = pvr_blend_vertices(&c.s, 0.5f, &n); show("uv", o, 3, n);

    /* 3. count, texture, state */
    begin(&c, 1);
    batch(&c, 2, 7, 110, 100, 0.5f, 0xff000000u);
    o = pvr_blend_vertices(&c.s, 0.5f, &n); show("count", o, 3, n);
    begin(&c, 1);
    batch(&c, 1, 8, 110, 100, 0.5f, 0xff000000u);
    o = pvr_blend_vertices(&c.s, 0.5f, &n); show("tcw", o, 3, n);

    /* 5. another number of passes */
    begin(&c, 2);
    batch(&c, 1, 7, 110, 100, 0.5f, 0xff000000u);
    o = pvr_blend_vertices(&c.s, 0.5f, &n); show("passes", o, 0, 0);

    /* 4. old [A, B], current [X, A, B]: A and B still match; X (a texture the old scene lacked) does not */
    begin(&a, 1);
    batch(&a, 1, 1, 100, 100, 0.5f, 0xff000000u);
    batch(&a, 1, 2, 200, 100, 0.5f, 0xff000000u);
    pvr_blend_keep(&a.s);
    begin(&c, 1);
    batch(&c, 1, 9, 400, 300, 0.5f, 0xff000000u);
    batch(&c, 1, 1, 110, 100, 0.5f, 0xff000000u);
    batch(&c, 1, 2, 210, 100, 0.5f, 0xff000000u);
    o = pvr_blend_vertices(&c.s, 0.5f, &n);
    printf("insert blended %u x %.2f %.2f %.2f\n", n, o[0].x, o[3].x, o[6].x);

    /* an old batch matches one current batch: old [A], current [A, A] with one state: the second is drawn as it is */
    begin(&a, 1);
    batch(&a, 1, 1, 100, 100, 0.5f, 0xff000000u);
    pvr_blend_keep(&a.s);
    begin(&c, 1);
    batch(&c, 1, 1, 110, 100, 0.5f, 0xff000000u);
    batch(&c, 1, 1, 300, 100, 0.5f, 0xff000000u);
    o = pvr_blend_vertices(&c.s, 0.5f, &n);
    printf("twice blended %u x %.2f %.2f\n", n, o[0].x, o[3].x);

    /* 7. alpha 1 is the current scene */
    o = pvr_blend_vertices(&c.s, 1.0f, &n);
    printf("alpha1 same %d\n", !memcmp(o, c.v, 6u * sizeof *o));

    /* 3. menu buttons 61 apart, old [A, B, A, A, A], current [A, A, B, A, A]: nothing moves */
    begin(&a, 1), begin(&c, 1);
    for (u32 k = 0; k < 5u; k++) {
        batch(&a, 1, k == 1u ? 2u : 1u, 100, 100.0f + 61.0f * (f32)k, 0.5f, 0xff000000u);
        batch(&c, 1, k == 2u ? 2u : 1u, 100, 100.0f + 61.0f * (f32)k, 0.5f, 0xff000000u);
    }
    pvr_blend_keep(&a.s);
    o = pvr_blend_vertices(&c.s, 0.5f, &n);
    printf("menu blended %u still %d\n", n, !memcmp(o, c.v, c.s.nvtx * sizeof *o));
    /* 10. --interp-persp: the view's corners (-1,0,10) (1,0,10) (0,1,10), then 2 nearer, seen at 320 + 300 X/Z,
     * 240 + 300 Y/Z, z 1/Z; half way is the view at Z 9, not the screen positions' middle */
    g_blend_persp = 1;
    {
        static const f32 corner[3][2] = {{-1, 0}, {1, 0}, {0, 1}};
        Fix *both[2] = {&a, &c};
        for (u32 k = 0; k < 2u; k++) {
            f32 zv = k ? 8.0f : 10.0f;
            begin(both[k], 1);
            batch(both[k], 1, 5, 0, 0, 0.5f, 0xff000000u);
            for (u32 i = 0; i < 3u; i++) {
                both[k]->v[i].x = 320.0f + 300.0f * corner[i][0] / zv;
                both[k]->v[i].y = 240.0f + 300.0f * corner[i][1] / zv;
                both[k]->v[i].z = 1.0f / zv;
            }
            if (!k) pvr_blend_keep(&a.s);
        }
        o = pvr_blend_vertices(&c.s, 0.5f, &n); show("persp", o, 3, n);
    }
    g_blend_persp = 0;

    /* a sorted mesh: three triangles of sides 20, 60 and 100 in another order, moved 10; a triangle of another shape */
    {
        static const f32 at[3][2] = {{100, 20}, {110, 60}, {120, 100}};
        begin(&a, 1);
        batch(&a, 3, 1, 0, 100, 0.5f, 0xff000000u);
        for (u32 k = 0; k < 3u; k++) {
            a.v[k * 3u].x = at[k][0]; a.v[k * 3u + 1u].x = at[k][0] + at[k][1]; a.v[k * 3u + 2u].x = at[k][0];
            a.v[k * 3u + 2u].y = 100.0f + at[k][1];
        }
        pvr_blend_keep(&a.s);
        begin(&c, 1);
        batch(&c, 3, 1, 0, 100, 0.5f, 0xff000000u);
        for (u32 k = 0; k < 3u; k++) {
            const f32 *m = at[2u - k];
            c.v[k * 3u].x = m[0] + 10.0f; c.v[k * 3u + 1u].x = m[0] + 10.0f + m[1]; c.v[k * 3u].x = m[0] + 10.0f;
            c.v[k * 3u + 2u].x = m[0] + 10.0f; c.v[k * 3u + 2u].y = 100.0f + m[1];
        }
        o = pvr_blend_vertices(&c.s, 0.5f, &n);
        printf("sorted blended %u x %.2f %.2f %.2f\n", n, o[0].x, o[3].x, o[6].x);
        begin(&c, 1);
        batch(&c, 3, 1, 0, 100, 0.5f, 0xff000000u);
        for (u32 k = 0; k < 3u; k++) {
            c.v[k * 3u].x = 110.0f + k; c.v[k * 3u + 1u].x = 150.0f + k * 80.0f; c.v[k * 3u + 2u].x = 110.0f + k;
            c.v[k * 3u + 2u].y = 400.0f;
        }
        o = pvr_blend_vertices(&c.s, 0.5f, &n);
        printf("shape blended %u x %.2f\n", n, o[0].x);
        begin(&c, 1);
        batch(&c, 1, 7, 100, 100, 0.5f, 0xffffffffu);
        begin(&a, 1);
        batch(&a, 1, 7, 100, 100, 0.5f, 0xff000000u);
        pvr_blend_keep(&a.s);
        o = pvr_blend_vertices(&c.s, 0.5f, &n);
        printf("colour blended %u\n", n);
        /* a digit: the same place, the texture a step along */
        begin(&a, 1);
        batch(&a, 1, 4, 100, 100, 0.5f, 0xff000000u);
        for (u32 k = 0; k < 3u; k++) a.v[k].u = 0.25f;
        pvr_blend_keep(&a.s);
        begin(&c, 1);
        batch(&c, 1, 4, 100, 100, 0.5f, 0xff000000u);
        for (u32 k = 0; k < 3u; k++) c.v[k].u = 0.5f;
        o = pvr_blend_vertices(&c.s, 0.5f, &n);
        printf("digit blended %u u %.3f\n", n, o[0].u);
        begin(&c, 1);
        batch(&c, 1, 4, 100, 100, 0.5f, 0xff000000u);
        for (u32 k = 0; k < 3u; k++) c.v[k].u = 0.26f;
        o = pvr_blend_vertices(&c.s, 0.5f, &n);
        printf("scroll blended %u u %.3f\n", n, o[0].u);
        /* two triangles sharing an edge, moved 10; the second's far corner goes off too far for it to pair */
        begin(&a, 1);
        batch(&a, 2, 3, 0, 0, 0.5f, 0xff000000u);
        a.v[0].x = 0; a.v[0].y = 0; a.v[1].x = 100; a.v[1].y = 0; a.v[2].x = 0; a.v[2].y = 100;
        a.v[3].x = 100; a.v[3].y = 0; a.v[4].x = 0; a.v[4].y = 100; a.v[5].x = 100; a.v[5].y = 100;
        pvr_blend_keep(&a.s);
        begin(&c, 1);
        batch(&c, 2, 3, 0, 0, 0.5f, 0xff000000u);
        c.v[0].x = 10; c.v[0].y = 0; c.v[1].x = 110; c.v[1].y = 0; c.v[2].x = 10; c.v[2].y = 100;
        c.v[3].x = 110; c.v[3].y = 0; c.v[4].x = 10; c.v[4].y = 100; c.v[5].x = 400; c.v[5].y = 400;
        o = pvr_blend_vertices(&c.s, 0.5f, &n);
        printf("crack blended %u shared %d %d x %.2f far %.2f\n", n, o[1].x == o[3].x && o[1].y == o[3].y,
               o[2].x == o[4].x && o[2].y == o[4].y, o[1].x, o[5].x);
        /* --interp-early: the same scenes, paired before the current one is c, then taken */
        {
            static PvrVtx kept[MAXV];
            const PvrVtx *e;
            u32 m;
            memcpy(kept, o, c.s.nvtx * sizeof *o);
            pvr_blend_pair_next(&a.s, &c.s);
            pvr_blend_take(&a.s);
            e = (pvr_blend_vertices)(&c.s, 0.5f, &m);
            printf("early same %d blended %u\n", e && !memcmp(e, kept, c.s.nvtx * sizeof *e), m);
            pvr_blend_new_scene();
            printf("earlydrop %s\n", (pvr_blend_vertices)(&c.s, 0.5f, &m) ? "blends" : "NULL");
        }
    }

    return 0;
}
"""


@unittest.skipUnless(HAVE_VC, NO_VC)
class SceneBlend(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        work = TOOL / "tests" / ".work"
        d = pathlib.Path(tempfile.mkdtemp(dir=work if work.is_dir() else None))
        cls.dir = d
        (d / "port_config.h").write_text(game_config.port_config_h(
            {"name": "Fixture", "entry": {"name": "Fx_Main", "addr": "8c011000"}, "image_base": "8c010000",
             "boot_file": "", "console": None, "id": "FIXTURE", "boot_sha256": []}), encoding="utf-8")
        (d / "blend_main.c").write_text(DRIVER_C, encoding="utf-8")
        rd = TOOL / "targets" / "dreamcast_sh4" / "runtime_rd"
        srcs = " ".join(f'"{p}"' for p in [KIT / "pvr" / "pvr_blend.c", d / "blend_main.c"])
        cl = (f'cl /nologo /TC /std:c11 /W4 /WX /O2 /I"{d}" /I"{KIT / "runtime"}" /I"{KIT / "pvr"}" '
              f'/I"{KIT / "base"}" /I"{rd}" {srcs} /Fe:blend.exe')
        vc = str(game_config.vcvars32()).replace("/", "\\")
        r = subprocess.run(f'cmd /d /s /c ""{vc}" >nul 2>&1 && {cl}"', cwd=d, capture_output=True, text=True,
                           errors="replace", timeout=300)
        if r.returncode or "warning" in r.stdout.lower():
            shutil.rmtree(d, ignore_errors=True)
            raise RuntimeError(r.stdout[-3000:] + r.stderr[-1000:])
        run = subprocess.run([str(d / "blend.exe")], capture_output=True, text=True, timeout=60)
        if run.returncode:
            raise RuntimeError(run.stdout + run.stderr)
        cls.out = {ln.split(" ", 1)[0]: ln.split(" ", 1)[1] for ln in run.stdout.splitlines()}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def verts(self, name):
        """(blended count, [(x, y, z, argb, batch)]) of a line the driver printed."""
        head, body = self.out[name].split(":", 1)
        out = []
        for item in body.split():
            x, y, z, argb, b = item.split(",")
            out.append((float(x), float(y), float(z), argb, int(b[1:])))
        return int(head.split()[1]), out

    def test_nothing_to_blend(self):
        out = [self.out[k] for k in ("none", "empty", "emptyold", "passes")]
        self.assertEqual(out, ["NULL", "have 0", "NULL", "NULL"])

    def test_moved_triangle(self):
        # the old triangle: (100,100) (200,100) (100,200), black; the current is (+10, +20), 0x64 grey
        n, v = self.verts("t0")
        self.assertEqual((n, v[0][:2], v[1][:2], v[2][:2]), (1, (100.0, 100.0), (200.0, 100.0), (100.0, 200.0)))
        self.assertEqual(v[0][3], "ff000000")
        n, v = self.verts("t05")  # 105,110 and each channel 0x3c / 2 = 30 = 0x1e, alpha 0xff
        self.assertEqual((n, v[0][:3], v[1][:2], v[2][:2]), (1, (105.0, 110.0, 0.5), (205.0, 110.0), (105.0, 210.0)))
        self.assertEqual(v[0][3], "ff1e1e1e")
        n, v = self.verts("t1")
        self.assertEqual((n, v[0][:2], v[0][3]), (1, (110.0, 120.0), "ff3c3c3c"))
        self.assertTrue(all(b == 0 for *_, b in self.verts("t05")[1]))  # the batch index is the current's
        self.assertEqual(self.verts("copied")[1][0][0], 105.0)  # kept a copy: not the 9999 the source was spoiled to

    def test_cuts_are_drawn_as_they_are(self):
        for name, first in (("far", 300.0), ("deep", 100.0), ("uv", 100.0)):
            n, v = self.verts(name)
            self.assertEqual((name, n), (name, 0))
            self.assertEqual(v[0][0], first)
        self.assertEqual(self.verts("deep")[1][0][2], 5.0)

    def test_perspective_blend_is_the_view_half_way(self):
        # Z 9: 320 -/+ 300/9 = 286.67 and 353.33, 240 + 300/9 = 273.33, z 1/9; the screen's middle would be 286.25, 273.75
        n, v = self.verts("persp")
        self.assertEqual((n, [p[:3] for p in v]), (1, [(286.67, 240.0, 0.1111), (353.33, 240.0, 0.1111),
                                                       (320.0, 273.33, 0.1111)]))

    def test_unmatched_batches(self):
        for name in ("count", "tcw"):
            n, v = self.verts(name)
            self.assertEqual((name, n, v[0][0]), (name, 0, 110.0))

    def test_later_batches_still_match(self):
        # 2 triangles' worth matched (A and B), moved 10 pixels: halfway is +5; X (at 400) stays
        parts = self.out["insert"].split()
        self.assertEqual(parts[1], "2")
        self.assertEqual(parts[3:], ["400.00", "105.00", "205.00"])

    def test_an_old_batch_matches_once(self):
        parts = self.out["twice"].split()
        self.assertEqual((parts[1], parts[3:]), ("1", ["105.00", "300.00"]))
        self.assertEqual(self.out["alpha1"], "same 1")  # alpha 1 is the current scene

    def test_a_sorted_mesh_pairs_by_nearness(self):
        # halfway from each triangle's old place, not from the one at its old index
        self.assertEqual(self.out["sorted"], "blended 3 x 125.00 115.00 105.00")

    def test_another_shape_or_colour_is_drawn_as_it_is(self):
        parts = self.out["shape"].split()
        self.assertEqual((parts[1], parts[3]), ("0", "110.00"))
        self.assertEqual(self.out["colour"], "blended 0")

    def test_a_changed_picture_does_not_slide(self):
        self.assertEqual(self.out["digit"], "blended 1 u 0.500")
        self.assertEqual(self.out["scroll"], "blended 1 u 0.255")
        self.assertEqual(self.out["menu"], "blended 3 still 1")  # buttons that only changed texture don't slide

    def test_a_mesh_does_not_crack(self):
        # the triangle that can't pair still shares its edge; its own far corner has no movement
        self.assertEqual(self.out["crack"], "blended 1 shared 1 1 x 105.00 far 400.00")

    def test_early_pairs_blend_the_same(self):
        # --interp-early: pairs found for the next scene before it is current, then taken, give the drawing's own
        # vertices; dropped (blending off), nothing blends until the next scene is taken
        self.assertEqual(self.out["early"], "same 1 blended 1")
        self.assertEqual(self.out["earlydrop"], "NULL")


if __name__ == "__main__":
    unittest.main()
