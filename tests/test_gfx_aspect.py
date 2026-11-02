"""The frame's shape (port/base/present_aspect.c: --aspect, the Aspect menu) and where the 640x480 screen is in the draw
target (port/pvr/pvr_screen.c): the real modules, compiled at test time with MSVC and run on small cases.

Nearest existing: tests/test_pvr_blend.py - the same compile-the-kit-module-and-print harness; it covers pvr_blend.c, not the shape.
"""
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

TOOL = pathlib.Path(__file__).resolve().parent.parent
KIT = TOOL / "port"
sys.path.insert(0, str(TOOL / "tests"))
sys.path.insert(0, str(TOOL / "port" / "tools"))
import game_config  # noqa: E402
from test_lockstep import HAVE_VC, NO_VC  # noqa: E402

DRIVER_C = r"""#include <stdio.h>
#include <string.h>
#include "pvr_int.h"
#include "platform.h"
void port_log(const char *fmt, ...) { (void)fmt; }
GfxState g_gfx;
f32 port_widescreen(void) { return g_gfx.wide_k > 1.0f ? g_gfx.wide_k : 1.0f; }
f32 port_tallscreen(void) { return g_gfx.tall_k > 1.0f ? g_gfx.tall_k : 1.0f; }

static void parse(const char *t)
{
    f32 r = -1.0f;
    char buf[16];
    int ok = gfx_aspect_parse(t, &r);
    printf("parse %s: %d %.4f %s\n", t, ok, ok ? r : 0.0f, ok ? gfx_aspect_name(r, buf) : "-");
}

static void shape(u32 w, u32 h, const char *t)
{
    GfxFrameShape s;
    u32 r[4];
    f32 ratio = 0.0f;
    gfx_aspect_parse(t, &ratio);
    gfx_frame_shape(w, h, ratio, &s);
    gfx_screen_rect(s.w, s.h, s.wide_k, s.tall_k, r);
    printf("shape %ux%u %s: frame %ux%u at %u,%u wide %.4f tall %.4f screen %u,%u %ux%u\n", w, h, t, s.w, s.h, s.x, s.y,
           s.wide_k, s.tall_k, r[0], r[1], r[2], r[3]);
}

static void draw(f32 wide, f32 tall)
{
    g_gfx.draw_w = 2560;
    g_gfx.draw_h = 1600;
    g_gfx.wide_k = wide;
    g_gfx.tall_k = tall;
    printf("draw %.2f %.2f: x %ld %ld %ld %ld | y %ld %ld %ld %ld\n", wide, tall, (long)pvr_draw_x(0, 0),
           (long)pvr_draw_x(320, 0), (long)pvr_draw_x(640, 0), (long)pvr_draw_x(640, 1), (long)pvr_draw_y(0, 0),
           (long)pvr_draw_y(240, 0), (long)pvr_draw_y(480, 0), (long)pvr_draw_y(480, 1));
}

int main(void)
{
    const char *p[] = {"fit", "window", "4:3", "16:9", "21:9", "1.6", "16:10", "32:9", "3:4", "x", "0:3", "99:1", "4:", "4:3x", ""};
    for (unsigned i = 0; i < sizeof p / sizeof *p; i++) parse(p[i]);
    shape(2560, 1600, "fit");
    shape(2560, 1600, "4:3");
    shape(1920, 1440, "4:3");
    shape(2560, 1600, "16:9");
    shape(2560, 1600, "21:9");
    shape(2560, 1600, "3:4");
    shape(1000, 2000, "fit");
    shape(5120, 1440, "32:9");
    shape(1280, 720, "4:3");
    draw(1.0f, 1.0f);
    draw(1.2f, 1.0f);
    draw(1.0f, 1.5f);
    return 0;
}
"""


