"""Harness reuse: a worker skips Ghidra when the function's harness is current (core/job.reusable_harness), and
tools/prepare_list.py prepares a list ahead of the workers, leaving current harnesses alone.

Synthetic workspace in a temp folder; Ghidra is never run (slot and prepare are fakes that fail the test if used).
Reuse is opt-in: config "harness_reuse" (default off; on in the gamecube_ppc target.json). Off, every function is
prepared, as before, and reusable_harness is never called. With it on, and master's requeue re-judge and prefetch
(test_requeue_rejudge's and test_prefetch's own fixtures):
- a requeued function with a current harness opens no Ghidra and its old C is still judged first (attempt 0);
- a prefetched function with a current harness takes no slot and no prepare on the prefetch thread, and investigate
  does not decide again: each function is prepared at most once, wherever the decision falls.
Run: python -W error tests/test_harness_reuse.py (also from test_tooling.py's discovery).
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
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools"))
from test_sync import TMP  # noqa: E402  (sets DECOMP_PROJECT when run alone)

from core import ghidra, job, oracle_fresh, paths  # noqa: E402
import prepare_list  # noqa: E402
import test_prefetch  # noqa: E402  (as modules: their own TestCases are not collected twice here)
import test_requeue_rejudge  # noqa: E402

A = "80001000"
PLATE = b"/* TC decomp: MATCHED\n   PASS on attempt 1; 1 attempt(s), model haiku, 2026-10-20T08:40:58 */\n\n"
BODY = b"void FUN_80001000(void)\n\n{\n  return;\n}\n"
SUMMARY = {"dev_valid": 48, "hidden_valid": 48, "blocks_total": 4, "blocks_hit_all": 4, "blocks_hit_dev": 4}


class FakeStamper:
    stamp = "s-today"

    def __init__(self, cfg):
        pass

    def today(self, addr):
        return self.stamp


def workspace(stamp="s-today", packet=True, files=("vectors.txt", "expected.txt", "meta.json", "oracle_summary.json")):
    ws = pathlib.Path(tempfile.mkdtemp(prefix="reuse", dir=TMP))
    h = ws / "harness" / A
    h.mkdir(parents=True)
    for n in files:
        data = {"meta.json": json.dumps({"signature": "u32 fn_80001000(u32 a)", "unsupported": None,
                                         "oracle_stamp": stamp, "callees": []}),
                "oracle_summary.json": json.dumps(SUMMARY)}.get(n, "x\n")
        (h / n).write_text(data, encoding="utf-8")
    if packet:
        (ws / "packets" / A).mkdir(parents=True)
    return ws


def tree(d):
    """{relative path: (bytes, mtime_ns)} of every file under d."""
    return {p.relative_to(d).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns) for p in d.rglob("*") if p.is_file()}


class Paths:
    """Point paths.HARNESS / PACKETS at ws for the block."""

    def __init__(self, ws):
        self.ws = ws

    def __enter__(self):
        self.old = paths.HARNESS, paths.PACKETS, paths.IMPORTS
        paths.HARNESS, paths.PACKETS, paths.IMPORTS = self.ws / "harness", self.ws / "packets", self.ws / "imports"

    def __exit__(self, *exc):
        paths.HARNESS, paths.PACKETS, paths.IMPORTS = self.old


class Reusable(unittest.TestCase):
    def check(self, ws, take=False):
        with Paths(ws), mock.patch.object(oracle_fresh, "Stamper", FakeStamper):
            return job.reusable_harness({}, A, take=take)

    def test_current(self):
        meta, summary = self.check(workspace())
        self.assertEqual(meta["signature"], "u32 fn_80001000(u32 a)")
        self.assertEqual(summary, SUMMARY)

    def test_not_current(self):
        self.assertIsNone(self.check(workspace(stamp="s-old")))  # other oracle inputs
        self.assertIsNone(self.check(workspace(packet=False)))  # the sandbox needs the packet
        self.assertIsNone(self.check(workspace(files=("vectors.txt", "expected.txt", "meta.json"))))
        self.assertIsNone(self.check(workspace(stamp=None)))

    def test_callee_drift(self):
        ws = workspace()
        meta = json.loads((ws / "harness" / A / "meta.json").read_text(encoding="utf-8"))
        meta["callees"] = [{"addr": "80002000", "signature": "void fn_80002000(void)"}]
        (ws / "harness" / A / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
        (ws / "harness" / "80002000").mkdir()
        (ws / "harness" / "80002000" / "meta.json").write_text(
            json.dumps({"signature": "u32 fn_80002000(u32 a)"}), encoding="utf-8")
        self.assertIsNone(self.check(ws))

    def test_status_plate(self):  # an older packet's status plate goes, as a prepare now would leave it out
        ws = workspace()
        f = ws / "packets" / A / "ghidra.c"
        f.write_bytes(b"\n" + PLATE + BODY)
        self.assertIsNotNone(self.check(ws, take=True))
        self.assertEqual(f.read_bytes(), b"\n" + BODY)

    def test_check_writes_nothing(self):  # only a worker taking the harness strips; a check (prepare_list) reads
        ws = workspace()
        (ws / "packets" / A / "ghidra.c").write_bytes(b"\n" + PLATE + BODY)
        before = tree(ws)
        self.assertIsNotNone(self.check(ws))
        self.assertEqual(tree(ws), before)
        with Paths(ws), mock.patch.object(oracle_fresh, "Stamper", FakeStamper):
            self.assertIsNone(job.reuse_harness({}, A))  # reuse off: not even looked at
            self.assertIsNotNone(job.reuse_harness({"harness_reuse": True}, A))  # a worker takes it
        self.assertEqual((ws / "packets" / A / "ghidra.c").read_bytes(), b"\n" + BODY)

    def test_status_plate_split(self):  # split with the plate in: the parts would differ from a fresh split
        ws = workspace()
        part = ws / "packets" / A / "ghidra_1.c"
        part.write_bytes(b"// part 1 of 2 of ghidra.c\n\n" + PLATE + BODY)
        self.assertIsNone(self.check(ws))
        self.assertEqual(part.read_bytes(), b"// part 1 of 2 of ghidra.c\n\n" + PLATE + BODY)


class StatusPlate(unittest.TestCase):
    def strip(self, data, name="ghidra.c"):
        d = pathlib.Path(tempfile.mkdtemp(prefix="plate", dir=TMP))
        (d / name).write_bytes(data)
        ok = ghidra.strip_status(d)
        return ok, (d / name).read_bytes()

    def test_strip(self):
        self.assertEqual(self.strip(b"\n" + PLATE + BODY), (True, b"\n" + BODY))
        crlf = (b"\n" + PLATE + BODY).replace(b"\n", b"\r\n")
        self.assertEqual(self.strip(crlf), (True, b"\r\n" + BODY.replace(b"\n", b"\r\n")))
        self.assertEqual(self.strip(BODY), (True, BODY))  # none: unchanged
        twice = b"\n" + PLATE + BODY + PLATE  # only the function's own plate, the first
        self.assertEqual(self.strip(twice), (True, b"\n" + BODY + PLATE))
        wrapped = (b"\r\n/* TC decomp: FAILED\r\n   attempt limit 1 reached (best: 5 failing vectors); 1 attempt(s), "
                   b"model haiku, 2026-10-18T11:24:23\r\n    */\r\n\r\n")  # Ghidra wraps a long plate (GameCube)
        self.assertEqual(self.strip(wrapped + BODY), (True, b"\r\n" + BODY))
        inline = BODY.replace(b"{\n", b"{\n  x = 1; /* TC decomp: MATCHED */\n")  # not at a line start: kept
        self.assertEqual(self.strip(inline), (True, inline))

    def test_answer_only(self):  # write=False: the same answer, the file untouched
        d = pathlib.Path(tempfile.mkdtemp(prefix="plate", dir=TMP))
        (d / "ghidra.c").write_bytes(b"\n" + PLATE + BODY)
        self.assertTrue(ghidra.strip_status(d, write=False))
        self.assertEqual((d / "ghidra.c").read_bytes(), b"\n" + PLATE + BODY)
        (d / "ghidra_1.c").write_bytes(b"// part 1 of 2 of ghidra.c\n\n" + PLATE + BODY)
        self.assertFalse(ghidra.strip_status(d, write=False))

    def test_prepare_strips_before_split(self):  # ghidra.prepare: strip_status, then split_packet
        src = pathlib.Path(ghidra.__file__).read_text(encoding="utf-8")
        self.assertLess(src.index("    strip_status(packet)"), src.index("    split_packet(packet)"))


class Worker:
    def __init__(self):
        self.id, self.stop_flag, self.stop_reason, self.phases = 1, threading.Event(), None, []

    def set_phase(self, addr, text, n=None):
        self.phases.append(text)


class Orch:
    def __init__(self):
        self.cfg = {"max_attempts": 1, "stall_limit": 2, "min_valid_vectors": 40, "model": "haiku",
                    "ghidra_parallel": 6, "harness_reuse": True}
        self.functions = {A: {"tier": "easy", "score": 1, "insns": 10}}
        self.system_of, self.ledger, self.logs = {}, {"functions": {}}, []
        self.usage = mock.Mock(blocked_until=lambda: None)

    def rec(self, addr):
        return self.ledger["functions"].setdefault(addr, {"addr": addr, "status": "queued", "attempts": []})

    def save(self):
        pass

    def log(self, m):
        self.logs.append(m)

    def check_budget(self):
        pass


def no_ghidra(*a, **k):
    raise AssertionError("Ghidra was opened for a current harness")


class Investigate(unittest.TestCase):
    def run_one(self, reused, reuse_on=True):
        o, w = Orch(), Worker()
        o.cfg["harness_reuse"] = reuse_on
        seen, self.asked = {}, []

        def attempt(o_, w_, addr, n, meta, summary, prev, text):
            seen["meta"], seen["summary"] = meta, summary
            return {"verdict": "PASS", "work_file": "-", "score": 0, "stage": "compare"}

        def reusable(cfg, addr, take=False):
            self.asked.append(addr)
            self.took = take  # a worker takes the harness it reuses (job.reuse_harness: an old plate goes)
            return reused

        with Paths(workspace()), mock.patch.object(job, "reusable_harness", reusable), \
                mock.patch.object(job.ghidra, "slot", no_ghidra), mock.patch.object(job.ghidra, "prepare", no_ghidra), \
                mock.patch.object(job, "attempt", attempt), \
                mock.patch.object(job, "finish", lambda o_, addr, status, detail: (status, detail)):
            status = job.investigate(o, w, A)
        return status, seen, o, w

    def test_reused_skips_ghidra(self):
        meta = {"signature": "u32 fn_80001000(u32 a)", "unsupported": None}
        status, seen, o, w = self.run_one((meta, SUMMARY))
        self.assertEqual(status[0], "matched")
        self.assertEqual((seen["meta"], seen["summary"]), (meta, SUMMARY))
        self.assertTrue(any("reused without Ghidra" in m for m in o.logs))
        self.assertTrue(self.took)
        self.assertFalse(any(p.startswith("Ghidra slot") for p in w.phases))  # no slot held (the wait phase is master's)
        self.assertEqual(set(o.ledger["functions"][A]["harness_sha256"]), set(job.HARNESS_FILES))

    def test_key_off_prepares_a_current_harness(self):  # master's path: the Ghidra fake is called
        meta = {"signature": "u32 fn_80001000(u32 a)", "unsupported": None}
        status, seen, o, w = self.run_one((meta, SUMMARY), reuse_on=False)
        self.assertEqual(status, ("error", "Ghidra was opened for a current harness"))
        self.assertEqual(self.asked, [])  # reusable_harness never called
        self.assertFalse(any("reused without Ghidra" in m for m in o.logs))
        for off in ({}, {"harness_reuse": False}, {"harness_reuse": None}):
            self.assertIsNone(job.reuse_harness(off, A))

    def test_shipped_defaults(self):
        root = pathlib.Path(__file__).resolve().parents[1]
        self.assertIs(json.loads((root / "config.json").read_text(encoding="utf-8"))["harness_reuse"], False)
        for t in sorted((root / "targets").glob("*/target.json")):
            on = json.loads(t.read_text(encoding="utf-8")).get("harness_reuse")
            self.assertEqual(bool(on), t.parent.name == "gamecube_ppc", t.parent.name)

    def test_not_current_prepares(self):
        status, seen, o, w = self.run_one(None)  # falls through to the slot (the fake refuses)
        self.assertEqual(status, ("error", "Ghidra was opened for a current harness"))
        self.assertNotIn("meta", seen)


class RejudgeReused(test_requeue_rejudge.Rejudge):
    """test_requeue_rejudge's four cases, each with a current harness: the same verdicts, and no Ghidra."""

    def setUp(self):
        super().setUp()

        class FakeO(test_requeue_rejudge.FakeO):
            def __init__(self):
                super().__init__()
                self.cfg["harness_reuse"] = True

        p = mock.patch.object(test_requeue_rejudge, "FakeO", FakeO)
        p.start()
        self.patches.append(p)
        current = ({"signature": "int f(int)"}, {"dev_valid": 48, "hidden_valid": 48, "blocks_hit_all": 4,
                                                 "blocks_total": 4})
        for p in (mock.patch.object(job.ghidra, "slot", no_ghidra), mock.patch.object(job.ghidra, "prepare", no_ghidra),
                  mock.patch.object(job, "reusable_harness", lambda cfg, addr, take=False: current)):
            p.start()
            self.patches.append(p)

    def run_job(self, *verdicts):
        status, r = super().run_job(*verdicts)
        self.assertTrue(r["harness_reused"])
        self.assertTrue(any("reused without Ghidra" in x for x in self.o.lines))
        return status, r


