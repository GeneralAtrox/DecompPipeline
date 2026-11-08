"""A readable attempt that fails lint is not retried by an agent (core/refine.lint_final), and the address numbers lint
refuses are named before and after the agent:
1. the real refine.refine (agent, sandbox and judge faked): a lint FAIL ends the function after one session, failed,
   with the lint in the note; a compare FAIL is still retried; an RR3 number that has a name is written as the name
   (core/raw_consts.name_literals) and judged again, and a pass there is recorded;
2. raw_consts.uses reads an address sign-extended to 64 bits (Xbox 360 raw.c `0xffffffff8215fbd8ull`) as its low
   32 bits, so core/global_infer names it before the run; other 16-digit numbers are not addresses;
3. name_literals: a function, a global, a global plus an offset, a sign-extended number keeps its 64 bits, an unnamed
   address and a number outside the ranges are left alone.
Run: python -W error tests/test_readable_lint_final.py
"""
import atexit
import json
import os
import pathlib
import shutil
import sys
import tempfile
import unittest
from unittest import mock

TOOL = pathlib.Path(__file__).resolve().parents[1]
(TOOL / "tests" / ".work").mkdir(parents=True, exist_ok=True)
_TMP = tempfile.TemporaryDirectory(prefix="lintfinal_", dir=TOOL / "tests" / ".work")
TMP = pathlib.Path(_TMP.name)
atexit.register(_TMP.cleanup)
if not (pathlib.Path(os.environ.get("DECOMP_PROJECT", "")) / "project.json").exists():  # alone: a project for paths
    (TMP / "project.json").write_text(json.dumps({"target": "dreamcast_sh4", "ghidra_name": "fixture",
                                                  "binary": "game.bin"}), encoding="utf-8")
    os.environ["DECOMP_PROJECT"] = str(TMP)
sys.path.insert(0, str(TOOL))

from core import paths, raw_consts, readable, refine  # noqa: E402

A = "8c010000"
CFG = {"model": "raw-model", "readable_model": "readable-model", "effort": None, "readable_effort": None,
       "attempt_budget_usd": 1.0, "readable_budget_usd": 5.0}
LINT = {"verdict": "FAIL", "stage": "lint", "lint": ["RR3: guest address 0x8c0d6478 written as a number; name it"]}
COMPARE = {"verdict": "FAIL", "stage": "compare", "failing": 3, "total": 48}


