"""Live input: a run that plays by itself never reads the real keyboard or gamepads (port/base/live.c).
Compiles live.c with a driver that feeds it an argument list the way main.c does, and checks the source wiring
that keeps the real devices and the window focus out of such a run. No game, no window, no real input.
Run: python -W error tests/test_live_input.py
"""
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[1]
KIT = TOOL / "port"
sys.path.insert(0, str(KIT / "tools"))
sys.path.insert(0, str(TOOL))
sys.path.insert(0, str(TOOL / "tests"))

import game_config  # noqa: E402
from test_sync import TMP  # noqa: E402,F401  (sets DECOMP_PROJECT when run alone)
from test_lockstep import HAVE_VC, NO_VC  # noqa: E402

DRIVER = r"""
#include <stdio.h>
int input_live_option(const char *arg, const char *value);
int input_live(void);
void port_log(const char *fmt, ...) { (void)fmt; }
int main(int argc, char **argv)
{
    for (int i = 1, used; i < argc; i += used) {
        used = i + 1 < argc ? 2 : 1;
        used = input_live_option(argv[i], i + 1 < argc ? argv[i + 1] : 0);
        if (!used) used = i + 1 < argc ? 2 : 1;
    }
    printf("%d\n", input_live());
    return 0;
}
"""

CASES = [
    ([], 1),                                                  # a person playing
    (["--seconds", "0", "--present", "interp"], 1),
    (["--input", "A@10"], 0),
    (["--replay", "x.ptrec"], 0),
    (["--headless"], 0),
    (["--no-window"], 0),
    (["--fixed-clock", "1"], 0),
    (["--fixed-clock", "0"], 1),
    (["--fast-until", "100"], 0),
    (["--lockstep", "x.txt"], 0),
    (["--replay", "x.ptrec", "--live-input", "on"], 1),       # watch and play on
    (["--input", "A@10", "--live-input", "on"], 1),
    (["--live-input", "off"], 0),
    (["--live-input", "on", "--replay", "x.ptrec"], 1),       # the order does not matter
]


class LiveInputRule(unittest.TestCase):
    @unittest.skipUnless(HAVE_VC, NO_VC)
    def test_rule(self):
        d = pathlib.Path(tempfile.mkdtemp())
        try:
            (d / "port_config.h").write_text(game_config.port_config_h(
                {"name": "Fixture", "entry": {"name": "Fx_Main", "addr": "8c011000"}, "image_base": "8c010000",
                 "boot_file": "", "console": None, "id": "FIXTURE", "boot_sha256": []}), encoding="utf-8")
            (d / "drv.c").write_text(DRIVER, encoding="utf-8")
            rd = TOOL / "targets" / "dreamcast_sh4" / "runtime_rd"
            cl = (f'cl /nologo /TC /std:c11 /W4 /WX /I"{d}" /I"{KIT / "runtime"}" /I"{KIT / "base"}" /I"{rd}" '
                  f'drv.c "{KIT / "base" / "live.c"}" /Fe:drv.exe')
            vc = str(game_config.vcvars32()).replace("/", "\\")
            r = subprocess.run(f'cmd /d /s /c ""{vc}" >nul 2>&1 && {cl}"', cwd=d, capture_output=True, text=True,
                               errors="replace", timeout=300)
            self.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr[-1000:])
            for args, live in CASES:
                with self.subTest(args=args):
                    out = subprocess.run([str(d / "drv.exe")] + args, capture_output=True, text=True, timeout=30)
                    self.assertEqual(out.stdout.strip(), str(live))
        finally:
            shutil.rmtree(d, ignore_errors=True)


class LiveInputWiring(unittest.TestCase):
    def read(self, rel):
        return (KIT / rel).read_text(encoding="utf-8")

    def test_devices_are_read_only_when_live(self):
        text = self.read("base/input.c")
        body = text[text.index("u32 host_pads_read"):]
        body = body[:body.index("\n}\n")]
        self.assertRegex(body, r"if \(input_live\(\) && sdl_ready\(\)\) \{\n\s+s\[0\] = read_keyboard")
        # the only place the keyboard and the devices are read
        self.assertEqual(len(re.findall(r"\bread_keyboard\(", text)), 2)  # its definition and this call
        self.assertEqual(len(re.findall(r"\bscan_devices\(", text)), 2)

    def test_main_asks_live_first(self):
        text = self.read("runtime/main.c")
        # before every other option: --fixed-clock and --fast-until are read earlier in the chain than input_option
        # (a first version asked after them, so those runs stayed live: build box B's focus probe showed it)
        self.assertEqual(text.count("input_live_option(a, v)"), 1)
        self.assertLess(text.index("input_live_option(a, v)"), text.index('!strcmp(a, "--disc")'))
        self.assertLess(text.index("input_live_option(a, v)"), text.index('!strcmp(a, "--fixed-clock")'))

    def test_window_cannot_take_focus(self):
        text = self.read("base/host_window.cpp")
        self.assertIn("SDL_WINDOW_NOT_FOCUSABLE", text)
        self.assertIn('SDL_HINT_WINDOW_ACTIVATE_WHEN_SHOWN, take_focus ? "1" : "0"', text)
        self.assertIn('SDL_HINT_JOYSTICK_ALLOW_BACKGROUND_EVENTS, input_live() ? "1" : "0"', text)
        self.assertIn("const bool take_focus = input_live() != 0;", text)
        self.assertIn("if (take_focus && !SDL_RaiseWindow(g_window))", text)  # raising takes the foreground (build box B probe)

    def test_no_window_is_a_play_run_that_shows_nothing(self):
        main = self.read("base/host_main.cpp")
        self.assertNotRegex(main, r'bool scripted = [^;]*"--no-window"')  # settings, game log and defaults stay
        self.assertRegex(main, r'crash_install\([^;]*!has_flag\(argc, argv, "--no-window"\)\)')  # no message box
        self.assertIn('!strcmp(arg, "--no-window")) return g_gfx.headless = 1', self.read("base/present_device.c"))

    def test_session_names_the_input_state_at_the_end_of_a_recording(self):
        self.assertIn("--live-input on reads the real pads", self.read("base/session.c"))


if __name__ == "__main__":
    unittest.main()
