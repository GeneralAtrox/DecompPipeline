"""The build stamp: a real source change is -dirty, files the live line lays in a state/ folder are not."""
import pathlib
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "port" / "tools"))
import game_config


def git(folder, *args):
    subprocess.run(["git", "-C", str(folder), "-c", "user.name=t", "-c", "user.email=t@t", *args], check=True, capture_output=True)


class Stamp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = pathlib.Path(self.tmp.name)
        (self.repo / "port").mkdir()
        (self.repo / "decomp/main/state").mkdir(parents=True)
        (self.repo / "port/game.json").write_text("{}")
        (self.repo / "decomp/main/state/catalog.json").write_text("{}")
        git(self.repo, "init", "-q")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "one")
        self.addCleanup(self.tmp.cleanup)

    def test_clean(self):
        self.assertFalse(game_config.commit(self.repo / "port").endswith("-dirty"))

    def test_laid_state_is_not_dirty(self):
        (self.repo / "decomp/main/state/catalog.json").write_text("{\"laid\": 1}")
        self.assertFalse(game_config.commit(self.repo / "port").endswith("-dirty"))

    def test_source_change_is_dirty(self):
        (self.repo / "port/game.json").write_text("{\"changed\": 1}")
        self.assertTrue(game_config.commit(self.repo / "port").endswith("-dirty"))

    def test_outside_git(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(game_config.commit(d), "unknown")


if __name__ == "__main__":
    unittest.main()
