"""The strict judge's exit frame when the original's record is short or missing (core/strict_judge.py): planted wrong
answers that passed while a shorter record was taken as 0 and a missing one was skipped. Beside test_strict_judge.py,
which is at its 400-line cap, reusing its vector builder (Judge.sh).
Run: python -W error tests/test_strict_judge_exit.py
"""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import test_strict_judge as tsj  # noqa: E402  (as a module: its test classes stay out of this run)

OK = ["CS 0 1 4433221100000000"]
ZEROS = "XR 8cffe000 00000001 0c001234 00000003 0 0 0 0 0 0 0 0"


class ExitRecord(unittest.TestCase):
    setUp = tsj.Judge.setUp
    sh = tsj.Judge.sh

    def test_a_short_record_is_not_a_pass(self):
        short = self.sh(OK, ZEROS, exr=tsj.XR_SH[:40])  # the original's record stops at r11
        self.assertEqual([d.split(":")[0] for d in short], ["r12 at exit", "r13 at exit", "r14 at exit", "fr12 at exit",
                                                            "fr13 at exit", "fr14 at exit", "fr15 at exit"])
        self.assertIn("the original's record has no value for it", short[0])

    def test_a_missing_record_is_reported(self):
        rep = []
        self.assertEqual(self.sh(OK, ZEROS, exr=None, report=rep), [])  # no record to enforce: reported
        self.assertEqual(rep, ["exit_frame (report only): the exit frame: the original's run recorded none, so it was "
                               "not compared"])
        self.assertEqual(self.sh(OK, None, exr=None, report=rep), [])  # neither side has one: nothing to say
        self.assertEqual(len(rep), 1)


if __name__ == "__main__":
    unittest.main()
