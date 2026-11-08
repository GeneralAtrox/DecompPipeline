"""The port base's drift test (port/README.md "The port base"): no console kit makes its own window, reads pads itself,
opens its own log, builds its own menu or has a program entry of its own, and every console's menu is the base's menu.

1. The real tree: port/base/ owns all of it, and port/tools/base_drift.py finds nothing in any kit folder (every folder
   under port/ that is not base/ or tools/, and every targets/*/port/).
2. A planted copy fails: a bare window in a new kit folder, a pad read and a log in a kit beside a translator
   (targets/<name>/port/), a menu built in the Dreamcast kit - each is reported, and the same tree without them passes.
3. The menu list is one list: every console-only entry (a PortMenuItem in a console_*.c) names Display, Audio or Input
   and repeats no label of the base's bar.

usage: python -W error tests/test_port_base.py
"""
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parent.parent
KIT = TOOL / "port"
sys.path.insert(0, str(KIT / "tools"))

import base_drift  # noqa: E402
import game_config  # noqa: E402,F401
sys.path.insert(0, str(TOOL / "tests"))
from test_port_buttons import BuildRun  # noqa: E402  (ExButtons' build-and-run helper)


class RealTree(unittest.TestCase):
    def test_no_kit_does_what_the_base_owns(self):
        self.assertEqual(base_drift.scan(TOOL), [])

    def test_the_base_has_the_pieces(self):
        for name in ("host_window.cpp", "host_menu.cpp", "host_copy_button.cpp", "host_settings.cpp", "host_crash.cpp",
                     "input.c", "session.c", "game_log.c", "scene_dump.c", "port_log.c", "clock.c", "console.h", "base.h"):
            with self.subTest(name=name):
                self.assertTrue((KIT / "base" / name).is_file(), name)

    def test_every_kit_with_a_console_defines_port_console(self):
        for kit in base_drift.kit_dirs(TOOL):
            files = sorted(kit.glob("console_*.c"))
            if not files:
                continue
            with self.subTest(kit=kit.relative_to(TOOL).as_posix()):
                self.assertTrue(any(re.search(r"\bconst\s+PortConsole\s+port_console\b", f.read_text(encoding="utf-8")) for f in files))


class BuildList(unittest.TestCase):
    """Both build tools take all of base/, so a file added to the base reaches every console."""

    def test_both_builds_take_the_whole_base(self):
        saturn = (KIT / "tools" / "build_saturn.py").read_text(encoding="utf-8")
        cmake = (KIT / "CMakeLists.txt").read_text(encoding="utf-8")
        for needle in ('BASE.glob("*.c")', 'BASE.glob("*.cpp")'):
            self.assertIn(needle, saturn)
        for needle in ("GLOB PORT_BASE_C CONFIGURE_DEPENDS base/*.c", "GLOB PORT_HOST CONFIGURE_DEPENDS base/*.cpp"):
            self.assertIn(needle, cmake)


