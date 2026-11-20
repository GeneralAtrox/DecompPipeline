"""File > Restart and File > Exit (port/base/host_menu.cpp, host_menu_extra.cpp), from the source:
1. File is the bar's first menu, the same on every console: the base builds it (the plain menu and the Phangrade title
   bar, host_bar.cpp, draw the same HMENU), Restart from the extras, then a separator and Exit.
2. Restart is host_online.cpp's start_again (no second relauncher): this run's own command line less a scripted
   --menu-pick; with no window the run ends through port_exit, so its recording and game log close first. It is
   offered in a play run only: greyed (and refused from --menu-pick) in a replay or on the fixed clock.
3. The new run logs its own command line (host_resume.cpp resume_wait), which the restart proof compares.
4. The Saturn kit takes --menu-pick (FRAME is the vertical blank), so a scripted run can pick File > Restart.

Nearest existing: tests/test_port_base.py MenuList (the console entries against the base's menus).

usage: python -W error tests/test_file_menu.py
"""
import pathlib
import re
import unittest

KIT = pathlib.Path(__file__).resolve().parents[1] / "port"


def text(name):
    return (KIT / name).read_text(encoding="utf-8")


class FileMenu(unittest.TestCase):
    def test_file_is_first_then_display(self):
        attach = re.search(r"void menu_attach\(void \*hwnd\)\n\{(.*?)\n\}", text("base/host_menu.cpp"), re.S).group(1)
        tops = re.findall(r'AppendMenuW\(g_menu, MF_POPUP, [^;]*L"&(\w+)"\);', attach)
        self.assertEqual(tops[:2], ["File", "Display"])
        self.assertIn('append_extras(file, "File", false);', attach)
        self.assertIn('AppendMenuW(file, MF_STRING, kExit, L"E&xit\\tAlt+F4");', attach)
        self.assertLess(attach.index('append_extras(file, "File"'), attach.index("kExit"))

    def test_exit_closes_the_window(self):
        self.assertRegex(text("base/host_menu.cpp"), r"if \(id == kExit\) \{[^\n]*\n\s+PostMessageW\(g_hwnd, WM_CLOSE, 0, 0\);")

    def test_restart_is_start_again_in_play_runs_only(self):
        extra = text("base/host_menu_extra.cpp")
        self.assertIn('{"File", "&Restart", PORT_MENU_COMMAND,', extra)
        self.assertIn("return !session_replaying() && !host_fixed_clock();", extra)
        self.assertIn('host::start_again(hwnd, {L"--menu-pick"}, {}, "File > Restart") && !hwnd) port_exit(', extra)
        self.assertIn("g_extra_all.assign(std::begin(k_file), std::end(k_file));", extra)  # first of the extras
        self.assertNotIn("CreateProcess", extra)  # one relauncher: host_online.cpp's

    def test_the_new_run_logs_its_command_line(self):
        self.assertIn('port_log_early("start again: started by process %ls, which ends first; this run: %s\\n"',
                      text("base/host_resume.cpp"))
        self.assertIn('game_log_line("%s: started process %lu: %s", who', text("base/host_online.cpp"))

    def test_saturn_takes_menu_pick(self):
        self.assertIn("(used = port_menu_pick_option(a, v)) == 0", text("saturn/sat_main.c"))
        self.assertIn("port_menu_pick_poll(g_vblanks);", text("saturn/sat_sys.c"))


if __name__ == "__main__":
    unittest.main()
