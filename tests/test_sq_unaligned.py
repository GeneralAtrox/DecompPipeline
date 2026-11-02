"""core/sq_unaligned: off by default, moves only window pointers, keeps whole vector blocks; the translator's queue
keeps the stream's earlier stores below a pointer inside its line."""
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "targets" / "dreamcast_sh4" / "translator")]
from core import sq_unaligned as su  # noqa: E402
from sh4translate.sqmodel import SQ_HELPERS, sq_callee_helpers  # noqa: E402

VEC = ("V 0 dev\nR 0 0 0 0\nG 00000000 00000000 00000000 00000000 00000000 00000000 e0c00160\nE\n"
       "V 1 hidden\nR 0 0 0 0\nG 00000000 00000000 00000000 00000000 00000000 00000000 8cc00160\nE\n"
       "V 2 dev\nR 0 0 0 0\nG 00000000 00000000 00000000 00000000 00000000 00000000 e0c00180\nE\n")


class Tests(unittest.TestCase):
    def test_off_by_default(self):
        self.assertFalse(su.on({}))
        self.assertFalse(su.on({"sq_unaligned": False, "target": "saturn_sh2"}))
        self.assertTrue(su.on({"sq_unaligned": True, "target": su.TARGET}))
        with self.assertRaises(ValueError):
            su.on({"sq_unaligned": True, "target": "saturn_sh2"})

    def test_off_returns_the_result_untouched(self):
        r = {"verdict": "PASS", "stage": "compare"}
        self.assertIs(su.judge({}, "8c000000", "x.c", ROOT, (), None, (), False, False, {"sq_window": "r14"}, None, r), r)

    def test_private_harness_and_failure_are_left_alone(self):
        cfg = {"sq_unaligned": True, "target": su.TARGET}
        p, f = {"verdict": "PASS"}, {"verdict": "FAIL"}
        self.assertIs(su.judge(cfg, "8c000000", "x.c", ROOT, (), None, (), False, False, {"sq_window": "r14"}, ROOT, p), p)
        self.assertIs(su.judge(cfg, "8c000000", "x.c", ROOT, (), None, (), False, False, {"sq_window": "r14"}, None, f), f)
        self.assertIs(su.judge(cfg, "8c000000", "x.c", ROOT, (), None, (), False, False, {}, None, p), p)

    def test_window_index(self):
        self.assertEqual(su.window_index({"sq_window": "r14"}), 6)
        self.assertIsNone(su.window_index({"sq_window": "r4"}))
        self.assertIsNone(su.window_index({}))

    def test_moves_only_window_pointers_inside_the_line(self):
        text, ids = su.move_vectors(VEC, 6)
        self.assertEqual(ids, [0, 2])
        self.assertIn("e0c00164", text)     # 4 * (1 + 0 % 7)
        self.assertIn("8cc00160", text)     # RAM path stays
        self.assertIn("e0c0018c", text)     # 4 * (1 + 2 % 7)

    def test_keep_blocks(self):
        kept = su.keep_blocks("P 00040001\nS 14\n" + VEC, [2])
        self.assertEqual(kept.count("V "), 1)
        self.assertTrue(kept.startswith("P 00040001\nS 14\nV 2 dev"))
        self.assertTrue(kept.endswith("E\n"))
        self.assertIn("V 1 dev", su.keep_blocks(VEC, [1]))

    def test_queue_holds_bytes_below_the_pointer(self):
        for h in (SQ_HELPERS, sq_callee_helpers()):
            text = "\n".join(h)
            self.assertIn("for (j = 0; j < (p & 31u); j++)", text)
            self.assertNotIn("p &= 0xffffffe0u", text)


if __name__ == "__main__":
    unittest.main()
