"""The developer-only vector seed offset (core/ghidra.vector_seed, env DP_SEED_OFFSET, prepare_list --seed-offset) and
the Xbox 360 rule that a run whose branch lands below the image is an invalid input, so a function with no other valid
vector is NO_VECTORS and never PASS (the discard is counted in oracle_summary.json "invalid", never in a verdict).
usage: python tests/test_x360_seedoffset.py
"""
import pathlib
import sys
import unittest
from unittest import mock

sys.path[:0] = [str(pathlib.Path(__file__).resolve().parent)]
from test_xbox360_ppc import X360  # noqa: E402
from core import ghidra  # noqa: E402
import x360_check  # noqa: E402


class SeedOffset(unittest.TestCase):
    def test_default_is_the_old_seed(self):
        for a in ("82e40388", "82de7510", "FFFFFFFF", "00000000"):
            self.assertEqual(ghidra.vector_seed(a, {}), int(a, 16) & 0x7FFFFFFF)
            self.assertEqual(ghidra.vector_seed(a, {"DP_SEED_OFFSET": "0"}), int(a, 16) & 0x7FFFFFFF)
            self.assertEqual(ghidra.vector_seed(a, {"DP_SEED_OFFSET": ""}), int(a, 16) & 0x7FFFFFFF)

    def test_offset_changes_it(self):
        self.assertEqual(ghidra.vector_seed("82de7510", {"DP_SEED_OFFSET": "1"}), (0x82de7510 + 1) & 0x7FFFFFFF)
        self.assertEqual(ghidra.vector_seed("82de7510", {"DP_SEED_OFFSET": "0x2"}), (0x82de7510 + 2) & 0x7FFFFFFF)
        self.assertNotEqual(ghidra.vector_seed("82de7510", {"DP_SEED_OFFSET": "1"}), ghidra.vector_seed("82de7510", {}))

    def test_not_a_setting(self):
        src = pathlib.Path(ghidra.__file__).read_text(encoding="utf-8")
        self.assertNotIn("seed_offset", src)   # no project.json / config key reads it


@mock.patch("core.paths.config", return_value={"min_valid_vectors": 16})
class AllDiscarded(unittest.TestCase):
    def test_no_valid_vector_is_no_vectors(self, _):
        r = {"addr": "82e40388", "verdict": "FAIL", "stage": "compare", "lint": [], "build": [], "first_diffs": [],
             "vectors": {s: {"total": 0, "failing": 0} for s in ("dev", "hidden")}, "failing": 0, "total": 0}
        self.assertEqual(x360_check.finish(r, ("dev", "hidden"), {})["verdict"], "NO_VECTORS")

    def test_data_branch_is_invalid_not_refused(self, _):
        o = (X360 / "ghidra_scripts" / "TcOracle.java").read_text(encoding="utf-8")
        self.assertIn("TcRun.intoData(pc)", o)
        self.assertLess(o.index("TcRun.intoData(pc)"), o.index("res.outside = pc;"))


if __name__ == "__main__":
    unittest.main()
