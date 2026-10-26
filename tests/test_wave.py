"""tools/wave.py, the re-stamp wave driver: its output parsing, the recatalog check, the estimate, and the guard that
kills only its own child's tree (a fake child; Ghidra is never run).
Run: python -W error tests/test_wave.py (also from test_tooling.py's discovery).
"""
import json
import pathlib
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools"))
from test_sync import TMP  # noqa: E402  (sets DECOMP_PROJECT when run alone)

import wave  # noqa: E402

CHUNK = """stamps: 812 matched harness(es) not current by oracle stamp; this run re-prepares 300 \
(2 Ghidra slot(s) at once)
  8c010000: prepare failed: Ghidra timed out
  8c010200: re-prepared; its matched raw C FAILS the new harness: dev 3/48
  300/300 re-prepared
stamps: 299 re-prepared, 1 prepare error(s), 1 raw C failing the new harness (8c010200); 513 left for the next run
4 prepare(s) of 3 function(s) in 2 round(s); still stale: 0; stuck: 0; prepare errors: 0; raw C failing its new \
harness: 0
"""


def fn(addr, **kw):
    return {"addr": addr, "insns": 10, "hw_refs": 0, "sys_ops": 0, **kw}


class Parse(unittest.TestCase):
    def test_chunk(self):
        c = wave.parse(CHUNK)
        self.assertEqual((c["stale"], c["stamped"], c["errors"], c["left"], c["cascade"], c["still"]),
                         (812, 299, 1, 513, 4, 0))
        self.assertEqual(c["failed"], ["8c010200"])

    def test_empty(self):  # a killed chunk prints nothing: unknown counts, nothing re-prepared
        c = wave.parse("")
        self.assertEqual((c["stale"], c["stamped"], c["left"], c["still"]), (-1, 0, -1, -1))


class Catalog(unittest.TestCase):
    OLD = {"functions": [fn("8c010000", sys_ops=2), fn("8c010100", hw_refs=1), fn("8c010200")]}

    def test_counts_only(self):  # sys_ops -> sr_ops, hw_refs -> sq_refs: excluded -> hard is no problem
        new = {"functions": [fn("8c010000", sr_ops=2), fn("8c010100", sq_refs=1), fn("8c010200")]}
        t_old = {"8c010000": "excluded", "8c010100": "excluded", "8c010200": "easy"}
        t_new = {"8c010000": "hard", "8c010100": "hard", "8c010200": "easy"}
        self.assertEqual(wave.catalog_check(self.OLD, new, t_old, t_new), [])

    def test_refused(self):
        new = {"functions": [fn("8c010000", insns=11), fn("8c010100", hw_refs=1), fn("8c010300")]}
        t_old = {"8c010000": "easy", "8c010100": "excluded", "8c010200": "easy"}
        t_new = {"8c010000": "excluded", "8c010100": "excluded"}
        bad = " ".join(wave.catalog_check(self.OLD, new, t_old, t_new))
        for want in ("functions gone: ['8c010200']", "functions new: ['8c010300']", "fields other than",
                     "in-scope functions gone: ['8c010200']", "newly excluded: ['8c010000']"):
            self.assertIn(want, bad)


class Estimate(unittest.TestCase):
    def test_served(self):  # served = fresh - overhead, at least the floor; the median fills a gap; tail >= LONG_S
        fresh, served, tail = wave.estimate({"a": 10.0, "b": 400.0, "c": 3.0}, ["a", "b", "c", "d"], overhead=7.0)
        self.assertEqual(fresh, 10 + 400 + 3 + 10)  # d: the median, 10
        self.assertEqual(served, 3 + 393 + 1 + 3)
        self.assertEqual(tail, 393)

    def test_eta(self):  # the wave's own seconds per prepare times what is left, est for workspaces not started
        st = {"ws": {"tc": {"prepared": 100, "seconds": 1000.0, "left": 50}}, "est": {"tc": 999, "ps2": 20}}
        self.assertEqual(wave.eta(st, ["tc", "ps2"]), (50 + 20) * 10.0)


NOTE = """# fused fmac: requeue candidates

## ToyCommander (C:/g/tc)

2 functions with fmac, 2 matched; unmatched: none

8c010838 8c01134c

## Spawn (C:/g/spawn/main)

3 functions with fmac, 2 matched; unmatched: 8c063c00; already sh4_fmac: 0

8c0164e0
8c0165b6
"""


