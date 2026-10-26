"""The prompt cache's lifetime for agent sessions (config cache_ttl, readable_cache_ttl; core/agent_cli.agent_env).
1. agent_env sets CLAUDE_CODE_PROMPT_CACHE_TTL from cfg cache_ttl ("5m" or "1h"); null leaves the CLI's own choice;
   anything else is refused;
2. run_attempt starts the session with that environment;
3. the wiring: config.json has 5m for raw attempts and null for readable ones; refine's sessions take
   readable_cache_ttl in place of cache_ttl.
Run: python -W error tests/test_cache_ttl.py (also from test_tooling.py).
"""
import json
import os
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

VAR = "CLAUDE_CODE_PROMPT_CACHE_TTL"


class CacheTtl(unittest.TestCase):
    def setUp(self):
        env = {k: v for k, v in os.environ.items() if k != VAR}
        self.env = mock.patch.dict(os.environ, env, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_env(self):
        self.assertNotIn(VAR, agent_cli.agent_env())
        self.assertNotIn(VAR, agent_cli.agent_env({"cache_ttl": None}))
        self.assertEqual(agent_cli.agent_env({"cache_ttl": "5m"})[VAR], "5m")
        self.assertEqual(agent_cli.agent_env({"cache_ttl": "1h"})[VAR], "1h")
        self.assertEqual(agent_cli.agent_env({"cache_ttl": "5m"})["CLAUDE_CODE_DISABLE_CLAUDE_MDS"], "1")
        with self.assertRaises(ValueError):
            agent_cli.agent_env({"cache_ttl": "10m"})

    def test_session_gets_it(self):
        seen = []

        def run_bounded(cmd, timeout, cwd=None, env=None, stdin_text=None, on_line=None, stop_flag=None):
            seen.append(env.get(VAR))
            return 0, "", None
        cfg = {"agent_command": ["{python}", "-c", "pass"], "attempt_timeout_s": 5, "output_cap": None}
        with mock.patch.object(paths, "run_bounded", run_bounded):
            for ttl in ("5m", None):
                agent.run_attempt(dict(cfg, cache_ttl=ttl), TOOL, "task", TOOL / "tests" / ".work" / "t.jsonl",
                                  lambda m: None, threading.Event())
        self.assertEqual(seen, ["5m", None])

    def test_wiring(self):
        cfg = json.loads((TOOL / "config.json").read_text(encoding="utf-8"))
        self.assertEqual((cfg["cache_ttl"], cfg["readable_cache_ttl"]), ("5m", None))
        src = (TOOL / "core" / "refine.py").read_text(encoding="utf-8")
        self.assertIn('cache_ttl=cfg.get("readable_cache_ttl")', src)
        src = (TOOL / "core" / "agent.py").read_text(encoding="utf-8")  # the session env, plus the warm check's address
        self.assertIn("env = agent_env(cfg)", src)
        self.assertIn("env={**env, **warm}", src)


if __name__ == "__main__":
    unittest.main()
