"""Packs a game builds from its own art and the disc (port/tools/pack_builds.py, game.json pack_builds).
1. a pack is built into <build>/packs/NAME with the kit's tools and the disc, and a second run with nothing changed
   doesn't run the tool again;
2. a changed input file, tool or disc builds it again;
3. a tool that fails keeps the pack already there and writes its output to packs/NAME.log; with no pack there, none
   appears (no half-built folder);
4. no disc, or no tool: reported, nothing built, and nothing raises; a pack not built leaves its reason in
   packs/NAME.skipped for the game log, removed once it is built;
5. game.json pack_builds: names and specs are checked (no on/off, both keys), paths resolve against game.json;
6. build.py wires it in after the compile.
Synthetic tools. Run: python -W error tests/test_pack_builds.py.
Nearest existing: tests/test_port_kit.py GameConfig - same game.json fixture, split out because that file is at its size cap.
"""
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "port" / "tools"))
from test_port_kit import GAME, game_config  # noqa: E402

import pack_builds  # noqa: E402

TOOL = '''import argparse, pathlib, sys
ap = argparse.ArgumentParser()
for k in ("--disc", "--kit-tools", "--out"):
    ap.add_argument(k, required=True)
a = ap.parse_args()
src = pathlib.Path(__file__).parent / "art.txt"
if src.read_text() == "fail":
    print("refusing"); sys.exit(3)
out = pathlib.Path(a.out); out.mkdir(parents=True)
(out / "index.txt").write_text("0123456789abcdef a.png\\n")
(out / "a.png").write_text(src.read_text() + "|" + pathlib.Path(a.disc).read_text())
print("made", a.kit_tools)
'''


def run(cmd, timeout):
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return p.returncode, p.stdout + p.stderr


class PackBuilds(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.art = self.root / "art"
        self.art.mkdir()
        (self.art / "make.py").write_text(TOOL)
        (self.art / "art.txt").write_text("v1")
        self.disc = self.root / "disc.cue"
        self.disc.write_text("D1")
        self.bdir = self.root / "build" / "x86"
        self.bdir.mkdir(parents=True)
        self.spec = {"tool": self.art / "make.py", "inputs": self.art}
        self.runs = 0

    def tearDown(self):
        self.tmp.cleanup()

    def build(self, disc=None):
        def counted(cmd, t):
            self.runs += 1
            return run(cmd, t)
        return pack_builds.build_one("HD art", self.spec, str(disc or self.disc), self.bdir, counted, lambda s: None)

    def image(self):
        return (self.bdir / "packs" / "HD art" / "a.png").read_text()

    def test_built_then_up_to_date(self):
        self.assertEqual(self.build(), "built")
        self.assertEqual(self.image(), "v1|D1")
        self.assertEqual(self.build(), "up to date")
        self.assertEqual(self.runs, 1)
        self.assertFalse((self.bdir / "packs" / "HD art.new").exists())

    def test_changes_build_again(self):
        self.build()
        (self.art / "art.txt").write_text("v2")
        self.assertEqual(self.build(), "built")
        self.assertEqual(self.image(), "v2|D1")
        self.disc.write_text("D22")
        self.assertEqual(self.build(), "built")
        self.assertEqual(self.image(), "v2|D22")
        (self.art / "make.py").write_text(TOOL + "\n# changed\n")
        self.assertEqual(self.build(), "built")
        self.assertEqual(self.runs, 4)

    def test_work_files_and_caches_are_not_inputs(self):
        self.build()
        for d in ("_work", "__pycache__"):
            (self.art / d).mkdir()
            (self.art / d / "x").write_text("scratch")
        self.assertEqual(self.build(), "up to date")

    def test_failure_keeps_the_old_pack(self):
        self.build()
        (self.art / "art.txt").write_text("fail")
        r = self.build()
        self.assertIn("exited 3", r)
        self.assertIn("kept", r)
        self.assertEqual(self.image(), "v1|D1")
        self.assertIn("refusing", (self.bdir / "packs" / "HD art.log").read_text())
        self.assertFalse((self.bdir / "packs" / "HD art.new").exists())

    def test_failure_with_no_pack_leaves_none(self):
        (self.art / "art.txt").write_text("fail")
        self.assertIn("there is no pack", self.build())
        self.assertFalse((self.bdir / "packs" / "HD art").exists())

    def test_no_disc_or_tool(self):
        self.assertIn("no disc image", self.build(disc=self.root / "missing.cue"))
        self.spec["tool"] = self.art / "gone.py"
        self.assertIn("not built", self.build())
        self.assertEqual(self.runs, 0)
        cfg = {"pack_builds": {"HD art": self.spec}}
        self.assertEqual(pack_builds.build_all(cfg, self.bdir, None, run, lambda s: None)[0][1][:9], "not built")


    def test_skipped_note(self):
        cfg, note = {"pack_builds": {"HD art": self.spec}}, self.bdir / "packs" / "HD art.skipped"
        (self.art / "art.txt").write_text("fail")
        pack_builds.build_all(cfg, self.bdir, str(self.disc), run, lambda s: None)
        self.assertIn("exited 3 (there is no pack", note.read_text(encoding="utf-8"))
        (self.art / "art.txt").write_text("v1")
        self.assertEqual(pack_builds.build_all(cfg, self.bdir, str(self.disc), run, lambda s: None)[0][1], "built")
        self.assertFalse(note.exists())


class GameJson(unittest.TestCase):
    def load(self, pb):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        d = pathlib.Path(tmp.name)
        (d / "src").mkdir()
        (d / "src" / "own.c").write_text("void Fx_Hw(void) {}\n", encoding="utf-8")
        (d / "game.json").write_text(json.dumps(dict(GAME, pack_builds=pb)), encoding="utf-8")
        return game_config.load(d), d.resolve()

    def test_paths_resolve(self):
        cfg, d = self.load({"HD art": {"tool": "../art/hd/tools/hd_pack.py", "inputs": "../art/hd"}})
        self.assertEqual(cfg["pack_builds"]["HD art"]["tool"], (d / "../art/hd/tools/hd_pack.py").resolve())
        self.assertEqual(cfg["pack_builds"]["HD art"]["inputs"], (d / "../art/hd").resolve())

    def test_bad_specs(self):
        for pb in ({"off": {"tool": "a", "inputs": "b"}}, {"x/y": {"tool": "a", "inputs": "b"}},
                   {"HD": {"tool": "a"}}, {"HD": "a.py"}, ["a.py"]):
            with self.subTest(pb=pb), self.assertRaises(game_config.ConfigError):
                self.load(pb)


class Wired(unittest.TestCase):
    def test_build_py_runs_it_after_the_compile(self):
        src = next((pathlib.Path(__file__).resolve().parents[1] / "port" / "tools").glob("buil?.py")).read_text(encoding="utf-8")
        self.assertIn("import pack_builds", src)
        self.assertLess(src.index("raise SystemExit(1)\n    disc ="), src.index("pack_builds.build_all("))


if __name__ == "__main__":
    unittest.main()
