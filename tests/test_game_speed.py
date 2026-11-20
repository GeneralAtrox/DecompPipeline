"""Fast forward (port/base/speed.c, its button in port/base/input_bind.c, the Saturn kit's clock in port/saturn/sat_sys.c):
the base's code compiled at /W4 /WX with stub host calls, then run.
1. Never pressed, nothing changes: port_speed() is 1 and the machine's clock is the real clock exactly.
2. Tab or the controller's button (R3 until the player chooses, Input > Controls > Fast forward, bind-fast-forward) held
   gives the chosen speed (3x by default; 2x, 4x, as fast as it goes: the base's option fast-forward, game_options.c);
   the toggle option turns it on with one press and off with the next; --fast-forward-test holds it for a window of blanks.
3. Never in a run that may not have it: a console without PORT_CONSOLE_SPEED, the fixed clock, a replay, or the console's
   own refusal (online play, --det, --fast).
4. The machine's clock under it: N times the real time while on, continuous across the changes, 1:1 again after.
5. The Saturn kit runs its blanks, timer, raster and sound on that clock and drops its sound cleanly while fast.
Only where Visual Studio's 32-bit tools are found (tests/test_lockstep.py NO_VC says how), skipped elsewhere.

Nearest existing: tests/test_kit_pad.py - the same compile-and-run harness for input_bind.c; it covers the console's
actions, not the base's own fast forward.

usage: python -W error tests/test_game_speed.py
"""
import pathlib
import shutil
import sys
import tempfile
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[1]
KIT = TOOL / "port"
sys.path.insert(0, str(TOOL / "tests"))

from test_lockstep import HAVE_VC, NO_VC  # noqa: E402
from test_kit_pad import compile_run  # noqa: E402

