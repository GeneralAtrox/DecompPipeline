"""The warm agent check (core/warm_check, core/warm_client; row 24 of the second waste audit), with a stand-in tool:
1. the sandbox's check.py (core/agent.CHECK_LAUNCHER) gives the same output and exit code warm as cold, and the
   tool sees the same arguments, working folder, environment and sys.path[0];
2. a spare serves one check, then the next spare serves the next;
3. refused, check.py runs the check cold: a wrong token, a command that is not an agent check of this workspace,
   a project.json changed since the spare loaded it;
4. config "warm_check": false gives the session no warm check; closing a session ends its spare.
No compiler, Ghidra or workspace: the tool is a script in a temporary folder.
Run: python -W error tests/test_warm_check.py (also from test_tooling.py).
"""
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
from core import warm_check  # noqa: E402

FAKE = r'''import os, sys
print("argv", sys.argv[1:])
print("cwd", os.getcwd())
print("env", os.environ.get("WC_TEST"))
print("path0", sys.path[0])
print("main", __name__)
sys.exit(3)
'''
LAUNCHER = None


def launcher(tool, project):
    global LAUNCHER
    if LAUNCHER is None:  # core/agent's own text, read from the file (importing core.agent needs a workspace)
        src = (TOOL / "core" / "agent.py").read_text(encoding="utf-8")
        start = src.index('CHECK_LAUNCHER = """') + len('CHECK_LAUNCHER = """')
        LAUNCHER = src[start:src.index('"""', start)]
    return LAUNCHER.format(tool=tool, project=project, addr="8c010080", work="work.c")


class Warm(unittest.TestCase):
    def setUp(self):
        self.d = pathlib.Path(tempfile.mkdtemp(prefix="wc_"))
        self.tools = self.d / "tools"
        (self.tools / "core").mkdir(parents=True)
        (self.tools / "decomp.py").write_text(FAKE, encoding="utf-8")
        shutil.copyfile(TOOL / "core" / "warm_client.py", self.tools / "core" / "warm_client.py")
        self.ws = self.d / "ws"
        self.ws.mkdir()
        (self.ws / "project.json").write_text("{}", encoding="utf-8")
        self.box = self.d / "box"
        self.box.mkdir()
        (self.box / "check.py").write_text(launcher(self.tools / "decomp.py", self.ws), encoding="utf-8")
        self.env = {k: v for k, v in os.environ.items() if k != "DP_WARM_CHECK"}
        self.env["WC_TEST"] = "seen"

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def check(self, env):
        p = subprocess.run([sys.executable, "check.py"], cwd=self.box, env=env, capture_output=True, timeout=120)
        return p.returncode, p.stdout

    def session(self):
        s = warm_check.Session(self.env, self.box, self.ws, tool_dir=self.tools)
        self.addCleanup(s.close)
        self.assertTrue(s.ready.wait(60))
        return s

    def test_same_as_cold(self):
        cold = self.check(self.env)
        self.assertEqual(cold[0], 3)
        out = cold[1].decode()
        self.assertIn("'--agent'", out)
        self.assertIn(f"cwd {self.box}", out)
        self.assertIn("env seen", out)
        self.assertIn(f"path0 {self.tools}", out)
        self.assertIn("main __main__", out)
        s = self.session()
        for n in (1, 2):  # one spare per check: the second is the next spare
            self.assertEqual(self.check({**self.env, **s.env_vars()}), cold)
            self.assertEqual((s.served, s.refused), (n, 0))
            self.assertTrue(s.ready.wait(60))

    def test_refused_runs_cold(self):
        cold = self.check(self.env)
        s = self.session()
        bad = {**self.env, "DP_WARM_CHECK": f"{s.port}:{'0' * 32}"}
        self.assertEqual(self.check(bad), cold)
        self.assertEqual((s.served, s.refused), (0, 1))
        other = self.d / "other"
        other.mkdir()
        (other / "project.json").write_text("{}", encoding="utf-8")
        (self.box / "check.py").write_text(launcher(self.tools / "decomp.py", other), encoding="utf-8")
        self.assertEqual(self.check({**self.env, **s.env_vars()})[0], 3)  # another workspace: cold
        self.assertEqual(s.refused, 2)
        (self.box / "check.py").write_text(launcher(self.tools / "decomp.py", self.ws), encoding="utf-8")
        self.assertTrue(s.allowed({"token": s.token, "argv": ["py", str(self.tools / "decomp.py"), "--project",
                                                               str(self.ws), "check", "--agent"],
                                   "env": {}, "cwd": "."}))
        for argv in (["py", str(self.tools / "decomp.py"), "--project", str(self.ws), "check"],  # no --agent
                     ["py", str(self.d / "decomp.py"), "--project", str(self.ws), "check", "--agent"],  # elsewhere
                     ["py", str(self.tools / "core" / "warm_client.py"), "--project", str(self.ws), "--agent"]):
            self.assertFalse(s.allowed({"token": s.token, "argv": argv, "env": {}, "cwd": "."}), argv)

    def test_project_changed(self):  # core/paths reads project.json when imported: a changed one is not served warm
        cold = self.check(self.env)
        s = self.session()
        (self.ws / "project.json").write_text(json.dumps({"changed": True}), encoding="utf-8")
        self.assertEqual(self.check({**self.env, **s.env_vars()}), cold)
        self.assertEqual((s.served, s.refused), (0, 1))
        self.assertTrue(s.ready.wait(60))  # the next spare loads the new one
        self.assertEqual(self.check({**self.env, **s.env_vars()}), cold)
        self.assertEqual(s.served, 1)

    def test_off_and_close(self):
        with warm_check.session({"warm_check": False}, self.env, self.box, self.ws) as extra:
            self.assertEqual(extra, {})
        s = warm_check.Session(self.env, self.box, self.ws, tool_dir=self.tools)
        self.assertTrue(s.ready.wait(60))
        proc = s.proc
        s.close()
        self.assertIsNotNone(proc.poll())
        self.assertFalse(s.dir.exists())
        self.assertEqual(self.check({**self.env, **s.env_vars()})[0], 3)  # closed: check.py runs it cold


if __name__ == "__main__":
    unittest.main()
