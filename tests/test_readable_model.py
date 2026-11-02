"""Readable C runs on config readable_model, never on model (raw C's). The user (2026-11-01): "Unless Haiku has proven to be good at
readable C, use Opus." Controls, with model and readable_model always different so a fallback shows:
1. config.json: readable_model opus, model haiku, and a rate for each, so a killed Opus session still costs something in the ledger;
2. the real refine.refine (agent, sandbox and judge faked): the session and the ledger get readable_model, --model wins, and a
   config without readable_model stops instead of falling back to model;
3. the session's spend ceiling is readable_budget_usd, not raw's attempt_budget_usd;
4. tools/name_pass.py's session is on readable_model;
5. raw attempts stay on model (core/job.model_cfg);
6. usage_report readable shows each attempt's function, model and cost, and a total per model.
Run: python -W error tests/test_readable_model.py
"""
import atexit
import contextlib
import io
import json
import os
import pathlib
import shutil
import sys
import tempfile
import unittest
from unittest import mock

TOOL = pathlib.Path(__file__).resolve().parents[1]
(TOOL / "tests" / ".work").mkdir(parents=True, exist_ok=True)
_TMP = tempfile.TemporaryDirectory(prefix="rmodel_", dir=TOOL / "tests" / ".work")
TMP = pathlib.Path(_TMP.name)
atexit.register(_TMP.cleanup)  # as test_tooling: unittest runs module cleanups when it switches module
if not (pathlib.Path(os.environ.get("DECOMP_PROJECT", "")) / "project.json").exists():  # alone: a project for paths
    (TMP / "project.json").write_text(json.dumps({"target": "dreamcast_sh4", "ghidra_name": "fixture",
                                                  "binary": "game.bin"}), encoding="utf-8")
    os.environ["DECOMP_PROJECT"] = str(TMP)
sys.path.insert(0, str(TOOL))
sys.path.insert(0, str(TOOL / "tools"))

from core import job, paths, refine  # noqa: E402
import name_pass, usage_readable  # noqa: E401,E402

A = "8c010000"
CFG = {"model": "raw-model", "readable_model": "readable-model", "effort": None, "readable_effort": None,
       "attempt_budget_usd": 1.0, "readable_budget_usd": 5.0}


class Config(unittest.TestCase):
    def test_config(self):
        cfg = json.loads((TOOL / "config.json").read_text(encoding="utf-8"))
        self.assertEqual((cfg["readable_model"], cfg["model"]), ("opus", "haiku"))
        self.assertLessEqual({"opus", "haiku"}, set(cfg["agent_rates"]))
        self.assertEqual(set(cfg["agent_rates"]["opus"]) - {"long_tokens", "long_x"}, {"input", "cache_read", "cache_write_5m",
                                                                                      "cache_write_1h", "output"})
        self.assertGreater(cfg["readable_budget_usd"], cfg["attempt_budget_usd"])


class Refine(unittest.TestCase):
    def setUp(self):
        self.d = pathlib.Path(tempfile.mkdtemp(dir=TMP))
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        (self.d / "box").mkdir()
        (self.d / "box" / "work.c").write_text("/* work */", encoding="utf-8")
        (self.d / "src").mkdir()
        self.sessions = []

        def run_attempt(acfg, *a):
            self.sessions.append(acfg)
            return {}
        patches = [mock.patch.object(refine, "LEDGER", self.d / "readable.json"),
                   mock.patch.object(refine.readable, "SRC", self.d / "src"),
                   mock.patch.object(refine.readable, "Registry", lambda: None),
                   mock.patch.object(paths, "RUNS", self.d / "runs"),
                   mock.patch.object(refine, "raw_missing", lambda a: None),
                   mock.patch.object(refine, "harness_stale", lambda cfg, a, **k: None),
                   mock.patch.object(refine, "preflight", lambda cfg, reg, a, meta: None),
                   mock.patch.object(refine, "build_sandbox", lambda *a: (self.d / "box", "task")),
                   mock.patch.object(refine, "tree_hashes", lambda box: {}),
                   mock.patch.object(refine.agent, "run_attempt", run_attempt),
                   mock.patch.object(refine.rcheck, "check_readable", lambda *a, **k: {"verdict": "FAIL", "stage": "run"})]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def run_it(self, cfg=CFG, **k):
        refine.refine(cfg, A, max_attempts=1, log=lambda m: None, **k)
        return refine.load_ledger()[A]

    def test_session_and_ledger_use_readable_model(self):
        row = self.run_it()
        self.assertEqual(self.sessions[0]["model"], "readable-model")
        self.assertEqual((row["model"], row["attempts"][-1]["model"]), ("readable-model", "readable-model"))

    def test_the_flag_overrides(self):
        row = self.run_it(model="flag-model")
        self.assertEqual((self.sessions[0]["model"], row["model"]), ("flag-model", "flag-model"))

    def test_no_fallback_to_the_raw_model(self):
        with self.assertRaises(KeyError):
            self.run_it({k: v for k, v in CFG.items() if k != "readable_model"})
        self.assertEqual(self.sessions, [])

    def test_budget_is_the_readable_one(self):
        self.run_it()
        self.assertEqual(self.sessions[0]["attempt_budget_usd"], 5.0)


class Others(unittest.TestCase):
    def test_name_pass_uses_readable_model(self):
        seen = []

        def run_bounded(cmd, *a, **k):
            seen.append(cmd)
            return 0, "", None
        cfg = dict(CFG, claude=sys.executable, attempt_timeout_s=5, agent_system_prompt="lean", shared_prefix=False)
        with mock.patch.object(paths, "run_bounded", run_bounded):
            name_pass.ask_model(cfg, "prompt", None)
        self.assertEqual(seen[0][seen[0].index("--model") + 1], "readable-model")

    def test_raw_attempts_stay_on_model(self):
        with mock.patch.object(paths, "STATE", pathlib.Path(tempfile.gettempdir()) / "no_such_state_dir"):
            self.assertEqual(job.model_cfg(CFG, A)["model"], "raw-model")

    def test_usage_report_shows_function_model_and_cost(self):
        rows = [{"addr": A, "n": 1, "verdict": "PASS", "model": "claude-opus-5-5", "cost": 0.5, "effort": "high", "killed": None,
                 "when": 1.0, "reads": [], "refused": [], **{k: 1 for k in usage_readable.K}},
                {"addr": "8c010100", "n": 1, "verdict": "FAIL", "model": "claude-haiku-5-5", "cost": 0.25, "effort": "high",
                 "killed": None, "when": 2.0, "reads": [], "refused": [], **{k: 1 for k in usage_readable.K}}]
        out = io.StringIO()
        with mock.patch.object(usage_readable, "rows", lambda ws, since: rows), contextlib.redirect_stdout(out):
            usage_readable.report(pathlib.Path("ws"))
        text = out.getvalue()
        self.assertRegex(text, rf"{A}\s+1 PASS\s+claude-opus-5-5\s+0\.500")
        self.assertIn("model claude-opus-5-5: 1 functions, 1 attempts, 1 PASS, $0.50", text)
        self.assertIn("model claude-haiku-5-5: 1 functions, 1 attempts, 0 PASS, $0.25", text)


if __name__ == "__main__":
    unittest.main()
