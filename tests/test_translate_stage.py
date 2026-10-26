"""The translator stage (core/translate_stage, docs/TRANSLATOR_STAGE.md) in core/job.investigate.

Once the harness exists and before agent attempt 1, the target's translator (target.json "translator", its folder in
config "translators") writes the function's C and the official judge decides it:
1. a PASS is a match with no agent run: attempt 0 model "translated", cost 0, ledger "source": "translated", the
   matched file is the translator's C unchanged, through the same finish (proof gates) as any match;
2. a FAIL starts agent attempt 1 from the translated C with its result and NOTE; the agent's match is "agent";
3. no C (exit status 3, a crash, a timeout): r["translator"] says why, attempt 1 starts from scratch; a stop asked
   for before it runs: no attempt; a translator that changes the harness files: a FAIL (tamper), as for an agent;
4. stage off (no entry, no folder, translate_first false): the job is exactly as before;
5. an import that fails with a better score than the translation still seeds attempt 1; one that passes wins;
6. targets/saturn_sh2/translate.py maps DP's inputs onto sh2-translate's API (here a stand-in package);
7. a FAIL is kept (r["translated_fail"]): the next attempt on the very same inputs takes it without translating or
   judging; a changed input, a PASS, or a translator of unknown revision keeps none.
The judge (check.run_check) is faked: the stage never changes it. The translator is a real subprocess (a stub).
Run: python -W error tests/test_translate_stage.py (also from test_tooling.py).
"""
import json
import pathlib
import subprocess
import sys
import textwrap
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import test_requeue_rejudge as base  # noqa: E402  (FakeO, FakeW, the faked judge and agent)
from test_requeue_rejudge import A, NEW_C, SEED_C, FakeO  # noqa: E402

from core import audit, check, job, paths, translate_stage  # noqa: E402

SHA256_FILE = paths.sha256_file  # test_requeue_rejudge's setUp fakes it

TX_C = "int f(int a) { return a + 3; }\n"
STUB = textwrap.dedent("""\
    import sys, time
    out, mode = sys.argv[1], sys.argv[2]
    if mode == "none":
        print("sh2-translate: unsupported instruction mac.l at 8c010004", file=sys.stderr)
        sys.exit(3)
    if mode == "crash":
        raise SystemExit(7)
    if mode == "slow":
        time.sleep(30)
    if mode == "tamper":
        open(sys.argv[3], "a", encoding="utf-8").write(" ")
    open(out, "w", encoding="utf-8").write(%r)
    """) % TX_C


class Stage(unittest.TestCase):
    """test_requeue_rejudge's fakes (its setUp, tearDown and put), without its tests."""
    mode = "ok"
    tearDown, put = base.Rejudge.tearDown, base.Rejudge.put

    def setUp(self):
        base.Rejudge.setUp(self)
        self.scores = []
        stub = self.root / "stub.py"
        stub.write_text(STUB, encoding="utf-8")
        self.tx = {"name": "stub-tx", "command": ["{python}", str(stub), "{out}", self.mode], "timeout_s": 20}
        run_check = check.run_check

        def scored(cfg, addr, work_file, sets, build_dir):
            res = run_check(cfg, addr, work_file, sets, build_dir)
            if self.scores and res["verdict"] == "FAIL":
                res["score"] = self.scores.pop(0)
            return res
        p = mock.patch.object(check, "run_check", scored)
        p.start()
        self.patches.append(p)

    def run_tx(self, *verdicts, cfg=None):
        self.verdicts = list(verdicts)
        self.o = FakeO()
        self.o.cfg.update({"translator": self.tx, "translators": {"stub-tx": str(self.root)},
                           "binary_path": self.root / "game.bin"}, **(cfg or {}))
        status = job.investigate(self.o, base.FakeW(), A)
        self.assertEqual(self.verdicts, [], "every faked verdict used")
        return status, self.o.ledger[A]


