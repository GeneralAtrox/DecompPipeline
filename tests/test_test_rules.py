"""The two test rules (AGENTS.md "Test rules"), with planted faults: expected values come from original evidence, and a bug
fix's check fails on the unfixed build. port/tools/provenance.py, regress.py, tools/golden_survey.py, tools/fails_before.py.
Run: python -W error tests/test_test_rules.py
"""
import argparse
import importlib.util
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np
from PIL import Image

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "port" / "tools"), str(ROOT / "tools")]
import fails_before  # noqa: E402
import golden_survey  # noqa: E402
import provenance  # noqa: E402
_spec = importlib.util.spec_from_file_location("port_regress", ROOT / "port" / "tools" / "regress.py")  # tools/regress.py is another module
regress = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(regress)

# commits before the rule landed are not asked for a trailer
RULE_SINCE = "d40fb7ae"
WAIVED = ROOT / "tests" / "fails_before_waived.txt"


def picture(path, shade):
    a = np.full((480, 640, 3), 0, dtype=np.uint8)
    a[:, :, 0] = (np.arange(640) % 200 + shade).astype(np.uint8)
    pathlib.Path(path).parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(a).save(path)


class Game:
    """A game folder with one script of one frame, and a fake port that writes its frame in a given shade."""

    def __init__(self, root, shade=10):
        self.dir, self.shade = pathlib.Path(root), shade
        (self.dir / "tests").mkdir()
        self.script = self.dir / "tests" / "boot.script"
        self.script.write_text("seconds 1\nframes 5\n", encoding="utf-8")
        self.gold = self.dir / "tests" / "goldens" / "boot" / "frame_000005.png"
        self.golds = self.dir / "tests" / "goldens"

    def fake_run(self, cmd, seconds, cwd=None):
        out = pathlib.Path(cmd[cmd.index("--dump-dir") + 1])
        picture(out / "frame_000005.png", self.shade)
        pathlib.Path(cmd[cmd.index("--log") + 1]).write_text("STUB #1 fn\nEXIT: watchdog\n", encoding="utf-8")
        return regress.WATCHDOG_EXIT, ""

    def run(self, **flags):
        a = argparse.Namespace(build_dir=None, accept_log=flags.get("accept_log", False), accept_frames=flags.get("accept_frames", False))
        cfg = {"dir": self.dir, "disc": "x", "fsca_table": None}
        with mock.patch.object(regress, "run", self.fake_run), mock.patch("builtins.print") as said:
            ok = regress.run_script(cfg, self.script, a)
        return ok, "\n".join(str(c.args[0]) for c in said.call_args_list if c.args)

    def as_emulator(self):
        """The golden as an emulator capture, with its evidence recorded."""
        led = provenance.Ledger(self.golds)
        led.original("boot/frame_000005.png", "emulator-capture", self.gold)
        led.save()


