"""tools/dashboard.py: a game made outside the pipeline shows from its progress.json alone (docs/DASHBOARD.md), and a
workspace's game folder is not listed twice. Run: python tests/test_dashboard.py
"""
import json
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


if __name__ == "__main__":
    unittest.main()
