"""Batch 26 item 4: no machine paths in tracked files. config.json keeps portable defaults and config.local.json
(git-ignored) gives this machine's paths, merged over it by core/toolcfg (core/paths.config, tools/sdk_fid.py,
tools/watchdog.py); tools/regress.py takes each project's workspace path from tests/patterns.local.json.
Run: python -W error tests/test_batch26b.py (also from tests/test_tooling.py)
"""
import json
import os
import pathlib
import re
import shutil
import sys
import tempfile
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
sys.path.insert(0, str(TOOL / "tools"))

from core import toolcfg  # noqa: E402

# what the repository tracks (live's git ls-files: these roots); the rest of a stage folder is not shipped
TRACKED_DIRS = ("core", "docs", "port", "targets", "tests", "tools", "ui")
TRACKED_TOP = (".gitignore", "AGENTS.md", "LICENSE", "README.md", "config.json", "decomp.py", "refine.py", "xproject.py")
IGNORED_PARTS = {"__pycache__", ".work", "ghidra_work", "backup"}
IGNORED_NAMES = {toolcfg.LOCAL, "patterns.local.json"}
TEXT = {".py", ".md", ".json", ".java", ".c", ".h", ".txt", ".html", ".js", ".css", ".gitignore", ".bat", ".sh",
        ".slaspec", ".sinc", ".pspec", ".cspec", ".ldefs", ".opinion", ".tsv", ".cfg", ""}
MACHINE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]+(?:Projects|Users|Tools|Program Files)\b|[C]:\\", re.I)  # [C]: not a hit itself


def tracked_files(root):
    root = pathlib.Path(root)
    out = [root / n for n in TRACKED_TOP if (root / n).is_file()]
    for d in TRACKED_DIRS:
        for top, dirs, names in os.walk(root / d):
            dirs[:] = sorted(x for x in dirs if x not in IGNORED_PARTS)  # never walk tests/.work and the like
            for n in sorted(names):
                p = pathlib.Path(top) / n
                if n not in IGNORED_NAMES and not n.endswith(".pyc") and ".bak" not in n \
                        and p.suffix.lower() in TEXT:
                    out.append(p)
    return out


def machine_paths(files):
    """[(file, line no, line)] holding a machine path, except a line marked as an example."""
    hits = []
    for p in files:
        for i, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if MACHINE.search(line) and "example" not in line.lower():
                hits.append((p, i, line.strip()[:120]))
    return hits


