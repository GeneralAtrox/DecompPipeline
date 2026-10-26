"""The machine-wide Ghidra queue (core/ghidra_broker, config "ghidra_broker"). Threads stand in for processes (each
ticket is named by its thread), a fake process table for the machine's JVMs; Ghidra is never run.
Run: python -W error tests/test_ghidra_broker.py (also from test_tooling.py's discovery).
"""
import io
import json
import os
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
from test_sync import TMP  # noqa: E402  (sets DECOMP_PROJECT when run alone)

from core import ghidra, ghidra_broker, ghidra_server, job, paths  # noqa: E402
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools"))
import sdk_fid  # noqa: E402


def cfg(slots=1, **kw):
    return {"ghidra_broker": {"slots": slots, "dir": tempfile.mkdtemp(prefix="broker", dir=TMP), **kw}}


class Machine:
    """The fake machine: RAM in use (percent) and its processes [(pid, ppid, name, create time)], every java one a
    Ghidra JVM; 32,000 MB in all, and each JVM's MB in mem (0 when not set)."""
    def __init__(self, ram=85.0):
        self.ram, self.procs, self.mem = ram, [], {}

    def __enter__(self):
        self.ps = [mock.patch.object(ghidra_broker, "ram_pct", lambda: self.ram),
                   mock.patch.object(ghidra_broker, "processes", lambda: list(self.procs)),
                   mock.patch.object(ghidra_broker, "_is_ghidra", lambda pid: True),
                   mock.patch.object(ghidra_broker, "total_mb", lambda: 32000.0),
                   mock.patch.object(ghidra_broker, "jvm_mem",
                                     lambda pids, procs=(): {p: self.mem.get(p, 0.0) for p in pids}),
                   mock.patch.object(ghidra_broker, "top_processes", lambda n: [["fake.exe", 1]])]
        for x in self.ps:
            x.start()
        return self

    def __exit__(self, *exc):
        for x in self.ps:
            x.stop()
        return False


