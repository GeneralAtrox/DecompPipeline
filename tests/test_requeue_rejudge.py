"""A requeued function's own C is judged again before any agent run (core/job.investigate).

orch.requeue (and tools/requeue.py offline) moves matched/ADDR.c to matched_stale/ADDR.c. The job then:
1. judges that C first, as attempt 0 (model "previous", no cost): a PASS is a match with no agent run, through the
   same finish (proof gates included) as any match, and the judged file is that C, unchanged;
2. on a FAIL, starts attempt 1 from it: work.c is that C and the agent's previous-attempt text names it;
3. an import (import/ADDR.c, e.g. requeue --seed, which may pick matched_stale/ first or not) still wins over it;
4. with neither, attempt 1 starts from scratch, as before;
5. the audit's failure reasons come from the agent's attempts, not attempt 0 (as for an import).
The judge itself (check.run_check, its verdict, the proof) is unchanged: here it is faked.
Run: python -W error tests/test_requeue_rejudge.py (also from test_tooling.py).
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

from core import audit, check, ghidra, job, paths, prepare_timing, proof  # noqa: E402

A = "8c010000"
OLD_C = "int f(int a) { return a + 1; }\n"
SEED_C = "int f(int a) { return 1 + a; }\n"
NEW_C = "int f(int a) { return a + 2; }\n"


def result(verdict):
    failing = 0 if verdict == "PASS" else 3
    return {"verdict": verdict, "stage": "run", "score": failing, "lint": [], "build": [], "first_diffs": [],
            "vectors": {"dev": {"failing": failing, "total": 48}, "hidden": {"failing": failing, "total": 48}},
            "coverage": None}


class FakeO:
    def __init__(self):
        self.cfg = {"max_attempts": 3, "stall_limit": 3, "min_valid_vectors": 8, "model": "haiku"}
        self.functions = {A: {"tier": 1, "score": 10, "insns": 12}}
        self.system_of, self.ledger, self.lines = {}, {}, []
        self.usage = mock.Mock(blocked_until=lambda: None)

    def rec(self, addr):
        return self.ledger.setdefault(addr, {})

    def save(self):
        pass

    def log(self, line):
        self.lines.append(line)

    def check_budget(self):
        pass


class FakeW:
    id, stop_reason = 1, "stopped"

    def __init__(self):
        self.stop_flag = threading.Event()

    def set_phase(self, *a):
        pass


class Rejudge(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=TMP)
        root = self.root = pathlib.Path(self.tmp.name)
        self.patches = [mock.patch.object(paths, k, root / d) for k, d in (
            ("ROOT", "."), ("IMPORTS", "import"), ("RUNS", "runs"), ("MATCHED", "matched"), ("WORK", "work"),
            ("BUILD", "build"), ("STATE", "state"), ("HARNESS", "harness"))]
        summary = {"dev_valid": 48, "hidden_valid": 48, "blocks_hit_all": 4, "blocks_total": 4}
        self.verdicts, self.judged, self.agent_calls, self.proved = [], [], [], []

        def run_check(cfg, addr, work_file, sets, build_dir):
            self.judged.append(pathlib.Path(work_file).read_text(encoding="utf-8"))
            return result(self.verdicts.pop(0))

        def attempt(o, w, addr, n, meta, summary, prev_work, previous_text):
            self.agent_calls.append((n, prev_work, previous_text))
            run_dir = paths.RUNS / addr / f"attempt-{n:02d}"
            run_dir.mkdir(parents=True, exist_ok=True)
            work = run_dir / "work.c"
            work.write_text(NEW_C, encoding="utf-8")
            att = {"n": n, "model": "haiku", "duration_s": 1.0, "cost_usd": 0.01, "work_file": str(work)}
            return job.judge(o, w, addr, n, att, work, run_dir)

        self.patches += [
            mock.patch.object(ghidra, "slot", lambda cfg, stop_flag=None, prio=None: contextlib.nullcontext(1)),
            mock.patch.object(ghidra, "prepare", lambda cfg, addr, **k: ({"signature": "int f(int)"}, summary)),
            mock.patch.object(ghidra, "queue_mark", lambda *a: None),
            mock.patch.object(paths, "sha256_file", lambda p: "0"),
            mock.patch.object(prepare_timing, "brief", lambda t: "1 s"),
            mock.patch.object(check, "run_check", run_check),
            mock.patch.object(check, "format_for_agent", lambda res: "3 vectors differ"),
            mock.patch.object(job, "attempt", attempt),
            mock.patch.object(job, "prove", lambda o, addr, r: self.proved.append(addr) or {"state": proof.VERIFIED}),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def put(self, sub, text):
        (self.root / sub).mkdir(exist_ok=True)
        (self.root / sub / f"{A}.c").write_text(text, encoding="utf-8")

    def run_job(self, *verdicts):
        self.verdicts = list(verdicts)
        self.o = FakeO()
        status = job.investigate(self.o, FakeW(), A)
        self.assertEqual(self.verdicts, [], "every faked verdict used")
        return status, self.o.ledger[A]

    def test_previous_c_passes(self):
        self.put("matched_stale", OLD_C)
        status, r = self.run_job("PASS")
        self.assertEqual(status, "matched")
        self.assertEqual(self.judged, [OLD_C])
        self.assertEqual(self.agent_calls, [])  # no agent run
        self.assertEqual([(a["n"], a["model"], a["cost_usd"], a["verdict"]) for a in r["attempts"]],
                         [(0, "previous", 0.0, "PASS")])
        self.assertTrue(r["verdict_detail"].startswith("PASS: its C from before the requeue passes the new harness"))
        self.assertEqual((self.root / "matched" / f"{A}.c").read_text(encoding="utf-8"), OLD_C)
        self.assertEqual(self.proved, [A])  # the same proof gates as any match
        self.assertEqual(r["cost_usd"], 0)
        self.assertTrue(any("checking its C from before the requeue" in x for x in self.o.lines))

    def test_previous_c_fails_and_seeds_attempt_1(self):
        self.put("matched_stale", OLD_C)
        status, r = self.run_job("FAIL", "PASS")
        self.assertEqual(status, "matched")
        self.assertEqual(self.judged, [OLD_C, NEW_C])
        (n, prev_work, text), = self.agent_calls
        self.assertEqual(n, 1)
        self.assertEqual(pathlib.Path(prev_work).read_text(encoding="utf-8"), OLD_C)
        self.assertIn("own C that passed before it was requeued", text)
        self.assertIn("3 vectors differ", text)
        self.assertEqual([a["model"] for a in r["attempts"]], ["previous", "haiku"])
        self.assertEqual(r["verdict_detail"], "PASS on attempt 1")
        self.assertEqual((self.root / "matched" / f"{A}.c").read_text(encoding="utf-8"), NEW_C)

    def test_import_wins(self):
        self.put("matched_stale", OLD_C)
        self.put("import", SEED_C)
        status, r = self.run_job("PASS")
        self.assertEqual(self.judged, [SEED_C])
        self.assertEqual(r["attempts"][0]["model"], "import")
        self.assertTrue(r["verdict_detail"].startswith("PASS: imported from an earlier project"))

    def test_neither_starts_from_scratch(self):
        status, r = self.run_job("PASS")
        self.assertEqual(status, "matched")
        self.assertEqual(self.agent_calls, [(1, None, "")])
        self.assertEqual([a["model"] for a in r["attempts"]], ["haiku"])


class AuditAttempt0(unittest.TestCase):
    def test_last_attempt_skips_attempt_0(self):
        for model in ("import", "previous"):
            rec = {"attempts": [{"model": model, "stage": "compare"}, {"model": "haiku", "stage": "lint"}]}
            self.assertEqual(audit.last_attempt(rec)["stage"], "lint")
            self.assertEqual(audit.last_attempt({"attempts": rec["attempts"][:1]})["stage"], "compare")


if __name__ == "__main__":
    unittest.main()
