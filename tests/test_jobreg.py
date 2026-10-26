"""tools/jobreg.py (CLAUDE.md "No redundant work"), offline in a temp registry:
1. the classifier: heavy jobs found in a command line (inside ssh, powershell -Command, bash -c, bounded.py, timeout,
   wrapper scripts, a script file or here-document the command runs), and light commands left alone (git, ls, cat,
   grep, a dry run, a help text, one function, a targeted test, a here-document only written);
2. the key: limits split from the inputs, --changed joins the key, a function list by content;
3. the decision: a finished job is reused, one that failed or timed out with the same limits is refused, other
   limits or a named cross-check run; a running job (its claim held) is refused;
4. run end to end with a trivial command: start/end records, reuse, refuse, cross-check, the wrapper's own timeout;
5. the hook's reply: warn (the command runs, the agent is told), block (denied), exempt, and never an error.
Run: python tests/test_jobreg.py
"""
import argparse
import io
import json
import os
import pathlib
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

TOOL = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(TOOL / "tools"), str(TOOL)]
import jobreg  # noqa: E402
from core import filelock  # noqa: E402

PY = sys.executable


def kinds(cmd, read=None):
    return [(k, h) for k, h, _ in jobreg.classify(cmd, read)]


def args(**kw):
    base = dict(why="test", cross_check=None, changed=None, commit="c0ffee", stamp=None, fn_list=None, input=None,
                result=None, timeout=None, cmd=[])
    base.update(kw)
    return argparse.Namespace(**base)


class Classify(unittest.TestCase):
    def test_heavy(self):
        for cmd, want in [
            ("python decomp.py --project W prepare 8c010000", ("prepare", None)),
            ("py -3.12 tools/prepare_list.py --project W @l.txt --slots 2", ("prepare", None)),
            ("timeout 3600 python tools/bounded.py 3500 -- python tools/regress.py --projects tc --sample 3",
             ("regress gate", None)),
            ("python tools/mutation_audit.py run --ws tc=W --out r.json", ("mutation audit", None)),
            ("python tools/dp_judge.py --ws W --out o --all --jobs 4", ("rejudge", None)),
            ("python -m pytest -q", ("test suite", None)),
            ("python -m pytest -q tests/test_tooling.py", ("test suite", None)),
            ("python -m unittest test_tooling", ("test suite", None)),
            ("python tools/wave.py run --for 0", ("wave", None)),
            ("python decomp.py --project P divergence mission1", ("divergence run", None)),
            ("python x/ramguard.py 900 -- python -B tools/prepare_list.py --project W @l", ("prepare", None)),
            ("python -m cProfile -o p.out tools/prepare_list.py --project W @l --slots 1", ("prepare", None)),
            ('timeout 900 ssh -o BatchMode=yes laptop2 "cd /d C:\\dp && py -3.12 tools/regress.py --projects tc"',  # an example path
             ("regress gate", "laptop2")),
            ('ssh $Q laptop2 "set X=1&& cd /d C:\\dp && py -3.12 decomp.py --project W setup"',  # an example path
             ("prepare", "laptop2")),
            ('powershell -NoProfile -Command "Set-Location C:/dp; py -3.12 tools/oracle_probe.py --project W"',
             ("prepare", None)),
            ("bash -c 'python tools/final_audit.py --project W'", ("rejudge", None)),
            ("python tools/regress.py \\\n  --projects tc", ("regress gate", None)),
            ("ssh laptop2 \"powershell -Command -\" <<'EOF'\npy -3.12 tools/regress.py --projects tc\nEOF",
             ("regress gate", "laptop2")),
            ("S=/c/x; cat > $S/a.sh <<'EOF'\npython tools/regress.py --projects tc\nEOF\nbash $S/a.sh",
             ("regress gate", None)),
        ]:
            with self.subTest(cmd=cmd):
                self.assertIn(want, kinds(cmd))

    def test_light(self):
        for cmd in [
            "git log --oneline -5", "ls tools", "cat tools/regress.py | head", "grep -n prepare decomp.py",
            "python tools/wave.py dry", "python tools/prepare_list.py --project W @l --dry",
            "python -m pytest --version", "python refine.py --project P --help",
            "python -m pytest tests/test_wave.py -q", "python -m pytest -q tests/test_tooling.py -k Sc",
            "python tools/dp_judge.py --ws W --out o --only 8c010000",
            "python tools/dp_judge.py --ws W --out o --all --only 0601dbd0 --jobs 1",
            "python refine.py --project P verify --no-sync 8c09cebc", "DRY=1 timeout 300 bash saturn_driver.sh",
            "bash -n saturn_driver.sh", "py -3.12 tools/jobreg.py run --why x -- python tools/regress.py --projects tc",
            "cat > run.sh <<'EOF'\npython tools/regress.py --projects tc\nEOF",
            "python - <<'EOF'\nimport os\nos.system('python tools/regress.py')\nEOF",
            "echo python tools/regress.py --projects tc", 'python -c "print(1)"',
        ]:
            with self.subTest(cmd=cmd):
                self.assertEqual(kinds(cmd), [])

    def test_script_files(self):
        files = {"C:/w/run.sh": "cd /c/dp\ntimeout 9000 python tools/regress.py --projects tc\n",
                 "C:/w/job.ps1": "Set-Location C:/dp\npy -3.12 tools/dp_judge.py --ws W --out o --all\n",
                 "C:/w/light.sh": "git status\n"}
        read = files.get
        self.assertEqual(kinds("bash C:/w/run.sh", read), [("regress gate", None)])
        self.assertEqual(kinds("S=C:/w; sh \"$S/run.sh\" a b", read), [("regress gate", None)])
        self.assertEqual(kinds("cd C:/w && ./run.sh", read), [("regress gate", None)])
        self.assertEqual(kinds("pwsh -NoProfile -File C:/w/job.ps1", read), [("rejudge", None)])
        self.assertEqual(kinds('ssh laptop2 "powershell -Command -" < C:/w/job.ps1', read), [("rejudge", "laptop2")])
        self.assertEqual(kinds("bash C:/w/light.sh", read), [])
        self.assertEqual(kinds("bash $UNKNOWN/run.sh", read), [])  # a path it cannot resolve is not guessed
        self.assertEqual(kinds("cat C:/w/run.sh", read), [])  # read, not run

    def test_read_script_bounded(self):
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d, "a.sh")
            p.write_text("python tools/regress.py --projects tc\n")
            self.assertIn("regress", jobreg.read_script(str(p)))
            self.assertIsNone(jobreg.read_script(str(p), limit=4))
            self.assertIsNone(jobreg.read_script(str(pathlib.Path(d, "none.sh"))))


