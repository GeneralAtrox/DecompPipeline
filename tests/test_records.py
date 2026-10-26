"""The scorecard's own records (docs/DEFINITION_OF_DONE.md), and that they are records only:
1. core/busylog: a worker's events in its process's own file; a nested span counts once; DP_NO_RECORDS=1; a missing
   folder never raises; refine.py run's worker slots;
2. core/oplog: a hand start, a stop in the driver's own words, a driver's allowed exit code, Ctrl+C as a person's act,
   a driver started by another driver writes nothing (DP_RUN reaches child processes), a port game folder;
3. the hooks: the raw judge's span (core/job), a worker's start and end and a fatal stop (core/orch), the monitor
   page's buttons as acts and a tool's calls not (core/server), a readable attempt's duration_s and its spans
   (core/refine under core/fn_claim.cycle);
4. records only: the raw judge's attempt and check.json, and a readable cycle's ledger row, byte-identical with the
   records on and off (only duration_s, a time, is left out of the comparison).
Run: python tests/test_records.py (also from test_tooling.py).
"""
import json
import os
import pathlib
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from test_sync import TMP  # noqa: E402  (sets DECOMP_PROJECT when run alone)

from core import busylog, check, fn_claim, job, oplog, orch, paths, refine, server  # noqa: E402

A = "8c010000"


def tmpdir():
    return pathlib.Path(tempfile.mkdtemp(prefix="rec", dir=TMP))


def busy_events(state):
    files = sorted((state / busylog.DIR).glob("*.log"))
    return [ln.split()[1:] for f in files for ln in f.read_text(encoding="ascii").splitlines()]


def op_records(root):
    p = root / "state" / oplog.FILE
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []


