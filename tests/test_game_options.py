"""Per-game options (port/base/game_options.c, console.h PortOption): a game's table, compiled at /W4 /WX with stub host
calls, then run in each kind of run.
1. A noted option (Quick dialogue's shape): on by default in a play run, the original (off) in a check or a scripted run;
   a flag or setting wins; kept in the recording as a note only when it is not the original, and a replay takes the
   recording's note (a recording without it: the original).
2. A live option (fast forward's speed's shape): its value follows the flag or the menu at once, never recorded.
3. The menu entries: one per option, in the table's menu, with the id as the settings key; a menu change to a noted
   option applies from the next start.
4. A build with no table of the game's links an empty one.
Only where Visual Studio's 32-bit tools are found (tests/test_lockstep.py NO_VC says how), skipped elsewhere.

Nearest existing: tests/test_menu_data.py - game.json menu_choices (base/menu_data.c), which has no flag, note or default.

usage: python -W error tests/test_game_options.py
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
#include <string.h>

static int g_fixed, g_scripted, g_replaying, g_note = -1;
void port_log(const char *fmt, ...) { (void)fmt; }
void game_log_line(const char *fmt, ...) { (void)fmt; }
int host_fixed_clock(void) { return g_fixed; }
int input_scripted(void) { return g_scripted; }
int session_replaying(void) { return g_replaying; }
int session_note_replayed(const char *name, void *buf, u32 size)
{
    if (g_note < 0 || strcmp(name, "*quick")) return 0;
    *(u8 *)buf = (u8)g_note, (void)size;
    return 1;
}
void session_save_read(const char *name, const void *buf, u32 size, int got)
{
    printf("note %s %u %u %d\n", name, *(const u8 *)buf, size, got);
}
const PortOptions port_speed_options = {NULL, 0u};
#ifndef NO_TABLE
static const char *const k_labels[] = {"2x", "3x", "4x"};
static const char *const k_words[] = {"2x", "3x", "4x"};
static const PortOption k_items[] = {
    {"quick", "Game", "&Quick (from the next start)", PORT_MENU_CHECK, NULL, NULL, 0, 1, 0, PORT_OPTION_NOTE, NULL},
    {"speedy", "Game", "&Speedy", PORT_MENU_CHOICE, k_labels, k_words, 3, 1, 1, 0, NULL},
};
const PortOptions port_game_options = {k_items, 2u};
#endif

int main(int argc, char **argv)
{
    const char *mode = argc > 1 ? argv[1] : "play";
    int play = 1;
    if (!strcmp(mode, "check")) play = 0;
    if (!strcmp(mode, "scripted")) g_scripted = 1;
    if (!strcmp(mode, "flag_off")) port_options_option("--quick", "off");
    if (!strcmp(mode, "flag_on_check")) play = 0, port_options_option("--quick", "on");
    if (!strcmp(mode, "replay_note")) g_replaying = 1, g_note = 1;
    if (!strcmp(mode, "replay_none")) g_replaying = 1;
    if (!strcmp(mode, "lazy")) {
        printf("quick %d\n", port_option("quick")); /* no port_options_start: the base's guess (not fixed clock) */
        return 0;
    }
    if (!strcmp(mode, "lazy_fixed")) {
        g_fixed = 1;
        printf("quick %d\n", port_option("quick"));
        return 0;
    }
    port_options_start(play);
    printf("quick %d\n", port_option("quick"));
    printf("speedy %d\n", port_option("speedy"));
    printf("unknown %d %d\n", port_option("nothing"), port_options_option("--nothing", "on"));
    if (!strcmp(mode, "play") && port_option_find("quick") >= 0) {
        unsigned n = 0;
        const PortMenuItem *m = port_options_menu(&n);
        printf("taken %d %d\n", port_options_option("--speedy", "4x"), port_options_option("--speedy", "9x"));
        printf("speedy_live %d\n", port_option("speedy"));
        printf("menu %u %s|%s|%s|%d %s|%s|%d|%u\n", n, m[0].menu, m[0].label, m[0].setting, m[0].kind, m[1].setting, m[1].values[2],
               m[1].kind, m[1].n_choices);
        m[0].set(0);
        printf("menu_quick %d run %d\n", m[0].get(), port_option("quick")); /* the next start's; this run keeps its own */
        m[1].set(0);
        printf("menu_speedy %d run %d\n", m[1].get(), port_option("speedy"));
    }
    return 0;
}
"""
MODES = ["play", "check", "scripted", "flag_off", "flag_on_check", "replay_note", "replay_none", "lazy", "lazy_fixed"]


