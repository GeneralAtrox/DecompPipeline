"""The shared prompt prefix for raw attempts (config shared_prefix; core/agent.raw_system_prompt).
1. off (the default): TASK.md is the whole task as before, byte for byte, and the session keeps the lean system
   prompt on its command line;
2. on: the system prompt holds the generic task text (goal items, files, rules R1-R12, SH-4 reminders, procedure)
   and tc_rt.h, with no per-function value in it; TASK.md and the first message hold the per-function part;
3. run_attempt writes that system prompt beside the transcript and starts the CLI with --system-prompt-file (a
   prompt this long does not fit a Windows command line through claude.cmd);
4. a target with its own build_sandbox hook (its own TASK.md), or the CLI's own system prompt, is left alone;
   job.attempt passes the prompt; config.json turns it on.
Run: python -W error tests/test_shared_prefix.py (also from test_tooling.py).
"""
import json
import pathlib
import re
import sys
import tempfile
import threading
import unittest
from unittest import mock

TOOL = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL / "tests"))
sys.path.insert(0, str(TOOL))
from test_sync import TMP  # noqa: E402,F401  (sets DECOMP_PROJECT when run alone)

from core import agent, agent_cli, paths, target_hooks  # noqa: E402

ADDR = "8c0abcd0"
FIELDS = {"addr": ADDR, "box": "C:/box", "signature": f"u32 fn_{ADDR}(u32 param_1)", "ret_reg": "r0",
          "entry_sp": 0x8cffe000, "previous": "\n## Previous attempt (#1), which FAILED\n"}
# The task text before the split (master eff2ea6), reduced to its fixed opening and a few lines from each part.
OLD_LINES = ["# TASK: decompile fn_{addr} into C that behaves exactly like the original SH-4 code",
             "1. the same return value ({ret_reg}),", "## Files in this directory",
             "- R3. Define exactly one non-static function, fn_{addr}, with exactly the signature above. Every helper "
             "function must be `static`.",
             "- r15 (the stack pointer) is 0x{entry_sp:08x} at entry. The original's stack frame is guest memory just "
             "below it: an",
             "2. Write fn_{addr} in work.c by translating asm.txt block by block. Keep the original control flow and "
             "the original order of memory writes and calls."]


class SharedPrefix(unittest.TestCase):
    def test_off_is_the_old_task(self):
        task = agent.TASK.format(**FIELDS)
        for line in OLD_LINES:
            self.assertIn(line.format(**FIELDS), task.splitlines())
        self.assertTrue(task.endswith(FIELDS["previous"]))
        self.assertNotIn("system prompt", task)
        self.assertIsNone(agent.raw_system_prompt({}))
        self.assertIsNone(agent.raw_system_prompt({"shared_prefix": False}))
        self.assertFalse(agent.shared_prefix({}))

    def test_on_splits_the_task(self):
        cfg = {"shared_prefix": True}
        self.assertTrue(agent.shared_prefix(cfg))
        system = agent.raw_system_prompt(cfg)
        self.assertTrue(system.startswith(agent_cli.SYSTEM_PROMPT))
        self.assertEqual(system, agent.raw_system_prompt(dict(cfg, model="other")))  # the same for every session
        for n in range(1, 13):
            self.assertIn(f"- R{n}. ", system)
        for part in ("## Files in this directory", "## SH-4 reminders", "## Procedure", "2. the same final bytes",
                     "fn_ADDR, with exactly the signature the task gives", "0x8cffe000 at entry"):
            self.assertIn(part, system)
        self.assertIn((paths.RUNTIME / "tc_rt.h").read_text(encoding="utf-8"), system)
        self.assertIsNone(re.search(r"\{[a-z_]+(:[^}]*)?\}", system), "an unfilled placeholder")
        self.assertNotIn("fn_8c", system)
        self.assertNotIn("Working directory:", system)
        task = agent.TASK_SHARED.format(**FIELDS)
        for value in (f"fn_{ADDR}", f"0x{ADDR}", FIELDS["box"], FIELDS["signature"], "Its return value: r0."):
            self.assertIn(value, task)
        self.assertTrue(task.endswith(FIELDS["previous"]))
        self.assertNotIn("- R1. ", task)
        # nothing of the generic text is lost: every line of the old task's body is in the system prompt
        body = agent.TASK_BODY.format(fname="fn_ADDR", sig_where="the task gives", entry_sp=FIELDS["entry_sp"])
        old = agent.TASK.format(**FIELDS)
        for line in body.splitlines():
            self.assertIn(line, system)
            if "fn_ADDR" not in line and "the task gives" not in line:
                self.assertIn(line, old.splitlines())

    def test_cli_prompt_left_alone(self):
        self.assertFalse(agent.shared_prefix({"shared_prefix": True, "agent_system_prompt": "cli"}))
        self.assertTrue(agent.shared_prefix({"shared_prefix": True, "agent_system_prompt": "lean"}))

    def test_hooked_target_left_alone(self):
        with mock.patch.object(target_hooks, "get", lambda name: (lambda *a: None) if name == "build_sandbox" else None):
            self.assertFalse(agent.shared_prefix({"shared_prefix": True}))
            self.assertIsNone(agent.raw_system_prompt({"shared_prefix": True}))

    def test_session_command(self):
        seen = []

        def run_bounded(cmd, timeout, cwd=None, env=None, stdin_text=None, on_line=None, stop_flag=None):
            seen.append(cmd)
            return 0, "", None
        (TOOL / "tests" / ".work").mkdir(exist_ok=True)
        tmp = tempfile.TemporaryDirectory(prefix="shared_prefix_", dir=TOOL / "tests" / ".work")  # one per run
        self.addCleanup(tmp.cleanup)
        work = pathlib.Path(tmp.name)
        cfg = {"claude": sys.executable, "model": "m", "attempt_budget_usd": 1, "effort": None, "attempt_timeout_s": 5,
               "output_cap": None}
        with mock.patch.object(paths, "run_bounded", run_bounded):
            agent.run_attempt(cfg, TOOL, "task", work / "t.jsonl", lambda m: None, threading.Event())
            self.assertFalse((work / "system_prompt.md").exists())
            agent.run_attempt(cfg, TOOL, "task", work / "t.jsonl", lambda m: None, threading.Event(),
                              system="SYSTEM\nTEXT")
        lean, shared = seen
        self.assertEqual(lean[lean.index("--system-prompt") + 1], agent_cli.SYSTEM_PROMPT)
        self.assertNotIn("--system-prompt-file", lean)
        self.assertNotIn("--system-prompt", shared)
        f = pathlib.Path(shared[shared.index("--system-prompt-file") + 1])
        self.assertEqual(f, work / "system_prompt.md")
        self.assertEqual(f.read_bytes(), b"SYSTEM\nTEXT")

    def test_wiring(self):
        self.assertIs(json.loads((TOOL / "config.json").read_text(encoding="utf-8"))["shared_prefix"], True)
        src = (TOOL / "core" / "job.py").read_text(encoding="utf-8")
        self.assertIn("system=agent.raw_system_prompt(cfg)", src)
        self.assertIn("(TASK_SHARED if shared_prefix(cfg) else TASK).format(",
                      (TOOL / "core" / "agent.py").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
