"""One Ghidra per slot (core/ghidra_server), with Ghidra faked by a small Python server speaking TcServe's protocol.
1. serves(): only when enabled, only slots 1.., staged prepares only with "staged";
2. one server per slot answers request after request (its start-up lines go to the first request only); it is
   restarted when the scripts or the catalog stamp change and after max_requests;
3. a failed request, a server that exits, a timeout and a stop each raise and take the server (and its process
   tree) away; a launcher that fails ("Press any key to continue") raises at once instead of waiting on stdin;
4. the heap lines are kept; the idle reaper closes only an idle server, never one serving a request (even for a
   caller that holds no slot), and only when it can reserve the slot;
5. ghidra.prepare on a serving slot asks the server instead of starting a Ghidra, marks the request's start as the
   script's start, and writes the request's lines to the slot's log;
6. ghidra.slot(first=1) never yields the master (reprepare_stale --serve), and waits for a copy while all are busy;
7. with the machine-wide queue on, a server holds one Lease for its life (no token per request); a restart closes
   the old server before it waits, so at one slot its own lease never blocks its successor; give_back closes an
   idle server whose lease another waiter contests, never a busy one, and the next request takes a new lease.
Run: python -W error tests/test_ghidra_server.py (also from test_tooling.py).
"""
import json
import os
import pathlib
import re
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from test_prepare_timing import Case, Prepare  # noqa: E402

from core import ghidra, ghidra_broker, ghidra_server, paths  # noqa: E402

FAKE = '''import sys, time
if sys.argv[1] == "press":
    print("Press any key to continue . . .", flush=True)
    sys.stdin.readline()
    sys.exit(1)
print("INFO  Headless startup complete (1 ms) (AnalyzeHeadless)")
print("INFO  TcServe.java> TcServe: ready (GhidraScript)", flush=True)
n = 0
for line in sys.stdin:
    line = line.rstrip("\\n")
    if line == "QUIT":
        break
    if not line.startswith("REQ "):
        continue
    rid, *args = line[4:].split("\\t")
    print(f"INFO  TcServe.java> TcServe: begin {rid} (GhidraScript)", flush=True)
    if args[0] == "hang":
        time.sleep(600)
    if args[0] == "die":
        sys.exit(3)
    if args[0] == "slow":
        time.sleep(1)
    if args[0] == "err":
        print(f"INFO  TcServe.java> TcServe: done {rid} error java.lang.RuntimeException: boom (GhidraScript)")
    else:
        print("INFO  TcPrepare.java> TcPrepare: " + args[0] + " " + "|".join(args[1:]) + " (GhidraScript)")
        print(f"INFO  TcServe.java> TcServe: done {rid} ok 100 (GhidraScript)")
    n += 1
    if n % 2 == 0:
        print(f"INFO  TcServe.java> TcServe: heap {n} {100 + n} (GhidraScript)")
    sys.stdout.flush()
print(f"TcServe: quit after {n}", flush=True)
'''


class ServerCase(Case):
    def setUp(self):
        super().setUp()
        self.fake = self.root / "fake_server.py"
        self.fake.write_text(FAKE, encoding="utf-8")
        self.scripts = self.root / "scripts"
        self.scripts.mkdir()
        (self.scripts / "TcPrepare.java").write_text("class TcPrepare {}\n", encoding="utf-8")
        self.cmd = mock.patch.object(ghidra_server, "command", lambda cfg, project, scripts: [
            sys.executable, "-u", str(self.fake), pathlib.Path(project).name])
        self.cmd.start()
        self.cfg = {"ghidra_serve": {"enabled": True, "max_requests": 3}, "ghidra_timeout_s": 20}

    def tearDown(self):
        ghidra_server.stop_all()
        self.cmd.stop()
        super().tearDown()

    def run_(self, args, project="ok", stamp="s1", **k):
        return ghidra_server.run(self.cfg, 1, self.root / project, self.scripts, stamp, args, None, **k)

    def server(self):
        return ghidra_server._servers.get(1)


