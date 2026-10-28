"""Lint R9/fmac (raw C, core/check.run_check) and RR8 (readable C, core/readable_check): a file whose function's code
has the target's fused multiply-add (fmac) and which never calls the one-rounding function (sh4_fmac) fails lint
(core/fused; fmac-probe 2026-10-26: TC Spline_MeasureLength's `a*b + c` drifted the h5n1 camera). The rules only
add a failure: the checks that ask whether a harness or a mutant is judged right leave R9/fmac out (fused=False),
the do-nothing preflight stub is never held to RR8, and a target without SPEC.fused is unaffected.
Synthetic image and harness; the lint is the real one, nothing is built. Run: python -W error tests/test_fmac_lint.py
"""
import inspect
import json
import pathlib
import shutil
import struct
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_sync import TMP  # noqa: E402  (sets DECOMP_PROJECT when run alone)

from core import check, draft_run, frame_args, fused, gate, mutation, paths, readable_check, refine  # noqa: E402
from core.readable_lint import fused_lint  # noqa: E402
from core.rtx import source  # noqa: E402

A, B = "8c010000", "8c010010"
SIG = "f32 fn_{}(f32 a, f32 b, f32 c)"
TWICE = '#include "tc_rt.h"\n\nf32 fn_{}(f32 a, f32 b, f32 c)\n{{\n    return a * b + c; /* sh4_fmac( */\n}}\n'
ONCE = '#include "tc_rt.h"\n\nf32 fn_{}(f32 a, f32 b, f32 c)\n{{\n    return sh4_fmac(a, b, c);\n}}\n'


class FmacLint(unittest.TestCase):
    def setUp(self):
        self.d = pathlib.Path(tempfile.mkdtemp(prefix="fml", dir=TMP))
        self.addCleanup(shutil.rmtree, self.d, True)
        # A: fmac fr0,fr2,fr3 in its block, one more in the pool after it (not code); B: none
        words = [0xF32E, 0x000B, 0x0009, 0x0009, 0xF45E, 0, 0, 0, 0x000B, 0x0009]
        self.bin = self.d / "image.bin"
        self.bin.write_bytes(b"".join(struct.pack("<H", w) for w in words))
        self.cfg = dict(paths.config(), binary_path=self.bin)
        for addr, hi in ((A, "8c010007"), (B, "8c010013")):
            h = self.d / "harness" / addr
            h.mkdir(parents=True)
            (h / "meta.json").write_text(json.dumps({"addr": addr, "name": f"fn_{addr}", "signature": SIG.format(addr),
                                                     "blocks": [[addr, hi]], "callees": []}), encoding="utf-8")
        sh4, self.sat = source.spec("dreamcast_sh4"), source.spec("saturn_sh2")
        for p in (mock.patch.object(source, "spec", lambda target=None: sh4),
                  mock.patch.object(frame_args, "lint_frame_args", lambda *a: [])):
            p.start()
            self.addCleanup(p.stop)

    def meta(self, addr):
        return paths.read_json(self.d / "harness" / addr / "meta.json")

    def run_raw(self, addr, text, **kw):
        f = self.d / f"{addr}.c"
        f.write_text(text.format(addr), encoding="utf-8")
        return check.run_check(self.cfg, addr, str(f), build_dir=self.d / "b", harness_dir=self.d / "harness" / addr,
                               **kw)

    def test_raw_twice_fails_at_lint(self):
        res = self.run_raw(A, TWICE)
        self.assertEqual((res["verdict"], res["stage"]), ("FAIL", "lint"))
        self.assertEqual(len(res["lint"]), 1)
        self.assertTrue(res["lint"][0].startswith("R9/fmac: 1 fmac in the code, no sh4_fmac in the file."))
        self.assertIn("sh4_fmac(fr0, frm, frn)", res["lint"][0])

    def test_raw_once_and_no_fmac_pass_lint(self):
        """The rule never fails sh4_fmac C, nor C for code without fmac (they go on to the build, faked here)."""
        with mock.patch.object(check, "build", lambda *a, **k: (False, ["faked"])):
            for addr, text in ((A, ONCE), (B, TWICE)):
                res = self.run_raw(addr, text)
                self.assertEqual((res["stage"], res["lint"]), ("build", []), addr)

    def test_fused_false_leaves_rule_out(self):
        with mock.patch.object(check, "build", lambda *a, **k: (False, ["faked"])):
            self.assertEqual(self.run_raw(A, TWICE, fused=False)["stage"], "build")

    def test_target_without_fused_unaffected(self):
        with mock.patch.object(source, "spec", lambda target=None: None):
            self.assertEqual(fused.ops(self.cfg, self.meta(A)), 0)
            self.assertEqual(fused_lint(self.cfg, self.meta(A), TWICE.format(A)), [])
        with mock.patch.object(source, "spec", lambda target=None: self.sat):  # SH-2: no FPU
            self.assertEqual(fused_lint(self.cfg, self.meta(A), TWICE.format(A)), [])

    def test_readable_rr8(self):
        text = "f32 Lerp(f32 a, f32 b, f32 c)\n{\n    return a * b + c;\n}\n"
        got = fused_lint(self.cfg, self.meta(A), text)
        self.assertEqual(len(got), 1)
        self.assertTrue(got[0].startswith("RR8: 1 fmac in the code, no sh4_fmac in the file."))
        self.assertEqual(fused_lint(self.cfg, self.meta(A), text.replace("a * b + c", "sh4_fmac(a, b, c)")), [])
        self.assertEqual(fused_lint(self.cfg, self.meta(B), text), [])

    def test_draft_run_uses_the_same_test(self):
        """refine.py draft --fmac selects exactly what RR8 fails (one count, core/fused)."""
        self.assertEqual(draft_run.fused_ops(self.cfg, self.meta(A)), fused.ops(self.cfg, self.meta(A)))
        for text in (TWICE.format(A), ONCE.format(A)):
            self.assertEqual(bool(draft_run.fused_gap(self.cfg, A, self.meta(A), text)),
                             bool(fused_lint(self.cfg, self.meta(A), text)))

    def test_harness_stale_ignores_rule(self):
        """A raw file that rounds twice is not a stale harness: verify then marks the readable file stale (for
        draft --fmac --adopt and run --redo), never harness_stale (which both skip)."""
        seen = []

        def fake(cfg, addr, work, **kw):
            seen.append(kw.get("fused"))
            return {"verdict": "PASS", "stage": "compare", "total": 96}
        with mock.patch.object(refine, "raw_missing", lambda a: None), mock.patch.object(check, "run_check", fake):
            self.assertIsNone(refine.harness_stale(self.cfg, A))
        self.assertEqual(seen, [False])

    def test_oracle_checks_leave_rule_out(self):
        """The watcher gate's regression and the mutation audit judge the oracle and the comparison: unchanged."""
        self.assertEqual(inspect.getsource(gate.regression).count("fused=False"), 2)
        self.assertEqual(inspect.getsource(mutation).count("fused=False"), 1)

    def test_preflight_stub_not_held_to_rr8(self):
        self.assertIn("([] if stub else fused_lint(cfg, meta, src))", inspect.getsource(readable_check.check_readable))


