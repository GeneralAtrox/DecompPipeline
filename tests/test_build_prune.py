"""Build-cache pruning (core/build_prune): the compiler outputs of build/ADDR once no job holds the function.
1. prune_dir deletes executables, objects, precompiled headers and listings written before the cut-off, and the
   folders left empty; the C, game.h, actual.txt and anything newer stay;
2. a function a job holds is skipped: its claim held by another thread (a refine run, verify), or an orchestrator
   status (running, queued); a claim the caller itself holds is re-entered (after_job inside fn_claim.run_one);
3. sweep: by age first, then oldest first while over the size cap, never below min_age_min; a dry run deletes
   nothing;
4. maybe_sweep runs once per interval (state stamp), and "enabled": false turns everything off;
5. fn_claim.run_one prunes the function it finished, under its claim.
Synthetic folders. Run: python -W error tests/test_build_prune.py (also from test_tooling.py).
"""
import json
import os
import pathlib
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from test_sync import TMP  # noqa: E402,F401  (sets DECOMP_PROJECT when run alone)

from core import build_prune, filelock, fn_claim, paths  # noqa: E402

HOUR = 3600.0
OUTPUTS = ["official/runner.exe", "official/work.obj", "official/work.asm", "rd_agent/rd_runner.exe",
           "rd_agent/pch/rd_pch.pch", "rd_agent/pch/rd_pch.obj", "rd_verify_proof/lean_proof.obj"]
KEPT = ["official/work.c", "official/actual.txt", "rd_agent/pch/rd_pch.h", "rd_agent_inc/game.h"]


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=TMP)
        root = pathlib.Path(self.tmp.name)
        self.build, self.state = root / "build", root / "state"
        self.state.mkdir()
        self.patches = [mock.patch.object(paths, "BUILD", self.build), mock.patch.object(paths, "STATE", self.state),
                        mock.patch.object(paths, "LEDGER", self.state / "ledger.json")]
        for p in self.patches:
            p.start()
        self.now = time.time()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def make(self, addr, age_s, files=OUTPUTS + KEPT, size=1000):
        for rel in files:
            p = self.build / addr / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"x" * size)
            os.utime(p, (self.now - age_s, self.now - age_s))

    def left(self, addr):
        d = self.build / addr
        return sorted(p.relative_to(d).as_posix() for p in d.rglob("*") if p.is_file()) if d.exists() else []

    def ledger(self, rows):
        (self.state / "ledger.json").write_text(json.dumps({"functions": rows}), encoding="utf-8")