class Records(unittest.TestCase):
    """A workspace with state/ (paths.STATE and paths.ROOT point at it), the records on and no run inherited."""

    def setUp(self):
        self.ws = tmpdir()
        (self.ws / "state").mkdir()
        (self.ws / "project.json").write_text("{}", encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if k not in (oplog.ENV, "DP_NO_RECORDS")}
        self.patches = [mock.patch.object(paths, "STATE", self.ws / "state"), mock.patch.object(paths, "ROOT", self.ws),
                        mock.patch.dict(os.environ, env, clear=True), mock.patch.dict(oplog._RUN, {}, clear=True)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()


class BusyLog(Records):
    def test_spans(self):
        busylog.event("W1", "+")
        with busylog.busy("W1", "a"):
            with busylog.busy("W1", "j"):  # nested: part of the agent's span
                pass
        with busylog.busy(None, "j"):  # no worker: nothing
            pass
        busylog.event("W1", "-")
        self.assertEqual(busy_events(self.ws / "state"), [["W1", "+"], ["W1", "a"], ["W1", "."], ["W1", "-"]])
        (f,) = (self.ws / "state" / busylog.DIR).iterdir()
        self.assertTrue(f.name.startswith(f"{os.getpid()}-"))  # this process's own file
        line = f.read_text(encoding="ascii").splitlines()[0]
        self.assertLess(len(line), 24)  # a few bytes per event
        self.assertAlmostEqual(float(line.split()[0]), time.time(), delta=60)

    def test_off_and_missing(self):
        with mock.patch.dict(os.environ, {"DP_NO_RECORDS": "1"}):
            busylog.event("W1", "+")
        self.assertFalse((self.ws / "state" / busylog.DIR).exists())
        with mock.patch.object(paths, "STATE", self.ws / "gone" / "state"):
            busylog.event("W1", "+")  # never raises

    def test_slots(self):
        self.assertIsNone(busylog.slot())  # outside refine.py run: nothing recorded
        busylog.start_slots(2)
        keys = []
        threads = [threading.Thread(target=lambda: keys.append((busylog.slot(), busylog.slot()))) for _ in range(2)]
        for t in threads:
            t.start()
            t.join()
        busylog.end_slots(2)
        self.assertEqual(sorted(keys), [("R0", "R0"), ("R1", "R1")])
        self.assertIsNone(busylog.slot())
        self.assertEqual(busy_events(self.ws / "state"), [["R0", "+"], ["R1", "+"], ["R0", "-"], ["R1", "-"]])


class OpLog(Records):
    def run_driver(self, body, roots=None, ok=(0,)):
        def main():
            oplog.begin("tool.py", roots or [self.ws])
            return body()
        return oplog.main(main, ok=ok)

    def test_start_and_finished(self):
        self.assertEqual(self.run_driver(lambda: 0), 0)
        (r,) = op_records(self.ws)
        self.assertEqual((r["kind"], r["tool"]), ("start", "tool.py"))
        self.assertTrue(r["run"])
        self.assertNotIn(oplog.ENV, os.environ)  # the run ends with its driver

    def test_stop_in_its_own_words(self):
        with self.assertRaises(SystemExit):
            self.run_driver(lambda: sys.exit("the project's server is running: stop it first"))
        start, stop = op_records(self.ws)
        self.assertEqual((stop["kind"], stop["step"]), ("stop", "the project's server is running: stop it first"))
        self.assertEqual(stop["run"], start["run"])
        self.run_driver(lambda: 1)
        self.assertEqual(op_records(self.ws)[-1]["step"], "exit 1")

    def test_allowed_code_error_and_interrupt(self):
        self.run_driver(lambda: 2, ok=(0, 2))  # idle_prepare gave way: nobody needed
        self.assertEqual([r["kind"] for r in op_records(self.ws)], ["start"])

        def boom():
            raise RuntimeError("no catalog")
        with self.assertRaises(RuntimeError):
            self.run_driver(boom)

        def ctrl_c():
            raise KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            self.run_driver(ctrl_c)
        kinds = [(r["kind"], r["step"]) for r in op_records(self.ws)]
        self.assertEqual(kinds[2], ("stop", "RuntimeError: no catalog"))
        self.assertEqual(kinds[4][0], "act")

    def test_nested_writes_nothing(self):
        seen = {}

        def body():
            out = subprocess.run([sys.executable, "-c", f"import os; print(os.environ.get('{oplog.ENV}'))"],
                                 capture_output=True, text=True, timeout=60)
            seen["child"] = out.stdout.strip()
            seen["run"] = oplog.begin("inner.py", [self.ws])  # a second driver in the same run
            return 0
        self.run_driver(body)
        (r,) = op_records(self.ws)
        self.assertEqual(seen, {"child": r["run"], "run": r["run"]})
        with mock.patch.dict(os.environ, {oplog.ENV: "outer-run"}):  # started by a driver: no start, no stop
            self.assertEqual(oplog.main(lambda: oplog.begin("child.py", [self.ws]) and 1), 1)
        self.assertEqual(len(op_records(self.ws)), 1)

    def test_places(self):
        game = tmpdir()
        (game / "game.json").write_text("{}", encoding="utf-8")
        bare = tmpdir()  # neither a workspace's state/ nor a game folder: nothing written there
        oplog.record([game, bare, None], "port build.py", "stop", "cmake configure failed (1)", run="r")
        self.assertEqual(op_records(game)[0]["step"], "cmake configure failed (1)")
        self.assertEqual(list(bare.iterdir()), [])
        self.assertEqual(oplog.workspace(str(self.ws)), self.ws.resolve())
        with mock.patch.dict(os.environ, {"DP_NO_RECORDS": "1"}):
            oplog.record([self.ws], "x", "act", "y")
        self.assertEqual(op_records(self.ws), [])


class FakeO:
    def __init__(self):
        self.cfg, self.lock, self.ledger, self.workers = {}, threading.RLock(), {"functions": {}}, {}
        self.retire_pending, self.lines, self.stopped = None, [], []

    def claim(self, w, addr=None, current=True):
        return addr

    def rec(self, addr):
        return self.ledger["functions"].setdefault(addr, {"attempts": []})

    def save(self):
        pass

    def log(self, m):
        self.lines.append(m)

    def stop_all(self, why):
        self.stopped.append(why)


class Hooks(Records):
    def test_worker_start_end_and_fatal_stop(self):
        o = FakeO()
        orch.Worker(o, 3).run()  # an empty queue
        w = orch.Worker(o, 4, A)
        with mock.patch.object(job, "investigate", side_effect=job.Fatal("total spend reached budget_total_usd")):
            w.run()
        self.assertEqual(busy_events(self.ws / "state"), [["W3", "+"], ["W3", "-"], ["W4", "+"], ["W4", "-"]])
        (r,) = op_records(self.ws)
        self.assertEqual((r["kind"], r["tool"]), ("stop", "orch"))
        self.assertIn("budget_total_usd", r["step"])

    def test_monitor_buttons(self):
        o = mock.Mock(cfg={"name": "fixture"})
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]

        class Two(server.ThreadingHTTPServer):
            def serve_forever(self):
                for _ in range(2):
                    self.handle_request()
                self.server_close()
        with mock.patch.object(server, "ThreadingHTTPServer", Two), mock.patch("builtins.print"):
            t = threading.Thread(target=server.serve, args=(o, port), daemon=True)
            t.start()
            for origin in (f"http://127.0.0.1:{port}", None):  # the page, then a tool (tools/requeue.py)
                for _ in range(50):
                    try:
                        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/loop", b'{"enabled": true}',
                                                     {"Origin": origin} if origin else {})
                        urllib.request.urlopen(req, timeout=10).read()
                        break
                    except OSError:
                        time.sleep(0.1)
            t.join(30)
        self.assertEqual(o.set_loop.call_count, 2)
        (r,) = op_records(self.ws)
        self.assertEqual((r["kind"], r["tool"]), ("act", "monitor"))
        self.assertIn("/api/loop", r["step"])