class PrefetchReused(test_prefetch.Prefetch):
    """test_prefetch's fixture (a real Worker, claim and investigate) with self.current naming the functions whose
    harness is current."""
    test_on = test_off = test_failed_prefetch = test_stop_gives_the_next_back = test_loop_off = None  # run there

    def setUp(self):
        super().setUp()
        self.current = set()
        self.asked = []
        p = mock.patch.object(job, "reusable_harness", lambda cfg, addr, take=False: self.asked.append(addr) or (
            {"signature": f"int f_{addr}(int a)"}, dict(test_prefetch.SUMMARY)) if addr in self.current else None)
        p.start()
        self.patches.append(p)

    def orchestrator(self, prefetch=True, loop=True, ledger=None, reuse=True):
        o = super().orchestrator(prefetch, loop, ledger)
        o.cfg["harness_reuse"] = reuse
        return o

    def test_key_off_is_master(self):  # a current harness is prepared anyway, prefetched or not, as on master
        A, B, C = test_prefetch.A, test_prefetch.B, test_prefetch.C
        self.current = {A, B, C}
        for prefetch, where in ((True, "prefetch-W1"), (False, "worker-1")):
            self.events.clear()
            o = self.orchestrator(prefetch=prefetch, reuse=False)
            self.assertEqual(self.work(o), {A: "matched", B: "matched", C: "matched"})
            self.assertEqual(self.prepares(), [(A, "worker-1"), (B, where), (C, where)])
            self.assertEqual(self.asked, [])
            self.assertFalse(any(o.ledger["functions"][a]["harness_reused"] for a in (A, B, C)))

    def test_prefetched_current(self):
        self.current = {test_prefetch.B}
        o = self.orchestrator()
        A, B, C = test_prefetch.A, test_prefetch.B, test_prefetch.C
        self.assertEqual(self.work(o), {A: "matched", B: "matched", C: "matched"})
        self.assertEqual(self.prepares(), [(A, "worker-1"), (C, "prefetch-W1")])  # B: no prepare anywhere
        rb = o.ledger["functions"][B]
        self.assertTrue(rb["harness_reused"] and "prefetch_wait_s" in rb)
        self.assertIsNone(rb["prepare_timing"])
        self.assertTrue(any(f"{B}: prefetch found its harness current" in x for x in o.lines))
        self.assertFalse(o.ledger["functions"][C]["harness_reused"])

    def test_off_current(self):
        self.current = {test_prefetch.B}
        o = self.orchestrator(prefetch=False)
        A, B, C = test_prefetch.A, test_prefetch.B, test_prefetch.C
        self.assertEqual(self.work(o), {A: "matched", B: "matched", C: "matched"})
        self.assertEqual(self.prepares(), [(A, "worker-1"), (C, "worker-1")])
        self.assertTrue(o.ledger["functions"][B]["harness_reused"])

    def requeued_b(self, current):
        """B was requeued (its C in matched_stale/) and is prefetched: its old C is judged first, as attempt 0, and
        passes with no agent run; B is prepared once (prefetch thread) or not at all (current harness)."""
        A, B, C = test_prefetch.A, test_prefetch.B, test_prefetch.C
        self.current = current
        (paths.ROOT / "matched_stale").mkdir()
        (paths.ROOT / "matched_stale" / f"{B}.c").write_text("int f(int a) { return a; }\n", encoding="utf-8")
        o = self.orchestrator(ledger={B: {"addr": B, "status": "queued", "attempts": [], "requeued": "test"}})
        self.assertEqual(self.work(o), {A: "matched", B: "matched", C: "matched"})
        rb = o.ledger["functions"][B]
        self.assertEqual([a["model"] for a in rb["attempts"]], ["previous"])
        self.assertNotIn(("agent", B), [e[:2] for e in self.events])
        return [p for p in self.prepares() if p[0] == B], rb

    def test_requeue_current(self):
        prepared, rb = self.requeued_b({test_prefetch.B})
        self.assertEqual(prepared, [])
        self.assertTrue(rb["harness_reused"])

    def test_requeue_not_current(self):
        prepared, rb = self.requeued_b(set())
        self.assertEqual(prepared, [(test_prefetch.B, "prefetch-W1")])  # once
        self.assertFalse(rb["harness_reused"])


