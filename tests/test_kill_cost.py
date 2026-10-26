"""A killed session's spend (core/agent_cli.SpendMeter; config agent_rates; core/agent.run_attempt).
1. the meter prices each request once on its last usage, adds the thinking tokens reported before it as output, and
   multiplies a request whose prompt is over long_tokens by long_x; a model with no rates gives None;
2. run_attempt records a session that ends with no result event (timeout, output cap, stop) at the meter's estimate,
   marked cost_estimated, and keeps the CLI's own total_cost_usd whenever there is one;
3. the wiring: config.json has haiku rates, and job.py adds the attempt's cost_usd to the worker's spend.
Run: python -W error tests/test_kill_cost.py (also from test_tooling.py).
"""
import json
import pathlib
import sys
import threading
import unittest
from unittest import mock

TOOL = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL / "tests"))
sys.path.insert(0, str(TOOL))
from test_sync import TMP  # noqa: E402,F401  (sets DECOMP_PROJECT when run alone)

from core import agent, agent_cli, paths  # noqa: E402

RATES = {"haiku": {"input": 1.0, "cache_read": 0.1, "cache_write_5m": 1.25, "cache_write_1h": 2.0, "output": 5.0,
                   "long_tokens": 100000, "long_x": 5}}


def assistant(mid, **usage):
    return {"type": "assistant", "message": {"id": mid, "usage": usage}}


def thinking(n):
    return {"type": "system", "subtype": "thinking_tokens", "estimated_tokens_delta": n}


INIT = {"type": "system", "subtype": "init", "model": "claude-haiku-5-5"}
STREAM = [INIT,
          assistant("a", input_tokens=1000, cache_creation_input_tokens=10000, output_tokens=10),
          assistant("a", input_tokens=1000, cache_creation_input_tokens=10000, output_tokens=200),  # a's last usage
          thinking(3000),
          assistant("b", input_tokens=10, cache_read_input_tokens=120000, output_tokens=100)]
# a: (1000 * 1 + 10000 * 1.25 + 200 * 5) / 1e6 = 0.0145; b: (10 * 1 + 120000 * 0.1 + (100 + 3000) * 5) / 1e6 x 5
WANT = round(0.0145 + (10 + 12000 + 15500) / 1e6 * 5, 6)


class SpendMeter(unittest.TestCase):
    def test_estimate(self):
        m = agent_cli.SpendMeter(RATES)
        for ev in STREAM:
            m.feed(ev)
        self.assertAlmostEqual(m.cost(), WANT, places=6)
        self.assertEqual(m.cost(), WANT)

    def test_1h_writes_and_trailing_thinking(self):
        m = agent_cli.SpendMeter(RATES)
        for ev in (INIT, assistant("a", cache_creation_input_tokens=1000,
                                   cache_creation={"ephemeral_1h_input_tokens": 1000}), thinking(100)):
            m.feed(ev)
        self.assertEqual(m.cost(), round((1000 * 2.0 + 100 * 5) / 1e6, 6))  # thinking after the last request: its own

    def test_unknown_model(self):
        m = agent_cli.SpendMeter(RATES)
        m.feed(assistant("a", input_tokens=1000))
        self.assertEqual(m.cost("haiku"), round(1000 / 1e6, 6))  # no init event: the configured model name
        self.assertIsNone(m.cost("sonnet"))
        self.assertIsNone(agent_cli.SpendMeter(None).cost("haiku"))


class RunAttempt(unittest.TestCase):
    def run_with(self, events, reason):
        def run_bounded(cmd, timeout, cwd=None, env=None, stdin_text=None, on_line=None, stop_flag=None):
            for ev in events:
                on_line(json.dumps(ev) + "\n")
            return (None if reason else 0), "", reason
        cfg = {"agent_command": ["{python}", "-c", "pass"], "attempt_timeout_s": 5, "output_cap": None,
               "model": "haiku", "agent_rates": RATES}
        with mock.patch.object(paths, "run_bounded", run_bounded):
            return agent.run_attempt(cfg, TOOL, "task", TOOL / "tests" / ".work" / "kill_cost.jsonl",
                                     lambda m: None, threading.Event())

    def test_killed_session_is_estimated(self):
        got = self.run_with(STREAM, "timeout")
        self.assertEqual(got["cost_usd"], WANT)
        self.assertIs(got["cost_estimated"], True)

    def test_result_event_wins(self):
        got = self.run_with(STREAM + [{"type": "result", "subtype": "success", "total_cost_usd": 0.5}], None)
        self.assertEqual(got["cost_usd"], 0.5)
        self.assertNotIn("cost_estimated", got)

    def test_no_rates_unchanged(self):
        got = self.run_with([dict(INIT, model="claude-sonnet-5-5")] + STREAM[1:], "timeout")
        self.assertIsNone(got["cost_usd"])
        self.assertNotIn("cost_estimated", got)

    def test_wiring(self):
        cfg = json.loads((TOOL / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(set(cfg["agent_rates"]["haiku"]), set(RATES["haiku"]))
        src = (TOOL / "core" / "job.py").read_text(encoding="utf-8")
        self.assertIn('w.add_cost(att.get("cost_usd") or 0)', src)


if __name__ == "__main__":
    unittest.main()
