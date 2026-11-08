"""The play session's summary line (port/base/play_stats.c): the real file compiled at /W4 /WX with a driver that plays
frames on the real clock, the hooks the kit calls (wait, draw, shown) and the exit report.
1. play_stats_percentile reads the histogram: the bucket's upper edge holding rank p (median, 99th, an empty one is 0);
2. forced on, 700 frames of a 300 us draw inside a 1 ms tick: the lines parse with tools/scorecard_runtime.parse_summary
   (the gate reads them), 700 frames, the draw median is about 0.3 ms, and a draw nested in a draw (pvr_render inside an
   in-between frame's draw) counts once; a frame's make time is the work of its tick plus its draws after the tick, a
   render inside the tick counted once; a 40 ms gap is 5 missed slots of 144 and fails the verdict, and after 1 MB from
   the disc it is a loading screen's, counted apart;
3. off in a check run: the fixed clock, a replay, a headless window each print no line; --play-summary on forces it and
   off forces it away; a run of fewer than two shown frames prints none.
Only where Visual Studio's 32-bit tools are found (tests/test_lockstep.py NO_VC says how), skipped elsewhere.

usage: python -W error tests/test_play_stats.py
"""
import os
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
sys.path.insert(0, str(TOOL / "tools"))

import game_config  # noqa: E402
import scorecard_runtime as sr  # noqa: E402
from test_lockstep import HAVE_VC, NO_VC  # noqa: E402

DRIVER_C = r"""#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <windows.h>
#include "platform.h"
#include "session.h"
#include "play_stats.h"
static PortView g_view; /* the console's view (base/console.h) */
void console_view(PortView *v) { *v = g_view; }
static int g_fixed, g_replay, g_gap, g_slow, g_tick, g_intick, g_disc;
const PortConsole port_console = {"test", "Test", "Fixture", "0", 640u, 480u, 60u, 0u};
u32 port_vblank_count(void) { return 7u; }
int host_fixed_clock(void) { return g_fixed; }
int session_replaying(void) { return g_replay; }
void port_log(const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    vprintf(fmt, ap);
    va_end(ap);
}
static void spin_us(u64 us)
{
    LARGE_INTEGER f, a, b;
    QueryPerformanceFrequency(&f);
    QueryPerformanceCounter(&a);
    do QueryPerformanceCounter(&b); while ((u64)(b.QuadPart - a.QuadPart) * 1000000u < us * (u64)f.QuadPart);
}
static void draws(void)
{
    play_stats_draw(1);
    play_stats_draw(1); /* pvr_render inside an in-between draw */
    spin_us(150);
    play_stats_draw(0);
    spin_us(150);
    play_stats_draw(0);
}
static void frames(int n)
{
    for (int i = 0; i < n; i++) {
        if (g_tick) spin_us(2000); /* the game's work in the tick */
        if (g_intick) draws();     /* its render, inside the tick */
        play_stats_wait(1);
        if (!g_intick) draws();    /* in-between draws, in the wait */
        play_stats_shown();
        if (i == 5 && g_disc) play_stats_disc(1u << 20); /* a load: 1 MB from the disc */
        spin_us(i == 5 && g_gap ? 40000u : 550u); /* gap: one tick of 40 ms, over 2 blanks of 60 Hz */
        play_stats_wait(0);
    }
}
int main(int argc, char **argv)
{
    u32 h[PS_BUCKETS];
    int n = 700, i;
    memset(h, 0, sizeof h);
    h[9] = 98;  /* 0.10 ms */
    h[19] = 1;  /* 0.20 ms */
    h[99] = 1;  /* 1.00 ms */
    printf("pct %.2f %.2f %.2f %.2f\n", play_stats_percentile(h, 100, 0.5), play_stats_percentile(h, 100, 0.99),
           play_stats_percentile(h, 100, 1.0), play_stats_percentile(h, 0, 0.5));
    for (i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "fixed")) g_fixed = 1;
        else if (!strcmp(argv[i], "replay")) g_replay = 1;
        else if (!strcmp(argv[i], "headless")) g_view.headless = 1;
        else if (!strcmp(argv[i], "one")) n = 1;
        else if (!strcmp(argv[i], "gap")) g_gap = 1;
        else if (!strcmp(argv[i], "slow")) g_slow = 1;
        else if (!strcmp(argv[i], "tick")) g_tick = 1;
        else if (!strcmp(argv[i], "intick")) g_intick = 1;
        else if (!strcmp(argv[i], "disc")) g_disc = 1;
        else play_stats_option("--play-summary", argv[i]);
    }
    if (g_slow) { /* measured ticks, as the Saturn reports them: no clock, so the lines are exact */
        play_stats_tick(33.0); /* just under two blanks of 60 Hz: no line */
        play_stats_tick(40.0); /* over: a line */
        play_stats_tick(50.0); /* within the same second: counted into the next line */
        return 0;
    }
    g_view.out_w = 2560;
    g_view.out_h = 1600;
    frames(n);
    play_stats_report();
    return 0;
}
"""


