"""Per-step prepare timing (core/prepare_timing, ghidra.prepare).
1. Clock marks Ghidra's milestones from its output lines (the first matching line wins) and the Python steps, and
   derives the phases (jvm, startup, open, script, exit, post, total);
2. finish appends one JSON line to state/prepare_timing.jsonl, copies it into the caller's dict, rotates the file
   past ROTATE and never raises;
3. ghidra.prepare, with Ghidra faked by a run_bounded that prints a real prepare's milestone lines and writes the
   harness: every mark is set in order, the caller's on_line still sees every line, the harness and packet are the
   bytes the fake wrote plus the usual post-steps (the same as without timing), and a failed prepare is recorded
   with ok false and still raises.
Run: python -W error tests/test_prepare_timing.py (also from test_tooling.py).
"""
import json
import pathlib
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from test_sync import TMP  # noqa: E402,F401  (sets DECOMP_PROJECT when run alone)

from core import ghidra, oracle_stamp, paths, prepare_timing  # noqa: E402

GHIDRA_LINES = [  # a real slot-0 prepare's milestone lines (TC 8c09d9dc, 2026-10-20), shortened
    "INFO  Using log config file: jar:file:/ghidra/Generic.jar!/generic.log4j.xml (LoggingInitialization)",
    "INFO  Class search complete (946 ms) (ClassSearcher)",
    "INFO  Headless startup complete (3489 ms) (AnalyzeHeadless)",
    "INFO  HEADLESS: execution starts (HeadlessAnalyzer)",
    "INFO  Opening existing project: /p/ghidra/fixture (HeadlessAnalyzer)",
    "INFO  REPORT: Processing read-only project file: /game.bin (HeadlessAnalyzer)",
    "INFO  REPORT: Execute script: TcPrepare.java '8c010000' 'p' 'h' '48' '48' '1' 'f' '-' '-' '-'  (HeadlessAnalyzer)",
    "INFO  SCRIPT: /p/state/ghidra_scripts/TcPrepare.java (HeadlessAnalyzer)",
    'INFO  TcPrepare.java> TcPrepare: {"dev_valid":48,"hidden_valid":48} (GhidraScript)',
    "INFO  REPORT: Save succeeded for processed file: /game.bin (HeadlessAnalyzer)",
]
STEPS = ["launch", "first_line", "startup", "project_open", "script_start", "script_done", "saved", "exit",
         "split_packet", "adopt", "sig_override", "stack_uninit", "oracle_stamp", "done"]


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=TMP)
        self.root = pathlib.Path(self.tmp.name)
        self.state = self.root / "state"
        self.p = mock.patch.object(paths, "STATE", self.state)
        self.p.start()

    def tearDown(self):
        self.p.stop()
        self.tmp.cleanup()

    def lines(self):
        f = self.state / "prepare_timing.jsonl"
        return [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines()] if f.exists() else []


class ClockTest(Case):
    def test_marks_and_phases(self):
        c = prepare_timing.Clock("8c010000", 1)
        seen = []
        watch = c.watch(seen.append)
        for line in GHIDRA_LINES + ["INFO  REPORT: Execute script: TcPrepare.java again"]:
            watch(line + "\n")
        self.assertEqual(len(seen), len(GHIDRA_LINES) + 1)  # the caller's on_line sees every line
        first = dict(c.marks)
        watch("INFO  Headless startup complete (1 ms) (AnalyzeHeadless)\n")
        self.assertEqual(c.marks, first)  # the first matching line wins
        for m in ("exit", "split_packet"):
            c.mark(m)
        timing = {"stale": 1}
        rec = c.finish(timing)
        self.assertEqual(timing, rec)
        self.assertEqual(list(rec["marks"]), ["launch", "first_line", "startup", "project_open", "script_start",
                                              "script_done", "saved", "exit", "split_packet", "done"])
        self.assertEqual(set(rec["phases"]), {"jvm", "startup", "open", "project", "program", "script", "exit", "post",
                                              "total"})
        self.assertTrue(all(v >= 0 for v in rec["phases"].values()))
        self.assertEqual((rec["addr"], rec["slot"], rec["staging"], rec["ok"]), ("8c010000", 1, False, True))
        self.assertEqual(self.lines(), [rec])
        self.assertRegex(prepare_timing.brief(rec), r"^\d+\.\d s \(jvm \d+\.\d, startup .*post \d+\.\d\)$")

    def test_missing_marks_and_rotation(self):
        c = prepare_timing.Clock("8c010000", 0, staging={"x": 1})
        rec = c.finish(ok=False)  # killed before any output: only launch and done
        self.assertEqual(rec["phases"], {"total": rec["phases"]["total"]})
        self.assertTrue(rec["staging"])
        self.assertFalse(rec["ok"])
        with mock.patch.object(prepare_timing, "ROTATE", 10):
            prepare_timing.Clock("8c010100", 0).finish()
        self.assertEqual([r["addr"] for r in self.lines()], ["8c010100"])
        old = self.state / "prepare_timing.jsonl.1"
        self.assertEqual(json.loads(old.read_text(encoding="utf-8"))["addr"], "8c010000")
        (self.root / "file").write_text("", encoding="utf-8")
        with mock.patch.object(prepare_timing.paths, "STATE", self.root / "file" / "state"):
            self.assertTrue(prepare_timing.Clock("8c010200", 0).finish()["ok"])  # unwritable: still a record