class Serves(ServerCase):
    def test_serves(self):
        self.assertFalse(ghidra_server.serves({}, 1, None))
        self.assertTrue(ghidra_server.serves(self.cfg, 1, None))
        self.assertFalse(ghidra_server.serves(self.cfg, 0, None))  # the master saves marks: always fresh
        self.assertFalse(ghidra_server.serves(self.cfg, 2, {"scripts": self.scripts}))  # staged: fresh
        staged = {"ghidra_serve": {"enabled": True, "staged": True}}  # unless asked (gates, regress, probes)
        self.assertTrue(ghidra_server.serves(staged, 2, {"scripts": self.scripts}))
        self.assertFalse(ghidra_server.serves(staged, 0, {"scripts": self.scripts}))

    def test_requests_and_restarts(self):
        out = self.run_(["8c010000", "p", "h"])
        self.assertIn("TcServe: ready", out)
        self.assertIn("TcPrepare: 8c010000 p|h", out)
        first = self.server()
        out = self.run_(["8c010100"])
        self.assertNotIn("TcServe: ready", out)  # the same server: no start-up lines
        self.assertIn("TcPrepare: 8c010100", out)
        self.assertIs(self.server(), first)
        self.run_(["8c010200"])
        self.assertIs(self.server(), first)
        self.assertEqual(first.heap, [["2", "102"]])  # printed after request 2's result: read with request 3's
        self.run_(["8c010300"])  # max_requests 3: a new server
        self.assertIsNot(self.server(), first)
        self.assertFalse(first.alive())
        second = self.server()
        self.run_(["8c010400"], stamp="s2")  # the catalog changed
        self.assertIsNot(self.server(), second)
        third = self.server()
        time.sleep(0.05)
        (self.scripts / "TcOther.java").write_text("class TcOther {}\n", encoding="utf-8")  # a script sync
        self.run_(["8c010500"], stamp="s2")
        self.assertIsNot(self.server(), third)
        self.assertFalse(second.alive() or third.alive())

    def failing(self, args, message, **k):
        self.run_(["8c010000"])
        srv = self.server()
        t0 = time.monotonic()
        with self.assertRaisesRegex(RuntimeError, message):
            self.run_(args, **k)
        self.assertIsNone(self.server())
        self.assertFalse(srv.alive())
        self.assertIn("TcPrepare: 8c010600", self.run_(["8c010600"]))  # the next request starts a new server
        return time.monotonic() - t0

    def test_failed_request(self):
        self.failing(["err"], r"Ghidra failed \(server, slot 1\): java.lang.RuntimeException: boom")

    def test_server_exits(self):
        self.failing(["die"], r"Ghidra server \(slot 1\) exited")

    def test_timeout_kills_the_server(self):
        self.cfg["ghidra_timeout_s"] = 1
        self.assertLess(self.failing(["hang"], "Ghidra timeout"), 15)

    def test_stop(self):
        flag = threading.Event()
        threading.Timer(0.5, flag.set).start()
        self.failing(["hang"], "Ghidra stopped", stop_flag=flag)

    def test_launcher_failure(self):
        t0 = time.monotonic()
        with self.assertRaisesRegex(RuntimeError, r"failed to start:\n.*Press any key"):
            self.run_(["8c010000"], project="press")
        self.assertLess(time.monotonic() - t0, 10)
        self.assertIsNone(self.server())

    def test_idle_reaper(self):
        self.run_(["8c010000"])
        srv = self.server()
        srv.used -= 1000
        with mock.patch.object(ghidra, "try_slot", return_value=False):  # a request holds the slot: keep it
            ghidra_server._close_if_idle(1, 120)
        self.assertIs(self.server(), srv)
        released = []
        with mock.patch.object(ghidra, "try_slot", return_value=True), \
                mock.patch.object(ghidra, "release_slot", released.append):
            ghidra_server._close_if_idle(1, 2000)  # not idle that long
            self.assertIs(self.server(), srv)
            srv.busy = 1  # a request in flight from a caller that holds no slot (regress, probes)
            ghidra_server._close_if_idle(1, 120)
            self.assertIs(self.server(), srv)
            srv.busy = 0
            ghidra_server._close_if_idle(1, 120)
        self.assertIsNone(self.server())
        self.assertFalse(srv.alive())
        self.assertEqual(released, [1, 1, 1])

    def test_long_request_outlives_idle_s(self):
        self.cfg["ghidra_serve"]["idle_s"] = 0
        self.run_(["8c010000"])
        srv = self.server()
        with mock.patch.object(ghidra, "try_slot", return_value=True), \
                mock.patch.object(ghidra, "release_slot"):
            reaped = threading.Timer(0.3, ghidra_server._close_if_idle, (1, 0))
            reaped.start()
            self.assertIn("TcPrepare: slow", self.run_(["slow"]))  # the fake takes 1 s: the reaper must wait
            reaped.join()
        self.assertIs(self.server(), srv)
        self.assertEqual(srv.busy, 0)


