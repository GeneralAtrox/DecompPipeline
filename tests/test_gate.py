"""core/gate.py, the watcher gate's check 4, the broken control. A workspace tools/game_setup.py makes has no
broken_control (only "references": []): the gate raised KeyError on it, and skipped the check without a word when the
control's tests were not regenerated. Now a control that is not set or cannot run fails the check, its reason in the
gate's log and its checks, so no change is promoted unproven. No Ghidra, no compiler: the trial and the judge are fakes.
Run: python -W error tests/test_gate.py
"""
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from test_sync import TMP  # noqa: E402  (sets DECOMP_PROJECT when run alone)

from core import gate  # noqa: E402

REF, TARGET, NAME = "8c010000", "8c020000", "broken control still fails"
BROKEN = f"{REF}_broken.c"


class BrokenControl(unittest.TestCase):
    def regression(self, cfg, trial_fails=()):
        """gate.regression over cfg's references with fake trials and judge: ({check: (ok, detail)}, log, judged)."""
        tmp, logs, judged = pathlib.Path(tempfile.mkdtemp(prefix="gate", dir=TMP)), [], []

        def trial(cfg, addr, ws, out, stop_flag=None):
            if addr in trial_fails:
                raise RuntimeError("Ghidra timed out")
            (out / "harness").mkdir(parents=True)
            (out / "harness" / "vectors.txt").write_text("V 0 dev\nE\n", encoding="utf-8")
            return {}, {"blocks_hit_all": 3}

        def run_check(cfg, addr, c_file, **kw):
            judged.append(pathlib.Path(c_file).name)
            bad = c_file.endswith(BROKEN)
            return {"verdict": "FAIL" if bad else "PASS", "failing": 2 if bad else 0, "total": 8}

        o = mock.Mock(cfg=cfg, ledger={"functions": {}})
        with mock.patch.object(gate, "trial", trial), mock.patch.object(gate.check, "run_check", run_check):
            checks = gate.regression(cfg, o, TARGET, tmp, tmp, None, logs.append)
        return {c[0]: tuple(c[1:]) for c in checks}, logs, judged

    def test_game_setup_workspace(self):  # no broken_control: a failed check saying so, not a KeyError
        got, logs, _ = self.regression({"references": []})
        self.assertFalse(got[NAME][0])
        self.assertIn("project.json has no broken_control", got[NAME][1])
        self.assertTrue(any("[WATCH] broken control not run" in x for x in logs), logs)

    def test_control_still_fails(self):
        got, logs, judged = self.regression({"references": [REF], "broken_control": [REF, BROKEN]})
        self.assertEqual(got[NAME], (True, "2/8 differ"))
        self.assertEqual(got[f"regression {REF}: accepted C still passes"], (True, "0/8 differ"))
        self.assertIn(BROKEN, judged)
        self.assertFalse(any("not run" in x for x in logs), logs)

    def test_control_that_cannot_run(self):  # each a failed check with its reason, never a silent skip
        got, _, judged = self.regression({"references": [REF], "broken_control": [REF, BROKEN]}, trial_fails=(REF,))
        self.assertFalse(got[NAME][0])
        self.assertIn("its regression trial failed", got[NAME][1])
        self.assertNotIn(BROKEN, judged)
        got, _, _ = self.regression({"references": [], "broken_control": [REF, BROKEN]})
        self.assertEqual(got[NAME][0], False)
        self.assertIn("not in project.json references", got[NAME][1])
        got, _, _ = self.regression({"references": [REF], "broken_control": REF})
        self.assertIn("is not [ADDR, FILE in reference/]", got[NAME][1])
        with mock.patch.object(gate.check, "run_check", side_effect=OSError("no compiler")):
            check = gate.control_check({"references": [REF], "broken_control": [REF, BROKEN]}, self.tmp_with_tests(),
                                       lambda m: None)
        self.assertEqual(check[:2], (NAME, False))
        self.assertIn("its check crashed: OSError: no compiler", check[2])

    def tmp_with_tests(self):
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="gate", dir=TMP))
        (tmp / REF / "harness").mkdir(parents=True)
        (tmp / REF / "harness" / "vectors.txt").write_text("V 0 dev\nE\n", encoding="utf-8")
        return tmp


if __name__ == "__main__":
    unittest.main()
