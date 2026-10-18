"""Batch 32: the proof gates (core/proof.py, core/mutation.py, docs/PROOF_GATES.md).

1. Coverage gate: the allowance by size (all but one block from min_blocks up, else min_block_pct), the states
   (verified, matched_low_coverage, no data, waived, report only), the settings (config.json keys, a project's
   partial override), the workspace scan and its figure, the final audit's proof check (FAIL / INFO / PASS), the
   doctor's figure from any workspace, the ledger row at a match (job.prove) and the server's counts.
2. Mutation check: the tokenizer (comments, preprocessor lines, initializer braces), each operator's candidates and
   what they leave alone (unary - and &, case labels, declarations, return 0), deterministic seeded generation with
   every operator represented, the kill / survive / drop logic, the baseline refusal, the time cap and the record
   file. The judge is a fake here; tools/mutation_check.py runs the real one (host build, stored vectors).
Synthetic data. Run: python -W error tests/test_batch32.py (also from test_tooling.py).
"""
import json
import pathlib
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from test_sync import TMP  # noqa: E402,F401  (sets DECOMP_PROJECT when run alone)

from core import check, final_audit, job, mutation, mutation_ops, orch, proof  # noqa: E402

TOOL = pathlib.Path(__file__).resolve().parents[1]
GATE = dict(proof.COVERAGE_DEFAULTS)
REPORT = dict(proof.MUTATION_DEFAULTS)


def summ(total, hit):
    return {"blocks_total": total, "blocks_hit_all": hit, "blocks_hit_dev": hit, "dev_valid": 48, "hidden_valid": 48}


def put(ws, rel, data):
    p = pathlib.Path(ws) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")


def workspace(rows, project=None, mutation_rows=None):
    """A workspace whose matched functions have these harness summaries ({addr: summary or None})."""
    ws = pathlib.Path(tempfile.mkdtemp(prefix="proof", dir=TMP))
    put(ws, "project.json", dict({"name": "pf", "target": "dreamcast_sh4"}, **(project or {})))
    put(ws, "state/ledger.json", {"functions": {a: {"addr": a, "status": "matched"} for a in rows}})
    for a, s in rows.items():
        if s is not None:
            put(ws, f"harness/{a}/oracle_summary.json", s)
    if mutation_rows:
        put(ws, "state/mutation.json", {"functions": mutation_rows})
    return ws


class Allowance(unittest.TestCase):
    def test_by_size(self):
        a = proof.allowed_uncovered
        self.assertEqual(a(1, GATE), 0)
        self.assertEqual(a(2, GATE), 0)  # below min_blocks: every block needed
        self.assertEqual([a(n, GATE) for n in (3, 5, 10, 19)], [1, 1, 1, 1])  # all but one
        self.assertEqual(a(20, GATE), 2)  # 90 %
        self.assertEqual(a(406, GATE), 40)
        self.assertEqual(a(10, dict(GATE, min_block_pct=100, allow_uncovered_blocks=0)), 0)


class CoverageVerdict(unittest.TestCase):
    def test_states(self):
        v = proof.coverage_verdict
        self.assertTrue(v(summ(8, 8), GATE)["ok"])
        self.assertTrue(v(summ(8, 7), GATE)["ok"])  # one unreached block
        low = v(summ(8, 6), GATE)
        self.assertFalse(low["ok"])
        self.assertEqual((low["uncovered"], low["allowed"], low["pct"]), (2, 1, 75.0))
        self.assertFalse(v(summ(2, 1), GATE)["ok"])  # tiny: both blocks needed
        self.assertTrue(v(summ(30, 27), GATE)["ok"])
        self.assertFalse(v(summ(30, 26), GATE)["ok"])
        self.assertIsNone(v(None, GATE)["ok"])
        self.assertIsNone(v({"dev_valid": 48}, GATE)["ok"])

    def test_waived(self):
        g = dict(GATE, waive={"8c000000": "fault path: no input reaches it"})
        r = proof.coverage_verdict(summ(8, 2), g, "8c000000")
        self.assertTrue(r["ok"])
        self.assertIn("fault path", r["waived"])
        self.assertFalse(proof.coverage_verdict(summ(8, 2), g, "8c000100")["ok"])

    def test_state(self):
        low, ok = {"ok": False}, {"ok": True}
        self.assertEqual(proof.state(low, None, REPORT, GATE), proof.LOW)
        self.assertEqual(proof.state(low, None, REPORT, dict(GATE, enforce=False)), proof.LOW)  # report only: still low
        self.assertEqual(proof.state({"ok": None}, None, REPORT, GATE), proof.VERIFIED)  # no data: never low
        weak = {"tried": 10, "killed": 5, "survived": 5, "score": 50.0}
        self.assertEqual(proof.state(ok, weak, REPORT, GATE), proof.VERIFIED)  # report: a flag only
        self.assertEqual(proof.mutation_flag(weak, REPORT), "weak")
        enforce = dict(REPORT, mode="enforce")
        self.assertEqual(proof.state(ok, weak, enforce, GATE), proof.WEAK)
        self.assertEqual(proof.state(ok, dict(weak, score=90.0), enforce, GATE), proof.VERIFIED)
        self.assertEqual(proof.state(low, weak, enforce, GATE), proof.LOW)  # coverage first
        self.assertIsNone(proof.mutation_flag(weak, dict(REPORT, mode="off")))
        self.assertIsNone(proof.mutation_flag({"error": "baseline FAIL", "tried": 0}, REPORT))