class FiprLint(unittest.TestCase):
    """fipr and ftrv (rtx SPEC.once, fipr-once 2026-10-27): code with fipr must call sh4_fipr or sh4_fipr_fv, code with
    ftrv sh4_ftrv or sh4_ftrv_fv; float multiplies and adds round seven times where the SH-4 rounds once."""
    def setUp(self):
        self.d = pathlib.Path(tempfile.mkdtemp(prefix="fpl", dir=TMP))
        self.addCleanup(shutil.rmtree, self.d, True)
        # A: fipr fv0,fv4 and ftrv xmtrx,fv0; B: fsca, fschg, frchg (same high bits, none of them); C: fmac + fipr
        words = [0xF4ED, 0xF1FD, 0x000B, 0x0009, 0xF0FD, 0xF2FD, 0xF3FD, 0xFBFD, 0xF32E, 0xF4ED, 0x000B, 0x0009]
        self.bin = self.d / "image.bin"
        self.bin.write_bytes(b"".join(struct.pack("<H", w) for w in words))
        self.cfg = dict(paths.config(), binary_path=self.bin)
        self.metas = {a: {"blocks": [[a, hi]]} for a, hi in (("8c010000", "8c010007"), ("8c010008", "8c01000f"),
                                                             ("8c010010", "8c010017"))}
        sh4, self.sat = source.spec("dreamcast_sh4"), source.spec("saturn_sh2")
        p = mock.patch.object(source, "spec", lambda target=None: sh4)
        p.start()
        self.addCleanup(p.stop)

    def test_floats_fail_calls_pass(self):
        a, b, c = self.metas.values()
        floats = "f32 Dot(const f32 *a, const f32 *b)\n{\n    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2];\n}\n"
        got = fused_lint(self.cfg, a, floats)
        self.assertEqual([g.split(". ")[0] for g in got], ["RR8: 1 fipr in the code, no sh4_fipr or sh4_fipr_fv in the file",
                                                 "RR8: 1 ftrv in the code, no sh4_ftrv or sh4_ftrv_fv in the file"])
        self.assertIn("sh4_fipr(a, b)", got[0])
        self.assertIn("sh4_ftrv_fv(n)", got[1])
        self.assertEqual(fused_lint(self.cfg, a, "x = sh4_fipr(a, b); sh4_ftrv_fv(4);"), [])
        self.assertEqual(len(fused_lint(self.cfg, a, "x = sh4_fipr_fv(0, 4); /* sh4_ftrv(v) */")), 1)
        self.assertEqual(fused_lint(self.cfg, b, floats), [])  # fsca, fschg, frchg are neither
        self.assertEqual(fused.ops(self.cfg, c), 1)  # ops() still counts fmac alone
        both = fused_lint(self.cfg, c, floats)
        self.assertEqual([g.split(":")[1].strip()[:6] for g in both], ["1 fmac", "1 fipr"])
        self.assertEqual(fused.gap(self.cfg, c, "sh4_fmac(a, b, c);"),
                         "1 fipr in the code, no sh4_fipr or sh4_fipr_fv in the file")

    def test_saturn_unaffected(self):
        with mock.patch.object(source, "spec", lambda target=None: self.sat):  # SH-2: no FPU
            self.assertEqual(fused_lint(self.cfg, self.metas["8c010000"], "return a * b;"), [])


if __name__ == "__main__":
    unittest.main()