@unittest.skipUnless(HAVE_VC, NO_VC)
class PlayStats(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        work = TOOL / "tests" / ".work"
        d = pathlib.Path(tempfile.mkdtemp(dir=work if work.is_dir() else None))
        cls.dir = d
        (d / "port_config.h").write_text(game_config.port_config_h(
            {"name": "Fixture", "entry": {"name": "Fx_Main", "addr": "8c011000"}, "image_base": "8c010000",
             "boot_file": "", "console": None, "id": "FIXTURE", "boot_sha256": []}), encoding="utf-8")
        (d / "stats_main.c").write_text(DRIVER_C, encoding="utf-8")
        rd = TOOL / "targets" / "dreamcast_sh4" / "runtime_rd"
        srcs = " ".join(f'"{p}"' for p in [KIT / "base" / "play_stats.c", d / "stats_main.c"])
        cl = (f'cl /nologo /TC /std:c11 /W4 /WX /O2 /I"{d}" /I"{KIT / "runtime"}" /I"{KIT / "pvr"}" '
              f'/I"{KIT / "base"}" /I"{rd}" {srcs} /Fe:stats.exe')
        vc = str(game_config.vcvars32()).replace("/", "\\")
        r = subprocess.run(f'cmd /d /s /c ""{vc}" >nul 2>&1 && {cl}"', cwd=d, capture_output=True, text=True,
                           errors="replace", timeout=300)
        if r.returncode or "warning" in r.stdout.lower():
            shutil.rmtree(d, ignore_errors=True)
            raise RuntimeError(r.stdout[-3000:] + r.stderr[-1000:])

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def run_driver(self, *args):
        r = subprocess.run([str(self.dir / "stats.exe"), *args], capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.out = r.stdout
        lines = r.stdout.splitlines()
        return lines[0], [ln for ln in lines[1:] if ln.startswith("play summary:")], [
            ln for ln in lines[1:] if ln.startswith("tick gap:")]

    def summary(self, *args):
        self.run_driver(*args)
        return sr.parse_summary(self.out)

    def test_percentile_is_the_buckets_upper_edge(self):
        pct, _, _ = self.run_driver("off")
        self.assertEqual(pct, "pct 0.10 0.20 1.00 0.00")

    def test_a_play_run_writes_a_line_the_gate_reads(self):
        _, line, _ = self.run_driver()
        self.assertEqual(len(line), 1)
        s = sr.parse_summary(line[0] + "\n")
        self.assertEqual((s["w"], s["h"], s["frames"]), (2560, 1600, 700))
        self.assertGreater(s["fps"], 100.0)
        self.assertLess(s["fps"], 1500.0)
        for name in ("tick_ms", "draw_ms", "frame_ms", "make_ms"):
            median, p99, worst = s[name]
            self.assertLessEqual(median, p99, name)
            self.assertLessEqual(p99 - 0.01, worst + 0.01, name)
        self.assertGreater(s["draw_ms"][0], 0.25)  # 300 us of spin, counted once: not 450
        self.assertLess(s["draw_ms"][0], 0.45)
        s = self.summary()
        self.assertIn(s["verdict"], ("pass", "fail"))
        self.assertGreater(s["make_ms"][0], 0.25)  # no tick work: the draws after it, counted once
        self.assertLess(s["make_ms"][0], 0.45)
        self.assertEqual(s["judged"], 700)
        self.assertEqual(s["machine"].lower(), os.environ.get("COMPUTERNAME", s["machine"]).lower())  # where it ran

    def test_make_is_the_tick_plus_its_draw(self):
        s = self.summary("tick")
        self.assertGreater(s["make_ms"][0], 2.2)  # 2 ms of tick, then 0.3 ms of draw before it is shown
        self.assertLess(s["make_ms"][0], 2.7)
        s = self.summary("intick")
        self.assertGreater(s["make_ms"][0], 0.25)  # a render inside the tick is in the tick's work: 0.3, not 0.6
        self.assertLess(s["make_ms"][0], 0.45)

    def test_missed_slots_and_loading_screens(self):
        s = self.summary("gap")
        self.assertGreaterEqual(s["late"], 5)  # 40 ms spans 6 slots of 144: 5 missed
        self.assertEqual(s["verdict"], "fail")
        s = self.summary("gap", "disc")
        self.assertGreaterEqual(s["late_loading"], 5)  # after 1 MB from the disc the gap is a loading screen's
        self.assertGreater(s["shown_loading"], 0)
        self.assertEqual(s["judged"] + s["shown_loading"], 700)

    def test_check_runs_write_nothing(self):
        for flag in ("fixed", "replay", "headless"):
            self.assertEqual(self.run_driver(flag)[1], [], flag)
            self.assertNotIn("play verdict", self.out, flag)
        self.assertEqual(len(self.run_driver("fixed", "on")[1]), 1)
        self.assertEqual(self.run_driver("off")[1], [])
        self.assertEqual(self.run_driver("one", "on")[1], [])  # fewer than two shown frames

    def test_a_tick_over_two_blanks_is_logged(self):
        _, _, gaps = self.run_driver("slow")
        self.assertEqual(gaps, ["tick gap: 40.0 ms at vertical blank 7, over 2 blanks (0 more since the last line)"])
        # through the waits: the 40 ms tick is logged; a busy machine may stall another tick too, so at least one line
        _, _, gaps = self.run_driver("gap")
        self.assertTrue(gaps, "no line for a 40 ms tick between waits")
        self.assertTrue(any(float(g.split()[2]) >= 40.0 for g in gaps), gaps)

    def test_session_summary_text_matches_the_gate_format(self):
        _, line, _ = self.run_driver()
        self.assertRegex(line[0], r"^play summary: shown_fps [\d.]+ output 2560x1600 frames 700 tick_ms [\d.]+/[\d.]+/[\d.]+ "
                                  r"draw_ms [\d.]+/[\d.]+/[\d.]+ frame_ms [\d.]+/[\d.]+/[\d.]+ make_ms [\d.]+/[\d.]+/[\d.]+$")
        self.assertRegex(self.out, sr.VERDICT)


if __name__ == "__main__":
    unittest.main()