class Planted(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="portbase_"))
        (self.d / "port" / "base").mkdir(parents=True)
        # the base itself may do all of it
        (self.d / "port" / "base" / "host_window.cpp").write_text('void f(){ SDL_CreateWindow("x",1,1,0); CreateMenu(); }\n')
        (self.d / "port" / "base" / "host_menu.cpp").write_text('void g(){ AppendMenuW(m, MF_STRING, 1, L"&Full screen"); }\n')
        (self.d / "port" / "kit_ok").mkdir()
        (self.d / "port" / "kit_ok" / "run.c").write_text("/* CreateWindowEx and XInputGetState are only mentioned here */\nint port_main(int a, char **b){ return 0; }\n")

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_clean_tree_passes(self):
        self.assertEqual(base_drift.scan(self.d), [])

    def test_a_bare_window_in_a_kit_is_found(self):
        (self.d / "port" / "newkit").mkdir()
        (self.d / "port" / "newkit" / "video.c").write_text('void v(void){ CreateWindowExA(0, "c", "t", 0, 0, 0, 1, 1, 0, 0, 0, 0); }\n')
        found = base_drift.scan(self.d)
        self.assertEqual(len(found), 1, found)
        self.assertIn("port/newkit/video.c:1", found[0])
        self.assertIn("window", found[0])

    def test_a_kit_beside_a_translator_is_scanned(self):
        kit = self.d / "targets" / "xbox360_ppc" / "port"
        kit.mkdir(parents=True)
        (kit / "pad.c").write_text("int p(void){ XINPUT_STATE s; return XInputGetState(0, &s); }\n")
        (kit / "log.c").write_text('#include <stdio.h>\nvoid port_log(const char *f, ...) { FILE *x = fopen("port.log", "w"); }\n')
        found = base_drift.scan(self.d)
        self.assertTrue(any("targets/xbox360_ppc/port/pad.c" in f and "pad read" in f for f in found), found)
        self.assertTrue(any("targets/xbox360_ppc/port/log.c" in f and "log" in f for f in found), found)

    def test_an_audio_device_a_presenter_and_a_tick_wait_are_found(self):
        for what, call in (("an audio device of its own", "waveOutOpen(&d, 0, &f, 0, 0, 0);"),
                           ("an audio device of its own", "SDL_OpenAudioDeviceStream(0, &s, 0, 0);"),
                           ("a presenter of its own", "D3D11CreateDevice(0, 0, 0, 0, 0, 0, 7, &a, &b, &c);"),
                           ("a presenter of its own", "SDL_CreateRenderer(w, 0);"),
                           ("pacing of its own", "Sleep(1);")):
            kit = self.d / "port" / "planted"
            kit.mkdir(parents=True, exist_ok=True)
            (kit / "bad.c").write_text("void f(void) { " + call + " }\n", encoding="utf-8")
            self.assertTrue(any(what in f for f in base_drift.scan(self.d)), call)

    def test_a_wait_marked_as_no_tick_is_let_through(self):
        kit = self.d / "port" / "planted"
        kit.mkdir(parents=True, exist_ok=True)
        (kit / "ok.c").write_text("void f(void) { Sleep(1); /* not the tick: the sampler thread */ }\n", encoding="utf-8")
        self.assertEqual(base_drift.scan(self.d), [])

    def test_a_menu_and_an_entry_point_are_found(self):
        (self.d / "port" / "runtime").mkdir()
        (self.d / "port" / "runtime" / "m.c").write_text("void m(void){ HMENU h = CreatePopupMenu(); AppendMenuA(h, 0, 1, \"x\"); }\nint main(int c, char **v){ return 0; }\n")
        found = base_drift.scan(self.d)
        self.assertTrue(any("menu" in f for f in found), found)
        self.assertTrue(any("program entry" in f for f in found), found)


class MenuList(unittest.TestCase):
    def test_console_entries_are_in_the_base_menus_and_repeat_nothing(self):
        base = set(base_drift.menu_labels(TOOL))
        self.assertIn("&Copy agent prompt\tF9", base)
        for kit in base_drift.kit_dirs(TOOL):
            for f in sorted(kit.glob("console_*.c")):
                for menu, label in re.findall(r'\{\s*"([A-Za-z]+)"\s*,\s*"([^"]*)"\s*,\s*PORT_MENU_', f.read_text(encoding="utf-8")):
                    with self.subTest(file=f.name, entry=label):
                        self.assertIn(menu, ("Display", "Audio", "Input"))
                        self.assertNotIn("&" + label.replace("&", ""), base)
                        self.assertNotIn(label, base)


REQUIRED = ("port_vblank_count", "port_extra_drawing", "console_guest_ptr", "console_scene_extra", "console_replay_args",
            "console_capture_bmp", "console_view", "console_watch", "console_lockstep_pad", "console_lockstep_timer",
            "console_lockstep_time", "console_menu_extra", "console_pads_changed", "console_actions", "console_check_image",
            "console_present_option", "console_present_over", "console_present_release", "console_widens", 
            "extra_interp", "extra_set_fps", "extra_fps", "port_main")


def defined(text, name):
    return re.search(r"^[A-Za-z_][\w 	\*]*[ 	\*]" + name + r"\s*\(", text, re.M) is not None


class NewConsole(unittest.TestCase):
    """tools/game_setup.py console: a new console starts with every function of base/console.h, and builds."""

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(TOOL / "tools"))
        import game_setup
        cls.d = Path(tempfile.mkdtemp(prefix="newconsole_"))
        cls.file = game_setup.new_console("newbox", "NewBox", "Test Game", cls.d / "port" / "newbox")
        cls.text = cls.file.read_text(encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.d, ignore_errors=True)

    def test_every_console_function_is_there(self):
        self.assertIn("port_console", self.text)
        for name in REQUIRED:
            with self.subTest(name=name):
                self.assertTrue(defined(self.text, name), name)

    def test_the_real_consoles_define_the_same_list(self):
        for folder in ("runtime", "saturn"):
            text = "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in sorted((KIT / folder).rglob("*.c")))
            for name in REQUIRED:
                with self.subTest(kit=folder, name=name):
                    self.assertTrue(defined(text, name), name)

    def test_the_scaffold_is_clean_to_the_drift_test_and_unfilled_todos_are_marked(self):
        d = Path(tempfile.mkdtemp(prefix="newconsole_tree_"))
        try:
            (d / "port" / "base").mkdir(parents=True)
            (d / "port" / "newbox").mkdir()
            shutil.copy(self.file, d / "port" / "newbox" / self.file.name)
            self.assertEqual(base_drift.scan(d), [])
        finally:
            shutil.rmtree(d, ignore_errors=True)
        self.assertIn("TODO", self.text)

    def test_the_scaffold_compiles(self):
        import subprocess
        import game_config
        vc = game_config.vcvars32()
        if not vc:
            self.skipTest("no 32-bit Visual Studio environment")
        bat = self.d / "c.bat"
        bat.write_text('@echo off\r\ncall "%s" >nul\r\ncl /nologo /c /W4 /WX /I"%s" /Fo"%s/" "%s"\r\n' %
                       (vc, KIT / "base", self.d, self.file), encoding="ascii")
        r = subprocess.run(["cmd", "/c", str(bat)], capture_output=True, text=True, timeout=300)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