DRIVER_C = r"""#define _CRT_SECURE_NO_WARNINGS
#include "base.h"
#include "console.h"
#include "host.h"
#include "session.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static u32 g_vb;
static int g_fixed, g_replay;
u32 port_vblank_count(void) { return g_vb; }
static const PortAction k_actions[] = {{"A", "A"}, {"B", "B"}, {"C", "RB"}};
const PortAction *console_actions(unsigned *count) { *count = 3; return k_actions; }
void port_log(const char *fmt, ...) { (void)fmt; }
void game_log_line(const char *fmt, ...) { (void)fmt; }
int host_fixed_clock(void) { return g_fixed; }
int session_replaying(void) { return g_replay; }
u64 host_ticks(u32 hz) { return (u64)g_vb * hz / 60u; }
const PortConsole port_console = {"t", "T", "T", "1", 320u, 224u, 60u, SPEED_FLAGS};
int session_note_replayed(const char *name, void *buf, u32 size) { (void)name, (void)buf, (void)size; return 0; }
void session_save_read(const char *name, const void *buf, u32 size, int got) { (void)name, (void)buf, (void)size, (void)got; }

static void pads(const char *held, int player)
{
    HostPad pad[HOST_PADS];
    memset(pad, 0, sizeof pad);
    if (*held) pad[player].buttons = input_name_bit(held);
    port_speed_pads(pad, 4);
}

int main(int argc, char **argv)
{
    PortSpeedClock c = {0, 0};
    s64 t = 1000, m = 0;
    int same = 1;
    u32 s;
    (void)argc;
    if (!strcmp(argv[1], "fixed")) g_fixed = 1;
    if (!strcmp(argv[1], "replay")) g_replay = 1;
    port_speed_allow(strcmp(argv[1], "refused") != 0);
    printf("idle %u\n", port_speed());
    port_speed_key(1);
    s = port_speed(); /* first: the label follows it */
    printf("tab %u label %s\n", s, port_speed_label());
    port_speed_key(0);
    s = port_speed();
    printf("tab_up %u label [%s]\n", s, port_speed_label());
    pads("R3", 1);
    printf("r3_p2 %u\n", port_speed());
    pads("", 0);
    printf("r3_up %u\n", port_speed());
    pads("A", 0);
    printf("a %u\n", port_speed());
    pads("", 0);
    input_bind_option("--bind-fast-forward", "back");
    pads("R3", 0);
    printf("rebound_r3 %u\n", port_speed());
    pads("BACK", 0);
    printf("rebound_back %u button %s\n", port_speed(), port_speed_button());
    pads("", 0);
    port_options_option("--fast-forward", "4x");
    port_speed_key(1);
    printf("4x %u\n", port_speed());
    port_options_option("--fast-forward", "max");
    printf("max %d\n", port_speed() == PORT_SPEED_FREE);
    port_speed_key(0);
    port_options_option("--fast-forward", "2x");
    port_options_option("--fast-forward-toggle", "on");
    port_speed_key(1), port_speed_key(0);
    printf("toggle_on %u\n", port_speed());
    port_speed_key(1), port_speed_key(0);
    printf("toggle_off %u\n", port_speed());
    port_options_option("--fast-forward-toggle", "off");
    port_speed_option("--fast-forward-test", "100+50");
    g_vb = 99, printf("test_before %u\n", port_speed());
    g_vb = 100, printf("test_in %u\n", port_speed());
    g_vb = 150, printf("test_after %u\n", port_speed());
    /* the console's own assignments are as before */
    printf("bind_a %d\n", input_bind_option("--bind-A", "lb"));
    {
        char text[64];
        printf("bind_a_text %s\n", input_bind_text(0, text, sizeof text));
    }
    /* the machine's clock: speed 1 is the real clock exactly */
    for (int i = 0; i < 100; i++, t += 7) same &= port_speed_clock(&c, t, 1u) == t;
    printf("clock_one %d\n", same);
    m = port_speed_clock(&c, t, 1u); /* the switch: the kit reads the time before it at the old speed (sat_sys.c) */
    for (int i = 0; i < 10; i++) t += 10, m = port_speed_clock(&c, t, 3u);
    printf("clock_three %lld %lld\n", (long long)(m - t), (long long)c.ahead);
    t += 5, m = port_speed_clock(&c, t, 1u);
    printf("clock_back %lld\n", (long long)(m - t));
    t += 5, m = port_speed_clock(&c, t, PORT_SPEED_FREE);
    printf("clock_free %lld\n", (long long)(m - t));
    return 0;
}
"""


def run(d, name, flags, args):
    (d / f"{name}.c").write_text(DRIVER_C, encoding="utf-8")
    srcs = " ".join(f'"{p}"' for p in [d / f"{name}.c", KIT / "base" / "speed.c", KIT / "base" / "game_options.c", KIT / "base" / "input_bind.c",
                                         KIT / "base" / "input_script.c", KIT / "base" / "pad_twin.c"])
    build, outs = compile_run(d, name, f'/DSPEED_FLAGS={flags} /I"{KIT / "base"}" {srcs}', [[a] for a in args])
    return build, [dict(line.split(" ", 1) for line in out if " " in line) for out in outs]