class Translated(Stage):
    def test_pass_is_a_match_with_no_agent(self):
        status, r = self.run_tx("PASS")
        self.assertEqual(status, "matched")
        self.assertEqual(self.judged, [TX_C])
        self.assertEqual(self.agent_calls, [])
        (att,) = r["attempts"]
        self.assertEqual((att["n"], att["model"], att["cost_usd"], att["translator"]), (0, "translated", 0.0, "stub-tx"))
        self.assertEqual(r["source"], "translated")
        self.assertTrue(r["verdict_detail"].startswith("PASS: translated by stub-tx"), r["verdict_detail"])
        self.assertEqual((self.root / "matched" / f"{A}.c").read_text(encoding="utf-8"), TX_C)
        self.assertEqual(self.proved, [A])
        self.assertEqual(r["cost_usd"], 0)
        self.assertTrue(r["translator"]["ok"])
        self.assertTrue((self.root / "runs" / A / "translated" / "check.json").is_file())

    def test_fail_seeds_attempt_1(self):
        status, r = self.run_tx("FAIL", "PASS")
        self.assertEqual(status, "matched")
        self.assertEqual(self.judged, [TX_C, NEW_C])
        (n, prev_work, text), = self.agent_calls
        self.assertEqual(n, 1)
        self.assertEqual(pathlib.Path(prev_work).read_text(encoding="utf-8"), TX_C)
        self.assertIn("written by the target's machine-code translator", text)
        self.assertIn("3 vectors differ", text)  # its official result and differences
        self.assertEqual([a["model"] for a in r["attempts"]], ["translated", "haiku"])
        self.assertEqual(r["source"], "agent")
        self.assertEqual(audit.last_attempt(r)["model"], "haiku")

    def test_failed_function_has_no_source(self):
        status, r = self.run_tx("FAIL", "FAIL", "FAIL", "FAIL")
        self.assertEqual(status, "failed")
        self.assertNotIn("source", r)

    def test_import_passing_wins(self):
        self.put("import", SEED_C)
        status, r = self.run_tx("PASS")
        self.assertEqual(self.judged, [SEED_C])
        self.assertEqual(r["source"], "import")

    def test_better_import_seeds_attempt_1(self):
        self.put("import", SEED_C)
        self.scores = [1, 40]  # the import fails on 1 vector, the translation on 40
        status, r = self.run_tx("FAIL", "FAIL", "PASS")
        self.assertEqual(self.judged, [SEED_C, TX_C, NEW_C])
        self.assertEqual(pathlib.Path(self.agent_calls[0][1]).read_text(encoding="utf-8"), SEED_C)

    def test_better_translation_seeds_attempt_1(self):
        self.put("import", SEED_C)
        self.scores = [40, 1]
        status, r = self.run_tx("FAIL", "FAIL", "PASS")
        self.assertEqual(pathlib.Path(self.agent_calls[0][1]).read_text(encoding="utf-8"), TX_C)

    def test_off(self):
        for cfg in ({"translate_first": False}, {"translators": {}}, {"translator": None}):
            self.judged, self.agent_calls = [], []
            with mock.patch.object(paths, "TARGET", self.root):  # no folder: none in config nor in the target
                status, r = self.run_tx("PASS", cfg=cfg)
            self.assertEqual(self.judged, [NEW_C], cfg)
            self.assertEqual(self.agent_calls, [(1, None, "")])
            self.assertNotIn("translator", r)


class NoTranslation(Stage):
    mode = "none"

    def test_agent_from_scratch(self):
        status, r = self.run_tx("PASS")
        self.assertEqual(self.agent_calls, [(1, None, "")])
        self.assertFalse(r["translator"]["ok"])
        self.assertIn("no translation: sh2-translate: unsupported instruction mac.l", r["translator"]["why"])
        self.assertEqual([a["model"] for a in r["attempts"]], ["haiku"])


class Crash(Stage):
    mode = "crash"

    def test_crash_is_no_translation(self):
        status, r = self.run_tx("PASS")
        self.assertIn("translator exited 7", r["translator"]["why"])
        self.assertEqual(self.agent_calls, [(1, None, "")])