class Config(unittest.TestCase):
    def setUp(self):
        self.dir = pathlib.Path(tempfile.mkdtemp())
        (self.dir / "config.json").write_text(json.dumps({"vcvars": None, "workers": 4, "model": "haiku"}),
                                              encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_local_overrides_keys(self):
        (self.dir / toolcfg.LOCAL).write_text(json.dumps({"vcvars": "V:/vc/vcvars64.bat", "workers": 9}),
                                              encoding="utf-8")
        cfg = toolcfg.load(self.dir)
        self.assertEqual((cfg["vcvars"], cfg["workers"], cfg["model"]), ("V:/vc/vcvars64.bat", 9, "haiku"))
        self.assertEqual(toolcfg.need(cfg, "vcvars"), "V:/vc/vcvars64.bat")

    def test_missing_local_falls_back(self):
        cfg = toolcfg.load(self.dir)
        self.assertEqual(cfg, {"vcvars": None, "workers": 4, "model": "haiku"})

    def test_unset_path_is_a_clear_error(self):
        with self.assertRaises(RuntimeError) as e:
            toolcfg.need(toolcfg.load(self.dir), "vcvars")
        self.assertIn("'vcvars'", str(e.exception))
        self.assertIn(toolcfg.LOCAL, str(e.exception))
        with self.assertRaises(RuntimeError):  # not on PATH either
            toolcfg.need({}, "sevenzip", which="no_such_program_b26")

    def test_live_config_is_portable(self):
        cfg = json.loads((TOOL / "config.json").read_text(encoding="utf-8"))
        self.assertEqual([cfg.get(k) for k in toolcfg.MACHINE_KEYS], [None] * len(toolcfg.MACHINE_KEYS))


class Patterns(unittest.TestCase):
    def setUp(self):
        self.dir = pathlib.Path(tempfile.mkdtemp())
        (self.dir / "patterns.json").write_text(json.dumps(
            {"projects": {"tc": None, "sp": None}, "fixtures": [], "default_projects": ["tc"]}), encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_local_paths_used(self):
        (self.dir / "patterns.local.json").write_text(json.dumps({"projects": {"tc": "W:/tc/decomp"}}),
                                                      encoding="utf-8")
        spec = toolcfg.patterns(self.dir)
        self.assertEqual(spec["projects"], {"tc": "W:/tc/decomp", "sp": None})
        import regress
        a = type("A", (), {"projects": "tc", "only": None, "sample": 1, "list": None})()
        self.assertEqual(regress.select(spec, a), {"tc": []})

    def test_unset_project_stops(self):
        import regress
        a = type("A", (), {"projects": "tc", "only": None, "sample": 1, "list": None})()
        with self.assertRaises(SystemExit) as e:
            regress.select(toolcfg.patterns(self.dir), a)
        self.assertIn("patterns.local.json", str(e.exception.code))

    def test_tracked_patterns_portable(self):
        spec = json.loads((TOOL / "tests" / "patterns.json").read_text(encoding="utf-8"))
        self.assertTrue(spec["projects"])
        self.assertEqual({v for v in spec["projects"].values()}, {None})

    def test_workspace_fixtures(self):
        ws = self.dir / "ws"
        (ws / "regress").mkdir(parents=True)
        (ws / "regress" / "fixtures.json").write_text(json.dumps({"fixtures": [
            {"id": "x-ref-8c010000", "pattern": "reference", "addr": "8c010000", "pass": "x.pass.c", "fail": None}]}),
            encoding="utf-8")
        (self.dir / "patterns.local.json").write_text(json.dumps({"projects": {"tc": str(ws)}}), encoding="utf-8")
        fx = toolcfg.patterns(self.dir)["fixtures"]
        self.assertEqual(len(fx), 1)
        self.assertEqual(fx[0]["project"], "tc")
        self.assertEqual(pathlib.Path(fx[0]["pass"]), ws / "regress" / "x.pass.c")
        self.assertIsNone(fx[0]["fail"])

    def test_no_game_fixtures_tracked(self):
        """Fixtures are game-derived C: they live in each workspace's regress/, never in DecompPipeline."""
        spec = json.loads((TOOL / "tests" / "patterns.json").read_text(encoding="utf-8"))
        self.assertEqual(spec["fixtures"], [])
        rel = [pathlib.Path(f).relative_to(TOOL).as_posix() for f in tracked_files(TOOL)]
        self.assertEqual([f for f in rel if f.endswith(".c") and not f.startswith(("targets/", "port/"))], [])
        # port/ is the game-free port kit: its C may hold no guest code address, game name or game.h runtime include
        # (tests/test_port_kit.py, the same scan)
        import test_port_kit
        self.assertEqual(test_port_kit.game_material(test_port_kit.kit_files()), [])


class NoMachinePaths(unittest.TestCase):
    def test_tracked_files_clean(self):
        files = tracked_files(TOOL)
        self.assertGreater(len(files), 200)
        self.assertEqual(machine_paths(files), [])

    def test_a_machine_path_is_found(self):
        d = pathlib.Path(tempfile.mkdtemp())
        try:
            (d / "core").mkdir()
            (d / "core" / "x.py").write_text('HOME = "' + "C" + ':/Projects/Game"\nOK = "relative/path"\n', encoding="utf-8")
            (d / "config.local.json").write_text('{"vcvars": "C:/VS/vcvars64.bat"}', encoding="utf-8")  # ignored
            (d / "README.md").write_text("Example: `C:\\Tools\\ghidra` (an example path)\n", encoding="utf-8")
            hits = machine_paths(tracked_files(d))
            self.assertEqual([(h[0].name, h[1]) for h in hits], [("x.py", 1)])
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
