"""Prefetch (config "prefetch"; core/orch.Worker, core/job.Prefetch): a worker prepares its next function while its
agent works on the current one. A real Worker, claim and investigate; Ghidra's prepare and the agent are faked.
1. on: function n+1's prepare starts (on the prefetch thread) while function n's agent works, each function is
   prepared once, with the same arguments, every function ends as without prefetch, and the wait it left is recorded;
2. off (the default): prepare and agent strictly one after the other, on the worker's thread, as before;
3. a prefetched prepare that fails ends that function as an error, as its own prepare would; the worker goes on;
4. a stop while the next function is prefetched gives that function back with its status from before the claim;
5. with the loop off nothing is prefetched (the worker stops after its function).
Run: python -W error tests/test_prefetch.py (also from test_tooling.py).
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

from core import check, ghidra, job, orch, paths, prepare_timing, proof  # noqa: E402

A, B, C = "8c010000", "8c010100", "8c010200"
SUMMARY = {"dev_valid": 48, "hidden_valid": 48, "blocks_hit_all": 4, "blocks_total": 4}
PASS = {"verdict": "PASS", "stage": "run", "score": 0, "lint": [], "build": [], "first_diffs": [], "coverage": None,
        "vectors": {"dev": {"failing": 0, "total": 48}, "hidden": {"failing": 0, "total": 48}}}


class Prefetch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=TMP)
        root = pathlib.Path(self.tmp.name)
        self.events, self.lock = [], threading.Lock()
        self.started = {a: threading.Event() for a in (A, B, C)}
        self.mode, self.gate, self.on_agent = {}, {}, {}
        self.patches = [mock.patch.object(paths, k, root / d) for k, d in (
            ("ROOT", "."), ("IMPORTS", "import"), ("RUNS", "runs"), ("MATCHED", "matched"), ("WORK", "work"),
            ("BUILD", "build"), ("STATE", "state"), ("HARNESS", "harness"))]

        def event(*e):
            with self.lock:
                self.events.append(e)

        def prepare(cfg, addr, stop_flag=None, slot_index=None, timing=None):
            event("prepare", addr, threading.current_thread().name, slot_index)
            self.started[addr].set()
            mode = self.mode.get(addr)
            if mode == "err":
                raise RuntimeError("boom in TcPrepare")
            if mode == "hang":
                stop_flag.wait(10)
                raise RuntimeError("Ghidra stopped")
            timing["phases"] = {"total": 1.0}
            return {"signature": f"int f_{addr}(int a)"}, dict(SUMMARY)

        @contextlib.contextmanager
        def slot(cfg, stop_flag=None, prio=None):
            yield 1

        def attempt(o, w, addr, n, meta, summary, prev_work, previous_text):
            event("agent", addr)
            if addr in self.gate:  # the next function's prepare must start while this agent works
                self.assertTrue(self.started[self.gate[addr]].wait(5), f"no prefetch of {self.gate[addr]}")
            if addr in self.on_agent:
                self.on_agent[addr](w)
            run_dir = paths.RUNS / addr / f"attempt-{n:02d}"
            run_dir.mkdir(parents=True, exist_ok=True)
            work = run_dir / "work.c"
            work.write_text("int f(int a) { return a; }\n", encoding="utf-8")
            event("agent done", addr)
            att = {"n": n, "model": "haiku", "duration_s": 1.0, "cost_usd": 0.0, "work_file": str(work)}
            return job.judge(o, w, addr, n, att, work, run_dir)

        self.patches += [
            mock.patch.object(ghidra, "slot", slot), mock.patch.object(ghidra, "prepare", prepare),
            mock.patch.object(ghidra, "queue_mark", lambda *a: None),
            mock.patch.object(paths, "sha256_file", lambda p: "0"),
            mock.patch.object(prepare_timing, "brief", lambda t: "1 s"),
            mock.patch.object(check, "run_check", lambda *a, **k: dict(PASS)),
            mock.patch.object(check, "format_for_agent", lambda res: ""),
            mock.patch.object(job, "attempt", attempt),
            mock.patch.object(job, "prove", lambda o, addr, r: {"state": proof.VERIFIED}),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def orchestrator(self, prefetch=True, loop=True, ledger=None):
        o = object.__new__(orch.Orchestrator)
        o.cfg = {"prefetch": prefetch, "priority": [], "max_attempts": 2, "stall_limit": 2, "min_valid_vectors": 8,
                 "model": "haiku", "ghidra_parallel": 2, "ghidra_timeout_s": 20}
        o.lock, o.workers, o.system_of, o.retire_pending, o.watcher = threading.RLock(), {}, {}, 0, None
        o.functions = {a: {"addr": a, "tier": "easy", "score": i, "insns": 10} for i, a in enumerate((A, B, C))}
        o.ledger = {"loop": loop, "functions": ledger or {}}
        o.usage = mock.Mock(blocked_until=lambda: None)
        o.lines = []
        o.save, o.log, o.check_budget = (lambda: None), o.lines.append, (lambda: None)
        return o

    def work(self, o):
        w = orch.Worker(o, 1)
        o.workers[1] = w
        t = threading.Thread(target=w.run, name="worker-1")
        t.start()
        t.join(30)
        self.assertFalse(t.is_alive())
        self.assertIsNone(w.next)
        return {a: r.get("status") for a, r in o.ledger["functions"].items()}

    def prepares(self):
        return [e[1:3] for e in self.events if e[0] == "prepare"]

    def test_on(self):
        self.gate = {A: B, B: C}
        o = self.orchestrator()
        self.assertEqual(self.work(o), {A: "matched", B: "matched", C: "matched"})
        self.assertEqual(self.prepares(), [(A, "worker-1"), (B, "prefetch-W1"), (C, "prefetch-W1")])
        order = [e[:2] for e in self.events if e[0] != "agent done"]
        self.assertLess(order.index(("prepare", B)), [e[:2] for e in self.events].index(("agent done", A)))
        self.assertEqual([a for a in (A, B, C) if "prefetch_wait_s" in o.ledger["functions"][a]], [B, C])
        self.assertTrue(all(o.ledger["functions"][a]["signature"] == f"int f_{a}(int a)" for a in (A, B, C)))
        self.assertTrue(any(f"{B}: prefetched in Ghidra slot 1" in x for x in o.lines))

    def test_off(self):
        o = self.orchestrator(prefetch=False)
        self.assertEqual(self.work(o), {A: "matched", B: "matched", C: "matched"})
        self.assertEqual([e[:2] for e in self.events], [
            ("prepare", A), ("agent", A), ("agent done", A), ("prepare", B), ("agent", B), ("agent done", B),
            ("prepare", C), ("agent", C), ("agent done", C)])
        self.assertEqual({e[2] for e in self.events if e[0] == "prepare"}, {"worker-1"})
        self.assertFalse(any("prefetch_wait_s" in r for r in o.ledger["functions"].values()))

    def test_failed_prefetch(self):
        self.gate, self.mode = {A: B}, {B: "err"}
        o = self.orchestrator()
        self.assertEqual(self.work(o), {A: "matched", B: "error", C: "matched"})
        self.assertEqual(o.ledger["functions"][B]["verdict_detail"], "boom in TcPrepare")

    def test_stop_gives_the_next_back(self):
        self.gate, self.mode = {A: B}, {B: "hang"}
        self.on_agent = {A: lambda w: w.stop_flag.set()}
        o = self.orchestrator(ledger={B: {"addr": B, "status": "stopped", "attempts": []}})
        st = self.work(o)
        self.assertEqual(st[B], "stopped")  # its status from before the claim, not "waiting"
        self.assertNotIn(C, st)
        self.assertEqual(self.prepares(), [(A, "worker-1"), (B, "prefetch-W1")])
        o2 = self.orchestrator(ledger={})
        self.mode = {B: "hang"}
        self.started[B].clear()
        self.events.clear()
        self.on_agent = {A: lambda w: w.stop_flag.set()}
        self.assertEqual(self.work(o2).get(B), "queued")  # never claimed before: back in the queue

    def test_loop_off(self):
        o = self.orchestrator(loop=False)
        w = orch.Worker(o, 1, A)
        w.run()
        self.assertEqual({a: r["status"] for a, r in o.ledger["functions"].items()}, {A: "matched"})
        self.assertEqual(self.prepares(), [(A, threading.current_thread().name)])


if __name__ == "__main__":
    unittest.main()
