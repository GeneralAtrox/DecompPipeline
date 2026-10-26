"""Idle-time prepare (tools/idle_prepare.py), its guard against the server (core/idle_guard) and the long-tail order
(core/longtail). Synthetic files in a temp folder; Ghidra is never run (prepare is a fake).
Run: python -W error tests/test_idle_prepare.py (also from test_tooling.py's discovery).
"""
import json
import pathlib
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools"))
from test_sync import TMP  # noqa: E402  (sets DECOMP_PROJECT when run alone)

from core import idle_guard, longtail, orch  # noqa: E402
import idle_prepare  # noqa: E402


def folder():
    return pathlib.Path(tempfile.mkdtemp(prefix="idle", dir=TMP))


class LongTail(unittest.TestCase):
    def test_history(self):
        st = folder()
        (st / "events.log").write_text(
            "23:59:00  [W1] 8c000010: preparing in Ghidra slot 0 (tier easy)\n"
            "00:04:00  [W1] 8c000010: oracle ok, 48/48\n"  # past midnight: 300 s, not -86100
            "00:05:00  [W2] 8c000020: preparing in Ghidra slot 1\n"
            "00:05:10  [W1] 8c000030: attempt 1 started\n"  # another worker's line ends nothing
            "00:05:20  [W2] 8c000020: prepare failed: Ghidra timed out\n"
            "00:06:00  [W2] 8c000040: preparing in Ghidra slot 1\n", encoding="utf-8")  # never ended: none
        (st / "prepare_timing.jsonl.1").write_text(
            json.dumps({"addr": "8c000020", "ok": True, "phases": {"total": 400.0}}) + "\n", encoding="utf-8")
        (st / "prepare_timing.jsonl").write_text("\n".join(json.dumps(r) for r in (
            {"addr": "8c000020", "ok": True, "phases": {"total": 9.5}},  # newer than .1: wins
            {"addr": "8c000050", "ok": False, "phases": {"total": 600.0}},  # a failed prepare: not a duration
            {"addr": "8c000060", "ok": True, "phases": {"total": 130.0}})) + "\nnot json\n", encoding="utf-8")
        self.assertEqual(longtail.history(st), {"8c000010": 300, "8c000020": 9.5, "8c000060": 130.0})

    def test_order(self):
        hist = {"b": 300, "c": 5, "d": 150, "e": 119}
        self.assertEqual(longtail.order(["a", "b", "c", "d", "e"], hist), ["b", "d", "a", "c", "e"])
        self.assertEqual(longtail.order([], hist), [])


class Guard(unittest.TestCase):
    def test_acquire_release(self):
        st = folder()
        self.assertTrue(idle_guard.acquire(st))
        self.assertFalse(idle_guard.acquire(st))  # this process holds it
        self.assertIsNotNone(idle_guard.holder(st))
        idle_guard.release(st)
        self.assertIsNone(idle_guard.holder(st))

    def test_stale_holder(self):  # its process is gone: removed, the workspace is free
        st = folder()
        p = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"], capture_output=True, text=True)
        (st / idle_guard.HOLD).write_text(json.dumps({"pid": int(p.stdout), "started": 0}), encoding="utf-8")
        self.assertIsNone(idle_guard.holder(st))
        self.assertFalse((st / idle_guard.HOLD).exists())
        self.assertTrue(idle_guard.end_job(st, timeout=1))

    def test_end_job(self):  # the server asks; the job sees the stop, releases; end_job returns
        st = folder()
        idle_guard.acquire(st)
        seen = []

        def job():
            while not idle_guard.stop_requested(st):
                time.sleep(0.05)
            seen.append(True)
            idle_guard.release(st)
        t = threading.Thread(target=job)
        t.start()
        logs = []
        self.assertTrue(idle_guard.end_job(st, logs.append, timeout=10))
        t.join(5)
        self.assertEqual(seen, [True])
        self.assertFalse((st / idle_guard.STOP).exists())
        self.assertIn("asked it to stop", logs[0])

    def test_end_job_timeout(self):  # a job that does not stop: False, and it is logged
        st = folder()
        idle_guard.acquire(st)
        logs = []
        self.assertFalse(idle_guard.end_job(st, logs.append, timeout=0.6))
        self.assertIn("did not stop", logs[-1])
        idle_guard.release(st)

    def test_server_ends_job_first(self):  # Orchestrator.__init__ asks before reading anything else
        src = pathlib.Path(orch.__file__).read_text(encoding="utf-8")
        init = src[src.index("    def __init__(self):\n        self.cfg"):]
        self.assertLess(init.index("idle_guard.end_job(paths.STATE"), init.index("catalog = paths.read_json"))

    def test_ram_pct(self):
        r = idle_guard.ram_pct()
        self.assertTrue(r is None or 0 < r <= 100)