class Key(unittest.TestCase):
    def test_limits_split(self):
        cmd = "python tools/bounded.py {} python tools/regress.py --jobs {} --timeout 3400"
        a, la = jobreg._split_limits(cmd.format(900, 2).split())
        b, lb = jobreg._split_limits(cmd.format(1800, 4).split())
        self.assertEqual(a, b)
        self.assertNotEqual(la, lb)

    def test_key_and_inputs(self):
        with tempfile.TemporaryDirectory() as d:
            pathlib.Path(d, "l.txt").write_text("8c010000\n")
            cmd = ["python", "tools/prepare_list.py", "--project", "W", "@l.txt", "--slots", "2"]
            k1 = jobreg.job_key(cmd, args(), d)
            k2 = jobreg.job_key(cmd[:-1] + ["3"], args(), d)
            self.assertEqual(k1["inputs"], k2["inputs"])  # the limits decide only the key
            self.assertNotEqual(k1["key"], k2["key"])
            k3 = jobreg.job_key(cmd, args(changed="laptop2 workspace re-copied"), d)
            self.assertNotEqual(k1["inputs"], k3["inputs"])
            pathlib.Path(d, "l.txt").write_text("8c010000\n8c020000\n")
            self.assertNotEqual(k1["inputs"], jobreg.job_key(cmd, args(), d)["inputs"])  # the list by content

    def test_remote_commit_needed(self):
        with mock.patch.object(jobreg, "remote_commit", return_value=None):
            with self.assertRaises(ValueError):
                jobreg.job_key(["ssh", "laptop2", "py -3.12 tools/regress.py"], args(commit=None))


def rec(i, event, **kw):
    return dict({"event": event, "id": i}, **kw)