class Prune(Case):
    def test_prune_dir(self):
        self.make("8c010000", 2 * HOUR)
        self.make("8c010000", 60, ["readable/rd_runner.exe"])  # a build that is still being written
        freed, n = build_prune.prune_dir(self.build / "8c010000", self.now - HOUR)
        self.assertEqual((freed, n), (7000, 7))
        self.assertEqual(self.left("8c010000"), sorted(KEPT + ["readable/rd_runner.exe"]))
        self.assertFalse((self.build / "8c010000" / "rd_verify_proof").exists())  # left empty: removed
        self.assertTrue((self.build / "8c010000" / "rd_agent" / "pch").exists())  # rd_pch.h stays

    def test_held(self):
        for a in ("8c010000", "8c010100", "8c010200"):
            self.make(a, 2 * HOUR)
        self.ledger({"8c010100": {"status": "running"}, "8c010200": {"status": "matched"}})
        got, release = {}, threading.Event()

        def rival():  # a refine run in another thread holds 8c010000
            with filelock.claim(self.state / "fnlocks" / "8c010000.lock", 0, {"purpose": "run"}):
                got["held"] = True
                release.wait(30)
        t = threading.Thread(target=rival)
        t.start()
        try:
            while "held" not in got:
                time.sleep(0.01)
            freed, n, busy = build_prune.prune_function("8c010000")
            self.assertEqual((freed, n), (0, 0))
            self.assertIn("busy", busy)
        finally:
            release.set()
            t.join()
        self.assertEqual(build_prune.prune_function("8c010100")[2], "orchestrator: running")
        self.assertEqual(len(self.left("8c010100")), len(OUTPUTS + KEPT))
        self.assertEqual(build_prune.prune_function("8c010200")[:2], (7000, 7))  # matched: final
        with filelock.claim(self.state / "fnlocks" / "8c010000.lock", 0, {"purpose": "run"}):  # the caller's own
            self.assertEqual(build_prune.after_job({}, "8c010000"), (7000, 7, None))
        self.assertEqual(self.left("8c010000"), sorted(KEPT))

    def test_sweep(self):
        for a, age in (("8c010000", 48 * HOUR), ("8c010100", 30 * HOUR), ("8c010200", 2 * HOUR),
                       ("8c010300", 600)):
            self.make(a, age)
        cfg = {"build_prune": {"age_h": 24, "cap_gb": 1, "min_age_min": 30}}
        dry = build_prune.sweep(cfg, dry_run=True, now=self.now)
        self.assertEqual((dry["folders"], dry["pruned"], dry["freed"], dry["bytes"]), (4, 2, 14000, 28000))
        self.assertEqual(len(self.left("8c010000")), len(OUTPUTS + KEPT))  # nothing deleted
        rep = build_prune.sweep(cfg, now=self.now)
        self.assertEqual((rep["pruned"], rep["freed"], rep["kept"]), (2, 14000, 14000))
        self.assertEqual(self.left("8c010100"), sorted(KEPT))
        self.assertEqual(len(self.left("8c010200")), len(OUTPUTS + KEPT))  # younger than age_h, under the cap
        cap = {"build_prune": {"age_h": 24, "cap_gb": 0, "min_age_min": 30}}  # over the cap: the oldest of the rest
        self.assertEqual(build_prune.sweep(cap, now=self.now)["pruned"], 1)
        self.assertEqual(self.left("8c010200"), sorted(KEPT))
        self.assertEqual(len(self.left("8c010300")), len(OUTPUTS + KEPT))  # under min_age_min: never

    def test_maybe_sweep(self):
        self.make("8c010000", 48 * HOUR)
        cfg = {"build_prune": {"interval_h": 6}}
        rep = build_prune.maybe_sweep(cfg, background=False)
        self.assertEqual(rep["pruned"], 1)
        stamp = json.loads((self.state / "build_prune.json").read_text(encoding="utf-8"))
        self.assertEqual(stamp["report"]["freed"], 7000)
        self.make("8c010100", 48 * HOUR)
        self.assertIsNone(build_prune.maybe_sweep(cfg, background=False))  # within the interval
        self.assertEqual(len(self.left("8c010100")), len(OUTPUTS + KEPT))
        off = {"build_prune": {"enabled": False, "interval_h": 0}}
        self.assertIsNone(build_prune.maybe_sweep(off, background=False))
        self.assertIsNone(build_prune.after_job(off, "8c010100"))
        self.assertEqual(len(self.left("8c010100")), len(OUTPUTS + KEPT))


class Wiring(Case):
    def test_run_one(self):
        self.make("8c010000", 2 * HOUR)
        seen = {}

        def cycle(cfg, addr, *a, **k):
            return "readable"

        def after(cfg, addr, log=None):
            try:  # another thread cannot take the claim while run_one holds it
                th = threading.Thread(target=lambda: seen.setdefault(
                    "rival", build_prune.prune_function(addr)[2]))
                th.start()
                th.join()
            finally:
                seen["addr"] = addr
            return real(cfg, addr, log)
        real = build_prune.after_job
        with mock.patch.object(fn_claim, "cycle", cycle), mock.patch.object(build_prune, "after_job", after), \
                mock.patch.object(fn_claim, "LOCKS", self.state / "fnlocks"), \
                mock.patch.object(fn_claim, "skip_reason", lambda *a, **k: None):
            self.assertEqual(fn_claim.run_one({}, "8c010000", 1, lambda m: None), "readable")
        self.assertEqual(seen["addr"], "8c010000")
        self.assertIn("busy", seen["rival"])
        self.assertEqual(self.left("8c010000"), sorted(KEPT))


if __name__ == "__main__":
    unittest.main()