class Broker(unittest.TestCase):
    def setUp(self):  # RAM between third_below and stop_at, no JVMs: `slots` tokens
        self.m = Machine().__enter__()
        self.addCleanup(self.m.__exit__)

    def test_off(self):
        c = {"ghidra_broker": {"slots": 0, "dir": tempfile.mkdtemp(prefix="broker", dir=TMP)}}
        with ghidra_broker.token(c) as waited:
            self.assertEqual(waited, 0.0)
        self.assertEqual(list(pathlib.Path(c["ghidra_broker"]["dir"]).iterdir()), [])
        self.assertEqual(ghidra_broker.settings({})[0]["slots"], 0)  # the default: off

    def test_limit(self):  # never more holders than slots, across "processes"
        c, lock, now, peak = cfg(2), threading.Lock(), [0], [0]

        def one():
            with ghidra_broker.token(c, poll=0.01):
                with lock:
                    now[0] += 1
                    peak[0] = max(peak[0], now[0])
                time.sleep(0.05)
                with lock:
                    now[0] -= 1
        ts = [threading.Thread(target=one) for _ in range(6)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(30)
        self.assertEqual(peak[0], 2)
        self.assertEqual(list(pathlib.Path(c["ghidra_broker"]["dir"]).glob("[thy]_*.json")), [])

    def test_priority(self):  # a worker queued after a tool still goes first
        c, order, held, release = cfg(1), [], threading.Event(), threading.Event()

        def holder():
            with ghidra_broker.token(c, "tool", poll=0.01):
                held.set()
                release.wait(10)

        def waiter(prio):
            with ghidra_broker.token(c, prio, poll=0.01):
                order.append(prio)
        h = threading.Thread(target=holder)
        h.start()
        held.wait(5)
        ts = []
        for prio in ("idle", "tool", "prefetch", "worker"):  # queued in this order
            ts.append(threading.Thread(target=waiter, args=(prio,)))
            ts[-1].start()
            time.sleep(0.1)
        release.set()
        for t in [h, *ts]:
            t.join(30)
        self.assertEqual(order, ["worker", "prefetch", "tool", "idle"])

    def test_idle_yields(self):  # an idle holder's stop_flag is set while a worker waits; the worker then goes
        c, idle_stop, held, got = cfg(1), threading.Event(), threading.Event(), []

        def idle():
            with ghidra_broker.token(c, "idle", stop_flag=idle_stop, poll=0.01):
                held.set()
                idle_stop.wait(10)  # its Ghidra run, killed by the stop

        t = threading.Thread(target=idle)
        t.start()
        held.wait(5)
        with ghidra_broker.token(c, "worker", poll=0.01) as waited:
            got.append(waited)
        t.join(10)
        self.assertTrue(idle_stop.is_set())
        self.assertLess(got[0], 5)

    def test_tool_does_not_yield(self):  # only idle holders give way
        c, stop, held, release = cfg(1), threading.Event(), threading.Event(), threading.Event()

        def tool():
            with ghidra_broker.token(c, "tool", stop_flag=stop, poll=0.01):
                held.set()
                release.wait(10)
        t = threading.Thread(target=tool)
        t.start()
        held.wait(5)
        w_stop = threading.Event()
        threading.Timer(0.5, w_stop.set).start()
        with self.assertRaises(RuntimeError):  # the worker waits, then gives up on its own stop
            with ghidra_broker.token(c, "worker", stop_flag=w_stop, poll=0.01):
                pass
        self.assertFalse(stop.is_set())
        release.set()
        t.join(10)
        d = pathlib.Path(c["ghidra_broker"]["dir"])
        self.assertEqual(list(d.glob("[thy]_*.json")), [])  # the stopped waiter's ticket and the yield flag gone

    def test_dead_holder(self):  # a hold of a process that is gone does not count
        c = cfg(1)
        d = pathlib.Path(c["ghidra_broker"]["dir"])
        p = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"], capture_output=True, text=True)
        (d / f"h_2_{time.time_ns():020d}_{int(p.stdout)}_1.json").write_text(json.dumps({}), encoding="utf-8")
        with ghidra_broker.token(c, "worker", poll=0.01) as waited:
            self.assertLess(waited, 2)
        self.assertEqual(list(d.glob("[thy]_*.json")), [])


    def test_ram_rule(self):  # the machine's slot rule: 2 JVMs, a 3rd under 82%, none from 88%
        b = ghidra_broker.settings(cfg(2))[0]
        self.assertEqual([ghidra_broker.allowed(b, r) for r in (50, 81.9, 82, 87.9, 88, 99, None)],
                         [3, 3, 2, 2, 0, 0, 2])

    def test_burst(self):  # opt-in (a workspace's project.json): up to 5 under 75%, the normal rule above it
        b = {**ghidra_broker.settings(cfg(2))[0], "burst": 5}
        self.assertEqual([ghidra_broker.allowed(b, r) for r in (50, 74.9, 75, 81.9, 82, 88, None)],
                         [5, 5, 3, 3, 2, 0, 2])
        self.assertEqual(ghidra_broker.settings(cfg(2))[0]["burst"], 0)  # off unless set

    def test_in_use(self):  # JVMs under a holder count once; a hold with no JVM yet counts; outside JVMs count
        procs = [(10, 1, "python.exe", 0), (11, 10, "cmd.exe", 0), (12, 11, "java.exe", 0),  # holder 10's JVM
                 (20, 1, "python.exe", 0),  # holder 20: token held, JVM not started
                 (30, 1, "cmd.exe", 0), (31, 30, "java.exe", 0)]  # a JVM outside the broker
        found = ghidra_broker.jvms(procs, is_ghidra=lambda pid: True)
        self.assertEqual(set(found), {12, 31})
        self.assertEqual(ghidra_broker.in_use([f"0_{1:020d}_10_1", f"0_{2:020d}_20_1"], found), 3)
        self.assertEqual(ghidra_broker.in_use([f"0_{1:020d}_10_1", f"0_{2:020d}_10_2"], found), 3)  # 10: 2 holds
        self.assertEqual(ghidra_broker.in_use([], found), 2)
        self.assertEqual(ghidra_broker.jvms(procs, is_ghidra=lambda pid: False), {})  # java, not Ghidra

    def test_outside_jvms(self):  # 2 JVMs started outside the broker: none more at 85%, a 3rd under 82%
        c = cfg(2)
        self.m.procs = [(900, 1, "cmd.exe", 0), (901, 900, "java.exe", 0), (902, 900, "java.exe", 0)]
        got = threading.Event()

        def w():
            with ghidra_broker.token(c, "worker", poll=0.01):
                got.set()
        t = threading.Thread(target=w)
        t.start()
        self.assertFalse(got.wait(1.0))
        self.m.ram = 81.0
        self.assertTrue(got.wait(5))
        t.join(10)
        self.m.ram, self.m.procs = 88.0, []  # at stop_at nothing starts, even with no JVM running
        got.clear()
        t = threading.Thread(target=w)
        t.start()
        self.assertFalse(got.wait(1.0))
        self.m.ram = 87.0
        self.assertTrue(got.wait(5))
        t.join(10)

    def test_never_over_the_rule(self):  # 8 "processes" and 1 outside JVM: 1 more at 85%, 2 more at 80%
        c, lock, now, peak = cfg(2), threading.Lock(), [0], {}
        self.m.procs = [(900, 1, "java.exe", 0)]

        def one():
            with ghidra_broker.token(c, poll=0.01):
                with lock:
                    now[0] += 1
                    peak[self.m.ram] = max(peak.get(self.m.ram, 0), now[0])
                time.sleep(0.05)
                with lock:
                    now[0] -= 1
        for ram in (85.0, 80.0):
            self.m.ram = ram
            ts = [threading.Thread(target=one) for _ in range(8)]
            for t in ts:
                t.start()
            for t in ts:
                t.join(30)
        self.assertEqual(peak, {85.0: 1, 80.0: 2})

    def test_lease(self):  # one token for a server's whole life, given back by release() from any thread
        c = cfg(1)
        lease = ghidra_broker.lease(c, "worker", "server slot 1", poll=0.01)
        self.assertEqual((holds(c), ghidra_broker.holding()), (1, 0))  # held by the server, not by this thread
        t = threading.Thread(target=lease.release)
        t.start()
        t.join(10)
        lease.release()  # twice is harmless
        self.assertEqual(holds(c), 0)
        self.assertIsNone(ghidra_broker.lease({"ghidra_broker": {"slots": 0}}))  # broker off: no lease

    def test_contested_lease_gives_way(self):
        """The 2026-10-23 deadlock's shape with leases: two slot servers of this process (JVMs 901, 902) fill the
        limit and another ticket that needs a JVM waits. A lease is contested once that ticket has waited lease_s, or
        at once when its server has been idle yield_idle_s; its server closes and the ticket is granted. A ticket
        behind a HOLD that does not let it through contests nothing."""
        c, stop, got, me = cfg(2), threading.Event(), [], os.getpid()
        d = pathlib.Path(c["ghidra_broker"]["dir"])
        a, b = (ghidra_broker.lease(c, poll=0.01) for _ in range(2))
        self.m.procs = [(me, 1, "python.exe", 0), (901, me, "java.exe", 0), (902, me, "java.exe", 0)]  # both started
        self.assertFalse(a.contested(0.0, 30, 2))  # nobody waits

        def first():
            try:
                with ghidra_broker.token(c, "tool", poll=0.01, stop_flag=stop):
                    got.append("first")
            except RuntimeError:
                pass

        def waits(test):
            for _ in range(500):
                if any(test(q) for q in d.glob("t_*.json")):
                    return
                time.sleep(0.01)
        th = threading.Thread(target=first)
        th.start()
        waits(lambda q: True)
        time.sleep(0.2)
        self.assertEqual(got, [])  # the limit is full
        self.assertFalse(a.contested(0.5, 30, 2))  # busy servers, a short wait: the leases stay
        self.assertTrue(a.contested(2.0, 30, 2))  # an idle server while someone waits: give way
        self.assertTrue(a.contested(0.0, 30, 2, now=time.time() + 31))  # waited lease_s: give way
        self.m.procs = [q for q in self.m.procs if q[0] != 901]
        a.release()  # its server closed: the waiter goes
        th.join(10)
        self.assertEqual(got, ["first"])
        (d / "HOLD").write_text("{}", encoding="utf-8")
        th = threading.Thread(target=first)
        th.start()
        try:
            time.sleep(0.3)
            self.assertEqual(list(d.glob("t_*.json")), [])  # the HOLD stops it: it waits outside the queue
            self.assertFalse(b.contested(10.0, 0, 0))  # it cannot be granted anyway: the server keeps serving
        finally:
            stop.set()
            th.join(10)
            (d / "HOLD").unlink()
            b.release()
        self.assertEqual((got, holds(c)), (["first"], 0))

    def test_idle_lease_gives_way(self):  # as an idle token's holder does
        c = cfg(1)
        idle = ghidra_broker.lease(c, "idle", poll=0.01)
        d = pathlib.Path(c["ghidra_broker"]["dir"])
        (d / f"t_3_{time.time_ns():020d}_{os.getpid()}_1.json").write_text("{}", encoding="utf-8")
        self.assertFalse(idle.contested(0.0, 30, 2))  # another idle one waits: the usual rule
        (d / f"t_2_{time.time_ns():020d}_{os.getpid()}_2.json").write_text("{}", encoding="utf-8")
        self.assertTrue(idle.contested(0.0, 30, 2))  # a tool (or worker, prefetch) waits: at once
        idle.release()

    def test_copy_refreshed_without_a_token(self):
        """ghidra.slot refreshes a stale read-only copy under the master (slot 0) before it takes a token. Before, it
        took the token first: the master's thread waited in the queue for a token while a copy's thread held one and
        waited for the master (2026-10-23: prepare_list on a workspace with no copies yet, 3 holds and no JVM for
        minutes, every session's queue stopped). Here the limit is 1, another process holds it, the master's thread
        (prio "tool") queues, then a copy's thread (prio "worker", granted first) asks for slot 1."""
        c, stop, got, fresh, refreshed = {**cfg(1), "ghidra_parallel": 2}, threading.Event(), [], {1: False}, []
        d, other_done = pathlib.Path(c["ghidra_broker"]["dir"]), threading.Event()

        def refresh(i):
            refreshed.append(i)
            fresh[i] = True

        def other():  # another process's hold
            with ghidra_broker.token(c, "tool", poll=0.01):
                other_done.wait(10)

        def use(prio):
            try:
                with ghidra.slot(c, stop_flag=stop, prio=prio) as i:
                    got.append(i)
            except RuntimeError:
                pass
        ps = [mock.patch.object(ghidra_server, "up", lambda i: False),
              mock.patch.object(ghidra, "_copy_is_fresh", lambda i: fresh[i]),
              mock.patch.object(ghidra, "_refresh_copy", refresh)]
        for x in ps:
            x.start()
            self.addCleanup(x.stop)
        threads = [threading.Thread(target=other)]
        threads[0].start()
        for _ in range(500):
            if list(d.glob("h_*.json")):
                break
            time.sleep(0.01)
        threads.append(threading.Thread(target=use, args=("tool",)))  # takes the master, queues for a token
        threads[1].start()
        for _ in range(500):
            if list(d.glob("t_*.json")):
                break
            time.sleep(0.01)
        threads.append(threading.Thread(target=use, args=("worker",)))  # takes slot 1: its copy is stale
        threads[2].start()
        time.sleep(0.5)
        other_done.set()
        threading.Timer(5, stop.set).start()
        for th in threads:
            th.join(10)
        stop.set()
        self.assertEqual((got, refreshed), ([0, 1], [1]))  # before: ([1], [1]), the master's thread stopped at 5 s

    def test_no_starvation(self):  # workers that re-queue without a break: a tool waiter still gets a token
        def run(age_s):
            c, stop, got = cfg(1, age_s=age_s), threading.Event(), threading.Event()

            def worker():
                while not stop.is_set():
                    with ghidra_broker.token(c, "worker", poll=0.01):
                        time.sleep(0.02)

            def tool():
                try:
                    with ghidra_broker.token(c, "tool", poll=0.01, stop_flag=stop):
                        got.set()
                except RuntimeError:  # stopped while waiting
                    pass
            ws = [threading.Thread(target=worker) for _ in range(3)]
            for t in ws:
                t.start()
            time.sleep(0.2)
            t0 = time.monotonic()
            tt = threading.Thread(target=tool)
            tt.start()
            ok = got.wait(4)
            took = time.monotonic() - t0
            stop.set()
            for t in [*ws, tt]:
                t.join(10)
            return ok, took
        self.assertEqual(run(0)[0], False)  # strict priority: the tool waits as long as workers queue
        ok, took = run(0.3)  # two age steps (tool, prefetch, worker), then it is the oldest worker-level ticket
        self.assertTrue(ok)
        self.assertLess(took, 3)

    def test_error_frees(self):  # a run that fails (its JVM killed by a guard) gives its token back
        c = cfg(1)
        with self.assertRaises(RuntimeError):
            with ghidra_broker.token(c, "tool", poll=0.01):
                raise RuntimeError("Ghidra exited with code 1")
        with ghidra_broker.token(c, "worker", poll=0.01) as waited:
            self.assertLess(waited, 2)

    def test_killed_process_frees(self):  # a holder whose process tree a guard kills, or that crashes
        root = str(pathlib.Path(ghidra_broker.__file__).resolve().parents[1])
        for how in ("taskkill", "exit"):
            c = cfg(1)
            d = pathlib.Path(c["ghidra_broker"]["dir"])
            child = (f"import os, sys, time; sys.path.insert(0, {root!r}); from core import ghidra_broker as g; "
                     f"cm = g.token({{'ghidra_broker': {{'slots': 9, 'dir': {str(d)!r}}}}}, 'tool', poll=0.01); "
                     "cm.__enter__(); print('held', flush=True); "
                     + ("time.sleep(60)" if how == "taskkill" else "os._exit(3)"))
            p = subprocess.Popen([sys.executable, "-c", child], stdout=subprocess.PIPE, text=True)
            self.assertEqual(p.stdout.readline().strip(), "held")
            if how == "taskkill":
                self.assertEqual(len(list(d.glob("h_*.json"))), 1)
                subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True, timeout=60)
            p.wait(30)
            p.stdout.close()
            with ghidra_broker.token(c, "worker", poll=0.01) as waited:
                self.assertLess(waited, 5)
            self.assertEqual(list(d.glob("[thy]_*.json")), [])

    def test_reused_pid(self):  # a hold whose pid now belongs to a newer process is stale
        c = cfg(1)
        d = pathlib.Path(c["ghidra_broker"]["dir"])
        (d / f"h_2_{10**18:020d}_{os.getpid()}_1.json").write_text("{}", encoding="utf-8")  # "held" since 2001
        with ghidra_broker.token(c, "worker", poll=0.01) as waited:
            self.assertLess(waited, 2)
        self.assertEqual(list(d.glob("[thy]_*.json")), [])

    def test_before_restart(self):  # files from before the machine started are stale, whoever has the pid now
        c = cfg(1)
        d = pathlib.Path(c["ghidra_broker"]["dir"])
        boot = time.time() - 60
        before = d / f"t_2_{int((boot - 3600) * 1e9):020d}_{os.getpid()}_7.json"  # a live pid, start time unknown
        after = d / f"h_2_{int((boot + 30) * 1e9):020d}_{os.getpid()}_8.json"
        for p in (before, after):
            p.write_text("{}", encoding="utf-8")
        with mock.patch.object(ghidra_broker, "_started", lambda pid: 0.0),                 mock.patch.object(ghidra_broker, "_boot", lambda: boot):
            ghidra_broker._reap(d)
            self.assertEqual([p.name for p in d.glob("[thy]_*.json")], [after.name])  # its process may hold it
            with mock.patch.object(ghidra_broker, "_boot", lambda: 0.0):  # boot unknown: as before
                (d / before.name).write_text("{}", encoding="utf-8")
                ghidra_broker._reap(d)
                self.assertEqual(len(list(d.glob("[thy]_*.json"))), 2)


