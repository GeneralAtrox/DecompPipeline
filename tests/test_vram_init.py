"""game.json "vram_init" (port/pvr/pvr_state.c, PORT_VRAM_INIT in the generated port_config.h): bytes set in texture memory
before the game's first read or upload. Synthetic data, no workspace.
Nearest existing: tests/test_port_kit.py GameConfig - same fixture, split out because that file is at its size cap.
"""
import json
import pathlib
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tests"))
from test_port_kit import GAME, game_config  # noqa: E402


class VramInit(unittest.TestCase):
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
        cfg = self.load()
        self.assertEqual(cfg["vram_init"], [])  # texture memory starts zeroed unless the game says
        vi = self.load(vram_init={"0": "FFff", "1000": "01"})
        self.assertEqual(vi["vram_init"], [(0, "ffff"), (0x1000, "01")])
        self.assertIn('#define PORT_VRAM_INIT {0x0u, "ffff"}, {0x1000u, "01"}, {0, 0}', game_config.port_config_h(vi))
        self.assertNotIn("PORT_VRAM_INIT", game_config.port_config_h(cfg))

    def test_refused(self):
        for over in ({"vram_init": ["ff"]}, {"vram_init": {"0": "f"}}, {"vram_init": {"zz": "ff"}},
                     {"vram_init": {"7fffff": "ffff"}}):
            with self.subTest(over=over), self.assertRaises(SystemExit):
                self.load(**over)


if __name__ == "__main__":
    unittest.main()
