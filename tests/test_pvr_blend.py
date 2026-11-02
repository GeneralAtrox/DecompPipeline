"""Scene blending (port/pvr/pvr_blend.c, the in-between frames of --interp-scene): the real module, compiled at
/W4 /WX and run on small hand-made scenes. Each expected number is worked by hand:
1. one triangle moved (+10, +20) with a colour change, blended at t 0, 0.5 and 1: the old, the middle (a position
   mixed linearly, a colour mixed per channel and rounded) and the current vertices; the batch index stays the current's;
2. a vertex that moved more than 80 pixels, a depth that changed by more than half, or a texture coordinate that moved by
   more than half a texture: that triangle is drawn as it is (a cut), and the count of blended triangles says so;
3. a batch with another vertex count, another texture or another render state does not match the old one: drawn as it is;
4. a batch that was not in the old scene does not stop the later ones matching theirs (old [A, B], current [X, A, B]);
5. no old scene, an empty one, or another number of passes: nothing to blend (NULL);
6. an old batch matches one current batch only (old [A], current [A, A]: the second is drawn as it is);
7. pvr_blend_keep copies: changing the source scene afterwards does not change the old scene;
8. alpha 1 gives the current scene exactly, whatever matched.
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
    o = pvr_blend_vertices(&c.s, 0.5f, &n);
    show("none", o, 0, 0);
    begin(&a, 1);
    pvr_blend_keep(&a.s);
    printf("empty have %d\n", pvr_blend_have());
    o = pvr_blend_vertices(&c.s, 0.5f, &n);
    show("emptyold", o, 0, 0);

    /* 1. a moved, recoloured triangle */
    begin(&a, 1);
    batch(&a, 1, 7, 100, 100, 0.5f, 0xff000000u);
    pvr_blend_keep(&a.s);
    printf("have %d\n", pvr_blend_have());
    begin(&c, 1);
    batch(&c, 1, 7, 110, 120, 0.5f, 0xff646464u);
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
    batch(&c, 1, 1, 110, 100, 0.5f, 0xff000000u);
    o = pvr_blend_vertices(&c.s, 0.5f, &n);
    printf("twice blended %u x %.2f %.2f\n", n, o[0].x, o[3].x);

    /* 7. alpha 1 is the current scene */
    o = pvr_blend_vertices(&c.s, 1.0f, &n);
    printf("alpha1 same %d\n", !memcmp(o, c.v, 6u * sizeof *o));
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
        self.assertEqual(self.out["none"], "NULL")
        self.assertEqual(self.out["empty"], "have 0")
        self.assertEqual(self.out["emptyold"], "NULL")
        self.assertEqual(self.out["passes"], "NULL")

    def test_moved_triangle(self):
        # the old triangle: (100,100) (200,100) (100,200), black; the current is (+10, +20), 0x64 grey
        n, v = self.verts("t0")
        self.assertEqual((n, v[0][:2], v[1][:2], v[2][:2]), (1, (100.0, 100.0), (200.0, 100.0), (100.0, 200.0)))
        self.assertEqual(v[0][3], "ff000000")
        n, v = self.verts("t05")  # 105,110 and each channel 0x64 / 2 = 50 = 0x32, alpha 0xff
        self.assertEqual((n, v[0][:3], v[1][:2], v[2][:2]), (1, (105.0, 110.0, 0.5), (205.0, 110.0), (105.0, 210.0)))
        self.assertEqual(v[0][3], "ff323232")
        n, v = self.verts("t1")
        self.assertEqual((n, v[0][:2], v[0][3]), (1, (110.0, 120.0), "ff646464"))

    def test_batch_index_is_the_currents(self):
        self.assertTrue(all(b == 0 for *_, b in self.verts("t05")[1]))

    def test_keep_copies(self):
        n, v = self.verts("copied")
        self.assertEqual(v[0][0], 105.0)  # not the 9999 the source was spoiled to

    def test_cuts_are_drawn_as_they_are(self):
        for name, first in (("far", 300.0), ("deep", 100.0), ("uv", 100.0)):
            n, v = self.verts(name)
            self.assertEqual((name, n), (name, 0))
            self.assertEqual(v[0][0], first)
        self.assertEqual(self.verts("deep")[1][0][2], 5.0)

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
        self.assertEqual((parts[1], parts[3:]), ("1", ["105.00", "110.00"]))

    def test_alpha_one_is_current(self):
        self.assertEqual(self.out["alpha1"], "same 1")


if __name__ == "__main__":
    unittest.main()