class ByRam(unittest.TestCase):
    """Config "by_ram" (row 17 of the second waste audit): the cap from measured RAM headroom. 32,000 MB in all and
    a 960 MB peak JVM: one JVM is 3% of the machine."""
    T, PEAK = 32000.0, 960.0

    def b(self, **kw):
        return {**ghidra_broker.settings(cfg(2, by_ram=True))[0], **kw}

    def rule(self, ram, jvm_mb=(), pending=0, peak=PEAK, **kw):
        return ghidra_broker.by_ram(self.b(**kw), ram, self.T, list(jvm_mb), pending, peak, cores=24)

    def test_fills_to_85(self):  # more JVMs at today's median than the old rule's 2 or 3, none past 85%
        self.assertEqual([self.rule(r) for r in (50, 72, 75.9, 76, 79, 81.9)], [6, 4, 3, 2, 2, 2])
        self.assertEqual(self.rule(10, max_jvms=3), 3)  # the ceiling: max_jvms, else cores / 4
        self.assertEqual(self.rule(10), 6)
        old = ghidra_broker.settings(cfg(2))[0]
        self.assertEqual([ghidra_broker.allowed(old, r) for r in (50, 72, 79, 81.9)], [3, 3, 3, 3])

    def test_growth_counts(self):  # JVMs still growing to their peak, and tokens whose JVM has not started
        self.assertEqual(self.rule(72, [960, 960]), 6)  # at their peak: 4 more fit under 85%
        self.assertEqual(self.rule(72, [100, 100]), 4)  # 2 x 860 MB still to come (77.4% committed): 2 more
        self.assertEqual(self.rule(72, pending=2), 4)  # 2 x 960 MB to come
        self.assertEqual(self.rule(79, [100, 100]), 2)  # 84.4% committed: none more; the old rule started a 3rd

    def test_stop_line(self):  # the first `slots` JVMs start above 85%, never past 95%, never at stop_at
        self.assertEqual([self.rule(r) for r in (85, 87.9, 88, 99)], [2, 2, 0, 0])
        self.assertEqual(self.rule(84.0, peak=3500), 1)  # 84% + 10.9% stays under 95%, a second would not
        self.assertEqual(self.rule(86.0, peak=3500), 0)  # 86% + 10.9% passes 95%; the old rule started 2
        self.assertEqual(self.rule(87.0, pending=1, peak=2000), 1)  # 93.25% once the started one is up: none more
        self.assertEqual(self.rule(87.0, [1000], peak=2000), 1)  # the running one's growth to its peak counts too

    def test_unknown(self):  # RAM or total unknown: the old rule
        b = self.b()
        self.assertEqual(ghidra_broker.by_ram(b, None, self.T, [], 0, self.PEAK), ghidra_broker.allowed(b, None))
        self.assertEqual(ghidra_broker.by_ram(b, 70, None, [], 0, self.PEAK), ghidra_broker.allowed(b, 70))

    def test_peak_is_measured(self):  # the largest seen, kept in the folder; peak_mb until the first sample
        b, d = self.b(), pathlib.Path(tempfile.mkdtemp(prefix="broker", dir=TMP))
        self.assertEqual(ghidra_broker.peak(b, d, {}), 1024.0)
        self.assertEqual(ghidra_broker.peak(b, d, {11: 400.0, 12: 952.0}), 952.0)
        self.assertEqual(ghidra_broker.peak(b, d, {13: 300.0}), 952.0)
        self.assertEqual(json.loads((d / "jvm_peak.json").read_text(encoding="utf-8"))["pid"], 12)

    def test_tree_counted(self):  # a JVM's MB includes the processes under it (Ghidra's decompile.exe), real ones
        import psutil
        code = ("import subprocess, sys, time; subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
                "time.sleep(60)")
        top = subprocess.Popen([sys.executable, "-c", code])
        self.addCleanup(lambda: subprocess.run(["taskkill", "/PID", str(top.pid), "/T", "/F"], capture_output=True)
                        if os.name == "nt" else top.kill())
        for _ in range(200):
            kids = psutil.Process(top.pid).children()
            if kids:
                break
            time.sleep(0.05)
        self.assertTrue(kids)
        alone = ghidra_broker.jvm_mem([top.pid])[top.pid]
        both = ghidra_broker.jvm_mem([top.pid], ghidra_broker.processes())[top.pid]
        self.assertGreater(both, alone + 0.5 * ghidra_broker.jvm_mem([kids[0].pid])[kids[0].pid])

    def test_ram_log(self):  # a line a minute at most, the older half dropped past LOG_BYTES
        d = pathlib.Path(tempfile.mkdtemp(prefix="broker", dir=TMP))
        f = d / ghidra_broker.LOG
        with mock.patch.object(ghidra_broker, "top_processes", lambda n: [["game.exe", 6700]]):
            ghidra_broker.ram_log(d, 95.6, {41: 975.3})
            ghidra_broker.ram_log(d, 96.0, {})  # under a minute later: no line
            self.assertEqual([json.loads(x) for x in f.read_text(encoding="utf-8").splitlines()][0]["jvm_mb"],
                             {"41": 975})
            self.assertEqual(len(f.read_text(encoding="utf-8").splitlines()), 1)
            ghidra_broker.ram_log(d, 70.0, {}, now=time.time() + 61)
            self.assertEqual(json.loads(f.read_text(encoding="utf-8").splitlines()[-1])["top"], [["game.exe", 6700]])
            f.write_text("".join(json.dumps({"n": i}) + "\n" for i in range(400)), encoding="utf-8")
            with mock.patch.object(ghidra_broker, "LOG_BYTES", 4000):
                ghidra_broker.ram_log(d, 70.0, {}, now=time.time() + 61)
            rows = [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines()]  # whole lines only
            self.assertLess(len(rows), 250)
            self.assertEqual((rows[-2]["n"], rows[-1]["ram"]), (399, 70.0))
            d2 = pathlib.Path(tempfile.mkdtemp(prefix="broker", dir=TMP))  # log_soon: its own thread; top at 85%+
            for ram in (80.0, 90.0):
                ghidra_broker.log_soon(d2, ram, {}, 85.0)
                self.assertTrue(ghidra_broker._logging.acquire(timeout=30))  # its thread is done
                ghidra_broker._logging.release()
                if ram == 80.0:
                    os.utime(d2 / ghidra_broker.LOG, (time.time() - 61,) * 2)
            self.assertEqual([json.loads(x)["top"] for x in (d2 / ghidra_broker.LOG).read_text().splitlines()],
                             [[], [["game.exe", 6700]]])

    def test_queue(self):  # through the queue: tokens granted while the measured headroom allows, on the fake machine
        c = cfg(2, by_ram=True, max_jvms=8)
        d = pathlib.Path(c["ghidra_broker"]["dir"])
        (d / "jvm_peak.json").write_text(json.dumps({"mb": 960.0}), encoding="utf-8")
        with Machine(ram=76.0) as m:
            m.procs = [(900, 1, "cmd.exe", 0), (901, 900, "java.exe", 0), (902, 900, "java.exe", 0)]
            m.mem = {901: 960.0, 902: 960.0}  # 2 JVMs outside the broker at their peak, 76%: 2 more fit under 85%
            got, release = [], threading.Event()

            def w():
                with ghidra_broker.token(c, "worker", poll=0.01):
                    got.append(1)
                    release.wait(10)
            ts = [threading.Thread(target=w) for _ in range(4)]
            for t in ts:
                t.start()
            time.sleep(1.5)
            self.assertEqual(len(got), 2)  # held tokens count a full peak each until their JVM starts
            release.set()
            for t in ts:
                t.join(10)
            self.assertEqual(len(got), 4)


