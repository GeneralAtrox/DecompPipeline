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
            "console_present_option", "console_present_over", "console_present_release", "console_widens", "extra_set_interp",
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


class ExButtons(unittest.TestCase):
    """base.h port_pad_ex_*: held / pressed / name (input_ex.c) and the per-game settings store (host_settings.cpp), built
    with a stub beside them and run."""

    @classmethod
    def setUpClass(cls):
        import game_config
        cls.vc = game_config.vcvars32()
        cls.d = Path(tempfile.mkdtemp(prefix="exbuttons_"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.d, ignore_errors=True)

    def build_run(self, name, source, files, std):
        import subprocess
        if not self.vc:
            self.skipTest("no 32-bit Visual Studio environment")
        src = self.d / (name + (".cpp" if std == "c++20" else ".c"))
        src.write_text(source)
        bat = self.d / (name + ".bat")
        srcs = " ".join('"%s"' % f for f in [src] + files)
        bat.write_text('@echo off\r\ncall "%s" >nul\r\ncd /d "%s"\r\ncl /nologo /W4 /WX %s /I"%s" %s /Fe%s.exe /Fo"%s/" && "%s\\%s.exe"\r\n' %
                       (self.vc, self.d, "/std:c++20 /EHsc" if std == "c++20" else "/TC", KIT / "base", srcs, name, self.d, self.d, name), encoding="ascii")
        r = subprocess.run(["cmd", "/c", str(bat)], capture_output=True, text=True, timeout=300)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_held_pressed_and_names(self):
        self.build_run("ex", r'''
#include <stdio.h>
#include <string.h>
#include "base.h"
static int g_style;
int pad_style(void) { return g_style; }
#define CHECK(c) do { if (!(c)) { printf("fail line %d: %s\n", __LINE__, #c); return 1; } } while (0)
int main(void)
{
    HostPad pad[HOST_PADS] = {0};
    CHECK(port_pad_ex_held(0) == 0 && port_pad_ex_pressed(0) == 0 && port_pad_ex_held(9) == 0);
    pad[1].buttons = HOST_PAD_LB | HOST_PAD_RS_UP | HOST_PAD_A; /* a Dreamcast bit is not a modern one */
    input_ex_note(pad);
    CHECK(port_pad_ex_held(1) == (HOST_PAD_LB | HOST_PAD_RS_UP));
    CHECK(port_pad_ex_held(0) == 0);
    pad[1].buttons = HOST_PAD_LB; /* RS_UP released, LB still held: only the new press is "pressed" */
    input_ex_note(pad);
    CHECK(port_pad_ex_pressed(1) == (HOST_PAD_LB | HOST_PAD_RS_UP)); /* both were new since the last call */
    CHECK(port_pad_ex_pressed(1) == 0);
    pad[1].buttons = 0;
    input_ex_note(pad);
    pad[1].buttons = HOST_PAD_RB;
    input_ex_note(pad);
    CHECK(port_pad_ex_pressed(1) == HOST_PAD_RB && port_pad_ex_held(1) == HOST_PAD_RB);
    g_style = 1;
    CHECK(!strcmp(port_pad_ex_name(HOST_PAD_BACK, 0), "View"));
    g_style = 2;
    CHECK(!strcmp(port_pad_ex_name(HOST_PAD_LB, 0), "L1") && !strcmp(port_pad_ex_name(HOST_PAD_GUIDE, 0), "PS"));
    g_style = 0;
    CHECK(!strcmp(port_pad_ex_name(HOST_PAD_L3, 0), "Left stick click") && !strcmp(port_pad_ex_name(0x1, 0), ""));
    puts("ok");
    return 0;
}
''', [KIT / "base" / "input_ex.c"], "c")

    def test_settings_store(self):
        self.build_run("st", r'''
#include <cstdio>
#include <fstream>
#include <string>
#include "base.h"
#include "host_internal.hpp"
extern "C" void port_log(const char *, ...) {}
#define CHECK(c) do { if (!(c)) { std::printf("fail line %d: %s\n", __LINE__, #c); return 1; } } while (0)
int main()
{
    CHECK(port_setting_get_int("game.a", 7) == 7); // no settings file: the default
    port_setting_set_int("game.a", 3);             // and nothing is written
    CHECK(port_setting_get_int("game.a", 7) == 7);
    host::settings_use("st_settings.txt");
    { std::ofstream o("st_settings.txt"); o << "window=800x600\ngame.b=5 # a comment\n"; }
    CHECK(port_setting_get_int("game.b", 0) == 5 && port_setting_get_int("game.c", 9) == 9);
    port_setting_set_int("game.c", -4);
    port_setting_set_int("game.b", 6);
    port_setting_set_int("bind-A", 1); // not a game key: refused
    CHECK(port_setting_get_int("game.c", 9) == -4 && port_setting_get_int("game.b", 0) == 6);
    std::vector<std::string> args = host::settings_args("st_settings.txt");
    CHECK(args.size() == 2 && args[0] == "--window"); // game.* are not options
    std::puts("ok");
    return 0;
}
''', [KIT / "base" / "host_settings.cpp"], "c++20")


if __name__ == "__main__":
    unittest.main()