class Slow(Stage):
    mode = "slow"

    def test_timeout_kills_it(self):
        self.tx["timeout_s"] = 1
        status, r = self.run_tx("PASS")
        self.assertEqual(r["translator"]["why"], "timeout after 1 s")
        self.assertLess(r["translator"]["translate_s"], 20)


class Tamper(Stage):
    mode = "tamper"

    def test_changed_harness_fails(self):  # as for an agent: a translator that changes the harness never passes
        self.tx["command"].append("{meta}")
        (paths.HARNESS / A).mkdir(parents=True, exist_ok=True)  # (the faked prepare writes no harness)
        real = lambda p: SHA256_FILE(p) if pathlib.Path(p).is_file() else "-"  # noqa: E731 (no harness here)
        with mock.patch.object(paths, "sha256_file", real):
            status, r = self.run_tx("PASS", "PASS")  # the judge's own verdict on the translation is PASS
        self.assertEqual((status, r["source"]), ("matched", "agent"))  # (the faked agent attempt passes)
        att = r["attempts"][0]
        self.assertEqual((att["model"], att["verdict"]), ("translated", "FAIL"))
        self.assertEqual(att["tamper"], "harness files changed while translating")


class Stopped(Stage):
    def test_stop_before_translating(self):
        o, w = FakeO(), base.FakeW()
        o.cfg.update({"translator": self.tx, "translators": {"stub-tx": str(self.root)},
                      "binary_path": self.root / "game.bin"})
        w.stop_flag.set()
        self.assertIsNone(translate_stage.attempt(o, w, A, job.judge))
        self.assertFalse((paths.RUNS / A / "translated").exists())


def vectors(text):
    (paths.HARNESS / A).mkdir(parents=True, exist_ok=True)
    (paths.HARNESS / A / "vectors.txt").write_text(text, encoding="utf-8")


class KeptFail(Stage):  # waste audit 2, row 23: a FAIL on the same inputs is not translated and judged again
    def attempts(self, *verdicts, rev="r1", change=None):
        g = mock.patch.dict("os.environ", {"GIT_CEILING_DIRECTORIES": str(self.root.parent)})  # tests/.work is
        g.start()  # inside DP's checkout: its git revision is not the stub translator's
        self.patches.append(g)
        if rev:
            (self.root / "REVISION").write_text(rev, encoding="utf-8")
        o, w = FakeO(), base.FakeW()
        o.cfg.update({"translator": self.tx, "translators": {"stub-tx": str(self.root)},
                      "binary_path": self.root / "game.bin"})
        vectors("1 2 3\n")
        self.verdicts = list(verdicts)
        first = translate_stage.attempt(o, w, A, job.judge)
        if change:
            change()
        second = translate_stage.attempt(o, w, A, job.judge)
        self.assertEqual(self.verdicts, [], "every faked verdict used")
        return o.rec(A), first, second

    def test_same_inputs_taken(self):
        r, first, second = self.attempts("FAIL")
        self.assertEqual(self.judged, [TX_C])  # judged once
        self.assertEqual({k: second[k] for k in ("verdict", "score", "summary", "work_file")},
                         {k: first[k] for k in ("verdict", "score", "summary", "work_file")})
        self.assertEqual(second["reused"], first["started"])
        self.assertEqual(r["translated_fail"]["rev"], "r1")

    def test_changed_input_judged(self):
        r, first, second = self.attempts("FAIL", "FAIL",
                                         change=lambda: vectors("1 2 4\n"))
        self.assertEqual(self.judged, [TX_C, TX_C])
        self.assertNotIn("reused", second)

    def test_unknown_revision_judged(self):
        r, first, second = self.attempts("FAIL", "FAIL", rev=None)
        self.assertEqual(len(self.judged), 2)

    def test_pass_keeps_none(self):
        r, first, second = self.attempts("PASS", "PASS")
        self.assertNotIn("translated_fail", r)
        self.assertEqual(len(self.judged), 2)

    def test_backlog_record_keeps_fail(self):
        src = (pathlib.Path(__file__).resolve().parents[1] / "tools" / "translate_backlog.py").read_text(
            encoding="utf-8")
        self.assertIn('o.rec(addr)["translated_fail"] = probe.row["translated_fail"]', src)