@unittest.skipUnless(HAVE_VC, NO_VC)
class GameSpeed(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = d = pathlib.Path(tempfile.mkdtemp())
        cls.build, (cls.play, cls.fixed, cls.replay, cls.refused) = run(d, "on", "PORT_CONSOLE_SPEED", ["play", "fixed", "replay", "refused"])
        cls.build_off, (cls.none,) = run(d, "off", 2, ["play"])

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def test_built(self):
        self.assertEqual(self.build, "")
        self.assertEqual(self.build_off, "")

    def test_never_pressed_is_normal_speed(self):
        self.assertEqual(self.play["idle"], "1")
        self.assertEqual(self.play["clock_one"], "1")  # the machine's clock is the real clock exactly

    def test_tab_held_is_3x_by_default(self):
        self.assertEqual(self.play["tab"], "3 label Fast forward 3x")
        self.assertEqual(self.play["tab_up"], "1 label []")

    def test_r3_on_any_pad_and_rebinding(self):
        p = self.play
        self.assertEqual((p["r3_p2"], p["r3_up"], p["a"]), ("3", "1", "1"))
        self.assertEqual(p["rebound_r3"], "1")  # moved to Back: R3 does nothing now
        self.assertEqual(p["rebound_back"], "3 button BACK")

    def test_speeds_and_toggle(self):
        p = self.play
        self.assertEqual((p["4x"], p["max"]), ("4", "1"))
        self.assertEqual((p["toggle_on"], p["toggle_off"]), ("2", "1"))  # 2x, toggled by one press, off by the next

    def test_test_window(self):
        p = self.play
        self.assertEqual((p["test_before"], p["test_in"], p["test_after"]), ("1", "2", "1"))

    def test_console_actions_unchanged(self):
        self.assertEqual((self.play["bind_a"], self.play["bind_a_text"]), ("2", "LB"))

    def test_never_in_a_check_a_replay_or_a_refusing_run(self):
        for name, out in (("fixed clock", self.fixed), ("replay", self.replay), ("refused", self.refused), ("no flag", self.none)):
            with self.subTest(name):
                for key in ("idle", "tab", "r3_p2", "rebound_back", "4x", "toggle_on", "test_in"):
                    self.assertEqual(out[key].split()[0], "1", (key, out[key]))

    def test_machine_clock(self):
        # 10 steps of 10 at 3x: 200 ahead; back at 1x and as fast as it goes (which jumps in the kit) it stays 200 ahead
        self.assertEqual(self.play["clock_three"], "200 200")
        self.assertEqual(self.play["clock_back"], "200")
        self.assertEqual(self.play["clock_free"], "200")


class SaturnKit(unittest.TestCase):
    """The Saturn kit's side, from its source: it offers fast forward, keeps it out of checks and online play, and runs
    everything that measures time on the machine's clock."""

    def text(self, name):
        return (KIT / "saturn" / name).read_text(encoding="utf-8")

    def test_offered_and_allowed_only_on_the_real_clock(self):
        self.assertIn("PORT_CONSOLE_SPEED", self.text("console_sat.c"))
        self.assertIn("port_speed_allow(!run.fast && !host_fixed_clock() && !run.det && !sat_net_on())", self.text("sat_main.c"))

    def test_clock_blanks_and_sound_follow_the_machine(self):
        sys_c = self.text("sat_sys.c")
        self.assertIn("return port_speed_clock(&g_clk, now.QuadPart, s);", sys_c)
        self.assertIn("/ g_freq.QuadPart / g_speed", sys_c)  # the wait is a speed's part of the machine's
        self.assertIn("LONGLONG at = sat_sys_now();", self.text("sat_scsp.c"))  # a blank keeps its 1/60 s of sound
        self.assertIn("sat_sys_speed() == 1u", self.text("sat_inb.c"))  # no in-between frames while fast

    def test_as_fast_as_it_goes_skips_the_game_s_waits(self):
        # a flag wait jumps the clock to the blank it waits for, as sat_spin's wait does; at 1x..4x it is sat_irq_poll
        call = self.text("sat_call.c")
        self.assertEqual(call.count("sat_wait_poll();"), 2)  # sat_poll_hint and sat_busy_poll
        self.assertIn("port_speed() == PORT_SPEED_FREE) {", self.text("sat_sys.c"))
        self.assertIn("g_clk.ahead += g_next.QuadPart - now;", self.text("sat_sys.c"))

    def test_sound_dropped_cleanly(self):
        self.assertIn("sat_audio_fade(faded, stereo, frames, 0)", self.text("sat_audio.c"))
        self.assertIn("sat_audio_fade(fade, (const s16 *)raw, SAT_SECTOR / 4u, 1)", self.text("sat_cdda.c"))


if __name__ == "__main__":
    unittest.main()
