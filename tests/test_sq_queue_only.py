"""game.json "sq_queue_only" (port/runtime/tx/tx_rt.c, PORT_SQ_QUEUE_ONLY in the generated port_config.h): a translated store into the
store-queue window stays in the queue the translated C models. Synthetic data, no workspace.
Nearest existing: tests/test_vram_init.py - same fixture.
"""
import json
import pathlib
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tests"))
from test_port_kit import GAME, game_config  # noqa: E402


class SqQueueOnly(unittest.TestCase):
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
        self.assertFalse(cfg["sq_queue_only"])  # stores into the window land in RAM unless the game says
        sq = self.load(sq_queue_only=True)
        self.assertTrue(sq["sq_queue_only"])
        self.assertIn("#define PORT_SQ_QUEUE_ONLY 1", game_config.port_config_h(sq))
        self.assertNotIn("PORT_SQ_QUEUE_ONLY", game_config.port_config_h(cfg))

    def test_refused(self):
        for over in ({"sq_queue_only": "yes"}, {"sq_queue_only": 1}, {"sq_queue_only": [True]}):
            with self.subTest(over=over), self.assertRaises(SystemExit):
                self.load(**over)


if __name__ == "__main__":
    unittest.main()
