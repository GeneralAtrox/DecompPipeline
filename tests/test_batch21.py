"""Batch 21: one worker per function. Target lists each function once (core/targets.py, tools/common.expand_addrs),
the per-function claim (core/filelock.claim, core/fn_claim.py) and its stale records (filelock.stale_claims, the
doctor), infrastructure errors that are not verdicts (core/infra.py: retried once, then infra_error), and verify
leaving a busy function alone. Two concurrent `refine.py run` processes: tests/test_batch21_runs.py. Synthetic data
in temp folders; no Ghidra, compiler or agent. Run: python -W error tests/test_batch21.py (also from test_tooling.py).
"""
import contextlib
import io
import pathlib
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_sync import TMP, TOOL  # noqa: E402  (sets DECOMP_PROJECT when run alone)
from test_retire import make_ws as retire_ws  # noqa: E402

from core import doctor, filelock, fn_claim, infra, paths, readable, readable_verify, refine, sync_run  # noqa: E402
from core import targets  # noqa: E402
import common  # noqa: E402  (tools/common.py)

A, B, C = "8c010000", "8c010100", "8c010200"
ERRNO = r"[Errno 2] No such file or directory: '..\P\decomp\sandbox\rd_8c0412b4\work.c'"
LNK = (r"a do-nothing stub stops at build: LINK : fatal error LNK1181: cannot open input file "
       r"'..\P\decomp\build\8c046b2a\rd_preflight\protos.obj'")
HOLD = """
import os, sys, time
sys.path.insert(0, sys.argv[1])
from core import filelock
with filelock.claim(sys.argv[2], 0, {"purpose": "run", "command": "refine.py run @prod4.txt"}):
    print("held", flush=True)
    if sys.argv[3] == "die":
        os._exit(0)  # killed: no cleanup, the owner record stays
    time.sleep(float(sys.argv[3]))
"""


def hold(lock, mode):
    return subprocess.Popen([sys.executable, "-c", HOLD, str(TOOL), str(lock), mode], stdout=subprocess.PIPE,
                            text=True)


class Targets(unittest.TestCase):
    def test_crlf_bom_duplicates(self):
        lst = pathlib.Path(tempfile.mkdtemp(dir=TMP)) / "list.txt"
        lst.write_bytes(b"\xef\xbb\xbf8C010000\r\n0x8c010100 # b\r\n8c010000\r\n" + b"s01, 8c010200\n8c010100\n")
        logs = []
        out = targets.addresses([f"@{lst}", "8c010200"], {"S01": [A, C]}, log=logs.append)
        self.assertEqual(out, [A, B, C])
        self.assertIn("5 duplicate(s) dropped", logs[0])
        self.assertIn("3 unique", logs[0])
        with self.assertRaises(ValueError):
            targets.addresses(["Sx9"], {})
        self.assertEqual(targets.addresses([A, B], {}, log=logs.append), [A, B])
        self.assertEqual(len(logs), 1)  # no file, no duplicate: nothing to say

    def test_tools_common(self):
        lst = pathlib.Path(tempfile.mkdtemp(dir=TMP)) / "l.txt"
        lst.write_bytes(b"8c010000\r\n8C010000\r\n8c010100\n")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(common.expand_addrs([f"@{lst}"]), [A, B])
        self.assertIn("1 duplicate(s) dropped", err.getvalue())


