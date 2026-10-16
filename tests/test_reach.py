"""Call-site coverage and the reach pass (core/call_coverage.py, tools/call_coverage.py, TcReach.java).

The judge compares a callee's arguments only where a vector reaches the call; TC's port crashed on matched C at calls
no vector reached. One pass and one fail control each:
1. the figure: reached, explained (the reach pass's reason) and untried sites (a harness made before the pass),
   functions without data apart; the ranked report and the re-prepare list;
2. the final audit's calls check FAILS while a matched function has an untried unreached site and passes once every
   unreached site has the pass's reason (the broken control: an old harness);
3. the judge's verdict carries the reasons and the untried count; the ledger note names them; facts carry the switch
   (on by target.json, off with runs 0);
4. the wiring: TcPrepare runs the pass before writing the vectors and reports it, TcOracle's watch hook, the added
   vectors follow the first pass's (ids after them), the new scripts are locked, files stay under 400 lines; the
   function's constant data is not an input;
5. requeue --seed --stale seeds from matched_stale/ first.
The pass's search itself needs Ghidra: the regress gate judges it (Toy Commander's regress/fixtures.json, tc-reach-*).
Synthetic data. Run: python -W error tests/test_reach.py (also from test_tooling.py).
"""
import json
import pathlib
import re
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools"))  # requeue
from test_sync import TMP  # noqa: E402,F401  (sets DECOMP_PROJECT when run alone)

from core import call_coverage, check, final_audit, ghidra, job, paths  # noqa: E402

SCRIPTS = pathlib.Path(__file__).resolve().parents[1] / "targets/dreamcast_sh4/ghidra_scripts"


def harness(root, rows):
    """rows: {addr: summary dict} written as harness/ADDR/oracle_summary.json under root."""
    for a, s in rows.items():
        d = pathlib.Path(root) / a
        d.mkdir(parents=True, exist_ok=True)
        (d / "oracle_summary.json").write_text(json.dumps(s), encoding="utf-8")
    return pathlib.Path(root)


OLD = {"call_sites": {"8c000010": 5, "8c000020": 0, "8c000030": 0}}  # before the reach pass
NEW = {"call_sites": {"8c000110": 3, "8c000120": 1, "8c000130": 0},
       "reach": {"runs": 40, "added": [1, 1], "found": ["8c000120"],
                 "unreached": {"8c000130": "no path from the entry reaches it in the control flow"}}}
FULL = {"call_sites": {"8c000210": 9}, "reach": {"runs": 0, "added": [0, 0], "found": [], "unreached": {}}}


class Figure(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=TMP)
        self.h = harness(self.tmp.name, {"8c000000": OLD, "8c000100": NEW, "8c000200": FULL,
                                         "8c000300": {"dev_valid": 0}})

    def tearDown(self):
        self.tmp.cleanup()

    def test_rows(self):
        rows = call_coverage.site_rows(["8c000000", "8c000100", "8c000200", "8c000300", "8c000400"], self.h)
        self.assertEqual(sorted(rows), ["8c000000", "8c000100", "8c000200"])  # no call-site data: left out
        self.assertEqual(rows["8c000000"]["unreached"], ["8c000020", "8c000030"])
        self.assertFalse(rows["8c000000"]["tried"])
        self.assertEqual(rows["8c000100"]["reasons"], {"8c000130": "no path from the entry reaches it in the control flow"})
        self.assertEqual(rows["8c000100"]["found"], ["8c000120"])
        self.assertEqual(call_coverage.untried(rows), ["8c000000"])  # explained and fully reached ones are not

    def test_figure(self):
        rows = call_coverage.site_rows(["8c000000", "8c000100", "8c000200"], self.h)
        f = call_coverage.figure(rows, nodata=2)
        self.assertEqual((f["sites"], f["reached"], f["unreached"], f["explained"], f["untried"]), (7, 4, 3, 1, 2))
        self.assertEqual((f["fn_unreached"], f["fn_untried"], f["found"], f["percent"]), (2, 1, 1, 57.14))
        line = call_coverage.summary_line(f)
        self.assertIn("call sites reached 4/7 (57.14%)", line)
        self.assertIn("2 function(s) without call-site data", line)
        self.assertEqual(call_coverage.figure({})["percent"], 100.0)

    def test_report_ranked(self):
        lines = []
        old, paths.HARNESS = paths.HARNESS, self.h
        try:
            call_coverage.report(["8c000100", "8c000000", "8c000200"], log=lines.append)
        finally:
            paths.HARNESS = old
        self.assertTrue(lines[0].startswith("call sites reached"))
        self.assertTrue(lines[1].startswith("  8c000000  2 of 3 unreached (untried)"), lines)  # most unreached first
        self.assertIn("untried: the harness predates the reach pass", lines[2])
        self.assertTrue(any("8c000100  1 of 3 unreached (explained)" in x for x in lines), lines)


