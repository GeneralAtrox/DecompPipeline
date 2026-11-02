"""The game log's map side (port/base/game_log.c is checked by playing, see port/README.md "Game log"): scene_map.py
making per-player fields from "index", fields from "expr", the "log" flag, totals ("counts") and its planted errors,
and game_setup.py's starter map. Synthetic data; no game, no compiler.
Run: python -W error tests/test_game_log.py
"""
import json
import pathlib
import sys
import tempfile
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL / "port" / "tools"))
sys.path.insert(0, str(TOOL / "tools"))

import scene_map  # noqa: E402
import game_setup  # noqa: E402

GAME_H = "typedef struct { int life; } rdg_g_fighters_t[4];\ntypedef int rdg_g_mode_t;\n"


class SceneMapLog(unittest.TestCase):
    def build(self, m):
        d = pathlib.Path(tempfile.mkdtemp())
        (d / "scene_dump.json").write_text(json.dumps(m), encoding="utf-8")
        return scene_map.scene_map_c({"dir": d}, GAME_H, d)

    def test_index_expr_log_counts(self):
        c = self.build({"log_every": 300, "fields": [
            {"key": "mode", "global": "g_mode", "log": True},
            {"key": "p{i}_life", "expr": "g_fighters[{i}].life", "type": "s32", "index": 2, "log": True},
            {"key": "quiet", "expr": "g_fighters[3].life", "type": "s32"}],
            "counts": [{"key": "alive", "of": ["p0_life", "p1_life"]}]})
        self.assertIn('{"mode", &g_mode, NULL, 0, 4, SCENE_INT, NULL, 0, 1},', c)
        self.assertIn('{"p1_life", &(g_fighters[1].life), NULL, 0, 4, SCENE_INT, NULL, 0, 1},', c)
        self.assertIn('{"quiet", &(g_fighters[3].life), NULL, 0, 4, SCENE_INT, NULL, 0, 0},', c)
        self.assertIn('{"alive", count_0, 2},', c)
        self.assertIn("scene_count_count = 1;", c)
        self.assertIn("scene_log_every = 300;", c)

    def test_planted_errors(self):
        bad = [{"fields": [{"key": "a", "expr": "g_nope.x", "type": "s32"}]},
               {"fields": [{"key": "a", "expr": "g_mode", "type": "s32", "deref": [4]}]},
               {"fields": [{"key": "a", "expr": "g_mode"}]},
               {"fields": [{"key": "a", "global": "g_mode"}, {"key": "a", "global": "g_mode"}]},
               {"fields": [{"key": "a", "global": "g_mode"}], "counts": [{"key": "n", "of": ["zz"]}]},
               {"fields": [{"key": "a", "global": "g_mode"}], "counts": [{"key": "a", "of": ["a"]}]}]
        for m in bad:
            with self.assertRaises(SystemExit, msg=m):
                self.build(m)

    def test_no_map_still_generates(self):
        c = self.build({})
        self.assertIn("scene_field_count = 0;", c)
        self.assertIn("scene_log_every = 0;", c)


class StarterMap(unittest.TestCase):
    def test_starter_is_a_valid_map(self):
        with tempfile.TemporaryDirectory() as t:
            ws = pathlib.Path(t)
            game_setup.write_starter_map(ws)
            m = json.loads((ws / "port_starter" / "scene_dump.json").read_text(encoding="utf-8"))
        self.assertEqual(m["fields"], [])
        self.assertEqual(len(scene_map.expand(m["_example"])), 6)  # the example is what scene_map.py reads
        self.assertTrue(all(f["log"] for f in m["_example"]))


if __name__ == "__main__":
    unittest.main()