def fn(addr, score, tier="easy"):
    return {"addr": addr, "score": score, "tier": tier}


class Plan(unittest.TestCase):
    FUNCS = {a: fn(a, s) for a, s in (("a1", 10), ("a2", 20), ("a3", 30), ("a4", 40), ("a5", 50))}

    def test_plan(self):
        funcs = dict(self.FUNCS, x9=fn("x9", 1, "excluded"))
        rows = {"a1": {"status": "matched"}, "a3": {"status": "queued"}, "a4": {"status": "stopped"}}
        current = {"a2"}  # its harness is current: nothing to do
        hist = {"a5": 310, "a4": 3}
        got = idle_prepare.plan({}, funcs, rows, ["a5"], lambda a: a in current, hist, ahead=3)
        # the queue: a5 (priority), a2, a3, a4; the next 3: a5, a2, a3; a2 current; a5 slow first
        self.assertEqual(got, ["a5", "a3"])
        self.assertEqual(orch.open_order(funcs, rows, ["a5"]), ["a5", "a2", "a3", "a4"])


class Run(unittest.TestCase):
    def test_all(self):
        done = []
        rows = idle_prepare.run(["a", "b"], lambda a, s: done.append(a) or ({"signature": "s"}, {}),
                                threading.Event(), time.monotonic() + 60, 1, log=lambda m: None)
        self.assertEqual([r["state"] for r in rows], ["prepared", "prepared"])
        self.assertEqual(done, ["a", "b"])

    def test_stop(self):  # the stop kills the running prepare; the rest are not started
        stop = threading.Event()

        def prepare(a, s):
            if a == "b":
                s.set()
                raise RuntimeError("Ghidra stopped")
            return {"signature": "s"}, {}
        rows = idle_prepare.run(["a", "b", "c"], prepare, stop, time.monotonic() + 60, 1, log=lambda m: None)
        self.assertEqual([r["state"] for r in rows], ["prepared", "stopped", "stopped"])

    def test_idle_priority(self):  # a worker waiting on the broker stops the idle run; the killed prepare is "stopped"
        from core import ghidra, ghidra_broker
        d = folder()
        cfg = {"ghidra_parallel": 1, "ghidra_broker": {"slots": 1, "third_below": 0, "dir": str(d / "broker")}}  # one token
        stop, got, seen = threading.Event(), [], []

        def prepare(cfg_, addr, slot_index, stop_flag):  # stands in for Ghidra: runs until the stop kills it
            seen.extend(f.name for f in (d / "broker").glob("h_*.json"))
            got.append("go")
            if not stop_flag.wait(30):
                return {"signature": "s"}, {}
            raise RuntimeError("Ghidra stopped")

        def worker():
            while not got:
                time.sleep(0.01)
            with ghidra_broker.token(cfg, "worker", poll=0.05):
                got.append("worker")
        with mock.patch.object(ghidra_broker, "ram_pct", lambda: 50.0),                 mock.patch.object(ghidra_broker, "processes", lambda: []),                 mock.patch.object(ghidra, "prepare", prepare):
            t = threading.Thread(target=worker)
            t.start()
            rows = idle_prepare.run(["a", "b"], lambda a, s: idle_prepare.prepare_one(cfg, a, s), stop,
                                    time.monotonic() + 60, 1, log=lambda m: None)
            t.join(30)
        self.assertTrue(seen[0].startswith(f"h_{ghidra_broker.PRIO['idle']}_"), seen)
        self.assertEqual([r["state"] for r in rows], ["stopped", "stopped"])
        self.assertEqual(got, ["go", "worker"])

    def test_warm_slots(self):  # fresh copies first, then both slots prepare at once
        from core import ghidra, paths
        d = folder()
        (d / "master").mkdir()
        (d / "master" / "p.gpr").write_text("", encoding="utf-8")
        (d / "catalog.json").write_text("{}", encoding="utf-8")
        cfg = {"ghidra_parallel": 2}
        with mock.patch.object(paths, "GHIDRA_PROJECT", d / "master"), \
                mock.patch.object(paths, "GHIDRA_SLOTS", d / "slots"), mock.patch.object(paths, "CATALOG", d / "catalog.json"):
            idle_prepare.warm_slots(cfg)
            self.assertTrue(ghidra._copy_is_fresh(1))
            self.assertTrue((d / "slots" / "slot1" / "p.gpr").exists())
            lock, now, peak, used = threading.Lock(), [0], [0], set()

            def prepare(a, s):
                with ghidra.slot(cfg, stop_flag=s) as i:
                    with lock:
                        now[0] += 1
                        peak[0], _ = max(peak[0], now[0]), used.add(i)
                    time.sleep(0.2)
                    with lock:
                        now[0] -= 1
                return {"signature": "s"}, {}
            rows = idle_prepare.run(list("abcdef"), prepare, threading.Event(), time.monotonic() + 60, 2,
                                    log=lambda m: None)
        self.assertEqual({r["state"] for r in rows}, {"prepared"})
        self.assertEqual((peak[0], used), (2, {0, 1}))

    def test_deadline_and_error(self):
        rows = idle_prepare.run(["a"], lambda a, s: 1 / 0, threading.Event(), time.monotonic() + 60, 1,
                                log=lambda m: None)
        self.assertEqual(rows[0]["state"], "error")
        self.assertIn("ZeroDivisionError", rows[0]["detail"])
        rows = idle_prepare.run(["a"], lambda a, s: self.fail("prepared after the deadline"), threading.Event(),
                                time.monotonic() - 1, 1, log=lambda m: None)
        self.assertEqual(rows[0]["state"], "stopped")


class Watch(unittest.TestCase):
    def watch(self, st, server=None, ram=50.0):
        stop, logs = threading.Event(), []
        with mock.patch.object(idle_prepare, "server_state", lambda ws, timeout: server), \
                mock.patch.object(idle_guard, "ram_pct", lambda: ram):
            t = threading.Thread(target=idle_prepare.watch, args=(st, st, stop, 90, logs.append, 0.05))
            t.start()
            t.join(0.5)
            stopped = stop.is_set()
            stop.set()
            t.join(5)
        return stopped, logs

    def test_quiet(self):
        self.assertEqual(self.watch(folder()), (False, []))

    def test_server_asks(self):
        st = folder()
        (st / idle_guard.STOP).write_text("1", encoding="utf-8")
        self.assertEqual(self.watch(st), (True, ["stopping: the server asked"]))

    def test_server_running(self):
        self.assertEqual(self.watch(folder(), server={"project": "x"}), (True, ["stopping: the server is running"]))

    def test_ram(self):
        self.assertEqual(self.watch(folder(), ram=91.0), (True, ["stopping: RAM at 91%"]))


if __name__ == "__main__":
    unittest.main()