class PrepareList(unittest.TestCase):
    def test_run(self):
        made = []

        def prepare(a):
            made.append(a)
            if a == "80003000":
                raise RuntimeError("TcPrepare produced no meta.json\nmore")
            return {"signature": "void fn(void)", "unsupported": None}, {"dev_valid": 48, "hidden_valid": 47,
                                                                       "blocks_total": 8, "blocks_hit_all": 6}

        reusable = lambda a: ({"signature": "s"}, SUMMARY) if a == "80001000" else None  # noqa: E731
        rows = prepare_list.run({}, ["80001000", "80002000", "80003000"], prepare, reusable, 2, log=lambda m: None)
        self.assertEqual(sorted(made), ["80002000", "80003000"])  # the current one is left alone
        self.assertEqual([r["state"] for r in rows], ["current", "prepared", "error"])
        self.assertEqual(rows[1]["block_pct"], 75.0)
        self.assertEqual(rows[2]["detail"], "RuntimeError: TcPrepare produced no meta.json")
        out = pathlib.Path(tempfile.mkdtemp(prefix="plist", dir=TMP)) / "o.tsv"
        prepare_list.write_tsv(out, rows)
        lines = out.read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines[0].split("\t"), list(prepare_list.COLUMNS))
        self.assertEqual(len(lines), 4)


if __name__ == "__main__":
    unittest.main()
