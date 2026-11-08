"""tools/proof_backlog.py truth (2026-11-07): a matched row counts only while its latest recorded harness verdict is a
PASS on a current harness. Moved from tests/test_batch32.py (Backlog) under its size baseline; the fixtures are
test_batch32's. Synthetic data. Run: python -W error tests/test_ledger_truth.py
"""
import json
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import test_batch32  # noqa: E402  (as a module: its own TestCases are not collected twice here)
from test_batch32 import TOOL, put, summ, workspace  # noqa: E402


class LedgerTruth(unittest.TestCase):
    tool = test_batch32.Backlog.tool

    def test_ledger_truth(self):
        """`truth` (2026-11-07): a matched row counts only while its latest recorded harness verdict is a PASS on a
        current harness; --apply marks the rest unverified (backup first) and keeps another gate's unproven mark."""
        sys.path.insert(0, str(TOOL / "tools"))
        import proof_backlog
        import scorecard_data
        ws = workspace({a: summ(4, 4) for a in ("8c000000", "8c000100", "8c000200", "8c000300", "8c000400",
                                                 "8c000500", "8c000600")})
        led = json.loads((ws / "state/ledger.json").read_text())
        rows = led["functions"]
        for a, r in rows.items():
            r["verdict_detail"] = "PASS on attempt 1"
        rows["8c000100"]["verdict_detail"] = "FAIL: compare 3/96"                      # verdict
        rows["8c000400"]["unproven"] = {"reason": "unlisted call args unchecked"}     # another gate's mark
        rows["8c000500"]["rejudged"] = {"at": "2026-11-07T04:00:00", "verdict": "PASS"}
        put(ws, "state/ledger.json", led)
        put(ws, "state/readable.json", {"8c000200": {"status": "harness_stale", "verified": "2026-10-21T16:29:46"},
                                        "8c000500": {"status": "harness_stale", "verified": "2026-10-21T16:29:46"},
                                        "8c000000": {"status": "readable", "verified": "2026-10-21T16:29:46"}})
        found = proof_backlog.truth(ws, {"8c000300": "old stamp", "8c000100": "old stamp"})
        self.assertEqual(found, {"8c000100": ["verdict", "stale"], "8c000200": ["harness_stale"],
                                 "8c000300": ["stale"], "8c000400": ["marked"]})  # 8c000500: re-judged since
        self.assertEqual(scorecard_data.workspace_metrics(ws, with_scope=False)["matched"], 6)  # the unproven one
        self.assertEqual(proof_backlog.mark_unverified(ws, found, {"8c000300": "old stamp"}), 3)
        self.assertEqual(len(list((ws / "state").glob("ledger.before-truth-*.json"))), 1)
        rows = json.loads((ws / "state/ledger.json").read_text())["functions"]
        self.assertEqual(rows["8c000300"]["unverified"]["reason"], "stale (old stamp)")
        self.assertEqual(rows["8c000100"]["unverified"]["reason"], "verdict, stale (None)")
        self.assertNotIn("unverified", rows["8c000400"])                                 # its own mark kept
        self.assertEqual(rows["8c000400"]["unproven"], {"reason": "unlisted call args unchecked"})
        m = scorecard_data.workspace_metrics(ws, with_scope=False)
        self.assertEqual((m["matched"], m["unverified"]), (3, 4))
        p = self.tool("truth", "--project", ws, "--no-stale")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("4/7 matched rows fail ledger truth; marked 4, verdict 1, harness_stale 1, stale 0 "
                      "(stale not checked)", p.stdout)


if __name__ == "__main__":
    unittest.main()
