"""The windowed game window's size and place (port/base/host_place.h): the arithmetic that keeps the whole outer window,
title bar and menu bar included, inside the display's work area, whatever the display scaling. The header is built
alone with a small driver (no SDL, no window); a second part reads host_window.cpp to check that it uses the header and
no longer centres the client area alone (the old placement cut the title bar off the top of a 2560x1600 desktop at 150%).
Only where Visual Studio's tools are found (as tests/test_lockstep.py); without them the arithmetic tests skip and NO_VC
says so on stderr: a skip is not a pass.
Run: python -W error tests/test_window_place.py
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

import game_config  # noqa: E402

DRIVER_C = r"""/* Reads lines: "fit AX AY AW AH CW CH" (the fit multiple), "client AX AY AW AH CW CH W H" (the client size after
 * shrinking) or "origin AX AY AW AH OW OH" (the outer window's top left), and prints the answer. */
#include <stdio.h>
#include "host_place.h"

int main(void)
{
    char op[16];
    while (scanf("%15s", op) == 1) {
        PlaceRect a;
        int cw, ch, w, h, x, y;
        if (!strcmp(op, "fit") && scanf("%d %d %d %d %d %d", &a.x, &a.y, &a.w, &a.h, &cw, &ch) == 6) {
            printf("%d\n", place_fit_scale(a, cw, ch, 640, 480));
        } else if (!strcmp(op, "client") && scanf("%d %d %d %d %d %d %d %d", &a.x, &a.y, &a.w, &a.h, &cw, &ch, &w, &h) == 8) {
            place_client(a, cw, ch, &w, &h);
            printf("%d %d\n", w, h);
        } else if (!strcmp(op, "origin") && scanf("%d %d %d %d %d %d", &a.x, &a.y, &a.w, &a.h, &w, &h) == 6) {
            place_origin(a, w, h, &x, &y);
            printf("%d %d\n", x, y);
        } else {
            return 2;
        }
    }
    return 0;
}
"""


class Place(unittest.TestCase):
    out = None

    @classmethod
    def setUpClass(cls):
        cls.out = pathlib.Path(tempfile.mkdtemp())
        vc = game_config.vcvars32()
        if vc is None:
            sys.stderr.write("NO_VC: Visual Studio's tools are not found: the window placement arithmetic tests are "
                             "skipped, not passed\n")
            raise unittest.SkipTest("no Visual Studio tools")
        (cls.out / "driver.c").write_text("#include <string.h>\n" + DRIVER_C, encoding="utf-8")
        cl = (f'cl /nologo /TC /std:c11 /W4 /WX /O1 /D_CRT_SECURE_NO_WARNINGS /I"{KIT / "base"}" "{cls.out / "driver.c"}" '
              f'/Fe"{cls.out / "place.exe"}"')
        r = subprocess.run(f'cmd /d /s /c ""{str(vc).replace("/", chr(92))}" >nul 2>&1 && {cl}"', cwd=cls.out,
                           capture_output=True, text=True, errors="replace", timeout=300)
        if r.returncode:
            raise AssertionError(r.stdout[-3000:] + r.stderr[-1000:])

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.out, ignore_errors=True)

    def ask(self, *lines):
        r = subprocess.run([str(self.out / "place.exe")], input="\n".join(lines) + "\n", capture_output=True, text=True,
                           timeout=30)
        self.assertEqual(r.returncode, 0, r.stdout)
        return [tuple(int(v) for v in x.split()) for x in r.stdout.splitlines()]

    def test_the_case_that_was_cut_off(self):
        """2560x1600 at 150%: work area 2560x1528, chrome 22x86 (title bar, borders, menu bar): fit is 3x (1920x1440), the
        outer window 1942x1526 is centred 1 px below the top. The old placement centred the 1920x1440 client area alone,
        which left the outer window's top at -31."""
        self.assertEqual(self.ask("fit 0 0 2560 1528 22 86"), [(3,)])
        self.assertEqual(self.ask("origin 0 0 2560 1528 1942 1526"), [(309, 1)])

    def test_fit_counts_the_chrome(self):
        self.assertEqual(self.ask("fit 0 0 1920 1080 16 80"), [(2,)])  # 2x: 1280x960 + chrome fits 1000; 3x does not
        self.assertEqual(self.ask("fit 0 0 1920 1040 16 80"), [(2,)])
        self.assertEqual(self.ask("fit 0 0 3000 1440 16 80"), [(2,)])  # 1440 high alone would hold 3x; the chrome stops it
        self.assertEqual(self.ask("fit 0 0 1936 1520 16 80"), [(3,)])
        self.assertEqual(self.ask("fit 0 0 700 500 16 80"), [(0,)])  # not even 1x

    def test_the_picture_shrinks_keeping_its_aspect(self):
        # 1x (640x480) with chrome 16x80 in a 600x400 area: 584x320 left, the height is what runs out
        self.assertEqual(self.ask("client 0 0 600 400 16 80 640 480"), [(426, 320)])
        # a width that runs out first
        self.assertEqual(self.ask("client 0 0 500 900 16 80 640 480"), [(484, 363)])
        # it fits: unchanged
        self.assertEqual(self.ask("client 0 0 1920 1080 16 80 1280 960"), [(1280, 960)])
        # an area smaller than the chrome: the smallest size, never zero
        self.assertEqual(self.ask("client 0 0 10 10 16 80 640 480"), [(1, 1)])

    def test_the_whole_window_stays_inside_the_area(self):
        areas = [(0, 0, 2560, 1528), (0, 0, 1280, 672), (-1920, 0, 1920, 1040), (2560, 40, 1920, 1000), (0, 0, 640, 400)]
        chromes = [(16, 80), (22, 86), (0, 0)]
        for ax, ay, aw, ah in areas:
            for cw, ch in chromes:
                n = self.ask(f"fit {ax} {ay} {aw} {ah} {cw} {ch}")[0][0]
                w, h = self.ask(f"client {ax} {ay} {aw} {ah} {cw} {ch} {640 * max(n, 1)} {480 * max(n, 1)}")[0]
                x, y = self.ask(f"origin {ax} {ay} {aw} {ah} {w + cw} {h + ch}")[0]
                with self.subTest(area=(ax, ay, aw, ah), chrome=(cw, ch)):
                    self.assertTrue(ax <= x and x + w + cw <= ax + aw, (x, w + cw))
                    self.assertTrue(ay <= y and y + h + ch <= ay + ah, (y, h + ch))
                    self.assertLessEqual(abs(w * 480 - h * 640), 640)  # the shape is kept (to a pixel of rounding)

    def test_a_window_larger_than_the_area_shows_its_title_bar(self):
        self.assertEqual(self.ask("origin 100 50 800 600 900 700"), [(100, 50)])

    def test_the_area_may_not_start_at_zero(self):
        self.assertEqual(self.ask("origin 2560 40 1920 1000 1000 800"), [(2560 + 460, 40 + 100)])


class HostWindowUsesIt(unittest.TestCase):
    """Reads the source: no frame, golden or headless run goes through host_window.cpp, and what it does is placed by
    host_place.h."""
    SRC = (KIT / "base" / "host_window.cpp").read_text(encoding="utf-8")

    def test_the_header_is_used_and_the_client_is_not_centred_alone(self):
        self.assertIn('#include "host_place.h"', self.SRC)
        for name in ("place_fit_scale", "place_client", "place_origin"):
            self.assertIn(name, self.SRC)
        self.assertNotIn("SDL_WINDOWPOS_CENTERED", self.SRC)  # that centres the client area, the chrome ends up outside

    def test_the_chrome_is_read_from_the_window(self):
        self.assertIn("GetWindowRect", self.SRC)
        self.assertIn("GetClientRect", self.SRC)
        self.assertIn("rcWork", self.SRC)  # the work area (without the taskbar) of the monitor the window is on

    def test_full_screen_is_not_placed(self):
        """The full screen window is never sized or moved here; only the windowed paths call place_window."""
        calls = self.SRC.count("place_window()")
        self.assertEqual(calls, 4)  # the definition and the three windowed paths


if __name__ == "__main__":
    unittest.main()
