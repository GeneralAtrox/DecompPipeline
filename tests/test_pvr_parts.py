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
6. a scene where half the parts' vertices or more are new or gone (6 of 12 in the second tick: a part with another
   count is still that part) gets no in-between
   frames; one where every part paired does;
7. a part that turned back (x 0, 10, 0) is drawn where it was a tick before, 10, at any t; one on its way (0, 10, 20)
   blends to 15, and one that moved under a pixel a tick (0, 0.5, 0) blends to 0.25;
8. a split screen (a render per view, pvr_render_part): one part in both views pairs with its own view a tick before,
   view 0 from x 100 to 110 blending to 105 and view 1 from 400 to 390 to 395, not with the other view's render; a view
   drawn with another clip than its slot had pairs with nothing, and the frame holds.
9. a part of two pieces (a helicopter: 15 body vertices 20 on, 8 rotor vertices a quarter turn round as well, one
   vertex culled) moves by the map of its larger piece, 10 back from where it is, and the rotor's vertices each
   from their own a tick before, half way;
10. a scene whose view jumped (64 paired vertices, all 100 on in a tick) gets no in-between frames; 10 on, it does;
11. a vertex clamped at the near plane in both ticks moves across the screen, still clamped: -9000 to -8000 at -8500.
12. a part with another count whose vertices sit in one plane in the view (a 4x3 grid, 11 left after one is culled, all at
   1/w 0.5) plus 4 corners at 1/w 0.25 that a strip draws twice (one key, one place), all 20 on in x: each vertex whose
   key is at one place both ticks (all 19, the corners' two copies too) comes from its own place a tick before, so at
   one half every vertex is 10 on in x, y and 1/w as they are (with the same 1/w both ticks the view half way is the
   screen half way) - the corners at x 110, 120, 130, 140, not thrown out of the plane by a map fitted on the grid
   alone; the scene is not held.
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
from pvr_parts_driver import DRIVER_C  # noqa: E402


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
        srcs = " ".join(f'"{p}"' for p in [KIT / "pvr" / "pvr_parts.c", KIT / "pvr" / "pvr_parts_fit.c", KIT / "pvr" / "pvr_parts_blend.c",
                                                d / "parts_main.c"])
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

    def test_a_part_drawn_twice_pairs_with_the_nearest(self):
        self.assertEqual([p[0] for p in self.verts("t05")[1][6:8]], [15.0, 305.0])
        self.assertEqual([p[0] for p in self.verts("swap")[1]], [315.0, 25.0])  # drawn in the other order: still nearest

    def test_truth_blends_the_scene_two_before(self):
        self.assertEqual(self.verts("truth")[1][0][0], 230.0)  # frames 1 and 3 at one half: where frame 2 was

    def test_a_scene_mostly_new_or_gone_holds(self):
        self.assertEqual(self.out["hold"], "1")  # 6 of 12 part vertices new or gone
        self.assertEqual(self.out["paired"], "0")

    def test_a_part_that_turned_back_stays_where_it_was(self):
        self.assertEqual(self.out["turned"], "0 1")  # one part held, not the scene
        for name in ("turn", "turn1"):
            self.assertEqual(self.verts(name)[1][0], (10.0, 50.0, 0.5))  # 0, 10, 0: where it was, at any t
        self.assertEqual(self.verts("turn")[1][1][0], 15.0)  # 0, 10, 20: on its way
        self.assertEqual(self.verts("turn")[1][2][0], 0.25)  # 0, 0.5, 0: under a pixel a tick, blended

    def test_split_screen_views_pair_with_their_own_view(self):
        self.assertEqual(self.verts("view0")[1][0][0], 105.0)
        self.assertEqual(self.verts("view1")[1][0][0], 395.0)  # not from view 0's render: 250
        self.assertEqual(self.out["views"], "0")
        self.assertEqual(self.out["reclip"], "1")  # another view in the slot: nothing pairs

    def test_a_culled_part_moves_by_its_fitted_map(self):  # Toy Commander culls and clips per polygon in software
        self.assertEqual(self.out["culled"], "0")  # paired through its map: the scene is not held
        has, v = self.verts("cull")
        kept = [i for i in range(12) if i not in (2, 6)]
        want = [(110.0 + 10 * (i % 4), 100.0 + 10 * (i // 4)) for i in kept] + [(140.0, 150.0)]  # the new vertex too
        self.assertEqual((has, len(v)), (1, len(want)))
        for got, (x, y) in zip(v, want):
            self.assertAlmostEqual(got[0], x, delta=0.01)
            self.assertAlmostEqual(got[1], y, delta=0.01)
            self.assertAlmostEqual(got[2], 0.5, delta=0.001)

    def test_a_flat_culled_part_keeps_its_repeated_corners(self):  # Toy Commander: a floor strip draws its corners twice
        self.assertEqual(self.out["flatheld"], "0")  # all 19 vertices paired by place: not held
        has, v = self.verts("flat")
        kept = [i for i in range(12) if i != 5]
        want = [(110.0 + 10 * (i % 4), 100.0 + 10 * (i // 4), 0.5) for i in kept]  # the grid: each 10 on
        want += [(110.0 + 10 * (j // 2), 140.0, 0.25) for j in range(8)]  # each corner twice, 10 on, not thrown
        self.assertEqual((has, len(v)), (1, len(want)))
        for got, w in zip(v, want):
            for g, e in zip(got, w):
                self.assertAlmostEqual(g, e, delta=0.01)

    def test_vertices_clamped_at_the_near_plane_stay(self):  # Toy Commander: 1/w 0.1, x and y as projected
        v = self.verts("clamp")[1]
        self.assertEqual(v[0], (109.0909, 100.0, 0.0546))  # in perspective, as any vertex
        self.assertEqual(v[2:4], [(-9000.0, 300.0, 0.1), (9000.0, 300.0, 0.1)])  # no point of the part: as now
        v = self.verts("clamped")[1]  # a scene with none: the clamp is kept
        self.assertEqual(v[0], (122.5, 100.0, 0.06))
        self.assertEqual(v[2:], [(140.0, 100.0, 0.06), (150.0, 100.0, 0.06)])  # clamped a tick before: as now

    def test_a_part_drawn_in_another_order_pairs_by_its_map(self):  # Toy Commander: the floor under the eye
        self.assertEqual(self.out["reordered"], "0")
        has, v = self.verts("reorder")
        want = [(110.0 + 10 * (i % 4), 100.0 + 10 * (i // 4)) for i in ((j + (5 if j < 12 else 7)) % 12 for j in range(24))]
        self.assertEqual((has, len(v)), (1, 24))
        for got, (x, y) in zip(v, want):  # not torn: each from where it was, not from the one drawn there before
            self.assertAlmostEqual(got[0], x, delta=0.01)
            self.assertAlmostEqual(got[1], y, delta=0.01)

    def test_a_part_of_two_pieces_moves_piece_by_piece(self):  # Toy Commander: a helicopter and its rotor are one part
        self.assertEqual(self.out["twopiece"], "0")
        has, v = self.verts("two")
        rx, ry = [6.0, 4.243, 0.0, -4.243, -6.0, -4.243, 0.0, 4.243], [0.0, 4.243, 6.0, 4.243, 0.0, -4.243, -6.0, -4.243]
        want = [(210.0 + 10 * (i % 4), 200.0 + 10 * (i // 4)) for i in range(16) if i != 5]  # the body: half way
        want += [(225.0 + (rx[j] - ry[j]) / 2, 215.0 + (ry[j] + rx[j]) / 2) for j in range(8)]  # the rotor: each vertex from its own
        self.assertEqual((has, len(v)), (1, len(want)))
        for got, (x, y) in zip(v, want):
            self.assertAlmostEqual(got[0], x, delta=0.01)
            self.assertAlmostEqual(got[1], y, delta=0.01)

    def test_a_scene_whose_view_jumped_holds(self):
        self.assertEqual(self.out["jump"], "1")  # all 64 vertices 100 on in a tick
        self.assertEqual(self.out["glide"], "0")  # 10 on

    def test_vertices_clamped_in_both_ticks_move_across_the_screen(self):
        v = self.verts("both")[1]
        for got, want in zip(v[2:4], [(-8500.0, 310.0, 0.1), (9200.0, 310.0, 0.1)]):  # still clamped
            for g, w in zip(got, want):
                self.assertAlmostEqual(g, w, delta=0.01)
        self.assertEqual(v[4], (50.0, 50.0, 1.0))


if __name__ == "__main__":
    unittest.main()
