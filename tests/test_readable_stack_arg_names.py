"""The readable check names a compared stack word as the callee's declared parameter (core/readable_check)."""
import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_sync import TMP  # noqa: E402,F401  (sets DECOMP_PROJECT when run alone)

from core import readable_argnames, readable_check  # noqa: E402

SIG = "void fn_8c0a6200(s32 param_1, u32 param_2, u32 param_3)"
RAW = ("call #15 to 0x8c0a6200: argument s0 (stack word @(0,r15), args.s[0]) expected 0x00ffffff, got 0x00000000")


class StackArgNames(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = pathlib.Path(self.tmp.name)
        (root / "8c0a6200").mkdir()
        (root / "8c0a6200" / "meta.json").write_text(json.dumps({"signature": SIG, "params": [
            {"name": "param_1", "storage": "r4", "stack_offset": -1},
            {"name": "param_2", "storage": "r5", "stack_offset": -1},
            {"name": "param_3", "storage": "stack", "stack_offset": 0}]}), encoding="utf-8")
        self.harness = root / "8c0bcf60"
        self.meta = {"callees": [{"addr": "8c0a6200", "signature": SIG, "regs": ["r4", "r5", "s0"]}]}

    def test_names_the_parameter(self):
        out = readable_argnames.stack_arg_names(self.meta, self.harness, [RAW], readable_check.callee_storage)[0]
        self.assertIn("parameter 3 of the callee's prototype", out)
        self.assertNotIn("args.s", out)
        self.assertTrue(out.endswith("expected 0x00ffffff, got 0x00000000"))

    def test_other_text_and_unknown_callee_stay(self):
        other = RAW.replace("8c0a6200", "8c000000")
        for d in ("memory 0x8cc00320: expected 1, got 2", other):
            self.assertEqual(readable_argnames.stack_arg_names(self.meta, self.harness, [d], readable_check.callee_storage), [d])


if __name__ == "__main__":
    unittest.main()