RES = {"verdict": "FAIL", "stage": "compare", "score": 3, "lint": [], "build": [], "first_diffs": [{"vector": 3, "inputs": "r4=1", "diffs": ["r0"]}],
       "vectors": {"dev": {"failing": 2, "total": 64}, "hidden": {"failing": 1, "total": 32}}, "failing": 3,
       "total": 96}


class RawJudge(Records):
    def judge(self):
        run = tmpdir()
        (run / "work.c").write_text("int f(void) { return 1; }\n", encoding="utf-8")
        w = mock.Mock(id=5)
        with mock.patch.object(check, "run_check", lambda *a, **k: json.loads(json.dumps(RES))):
            att = job.judge(FakeO(), w, A, 1, {"n": 1, "model": "haiku"}, run / "work.c", run)
        return json.dumps(att, sort_keys=True), (run / "check.json").read_bytes()

    def test_same_verdict_records_on_and_off(self):
        on = self.judge()
        self.assertEqual(busy_events(self.ws / "state"), [["W5", "j"], ["W5", "."]])
        with mock.patch.dict(os.environ, {"DP_NO_RECORDS": "1"}):
            off = self.judge()
        self.assertEqual(on, off)
        self.assertEqual(len(busy_events(self.ws / "state")), 2)  # off: nothing more


def pass_check(*a, **k):
    return {"verdict": "PASS", "stage": "compare", "failing": 0, "total": 96}


class Readable(Records):
    """fn_claim.cycle around the real refine.refine, the agent, sandbox and judge faked (as test_batch21.Cycle)."""

    def cycle(self, slots=0):
        d = tmpdir()
        for sub in ("src", "box", "locks"):
            (d / sub).mkdir()
        (d / "box" / "work.c").write_text("/* attempt */", encoding="utf-8")

        def attempt(*a):
            time.sleep(0.25)
            return {"cost_usd": 0.01}
        patches = [mock.patch.object(refine, "LEDGER", d / "readable.json"),
                   mock.patch.object(refine.readable, "SRC", d / "src"),
                   mock.patch.object(refine.readable, "Registry", lambda: None),
                   mock.patch.object(paths, "RUNS", d / "runs"),
                   mock.patch.object(fn_claim, "LOCKS", d / "locks"),
                   mock.patch.object(refine, "raw_missing", lambda a: None),
                   mock.patch.object(refine, "harness_stale", lambda cfg, a, **k: None),
                   mock.patch.object(refine, "preflight", lambda cfg, reg, a, meta: None),
                   mock.patch.object(refine, "build_sandbox", lambda *a: (d / "box", "task")),
                   mock.patch.object(refine, "tree_hashes", lambda box: {}),
                   mock.patch.object(refine, "now", lambda: "2026-10-22T00:00:00"),
                   mock.patch.object(refine.agent, "run_attempt", attempt),
                   mock.patch.object(refine.rcheck, "check_readable", pass_check)]
        for p in patches:
            p.start()
        try:
            if slots:
                busylog.start_slots(slots)
            cfg = {"model": "m", "effort": None, "readable_effort": None, "function_lock_wait_s": 0.2}
            status = fn_claim.cycle(cfg, A, 3, lambda m: None)
            if slots:
                busylog.end_slots(slots)
            return status, refine.load_ledger()[A]
        finally:
            for p in reversed(patches):
                p.stop()

    def test_duration_and_spans(self):
        status, row = self.cycle(slots=1)
        self.assertEqual(status, "readable")
        (att,) = row["attempts"]
        self.assertGreaterEqual(att["duration_s"], 0.2)  # the agent session, as a raw attempt's
        self.assertLess(att["duration_s"], 30)
        ev = busy_events(self.ws / "state")
        self.assertEqual(ev[0], ["R0", "+"])
        self.assertEqual(ev[-1], ["R0", "-"])
        self.assertEqual([c for _, c in ev[1:-1]], ["j", ".", "j", ".", "a", ".", "j", "."])  # draft, preflight

    def test_same_row_records_on_and_off(self):
        def strip(row):
            return json.dumps({**row, "attempts": [{k: v for k, v in a.items() if k != "duration_s"}
                                                   for a in row["attempts"]]}, sort_keys=True)
        on = self.cycle(slots=1)
        with mock.patch.dict(os.environ, {"DP_NO_RECORDS": "1"}):
            off = self.cycle(slots=1)
        self.assertEqual((on[0], strip(on[1])), (off[0], strip(off[1])))
        self.assertIn("duration_s", off[1]["attempts"][0])  # a time on the attempt, as raw attempts carry, not a record


if __name__ == "__main__":
    unittest.main(verbosity=1)