def holds(c):
    return len(list(pathlib.Path(c["ghidra_broker"]["dir"]).glob("h_*.json")))


def ghidra_cfg(c):  # enough for ghidra._headless with a fake run_bounded
    return dict(c, ghidra_home=str(TMP), java_home=str(TMP), ghidra_timeout_s=60, ghidra_parallel=1)


class EveryStart(unittest.TestCase):
    """Every Ghidra JVM start takes a token (ghidra._headless, sdk_fid.headless), a server's start a lease, one per
    JVM: a thread that holds one (ghidra.slot's) takes no second, and HOLD stops new starts only."""
    def setUp(self):
        self.m = Machine().__enter__()  # 85%: `slots` JVMs
        self.addCleanup(self.m.__exit__)

    def guard(self, s=10):  # a wait that would never end fails the test instead
        stop = threading.Event()
        t = threading.Timer(s, stop.set)
        t.start()
        self.addCleanup(t.cancel)
        return stop

    def test_nested(self):  # inside a token, ensure takes none: one hold, and no wait at slots 1
        c = cfg(1)
        with ghidra_broker.token(c, "worker", poll=0.01):
            with ghidra_broker.ensure(c, stop_flag=self.guard()) as waited:
                self.assertEqual((waited, holds(c), ghidra_broker.holding()), (0.0, 1, 1))
        self.assertEqual((holds(c), ghidra_broker.holding()), (0, 0))
        with ghidra_broker.ensure(c) as waited:  # outside one, a token of its own
            self.assertEqual((holds(c), ghidra_broker.holding()), (1, 1))

    def test_headless(self):  # a direct _headless (decomp.py prepare, regress, setup) and one under ghidra.slot
        c = ghidra_cfg(cfg(1))
        seen = []

        def run(cmd, timeout, env=None, on_line=None, stop_flag=None, **k):
            seen.append((holds(c), ghidra_broker.holding()))
            return 0, "", None
        with mock.patch.object(paths, "run_bounded", run):
            ghidra._headless(c, ["-process", "x"], "ghidra_test.log")
            with ghidra.slot(c, prio="worker", stop_flag=self.guard()):
                ghidra._headless(c, ["-process", "x"], "ghidra_test.log", stop_flag=self.guard())
        self.assertEqual(seen, [(1, 1), (1, 1)])
        self.assertEqual(holds(c), 0)

    def test_regress_threads(self):  # regress_project-style threads, each its own JVM: never over the rule
        for ram, cap in ((85.0, 2), (80.0, 3)):
            self.m.ram = ram
            c = ghidra_cfg(cfg(2))
            lock, now, peak = threading.Lock(), [0], [0]

            def run(cmd, timeout, env=None, on_line=None, stop_flag=None, **k):
                with lock:
                    now[0] += 1
                    peak[0] = max(peak[0], now[0])
                time.sleep(0.8)  # long enough that every free token is taken (waiters poll at 0.25 s)
                with lock:
                    now[0] -= 1
                return 0, "", None
            with mock.patch.object(paths, "run_bounded", run):
                ts = [threading.Thread(target=ghidra._headless, args=(c, ["-process", "x"], "ghidra_test.log"))
                      for _ in range(6)]
                for t in ts:
                    t.start()
                for t in ts:
                    t.join(30)
            self.assertEqual(peak[0], cap, ram)
            self.assertEqual(holds(c), 0)

    def test_hold(self):  # HOLD: no new token; a running holder (idle too) goes on and is not asked to give way
        c = cfg(2)
        d = pathlib.Path(c["ghidra_broker"]["dir"])
        idle_stop, release, held = threading.Event(), threading.Event(), threading.Event()

        def holder():
            with ghidra_broker.token(c, "idle", stop_flag=idle_stop, poll=0.01):
                held.set()
                release.wait(30)
        t = threading.Thread(target=holder)
        t.start()
        self.assertTrue(held.wait(10))
        (d / ghidra_broker.HOLD).write_text("", encoding="utf-8")
        with self.assertRaises(RuntimeError):
            with ghidra_broker.token(c, "worker", poll=0.01, stop_flag=self.guard(1)):
                pass
        self.assertFalse(idle_stop.is_set())
        self.assertEqual(holds(c), 1)
        (d / ghidra_broker.HOLD).unlink()
        with ghidra_broker.token(c, "worker", poll=0.01, stop_flag=self.guard()) as waited:
            self.assertLess(waited, 5)
        release.set()
        t.join(30)
        self.assertEqual(holds(c), 0)

    def test_hold_pass(self):  # a HOLD naming a pass lets only DP_BROKER_PASS=that key through, under the slot rule
        c = cfg(1)
        d = pathlib.Path(c["ghidra_broker"]["dir"])
        (d / ghidra_broker.HOLD).write_text(json.dumps({"pass": "w1"}), encoding="utf-8")
        for key in (None, "other"):
            with mock.patch.dict(os.environ):
                os.environ.pop(ghidra_broker.PASS_ENV, None)
                if key:
                    os.environ[ghidra_broker.PASS_ENV] = key
                with self.assertRaises(RuntimeError, msg=key):
                    with ghidra_broker.token(c, "worker", poll=0.01, stop_flag=self.guard(1)):
                        pass
        with mock.patch.dict(os.environ, {ghidra_broker.PASS_ENV: "w1"}):
            with ghidra_broker.token(c, "tool", poll=0.01, stop_flag=self.guard()) as waited:
                self.assertLess(waited, 5)
                self.assertEqual(holds(c), 1)
            self.assertTrue(ghidra_broker.held(d / "elsewhere") is False)  # no HOLD there
        for text in ("", "{}", "not json"):  # a HOLD without a usable pass holds everyone
            (d / ghidra_broker.HOLD).write_text(text, encoding="utf-8")
            with mock.patch.dict(os.environ, {ghidra_broker.PASS_ENV: "w1"}):
                self.assertTrue(ghidra_broker.held(d), text)
        self.assertEqual(holds(c), 0)

    def test_held_waits_outside_the_queue(self):
        """A process the HOLD stops files no ticket: at the queue's head it could never be granted, and the pass
        holders behind it waited with it (2026-10-23 12:42: a pytest run without the pass, 8 sessions' tickets behind
        it). Here a worker without the pass waits; a process with it is granted at once; the worker gets its token
        when the HOLD goes."""
        c = cfg(1)
        d = pathlib.Path(c["ghidra_broker"]["dir"])
        (d / ghidra_broker.HOLD).write_text(json.dumps({"pass": "w1"}), encoding="utf-8")
        stop, got, err = self.guard(120), [], io.StringIO()  # long: a loaded machine starts the child slowly

        def held_worker():
            try:
                with ghidra_broker.token(c, "worker", poll=0.01, stop_flag=stop):
                    got.append("worker")
            except RuntimeError:
                pass
        with mock.patch.dict(os.environ), mock.patch("sys.stderr", err):
            os.environ.pop(ghidra_broker.PASS_ENV, None)
            t = threading.Thread(target=held_worker)
            t.start()
            time.sleep(0.5)
            self.assertEqual(list(d.glob("t_*.json")), [])  # before: its ticket, first in the queue
            root = str(pathlib.Path(ghidra_broker.__file__).resolve().parents[1])
            child = (f"import sys; sys.path.insert(0, {root!r}); from core import ghidra_broker as g; "
                     "g.ram_pct = lambda: 50.0; g.processes = lambda: []; "  # the machine's load aside
                     f"cm = g.token({{'ghidra_broker': {{'slots': 9, 'dir': {str(d)!r}}}}}, 'tool', poll=0.01); "
                     "cm.__enter__(); print('granted', flush=True); cm.__exit__(None, None, None)")
            r = subprocess.run([sys.executable, "-c", child], capture_output=True, text=True, timeout=60,
                               env={**os.environ, ghidra_broker.PASS_ENV: "w1"})
            self.assertEqual(r.stdout.strip(), "granted")  # the pass holder is not kept behind the held worker
            self.assertEqual(got, [])
            (d / ghidra_broker.HOLD).unlink()
            t.join(110)
        self.assertEqual(got, ["worker"])
        self.assertIn("waiting outside the queue", err.getvalue())
        self.assertEqual(list(d.glob("[thy]_*.json")), [])

    def test_suite_folder(self):  # the suite never uses the machine's folder (tests/conftest.py, tests/test_sync.py)
        machine = pathlib.Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()) / "DecompPipeline" / "ghidra_broker"
        folder = ghidra_broker.settings(paths.config())[1]
        self.assertNotEqual(folder.resolve(), machine.resolve())
        self.assertEqual(folder, pathlib.Path(os.environ[ghidra_broker.DIR_ENV]))
        self.assertEqual(ghidra_broker.settings({"ghidra_broker": {"dir": str(TMP)}})[1], TMP)  # a config's own wins

    def test_server_start(self):  # the request that starts a server takes a lease, in the slot's prio; one to a
        # running server none, and the server keeps its token between requests
        c = dict(cfg(1), ghidra_timeout_s=60)
        scripts = pathlib.Path(tempfile.mkdtemp(prefix="scripts", dir=TMP))
        seen, leases = [], []

        class Fake:
            def __init__(self, cfg, slot, project, scripts, key, env, lease=None):
                seen.append((holds(c), ghidra_broker.holding(), lease is not None))
                self.key, self.n, self.busy, self.used, self.slot, self.lease = key, 0, 0, time.monotonic(), slot, lease
                self.settings = ghidra_server.settings(cfg)

            def alive(self):
                return True

            def ready(self, *a):
                pass

            def request(self, *a):
                self.n += 1
                return ["done\n"]

            def close(self, wait=30):
                self.lease.release()
        real = ghidra_broker.lease

        def lease(*a, **k):
            leases.append(a[1])
            return real(*a, **k)
        with mock.patch.object(ghidra_server, "Server", Fake), mock.patch.object(ghidra_broker, "lease", lease):
            try:
                with ghidra_broker.prio_of("prefetch"):
                    for _ in range(2):
                        ghidra_server.run(c, 7, TMP, scripts, "stamp", ["8c010000"], None)
                self.assertEqual(holds(c), 1)
            finally:
                ghidra_server.stop(7)
        self.assertEqual((seen, leases, holds(c)), ([(1, 0, True)], ["prefetch"], 0))

    def test_sdk_fid(self):
        c = ghidra_cfg(cfg(1))
        seen = []
        with mock.patch.object(sdk_fid, "run", lambda *a, **k: seen.append(holds(c)) or (0, "")):
            sdk_fid.headless(c, tempfile.mkdtemp(prefix="fid", dir=TMP), "p", ["-process", "x"], 60, None)
        self.assertEqual((seen, holds(c)), ([1], 0))

    def test_default_on(self):  # config.json turns the machine's rule on for every checkout
        cj = json.loads((pathlib.Path(ghidra_broker.__file__).resolve().parents[1] / "config.json")
                        .read_text(encoding="utf-8"))
        b = ghidra_broker.settings(cj)[0]
        self.assertEqual((b["slots"], b["stop_at"], b["by_ram"], b["fill_below"], b["stop_line"]),
                         (2, 88, True, 85, 95))


