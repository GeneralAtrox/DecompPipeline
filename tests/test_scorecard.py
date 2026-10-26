"""tools/scorecard.py and tools/scorecard_data.py (docs/DEFINITION_OF_DONE.md), offline on synthetic folders:
1. the waste audit's findings table: fixed and proven-unavoidable rows are closed, proposals and report-only open;
2. mutation-audit kill rates counted as tools/mutation_audit.py counts them (lint/build drops left out);
3. sources: a file in the checkout, on a pending branch only (partial), or nowhere; an external own translator;
4. workspaces: discovery (never inside a workspace or its state), per-game totals, pilots of one binary counted
   once, the ledger-rows fallback named as the denominator's source;
5. the gates end to end: colours, "unmeasured" with no number and the missing source named, info rows ignored;
6. the records DP keeps for the scorecard: workers' busy/idle time (a dead process's workers end at their last event),
   manual operator steps (one record in two workspaces counts once; the first hand start is free), readable
   attempts' duration_s (rows timed in full only), and the gates they measure.
Needs git (a throwaway repository in a temp folder). Run: python tests/test_scorecard.py
"""
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

TOOL = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(TOOL / "tools"), str(TOOL)]
import scorecard as sc  # noqa: E402
import scorecard_data as sd  # noqa: E402

AUDIT = """# Waste audit

| Where | Saving | Status |
|-------|--------|--------|
| Per wave | 1 h | landed on the branch |

| # | Finding | Saving | Status | Byte-identical |
|---|---------|--------|--------|----------------|
| 1 | Stamp scan re-hashed files | 1 h | **fixed** | yes |
| 2 | Cold JVM per prepare | 6 s | proposal | yes |
| 3 | A scan nothing can avoid | 30 s | proven unavoidable | n/a |
| 4 | Leftover disk | - | report only, nothing deleted | n/a |

Text after the table.
"""


def git(d, *args):
    subprocess.run(["git", "-C", str(d), *args], check=True, capture_output=True, timeout=60)


def write(p, text):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text if isinstance(text, str) else json.dumps(text), encoding="utf-8")


def attempt(sec, usd, tin, tout):
    return {"model": "haiku", "duration_s": sec, "cost_usd": usd, "tokens_in": tin, "tokens_out": tout}


def workspace(d, binary, ledger, readable=None, prep=(), audit=None, div=None):
    write(d / "project.json", {"name": d.name, "target": "fake_cpu", "binary_sha256": binary})
    write(d / "state" / "ledger.json", {"functions": ledger})
    if readable is not None:
        write(d / "state" / "readable.json", readable)
    if prep:
        write(d / "state" / "prepare_timing.jsonl", "\n".join(json.dumps({"phases": {"total": t}}) for t in prep))
    if audit:
        write(d / "state" / "final_audit-20261022-000000.txt", f"lines\nVERDICT {audit}: details\n")
    if div:
        write(d / "state" / "divergence.json", {"when": "2026-10-22", "counts": div})


class Fixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = pathlib.Path(tempfile.mkdtemp(prefix="scorecard_"))
        tool = cls.tmp / "dp"
        for rel in ("targets/fake_cpu/tools/fakedisc.py", "targets/fake_cpu/ghidra_scripts/TcCatalog.java",
                    "targets/fake_cpu/ghidra_scripts/TcPrepare.java", "targets/fake_cpu/ghidra_scripts/TcOracle.java",
                    "targets/fake_cpu/runtime/runner.c", "targets/moved_cpu/translator/movedtranslate/__init__.py"):
            write(tool / rel, "x")
        git(tool, "init", "-q")
        git(tool, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "base")
        git(tool, "add", "-A")
        git(tool, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "checkout")
        git(tool, "checkout", "-q", "-b", "pending")
        write(tool / "targets/fake_cpu/ghidra_scripts/TcBound.java", "x")
        write(tool / "docs/WASTE_AUDIT_2026-10-22.md", AUDIT)
        base = {"0601": {"outcomes": "KKSLB", "sha": "a"}, "0602": {"error": "baseline FAIL"},
                "0603": {"outcomes": "KS", "sha": "b"}}
        write(tool / "tools/mutation_audit/baseline.json", {"fakews": base, "other": {"1": {"outcomes": "K"}}})
        git(tool, "add", "-A")
        git(tool, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "pending")
        git(tool, "checkout", "-q", "-")
        cls.tool = tool
        proj = cls.tmp / "projects"
        g = proj / "Fake" / "GameA"
        led = {"a1": {"status": "matched", "attempts": [attempt(20.0, 0.01, 1000, 200)]},
               "a2": {"status": "matched", "attempts": [attempt(10.0, 0.01, 800, 0), {"model": "import"}]},
               "a3": {"status": "failed", "attempts": [attempt(30.0, 0.02, 2000, 0)]},
               "a4": {"status": "queued"}}
        rd = {"a1": {"status": "readable", "verified": "2026-10-22", "attempts": [attempt(None, 0.01, 500, 0)]},
              "a2": {"status": "failed", "attempts": [attempt(None, 0.01, 500, 0)]}}
        workspace(g / "decomp" / "main", "bin-a", led, rd, prep=(1.0, 3.0, 2.0), audit="INCOMPLETE",
                  div={"clean": 2, "diverged": 1, "error": 0})
        workspace(g / "decomp" / "main" / "state" / "nested", "bin-x", {})  # inside a workspace: never found
        workspace(g / "research" / "pilot", "bin-a", {"a1": {"status": "matched"}, "a4": {"status": "matched"}})
        write(g / "port" / "game.json", {})
        cls.proj = proj
        cls.spec = json.loads((TOOL / "tools" / "scorecard" / "spec.json").read_text(encoding="utf-8"))
        cls.spec["consoles"] = {
            "fake": {"name": "Fake", "target": "fake_cpu", "cpu": "fake", "roots": ["Fake"],
                     "own_translator": "External/fake-translate"},
            "none": {"name": "None", "target": "none_cpu", "cpu": "none", "roots": ["Missing"], "own_translator": None}}
        cls.spec["mutation_keys"] = {"fakews": "fake"}
        cls.spec["baseline"]["functions_source"] = {"path": "External/size.md", "regex": r"(\d+) functions"}
        write(proj / "External" / "size.md", "about 1000 functions")
        (proj / "External" / "fake-translate").mkdir(parents=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def card(self, pending=()):
        return sc.scorecard(self.spec, sd.Sources(self.tool, pending), self.proj, scope=False)

    @staticmethod
    def checks(card, key, gate):
        return {c["name"]: c for c in card[key]["gates"][gate]["checks"]}


class Readers(Fixture):
    def test_waste_audit_table(self):
        p = sd.parse_waste_audit(AUDIT)
        self.assertEqual([r[0] for r in p["findings"]], ["1", "2", "3", "4"])
        self.assertEqual([r[0] for r in p["open"]], ["2", "4"])  # fixed and proven unavoidable are closed

    def test_audit_order(self):  # by the title's date: the second audit's file name sorts before the first's
        self.assertEqual(sd.audit_date("# Waste audit 2, 2026-10-23\ntext 2027-01-01"), "2026-10-23")
        self.assertEqual(sd.audit_date(AUDIT), "")
        d = pathlib.Path(tempfile.mkdtemp(dir=self.tmp))
        write(d / "docs/WASTE_AUDIT_2026-10-22.md", "# Waste audit, 2026-10-22\n")
        write(d / "docs/WASTE_AUDIT_2.md", "# Waste audit 2, 2026-10-23\n")
        src = mock.Mock(tool=d, pending=(), worktrees=lambda: [])
        self.assertEqual([a[0] for a in sd.waste_audits(src)], ["WASTE_AUDIT_2.md", "WASTE_AUDIT_2026-10-22.md"])

    def test_mutation_rates(self):
        r = sd.mutation_rates(json.dumps({"k": {"f": {"outcomes": "KKSLB"}, "g": {"error": "x"},
                                               "h": {"outcomes": "KS"}}, "e": {}}))
        self.assertEqual(r["k"], {"functions": 2, "mutants": 5, "killed": 3, "rate": 60.0})
        self.assertIsNone(r["e"]["rate"])

    def test_sources(self):
        src = sd.Sources(self.tool, ["pending"])
        self.assertEqual(src.where("targets/fake_cpu/runtime/runner.c"), "checkout")
        self.assertEqual(src.where("targets/fake_cpu/ghidra_scripts/TcBound.java"), "branch pending")
        self.assertEqual(src.where("targets/fake_cpu/tools/*disc*.py"), "checkout")
        self.assertIsNone(src.where("core/strict_judge.py"))
        self.assertIsNone(sd.Sources(self.tool).where("targets/fake_cpu/ghidra_scripts/TcBound.java"))
        text, where = src.read("tools/mutation_audit/baseline.json")
        self.assertEqual(where, "branch pending")
        self.assertIn("fakews", json.loads(text))

    def test_discovery_and_games(self):
        found, cut = sd.discover(self.proj / "Fake")
        self.assertFalse(cut)
        self.assertEqual(sorted(p.name for p in found), ["main", "pilot"])  # not the one inside main/state
        ms = [sd.workspace_metrics(p, with_scope=False) for p in found]
        main = next(m for m in ms if m["name"] == "main")
        self.assertEqual(main["scope_source"], "ledger rows")
        self.assertEqual(main["agent"], {"attempts": 3, "seconds": 60.0, "timed": 3, "usd": 0.04, "costed": 3,
                                         "tokens": 4000, "tokened": 3})
        self.assertEqual(main["readable"]["verified"], 1)
        self.assertEqual(main["final_audit"]["verdict"], "INCOMPLETE")
        self.assertTrue(main["port"])
        games = sc.by_game(ms)
        self.assertEqual(len(games), 1)
        self.assertEqual((games[0]["scope"], games[0]["matched"]), (4, 3))  # one binary: a1 counted once


class Gates(Fixture):
    def test_rules(self):
        self.assertEqual(sc.at_most("x", 3, 2, "s")["status"], "partial")
        self.assertEqual(sc.at_most("x", 5, 2, "s")["status"], "fail")
        self.assertEqual(sc.at_least("x", 95, 100, "s")["status"], "partial")
        self.assertEqual(sc.at_least("x", None, 100, "no file")["status"], "unmeasured")
        self.assertEqual(sc.gate_status([sc.check("a", "pass"), sc.check("b", "info")]), "green")
        self.assertEqual(sc.gate_status([sc.check("a", "pass"), sc.unmeasured("b", "none")]), "amber")
        self.assertEqual(sc.gate_status([sc.check("a", "partial"), sc.check("b", "fail")]), "red")

    def test_toolset(self):
        card = self.card(["pending"])
        c = self.checks(card, "fake", 0)
        self.assertEqual(c["disc extraction"]["status"], "pass")
        self.assertEqual(c["oracle and harness"]["status"], "pass")
        self.assertEqual(c["own translator"]["status"], "partial")
        self.assertIn("External/fake-translate", c["own translator"]["missing"])
        self.assertEqual(c["readable C"]["status"], "fail")
        self.assertEqual(c["lockstep divergence finder"]["status"], "fail")  # needs the port stage
        self.assertEqual(card["fake"]["gates"][0]["status"], "red")

    def test_translator_in_dp(self):  # imported into DP: the checkout counts, the old outside folder no longer matters
        con = {"name": "Moved", "target": "moved_cpu", "cpu": "moved", "own_translator": "External/fake-translate"}
        c = {x["name"]: x for x in sc.gate_toolset(sd.Sources(self.tool, ["pending"]), self.spec, con, self.proj)}
        self.assertEqual((c["own translator"]["status"], c["own translator"]["source"]), ("pass", "checkout"))

    def test_handsoff_trust_waste(self):
        card = self.card(["pending"])
        h = self.checks(card, "fake", 1)
        self.assertEqual(h["reference game"]["value"], "GameA")
        self.assertEqual(h["raw verified (matched / functions in scope)"]["value"], 75.0)
        self.assertEqual(h["final audit"]["status"], "partial")
        self.assertEqual(h["zero divergence"]["status"], "fail")
        self.assertEqual(h["manual operator steps"]["status"], "unmeasured")
        t = self.checks(card, "fake", 3)
        self.assertEqual(t["pre-wave item 1 targeted vectors"]["status"], "partial")
        self.assertEqual(t["pre-wave item 2 stricter judge"]["status"], "fail")
        self.assertEqual(t["mutation-audit kill rate (lowest workspace)"]["value"], 60.0)  # 3 killed of 5
        w = self.checks(card, "fake", 2)
        self.assertEqual(w["waste audit open findings"]["value"], 2)
        self.assertIn("branch pending", w["waste audit open findings"]["source"])
        self.assertEqual(w["prepare median per function"]["value"], 2.0)
        self.assertEqual(w["agent seconds per matched function (raw)"]["value"], 20.0)  # 60 s over 3 matched

    def test_nothing_invented(self):
        card = self.card()  # no pending refs: no audit, no baseline
        for key in card:
            for g in card[key]["gates"]:
                for c in g["checks"]:
                    if c["status"] == "unmeasured":
                        self.assertIsNone(c["value"], c)
                        self.assertTrue(c["missing"], c)
        w = self.checks(card, "fake", 2)
        self.assertEqual(w["waste audit open findings"]["status"], "unmeasured")
        self.assertEqual(w["idle share"]["status"], "unmeasured")
        self.assertEqual(self.checks(card, "none", 1)["reference game"]["status"], "fail")
        s = self.checks(card, "none", 4)
        self.assertEqual(s["baseline size"]["value"], 1000)
        self.assertEqual(s["projected agent cost"]["status"], "unmeasured")
        self.assertEqual(s["projected machine hours (raw + readable)"]["status"], "unmeasured")
        self.assertEqual(s["for scale only: at Fake's measured rates"]["status"], "info")
        text = sc.render(card)  # renders without error
        self.assertIn("Fake", text)
        self.assertIn("1 Full toolset", text)

    def test_agent_figures(self):  # translated and imported attempts are no agent's; a missing figure adds nothing
        rows = [{"attempts": [{"model": "translated", "duration_s": 0.2, "cost_usd": 0.0},
                              {"model": "import"}, {"model": "haiku", "duration_s": 5.0}]}]
        t = sd._attempt_totals(rows)
        self.assertEqual((t["attempts"], t["timed"], t["costed"], t["tokened"]), (1, 1, 0, 0))
        r = sc.rates([{"agent": t, "readable": None, "prepare_s": []}], [{"matched": 1}])
        self.assertIsNone(r["raw_usd"])  # no attempt carries a cost: unmeasured, not 0
        self.assertIsNone(r["raw_tokens"])
        self.assertEqual(r["raw_s"], 5.0)

    def test_scope_fallback_never_green(self):  # ledger rows as the scope (core.audit could not read it): not pass
        ws = {"scope_source": "ledger rows (core.audit: KeyError)", "game_root": "g", "binary": "b",
              "_scope": {"a1"}, "_matched": {"a1"}, "readable": None, "port": True, "scenarios": False,
              "final_audit": None, "divergence": None}
        (g,) = sc.by_game([ws])
        h = {c["name"]: c for c in sc.gate_handsoff([g], self.spec["thresholds"])}
        raw = h["raw verified (matched / functions in scope)"]
        self.assertEqual((raw["value"], raw["status"]), (100.0, "partial"))
        self.assertIn("ledger rows", raw["source"])
        self.assertEqual(h["port builds"]["status"], "unmeasured")  # a port folder is not a build


BUSY_LOG = """100.0 W1 +
100.0 W1 p
110.0 W1 .
110.0 W1 a
150.0 W1 .
160.0 W1 -
100.0 W2 +
105.0 W2 j
108.0 W2 a
120.0 W2 .
130.0 W2 -
200.0 W3 +
210.0 W3 a
not a line
300.0 W9 a
400.0 W4 +
405.0 W4 a
410.0 W4 +
420.0 W4 -
"""


def op(at, kind, run, step="x", tool="wave.py"):
    return {"at": at, "kind": kind, "tool": tool, "step": step, "run": run}


class Records(Fixture):
    """The three records the scorecard reads (core/busylog, core/oplog, core/refine's duration_s): counted from the
    files, and "unmeasured" when a file is absent (Gates.test_nothing_invented)."""

    def test_busy_totals(self):
        st = self.tmp / "busy" / "state"
        write(st / "worker_busy" / "1-1.log", BUSY_LOG)
        t = sd.busy_totals(st)
        # W1 60 s alive, 50 busy; W2 30, 15 (j ends where a starts); W3 never ended (a killed process: its idle time
        # after 210 is lost) and W4's first life (started again without an end): left out, counted as open; W4's
        # second life 10, 0; W9 never started: left out
        self.assertEqual((t["lives"], t["alive_s"], t["busy_s"], t["open"]), (3, 100.0, 65.0, 2))
        self.assertEqual(t["by"], {"p": 10.0, "a": 52.0, "j": 3.0})
        self.assertIsNone(sd.busy_totals(self.tmp / "busy" / "none"))

    def test_operator_steps(self):
        a = [op("2026-10-22T10:00:00", "start", "r1"), op("2026-10-22T10:05:00", "stop", "r1", "hold: still busy")]
        b = [a[1], op("2026-10-22T11:00:00", "start", "r2", tool="refine.py run"),
             op("2026-10-22T11:30:00", "act", "r2", "/api/stop {}", "monitor")]
        n = sd.operator_steps([a, b])  # the stop wave.py wrote into both workspaces counts once
        self.assertEqual((n["start"], n["stop"], n["act"], n["steps"]), (2, 1, 1, 3))
        self.assertEqual(n["since"], "2026-10-22T10:00:00")
        self.assertEqual(sd.operator_steps([[a[0]]])["steps"], 0)  # one hand start, nothing else: hands-off
        st = self.tmp / "ops" / "state"
        write(st / "operator_steps.jsonl", json.dumps(a[0]) + "\nnot json\n" + json.dumps({"kind": "other"}) + "\n")
        self.assertEqual(sd.oplog_records(st), [a[0]])
        self.assertIsNone(sd.oplog_records(self.tmp / "ops" / "none"))

    def test_job_days(self):  # tools/jobreg.py's records: the waste the guard saw and stopped, per day
        cmd = "python tools/regress.py --projects tc --jobs 2"
        recs = [{"event": "start", "id": "a", "inputs": "I", "key": "K",
                 "command": f"python tools/bounded.py 900 {cmd}", "started": "2026-10-24T10:00:00"},
                {"event": "end", "id": "a", "status": "done", "seconds": 7200.0},
                {"event": "reuse", "of": "a", "saved_s": 7200.0, "at": "2026-10-24T13:00:00"},
                {"event": "start", "id": "b", "inputs": "J", "key": "L", "command": "python tools/wave.py run",
                 "started": "2026-10-24T14:00:00"},
                {"event": "end", "id": "b", "status": "timeout", "seconds": 3600.0},
                {"event": "refuse", "reason": "timeout", "of": "b", "at": "2026-10-25T09:00:00"},
                {"event": "refuse", "reason": "running", "at": "2026-10-25T09:05:00"},
                {"event": "start", "id": "c", "inputs": "J", "key": "L", "command": "python tools/wave.py run",
                 "cross_check": "same on laptop2", "started": "2026-10-25T10:00:00"},
                {"event": "outside", "jobs": ["python tools/regress.py --projects tc --jobs 4"],
                 "at": "2026-10-25T11:00"},
                {"event": "outside", "jobs": ["python tools/prepare_list.py --project W @l"],
                 "at": "2026-10-25T12:00"},
                {"event": "outside", "jobs": [cmd], "denied": True, "at": "2026-10-25T12:30"}]  # blocked: never ran
        d = sd.job_days(recs)
        self.assertEqual(d["2026-10-24"], {"run": 2, "run_h": 3.0, "reused": 1, "saved_h": 2.0, "refused": 0,
                                           "refused_h": 0, "cross_checks": 0, "outside": 0, "duplicates": 0})
        self.assertEqual(d["2026-10-25"], {"run": 1, "run_h": 0, "reused": 0, "saved_h": 0, "refused": 2,
                                           "refused_h": 1.0, "cross_checks": 1, "outside": 2, "duplicates": 1})
        self.assertIn("2026-10-25", sc.render_jobs(d, "R"))
        self.assertIn("no records", sc.render_jobs({}, "R"))

    def test_readable_time(self):
        timed = {"status": "readable", "attempts": [attempt(30.0, 0.01, 1, 1), attempt(10.0, 0.01, 1, 1),
                                                    {"kind": "sync", "verdict": "PASS"}]}
        rows = [timed, {"status": "failed", "attempts": [attempt(20.0, 0.01, 1, 1)]},
                {"status": "readable", "attempts": [attempt(None, 0.01, 1, 1), attempt(5.0, 0.01, 1, 1)]},  # older
                {"status": "readable", "attempts": [{"kind": "draft", "verdict": "PASS"}]}]  # no agent: left out
        self.assertEqual(sd.readable_time(rows), {"rows": 2, "readable": 1, "seconds": 60.0})

    def test_gates_from_the_records(self):
        proj = self.tmp / "projects_rec"
        g = proj / "Fake" / "GameR"
        ws = g / "decomp"
        led = {"b1": {"status": "matched", "attempts": [attempt(20.0, 0.01, 1000, 200)]}}
        rd = {"b1": {"status": "readable", "verified": "2026-10-22", "attempts": [attempt(36.0, 0.01, 500, 0)]}}
        workspace(ws, "bin-r", led, rd, prep=(1.0,))
        write(ws / "state" / "worker_busy" / "7-1.log", "0.0 W1 +\n0.0 W1 a\n95.0 W1 .\n100.0 W1 -\n")
        write(ws / "state" / "operator_steps.jsonl", json.dumps(op("2026-10-22T10:00:00", "start", "r1")) + "\n")
        write(g / "port" / "game.json", {})
        write(g / "port" / "state" / "operator_steps.jsonl",
              json.dumps(op("2026-10-22T12:00:00", "start", "r2", tool="port build.py")) + "\n")
        write(proj / "External" / "size.md", "about 1000 functions")
        card = sc.scorecard(self.spec, sd.Sources(self.tool), proj, keys=["fake"], scope=False)
        h = self.checks(card, "fake", 1)["manual operator steps (since the log began)"]
        self.assertEqual((h["status"], h["value"]), ("fail", 1))  # the port build was a second hand start
        self.assertIn("2 hand start(s)", h["source"])
        w = self.checks(card, "fake", 2)["idle share (worker time not on real work)"]
        self.assertEqual((w["status"], w["value"]), ("pass", 5.0))
        self.assertIn("agent 95.0%", w["source"])
        self.records_never_green(proj, g, ws)
        from core import toolcfg
        workers = toolcfg.load(sc.TOOL).get("max_workers") or 1
        s = self.checks(card, "fake", 4)["projected machine hours (raw + readable)"]
        raw = self.checks(card, "fake", 4)["projected machine hours (raw stage)"]["value"]
        self.assertAlmostEqual(s["value"], round(raw + 1000 * 36.0 / workers / 3600, 4), places=3)
        self.assertIn("1 readable of 1 rows timed in full", s["source"])

    def records_never_green(self, proj, g, ws):
        """A log with no valid record, a lone hand start with no run reaching the port folder, and workers that all
        lack their end: unmeasured, never 0 steps or a low idle share."""
        def card():
            return sc.scorecard(self.spec, sd.Sources(self.tool), proj, keys=["fake"], scope=False)
        write(g / "port" / "state" / "operator_steps.jsonl", "not json\n" + json.dumps({"kind": "other"}) + "\n")
        write(ws / "state" / "operator_steps.jsonl", "")
        self.assertEqual(self.checks(card(), "fake", 1)["manual operator steps"]["status"], "unmeasured")
        write(ws / "state" / "operator_steps.jsonl", json.dumps(op("2026-10-22T10:00:00", "start", "r1")) + "\n")
        h = self.checks(card(), "fake", 1)["manual operator steps"]
        self.assertEqual(h["status"], "unmeasured")  # one start, nothing else, but the run never reached the port
        self.assertIn("no end-to-end run recorded", h["missing"])
        write(g / "port" / "state" / "operator_steps.jsonl",
              json.dumps(op("2026-10-22T10:30:00", "act", "r1", "/api/stop {}", "monitor")) + "\n")
        h = self.checks(card(), "fake", 1)["manual operator steps (since the log began)"]
        self.assertEqual((h["status"], h["value"]), ("fail", 1))
        write(g / "port" / "state" / "operator_steps.jsonl",
              json.dumps(op("2026-10-22T11:00:00", "stop", "r1", "port build ok", "port build.py")) + "\n")
        self.assertEqual(self.checks(card(), "fake", 1)["manual operator steps (since the log began)"]["status"], "fail")
        write(ws / "state" / "worker_busy" / "7-1.log", "0.0 W1 +\n0.0 W1 a\n95.0 W1 .\n")  # killed: no end
        w = self.checks(card(), "fake", 2)["idle share"]
        self.assertEqual(w["status"], "unmeasured")
        self.assertIn("1 worker run(s), none with its end", w["missing"])
        write(ws / "state" / "worker_busy" / "7-1.log", "0.0 W1 +\n0.0 W1 a\n95.0 W1 .\n100.0 W1 -\n")


if __name__ == "__main__":
    unittest.main(verbosity=1)