class Contract(unittest.TestCase):
    def test_placeholders(self):
        cfg = {"binary_path": pathlib.Path("g.bin")}
        cmd = translate_stage.command(cfg, ["{python}", "{target}/x.py", "{repo}", "{binary}", "{addr}", "{meta}",
                                            "{target_json}", "{bodies}", "{packet}", "{tool}", "{out}"], "R", A, "o.c")
        self.assertEqual(cmd[:5], [sys.executable, f"{paths.TARGET}/x.py", "R", "g.bin", A])
        self.assertEqual(cmd[5], str(paths.HARNESS / A / "meta.json"))
        self.assertNotIn("{", " ".join(cmd))

    def test_saturn_entry(self):
        tj = json.loads((paths.TARGETS / "saturn_sh2" / "target.json").read_text(encoding="utf-8"))
        self.assertEqual(tj["translator"]["name"], "sh2-translate")
        self.assertTrue((paths.TARGETS / "saturn_sh2" / "translate.py").is_file())
        self.assertIn("{target}/translate.py", tj["translator"]["command"])

    def test_sources(self):
        self.assertEqual([translate_stage.source_of({"model": m}) for m in ("translated", "import", "previous",
                                                                            "claude-haiku-5-5")],
                         ["translated", "import", "previous", "agent"])

    def test_revision_without_git(self):
        with mock.patch.object(translate_stage, "_REVS", {}), base.tempfile.TemporaryDirectory() as d:  # no repo
            self.assertEqual(translate_stage.revision(d), "unknown")
            (pathlib.Path(d) / "REVISION").write_text("abc123\n", encoding="utf-8")
            translate_stage._REVS.clear()
            self.assertEqual(translate_stage.revision(d), "abc123")


FAKE_SH2 = textwrap.dedent("""\
    class TranslateError(Exception): pass
    class TargetFacts:
        image_base = 0x06000000
        @classmethod
        def from_target_json(cls, tj): return cls()
    class Image:
        def __init__(self, data, base): self.data, self.base = data, base
    class Signature:
        @classmethod
        def from_meta(cls, meta): return meta["signature"]
    def translate_function(img, entry, sig, facts, body, entries=None):
        if entry == 0x06000010:
            raise TranslateError("mac.l at 06000012")
        return f"// {sig} {entry:08x} {body} {sorted(entries)} {len(img.data)}\\n"
    """)


class SaturnAdapter(unittest.TestCase):
    def test_adapter(self):
        with base.tempfile.TemporaryDirectory(dir=base.TMP) as d:
            d = pathlib.Path(d)
            (d / "sh2translate").mkdir()
            (d / "sh2translate" / "__init__.py").write_text("", encoding="utf-8")
            (d / "sh2translate" / "translate.py").write_text(FAKE_SH2, encoding="utf-8")
            (d / "bin").write_bytes(b"\0" * 64)
            (d / "meta.json").write_text(json.dumps({"signature": "void f(void)", "blocks": [["06000000", "06000003"]]}),
                                         encoding="utf-8")
            (d / "tj.json").write_text("{}", encoding="utf-8")
            (d / "bodies.txt").write_text("06000000 06000000 06000004\n06000010 06000010 06000020\n", encoding="utf-8")

            def run(addr):
                cmd = [sys.executable, str(paths.TARGETS / "saturn_sh2" / "translate.py"), "--repo", str(d), "--binary",
                       str(d / "bin"), "--addr", addr, "--meta", str(d / "meta.json"), "--target-json",
                       str(d / "tj.json"), "--bodies", str(d / "bodies.txt"), "--out", str(d / "out.c")]
                return subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            p = run("06000000")
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertEqual((d / "out.c").read_text(encoding="utf-8").strip(),
                             "// void f(void) 06000000 [(100663296, 100663299)] [100663296, 100663312] 64")
            p = run("06000010")
            self.assertEqual(p.returncode, translate_stage.NO_TRANSLATION)
            self.assertIn("mac.l at 06000012", p.stderr)


if __name__ == "__main__":
    unittest.main()
