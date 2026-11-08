"""The port kit's game contract (port/CONTRACT.md): the document lists exactly the game.json keys, SDK modules, exit
codes and enhancements the code has, with no second copy of its tables in port/README.md; game.json "contract" is
checked against its version (another major stops, a missing key warns once); and every enhancement is off in a check
run (headless or fixed clock). Reads sources only: no build, no game. Run: python -W error tests/test_port_contract.py
"""
import contextlib
import io
import json
import pathlib
import re
import shutil
import sys
import tempfile
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[1]
KIT = TOOL / "port"
sys.path.insert(0, str(KIT / "tools"))
import game_config  # noqa: E402

DOC = (KIT / "CONTRACT.md").read_text(encoding="utf-8")
KNOWN_DOC = (KIT / "KNOWN.md").read_text(encoding="utf-8")
GAME = {"name": "Fixture Game", "decomp": "../decomp", "entry": {"name": "Fx_Main", "addr": "8c011000"},
        "image_base": "8c010000"}


def section(title, doc=DOC):
    """The text of '## title' up to the next '## '."""
    m = re.search(rf"^## {re.escape(title)}\n(.*?)(?=^## |\Z)", doc, re.M | re.S)
    if not m:
        raise AssertionError(f"CONTRACT.md has no section {title!r}")
    return m.group(1)


def first_column(title):
    """The backticked names (or numbers) in the first column of the section's table rows."""
    names = set()
    for line in section(title).splitlines():
        m = re.match(r"\|\s*`([^`]+)`\s*\|", line) or re.match(r"\|\s*(\d+)\s*\|", line)
        if m:
            names.add(m.group(1))
    return names


def sources(*globs):
    return {p: p.read_text(encoding="utf-8", errors="replace") for g in globs for p in sorted(KIT.glob(g))}


def exit_codes_in_code():
    """Every literal exit code the runtime and host can end with."""
    pats = (r"port_exit\([^;]*?,\s*(\d+)\)", r"ExitProcess\((\d+)\)", r"(?<![\w.])exit\((\d+)\)", r"\b(?:port_install_)?fail\((\d+),",
            r"^\s*return (\d+);")
    codes = set()
    for p, text in sources("runtime/**/*.c", "base/*.c", "base/*.cpp", "pvr/*.c", "sdk/*.c").items():
        for pat in pats[:4]:
            codes |= {int(c) for c in re.findall(pat, text, re.M)}
        if p.name == "host_main.cpp":  # main() and install() return the process's exit code
            codes |= {int(c) for c in re.findall(pats[4], text, re.M)}
    return codes


class ContractDocument(unittest.TestCase):
    def test_version(self):
        self.assertRegex(DOC, r"(?m)^Contract version: \d+\.\d+\.\d+$")
        self.assertEqual(len(game_config.contract_version()), 3)

    def test_game_json_keys(self):
        self.assertEqual(first_column("game.json"), set(game_config.KNOWN))

    def test_sdk_modules(self):
        self.assertEqual(first_column("SDK modules"), set(game_config.sdk_modules()))

    def test_exit_codes(self):
        listed = {int(c) for c in first_column("Exit codes")}
        self.assertTrue(exit_codes_in_code() <= listed, sorted(exit_codes_in_code() - listed))
        self.assertTrue(listed <= exit_codes_in_code(), sorted(listed - exit_codes_in_code()))

    def test_options_exist(self):
        code = "\n".join(sources("runtime/**/*.c", "base/*.c", "base/*.cpp", "pvr/*.c").values())
        rows = [x for x in (section("Command-line options") + section("Enhancements")).splitlines() if x[:1] == "|"]
        named = set(re.findall(r"`(--[a-z][a-z0-9-]*)", "\n".join(rows)))
        self.assertTrue(named)
        self.assertEqual(sorted(o for o in named if f'"{o}"' not in code), [])

    def test_one_copy(self):
        readme = (KIT / "README.md").read_text(encoding="utf-8")
        for header in ("| Key | Meaning |", "| SDK module | Replaces |", "| Exit code | Meaning |", "| Id | Option |"):
            self.assertNotIn(header, readme)
        self.assertIn("CONTRACT.md", readme)