class Prepare(Case):
    META = {"addr": "8c010000", "signature": "void fn_8c010000(void)", "callees": []}

    def staging(self):
        st = {k: self.root / k for k in ("scripts", "packet", "harness", "callees")}
        for d in st.values():
            d.mkdir(parents=True, exist_ok=True)
        (st["scripts"] / "TcPrepare.java").write_text("class TcPrepare {}\n", encoding="utf-8")
        return dict(st, hints=None)

    def fake_run(self, fail=False):
        def run(cmd, timeout, env=None, on_line=None, stop_flag=None, **k):
            args = cmd[cmd.index("TcPrepare.java") + 1:]
            packet, harness = pathlib.Path(args[1]), pathlib.Path(args[2])
            out = []
            for line in GHIDRA_LINES:
                time.sleep(0.02)
                out.append(line + "\n")
                on_line(line + "\n")
            if fail:
                return 1, "".join(out), None
            packet.mkdir(parents=True, exist_ok=True)
            harness.mkdir(parents=True, exist_ok=True)
            (packet / "asm.txt").write_text("8c010000  000b  rts\n8c010002  0009  nop\n", encoding="utf-8")
            (packet / "ghidra.c").write_text("void fn_8c010000(void)\n{\n  return;\n}\n", encoding="utf-8")
            (harness / "meta.json").write_text(json.dumps(self.META), encoding="utf-8")
            (harness / "vectors.txt").write_bytes(b"V 0 dev\nE\n")
            (harness / "oracle_summary.json").write_text('{"dev_valid": 48}', encoding="utf-8")
            return 0, "".join(out), None
        return run

    def prepare(self, timing, fail=False):
        cfg = dict(paths.config(), ghidra_home=str(self.root), java_home=str(self.root), ghidra_timeout_s=60,
                   vectors_dev=48, vectors_hidden=48, ghidra_broker={"slots": 0})  # a fake Ghidra: no machine token
        seen = []
        with mock.patch.object(paths, "run_bounded", self.fake_run(fail)):
            res = ghidra.prepare(cfg, "8c010000", on_line=seen.append, slot_index=1, staging=self.staging(),
                                 timing=timing)
        self.assertEqual(len(seen), len(GHIDRA_LINES))
        return res

    def test_steps_in_order(self):
        timing = {}
        meta, summary = self.prepare(timing)
        self.assertEqual(summary, {"dev_valid": 48})
        self.assertEqual(list(timing["marks"]), STEPS)
        offsets = list(timing["marks"].values())
        self.assertEqual(offsets, sorted(offsets))
        self.assertGreater(timing["phases"]["script"], 0)
        self.assertEqual(self.lines(), [timing])
        # what prepare wrote is what it writes without timing: the fake's files plus the usual post-steps
        self.assertEqual((self.root / "harness" / "vectors.txt").read_bytes(), b"V 0 dev\nE\n")
        written = json.loads((self.root / "harness" / "meta.json").read_text(encoding="utf-8"))
        self.assertEqual(set(written) - set(self.META), {"uninit_stack", "oracle_stamp"})
        self.assertEqual(meta["oracle_stamp"], written["oracle_stamp"])
        # the bytes of the two writes it replaced: meta.json without the stamp, then oracle_stamp.record
        old = self.root / "old_meta.json"
        paths.write_json(old, {k: v for k, v in written.items() if k != "oracle_stamp"})
        oracle_stamp.record(old, written["oracle_stamp"])
        self.assertEqual(old.read_bytes(), (self.root / "harness" / "meta.json").read_bytes())

    def test_failure_recorded(self):
        timing = {}
        with self.assertRaises(RuntimeError):
            self.prepare(timing, fail=True)
        self.assertFalse(timing["ok"])
        self.assertIn("script_done", timing["marks"])
        self.assertIn("exit", timing["marks"])
        self.assertNotIn("split_packet", timing["marks"])
        self.assertEqual(self.lines(), [timing])