class Loop(unittest.TestCase):
    def setUp(self):
        self.d = pathlib.Path(tempfile.mkdtemp(dir=TMP))
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        (self.d / "box").mkdir()
        (self.d / "box" / "work.c").write_text("u32 f(void) { return 0x8c0d6478u; }", encoding="utf-8")
        (self.d / "src").mkdir()
        (self.d / "game.bin").write_bytes(b"\0" * 16)
        self.sessions, self.judged, self.verdicts = [], [], []

        def run_attempt(acfg, *a):
            self.sessions.append(acfg)
            return {}

        def check_readable(cfg, addr, src, *a, **k):
            self.judged.append(pathlib.Path(src).read_text(encoding="utf-8"))
            return dict(self.verdicts.pop(0))
        patches = [mock.patch.object(refine, "LEDGER", self.d / "readable.json"),
                   mock.patch.object(refine.readable, "SRC", self.d / "src"),
                   mock.patch.object(refine.readable, "Registry", lambda: None),
                   mock.patch.object(paths, "RUNS", self.d / "runs"),
                   mock.patch.object(paths, "BUILD", self.d / "build"),
                   mock.patch.object(refine, "raw_missing", lambda a: None),
                   mock.patch.object(refine, "harness_stale", lambda cfg, a, **k: None),
                   mock.patch.object(refine, "preflight", lambda cfg, reg, a, meta: None),
                   mock.patch.object(refine, "build_sandbox", lambda *a: (self.d / "box", "task")),
                   mock.patch.object(refine, "tree_hashes", lambda box: {}),
                   mock.patch.object(refine.global_extents, "for_process", lambda cfg, reg: []),
                   mock.patch.object(refine, "record_pass", lambda addr, h: refine.update(addr, status="readable")
                                     and "readable"),
                   mock.patch.object(refine.agent, "run_attempt", run_attempt),
                   mock.patch.object(refine.rcheck, "check_readable", check_readable)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def run_it(self, verdicts, named=None):
        self.verdicts = list(verdicts)
        fix = (lambda src, reg, ext, ranges: (src.replace("0x8c0d6478u", "((u32)(uintptr_t)&g_x)"), 1, [])) \
            if named else (lambda src, reg, ext, ranges: (src, 0, [0x8c0d6478]))
        with mock.patch.object(raw_consts, "name_literals", fix):
            out = refine.refine(dict(CFG, binary_path=self.d / "game.bin"), A, max_attempts=3, log=lambda m: None)
        return out, refine.load_ledger()[A]

    def test_lint_fail_is_final(self):
        out, row = self.run_it([LINT, LINT, LINT])
        self.assertEqual((out, row["status"], len(self.sessions), len(row["attempts"])), ("failed", "failed", 1, 1))
        self.assertIn("lint, final", row["note"])
        self.assertIn("RR3", row["note"])

    def test_compare_fail_still_retried(self):
        out, row = self.run_it([COMPARE, COMPARE, COMPARE])
        self.assertEqual((out, len(self.sessions)), ("failed", 3))

    def test_named_number_fixed_without_an_agent(self):
        out, row = self.run_it([LINT, {"verdict": "PASS", "stage": "compare"}], named=True)
        self.assertEqual((out, row["status"], len(self.sessions), row["lint_fix"]), ("readable", "readable", 1, 1))
        self.assertIn("&g_x", self.judged[1])
        self.assertIn("&g_x", (self.d / "src" / f"{A}.c").read_text(encoding="utf-8"))
        self.assertEqual(row["attempts"][0]["lint_fix"]["verdict"], "PASS")

    def test_named_number_that_fails_the_judge_is_failed(self):
        out, row = self.run_it([LINT, COMPARE], named=True)
        self.assertEqual((out, row["status"], len(self.sessions)), ("failed", "failed", 1))
        self.assertFalse((self.d / "src" / f"{A}.c").exists())
        self.assertEqual(row["attempts"][0]["lint_fix"]["stage"], "compare")


class Uses(unittest.TestCase):
    def test_sign_extended_word(self):
        t = ("a.r[0] = 0xffffffff8215fbd8ull; x = rd32(0x83190010u); y = 0xffffffffu; z = 0x0000000082000000ull;"
             " w = 0xFFFFFFFF83118888ULL; v = 0x7ff8000000000000ull;")
        got = {f"{k:08x}": v for k, v in raw_consts.uses(t, 0x1400000).items() if k >= raw_consts.IMAGE_BASE}
        if raw_consts.IMAGE_BASE == 0x82000000:  # the project's image base (the Xbox 360's in a Too Human workspace)
            self.assertEqual(got, {"8215fbd8": {"value"}, "83190010": {"access"}, "83118888": {"value"}})
        self.assertEqual(raw_consts.HEX.findall(t), ["8215fbd8", "83190010", "ffffffff", "83118888"])


class NameLiterals(unittest.TestCase):
    def test_names(self):
        reg = readable.Registry.empty({"functions": {"83118888": {"proto": "void Obj_Dtor(u32 p)"}},
                                       "globals": {"8215fbd8": {"decl": "be_u32 g_vtbl[4]"}}})
        src = ("x = 0x8215FBD8u; y = 0xffffffff8215fbdcULL; f(0x83118888); z = 0x82000004; w = 0x12345678;"
               " /* 0x8215fbd8 */")
        out, n, left = raw_consts.name_literals(src, reg, [(0x8215fbd8, 16)], [(0x82000000, 0x83400000)])
        self.assertEqual(n, 4)  # the comment's number too: a comment never reaches the judge
        self.assertIn("x = ((u32)(uintptr_t)&g_vtbl);", out)
        self.assertIn("y = ((u64)(s64)(s32)(u32)((uintptr_t)&g_vtbl + 0x4u));", out)
        self.assertIn("f(((u32)(uintptr_t)Obj_Dtor));", out)
        self.assertIn("z = 0x82000004;", out)
        self.assertIn("w = 0x12345678;", out)
        self.assertEqual(left, [0x82000004])


if __name__ == "__main__":
    unittest.main()
