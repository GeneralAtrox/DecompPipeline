"""dp_judge's "thin" label: a PASS whose vectors reached under 70% of the original's blocks. A label only, the verdict stays.

Synthetic data. Run: python -W error tests/test_gc_thin.py.
"""
import importlib.util
import pathlib
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[1]
JUDGE = TOOL / "targets" / "gamecube_ppc" / "translator" / "tools" / "dp_judge.py"
spec = importlib.util.spec_from_file_location("gc_dp_judge", JUDGE)
dp_judge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dp_judge)


def cov(hit, total):
    return {"blocks_hit_all": hit, "blocks_total": total}


class Thin(unittest.TestCase):
    def test_floor(self):
        self.assertTrue(dp_judge.thin("PASS", cov(69, 100)))
        self.assertTrue(dp_judge.thin("PASS", cov(0, 5)))
        self.assertFalse(dp_judge.thin("PASS", cov(70, 100)))  # the floor itself is not thin
        self.assertFalse(dp_judge.thin("PASS", cov(7, 10)))
        self.assertFalse(dp_judge.thin("PASS", cov(100, 100)))

    def test_only_a_pass(self):
        for v in ("FAIL", "ERROR", "NOTRANSLATE", "CRASH"):
            self.assertFalse(dp_judge.thin(v, cov(1, 100)), v)

    def test_no_block_data_is_not_called_thin(self):
        self.assertFalse(dp_judge.thin("PASS", None))
        self.assertFalse(dp_judge.thin("PASS", {}))
        self.assertFalse(dp_judge.thin("PASS", cov(None, 100)))
        self.assertFalse(dp_judge.thin("PASS", cov(0, 0)))

    def test_the_verdict_is_the_judges(self):
        s = JUDGE.read_text(encoding="utf-8")
        self.assertIn('"verdict": res["verdict"], "stage": res["stage"]', s)
        self.assertIn('"thin": thin(res["verdict"], res.get("coverage"))', s)


if __name__ == "__main__":
    unittest.main()