@unittest.skipUnless(HAVE_VC, NO_VC)
class GameOptions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = d = pathlib.Path(tempfile.mkdtemp())
        (d / "drv.c").write_text(DRIVER_C, encoding="utf-8")
        src = f'/I"{KIT / "base"}" "{d / "drv.c"}" "{KIT / "base" / "game_options.c"}"'
        cls.build, outs = compile_run(d, "drv", src, [[m] for m in MODES])
        cls.out = dict(zip(MODES, outs))
        cls.build_none, (cls.none,) = compile_run(d, "none", "/DNO_TABLE " + src, [["play"]])

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def lines(self, mode):
        return self.out[mode]

    def value(self, mode, key):
        return next(line.split()[1] for line in self.out[mode] if line.startswith(key + " "))

    def test_built(self):
        self.assertEqual(self.build, "")
        self.assertEqual(self.build_none, "")

    def test_on_by_default_in_a_play_run_and_noted(self):
        self.assertEqual(self.value("play", "quick"), "1")
        self.assertIn("note *quick 1 1 1", self.lines("play"))  # the same bytes Quick dialogue kept by hand

    def test_the_original_in_checks_and_scripted_runs_with_no_note(self):
        for mode in ("check", "scripted"):
            with self.subTest(mode):
                self.assertEqual(self.value(mode, "quick"), "0")
                self.assertFalse([line for line in self.lines(mode) if line.startswith("note")])

    def test_a_flag_wins(self):
        self.assertEqual(self.value("flag_off", "quick"), "0")
        self.assertEqual(self.value("flag_on_check", "quick"), "1")
        self.assertIn("note *quick 1 1 1", self.lines("flag_on_check"))

    def test_a_replay_takes_the_recording(self):
        self.assertEqual(self.value("replay_note", "quick"), "1")
        self.assertEqual(self.value("replay_none", "quick"), "0")  # recorded before the option: the original
        # the replay's own recording keeps the note (as session.c port_play_option does); one without it writes none
        self.assertEqual([line for line in self.lines("replay_note") if line.startswith("note")], ["note *quick 1 1 1"])
        self.assertFalse([line for line in self.lines("replay_none") if line.startswith("note")])

    def test_a_kit_that_never_starts_them(self):
        self.assertEqual(self.value("lazy", "quick"), "1")
        self.assertEqual(self.value("lazy_fixed", "quick"), "0")

    def test_live_option_and_flags(self):
        self.assertEqual(self.value("play", "speedy"), "1")
        self.assertIn("taken 2 2", self.lines("play"))  # a bad word is taken (and logged), not passed on
        self.assertEqual(self.value("play", "speedy_live"), "2")
        self.assertIn("unknown -1 0", self.lines("play"))

    def test_menu_entries(self):
        self.assertIn("menu 2 Game|&Quick (from the next start)|quick|1 speedy|4x|2|3", self.lines("play"))
        self.assertIn("menu_quick 0 run 1", self.lines("play"))  # from the next start
        self.assertIn("menu_speedy 0 run 0", self.lines("play"))  # live: at once

    def test_no_table(self):
        self.assertIn("quick -1", self.none)
        self.assertIn("speedy -1", self.none)


class Wired(unittest.TestCase):
    """The base's side, from its source: every kit's option chain passes --ID on, the menu and the settings know them."""

    def text(self, name):
        return (KIT / name).read_text(encoding="utf-8")

    def test_wired(self):
        self.assertIn("port_options_option(arg, value)", self.text("base/input.c"))
        self.assertIn("port_options_menu(&n)", self.text("base/host_menu_extra.cpp"))
        self.assertIn("port_option_find(key.c_str()) >= 0", self.text("base/host_settings.cpp"))
        self.assertIn("port_options_start(", self.text("saturn/sat_main.c"))


if __name__ == "__main__":
    unittest.main()
