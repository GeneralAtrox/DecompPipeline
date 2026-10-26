"""core/verdict_memo: the wave's step 5 takes step 4's PASS only on the very inputs it was judged on.
1. a recorded PASS is found again; any changed input (the matched C, a harness file, a new harness file, the packet,
   the fr0 listing, the config, the judge's code) gives another key, so the function is judged again;
2. a line cut short by a kill is skipped; a FAIL is never recorded (refine.harness_stale calls on_pass on PASS only);
3. oracle_fresh.judge_one with a hit does not run the judge, and with a miss (or without a memo: final_audit,
   fresh_oracle) runs it as before;
4. a draft cycle (draft_run.first: refine.harness_stale_kept, then refine.refine: raw_pass_kept) judges the raw C
   once when nothing changed between them, twice when anything did, and never takes a FAIL or a PASS twice;
5. the readable stage's kept raw PASSes (verdict_memo.RawPass, refine.harness_stale memo=): a PASS is taken by a
   later run (another RawPass on the same file) while nothing changed; a changed C judges again; a FAIL is never
   kept; with on_pass (reprepare_stale --verdicts) the judge always runs.
Run: python -W error tests/test_verdict_memo.py.
"""
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_sync import TMP  # noqa: E402  (sets DECOMP_PROJECT when run alone)

from core import check, oracle_fresh, paths, refine, ret_reg, verdict_memo  # noqa: E402

A = "8c0f1000"


def put(p, text):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