class AuditCheck(unittest.TestCase):
    def run_check(self, rows):
        with tempfile.TemporaryDirectory(dir=TMP) as d:
            old, oldm = paths.HARNESS, call_coverage.matched_functions
            paths.HARNESS, call_coverage.matched_functions = harness(d, rows), lambda: sorted(rows)
            try:
                return final_audit.check_calls(final_audit.Out(lambda m: None))
            finally:
                paths.HARNESS, call_coverage.matched_functions = old, oldm

    def test_old_harness_fails(self):  # the broken control: an unreached site nobody tried to reach
        ok, text = self.run_check({"8c000000": OLD, "8c000100": NEW})
        self.assertFalse(ok)
        self.assertIn("2 untried", text)

    def test_explained_passes(self):
        ok, text = self.run_check({"8c000100": NEW, "8c000200": FULL})
        self.assertTrue(ok, text)
        self.assertIn("1 explained by the reach pass, 0 untried", text)


class Verdict(unittest.TestCase):
    def finish(self, summary):
        res = {"stage": "compare", "failing": 0, "vectors": {"dev": {"total": 48}, "hidden": {"total": 48}},
               "first_diffs": [], "lint": [], "build": []}
        return check._finish(res, ["dev", "hidden"], summary)["coverage"]

    def test_reasons(self):
        cov = self.finish(NEW)
        self.assertEqual(cov["calls_untried"], 0)
        self.assertEqual(cov["calls_reasons"], {"8c000130": "no path from the entry reaches it in the control flow"})
        note = job.weak_note({"coverage": cov})
        self.assertIn("1 of 3 call sites never reached: 8c000130", note)
        self.assertNotIn("re-prepare", note)

    def test_untried(self):
        cov = self.finish(OLD)
        self.assertEqual(cov["calls_untried"], 2)
        self.assertTrue(all(v.startswith("untried") for v in cov["calls_reasons"].values()))
        self.assertIn("2 untried by the reach pass: re-prepare", job.weak_note({"coverage": cov}))
        self.assertEqual(job.weak_note({"coverage": self.finish(FULL)}), "")

    def test_facts(self):
        cfg = dict(paths.config(), reach_calls={"runs": 800, "seconds": 150})
        self.assertIn("reach_calls 800 150\n", ghidra.facts_body(cfg))
        self.assertNotIn("reach_calls", ghidra.facts_body(dict(cfg, reach_calls={"runs": 0})))
        target = json.loads((SCRIPTS.parent / "target.json").read_text(encoding="utf-8"))
        self.assertGreater(target["reach_calls"]["runs"], 0)  # on by default for every project of the target


