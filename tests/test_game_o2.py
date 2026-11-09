"""game.json "game_o2" (port/tools/build.py, PORT_GAME_O2 in port/CMakeLists.txt): the game's own C at /O2, and a build folder
set up the other way is set up again, since the launcher's folder outlives a game.json change. Synthetic data, no workspace.
Nearest existing: tests/test_sq_queue_only.py - same fixture.
"""
import json
import pathlib
import shutil
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "port" / "tools"))
from test_port_kit import GAME, game_config  # noqa: E402
import build  # noqa: E402


class GameO2(unittest.TestCase):
    def setUp(self):
        self.dir = pathlib.Path(tempfile.mkdtemp())
        (self.dir / "src").mkdir()
        (self.dir / "src" / "own.c").write_text("void Fx_Hw(void) {}\n", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def load(self, **over):
        (self.dir / "game.json").write_text(json.dumps(dict(GAME, **over)), encoding="utf-8")
        return game_config.load(self.dir)

    def test_resolved(self):
        self.assertFalse(self.load()["game_o2"])  # the judge's /O1 unless the game says
        self.assertTrue(self.load(game_o2=True)["game_o2"])

    def test_refused(self):
        for over in ({"game_o2": "yes"}, {"game_o2": 1}):
            with self.subTest(over=over), self.assertRaises(SystemExit):
                self.load(**over)

    def test_build_folder_set_up_the_other_way(self):
        self.assertFalse(build.cached_on(self.dir, "PORT_GAME_O2"))  # never set up
        cache = self.dir / "CMakeCache.txt"
        cache.write_text("PORT_GAME_O2:BOOL=OFF\n", encoding="utf-8")
        self.assertFalse(build.cached_on(self.dir, "PORT_GAME_O2"))
        cache.write_text("PORT_GAME_O2:BOOL=ON\nPORT_ASAN:BOOL=OFF\n", encoding="utf-8")
        self.assertTrue(build.cached_on(self.dir, "PORT_GAME_O2"))
        self.assertFalse(build.cached_on(self.dir, "PORT_ASAN"))  # --asan's folder is set up the same way


if __name__ == "__main__":
    unittest.main()
