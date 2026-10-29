"""tools/dashboard.py: a game made outside the pipeline shows from its progress.json alone (docs/DASHBOARD.md), and a
workspace's game folder is not listed twice; an enrolled workspace shows wherever it lives, its catalog's functions
coloured by the translator's verdicts when it has no ledger. Run: python tests/test_dashboard.py
"""
import json
import os
import pathlib
import sys
import tempfile
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL / "tools"))
import dashboard  # noqa: E402


class OwnProjects(unittest.TestCase):
    def test_progress_json_with_console_is_a_game(self):
        with tempfile.TemporaryDirectory() as d:
            base = pathlib.Path(d)
            for rel, pr in {"Xbox/Port": {"console": "Xbox", "road": [{"name": "Disc", "state": "done"}]},
                            "Dc/OnPipeline": {"console": "Dreamcast"},
                            "Notes": {"now": "no console: not a game"},
                            "a/b/c/TooDeep": {"console": "PC"},
                            "DecompPipeline-wt/G": {"console": "PC"}}.items():
                (base / rel).mkdir(parents=True)
                (base / rel / "progress.json").write_text(json.dumps(pr), encoding="utf-8")
            got = list(dashboard.own_projects(base, {(base / "Dc/OnPipeline").resolve()}))
        self.assertEqual([g["game"] for g in got], ["Port"])
        self.assertEqual(got[0]["console"], "Xbox")
        self.assertEqual(got[0]["progress"]["road"][0]["name"], "Disc")
        self.assertEqual(got[0]["units"], [])


class Enrol(unittest.TestCase):
    def test_enrolled_workspace_without_ledger(self):
        with tempfile.TemporaryDirectory() as d:
            d = pathlib.Path(d)
            ws = d / "elsewhere" / "MyGame" / "decomp"
            (ws / "state").mkdir(parents=True)
            (ws / "project.json").write_text(json.dumps({"target": "dreamcast_sh4"}), encoding="utf-8")
            (ws / "state" / "catalog.json").write_text(json.dumps({"functions": [
                {"addr": "8c010000", "bytes": 40}, {"addr": "8c010040", "bytes": 8}, {"addr": "8c020000", "bytes": 4}]}),
                encoding="utf-8")
            (ws / "state" / "translator_verdicts.json").write_text(json.dumps(
                {"functions": {"8c010000": "PASS", "8c010040": "FAIL"}}), encoding="utf-8")
            old = os.environ.get("LOCALAPPDATA")
            os.environ["LOCALAPPDATA"] = str(d / "appdata")
            try:
                dashboard.enrol(ws)
                dashboard.enrol(ws)  # twice: listed once, progress.json kept
                self.assertEqual(json.loads(dashboard.registry().read_text(encoding="utf-8"))["workspaces"],
                                 [str(ws.resolve())])
                self.assertIn("now", json.loads((ws.parent / "progress.json").read_text(encoding="utf-8")))
                games = dashboard.collect(d / "projects")
            finally:
                if old is None:
                    os.environ.pop("LOCALAPPDATA")
                else:
                    os.environ["LOCALAPPDATA"] = old
        g = [w for w in games if w["game"] == "MyGame"]
        self.assertEqual(len(g), 1)
        self.assertEqual(g[0]["console"], "Dreamcast")
        c = g[0]["counts"]
        self.assertEqual((c["matched"][0], c["progress"][0], c["todo"][0]), (1, 1, 1))


if __name__ == "__main__":
    unittest.main()