class Fmac(unittest.TestCase):
    def setUp(self):
        d = pathlib.Path(tempfile.mkdtemp(prefix="wave", dir=TMP))
        (d / "note.md").write_text(NOTE, encoding="utf-8")
        self.ps = [mock.patch.object(wave, "STATE_DIR", d), mock.patch.object(wave, "STATE", d / "state.json"),
                   mock.patch.object(wave, "LOG", d / "wave.log"),
                   mock.patch.object(wave, "WS", {"fmac_list": d / "note.md"})]
        for p in self.ps:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in self.ps])
        self.d = d

    def test_lists(self):  # header paths key the lists; the "unmatched:" line is no candidate
        fm = wave.fmac_lists()
        self.assertEqual(fm[wave.norm("C:/g/tc")], ["8c010838", "8c01134c"])
        self.assertEqual(fm[wave.norm("C:/g/spawn/main")], ["8c0164e0", "8c0165b6"])
        with mock.patch.object(wave, "WS", {}):
            self.assertEqual(wave.fmac_lists(), {})

    def test_rejudge(self):  # the child's JSON: flips to fmac-flips-KEY.txt, counts in the state; done once
        res = {"judged": 2, "pass": 1, "flips": ["8c01134c"], "detail": {"8c01134c": "run: 3/96 vectors differ"},
               "other": [], "not_matched": [], "not_current": []}
        st = {"ws": {"tc": {}}}
        with mock.patch.object(wave, "wait_ram", return_value=50.0), \
                mock.patch.object(wave, "guarded", side_effect=[("guard", ""), (0, "x\n" + json.dumps(res))]) as g:
            wave.rejudge(st, "tc", pathlib.Path("C:/g/tc"), ["8c010838", "8c01134c"])
            wave.rejudge(st, "tc", pathlib.Path("C:/g/tc"), ["8c010838", "8c01134c"])
        self.assertEqual(g.call_count, 2)  # one guard kill, one run; the second call finds it done
        self.assertEqual((self.d / "fmac-flips-tc.txt").read_text(encoding="utf-8"), "8c01134c\n")
        self.assertEqual(st["ws"]["tc"]["fmac"], {"judged": 2, "pass": 1, "flips": 1, "detail": 1, "other": 0,
                                                  "not_matched": 0, "not_current": 0})


class Requeue(unittest.TestCase):  # run --requeue: each workspace's flips to tools/requeue.py, once
    def setUp(self):
        d = pathlib.Path(tempfile.mkdtemp(prefix="wave", dir=TMP))
        self.ps = [mock.patch.object(wave, "STATE_DIR", d), mock.patch.object(wave, "STATE", d / "state.json"),
                   mock.patch.object(wave, "LOG", d / "wave.log"),
                   mock.patch.object(wave, "order", lambda: [("tc", pathlib.Path("C:/g/tc")),
                                                             ("ps2", pathlib.Path("C:/g/ps2"))])]
        for p in self.ps:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in self.ps])
        self.d = d

    def test_once_per_workspace(self):
        (self.d / "fmac-flips-tc.txt").write_text("8c01134c\n", encoding="utf-8")
        (self.d / "fmac-flips-ps2.txt").write_text("", encoding="utf-8")  # no flips: nothing to requeue
        st = {"steps": {}}
        done = mock.Mock(returncode=0, stdout="requeued 1", stderr="")
        with mock.patch.object(wave.subprocess, "run", return_value=done) as r:
            wave.requeue(st)
            wave.requeue(st)
        self.assertEqual(r.call_count, 1)
        cmd = r.call_args[0][0]
        self.assertEqual(cmd[1:3], ["-B", str(wave.me().parent / "requeue.py")])
        self.assertEqual(cmd[3:5], ["--project", str(pathlib.Path("C:/g/tc"))])
        self.assertEqual(cmd[-1], f"@{self.d / 'fmac-flips-tc.txt'}")
        self.assertIn("requeue_tc", st["steps"])

    def test_failed_is_tried_again(self):
        (self.d / "fmac-flips-tc.txt").write_text("8c01134c\n", encoding="utf-8")
        st = {"steps": {}}
        with mock.patch.object(wave.subprocess, "run", return_value=mock.Mock(returncode=2, stdout="", stderr="x")):
            wave.requeue(st)
        self.assertNotIn("requeue_tc", st["steps"])

    def test_opt_in(self):
        src = pathlib.Path(wave.__file__).read_text(encoding="utf-8")
        self.assertIn("    if a.requeue:\n        requeue(st)", src)