class SessionAhead(BuildRun, unittest.TestCase):
    """session.c: the sound mixed ahead of a real clock goes into the recording at its blank and comes back at that blank
    on replay; a save read after it still finds its own entry, and one the replay has passed is dropped."""

    def test_mixed_ahead_round_trip(self):
        self.build_run("ahead", r'''
#include <stdio.h>
#include <string.h>
#include "base.h"
#include "session.h"
static u32 g_vb;
u32 port_vblank_count(void) { return g_vb; }
int port_extra_drawing(void) { return 0; }
int console_lockstep_pad(HostPad *pad) { (void)pad; return 0; }
s32 console_lockstep_timer(s32 live) { return live; }
int console_lockstep_time(HostTime *t) { (void)t; return 0; }
int input_live(void) { return 0; }
void port_log(const char *fmt, ...) { (void)fmt; }
#define CHECK(c) do { if (!(c)) { printf("fail line %d: %s\n", __LINE__, #c); return 1; } } while (0)
int main(void)
{
    u32 save = 0x12345678, got = 0;
    g_vb = 5, session_mix_ahead(3);
    g_vb = 6, session_mix_ahead(0); /* nothing kept */
    g_vb = 7, session_save_read("a.sav", &save, 4, 1);
    g_vb = 9, session_mix_ahead(2);
    g_vb = 11, session_mix_ahead(4);
    g_vb = 12, session_save_read("b.sav", &save, 4, 1);
    CHECK(session_write("ahead.ptrec", "test\n"));
    CHECK(session_option("--replay", "ahead.ptrec") == 2 && session_replaying());
    g_vb = 4;
    CHECK(session_mix_ahead_replayed() == 0);
    g_vb = 5;
    CHECK(session_mix_ahead_replayed() == 3);
    CHECK(session_mix_ahead_replayed() == 0); /* taken */
    g_vb = 7;
    CHECK(session_save_replayed("a.sav", &got, 4) == 1 && got == save);
    g_vb = 10;
    CHECK(session_mix_ahead_replayed() == 0); /* blank 9's is passed: dropped, not given late */
    g_vb = 11;
    CHECK(session_mix_ahead_replayed() == 4);
    got = 0;
    g_vb = 12;
    CHECK(session_save_replayed("b.sav", &got, 4) == 1 && got == save);
    CHECK(session_save_replayed("c.sav", &got, 4) == -2); /* the recording has no more */
    puts("ok");
    return 0;
}
''', [KIT / "base" / "session.c"], "c")

    def test_a_save_read_skips_a_note_it_meets(self):
        self.build_run("ahead2", r'''
#include <stdio.h>
#include <string.h>
#include "base.h"
#include "session.h"
static u32 g_vb;
u32 port_vblank_count(void) { return g_vb; }
int port_extra_drawing(void) { return 0; }
int console_lockstep_pad(HostPad *pad) { (void)pad; return 0; }
s32 console_lockstep_timer(s32 live) { return live; }
int console_lockstep_time(HostTime *t) { (void)t; return 0; }
int input_live(void) { return 0; }
void port_log(const char *fmt, ...) { (void)fmt; }
#define CHECK(c) do { if (!(c)) { printf("fail line %d: %s\n", __LINE__, #c); return 1; } } while (0)
int main(void)
{
    u32 save = 7, got = 0;
    g_vb = 2, session_mix_ahead(1);
    g_vb = 3, session_save_read("a.sav", &save, 4, 1);
    CHECK(session_write("ahead2.ptrec", "test\n"));
    CHECK(session_option("--replay", "ahead2.ptrec") == 2);
    g_vb = 1; /* out of step: the save read comes first */
    CHECK(session_save_replayed("a.sav", &got, 4) == 1 && got == save);
    CHECK(session_mix_ahead_replayed() == 0);
    puts("ok");
    return 0;
}
''', [KIT / "base" / "session.c"], "c")


if __name__ == "__main__":
    unittest.main()