class Claims(unittest.TestCase):
    def lock(self, name):
        return pathlib.Path(tempfile.mkdtemp(dir=TMP)) / f"{name}.lock"

    def test_threads_are_rivals_and_nesting(self):
        lock, held, done = self.lock(A), threading.Event(), threading.Event()

        def owner():
            with filelock.claim(lock, 0, {"purpose": "run"}):
                held.set()
                done.wait(10)

        t = threading.Thread(target=owner)
        t.start()
        try:
            self.assertTrue(held.wait(10))
            with self.assertRaises(filelock.Busy) as cm:
                with filelock.claim(lock, 0.2):
                    pass
            self.assertIn("another thread of this process", str(cm.exception))
        finally:
            done.set()
            t.join(10)
        with filelock.claim(lock, 1, {"purpose": "verify"}):
            with filelock.claim(lock, 0):  # the owning thread may nest
                self.assertEqual(filelock.owner_of(lock)["purpose"], "verify")
        self.assertFalse(lock.with_suffix(".owner").exists())

    def test_process_rival_then_dead_owner(self):
        lock = self.lock(B)
        p = hold(lock, "20")
        try:
            self.assertEqual(p.stdout.readline().strip(), "held")
            t0 = time.monotonic()
            with self.assertRaises(filelock.Busy) as cm:
                with filelock.claim(lock, 0.3):
                    pass
            self.assertLess(time.monotonic() - t0, 3)
            self.assertIn(f"pid {p.pid} (run)", str(cm.exception))
            self.assertIn("refine.py run @prod4.txt", str(cm.exception))
            stale, held = filelock.stale_claims(lock.parent)
            self.assertEqual((stale, list(held)), ({}, [B]))  # held: reported, never cleared
        finally:
            p.kill()
            p.wait(30)
            p.stdout.close()
        stale, held = filelock.stale_claims(lock.parent)  # killed: its record stays, its OS lock is gone
        self.assertEqual((list(stale), held), ([B], {}))
        self.assertEqual((stale[B]["pid"], stale[B]["alive"]), (p.pid, False))
        self.assertFalse(lock.with_suffix(".owner").exists())
        with filelock.claim(lock, 5):
            pass

    def test_doctor_clears_stale(self):
        ws = retire_ws("claims")
        p = hold(ws / "state" / "fnlocks" / f"{A}.lock", "die")
        self.assertEqual(p.stdout.readline().strip(), "held")
        p.wait(30)
        p.stdout.close()
        _, lines, facts = doctor.check(ws)
        self.assertEqual(facts["stale_claims_cleared"], 1, lines)
        self.assertTrue(any("stale function claim(s) cleared" in x and A in x for x in lines), lines)
        self.assertFalse((ws / "state" / "fnlocks" / f"{A}.owner").exists())
        self.assertEqual(doctor.check(ws)[2]["stale_claims_cleared"], 0)


class Infra(unittest.TestCase):
    def test_patterns(self):
        for t in (ERRNO, LNK, "[WinError 183] Cannot create a file when that file already exists",
                  r"C1083: Cannot open source file: '..\P\build\8c010000\readable\work.c'"):
            self.assertTrue(infra.is_infra(t), t)
        for t in ("registry: unknown type Foo", "a do-nothing stub stops at build: work.c(4): error C2065",
                  r"LNK1181: cannot open input file 'libfoo.lib'", None, ""):
            self.assertFalse(infra.is_infra(t), t)
        with self.assertRaises(infra.InfraError):
            infra.check(["ok", LNK], None)

    def test_status_rows(self):
        led = {A: {"status": "error", "note": ERRNO}, B: {"status": "blocked", "note": LNK},
               C: {"status": "blocked", "note": "registry: refused"}, "8c010300": {"status": "failed", "note": ERRNO},
               "8c010400": {"status": "readable", "redo_failed": {"outcome": "infra_error", "note": "x"}},
               "8c010500": {"status": "infra_error", "note": "y"}}
        self.assertEqual(sorted(infra.rows(led)), [A, B, "8c010400", "8c010500"])


def pass_check(*a, **k):
    return {"verdict": "PASS", "stage": "compare", "failing": 0, "total": 96}


