"""tools/prepare_list.py resumed after a bounded run does only the leftovers (Too Human seed2, 2026-11-13: 189 functions in
20-minute runs, each restart redoing work).
- A prepare refused before any vector (meta.json only) or killed at the kill timeout is a current refusal, keyed on
  today's oracle stamp and the kill timeout: not prepared again while both are unchanged, eligible when either moves.
  A stop or any other error is not recorded.
- Callees first: a caller waits for the callees its last meta.json names (its callee records take their harness
  signatures when it is prepared); one whose new meta.json names a callee still to come is checked again after it
  and re-prepared only when it drifted.
- Each result is logged when it ends, not held behind a slower one earlier in the list.
Synthetic workspace; Ghidra is never run. Run: python -W error tests/test_prepare_list_resume.py.
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
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools"))
from test_sync import TMP  # noqa: E402  (sets DECOMP_PROJECT when run alone)

from core import oracle_fresh, paths  # noqa: E402
import prepare_list  # noqa: E402

CFG = {"ghidra_timeout_s": 900}
SUMMARY = {"dev_valid": 48, "hidden_valid": 48, "blocks_total": 4, "blocks_hit_all": 4}
T, U, P, S, E, C = "82000100", "82000200", "82000300", "82000400", "82000500", "82000600"


class Stamper:
    stamp = "s1"

    def __init__(self, cfg):
        pass

    def today(self, addr):
        return Stamper.stamp


class Workspace:
    """paths.HARNESS / PACKETS / STATE in a fresh folder, and the fake oracle stamp, for the block."""

    def __enter__(self):
        self.ws = pathlib.Path(tempfile.mkdtemp(prefix="plresume", dir=TMP))
        self.old = paths.HARNESS, paths.PACKETS, paths.STATE
        paths.HARNESS, paths.PACKETS, paths.STATE = self.ws / "harness", self.ws / "packets", self.ws / "state"
        self.patch = mock.patch.object(oracle_fresh, "Stamper", Stamper)
        self.patch.start()
        Stamper.stamp = "s1"
        return self

    def __exit__(self, *exc):
        self.patch.stop()
        paths.HARNESS, paths.PACKETS, paths.STATE = self.old

    def meta(self, addr, callees):
        d = paths.HARNESS / addr
        d.mkdir(parents=True, exist_ok=True)
        (d / "meta.json").write_text(json.dumps({"signature": "void f(void)", "callees": [
            {"addr": c, "signature": "void g(void)"} for c in callees]}), encoding="utf-8")


def meta_of(callees=()):
    return {"signature": "void f(void)", "unsupported": None, "callees": [{"addr": c} for c in callees]}


class Refused(unittest.TestCase):
    def run_list(self, cfg=CFG):
        made = []

        def prepare(a):
            made.append(a)
            if a == T:
                raise RuntimeError("Ghidra timeout (see state/ghidra_prepare_slot1.log)")
            if a == U:
                return {"signature": "void f(void)", "unsupported": "vmx: vperm128"}, None
            if a == S:
                raise RuntimeError("Ghidra stopped (see state/ghidra_prepare_slot1.log)")
            if a == E:
                raise RuntimeError("TcPrepare produced no meta.json")
            return meta_of(), SUMMARY

        current = lambda a: (meta_of(), SUMMARY) if a == P else None  # noqa: E731
        rows = prepare_list.run(cfg, [T, U, P, S, E], prepare, current, 2, log=lambda m: None)
        return sorted(made), {r["addr"]: r["state"] for r in rows}

    def test_not_prepared_again_while_inputs_are_unchanged(self):
        with Workspace():
            made, _ = self.run_list()
            self.assertEqual(made, sorted([T, U, S, E]))
            made, states = self.run_list()  # the resume: only the stop and the error are tried again
            self.assertEqual(made, sorted([S, E]))
            self.assertEqual(states[T], "refused")
            self.assertEqual(states[U], "refused")
            self.assertEqual(states[P], "current")
            self.assertEqual(states[E], "error")

    def test_eligible_again_when_inputs_change(self):
        with Workspace():
            self.run_list()
            Stamper.stamp = "s2"  # scripts, facts, hints, catalog ...: another oracle stamp
            self.assertEqual(self.run_list()[0], sorted([T, U, S, E]))
            self.assertEqual(self.run_list()[0], sorted([S, E]))
            self.assertEqual(self.run_list(dict(CFG, ghidra_kill_s=1800))[0], sorted([T, U, S, E]))  # another timeout


class Order(unittest.TestCase):
    def test_callees_before_callers(self):
        """C calls E (C's last meta.json says so): E's prepare ends before C's starts, with two slots free."""
        events, lock = [], threading.Lock()

        def prepare(a):
            with lock:
                events.append(("start", a))
            time.sleep(0.3 if a == E else 0.01)
            with lock:
                events.append(("end", a))
            return meta_of([E] if a == C else []), SUMMARY

        with Workspace() as w:
            w.meta(C, [E])
            prepare_list.run(CFG, [C, E], prepare, lambda a: None, 2, log=lambda m: None)
        self.assertLess(events.index(("end", E)), events.index(("start", C)), events)

    def recheck(self, drifted):
        """C has no meta.json yet; its prepare names E, still to come: C is checked again once E is prepared."""
        made, checks = [], []

        def prepare(a):
            made.append(a)
            time.sleep(0.3 if a == E else 0.01)
            return meta_of([E] if a == C else []), SUMMARY

        def reusable(a):
            checks.append(a)
            return (meta_of([E]), SUMMARY) if a == C and checks.count(C) > 1 and not drifted else None

        with Workspace():
            rows = prepare_list.run(CFG, [C, E], prepare, reusable, 2, log=lambda m: None)
        self.assertEqual([r["state"] for r in rows], ["prepared", "prepared"])
        return made

    def test_late_callee_drifted(self):
        made = self.recheck(drifted=True)
        self.assertEqual(sorted(made), sorted([C, C, E]))
        self.assertEqual(made[-1], C)  # after E

    def test_late_callee_still_current(self):
        self.assertEqual(sorted(self.recheck(drifted=False)), sorted([C, E]))


class AsFinished(unittest.TestCase):
    def test_logged_when_it_ends(self):
        """S is slow and first in the list; F's line is out before S ends (ex.map held it behind S)."""
        F = P
        seen, lines = threading.Event(), []

        def log(m):
            lines.append(m)
            if F in m:
                seen.set()

        def prepare(a):
            if a == S:
                seen.wait(3)
            return meta_of(), SUMMARY

        with Workspace():
            prepare_list.run(CFG, [S, F], prepare, lambda a: None, 2, log=log)
        order = [x for m in lines for x in (S, F) if f"] {x}:" in m]
        self.assertEqual(order, [F, S])
        self.assertTrue(seen.is_set())


if __name__ == "__main__":
    unittest.main()
