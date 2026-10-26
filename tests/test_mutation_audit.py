"""The standing mutation audit (tools/mutation_audit.py; pre-wave item 3): no judge change may lower the kill rate.

1. core/mutation.check_function records each mutant's outcome in id order (K S L B, - past the time cap);
2. the gate: a mutant the baseline killed that a run did not is a lost kill and fails it; flips (the matched C no
   longer passes), stale functions (the matched C changed) and gained kills are reported, not failed; a function the
   run lacks fails it; dropped mutants are not compared;
3. `baseline` only moves up (refuses a lost kill) and keeps keys the run did not cover;
4. which changes need the audit: the judge, a runtime, target.json, a locked script; not docs or unlocked scripts.
Synthetic data (no build). Run: python -W error tests/test_mutation_audit.py (also from test_tooling.py).
"""
import contextlib
import io
import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tools")]
import mutation_audit as ma  # noqa: E402

from core import mutation  # noqa: E402

SRC = """void f(int *p, int a) {
    if (a < 3) p[0] = a + 1;
    p[1] = a << 2;
    return;
}
"""


class Outcomes(unittest.TestCase):
    def test_record(self):
        muts = mutation.generate(SRC, "8c000000", 6, 1)
        self.assertEqual(len(muts), 6)
        plan = {0: "PASS", 1: "FAIL", 2: "lint", 3: "build", 4: "PASS", 5: "FAIL"}
        calls = []

        def judge(text, tag):
            if tag == "base":
                return {"verdict": "PASS", "stage": "compare"}
            k = plan[len(calls)]
            calls.append(tag)
            return {"verdict": "FAIL", "stage": k} if k in ("lint", "build") else {"verdict": k, "stage": "compare"}
        mset = {"seed": 1, "max_mutants": 6, "time_cap_s": 100}
        rec = mutation.check_function("8c000000", SRC, judge, mset)
        self.assertEqual(rec["outcomes"], "SKLBSK")
        t = iter([0, 0, 0, 0, 200, 200, 200, 200])
        calls.clear()
        capped = mutation.check_function("8c000000", SRC, judge, mset, clock=lambda: next(t))
        self.assertEqual(capped["outcomes"], "SKL---")
        self.assertTrue(capped["time_capped"])


def f(sha, out):
    return {"sha": sha, "outcomes": out}


class Gate(unittest.TestCase):
    BASE = {"tc": {"a": f("1", "KKSL"), "b": f("2", "KS"), "c": f("3", "KK"), "d": f("4", "K"), "e": {"error": "x"}},
            "sat": {"s": f("9", "SSSS")}}

    def test_compare(self):
        run = {"tc": {"a": f("1", "KSKL"), "b": f("2", "KK"), "c": {"error": "baseline FAIL"}, "d": f("5", "S"),
                      "e": f("6", "K")}, "sat": {"s": f("9", "SKSK")}}
        res = ma.compare(self.BASE, run)
        self.assertFalse(res["ok"])
        self.assertEqual(res["lost"], [("tc", "a", 2)])
        tc = res["keys"]["tc"]
        self.assertEqual((tc["mutants"], tc["killed_base"], tc["killed_run"], tc["gained"], tc["lost"]),
                         (5, 3, 4, 2, 1))  # a: 3 compared (L dropped), b: 2
        self.assertEqual((tc["flips"], tc["stale"], tc["missing"]), (["c"], ["d"], []))
        self.assertEqual((res["keys"]["sat"]["rate_base"], res["keys"]["sat"]["rate_run"]), (0.0, 50.0))
        self.assertIn("gate: FAIL (1 lost kills: tc a m02)", ma.report(res))

    def test_pass_and_missing(self):
        run = {"tc": {"a": f("1", "KKKL"), "b": f("2", "KK"), "c": f("3", "KK"), "d": f("4", "K")},
               "sat": {"s": f("9", "SSSS")}}
        self.assertTrue(ma.compare(self.BASE, run)["ok"])
        del run["sat"]
        res = ma.compare(self.BASE, run)
        self.assertFalse(res["ok"])
        self.assertEqual(res["keys"]["sat"]["missing"], ["s"])

    def test_baseline_up_only(self):
        with tempfile.TemporaryDirectory() as d:
            here = pathlib.Path(d)
            (here / "baseline.json").write_text(json.dumps(self.BASE))
            worse = here / "worse.json"
            worse.write_text(json.dumps({"tc": {"a": f("1", "SSSL")}}))
            better = here / "better.json"
            better.write_text(json.dumps({"tc": {"a": f("1", "KKKL"), "b": f("2", "KK"), "c": f("3", "KK"),
                                                 "d": f("4", "K")}, "sat": {"s": f("9", "KSSS")}}))
            with mock.patch.object(ma, "HERE", here), contextlib.redirect_stdout(io.StringIO()):
                with mock.patch.object(sys, "argv", ["x", "baseline", str(worse)]):
                    self.assertEqual(ma.main(), 1)
                with mock.patch.object(sys, "argv", ["x", "baseline", str(better)]):
                    self.assertEqual(ma.main(), 0)
            new = json.loads((here / "baseline.json").read_text())
            self.assertEqual(new["sat"]["s"]["outcomes"], "KSSS")
            self.assertEqual(new["tc"]["e"], {"error": "x"})  # kept: the run did not cover it


class Needed(unittest.TestCase):
    def test_paths(self):
        locked = {"targets/dreamcast_sh4/ghidra_scripts/TcOracle.java"}
        files = ["core/compare.py", "core/check.py", "targets/dreamcast_sh4/runtime/tc_rt.c",
                 "targets/dreamcast_sh4/runtime_rd/rd_veh.c", "targets/dreamcast_sh4/ghidra_scripts/TcOracle.java",
                 "targets/saturn_sh2/target.json", "targets/gamecube_ppc/ppc_compare.py", "docs/PROOF_GATES.md",
                 "targets/dreamcast_sh4/ghidra_scripts/TcSeed.java", "core/watcher.py", "tests/test_x.py"]
        self.assertEqual(ma.at_stake(files, locked), files[:7])

    def test_locked_lists(self):  # every target's locked oracle scripts exist (the audit's list of them)
        for t in (ROOT / "targets").glob("*/target.json"):
            cfg = json.loads(t.read_text(encoding="utf-8"))
            dirs = [t.parent / "ghidra_scripts"] + [ROOT / "core" / "ghidra_shared" / d
                                                    for d in cfg.get("shared_scripts") or []]
            for s in cfg.get("locked_scripts") or []:
                self.assertTrue(any((d / s).exists() for d in dirs), f"{t.parent.name}/{s}")


if __name__ == "__main__":
    unittest.main()