class Cycle(unittest.TestCase):
    """fn_claim.cycle / run_one around the real refine.refine (agent, sandbox and judge faked as in RedoStatus)."""

    def setUp(self):
        self.d = pathlib.Path(tempfile.mkdtemp(prefix="cyc", dir=TMP))
        for sub in ("src", "box", "locks"):
            (self.d / sub).mkdir()
        (self.d / "box" / "work.c").write_text("/* attempt */", encoding="utf-8")
        self.calls = []
        self.patches = [mock.patch.object(refine, "LEDGER", self.d / "readable.json"),
                        mock.patch.object(refine.readable, "SRC", self.d / "src"),
                        mock.patch.object(refine.readable, "Registry", lambda: None),
                        mock.patch.object(paths, "RUNS", self.d / "runs"),
                        mock.patch.object(fn_claim, "LOCKS", self.d / "locks"),
                        mock.patch.object(refine, "raw_missing", lambda a: None),
                        mock.patch.object(refine, "harness_stale", lambda cfg, a: None),
                        mock.patch.object(refine, "preflight", lambda cfg, reg, a, meta: None),
                        mock.patch.object(refine, "build_sandbox", lambda *a: (self.d / "box", "task")),
                        mock.patch.object(refine, "tree_hashes", lambda box: {}),
                        mock.patch.object(refine.agent, "run_attempt", self.attempt),
                        mock.patch.object(refine.rcheck, "check_readable", pass_check)]
        for p in self.patches:
            p.start()
        self.cfg = {"model": "m", "effort": None, "readable_effort": None, "function_lock_wait_s": 0.2}
        self.fail_first = 1

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()

    def attempt(self, *a):
        self.calls.append(1)
        if len(self.calls) <= self.fail_first:  # the rival worker removed the sandbox mid-attempt
            raise FileNotFoundError(2, "No such file or directory", str(self.d / "box" / "work.c"))
        return {}

    def test_os_error_retried_then_pass(self):
        logs = []
        self.assertEqual(fn_claim.cycle(self.cfg, A, 3, logs.append), "readable")
        self.assertEqual(len(self.calls), 2)
        self.assertTrue(any("infrastructure error (FileNotFoundError)" in m and "retrying once" in m for m in logs))
        self.assertEqual(refine.load_ledger()[A]["status"], "readable")

    def test_twice_is_infra_error_not_error(self):
        self.fail_first = 9
        out = fn_claim.cycle(self.cfg, A, 3, lambda m: None)
        row = refine.load_ledger()[A]
        self.assertEqual(row["status"], "infra_error", out)
        self.assertIn("[Errno 2]", row["note"])
        (self.d / "src" / f"{B}.c").write_text("/* old pass */", encoding="utf-8")
        refine.update(B, status="readable")
        fn_claim.cycle(self.cfg, B, 3, lambda m: None)
        row = refine.load_ledger()[B]
        self.assertEqual((row["status"], row["redo_failed"]["outcome"]), ("readable", "infra_error"))

    def test_lnk1181_in_the_judge_is_retried(self):
        self.fail_first, seen = 0, []

        def judge(*a, **k):
            seen.append(1)
            if len(seen) == 1:
                return {"verdict": "FAIL", "stage": "build", "build": [LNK], "lint": []}
            return pass_check()

        with mock.patch.object(refine.rcheck, "check_readable", judge):
            self.assertEqual(fn_claim.cycle(self.cfg, A, 3, lambda m: None), "readable")
        atts = refine.load_ledger()[A]["attempts"]
        self.assertEqual([x["verdict"] for x in atts], ["FAIL", "PASS"])  # the first is recorded as it ended

    def test_preflight_lnk1181_is_not_blocked(self):
        stub = {"verdict": "FAIL", "stage": "build", "build": [LNK], "lint": []}
        with mock.patch.object(refine, "preflight", self.real_preflight), \
                mock.patch.object(refine.rcheck, "check_readable", lambda *a, **k: stub):
            out = fn_claim.cycle(self.cfg, A, 3, lambda m: None)
        self.assertEqual(refine.load_ledger()[A]["status"], "infra_error", out)
        self.assertIn("LNK1181", refine.load_ledger()[A]["note"])
        self.assertEqual(self.calls, [])  # no agent was paid
        stub["build"] = ["work.c(4): error C2065: 'x': undeclared identifier"]  # a real finding stays blocked
        with mock.patch.object(refine, "preflight", self.real_preflight), \
                mock.patch.object(refine.rcheck, "check_readable", lambda *a, **k: stub):
            self.assertEqual(fn_claim.cycle(self.cfg, B, 3, lambda m: None), "blocked")

    def real_preflight(self, cfg, reg, addr, meta):
        """The real refine.preflight, its registry, header and sandbox helpers faked."""
        with contextlib.ExitStack() as st:
            for name, fake in (("stale_callee_records", lambda m: []), ("stub_c", lambda r, p: "x"),
                               ("sandbox_decls", lambda r, a, m: ({a: "void f(void)"}, []))):
                st.enter_context(mock.patch.object(refine, name, fake))
            st.enter_context(mock.patch.object(refine.readable_lean, "headers_for",
                                               lambda *a: {"globals": [], "functions": []}))
            st.enter_context(mock.patch.object(refine.global_extents, "for_process", lambda *a: {}))
            st.enter_context(mock.patch.object(refine.readable, "game_h", lambda *a, **k: ""))
            st.enter_context(mock.patch.object(paths, "BUILD", self.d / "build"))
            st.enter_context(mock.patch.object(paths, "MATCHED", self.d / "matched"))
            (self.d / "matched").mkdir(exist_ok=True)
            (self.d / "matched" / f"{addr}.c").write_text("/* raw */", encoding="utf-8")
            return PREFLIGHT(cfg, type("R", (), {"proto": lambda s, a, m: "void f(void)"})(), addr, meta)

    def test_busy_leaves_the_row(self):
        refine.update(A, status="blocked", note="kept as it was")
        held, done = threading.Event(), threading.Event()

        def owner():
            with fn_claim.claim(A, "verify", 0):
                held.set()
                done.wait(10)

        t = threading.Thread(target=owner)
        t.start()
        try:
            self.assertTrue(held.wait(10))
            out = fn_claim.run_one(self.cfg, A, 3, lambda m: None)
        finally:
            done.set()
            t.join(10)
        self.assertTrue(out.startswith("busy (not run)"), out)
        self.assertEqual(refine.load_ledger()[A], {"status": "blocked", "note": "kept as it was"})
        self.assertEqual(self.calls, [])


