"""tools/dashboard.py: the page shows only what an agent added (docs/DASHBOARD.md); nothing is found by itself. An
added workspace shows wherever it lives, its catalog's functions coloured by the translator's verdicts when it has no
ledger; a game made outside the pipeline shows from its progress.json alone. Run: python tests/test_dashboard.py
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


class Added(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = pathlib.Path(self.tmp.name)
        self.old = os.environ.get("LOCALAPPDATA")
        os.environ["LOCALAPPDATA"] = str(self.d / "appdata")

    def tearDown(self):
        if self.old is None:
            os.environ.pop("LOCALAPPDATA")
        else:
            os.environ["LOCALAPPDATA"] = self.old
        self.tmp.cleanup()

    def listed(self):
        return json.loads(dashboard.project_list().read_text(encoding="utf-8"))["projects"]

    def test_added_workspace_without_ledger(self):
        game = self.d / "elsewhere" / "MyGame"
        ws = game / "decomp"
        (ws / "state").mkdir(parents=True)
        (ws / "project.json").write_text(json.dumps({"target": "dreamcast_sh4"}), encoding="utf-8")
        (ws / "state" / "catalog.json").write_text(json.dumps({"functions": [
            {"addr": "8c010000", "bytes": 40}, {"addr": "8c010040", "bytes": 8}, {"addr": "8c020000", "bytes": 4}]}),
            encoding="utf-8")
        (ws / "state" / "translator_verdicts.json").write_text(json.dumps(
            {"functions": {"8c010000": "PASS", "8c010040": "FAIL"}}), encoding="utf-8")
        dashboard.add(game, self.d / "projects")
        dashboard.add(game, self.d / "projects")  # twice: listed once, progress.json kept
        self.assertEqual(self.listed(), [{"folder": str(game.resolve()), "workspaces": ["decomp"]}])
        self.assertIn("now", json.loads((game / "progress.json").read_text(encoding="utf-8")))
        games = dashboard.collect(self.listed(), self.d / "projects")
        self.assertEqual([g["game"] for g in games], ["MyGame"])
        self.assertEqual(games[0]["console"], "Dreamcast")
        c = games[0]["counts"]
        self.assertEqual((c["matched"][0], c["progress"][0], c["todo"][0]), (1, 1, 1))

    def test_game_without_workspace_needs_progress_console(self):
        port = self.d / "Xbox" / "Port"
        port.mkdir(parents=True)
        with self.assertRaises(SystemExit):  # nothing to show: refused, not listed
            dashboard.add(port, self.d)
        self.assertFalse(dashboard.project_list().exists())
        (port / "progress.json").write_text(json.dumps(
            {"console": "Xbox", "road": [{"name": "Disc", "state": "done"}]}), encoding="utf-8")
        dashboard.add(port, self.d)
        games = dashboard.collect(self.listed(), self.d)
        self.assertEqual([(g["game"], g["console"], g["units"]) for g in games], [("Port", "Xbox", [])])
        self.assertEqual(games[0]["progress"]["road"][0]["name"], "Disc")
        self.assertEqual(dashboard.remove(port), 1)
        self.assertEqual(self.listed(), [])

    def test_scope_function_without_a_ledger_row_counts(self):
        ws = self.d / "G" / "decomp"
        (ws / "state").mkdir(parents=True)
        (ws / "project.json").write_text(json.dumps({"target": "dreamcast_sh4", "game_end": "8c020000"}),
                                         encoding="utf-8")
        (ws / "state" / "catalog.json").write_text(json.dumps({"functions": [
            {"addr": "8c010000", "bytes": 8}, {"addr": "8c010040", "bytes": 8},
            {"addr": "8c010080", "bytes": 8, "hw_refs": ["a05f6800"]}, {"addr": "8c030000", "bytes": 8}]}),
            encoding="utf-8")  # 8c030000: SDK, past game_end
        (ws / "state" / "ledger.json").write_text(json.dumps({"functions": {"8c010000": {"status": "matched"}}}),
                                                  encoding="utf-8")
        c = dashboard.workspace(ws)["counts"]
        self.assertEqual((c["matched"][0], c["todo"][0], c["blocked"][0]), (1, 1, 1))

    def test_unlisted_folders_are_not_shown(self):
        other = self.d / "Saturn" / "Other"
        other.mkdir(parents=True)
        (other / "progress.json").write_text(json.dumps({"console": "Saturn"}), encoding="utf-8")
        self.assertEqual(dashboard.collect([], self.d), [])


if __name__ == "__main__":
    unittest.main()