class Wiring(unittest.TestCase):
    def test_slot_takes_a_token(self):
        seen = []

        class Tok:
            def __init__(self, cfg, prio, stop_flag=None):
                seen.append(prio)

            def __enter__(self):
                return 0.0

            def __exit__(self, *exc):
                return False
        with mock.patch.object(ghidra_broker, "token", Tok):
            with ghidra.slot({"ghidra_parallel": 1}, prio="worker") as i:
                self.assertEqual(i, 0)
            with ghidra.slot({"ghidra_parallel": 1}):
                pass
        self.assertEqual(seen, ["worker", "tool"])

    def test_served_slot_takes_no_token(self):  # its server's lease stands for the JVM; the prio goes to the lease
        seen, prio = [], []

        class Tok:
            def __init__(self, cfg, prio, stop_flag=None):
                seen.append(prio)

            def __enter__(self):
                return 0.0

            def __exit__(self, *exc):
                return False
        c = {"ghidra_parallel": 3, "ghidra_serve": {"enabled": True}}
        with mock.patch.object(ghidra_broker, "token", Tok), mock.patch.object(ghidra_server, "up", lambda i: i == 2), \
                mock.patch.object(ghidra, "_copy_is_fresh", lambda i: True):
            with ghidra.slot(c, first=1, prio="worker") as i:
                prio.append(ghidra_broker.current_prio())
                self.assertEqual(i, 2)  # the warm slot first
            with ghidra.slot(c, prio="worker") as i, ghidra.slot(c, prio="worker") as j, \
                    ghidra.slot(c, prio="worker") as k:
                self.assertEqual(sorted((i, j, k)), [0, 1, 2])  # the master alone takes a token
        self.assertEqual((seen, prio, ghidra_broker.current_prio()), (["worker"], ["worker"], "tool"))

    def test_copy_stale_again_under_the_token(self):
        """The master changed while the copy's thread waited for its token: with the master free, the copy is
        refreshed under the token; with it busy, the token is given back and the copy refreshed without one."""
        for busy in (False, True):
            tokens, refreshed, checks = [], [], iter([True, False])  # fresh before the token, stale under it

            class Tok:
                def __init__(self, cfg, prio, stop_flag=None):
                    tokens.append(prio)

                def __enter__(self):
                    return 0.0

                def __exit__(self, *exc):
                    return False

            def fresh(i):
                return bool(refreshed) or next(checks, False)

            def free_master():
                with ghidra._SLOT_COND:
                    ghidra._slots_busy.discard(0)
                    ghidra._SLOT_COND.notify_all()
            with mock.patch.object(ghidra_broker, "token", Tok), mock.patch.object(ghidra_server, "up", lambda i: False),                     mock.patch.object(ghidra, "_copy_is_fresh", fresh),                     mock.patch.object(ghidra, "_refresh_copy", lambda i: refreshed.append((i, len(tokens)))):
                if busy:
                    with ghidra._SLOT_COND:
                        ghidra._slots_busy.add(0)
                    threading.Timer(0.3, free_master).start()
                with ghidra.slot({"ghidra_parallel": 2}, first=1) as i:
                    self.assertEqual(i, 1)
            self.assertEqual((tokens, refreshed), (["tool", "tool"], [(1, 1)]) if busy else (["tool"], [(1, 1)]))
            self.assertNotIn(0, ghidra._slots_busy)

    def test_job_priorities(self):  # a worker's own prepare and its prefetch say who they are
        src = pathlib.Path(job.__file__).read_text(encoding="utf-8")
        self.assertIn('ghidra.slot(cfg, w.stop_flag, prio="worker")', src)
        self.assertIn('ghidra.slot(o.cfg, w.stop_flag, prio="prefetch")', src)