PREFLIGHT = refine.preflight  # the real one, before Cycle's patches


class VerifyBusy(unittest.TestCase):
    def setUp(self):
        self.d = pathlib.Path(tempfile.mkdtemp(prefix="vb", dir=TMP))
        (self.d / "src").mkdir()
        for a in (A, B, C):
            (self.d / "src" / f"{a}.c").write_text(f"/* {a} */", encoding="utf-8")
        self.patches = [mock.patch.object(refine, "LEDGER", self.d / "readable.json"),
                        mock.patch.object(readable, "SRC", self.d / "src"),
                        mock.patch.object(readable, "Registry", lambda: None),
                        mock.patch.object(fn_claim, "LOCKS", self.d / "locks"),
                        mock.patch.object(fn_claim, "wait_s", lambda cfg: 0.2),
                        mock.patch.object(refine, "raw_missing", lambda a: None),
                        mock.patch.object(sync_run, "raw_report", lambda reg, files: ({}, (0, 0, 0))),
                        mock.patch.object(readable_verify.rcheck, "check_readable", self.judge)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()

    def judge(self, cfg, addr, src, bdir, **k):
        if addr == C:  # a run replaces the file while verify judges the old one
            (self.d / "src" / f"{C}.c").write_text("/* rewritten */", encoding="utf-8")
        return pass_check()

    def test_busy_and_changed_are_not_recorded(self):
        refine.update(A, status="running", redo_of="stale")
        refine.update(C, status="running")
        held, done, logs = threading.Event(), threading.Event(), []

        def owner():
            with fn_claim.claim(A, "run", 0):
                held.set()
                done.wait(20)

        t = threading.Thread(target=owner)
        t.start()
        try:
            self.assertTrue(held.wait(10))
            final, bad = readable_verify.verify({}, [A, B, C], workers=2, log=logs.append)
        finally:
            done.set()
            t.join(10)
        self.assertEqual((final[A]["verdict"], final[B]["verdict"], bad), ("BUSY", "PASS", 0))
        led = refine.load_ledger()
        self.assertEqual((led[A]["status"], led[B]["status"], led[C]["status"]), ("running", "readable", "running"))
        self.assertTrue(any(m.startswith(f"CHANGED {C}") for m in logs), logs)
        self.assertTrue(any("2/3 readable files pass; 1 busy" in m for m in logs), logs)


if __name__ == "__main__":
    unittest.main(verbosity=1)