class Leased(ServerCase):
    def setUp(self):
        super().setUp()
        self.d = pathlib.Path(tempfile.mkdtemp(prefix="broker", dir=self.root))
        self.cfg["ghidra_broker"] = {"slots": 1, "dir": str(self.d)}
        for x in (mock.patch.object(ghidra_broker, "ram_pct", lambda: 50.0),  # the fake servers are no JVMs
                  mock.patch.object(ghidra_broker, "processes", lambda: [])):
            x.start()
            self.addCleanup(x.stop)
        self.guard = threading.Event()  # a wait that would never end fails the test instead
        timer = threading.Timer(15, self.guard.set)
        timer.start()
        self.addCleanup(timer.cancel)

    def holds(self):
        return len(list(self.d.glob("h_*.json")))

    def waiter(self, age_s, prio=2):  # another process's ticket, waiting age_s seconds
        (self.d / f"t_{prio}_{time.time_ns() - int(age_s * 1e9):020d}_{os.getpid() + 1}_1.json").write_text(
            "{}", encoding="utf-8")

    def test_one_lease_across_requests(self):
        for a in ("8c010000", "8c010100", "8c010200"):
            self.run_([a], stop_flag=self.guard)
            self.assertEqual((self.holds(), ghidra_broker.holding()), (1, 0))
        first = self.server()
        self.run_(["8c010300"], stop_flag=self.guard)  # max_requests 3 at one slot: closed first, then a new lease
        self.run_(["8c010400"], stamp="s2", stop_flag=self.guard)  # the catalog changed: the same
        self.assertFalse(self.guard.is_set())
        self.assertFalse(first.alive())
        self.assertEqual(self.holds(), 1)
        ghidra_server.stop(1)
        self.assertEqual(self.holds(), 0)

    def test_give_back(self):
        self.run_(["8c010000"], stop_flag=self.guard)
        srv = self.server()
        self.assertFalse(ghidra_server.give_back(1))  # nobody waits
        self.waiter(1)
        self.assertFalse(ghidra_server.give_back(1))  # a short wait, a server just used: it keeps serving
        srv.used -= 5
        srv.busy = 1
        self.assertFalse(ghidra_server.give_back(1))  # idle for yield_idle_s, but a request is in flight
        srv.busy = 0
        self.assertTrue(ghidra_server.give_back(1))
        self.assertEqual((self.server(), srv.alive(), self.holds()), (None, False, 0))
        for p in self.d.glob("t_*.json"):
            p.unlink()
        self.run_(["8c010100"], stop_flag=self.guard)  # the next request: a new server, a new lease
        self.waiter(31)  # waited lease_s: given back right after the request, busy servers or not
        self.run_(["8c010200"], stop_flag=self.guard)
        self.assertEqual((self.server(), self.holds()), (None, 0))

    def test_broker_off_no_lease(self):  # ServerCase's cfg: no queue, the server starts at once
        del self.cfg["ghidra_broker"]
        self.run_(["8c010000"])
        self.assertIsNone(self.server().lease)


