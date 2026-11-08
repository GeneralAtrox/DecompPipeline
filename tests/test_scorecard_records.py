"""The records the scorecard reads (split from tests/test_scorecard.py at its 400-line cap, its point 6): workers'
busy/idle time (a dead process's workers end at their last event), manual operator steps (one record in two workspaces
counts once; the first hand start is free), readable attempts' duration_s (rows timed in full only), and the gates
they measure. Needs git (a throwaway repository in a temp folder). Run: python tests/test_scorecard_records.py
"""
import json
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_scorecard import Fixture, attempt, sc, sd, workspace, write  # noqa: E402,F401


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
                 "cross_check": "same on build box B", "started": "2026-10-25T10:00:00"},
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

    def test_readable_in_game(self):  # 2026-11-06: readable C counts as done only once proven in the running game
        proj = self.tmp / "projects_ig"
        g = proj / "Fake" / "GameI"
        ws = g / "decomp"
        rd = {a: {"status": "readable"} for a in ("8c000010", "8c000020", "8c000030")}
        rd["8c000040"] = {"status": "failed"}
        workspace(ws, "bin-i", {"8c000010": {"status": "matched"}}, rd)
        write(g / "port" / "game.json", {})
        write(proj / "External" / "size.md", "about 1000 functions")
        for a in rd:
            write(ws / "readable" / "src" / f"{a}.c", f"int f{a}(void) {{ return 0; }}\n")
        from core import in_game
        sha = {a: in_game.sha(ws / "readable" / "src" / f"{a}.c") for a in rd}

        def row():
            card = sc.scorecard(self.spec, sd.Sources(self.tool), proj, keys=["fake"], scope=False)
            return self.checks(card, "fake", 3)["readable C proven in the running game"]
        self.assertEqual(row()["status"], "unmeasured")  # no coverage.json, no in_game.json
        write(ws / "state" / "coverage.json", {"scenarios": {"boot": {"entered": ["8c000010", "8c000020"]},
                                                             "lvl": {"entered": ["8c000040"]}}})
        self.assertEqual(row()["status"], "unmeasured")  # coverage but no in_game.json
        write(ws / "state" / "in_game.json", {"8c000010": {"sha": sha["8c000010"]}, "8c000020": {"sha": "stale"},
                                              "8c000030": {"sha": sha["8c000030"]},
                                              "8c000040": {"sha": sha["8c000040"]}})
        r = row()
        self.assertEqual((r["status"], r["value"], r["target"]), ("fail", 50.0, ">= 100.0%"))  # 1 of 2 reached
        self.assertIn("1/2 readable functions a scenario enters proven, 2/3 of all readable", r["source"])
        write(ws / "state" / "in_game.json", {"8c000010": {"sha": sha["8c000010"]}, "8c000020": {"sha": sha["8c000020"]}})
        self.assertEqual((row()["status"], row()["value"]), ("pass", 100.0))
        write(ws / "state" / "coverage.json", {"scenarios": {"lvl": {"entered": ["8c000040"]}}})
        self.assertIn("no divergence scenario enters a readable function", row()["missing"])

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
    unittest.main()
