"""The Saturn kit's frame cost (port/saturn/sat_perf.c): the real file compiled at /W4 /WX with a driver that plays
frames on the real clock the way Dragon Force's map does: the game polls a flag with no guest write, and the vertical
blank's kit work (sound, compose) runs inside that poll's gap.

1. the game tick is never negative: a part that runs inside a poll's gap is not also counted as waiting. Before the fix
   the gap was all waiting and the part was then taken away again: "game tick median -1.00 ms" on the main laptop's
   Dragon Force map run (2026-11-19).
2. the time the game spends polling is counted as waiting, and the tick work stays near what the driver really did.
3. the per-tick trace (base/perf.h perf_tick) gets the parts in the order named, with work = game + the kit's parts.
"""
import pathlib
import re
import shutil
import sys
import tempfile
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[1]
KIT = TOOL / "port"
sys.path.insert(0, str(TOOL / "tests"))
sys.path.insert(0, str(KIT / "tools"))

from test_kit_pad import compile_run  # noqa: E402
from test_lockstep import HAVE_VC, NO_VC  # noqa: E402

DRIVER_C = r"""#pragma comment(lib, "winmm.lib")
#pragma comment(linker, "/alternatename:sat_perf_cpu=drv_no_cpu") /* a sat_perf.c from before them (x64, x86 names) */
#pragma comment(linker, "/alternatename:_sat_perf_cpu=_drv_no_cpu")
#pragma comment(linker, "/alternatename:sat_perf_flag=drv_no_cpu")
#pragma comment(linker, "/alternatename:_sat_perf_flag=_drv_no_cpu")
#include <stdarg.h>
#include <stdio.h>
#include <windows.h>
#include "sat.h"
#include "sat_cpu.h"
#include "sat_sys.h"
#include "play_stats.h"
#include "perf.h"
void sat_perf_vblank(void);
void sat_perf_poll(void);
void sat_perf_audio(long long ticks);
void sat_perf_compose(LONGLONG ticks);
void sat_perf_wait_begin(void);
void sat_perf_wait_end(void);
void sat_perf_report(void);
SatCpu g_cpu[2];
void sat_perf_cpu(int id);
void sat_perf_flag(int waiting);
void drv_no_cpu(int id) { (void)id; }
int sat_cpu_id(void) { return 0; }
u32 sat_sys_hz(void) { return 60u; }
int g_play_stats = 1;
u32 sat_interp(u32 entry, u32 top, const TcArgs *a, int stack_args) { (void)entry, (void)top, (void)a, (void)stack_args; return 0u; }
static double g_ticks_ms, g_ticks_n;
void play_stats_tick_slow(double ms) { g_ticks_ms += ms, g_ticks_n++; }
int g_perf_frames = 1, g_perf_on;
u32 g_perf_restarts;
static u32 g_rows, g_bad_sum;
void perf_tick(const u32 *us)
{
    u32 parts = us[1] + us[4] + us[5] + us[6] + us[7]; /* game + VDP2 + VDP1 + audio + show = work */
    g_rows++;
    if (parts > us[0] + 20u || parts + 20u < us[0]) g_bad_sum++;
}
void perf_sections(int (*now)(void), const char *const *names, int count) { (void)now, (void)names, (void)count; }
void perf_stack_source(u32 (*copy)(u32 *out, u32 max)) { (void)copy; }
void perf_parts(const char *const *names, int count) { (void)names, (void)count; }
void port_log(const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    vprintf(fmt, ap);
    va_end(ap);
}
static LONGLONG qpc(void)
{
    LARGE_INTEGER t;
    QueryPerformanceCounter(&t);
    return t.QuadPart;
}
static void spin_us(LONGLONG us)
{
    LARGE_INTEGER f;
    LONGLONG a = qpc();
    QueryPerformanceFrequency(&f);
    while ((qpc() - a) * 1000000 < us * f.QuadPart) {
    }
}
/* Dragon Force's master waiting for its slave: it reads FTCSR (ICF clear) in a loop that calls a function (a write of its
 * own each pass), a poll every 200 us, each giving the slave a short turn that writes too */
static void polls(int n)
{
    for (int i = 0; i < n; i++)
        spin_us(200), sat_wr_n += 2u, sat_perf_flag(1), sat_perf_poll(), sat_perf_cpu(1), sat_wr_n += 3u, sat_perf_cpu(0);
}
int main(void)
{
    for (int f = 0; f < 120; f++) {
        LONGLONG a;
        sat_perf_vblank();
        sat_wr_n += 40u, spin_us(1000); /* the game's own work: 1 ms, writing */
        sat_perf_poll();
        polls(5);                                                           /* 1 ms of waiting on a flag */
        a = qpc(), spin_us(1000), sat_perf_audio(qpc() - a);                /* the kit's sound, inside the poll's gap */
        a = qpc(), spin_us(500), sat_perf_compose(qpc() - a);               /* and its compose */
        polls(5);                                                           /* 1 ms more of waiting */
        sat_perf_flag(0);                                                   /* ICF set: the slave signalled */
        sat_perf_wait_begin(), spin_us(1000), sat_perf_wait_end();          /* the pacing wait for the blank */
    }
    sat_perf_vblank();
    sat_perf_report();
    printf("driver: ticks %.0f mean work %.3f ms; trace rows %u, %u with parts not adding up to work\n", g_ticks_n,
           g_ticks_n ? g_ticks_ms / g_ticks_n : 0.0, g_rows, g_bad_sum);
    return 0;
}
"""


@unittest.skipUnless(HAVE_VC, NO_VC)
class SatPerfTicks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = d = pathlib.Path(tempfile.mkdtemp())
        (d / "drv.c").write_text(DRIVER_C, encoding="utf-8")
        inc = "/D_CRT_SECURE_NO_WARNINGS " + " ".join(f'/I"{p}"' for p in (KIT / "saturn", KIT / "base", TOOL / "targets" / "saturn_sh2" / "runtime"))
        cls.build, (cls.out,) = compile_run(d, "drv", f'{inc} "{d / "drv.c"}" "{KIT / "saturn" / "sat_perf.c"}"', [[]])
        cls.text = "\n".join(cls.out)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def test_builds(self):
        self.assertEqual(self.build, "")

    def test_tick_not_negative(self):
        m = re.search(r"perf: game tick\s+median\s+(-?[\d.]+) ms", self.text)
        self.assertTrue(m, self.text[-2000:])
        self.assertGreaterEqual(float(m.group(1)), 0.5, self.text[-1500:])  # the game's own 1 ms, give or take the clock

    def test_work_leaves_out_the_waits(self):
        m = re.search(r"perf: frame \(all work\)\s+median\s+([\d.]+) ms", self.text)
        self.assertTrue(m, self.text[-2000:])
        self.assertGreater(float(m.group(1)), 2.2)  # 1 ms game + 1.5 ms parts (+ the polls' own few us)
        self.assertLess(float(m.group(1)), 3.2)  # the 2 ms of polling and the 1 ms pacing wait are left out
        w = re.search(r"waiting ([\d.]+)% \(pacing to the blank ([\d.]+)%, the game polling a flag ([\d.]+)%\)", self.text)
        self.assertTrue(w, self.text[-1500:])
        self.assertGreater(float(w.group(3)), 25.0)  # 2 of ~5.5 ms

    def test_trace_rows_add_up(self):
        m = re.search(r"trace rows (\d+), (\d+) with parts not adding up", self.text)
        self.assertTrue(m, self.text[-800:])
        self.assertEqual(int(m.group(1)), 120)
        self.assertEqual(int(m.group(2)), 0)


if __name__ == "__main__":
    unittest.main()