class Memo(unittest.TestCase):
    def setUp(self):
        put(paths.MATCHED / f"{A}.c", "u32 f(u32 x) { return x; }\n")
        for n in ("meta.json", "vectors.txt", "expected.txt"):
            put(paths.HARNESS / A / n, f'{{"addr": "{A}"}}' if n == "meta.json" else "1 2 3\n")
        put(paths.PACKETS / A / "asm.txt", "rts\n")
        self.addCleanup(lambda: [p.unlink() for p in (paths.HARNESS / A).iterdir()])
        self.file = pathlib.Path(tempfile.mkdtemp(dir=TMP)) / "verdicts.jsonl"
        self.src = paths.MATCHED / f"{A}.c"

    def recorded(self):
        verdict_memo.Memo(self.file).record(A, self.src, 96)
        return verdict_memo.Memo(self.file)

    def test_hit(self):
        m = self.recorded()
        self.assertEqual(m.passed(A, self.src), 96)
        self.assertEqual(m.hits, 1)
        self.assertIsNone(m.passed("8c0f2000", self.src))  # another function

    def test_changed_inputs(self):
        changes = [
            lambda: put(self.src, "u32 f(u32 x) { return x + 0; }\n"),
            lambda: put(paths.HARNESS / A / "vectors.txt", "1 2 4\n"),
            lambda: put(paths.HARNESS / A / "oracle_summary.json", "{}"),  # a harness file more
            lambda: put(paths.PACKETS / A / "asm.txt", "rts\nnop\n"),
            lambda: put(paths.STATE / ret_reg.LIST, f"{A}\n"),
        ]
        for change in changes:
            self.setUp()
            (paths.STATE / ret_reg.LIST).unlink(missing_ok=True)
            m = self.recorded()
            change()
            self.assertIsNone(verdict_memo.Memo(self.file).passed(A, self.src))
            self.assertEqual(m.hits, 0)
        (paths.STATE / ret_reg.LIST).unlink(missing_ok=True)

    def test_judge_and_config(self):
        self.recorded()
        cfg = paths.config()
        with mock.patch.object(paths, "config", lambda: {**cfg, "check_timeout_s": 1}):
            self.assertIsNone(verdict_memo.Memo(self.file).passed(A, self.src))
        real = verdict_memo._code
        with mock.patch.object(verdict_memo, "_code", lambda h, label, files: (
                h.update(b"edited") if label == "core" else None, real(h, label, files))):
            self.assertIsNone(verdict_memo.Memo(self.file).passed(A, self.src))
        self.assertEqual(verdict_memo.Memo(self.file).passed(A, self.src), 96)  # the cfg a run mutates is not read

    def test_target_files(self):  # an edited target file judges again; its translator's files never count
        tgt = pathlib.Path(tempfile.mkdtemp(dir=TMP))
        for n in ("target.json", "hooks.py", "runtime/tc.h", "translator/out/8c010000.c", "translator/x/t.py",
                  "runtime/__pycache__/a.pyc"):
            put(tgt / n, "{}" if n == "target.json" else n)
        with mock.patch.object(paths, "TARGET", tgt):
            self.assertEqual([p.relative_to(tgt).as_posix() for p in verdict_memo._target_files()],
                             ["hooks.py", "runtime/tc.h", "target.json"])
            stamp = verdict_memo.judge_stamp()
            put(tgt / "translator/out/8c010000.c", "other C")
            self.assertEqual(verdict_memo.judge_stamp(), stamp)
            h = tgt / "runtime/tc.h"
            st = h.stat()
            put(h, "runtime/tc.X")  # the same size, a later mtime: read again
            os.utime(h, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
            self.assertNotEqual(verdict_memo.judge_stamp(), stamp)
            put(h, "runtime/tc.h")
            self.assertEqual(verdict_memo.judge_stamp(), stamp)

    def test_stamp_bytes_kept(self):  # _code hashes what _files did: memo files written before stay valid
        import hashlib
        files = sorted((paths.TOOL / "core").glob("ret_reg.py"))
        a, b = hashlib.sha256(), hashlib.sha256()
        verdict_memo._files(a, "core", files)
        verdict_memo._code(b, "core", files)
        self.assertEqual(a.hexdigest(), b.hexdigest())

    def test_key_taken_before_judge(self):  # an input changed while the judge ran is judged again in step 5
        m = verdict_memo.Memo(self.file)
        before = m.key_of(A, self.src)
        put(paths.HARNESS / A / "vectors.txt", "1 2 4\n")  # a re-prepare lands mid-judge
        m.record(A, self.src, 96, key_before=before)
        self.assertIsNone(verdict_memo.Memo(self.file).passed(A, self.src))
        m.record(A, self.src, 96)  # without key_before: the key of the inputs now
        self.assertEqual(verdict_memo.Memo(self.file).passed(A, self.src), 96)

    def test_reprepare_keys_before(self):  # tools/reprepare_stale's judge takes the key before it judges
        text = (paths.TOOL / "tools" / "reprepare_stale.py").read_text(encoding="utf-8")
        self.assertIn("before = memo.key_of(x, src)", text)
        self.assertIn("key_before=before", text)

    def test_cut_line(self):
        self.recorded()
        with open(self.file, "a", encoding="utf-8") as f:
            f.write('{"addr": "8c0f1000", "ke')
        self.assertEqual(verdict_memo.Memo(self.file).passed(A, self.src), 96)

    def test_harness_stale_records_pass_only(self):
        got = []
        for verdict, n in (("PASS", 1), ("FAIL", 1)):
            res = {"verdict": verdict, "stage": "run", "failing": 0, "total": 96, "build": [], "run_error": None}
            with mock.patch.object(check, "run_check", return_value=res):
                refine.harness_stale({}, A, on_pass=got.append)
            self.assertEqual(len(got), n)

    def test_judge_one(self):
        calls = []
        res = {"verdict": "PASS", "total": 96}
        with mock.patch.object(check, "run_check", side_effect=lambda *a, **k: calls.append(a[1]) or res):
            m = self.recorded()
            self.assertEqual(oracle_fresh.judge_one({}, A, TMP, m), ("PASS", "96 vectors"))
            self.assertEqual(calls, [])  # taken from the memo
            self.assertEqual(oracle_fresh.judge_one({}, A, TMP), ("PASS", "96 vectors"))
            self.assertEqual(calls, [A])  # no memo (final_audit, fresh_oracle): judged
            put(self.src, "u32 f(u32 x) { return x | 0; }\n")
            self.assertEqual(oracle_fresh.judge_one({}, A, TMP, verdict_memo.Memo(self.file)), ("PASS", "96 vectors"))
            self.assertEqual(calls, [A, A])  # changed C: judged again


class DraftCycle(unittest.TestCase):  # refine.harness_stale_kept / raw_pass_kept (finding R2)
    def setUp(self):
        Memo.setUp(self)
        memo = verdict_memo.RawPass(self.file.with_name("raw_pass.jsonl"))  # each test its own kept PASSes
        patcher = mock.patch.object(refine, "raw_memo", lambda: memo)
        patcher.start()
        self.addCleanup(patcher.stop)

    def judge(self, verdict="PASS"):
        calls = []
        res = {"verdict": verdict, "stage": "run", "failing": 0, "total": 96, "build": [], "run_error": None}
        return calls, mock.patch.object(check, "run_check", side_effect=lambda *a, **k: calls.append(a[1]) or res)

    def second(self):  # refine.refine's check
        return None if refine.raw_pass_kept(A) else refine.harness_stale({}, A)

    def test_same_inputs_once(self):
        calls, p = self.judge()
        with p:
            self.assertIsNone(refine.harness_stale_kept({}, A))
            self.assertIsNone(self.second())
            self.assertEqual(calls, [A])
            self.assertIsNone(self.second())  # taken once: the next cycle judges
            self.assertEqual(calls, [A, A])

    def test_changed_inputs_judged(self):
        changes = [lambda: put(self.src, "u32 f(u32 x) { return x | 0; }\n"),
                   lambda: put(paths.HARNESS / A / "vectors.txt", "1 2 4\n"),
                   lambda: put(paths.PACKETS / A / "asm.txt", "rts\nnop\n")]
        for change in changes:
            self.setUp()
            calls, p = self.judge()
            with p:
                refine.harness_stale_kept({}, A)
                change()
                self.second()
            self.assertEqual(calls, [A, A])

    def test_fail_not_kept(self):
        calls, p = self.judge("FAIL")
        with p:
            self.assertIsNotNone(refine.harness_stale_kept({}, A))
            self.assertIsNotNone(self.second())
        self.assertEqual(calls, [A, A])

    def test_cycle_wiring(self):  # the draft keeps its PASS, refine takes it
        core = pathlib.Path(refine.__file__).parent
        self.assertIn("refine.harness_stale_kept(cfg, addr)", (core / "draft_run.py").read_text(encoding="utf-8"))
        self.assertIn("stale = None if raw_pass_kept(addr) else harness_stale(cfg, addr, memo=raw_memo())",
                      (core / "refine.py").read_text(encoding="utf-8"))


class RawPassKept(unittest.TestCase):  # verdict_memo.RawPass through refine.harness_stale (audit 2, row 22)
    setUp = Memo.setUp
    judge = DraftCycle.judge

    def memo(self):
        return verdict_memo.RawPass(self.file.with_name("raw_pass.jsonl"))

    def test_kept_across_runs(self):
        calls, p = self.judge()
        with p:
            self.assertIsNone(refine.harness_stale({}, A, memo=self.memo()))
            self.assertIsNone(refine.harness_stale({}, A, memo=self.memo()))  # a later run, the same inputs
        self.assertEqual(calls, [A])

    def test_changed_c_judged(self):
        calls, p = self.judge()
        with p:
            refine.harness_stale({}, A, memo=self.memo())
            put(self.src, "u32 f(u32 x) { return x | 0; }\n")
            refine.harness_stale({}, A, memo=self.memo())
        self.assertEqual(calls, [A, A])

    def test_fail_not_kept(self):
        calls, p = self.judge("FAIL")
        with p:
            self.assertIsNotNone(refine.harness_stale({}, A, memo=self.memo()))
            self.assertIsNotNone(refine.harness_stale({}, A, memo=self.memo()))
        self.assertEqual(calls, [A, A])

    def test_on_pass_judges(self):
        calls, p = self.judge()
        got = []
        with p:
            refine.harness_stale({}, A, memo=self.memo())
            refine.harness_stale({}, A, on_pass=got.append, memo=self.memo())
        self.assertEqual((calls, len(got)), ([A, A], 1))


if __name__ == "__main__":
    unittest.main(verbosity=1)