class Guard(unittest.TestCase):
    def setUp(self):
        d = pathlib.Path(tempfile.mkdtemp(prefix="wave", dir=TMP))
        self.ps = [mock.patch.object(wave, "STATE_DIR", d), mock.patch.object(wave, "STATE", d / "state.json"),
                   mock.patch.object(wave, "LOG", d / "wave.log"), mock.patch.object(wave, "STOP_FILE", d / "STOP"),
                   mock.patch.object(wave, "GUARD_POLL_S", 0.05)]
        for p in self.ps:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in self.ps])
        self.d = d

    def child(self):  # a child with a grandchild, both sleeping: the kill must take the whole tree
        return [sys.executable, "-c", "import subprocess, sys, time; print('up', flush=True); "
                "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); time.sleep(60)"]

    def test_ram_kill(self):
        killed = []
        real = wave.kill_tree
        with mock.patch.object(wave, "ram", side_effect=[50.0] * 10 + [90.0] * 100), \
                mock.patch.object(wave, "kill_tree", side_effect=lambda pid: killed.append(pid) or real(pid)):
            rc, out = wave.guarded(self.child(), 120, {}, cwd=self.d)
        self.assertEqual(rc, "guard")
        self.assertEqual(len(killed), 1)
        self.assertIn("RAM 90.0%", (self.d / "wave.log").read_text(encoding="utf-8"))

    def test_ok_and_pass(self):  # the pass key reaches the child's environment
        cmd = [sys.executable, "-c", "import os; print(os.environ.get('DP_BROKER_PASS'))"]
        with mock.patch.object(wave, "ram", return_value=50.0):
            self.assertEqual(wave.guarded(cmd, 60, {"pass": "k1"}, cwd=self.d), (0, "k1"))

    def test_time_limit(self):
        with mock.patch.object(wave, "ram", return_value=50.0):
            rc, _ = wave.guarded(self.child(), 0.5, {}, cwd=self.d)
        self.assertEqual(rc, 124)

    def test_stop(self):  # STOP: the child tree goes, the state is saved, exit 3 (the HOLD stays)
        (self.d / "STOP").write_text("", encoding="utf-8")
        with mock.patch.object(wave, "ram", return_value=50.0), self.assertRaises(SystemExit) as e:
            wave.guarded(self.child(), 60, {"steps": {"hold": 1}}, cwd=self.d)
        self.assertEqual(e.exception.code, 3)
        self.assertEqual(json.loads((self.d / "state.json").read_text(encoding="utf-8"))["steps"], {"hold": 1})

    def test_resume_readings(self):  # after a guard kill: 5 readings in a row under 85%, a reading over restarts
        seq = [86.0, 84.0, 84.0, 86.0] + [80.0] * 5
        with mock.patch.object(wave, "ram", side_effect=seq + [80.0]), mock.patch.object(wave.time, "sleep"):
            wave.wait_ram(after_kill=True)
        with mock.patch.object(wave, "ram", side_effect=[87.0, 80.0]), mock.patch.object(wave.time, "sleep"):
            self.assertEqual(wave.wait_ram(after_kill=False), 80.0)


class Brake(unittest.TestCase):  # reprepare_stale --brake (the wave passes 85): one prepare at a time from 85%
    def run_four(self, ram, pct):
        import reprepare_stale
        b = reprepare_stale.Brake(pct, ram=ram, poll=0.01)
        lock, now, most = threading.Lock(), [0], [0]

        def prepare():
            with b.held():
                with lock:
                    now[0] += 1
                    most[0] = max(most[0], now[0])
                time.sleep(0.1)
                with lock:
                    now[0] -= 1

        ts = [threading.Thread(target=prepare) for _ in range(4)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(10)
        self.assertFalse(any(t.is_alive() for t in ts))  # never stalls: the last prepare always starts
        return most[0], b

    def test_under(self):
        self.assertEqual(self.run_four(lambda: 80.0, 85.0)[0], 4)

    def test_over(self):
        most, b = self.run_four(lambda: 86.0, 85.0)
        self.assertEqual(most, 1)
        self.assertGreater(b.waited, 0)

    def test_off_and_unknown(self):  # no --brake: no limit; an unknown reading (None) counts as 0%
        self.assertEqual(self.run_four(lambda: 99.0, None)[0], 4)
        self.assertEqual(self.run_four(lambda: None, 85.0)[0], 4)

    def test_wave_passes_it(self):
        src = (pathlib.Path(wave.__file__)).read_text(encoding="utf-8")
        self.assertIn('"--brake", f"{RESUME_UNDER:g}"', src)
        self.assertLess(wave.RESUME_UNDER, wave.KILL_AT)


if __name__ == "__main__":
    unittest.main(verbosity=1)
