"""The ledger's save (Orchestrator.save, core/filelock.dumps_rows): one line per function, json's C encoder.
1. dumps_rows reads back as the same data (key order included) for ledgers with and without functions, other
   top-level keys, nested records, non-ASCII text and special floats, as json.dumps(indent=2) did;
2. every function is on a line of its own, and nothing else is split over lines;
3. Orchestrator.save writes that text, atomically, under the ledger's file lock;
4. processes that each hold the ledger in memory and save it lose nothing (filelock.save_rows, a merge per row): a
   row another process saved since is kept, one it changed is not reverted by a process that left it alone, a row
   this process changed is its own, a removed one stays removed; also with a holder that has no base (an
   object.__new__ Orchestrator). Before, each save wrote the whole in-memory ledger: 3 parallel `decomp.py
   investigate` runs kept 7 of their 21 rows on the Xbox 360 reference run;
5. the same for tools/translate_backlog's O.save, and tools/requeue's offline requeue (filelock.update_json, one line
   per function) keeps a row a holder saved meanwhile, and a holder's later save does not undo the requeue.
Run: python -W error tests/test_ledger_rows.py (also from test_tooling.py).
"""
import json
import pathlib
import sys
import tempfile
import threading
import types
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from test_sync import TMP  # noqa: E402  (sets DECOMP_PROJECT when run alone)

from core import filelock, orch, paths  # noqa: E402

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools"))
import requeue  # noqa: E402
import translate_backlog  # noqa: E402

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
        fake = types.SimpleNamespace(lock=threading.RLock(), ledger=json.loads(json.dumps(LEDGER)))
        with mock.patch.object(paths, "LEDGER", led):
            orch.Orchestrator.save(fake)
        self.assertEqual(led.read_text(encoding="utf-8"), filelock.dumps_rows(LEDGER))
        self.assertEqual(json.dumps(paths.read_json(led)), json.dumps(LEDGER))
        self.assertEqual(sorted(p.name for p in led.parent.iterdir()), ["ledger.json", "ledger.json.lock"])


class Merge(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(dir=TMP)
        self.addCleanup(tmp.cleanup)
        self.led = pathlib.Path(tmp.name) / "state" / "ledger.json"
        self.enterContext(mock.patch.object(paths, "LEDGER", self.led))
        filelock.write_atomic(self.led, filelock.dumps_rows({"loop": False, "functions": {
            "82000000": {"addr": "82000000", "status": "matched"}, "82000100": {"addr": "82000100", "status": "queued"},
            "82000200": {"addr": "82000200", "status": "queued"}}}))

    def holder(self, base=True):
        """A process's Orchestrator: its own copy of the ledger, as loaded (or, base=False, read without a base)."""
        if base:
            data, b = filelock.load_rows(self.led, {"functions": {}})
            return types.SimpleNamespace(lock=threading.RLock(), ledger=data, base=b)
        return types.SimpleNamespace(lock=threading.RLock(), ledger=paths.read_json(self.led))

    def rows(self):
        return {a: r["status"] for a, r in paths.read_json(self.led)["functions"].items()}

    def test_parallel_workers_lose_no_row(self):
        procs = [self.holder() for _ in range(3)]  # three `decomp.py investigate` runs, loaded at once
        for i, o in enumerate(procs):
            a = f"82000{i + 3}00"
            o.ledger["functions"][a] = {"addr": a, "status": "skipped"}
            orch.Orchestrator.save(o)
        self.assertEqual(self.rows(), {"82000000": "matched", "82000100": "queued", "82000200": "queued",
                                       "82000300": "skipped", "82000400": "skipped", "82000500": "skipped"})

    def test_no_base_holder_adds_rows(self):
        a, b = self.holder(base=False), self.holder(base=False)
        a.ledger["functions"]["82000300"] = {"addr": "82000300", "status": "skipped"}
        orch.Orchestrator.save(a)
        b.ledger["functions"]["82000400"] = {"addr": "82000400", "status": "skipped"}
        orch.Orchestrator.save(b)
        self.assertEqual({k: v for k, v in self.rows().items() if v == "skipped"}, {"82000300": "skipped",
                                                                                   "82000400": "skipped"})

    def test_changed_rows_and_removals(self):
        a, b = self.holder(), self.holder()
        held = b.ledger["functions"]["82000100"]  # a reference a worker thread of b holds
        a.ledger["functions"]["82000100"]["status"] = "matched"  # a finishes 82000100
        a.ledger["functions"]["82000200"]["status"] = "failed"
        orch.Orchestrator.save(a)
        b.ledger["functions"]["82000200"]["status"] = "matched"  # b changed 82000200 too: its own (the claim holder)
        del b.ledger["functions"]["82000000"]  # b removes a row (tools/retire's way)
        orch.Orchestrator.save(b)
        self.assertEqual(self.rows(), {"82000100": "matched", "82000200": "matched"})
        self.assertEqual(held["status"], "matched")  # the held row was updated in place
        orch.Orchestrator.save(a)  # a again: it left 82000200 alone since its save, so b's row stays
        self.assertEqual(self.rows()["82000200"], "matched")
        self.assertNotIn("82000000", self.rows())  # removed by b; a left it alone: it stays removed
        self.assertNotIn("82000000", a.ledger["functions"])

    def test_translate_backlog_holders(self):
        a, b = (translate_backlog.O(paths, filelock, {}) for _ in range(2))  # two --record runs at once
        a.rec("82000300")["status"] = "matched"
        a.save()
        b.rec("82000400")["status"] = "matched"
        b.save()
        self.assertEqual({k: v for k, v in self.rows().items() if k > "820002ff"}, {"82000300": "matched",
                                                                                   "82000400": "matched"})

    def test_requeue_and_a_holder(self):
        o = self.holder()  # a running `decomp.py investigate` that loaded before the requeue
        ws = self.led.parent.parent
        self.enterContext(mock.patch.object(paths, "STATE", self.led.parent))
        self.enterContext(mock.patch.object(paths, "MATCHED", ws / "matched"))
        self.enterContext(mock.patch.object(requeue, "server_state", lambda *a, **k: None))
        other = self.holder()
        other.ledger["functions"]["82000300"] = {"addr": "82000300", "status": "skipped"}
        orch.Orchestrator.save(other)  # saved after requeue's first read: requeue re-reads under the lock
        mem = {}
        self.assertEqual(requeue.requeue_offline(ws, mem, ["82000000"], "checker fix"), ["82000000"])
        self.assertEqual(self.rows()["82000300"], "skipped")
        self.assertEqual(self.led.read_text(encoding="utf-8"), filelock.dumps_rows(mem))  # one line per function
        o.ledger["functions"]["82000400"] = {"addr": "82000400", "status": "skipped"}
        orch.Orchestrator.save(o)
        self.assertEqual(self.rows(), {"82000000": "queued", "82000100": "queued", "82000200": "queued",
                                       "82000300": "skipped", "82000400": "skipped"})

    def test_single_writer_text_unchanged(self):
        o = self.holder()
        o.ledger["functions"]["82000100"]["status"] = "running"
        orch.Orchestrator.save(o)
        self.assertEqual(self.led.read_text(encoding="utf-8"), filelock.dumps_rows(o.ledger))


if __name__ == "__main__":
    unittest.main()