class Settings(unittest.TestCase):
    def test_config_keys(self):
        cfg = json.loads((TOOL / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(cfg["coverage_gate"], proof.COVERAGE_DEFAULTS)
        self.assertEqual(cfg["mutation_check"], proof.MUTATION_DEFAULTS)
        self.assertEqual(cfg["mutation_check"]["mode"], "report")  # report-only at first
        self.assertFalse(cfg["mutation_check"]["on_pass"])

    def test_project_override(self):
        s = proof.coverage_settings({"coverage_gate": {"min_block_pct": 80}})
        self.assertEqual(s["min_block_pct"], 80)
        self.assertEqual(s["allow_uncovered_blocks"], 1)  # the other keys stay
        self.assertEqual(proof.mutation_settings({"mutation_check": {"max_mutants": 4}})["mode"], "report")
        with self.assertRaises(ValueError):
            proof.mutation_settings({"mutation_check": {"mode": "strict"}})


class Scan(unittest.TestCase):
    def test_figure(self):
        weak = {"tried": 4, "killed": 3, "survived": 1, "score": 75.0}
        ws = workspace({"8c000000": summ(8, 8), "8c000100": summ(8, 5), "8c000200": None, "8c000300": summ(4, 3)},
                       mutation_rows={"8c000000": weak})
        rows = proof.scan(ws)
        self.assertEqual(rows["8c000100"]["state"], proof.LOW)
        self.assertEqual(rows["8c000000"]["mutation_flag"], "weak")
        f = proof.figure(rows)
        self.assertEqual((f["matched"], f[proof.VERIFIED], f[proof.LOW], f["no_coverage_data"], f["weak_flag"]),
                         (4, 3, 1, 1, 1))
        self.assertIn("1 matched_low_coverage", proof.summary_line(f))
        d = proof.distribution(rows)
        self.assertEqual(d["pct"]["100"], 1)
        self.assertEqual(d["pct"]["50-69"], 1)
        self.assertEqual(d["uncovered"], {"0": 1, "1": 1, "3": 1})

    def test_project_waiver_and_tool(self):
        import subprocess
        ws = workspace({"8c000000": summ(8, 2), "8c000100": summ(9, 3)},
                       project={"coverage_gate": {"waive": {"8c000000": "trap loop"}}})
        rows = proof.scan(ws)
        self.assertEqual(rows["8c000000"]["state"], proof.VERIFIED)
        self.assertEqual(rows["8c000100"]["state"], proof.LOW)
        with tempfile.TemporaryDirectory(dir=TMP) as d:
            lst, js = pathlib.Path(d) / "low.txt", pathlib.Path(d) / "out.json"
            p = subprocess.run([sys.executable, str(TOOL / "tools/coverage_gate.py"), "--project", str(ws), "--report",
                                "--list", str(lst), "--json", str(js)], capture_output=True, text=True, timeout=120)
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertIn("below the gate: 1/2 (50.0%)", p.stdout)
            self.assertEqual(lst.read_text().split(), ["8c000100"])
            self.assertEqual(json.loads(js.read_text())[str(ws.resolve())]["figure"]["waived"], 1)
            p = subprocess.run([sys.executable, str(TOOL / "tools/coverage_gate.py"), "--project", str(ws),
                                "--min-pct", "30"], capture_output=True, text=True, timeout=120)
            self.assertIn("below the gate: 0/2", p.stdout)  # 3/9 = 33 %: a trial setting, nothing written


class AuditProof(unittest.TestCase):
    def run_check(self, rows, cfg):
        ws = workspace(rows)
        old = final_audit.function_sets
        final_audit.function_sets = lambda _ws: (set(rows), set(), set(rows), set(rows))
        try:
            return final_audit.check_proof(cfg, ws, final_audit.Out(lambda m: None))
        finally:
            final_audit.function_sets = old

    def test_low_fails(self):  # the broken control: a matched function whose original is barely run
        ok, text = self.run_check({"8c000000": summ(8, 8), "8c000100": summ(10, 4)}, {})
        self.assertIs(ok, False)
        self.assertIn("1/2 matched functions verified; 1 matched_low_coverage", text)

    def test_report_only_is_info(self):  # an existing game working down its backlog: shown, not failed
        ok, text = self.run_check({"8c000100": summ(10, 4)}, {"coverage_gate": {"enforce": False}})
        self.assertIsNone(ok)
        self.assertIn("0/1 matched functions verified; 1 matched_low_coverage", text)
        self.assertIn("report only", text)
        ok, _ = self.run_check({"8c000100": summ(10, 4)}, {"coverage_gate": {"enforce": False},
                                                          "mutation_check": {"mode": "enforce"}})
        self.assertIs(ok, True)  # the mutation gate enforces, the coverage gate does not: low does not fail

    def test_covered_passes(self):
        ok, text = self.run_check({"8c000000": summ(8, 8), "8c000100": summ(10, 9)}, {})
        self.assertIs(ok, True, text)


class DoctorFigure(unittest.TestCase):
    def test_any_workspace(self):
        import os
        import subprocess
        from test_retire import A, B, make_ws
        ws = make_ws("doctorproof")
        put(ws, f"harness/{A}/oracle_summary.json", summ(12, 4))
        put(ws, f"harness/{B}/oracle_summary.json", summ(12, 12))
        env = {k: v for k, v in os.environ.items() if k != "DECOMP_PROJECT"}
        with tempfile.TemporaryDirectory(dir=TMP) as cwd:
            p = subprocess.run([sys.executable, str(TOOL / "tools/doctor.py"), "--project", str(ws), "--json"],
                               cwd=cwd, env=env, capture_output=True, text=True, timeout=120)
        self.assertNotIn("Traceback", p.stderr)
        f = json.loads(p.stdout)[str(ws.resolve())]["proof"]
        self.assertEqual((f[proof.VERIFIED], f[proof.LOW]), (1, 1))


class LedgerRow(unittest.TestCase):
    class O:
        def __init__(self, cfg):
            self.cfg, self.proof_states, self.logs = cfg, {}, []

        def log(self, m):
            self.logs.append(m)

    def test_prove(self):
        o = self.O({})
        rec = job.prove(o, "8c000100", {"oracle": summ(10, 4)})
        self.assertEqual(rec["state"], proof.LOW)
        self.assertEqual(o.proof_states["8c000100"], proof.LOW)
        self.assertIn("4/10 blocks reached (40.0%), 6 unreached, 1 allowed", proof.describe(rec))
        self.assertEqual(job.prove(o, "8c000000", {"oracle": summ(10, 10)})["state"], proof.VERIFIED)
        self.assertEqual(o.logs, [])  # on_pass off: no mutation check ran

    def test_finish_wiring(self):
        text = (TOOL / "core/job.py").read_text(encoding="utf-8")
        self.assertIn('r["proof"] = prove(o, addr, r)', text)
        self.assertIn('r.pop("proof", None)', text)

    def test_server_counts(self):
        ws = workspace({"8c000000": summ(8, 8), "8c000100": summ(8, 3)})
        fake = self.O({})
        old = orch.paths.ROOT
        orch.paths.ROOT = ws
        try:
            c = orch.Orchestrator.proof_counts(fake, ["8c000000", "8c000100"])
        finally:
            orch.paths.ROOT = old
        self.assertEqual(c, {"verified": 1, "matched_low_coverage": 1, "weak_harness": 0})
        ui = (TOOL / "ui/index.html").read_text(encoding="utf-8")
        self.assertIn("o.matched_low_coverage", ui)
        self.assertIn('run("proof", fa.check_proof', (TOOL / "tools/final_audit.py").read_text(encoding="utf-8"))


SRC = """#include "tc_rt.h"
#define LIMIT 8 /* not a body */

static const u32 table[4] = {1, 2, 3, 4};

u32 fn_8c000000(u32 p, u32 q)
{
    TcArgs a = {{0}, {0}, {0}};
    u32 r4 = 0;
    u32 n;
    // comment: a < b + 1
    if (p < q) {
        r4 = p - q;
    }
    n = -1;
    wr32(0x8c0c0000u, p & 0xffu);
    a.r[0] = p;
    a.r[1] = q << 2;
    tc_call(0x8c010000u, &a);
    switch (q) {
    case 3:
        r4 += table[2];
        break;
    }
    while (n != 0)
        n = n >> 1;
    if (r4 == 0) return 0;
    return r4 + n;
}
"""


class Tokens(unittest.TestCase):
    def test_skips(self):
        toks = mutation.tokens(SRC)
        texts = [t.text for t in toks]
        self.assertNotIn("LIMIT", texts)  # preprocessor line
        self.assertNotIn("comment", texts)
        init = [t.text for t in toks if t.init]
        self.assertEqual(init[:3], ["{", "1", ","])  # file-scope initializer
        self.assertTrue(all(t.init for t in toks if t.text == "0" and t.start < SRC.index("u32 r4")))


class Candidates(unittest.TestCase):
    def setUp(self):
        self.c = mutation.candidates(SRC)

    def whats(self, op):
        return [c["what"] for c in self.c if c["op"] == op]

    def test_operators(self):
        self.assertIn("< -> <=", self.whats("relop"))
        self.assertIn("!= -> ==", self.whats("relop"))
        self.assertIn("- -> +", self.whats("arith"))  # p - q
        self.assertEqual(len(self.whats("arith")), 3)  # p - q, += and r4 + n; not the unary -1
        self.assertEqual(self.whats("bitwise"), ["& -> |"])  # p & 0xff; not &a
        self.assertEqual(sorted(self.whats("shift")), ["<< -> >>", ">> -> <<"])
        self.assertEqual(len(self.whats("negate")), 3)  # if, while, if
        self.assertIn("return r4 + n -> return 0", self.whats("return0"))
        self.assertEqual(len(self.whats("return0")), 1)  # return 0 left alone

    def test_constants(self):
        consts = self.whats("const")
        self.assertIn("0x8c0c0000u -> 0x8c0c0001u", consts)
        self.assertFalse(any(w.startswith("3 ") for w in consts))  # case 3: a duplicate label
        self.assertFalse(any(w.startswith("4 ") for w in consts))  # the array size and the initializer
        self.assertTrue(any(w in ("2 -> 1", "2 -> 3") for w in consts))  # table[2], << 2: +1 or -1, seeded by place

    def test_delete_and_swap(self):
        dels = self.whats("delete")
        self.assertIn("deleted: wr32(0x8c0c0000u, p & 0xffu);", dels)
        self.assertIn("deleted: a.r[0] = p;", dels)
        self.assertIn("deleted: n = n >> 1;", dels)  # the body of a while without braces
        self.assertFalse(any("u32" in d for d in dels))  # declarations stay
        sw = self.whats("swap_args")
        self.assertIn("wr32(.. 0x8c0c0000u, p & 0xffu ..) -> wr32(.. p & 0xffu, 0x8c0c0000u ..)", sw)
        self.assertIn("swapped values of a.r[0] = p; and a.r[1] = q << 2;", sw)
        self.assertFalse(any(s.startswith("tc_call") for s in sw))  # target and &a: a type error, not a mutant
        swapped = mutation.apply(SRC, next(c for c in self.c if c["what"].startswith("swapped"))["edits"])
        self.assertIn("a.r[0] = q << 2;\n    a.r[1] = p;", swapped)
        deleted = mutation.apply(SRC, next(c for c in self.c if c["what"] == "deleted: n = n >> 1;")["edits"])
        self.assertIn("while (n != 0)\n        { }", deleted)


FRAME_SRC = """u32 fn_8c000100(u32 p)
{
    TcArgs args = {{0}, {0}, {0}, {0}};
    u32 sp = 0x8cffe000u - 16u;
    u32 i;
    wr32(sp + 4u, p);
    wr32(0x8cffdff4u, p);
    for (i = 0; i < 8; i++) {
        args.s[i] = rd32(sp + 4u * i);
    }
    args.r[3] = p + 1u;
    sp += 4u;
    wr32(0x8c0c0000u, p + 2u);
    return tc_call(0x8c010000u, &args);
}
"""


class Classes(unittest.TestCase):
    def cls(self, what):
        return [c["cls"] for c in mutation.candidates(FRAME_SRC) if c["what"] == what]

    def test_classes(self):
        self.assertEqual(self.cls("deleted: wr32(sp + 4u, p);"), ["stack"])
        self.assertEqual(self.cls("deleted: wr32(0x8cffdff4u, p);"), ["stack"])
        self.assertEqual(self.cls("deleted: sp += 4u;"), ["stack"])
        self.assertEqual(self.cls("deleted: args.r[3] = p + 1u;"), ["arg_slot"])
        self.assertEqual(self.cls("< -> <="), ["arg_slot"])  # the for header of a loop filling stack slots
        self.assertEqual(self.cls("deleted: wr32(0x8c0c0000u, p + 2u);"), ["logic"])
        self.assertEqual(self.cls("return tc_call(0x8c010000u, &args) -> return 0"), ["logic"])
        self.assertFalse(any(c["what"].startswith("deleted: {") for c in mutation.candidates(FRAME_SRC)))

    def test_stack_variables(self):  # a frame pointer set from a stack address, and pointers derived from it
        src = ("void f(u32 p)\n{\n    u32 fb = 0x8cffdfacu;\n    u32 q = fb + 0x10u;\n    u32 g = 0x8c0c0000u;\n"
               "    wr32(fb + 0x1cu, p);\n    wr32(q, p);\n    wr32(g + 4u, p);\n}\n")
        self.assertEqual(mutation_ops.stack_names(src), {"fb", "q"})
        by = {c["what"]: c["cls"] for c in mutation.candidates(src) if c["op"] == "delete"}
        self.assertEqual(by["deleted: wr32(fb + 0x1cu, p);"], "stack")
        self.assertEqual(by["deleted: wr32(q, p);"], "stack")
        self.assertEqual(by["deleted: wr32(g + 4u, p);"], "logic")

    def test_score_counts_logic_only(self):
        def judge(text, tag):  # every slot / stack change survives, every other is killed
            m = next((c for c in mutation.candidates(FRAME_SRC) if mutation.apply(FRAME_SRC, c["edits"]) == text), None)
            return fake_result("PASS" if m is None or m["cls"] != "logic" else "FAIL")
        rec = mutation.check_function("8c000100", FRAME_SRC, judge, dict(REPORT, max_mutants=30))
        self.assertGreater(rec["survived"], 0)
        self.assertEqual(rec["survived_by_class"]["logic"], 0)
        self.assertEqual(rec["score"], 100.0)
        self.assertLess(rec["score_raw"], 100.0)
        self.assertEqual(proof.mutation_flag(rec, REPORT), "ok")  # no logic survivor: not a weak harness
        self.assertTrue(all(s["cls"] in ("arg_slot", "stack") for s in rec["survivors"]))


class Generate(unittest.TestCase):
    def test_deterministic(self):
        a = mutation.generate(SRC, "8c000000", 16, 1)
        b = mutation.generate(SRC, "8c000000", 16, 1)
        self.assertEqual([m["src"] for m in a], [m["src"] for m in b])
        self.assertEqual(len(a), 16)
        self.assertEqual(len({m["src"] for m in a}), 16)
        self.assertNotIn(SRC, [m["src"] for m in a])
        self.assertEqual([m["op"] for m in a[:9]], list(mutation.OPERATORS))  # round-robin: every operator first
        other = mutation.generate(SRC, "8c000000", 16, 2)
        self.assertNotEqual([m["src"] for m in a], [m["src"] for m in other])
        self.assertEqual(len(mutation.generate(SRC, "8c000000", 4, 1)), 4)

    def test_cap_above_candidates(self):
        n = len(mutation.candidates(SRC))
        self.assertLessEqual(len(mutation.generate(SRC, "8c000000", 500, 1)), n)


def fake_result(verdict, stage="compare"):
    return {"verdict": verdict, "stage": stage, "failing": 0 if verdict == "PASS" else 3, "total": 96}


class Check(unittest.TestCase):
    def test_kill_survive_drop(self):
        def judge(text, tag):
            if text == SRC:
                return fake_result("PASS")
            if "<=" in text:  # the harness cannot tell < from <= here
                return fake_result("PASS")
            if "{ }" in text and "wr32(0x8c0c0000u" not in text:  # the store deleted: /W4 would see it
                return fake_result("FAIL", "build")
            if "q >> 2" in text:
                return fake_result("FAIL", "lint")
            return fake_result("FAIL")
        rec = mutation.check_function("8c000000", SRC, judge, dict(REPORT, max_mutants=40))
        self.assertGreater(rec["survived"], 0)
        self.assertEqual(rec["tried"], rec["killed"] + rec["survived"])
        self.assertEqual(rec["score"], mutation.score(rec["killed"], rec["survived"]))
        self.assertEqual(rec["dropped"], rec["dropped_build"] + rec["dropped_lint"])
        self.assertGreater(rec["dropped_build"], 0)
        s = rec["survivors"][0]
        self.assertEqual(s["op"], "relop")
        self.assertIn("+    if (p <= q) {", s["diff"])
        self.assertEqual(proof.mutation_flag(rec, REPORT), "weak")
        self.assertEqual(sum(v["survived"] for v in rec["by_op"].values()), rec["survived"])

    def test_all_killed(self):
        rec = mutation.check_function("8c000000", SRC,
                                      lambda t, tag: fake_result("PASS" if t == SRC else "FAIL"), REPORT)
        self.assertEqual((rec["score"], rec["survived"], rec["tried"]), (100.0, 0, 16))
        self.assertEqual(proof.mutation_flag(rec, REPORT), "ok")

    def test_baseline_fail(self):
        calls = []
        rec = mutation.check_function("8c000000", SRC, lambda t, tag: calls.append(tag) or fake_result("FAIL"),
                                      REPORT)
        self.assertIn("baseline FAIL", rec["error"])
        self.assertEqual(calls, ["base"])  # no mutant judged on a harness the matched C fails
        self.assertIsNone(rec["score"])

    def test_time_cap(self):
        t = [0.0]

        def clock():
            t[0] += 50.0
            return t[0]
        rec = mutation.check_function("8c000000", SRC, lambda s, tag: fake_result("PASS" if s == SRC else "FAIL"),
                                      dict(REPORT, time_cap_s=200), clock=clock)
        self.assertTrue(rec["time_capped"])
        self.assertLess(rec["tried"], 16)

    def test_outcome(self):
        self.assertEqual(mutation.outcome(fake_result("PASS")), "survived")
        self.assertEqual(mutation.outcome(fake_result("FAIL")), "killed")
        self.assertEqual(mutation.outcome(dict(fake_result("FAIL"), run_error="runner timeout")), "killed")
        self.assertEqual(mutation.outcome(fake_result("FAIL", "build")), "dropped_build")
        self.assertEqual(mutation.outcome(fake_result("FAIL", "lint")), "dropped_lint")
        self.assertIsNone(mutation.score(0, 0))

    def test_save_merges(self):
        with tempfile.TemporaryDirectory(dir=TMP) as d:
            p = pathlib.Path(d) / "state" / "mutation.json"
            p.parent.mkdir()
            mutation.save(p, {"8c000000": {"score": 100.0}}, REPORT)
            mutation.save(p, {"8c000100": {"score": 50.0}}, REPORT)
            data = json.loads(p.read_text())
            self.assertEqual(sorted(data["functions"]), ["8c000000", "8c000100"])
            self.assertEqual(data["settings"]["mode"], "report")
            self.assertEqual(proof.read_mutation(d)["8c000100"]["score"], 50.0)


class OutputCap(unittest.TestCase):
    """The judge's runner output cap (core/check.run_runner): C writing across the guest RAM makes a result file of
    GBs; past the cap the runner is stopped and its result emptied (every vector fails). A run under the cap, and a
    stale big result from an earlier run, are unaffected."""
    FLOOD = ("import sys, time\nwith open(sys.argv[1], 'w') as f:\n"
             "    for _ in range(int(sys.argv[2])):\n        f.write('W 0c000000 00\\n' * 64)\n        f.flush()\n"
             "        time.sleep(0.005)\n")

    def test_cap(self):
        with tempfile.TemporaryDirectory() as d:
            out = pathlib.Path(d) / "actual.txt"
            t0 = time.monotonic()
            rc, _, reason = check.run_runner([sys.executable, "-c", self.FLOOD, str(out), "100000"], 60, out, 8192)
            self.assertEqual((rc, reason), (None, "output over 0 MB"))
            self.assertEqual(out.stat().st_size, 0)
            self.assertLess(time.monotonic() - t0, 30)  # stopped by the cap, not by the timeout
            out.write_text("x" * 20000, encoding="utf-8")  # a stale result over the cap
            rc, _, reason = check.run_runner([sys.executable, "-c", self.FLOOD, str(out), "5"], 60, out, 8192)
            self.assertEqual((rc, reason), (0, None))
            self.assertTrue(0 < out.stat().st_size < 8192)  # the run wrote its result, untouched

    def test_cap_size(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(check.output_cap(d), check.OUTPUT_CAP_MIN)  # no expected.txt
            (pathlib.Path(d) / "expected.txt").write_bytes(b"x" * (3 << 20))
            self.assertEqual(check.output_cap(d), 24 << 20)

    def test_wiring(self):  # both judges run their runner through it
        for rel in ("core/check.py", "core/readable_check.py"):
            text = (TOOL / rel).read_text(encoding="utf-8")
            self.assertIn("run_runner(cmd, cfg[\"check_timeout_s\"], out, ", text, rel)

class Backlog(unittest.TestCase):
    """tools/proof_backlog.py: the flagged functions queued by kind, the status (CLEAR is the signal to enforce) and
    the per-project enforce switch (report only for an existing game, config.json's default true for a new one)."""

    def ws(self):
        reach = dict(summ(10, 6), call_sites={"8c000010": 0, "8c000020": 3}, reach={"runs": 0})
        tried = dict(summ(10, 7), call_sites={"8c000110": 0}, reach={"runs": 1})  # the reach pass already ran
        weak = {"tried": 8, "killed": 6, "survived": 2, "survived_by_class": {"logic": 2}, "score": 75.0}
        return workspace({"8c000000": reach, "8c000100": tried, "8c000200": None, "8c000300": summ(6, 6)},
                         mutation_rows={"8c000300": weak})

    def tool(self, *args):
        import subprocess
        return subprocess.run([sys.executable, str(TOOL / "tools/proof_backlog.py"), *map(str, args)],
                              capture_output=True, text=True, timeout=120)

    def test_kinds(self):
        sys.path.insert(0, str(TOOL / "tools"))
        import proof_backlog
        entries, _, _ = proof_backlog.backlog(self.ws())
        self.assertEqual([(e["kind"], e["addr"]) for e in entries],
                         [("reach", "8c000000"), ("vectors", "8c000100"), ("harness", "8c000300"),
                          ("no_data", "8c000200")])
        self.assertEqual((entries[0]["deficit"], entries[0]["untried_call_sites"]), (3, 1))  # 4 unreached, 1 allowed

    def test_queue_status_enforce(self):
        ws = self.ws()
        self.assertTrue(proof.coverage_settings(proof.project_cfg(ws))["enforce"])  # config.json: a new game enforces
        with tempfile.TemporaryDirectory(dir=TMP) as d:
            p = self.tool("queue", "--project", ws, "--out", d)
            self.assertEqual(p.returncode, 0, p.stderr)
            q = pathlib.Path(d) / f"pf_{ws.name}"
            self.assertEqual([ln.split()[0] for ln in (q / "reach.txt").read_text().splitlines()], ["8c000000"])
            self.assertEqual(json.loads((q / "backlog.json").read_text())["counts"],
                             {"reach": 1, "vectors": 1, "harness": 1, "no_data": 1})
        self.assertFalse((ws / "state" / "proof_backlog").exists())  # --out: nothing in the workspace
        p = self.tool("status", "--project", ws, "--check")
        self.assertEqual(p.returncode, 1)
        self.assertIn("coverage gate enforce; 2 below the coverage gate (reach 1, vectors 1)", p.stdout)
        before = (ws / "project.json").read_text()
        p = self.tool("enforce", "--project", ws, "off")
        self.assertIn('would set {"coverage_gate": {"enforce": false}}', p.stdout)
        self.assertEqual((ws / "project.json").read_text(), before)  # without --apply nothing is written
        self.tool("enforce", "--project", ws, "off", "--apply")
        pj = json.loads((ws / "project.json").read_text())
        self.assertEqual((pj["name"], pj["coverage_gate"]), ("pf", {"enforce": False}))
        self.assertIn("coverage gate report only", self.tool("status", "--project", ws).stdout)
        for a in ("8c000000", "8c000100"):  # more vectors reached the blocks: the backlog clears
            put(ws, f"harness/{a}/oracle_summary.json", summ(10, 10))
        p = self.tool("status", "--project", ws, "--check")
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertIn("CLEAR: no function below the coverage gate; switch it to enforce", p.stdout)
        self.tool("enforce", "--project", ws, "on", "--apply")
        p = self.tool("status", "--project", ws)
        self.assertIn("CLEAR: no function below the coverage gate (enforcing)", p.stdout)

    def test_set_enforce_keeps_the_file(self):
        sys.path.insert(0, str(TOOL / "tools"))
        import proof_backlog
        text = '{\n  "name": "pf",\n  "coverage_gate": {\n    "min_block_pct": 80\n  }\n}\n'
        new, old = proof_backlog.set_enforce(text, False)
        self.assertIsNone(old)
        self.assertEqual(json.loads(new), {"name": "pf", "coverage_gate": {"min_block_pct": 80, "enforce": False}})
        self.assertTrue(new.startswith('{\n  "name": "pf",') and new.endswith("}\n"))


class UnhitBlocks(unittest.TestCase):
    """oracle_summary.json blocks_unhit (TcPrepare, report only): the coverage verdict, its line, the watcher's brief
    and the backlog name the blocks no valid vector reached; a summary from before the field still works."""

    def low(self):
        return dict(summ(10, 6), blocks_unhit=["8c000010", "8c000020", "8c000040", "8c000060"])

    def test_verdict(self):
        self.assertEqual(proof.unhit_blocks(summ(6, 6)), [])  # every block reached: the field is omitted
        self.assertIsNone(proof.unhit_blocks(summ(10, 6)))  # an old summary: unknown
        self.assertIsNone(proof.unhit_blocks(None))
        v = proof.coverage_verdict(self.low(), GATE)
        self.assertEqual(v["unhit"], ["8c000010", "8c000020", "8c000040", "8c000060"])
        self.assertNotIn("unhit", proof.coverage_verdict(summ(10, 6), GATE))
        self.assertIn("4 unreached, 1 allowed (unreached blocks at 8c000010, 8c000020, 8c000040, 8c000060)",
                      proof.describe({"coverage": v}))
        self.assertEqual(proof.unhit_text([f"8c0000{i}0" for i in range(9)], 6),
                         "8c000000, 8c000010, 8c000020, 8c000030, 8c000040, 8c000050 and 3 more")

    def test_brief_and_backlog(self):
        from core import watch_brief
        cfg = {"min_valid_vectors": 16, "locked_scripts": ["TcPrepare.java"]}
        self.assertIn("blocks reached: 6 of 10 (not reached: 8c000010, 8c000020, 8c000040, 8c000060)",
                      watch_brief.watch_md(cfg, "8c000000", self.low()))
        self.assertIn("blocks reached: 6 of 10\n", watch_brief.watch_md(cfg, "8c000000", summ(10, 6)))
        self.assertIn("blocks 6/10 (not reached: 8c000010,",
                      watch_brief.format_result("try", {"summary": self.low(), "checks": []}))
        sys.path.insert(0, str(TOOL / "tools"))
        import proof_backlog
        entries, _, _ = proof_backlog.backlog(workspace({"8c000000": self.low(), "8c000100": summ(10, 6)}))
        self.assertEqual([e["unhit_blocks"] for e in entries], [["8c000010", "8c000020", "8c000040", "8c000060"], []])

    def test_java_is_output_only(self):  # one more summary key after the reach pass; nothing else reads it
        src = (TOOL / "targets/dreamcast_sh4/ghidra_scripts/TcPrepare.java").read_text(encoding="utf-8")
        self.assertIn("for (int i = 0; why == null && i < blocks.size(); i++) {", src)  # refused: no field
        self.assertLess(src.index("TcReach.run("), src.index("StringBuilder unhit"))  # after reach adds its vectors
        self.assertLess(src.index("StringBuilder unhit"), src.index("String summary"))
        self.assertEqual(src.count("unhit"), 7)  # declared, filled and written to the summary only


class Sizes(unittest.TestCase):
    def test_under_400_lines(self):
        for rel in ("core/proof.py", "core/mutation.py", "core/mutation_ops.py", "tools/mutation_check.py",
                    "tools/coverage_gate.py", "tools/proof_backlog.py"):
            self.assertLessEqual(len((TOOL / rel).read_text(encoding="utf-8").splitlines()), 400, rel)


if __name__ == "__main__":
    unittest.main()
