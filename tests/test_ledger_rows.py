"""The ledger's save (Orchestrator.save, core/filelock.dumps_rows): one line per function, json's C encoder.
1. dumps_rows reads back as the same data (key order included) for ledgers with and without functions, other
   top-level keys, nested records, non-ASCII text and special floats, as json.dumps(indent=2) did;
2. every function is on a line of its own, and nothing else is split over lines;
3. Orchestrator.save writes that text, atomically, under the ledger's file lock.
Run: python -W error tests/test_ledger_rows.py (also from test_tooling.py).
"""
import json
import pathlib
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from test_sync import TMP  # noqa: E402  (sets DECOMP_PROJECT when run alone)

from core import filelock, orch, paths  # noqa: E402

LEDGER = {
    "loop": {"running": False, "note": "Spawn ’s été \"quoted\"\n2nd line"},
    "functions": {
        "8c010000": {"addr": "8c010000", "status": "matched", "attempts": [
            {"n": 1, "cost_usd": 0.0046, "first_diffs": [{"diffs": ["r0: 1 != 2"]}], "coverage": None}],
            "score": float("inf"), "oracle": {"dev_valid": 48, "invalid": {}}},
        "8c010100": {"addr": "8c010100", "status": "queued", "attempts": [], "requeued": "a, b: {c}"},
        "8c010200": {"addr": "8c010200", "status": "failed", "attempts": [{"n": 1, "lint": [], "x": -0.0}]},
    },
    "watcher": {"queue": [], "seen": {"8c010000": 1}},
}


class Rows(unittest.TestCase):
    def test_round_trip(self):
        for data in (LEDGER, {"functions": {}}, {"loop": {}, "functions": {}, "watcher": None}, {"functions": {
                "8c000000": {}}}, {}):
            text = filelock.dumps_rows(data)
            back = json.loads(text)
            self.assertEqual(json.dumps(back, indent=2), json.dumps(data, indent=2))  # the same, inf and order too
            self.assertEqual(list(back), list(data))

    def test_one_line_per_function(self):
        lines = filelock.dumps_rows(LEDGER).splitlines()
        rows = [x for x in lines if x.startswith('  "8c')]
        self.assertEqual(len(rows), 3)
        for a, r in LEDGER["functions"].items():
            line = next(x for x in rows if x.startswith(f'  "{a}": '))
            self.assertEqual(json.loads(line.split(": ", 1)[1].rstrip(",")), r)
        self.assertEqual(len(lines), 3 + 3 + 3)  # braces, the three other keys' lines, the functions' lines
        self.assertTrue(all("\n" not in json.dumps(v) for v in LEDGER.values()))

    def test_save(self):
        tmp = tempfile.TemporaryDirectory(dir=TMP)
        self.addCleanup(tmp.cleanup)
        led = pathlib.Path(tmp.name) / "state" / "ledger.json"
        fake = mock.Mock(lock=threading.RLock(), ledger=LEDGER)
        with mock.patch.object(paths, "LEDGER", led):
            orch.Orchestrator.save(fake)
        self.assertEqual(led.read_text(encoding="utf-8"), filelock.dumps_rows(LEDGER))
        self.assertEqual(json.dumps(paths.read_json(led)), json.dumps(LEDGER))
        self.assertEqual(sorted(p.name for p in led.parent.iterdir()), ["ledger.json", "ledger.json.lock"])


if __name__ == "__main__":
    unittest.main()