class Enhancements(unittest.TestCase):
    """Every enhancement is listed, and off in a check run (headless or --fixed-clock), so goldens and emulator
    comparisons see the original."""

    def rows(self):
        out = {}
        for line in section("Enhancements").splitlines():
            cells = [c.strip() for c in re.split(r"(?<!\\)\|",line.strip().strip("|"))]
            if len(cells) >= 6 and cells[0].startswith("`"):
                out[cells[0].strip("`")] = cells
        return out

    def test_menu_settings_are_listed(self):
        menu = (KIT / "base" / "host_menu.cpp").read_text(encoding="utf-8")
        saved = set(re.findall(r'settings_set\("([a-z-]+)"', menu))
        listed = {c[5].strip("`") for c in self.rows().values()}
        prefs = set(re.findall(r"`([a-z-]+)`", section("Enhancements").split("Not enhancements:")[1]))
        self.assertEqual(sorted(saved - listed - prefs), [])
        self.assertEqual(listed & prefs, set())

    def test_check_tools_present_tick(self):
        for tool in ("regress.py", "lockstep.py", "bu_sweep.py"):
            text = (KIT / "tools" / tool).read_text(encoding="utf-8")
            with self.subTest(tool=tool):
                self.assertRegex(text, r'"--headless", "--fixed-clock", "1",[^\]]*"--present", "tick"')

    def test_host_adds_play_options_only_to_play_runs(self):
        text = (KIT / "base" / "host_main.cpp").read_text(encoding="utf-8")
        self.assertRegex(text, r'bool scripted = \(fixed && [^;]*has_flag\(argc, argv, "--headless"\)')
        block = re.search(r"if \(!scripted\) \{\n(.*?)\n    \}\n", text, re.S)
        self.assertIsNotNone(block)
        self.assertIn('"--present", "auto"', block.group(1))
        self.assertIn("settings_args", block.group(1))  # settings.txt (the menu's choices) is read in play runs only
        self.assertEqual(text.count("settings_args("), 1)
        self.assertEqual(text.count('"auto"'), 1)
        # no saved choice turns in-between frames off: they follow the frame-rate cap
        self.assertIn('key == "present"', (KIT / "base" / "host_settings.cpp").read_text(encoding="utf-8"))

    def test_wider_view_off_in_checks(self):
        """A check run stays 4:3 unless --aspect is given: not chosen, the shape follows the window only in a play run
        of a game with game.json widescreen."""
        text = (KIT / "base" / "present.c").read_text(encoding="utf-8")
        self.assertIn("console_widens() && !g_gfx.headless && !host_fixed_clock() ? 0.0f : 4.0f / 3.0f", text)
        self.assertIn("if (g_gfx.aspect_set) return g_gfx.aspect;", text)
        self.assertIn("int port_shape_chosen(void) { return g_gfx.aspect_set || gfx_aspect() == 0.0f; }", text)

    def test_given_only(self):
        """ssaa, aniso, vmu_screen, fps_counter: zero until their option sets them."""
        device = (KIT / "base" / "present_device.c").read_text(encoding="utf-8")
        for field in ("ssaa", "aniso"):
            sets = re.findall(rf"g_gfx\.{field} = ", "\n".join(sources("runtime/**/*.c", "pvr/*.c", "base/*.c").values()))
            self.assertEqual(len(sets), len(re.findall(rf"g_gfx\.{field} = ", device)), field)
        self.assertIn("static int g_mode; /* VMU_SCREEN_OFF", (KIT / "runtime/gfx/gfx_vmu.c").read_text("utf-8"))
        self.assertRegex((KIT / "base/host_window.cpp").read_text("utf-8"), r"(?m)^host::Prefs g_prefs;$")

    def test_texture_pack_off_in_checks(self):
        """A game's default pack is ignored on the fixed clock (the driven run is tests/test_pvr_pack.py
        TexturePackInCheckRuns); only --texture-pack on or DIR asks for it there."""
        pack = (KIT / "pvr" / "pvr_pack.c").read_text(encoding="utf-8")
        self.assertRegex(pack, r"static int g_wanted = -1;")
        self.assertIn("g_wanted < 0 ? !host_fixed_clock() : g_wanted", pack)
        self.assertNotIn("Texture pack in check runs", KNOWN_DOC)  # the gap is closed
        self.assertNotIn("not off yet", self.rows()["texture_pack"][3])


class ContractKey(unittest.TestCase):
    def setUp(self):
        self.dir = pathlib.Path(tempfile.mkdtemp())
        game_config._warned_no_contract.clear()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def load(self, **over):
        (self.dir / "game.json").write_text(json.dumps(dict(GAME, **over)), encoding="utf-8")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            cfg = game_config.load(self.dir)
        return cfg, err.getvalue()

    def test_same_version(self):
        have = ".".join(map(str, game_config.contract_version()))
        cfg, err = self.load(contract=have)
        self.assertEqual((cfg["contract"], err), (have, ""))

    def test_missing_warns_once(self):
        cfg, err = self.load()
        self.assertIsNone(cfg["contract"])
        self.assertIn('has no "contract" key', err)
        self.assertEqual(self.load()[1], "")

    def test_other_major_stops(self):
        major = game_config.contract_version()[0]
        with self.assertRaises(SystemExit) as stop:
            self.load(contract=f"{major + 1}.0.0")
        self.assertIn("A different major version means the game needs updating first", str(stop.exception))
        self.assertIn("CONTRACT.md", str(stop.exception))

    def test_newer_minor_warns(self):
        v = game_config.contract_version()
        _, err = self.load(contract=f"{v[0]}.{v[1] + 1}.0")
        self.assertIn("newer than this kit", err)

    def test_malformed(self):
        for bad in ("1", "1.0", "v1.0.0", 1):
            with self.subTest(bad=bad), self.assertRaises(SystemExit):
                self.load(contract=bad)

    def test_version_from_document(self):
        doc = self.dir / "C.md"
        doc.write_text("# x\n\nContract version: 2.3.4\n", encoding="utf-8")
        self.assertEqual(game_config.contract_version(doc), (2, 3, 4))
        doc.write_text("no version\n", encoding="utf-8")
        with self.assertRaises(SystemExit):
            game_config.contract_version(doc)


if __name__ == "__main__":
    unittest.main()