@unittest.skipUnless(HAVE_VC, NO_VC)
class GfxAspect(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        work = TOOL / "tests" / ".work"
        d = pathlib.Path(tempfile.mkdtemp(dir=work if work.is_dir() else None))
        cls.dir = d
        (d / "port_config.h").write_text(game_config.port_config_h(
            {"name": "Fixture", "entry": {"name": "Fx_Main", "addr": "8c011000"}, "image_base": "8c010000",
             "boot_file": "", "console": None, "id": "FIXTURE", "boot_sha256": []}), encoding="utf-8")
        (d / "aspect_main.c").write_text(DRIVER_C, encoding="utf-8")
        rd = TOOL / "targets" / "dreamcast_sh4" / "runtime_rd"
        srcs = " ".join(f'"{p}"' for p in [KIT / "base" / "present_aspect.c", KIT / "pvr" / "pvr_screen.c",
                                           d / "aspect_main.c"])
        cl = (f'cl /nologo /TC /std:c11 /W4 /WX /O2 /I"{d}" /I"{KIT / "runtime"}" /I"{KIT / "runtime" / "gfx"}" '
              f'/I"{KIT / "pvr"}" /I"{KIT / "base"}" /I"{rd}" {srcs} /Fe:aspect.exe')
        vc = str(game_config.vcvars32()).replace("/", "\\")
        r = subprocess.run(f'cmd /d /s /c ""{vc}" >nul 2>&1 && {cl}"', cwd=d, capture_output=True, text=True,
                           errors="replace", timeout=300)
        if r.returncode or "warning" in r.stdout.lower():
            shutil.rmtree(d, ignore_errors=True)
            raise RuntimeError(r.stdout[-3000:] + r.stderr[-1000:])
        run = subprocess.run([str(d / "aspect.exe")], capture_output=True, text=True, timeout=60)
        if run.returncode:
            raise RuntimeError(run.stdout + run.stderr)
        cls.out = run.stdout.splitlines()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def line(self, prefix):
        found = [ln for ln in self.out if ln.startswith(prefix)]
        self.assertEqual(len(found), 1, prefix)
        return found[0]

    def test_parse(self):
        self.assertEqual(self.line("parse fit:"), "parse fit: 1 0.0000 fit")
        self.assertEqual(self.line("parse window:"), "parse window: 1 0.0000 fit")
        self.assertEqual(self.line("parse 4:3:"), "parse 4:3: 1 1.3333 4:3")
        self.assertEqual(self.line("parse 16:9:"), "parse 16:9: 1 1.7778 16:9")
        self.assertEqual(self.line("parse 21:9:"), "parse 21:9: 1 2.3333 21:9")
        self.assertEqual(self.line("parse 1.6:"), "parse 1.6: 1 1.6000 1.6")
        self.assertEqual(self.line("parse 16:10:"), "parse 16:10: 1 1.6000 1.6")
        self.assertEqual(self.line("parse 32:9:"), "parse 32:9: 1 3.5556 3.556")
        self.assertEqual(self.line("parse 3:4:"), "parse 3:4: 1 0.7500 0.75")
        for bad in ("x", "0:3", "99:1", "4:", "4:3x"):
            self.assertTrue(self.line(f"parse {bad}:").startswith(f"parse {bad}: 0 "), bad)
        self.assertTrue(self.line("parse :").startswith("parse : 0 "))

    def test_fit_takes_the_output(self):
        self.assertEqual(self.line("shape 2560x1600 fit:"),
                         "shape 2560x1600 fit: frame 2560x1600 at 0,0 wide 1.2000 tall 1.0000 screen 213,0 2133x1600")
        # a tall window: more above and below, the screen across the full width
        self.assertEqual(self.line("shape 1000x2000 fit:"),
                         "shape 1000x2000 fit: frame 1000x2000 at 0,0 wide 1.0000 tall 2.6667 screen 0,625 1000x750")

    def test_original_shape_is_exact(self):
        # the checks and goldens: 4:3 is the output's own size, or the pillarboxed rectangle the kit always had
        self.assertEqual(self.line("shape 1920x1440 4:3:"),
                         "shape 1920x1440 4:3: frame 1920x1440 at 0,0 wide 1.0000 tall 1.0000 screen 0,0 1920x1440")
        self.assertEqual(self.line("shape 2560x1600 4:3:"),
                         "shape 2560x1600 4:3: frame 2133x1600 at 213,0 wide 1.0000 tall 1.0000 screen 0,0 2133x1600")
        self.assertEqual(self.line("shape 1280x720 4:3:"),
                         "shape 1280x720 4:3: frame 960x720 at 160,0 wide 1.0000 tall 1.0000 screen 0,0 960x720")

    def test_fixed_shapes_keep_their_own_shape_with_bars(self):
        self.assertEqual(self.line("shape 2560x1600 16:9:"),
                         "shape 2560x1600 16:9: frame 2560x1440 at 0,80 wide 1.3333 tall 1.0000 screen 320,0 1920x1440")
        self.assertEqual(self.line("shape 2560x1600 21:9:"),
                         "shape 2560x1600 21:9: frame 2560x1097 at 0,251 wide 1.7500 tall 1.0000 screen 548,0 1463x1097")
        self.assertEqual(self.line("shape 5120x1440 32:9:"),
                         "shape 5120x1440 32:9: frame 5120x1440 at 0,0 wide 2.6667 tall 1.0000 screen 1600,0 1920x1440")

    def test_narrower_than_4_3_shows_more_above_and_below(self):
        self.assertEqual(self.line("shape 2560x1600 3:4:"),
                         "shape 2560x1600 3:4: frame 1200x1600 at 680,0 wide 1.0000 tall 1.7778 screen 0,350 1200x900")

    def test_screen_maps_into_the_middle_of_the_target(self):
        self.assertEqual(self.line("draw 1.00 1.00:"), "draw 1.00 1.00: x 0 1280 2560 2560 | y 0 800 1600 1600")
        # wide: the clip across the screen spans the target, a clip at the screen's edge does not
        self.assertEqual(self.line("draw 1.20 1.00:"), "draw 1.20 1.00: x 213 1280 2347 2560 | y 0 800 1600 1600")
        self.assertEqual(self.line("draw 1.00 1.50:"), "draw 1.00 1.50: x 0 1280 2560 2560 | y 267 800 1333 1600")


if __name__ == "__main__":
    unittest.main()
