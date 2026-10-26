"""A function that ends before any attempt (job.finish with no attempts) ends cleanly instead of killing its worker.

Since a19dfee job.finish read the last attempt's coverage unguarded: a prepare error, a stop in the prepare, an
unsupported function or an oracle without enough valid vectors raised IndexError, the worker died ("ERROR list index
out of range": 7 times in TC's events.log, 14 in Spawn main's), the end line was never logged and the oracle watcher
was never told about the skip. Each case now returns its status, logs its end line and (for the oracle skip) tells
the watcher. Run: python -W error tests/test_finish_no_attempts.py (also from test_tooling.py).
"""
import contextlib
import pathlib
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from test_sync import TMP  # noqa: E402  (sets DECOMP_PROJECT when run alone)

from core import ghidra, job, paths, prepare_timing  # noqa: E402

A = "8c010000"


class FakeO:
    def __init__(self):
        self.cfg = {"max_attempts": 2, "stall_limit": 2, "min_valid_vectors": 8, "model": "haiku"}
        self.functions = {A: {"tier": "easy", "score": 1, "insns": 12}}
        self.system_of, self.ledger, self.lines = {}, {}, []
        self.watcher = mock.Mock()

    def rec(self, addr):
        return self.ledger.setdefault(addr, {})

    def save(self):
        pass

    def log(self, line):
        self.lines.append(line)


class FakeW:
    id, stop_reason = 1, "stopped by user"

    def __init__(self):
        self.stop_flag = threading.Event()

    def set_phase(self, *a):
        pass


class NoAttempts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=TMP)
        root = pathlib.Path(self.tmp.name)
        self.prepare = None
        self.patches = [mock.patch.object(paths, k, root / d) for k, d in (
            ("WORK", "work"), ("MATCHED", "matched"), ("HARNESS", "harness"), ("STATE", "state"))] + [
            mock.patch.object(ghidra, "slot", lambda cfg, stop_flag=None, prio=None: contextlib.nullcontext(1)),
            mock.patch.object(ghidra, "prepare", lambda *a, **k: self.prepare()),
            mock.patch.object(ghidra, "queue_mark", lambda *a: None),
            mock.patch.object(prepare_timing, "brief", lambda t: "1 s"),
            mock.patch.object(job, "attempt", side_effect=AssertionError("no agent run")),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def end(self, prepare, stop=False):
        self.prepare = prepare
        o, w = FakeO(), FakeW()
        if stop:
            w.stop_flag.set()
        status = job.investigate(o, w, A)
        r = o.ledger[A]
        self.assertEqual(r["attempts"], [])
        self.assertNotIn("unreached_calls", r)
        self.assertTrue(o.lines[-1].startswith(f"{A}: {status.upper()} - "), o.lines[-1])  # its end line
        return status, r, o

    def test_prepare_error(self):
        def boom():
            raise RuntimeError("Ghidra timeout")
        status, r, _ = self.end(boom)
        self.assertEqual((status, r["verdict_detail"]), ("error", "Ghidra timeout"))

    def test_stop_in_prepare(self):
        status, r, _ = self.end(lambda: ({"signature": "int f(int)"}, {}), stop=True)
        self.assertEqual((status, r["verdict_detail"]), ("stopped", "stopped by user"))

    def test_unsupported(self):
        status, r, _ = self.end(lambda: ({"signature": "int f(int)", "unsupported": "a jump into another body"}, {}))
        self.assertEqual((status, r["verdict_detail"]), ("skipped", "unsupported: a jump into another body"))

    def test_oracle_skip_tells_the_watcher(self):
        summary = {"dev_valid": 2, "hidden_valid": 2, "invalid": {"bus error": 94}}
        status, r, o = self.end(lambda: ({"signature": "int f(int)"}, summary))
        self.assertEqual(status, "skipped")
        self.assertTrue(r["verdict_detail"].startswith("oracle could not produce 8+ valid vectors"))
        o.watcher.notify.assert_called_once_with(A)


if __name__ == "__main__":
    unittest.main()