class Provenance(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.game = Game(t.name)
        self.addCleanup(regress.COMPARED.update, dict(regress.COMPARED))
        self.key = "boot/frame_000005.png"

    def accepted(self):
        return self.game.run(accept_frames=True, accept_log=True)

    def test_accepting_labels_a_native_baseline_with_no_fidelity_claim(self):
        ok, _ = self.accepted()
        self.assertTrue(ok)
        led = provenance.Ledger(self.game.golds)
        self.assertEqual(led.rows[self.key]["source"], provenance.NATIVE)
        self.assertFalse(led.fidelity(self.key))
        self.assertEqual(provenance.Ledger(self.game.dir / "tests" / "baseline").rows["boot.txt"]["source"], provenance.NATIVE)

    def test_the_run_counts_what_it_compared_against(self):
        regress.COMPARED.update(original=0, native=0)
        self.accepted()
        self.assertEqual(regress.COMPARED, {"original": 0, "native": 1})
        self.game.as_emulator()
        self.game.run()
        self.assertEqual(regress.COMPARED, {"original": 1, "native": 1})

    def test_plant_accepting_over_original_evidence_fails_and_keeps_the_file(self):
        self.accepted()
        self.game.as_emulator()
        before = self.game.gold.read_bytes()
        self.game.shade = 40
        ok, said = self.game.run(accept_frames=True)
        self.assertFalse(ok)
        self.assertIn("may not replace it", said)
        self.assertEqual(self.game.gold.read_bytes(), before)

    def test_plant_a_port_frame_copied_over_original_evidence_fails(self):
        self.accepted()
        self.game.as_emulator()
        picture(self.game.gold, 12)  # close enough for frames.compare's rounding: only the record sees it
        ok, said = self.game.run()
        self.assertFalse(ok)
        self.assertIn("not the one recorded", said)
        self.assertFalse(provenance.Ledger(self.game.golds).fidelity(self.key))

    def test_plant_a_golden_with_no_record_fails(self):
        picture(self.game.gold, 10)
        ok, said = self.game.run(accept_log=True)
        self.assertFalse(ok)
        self.assertIn("no provenance record", said)

    def test_plant_a_log_baseline_with_no_record_fails(self):
        self.accepted()
        (self.game.dir / "tests" / "baseline" / provenance.FILE).unlink()
        ok, said = self.game.run()
        self.assertFalse(ok)
        self.assertIn("log baseline", said)

    def test_plant_port_output_labelled_as_original_is_refused(self):
        self.accepted()
        led = provenance.Ledger(self.game.golds)
        for port_file in ("build/regress/boot/frames/frame_000005.png", "build/x86_regress/boot/port.log",
                          "build/x86-agent_regress/boot/frames/frame_000005.png"):
            f = self.game.dir / port_file
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(b"x")
            with self.assertRaises(provenance.Refused, msg=port_file):
                led.original(self.key, "emulator-capture", f)

    def test_plant_a_hand_edited_record(self):
        self.accepted()
        led = provenance.Ledger(self.game.golds)
        led.rows[self.key] = {"source": "looks right to me", "evidence": "x"}
        self.assertIn("unknown source", led.problem(self.key))
        led.rows[self.key] = {"source": "emulator-capture"}
        self.assertIn("no evidence", led.problem(self.key))
        led.rows[self.key] = {"source": provenance.NATIVE}
        self.assertIn("no evidence", led.problem(self.key))

    def test_a_real_capture_is_accepted(self):
        self.accepted()
        capture = self.game.dir / "captures" / "emu_000005.png"
        picture(capture, 10)
        led = provenance.Ledger(self.game.golds)
        led.original(self.key, "emulator-capture", capture, "flycast")
        self.assertTrue(led.fidelity(self.key))


class DpGoldens(unittest.TestCase):
    def test_every_golden_in_dp_says_where_it_came_from(self):
        led = provenance.Ledger(ROOT, golden_survey.DP_LEDGER)
        bad = [(k, led.problem(k)) for k in golden_survey.dp_files() if led.problem(k)]
        self.assertEqual(bad, [], "tools/golden_survey.py --label-native --root . --ledger tests/goldens_provenance.json")

    def test_plant_a_new_golden_is_found(self):
        with tempfile.TemporaryDirectory() as t:
            root = pathlib.Path(t)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True, timeout=30)
            for n, text in (("targets/x/regress/new.json", "{}"), ("tests/test_a.py", "THE_GOLDEN = 1\n"), ("port/golden.py", "")):
                (root / n).parent.mkdir(parents=True, exist_ok=True)
                (root / n).write_text(text, encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=root, check=True, timeout=30)
            self.assertEqual(golden_survey.dp_files(root), ["targets/x/regress/new.json", "tests/test_a.py#THE_GOLDEN"])


class Outcome(unittest.TestCase):
    def test_what_counts_as_failing_on_the_bug(self):
        self.assertEqual(fails_before.outcome(0, "1 passed")[0], "passed")
        self.assertEqual(fails_before.outcome(1, "E   AssertionError: 1 != 2\n1 failed")[0], "failed")
        self.assertEqual(fails_before.outcome(None, "no result in 5 s")[0], "broken")
        self.assertEqual(fails_before.outcome(2, "ERROR tests/x.py\n1 error during collection")[0], "broken")
        self.assertEqual(fails_before.outcome(1, "E   AttributeError: module has no attribute 'new'\n1 failed")[0], "broken")
        self.assertEqual(fails_before.outcome(1, "AttributeError: no\nE   AssertionError: 1 != 2")[0], "failed")
        self.assertEqual(fails_before.outcome(5, "no tests ran")[0], "broken")