class Decide(unittest.TestCase):
    K = {"inputs": "I", "key": "K"}

    def test_reuse_refuse_run(self):
        done = [rec("1", "start", inputs="I", key="K0"), rec("1", "end", status="done")]
        self.assertEqual(jobreg.decide(done, self.K)[0], "done")  # other limits, same result
        for st in ("failed", "timeout"):
            bad = [rec("1", "start", inputs="I", key="K"), rec("1", "end", status=st)]
            self.assertEqual(jobreg.decide(bad, self.K)[0], "failed")
            self.assertEqual(jobreg.decide(bad, {"inputs": "I", "key": "K2"})[0], "run")  # new limits
            self.assertEqual(jobreg.decide(bad, self.K, cross_check="both machines")[0], "run")
        lost = [rec("1", "start", inputs="I", key="K"), rec("1", "end", status="lost")]
        self.assertEqual(jobreg.decide(lost, self.K)[0], "run")
        self.assertEqual(jobreg.decide(done, self.K, cross_check="byte-identical on laptop2")[0], "run")

    def test_running_claim(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(jobreg.held(d, "abc"))
            go, stop = threading.Event(), threading.Event()

            def hold():
                with filelock.claim(jobreg._claim_path(d, "abc"), owner={"purpose": "job x"}):
                    go.set()
                    stop.wait(20)
            t = threading.Thread(target=hold)
            t.start()
            try:
                self.assertTrue(go.wait(20))
                self.assertIsNotNone(jobreg.held(d, "abc"))
            finally:
                stop.set()
                t.join(20)
            self.assertIsNone(jobreg.held(d, "abc"))


class Run(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self.tmp.name)
        p = mock.patch.object(jobreg, "settings", return_value=(dict(jobreg.DEFAULTS), self.dir))
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)

    def run_job(self, code, dry=False, **kw):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err), mock.patch.object(sys.stdout, "buffer", io.BytesIO(),
                                                                            create=True):
            rc = jobreg.cmd_run(args(cmd=["--", PY, "-c", code], **kw), dry=dry)
        return rc, out.getvalue() + err.getvalue()

    def statuses(self):
        return [jobreg.status_of(j) for j in jobreg.jobs(jobreg.records(self.dir)).values()]

    def events(self):
        return [r["event"] for r in jobreg.records(self.dir)]

    def test_done_then_reused(self):
        self.assertEqual(self.run_job("print('hi')")[0], 0)
        self.assertEqual(self.statuses(), ["done"])
        self.assertIn("hi", (self.dir / "logs").glob("*.log").__next__().read_text())
        rc, text = self.run_job("print('hi')")
        self.assertEqual(rc, 0)
        self.assertIn("NOT RUN", text)
        self.assertEqual(self.events(), ["start", "end", "reuse"])
        rc, text = self.run_job("print('hi')", dry=True)
        self.assertEqual(self.events(), ["start", "end", "reuse"])  # check writes nothing

    def test_failed_refused_then_cross_check(self):
        self.assertEqual(self.run_job("import sys; sys.exit(5)")[0], 5)
        rc, text = self.run_job("import sys; sys.exit(5)")
        self.assertEqual(rc, jobreg.REFUSED)
        self.assertIn("REFUSED", text)
        self.assertEqual(self.run_job("import sys; sys.exit(5)", cross_check="the fix on laptop2")[0], 5)
        self.assertEqual(self.run_job("import sys; sys.exit(5)", changed="network fault fixed")[0], 5)
        self.assertEqual(self.statuses(), ["failed", "failed", "failed"])
        self.assertEqual(self.events().count("refuse"), 1)

    def test_wrapper_timeout(self):
        rc, _ = self.run_job("import time; time.sleep(60)", timeout=1)
        self.assertEqual((rc, self.statuses()), (124, ["timeout"]))
        self.assertEqual(self.run_job("import time; time.sleep(60)", timeout=1)[0], jobreg.REFUSED)

    def test_why_required(self):
        self.assertEqual(self.run_job("print(1)", why=None)[0], 2)
        self.assertEqual(self.events(), [])

    def test_running_refused(self):
        k = jobreg.job_key([PY, "-c", "print(1)"], args())
        go, stop = threading.Event(), threading.Event()

        def hold():
            with filelock.claim(jobreg._claim_path(self.dir, k["inputs"]), owner={"purpose": "job x"}):
                go.set()
                stop.wait(30)
        t = threading.Thread(target=hold)
        t.start()
        try:
            self.assertTrue(go.wait(20))
            rc, text = self.run_job("print(1)")
        finally:
            stop.set()
            t.join(20)
        self.assertEqual(rc, jobreg.REFUSED)
        self.assertIn("running now", text)


class Hook(unittest.TestCase):
    def payload(self, cmd, tool="Bash"):
        return {"tool_name": tool, "tool_input": {"command": cmd}, "cwd": str(TOOL)}

    def test_warn_block_exempt(self):
        heavy = "python tools/regress.py --projects tc"
        warn = json.loads(jobreg.hook_reply(self.payload(heavy), "warn", jobreg.DEFAULTS["hook_exempt"]))
        out = warn["hookSpecificOutput"]
        self.assertEqual(out["hookEventName"], "PreToolUse")
        self.assertNotIn("permissionDecision", out)
        self.assertIn("jobreg.py run", out["additionalContext"])
        block = json.loads(jobreg.hook_reply(self.payload(heavy, "PowerShell"), "block", []))
        self.assertEqual(block["hookSpecificOutput"]["permissionDecision"], "deny")
        ex = json.loads(jobreg.hook_reply(self.payload("py -3.12 recheck_driver.py --ws x"), "block",
                                          jobreg.DEFAULTS["hook_exempt"]))
        self.assertNotIn("permissionDecision", ex["hookSpecificOutput"])

    def test_quiet(self):
        self.assertEqual(jobreg.hook_reply(self.payload("git status"), "block", []), "")
        self.assertEqual(jobreg.hook_reply({"tool_name": "Read", "tool_input": {}}, "block", []), "")

    def test_names_registry_record(self):
        with tempfile.TemporaryDirectory() as d:
            jobreg._append(d, {"event": "start", "id": "j1", "command": "python tools/regress.py --projects tc"})
            jobreg._append(d, {"event": "end", "id": "j1", "status": "done"})
            out = jobreg.hook_reply(self.payload("python tools/regress.py --projects tc"), "warn", [], d)
            self.assertIn("j1 (done)", out)

    def test_never_fails(self):
        out = io.StringIO()
        with mock.patch.object(sys, "stdin", io.StringIO("{not json")), redirect_stdout(out):
            self.assertEqual(jobreg.main(["hook"]), 0)
        self.assertEqual(out.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