class KeepOld(Prepare):
    """4. A prepare in the workspace (not staging) writes under state/prepare_new/ and swaps in only when Ghidra
    succeeded: a failed or timed-out one (here after writing a meta-only harness, as TcPrepare does) keeps the old
    harness and packet byte for byte; a good one replaces both whole; neither leaves prepare_new/ behind; a rename
    that fails during the swap puts both old folders back."""
    test_steps_in_order = test_failure_recorded = None
    OLD = {"harness": {"meta.json": '{"addr": "8c010000"}', "vectors.txt": "old\n", "expected.txt": "old\n"},
           "packets": {"asm.txt": "old\n", "stale.txt": "x\n"}}

    def setUp(self):
        super().setUp()
        ws, scripts = self.root / "ws", self.staging()["scripts"]
        self.ps = [mock.patch.object(paths, n, ws / n.lower()) for n in ("HARNESS", "PACKETS", "HINTS")]
        self.ps.append(mock.patch.object(paths, "sync_scripts", lambda: scripts))
        for p in self.ps:
            p.start()
        for d, files in self.OLD.items():
            (ws / d / "8c010000").mkdir(parents=True)
            for n, text in files.items():
                (ws / d / "8c010000" / n).write_text(text, encoding="utf-8")

    def tearDown(self):
        for p in self.ps:
            p.stop()
        super().tearDown()

    def folders(self):
        return {d.parent.name: {f.name: f.read_text(encoding="utf-8") for f in d.iterdir()}
                for d in (paths.HARNESS / "8c010000", paths.PACKETS / "8c010000")}

    def run_prepare(self, run):
        cfg = dict(paths.config(), ghidra_home=str(self.root), java_home=str(self.root), ghidra_timeout_s=60,
                   vectors_dev=48, vectors_hidden=48, ghidra_broker={"slots": 0}, ghidra_serve={"enabled": False})
        with mock.patch.object(paths, "run_bounded", run):
            return ghidra.prepare(cfg, "8c010000", on_line=lambda line: None, slot_index=1)

    def test_failure_and_timeout_keep_old(self):
        good = self.fake_run()

        def timeout(cmd, *a, **k):  # TcPrepare's early meta.json, then the limit
            harness = pathlib.Path(cmd[cmd.index("TcPrepare.java") + 3])
            harness.mkdir(parents=True)
            (harness / "meta.json").write_text(json.dumps(self.META), encoding="utf-8")
            return None, "", "timed out after 60 s"
        for run in (self.fake_run(fail=True), timeout, lambda cmd, *a, **k: (good(cmd, *a, **k)[0], "", "killed")):
            with self.assertRaises(RuntimeError):
                self.run_prepare(run)
            self.assertEqual(self.folders(), self.OLD)
            self.assertFalse((paths.STATE / "prepare_new" / "8c010000.slot1").exists())

    def test_success_replaces_whole(self):
        meta, summary = self.run_prepare(self.fake_run())
        got = self.folders()
        self.assertEqual(set(got["harness"]), {"meta.json", "vectors.txt", "oracle_summary.json"})
        self.assertEqual(got["harness"]["vectors.txt"], "V 0 dev\nE\n")
        self.assertNotIn("stale.txt", got["packets"])
        self.assertEqual(json.loads(got["harness"]["meta.json"])["oracle_stamp"], meta["oracle_stamp"])
        self.assertEqual(summary, {"dev_valid": 48})
        self.assertFalse((paths.STATE / "prepare_new" / "8c010000.slot1").exists())

    def test_swap_rename_failure_restores(self):
        new = paths.STATE / "prepare_new" / "8c010000.slot1"
        pairs = [(new / "packet", paths.PACKETS / "8c010000"), (new / "harness", paths.HARNESS / "8c010000")]
        for n, _ in pairs:
            n.mkdir(parents=True)
            (n / "new.txt").write_text("new\n", encoding="utf-8")
        replace = ghidra.os.replace

        def flaky(src, dst):
            if pathlib.Path(src) == new / "harness":
                raise PermissionError("held open")
            return replace(src, dst)
        with mock.patch.object(ghidra.os, "replace", flaky), self.assertRaises(PermissionError):
            ghidra._swap_in(new, pairs)
        self.assertEqual(self.folders(), self.OLD)
        self.assertTrue(all((n / "new.txt").is_file() for n, _ in pairs))


if __name__ == "__main__":
    unittest.main()