class FailsBefore(unittest.TestCase):
    """A little repo: lib.py has a bug at `base`, fixed in the working tree, and tests/check.py is the new check."""

    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.root = pathlib.Path(t.name)
        self.git("init", "-q", "-b", "master")
        self.git("config", "user.email", "t@t")
        self.git("config", "user.name", "t")
        self.write("lib.py", "def add(a, b):\n    return a - b\n")
        self.commit("start")
        (self.root / "tests").mkdir()

    def git(self, *a):
        return subprocess.run(["git", *a], cwd=self.root, check=True, capture_output=True, text=True, timeout=60).stdout

    def write(self, name, text):
        (self.root / name).write_text(text, encoding="utf-8")

    def commit(self, msg):
        self.git("add", ".")
        self.git("commit", "-q", "-m", msg)
        return self.git("rev-parse", "HEAD").strip()

    def prove(self, check, **kw):
        self.write("tests/check.py", check)
        cmd = [sys.executable, "tests/check.py"]
        a = argparse.Namespace(base=kw.get("base", "master"), overlay=[], unfixed_cmd=kw.get("unfixed_cmd"), seconds=60, cmd=cmd)
        with mock.patch("builtins.print") as said:
            rc = fails_before.prove(a, self.root)
        return rc, str(said.call_args.args[0])

    REAL = "import sys; sys.path.insert(0, '.'); import lib\nassert lib.add(2, 2) == 4, 'add is wrong'\n"

    def test_a_check_that_fails_before_and_passes_after_gives_the_trailer(self):
        self.write("lib.py", "def add(a, b):\n    return a + b\n")
        rc, said = self.prove(self.REAL)
        self.assertEqual(rc, 0, said)
        self.assertTrue(said.startswith(fails_before.TRAILER), said)
        self.assertIn("add is wrong", said)

    def test_plant_a_check_that_passes_on_the_unfixed_build_is_refused(self):
        self.write("lib.py", "def add(a, b):\n    return a + b\n")
        rc, said = self.prove("assert 1 + 1 == 2\n")
        self.assertEqual(rc, 1)
        self.assertIn("did not reproduce", said)

    def test_plant_a_check_that_only_misses_a_new_name_is_refused(self):
        self.write("lib.py", "def add(a, b):\n    return a + b\n\n\ndef added():\n    return 1\n")
        rc, said = self.prove("import sys; sys.path.insert(0, '.'); import lib\nassert lib.added() == 1\n")
        self.assertEqual(rc, 1)
        self.assertIn("never met the bug", said)

    def test_plant_a_check_that_fails_after_the_fix_too_is_refused(self):
        rc, said = self.prove(self.REAL)  # the working tree still has the bug
        self.assertEqual(rc, 1)
        self.assertIn("on the fixed build too", said)

    def test_a_port_check_names_the_unfixed_build_itself(self):
        self.write("lib.py", "def add(a, b):\n    return a + b\n")
        rc, said = self.prove(self.REAL, unfixed_cmd=f'"{sys.executable}" -c "raise SystemExit(1)"')
        self.assertEqual(rc, 0, said)
        rc, said = self.prove(self.REAL, unfixed_cmd=f'"{sys.executable}" -c "pass"')
        self.assertEqual(rc, 1)

    def test_check_finds_a_fix_commit_with_no_trailer(self):
        start = self.git("rev-parse", "HEAD").strip()
        self.write("lib.py", "def add(a, b):\n    return a + b\n")
        sha = self.commit("lib: fix add")
        bad = fails_before.problems(f"{start}..HEAD", self.root)
        self.assertEqual(len(bad), 1)
        self.assertIn(sha[:12], bad[0])

    def test_check_takes_a_trailer_a_reason_and_not_an_empty_reason(self):
        start = self.git("rev-parse", "HEAD").strip()
        for i, (msg, n) in enumerate((("fix add\n\nFails-before: abc x y => z", 0), ("fix add\n\nFails-before: n/a a timing race", 0),
                                      ("fix add\n\nFails-before: n/a", 1), ("land x: landing fix", 0), ("add a feature", 0))):
            self.write("lib.py", f"# {i}\n")
            self.commit(msg)
            self.assertEqual(len(fails_before.problems(f"{self.git('rev-parse', 'HEAD~1').strip()}..HEAD", self.root)), n, msg)
        self.assertEqual(len(fails_before.problems(f"{start}..HEAD", self.root)), 1)

    def test_plant_a_forged_trailer_fails_the_rerun(self):
        self.write("tests/check.py", "assert 1 + 1 == 2\n")
        self.commit("add a check")
        start = self.git("rev-parse", "HEAD").strip()
        self.write("lib.py", "def add(a, b):\n    return a + b\n")
        self.commit(f"fix add\n\nFails-before: abc {sys.executable.replace(chr(92), '/')} tests/check.py => add is wrong")
        self.assertEqual(fails_before.problems(f"{start}..HEAD", self.root), [])  # without --rerun it is only read
        bad = fails_before.problems(f"{start}..HEAD", self.root, rerun=True, seconds=60)
        self.assertEqual(len(bad), 1)
        self.assertIn("does not fail on the parent", bad[0])

    def test_a_true_trailer_passes_the_rerun(self):
        self.write("tests/check.py", self.REAL)
        start = self.git("rev-parse", "HEAD").strip()
        self.write("lib.py", "def add(a, b):\n    return a + b\n")
        self.commit(f"fix add\n\nFails-before: abc {sys.executable.replace(chr(92), '/')} tests/check.py => add is wrong")
        self.assertEqual(fails_before.problems(f"{start}..HEAD", self.root, rerun=True, seconds=60), [])


class Landing(unittest.TestCase):
    def test_every_fix_since_the_rule_says_how_it_failed_before(self):
        if subprocess.run(["git", "merge-base", "--is-ancestor", RULE_SINCE, "HEAD"], cwd=ROOT, timeout=30).returncode:
            self.skipTest("not on master's history")
        waived = {ln.split()[0] for ln in WAIVED.read_text(encoding="utf-8").splitlines() if ln.strip() and ln[0] != "#"}
        bad = [b for b in fails_before.problems(f"{RULE_SINCE}..HEAD", ROOT) if b.split()[0] not in waived]
        self.assertEqual(bad, [], f"reword before landing, or for a landed commit add `sha why` to {WAIVED.name}")


if __name__ == "__main__":
    unittest.main()