class FirstSlot(unittest.TestCase):
    def test_first_slot_is_a_copy(self):
        cfg = {"ghidra_parallel": 3}
        with mock.patch.object(ghidra, "_copy_is_fresh", lambda i: True):
            with ghidra.slot(cfg, first=1) as i, ghidra.slot(cfg, first=1) as j:
                self.assertEqual((i, j), (1, 2))
                got = []
                t = threading.Thread(target=lambda: got.append(ghidra.slot(cfg, first=1).__enter__()))
                t.start()
                t.join(1.5)
                self.assertEqual(got, [])  # slot 0 is free, but a copy is asked for: it waits
            t.join(5)
            self.assertIn(got, ([1], [2]))
            ghidra.release_slot(got[0])
            with ghidra.slot(cfg) as k:
                self.assertEqual(k, 0)  # the default still prefers the master

    def test_served_slot_first(self):  # with slot servers on: a running server, then a copy, the master last
        cfg = {"ghidra_parallel": 3, "ghidra_serve": {"enabled": True}}
        marks = pathlib.Path(self.enterContext(mock.patch.object(paths, "MARKS", pathlib.Path(
            self.enterContext(tempfile.TemporaryDirectory())) / "ghidra_marks.tsv")))
        with mock.patch.object(ghidra, "_copy_is_fresh", lambda i: True):
            with mock.patch.object(ghidra_server, "up", lambda i: i == 2), ghidra.slot(cfg) as i:
                self.assertEqual(i, 2)  # the warm one
            with mock.patch.object(ghidra_server, "up", lambda i: False):
                with ghidra.slot(cfg) as i:
                    self.assertEqual(i, 1)  # no server runs: a copy (its prepare starts one), not the master
                    with ghidra.slot(cfg) as j, ghidra.slot(cfg) as k:
                        self.assertEqual((j, k), (2, 0))  # the master when the copies are busy
                marks.write_text("8c010000\tmatched\t-\n" * (ghidra.MARKS_BATCH - 1), encoding="utf-8")
                with ghidra.slot(cfg) as i:
                    self.assertEqual(i, 1)  # marks wait, but fewer than a batch
                marks.write_text("8c010000\tmatched\t-\n" * ghidra.MARKS_BATCH, encoding="utf-8")
                with ghidra.slot(cfg) as i:
                    self.assertEqual(i, 0)  # a batch of marks waits: the master writes them
            with mock.patch.object(ghidra_server, "up", lambda i: i == 2), ghidra.slot({"ghidra_parallel": 3}) as i:
                self.assertEqual(i, 0)  # slot servers off: the master first, as before


class PrepareServed(Prepare):
    test_steps_in_order = test_failure_recorded = None  # Prepare's own tests run from test_prepare_timing

    def test_prepare_asks_the_server(self):
        st = self.staging()
        calls, facts = [], []
        fake = self.fake_run()
        java = (pathlib.Path(ghidra_server.__file__).parent / "ghidra_serve" / "TcServe.java").read_text(encoding="utf-8")
        facts_arg = int(re.search(r"static final int FACTS_ARG = (\d+);", java).group(1))

        def run(cfg, slot, project, scripts, stamp, args, env, on_line=None, stop_flag=None):
            calls.append((slot, pathlib.Path(project).name, args[0]))
            facts.append(args[facts_arg])
            begin = "INFO  TcServe.java> TcServe: begin 0 (GhidraScript)\n"
            on_line(begin)
            return begin + fake(["TcPrepare.java", *args], 60, on_line=on_line)[1]
        cfg = dict(paths.config(), ghidra_home=str(self.root), java_home=str(self.root), ghidra_timeout_s=60,
                   vectors_dev=48, vectors_hidden=48)
        timing = {}
        with mock.patch.object(ghidra_server, "serves", return_value=True), \
                mock.patch.object(ghidra_server, "run", run), \
                mock.patch.object(ghidra, "_stamp", return_value="s"), \
                mock.patch.object(paths, "run_bounded", side_effect=AssertionError("a fresh Ghidra")):
            meta, summary = ghidra.prepare(cfg, "8c010000", slot_index=1, staging=st, timing=timing)
        self.assertEqual(calls, [(1, "slot1", "8c010000")])
        self.assertEqual(facts, [ghidra.write_facts(cfg)])  # TcServe shifts the deadline in the right file
        self.assertTrue(facts[0].endswith(".txt"), facts)  # a real facts file, not a "-" that any place matches
        self.assertEqual(summary, {"dev_valid": 48})
        self.assertIn("oracle_stamp", meta)
        self.assertLessEqual(timing["marks"]["script_start"], timing["marks"]["script_done"])
        self.assertIn("exit", timing["marks"])
        log = (self.state / "ghidra_prepare_staging_slot1.log").read_text(encoding="utf-8")
        self.assertTrue(log.startswith("INFO  TcServe.java> TcServe: begin 0"))
        self.assertEqual(json.loads((st["harness"] / "oracle_summary.json").read_text(encoding="utf-8")),
                         {"dev_valid": 48})


del Case, Prepare  # imported for subclassing only: their tests run from test_prepare_timing


if __name__ == "__main__":
    unittest.main()