class Wiring(unittest.TestCase):
    def text(self, name):
        return (SCRIPTS / name).read_text(encoding="utf-8")

    def test_prepare(self):
        p = self.text("TcPrepare.java")
        self.assertLess(p.index("TcReach.run(this, proto, seed, seeds)"), p.index('harness.resolve("vectors.txt")'))
        self.assertIn('(reach != null ? reach + "," : "")', p)
        self.assertIn('case "reach_calls":', self.text("TcFacts.java"))

    def test_hooks(self):
        o, v, r = self.text("TcOracle.java"), self.text("TcVectors.java"), self.text("TcReach.java")
        self.assertIn("if (pc == watchPc && res.watched == null) TcReach.snap(res, calls, emu);", o)
        self.assertIn("long watchPc = -1;", o)
        self.assertIn("if (facts.reachRuns > 0) seeds.add(TcReach.entry(this, v, r));", v)
        self.assertIn("s.seeds = t.seeds;", self.text("TcRescue.java"))  # a rescue pass that loses keeps its seeds
        self.assertIn("int id = g.valid[0] + g.valid[1];", r)  # added vectors follow the first pass's
        self.assertIn("if (g.facts.reachRuns <= 0) return null;", r)  # off: no "reach" key, the sites stay untried
        self.assertNotRegex(r, r"\bHashSet<Integer>\s+covered|new HashMap<Long, Entry>")  # ordered: deterministic
        self.assertIn("if (h.nextBoolean() || !direct(v, loads, rets, ops))", r)  # compare operands, half the runs
        self.assertIn('m.startsWith("cmp") || m.startsWith("tst") || m.equals("dt")', self.text("TcCfg.java"))
        self.assertIn("reached only by invalid runs", r)  # a site behind a path that never returns
        # the function's own constant data (pool words, a braf table) is never mutated as an input (TC 8c0144da)
        self.assertIn("if (g.facts.isCode(a) || flow.isConst(a) || !seen.add(a)) continue;", r)
        self.assertIn('equals("mova") || g.poolValue(in) != null', self.text("TcCfg.java"))

    def test_locked(self):
        locked = json.loads((SCRIPTS.parent / "target.json").read_text(encoding="utf-8"))["locked_scripts"]
        self.assertIn("TcReach.java", locked)
        self.assertIn("TcRepair.java", locked)
        self.assertIn("TcCfg.java", locked)
        for name in ("TcReach.java", "TcRepair.java", "TcCfg.java", "TcVectors.java", "TcOracle.java", "TcPrepare.java"):
            self.assertLessEqual(len(self.text(name).splitlines()), 400, name)

    def test_repair_moved_unchanged(self):
        r, v = self.text("TcRepair.java"), self.text("TcVectors.java")
        self.assertIn("TcRepair.entry(this, v, r.faultAddr)", v)
        self.assertIn("TcRepair.ret(this, v, r.faultAddr, r.calls.size(), consts, h)", v)
        self.assertNotIn("boolean repairEntry(", v)
        self.assertTrue(re.search(r"long\[\]\[\] regs = \{g\.entryLow, g\.entryGi\};", r))


class Seed(unittest.TestCase):
    """requeue --seed starts from matched/ (else matched_stale/); --stale the other way round, to get back the C a
    faulty harness forced out."""
    def test_order(self):
        import requeue
        with tempfile.TemporaryDirectory() as d:
            ws = pathlib.Path(d)
            for sub, text in (("matched", "new"), ("matched_stale", "old")):
                (ws / sub).mkdir()
                (ws / sub / "8c000400.c").write_text(text, encoding="utf-8")
            (ws / "matched_stale" / "8c000500.c").write_text("only stale", encoding="utf-8")
            self.assertEqual(requeue.seed(ws, ["8c000400", "8c000600"], "n"), ["8c000400"])  # 600 has no C
            self.assertEqual((ws / "import/8c000400.c").read_text(encoding="utf-8"), "new")
            self.assertEqual(requeue.seed(ws, ["8c000400", "8c000500"], "n", stale_first=True), ["8c000400", "8c000500"])
            self.assertEqual((ws / "import/8c000400.c").read_text(encoding="utf-8"), "old")
            self.assertEqual((ws / "import/8c000500.note").read_text(encoding="utf-8"), "n\n")


if __name__ == "__main__":
    unittest.main()