class PassNotCaptured(unittest.TestCase):
    """The Visual Studio environment caches (state/msvc_env.json, msvc_env_x86.json, the port build's) once dumped the
    whole process environment: the broker's pass key under tools/wave.py, a session's token and address under an
    agent. Only the compiler's, Visual Studio's, the SDK's and the Windows variables are kept, and an older cache is
    cleaned when read."""
    SET = ("PATH=x\nDP_BROKER_PASS=k\nINCLUDE=y\nCLAUDE_CODE_MESSAGING_TOKEN=t\nsome_EMAIL=e\nno equals\n"
           "VS170COMNTOOLS=v\nWindowsSdkDir=w\nFrameworkDir=f\nVSCMD_ARG_TGT_ARCH=x64\nTmp=d\nVSCODE_IPC_HOOK=h\n")
    KEPT = {"PATH": "x", "INCLUDE": "y", "VS170COMNTOOLS": "v", "WindowsSdkDir": "w", "FrameworkDir": "f",
            "VSCMD_ARG_TGT_ARCH": "x64", "Tmp": "d"}

    def test_left_out(self):
        from core import check
        self.assertFalse(check.kept(ghidra_broker.PASS_ENV))
        self.assertEqual(check.captured(self.SET), self.KEPT)
        f = pathlib.Path(tempfile.mkdtemp(dir=TMP)) / "msvc_env.json"
        f.write_text(json.dumps({"PATH": "x", "dp_broker_pass": "k", "CLAUDE_CODE_MESSAGING_TOKEN": "t"}),
                     encoding="utf-8")
        self.assertEqual(check.env_cache(f), {"PATH": "x"})
        self.assertEqual(json.loads(f.read_text(encoding="utf-8")), {"PATH": "x"})

    def test_port_build(self):
        from core import check
        self.addCleanup(sys.path.__setitem__, slice(None), list(sys.path))  # port/tools' regress.py would shadow
        sys.path.insert(0, str(paths.TOOL / "port" / "tools"))                    # tools/regress.py for later tests
        import build
        self.assertEqual((build.CAPTURED, build.CAPTURED_RE.pattern), (check.CAPTURED, check.CAPTURED_RE.pattern))
        f = pathlib.Path(tempfile.mkdtemp(dir=TMP)) / "msvc_env.json"
        f.write_text(json.dumps({"PATH": "x", "DP_BROKER_PASS": "k", "CLAUDE_CODE_SESSION_ID": "s"}), encoding="utf-8")
        from core import toolcfg  # the cache's key (finding D) is tested in test_compiler_identity: current here
        import game_config
        with mock.patch.object(toolcfg, "cache_current", lambda *a: True), \
                mock.patch.object(game_config, "vcvars32", lambda: f.parent / "vcvars32.bat"):
            self.assertEqual(build.msvc_env(f), {"PATH": "x"})
        self.assertEqual(json.loads(f.read_text(encoding="utf-8")), {"PATH": "x"})


if __name__ == "__main__":
    unittest.main()
