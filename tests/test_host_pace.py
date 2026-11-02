"""The real-clock pacing (port/base/pace.h) and the inline read's spin-word filter (port/runtime/tx/tx_spin.c): the real
code, compiled at test time with MSVC and run on small cases.

Nearest existing: tests/test_gfx_aspect.py - the same compile-the-kit-module-and-print harness; it covers the frame's shape.
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
#include "platform.h"
#include "pace.h"
#include "port.h"
extern u32 port_tx_spin_bits, port_tx_spin_mask;
void port_log(const char *fmt, ...) { (void)fmt; }
void port_wait_vblank(void) {}
u32 port_vblank_count(void) { return 0; }
void *port_unused;

/* Ticks of `work` tenths of a slot each, from now 0: the blanks shown over 600 slots. */
static int blanks(const int *work, int count, int catch_up)
{
    unsigned long long due = 0, now = 0; /* in tenths of a slot */
    int shown = 0;
    while (now < 6000) {
        unsigned long long next = pace_next(&due, now / 10, catch_up) * 10;
        if (now < next) now = next;
        now += work[shown % count];
        shown++;
    }
    return shown;
}

static int goes_out(u32 a) { return !((a ^ port_tx_spin_bits) & port_tx_spin_mask); }

int main(void)
{
    static const int quick[] = {5}, over[] = {11}, mixed[] = {5, 5, 5, 12}, slow[] = {25};
    unsigned long long due = 100;
    printf("quick %d %d\n", blanks(quick, 1, 1), blanks(quick, 1, 0));
    printf("over %d %d\n", blanks(over, 1, 1), blanks(over, 1, 0));
    printf("mixed %d %d\n", blanks(mixed, 4, 1), blanks(mixed, 4, 0));
    printf("slow %d %d\n", blanks(slow, 1, 1), blanks(slow, 1, 0));
    printf("late %llu\n", pace_next(&due, 104, 1));
    due = 100;
    printf("stall %llu\n", pace_next(&due, 106, 1));
    printf("none %d %d\n", goes_out(0x8c000000u), goes_out(0xffffffffu));
    port_spin_word(0x8c123450u);
    printf("one %d %d %d %d\n", goes_out(0x8c123450u), goes_out(0xac123450u), goes_out(0x0c123454u), goes_out(0x8c000000u));
    port_spin_word(0x8c123460u);
    printf("two %d %d %d\n", goes_out(0x8c123450u), goes_out(0x8c123460u), goes_out(0x8d000000u));
    return 0;
}
"""


@unittest.skipUnless(HAVE_VC, NO_VC)
class HostPaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = pathlib.Path(tempfile.mkdtemp())
        (cls.tmp / "driver.c").write_text(DRIVER_C, encoding="utf-8")
        (cls.tmp / "port_config.h").write_text(game_config.port_config_h(
            {"name": "Fixture", "entry": {"name": "Fx_Main", "addr": "8c011000"}, "image_base": "8c010000",
             "boot_file": "", "console": None, "id": "FIXTURE", "boot_sha256": []}), encoding="utf-8")
        rd = TOOL / "targets" / "dreamcast_sh4" / "runtime_rd"
        inc = " ".join(f'/I"{p}"' for p in (cls.tmp, KIT / "runtime", KIT / "runtime" / "tx", KIT / "base", rd))
        cl = (f'cl /nologo /W3 /O1 /MT /D_CRT_SECURE_NO_WARNINGS {inc} driver.c '
              f'"{KIT / "runtime" / "tx" / "tx_spin.c"}" /Fet.exe')
        vc = str(game_config.vcvars32()).replace("/", "\\")
        built = subprocess.run(f'cmd /d /s /c ""{vc}" >nul 2>&1 && {cl}"', cwd=cls.tmp, capture_output=True, text=True,
                               timeout=300)
        assert built.returncode == 0, built.stdout + built.stderr
        r = subprocess.run([str(cls.tmp / "t.exe")], capture_output=True, text=True, timeout=30)
        cls.out = dict(line.split(" ", 1) for line in r.stdout.splitlines())

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_a_quick_game_keeps_the_rate_either_way(self):
        self.assertEqual(self.out["quick"], "600 600")

    def test_one_late_tick_in_four_is_repaid(self):
        """5, 5, 5, 12 tenths average under a slot: the catch-up holds the rate, the old wait loses a slot each time."""
        on, off = map(int, self.out["mixed"].split())
        self.assertGreaterEqual(on, 598)
        self.assertEqual(off, 480)

    def test_always_over_runs_as_fast_as_it_can(self):
        """1.1 slots each: nearly 1/1.1 of the rate (545; the debt's limit costs one wait each 60 ticks), not the old
        wait's half."""
        on, off = map(int, self.out["over"].split())
        self.assertGreaterEqual(on, 530)
        self.assertEqual(off, 300)

    def test_a_small_debt_is_kept_and_a_stall_is_dropped(self):
        self.assertEqual(self.out["late"], "101")
        self.assertEqual(self.out["stall"], "107")

    def test_the_read_filter(self):
        self.assertEqual(self.out["none"], "0 1")
        self.assertEqual(self.out["one"], "1 1 0 0")  # the word in any mirror, nothing else
        self.assertEqual(self.out["two"], "1 1 0")


if __name__ == "__main__":
    unittest.main()
