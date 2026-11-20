"""compile_all.py lints each function's C with the Xbox 360 judge's lint (x360_lint.lint, as x360_check), not core/lint: the judge
waives core/lint's 60 KB cap (an agent-readability limit) up to 2 MB, so a large function the judge and the port take
must not be counted as not building. With core/lint, 38 Too Human functions of 2,000 to 8,000 instructions were.

    python -m unittest tests.test_compile_all
"""
import json
import pathlib
import shutil
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
X360 = HERE.parent.parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "tools"))
import compile_all  # noqa: E402
from xenontranslate import workspace  # noqa: E402
from xenontranslate.translate import Signature, translate_function  # noqa: E402


class JudgeLint(unittest.TestCase):
    def setUp(self):
        self.d = pathlib.Path(tempfile.mkdtemp(prefix="x360_lint_"))
        img = bytearray(0x100)
        img[0:8] = bytes.fromhex("386300014E800020")   # addi r3,r3,1; blr
        (self.d / "image.bin").write_bytes(bytes(img))
        (self.d / "project.json").write_text(json.dumps({"target": "xbox360_ppc", "binary": "image.bin"}), encoding="utf-8")
        (self.d / "image.json").write_text(json.dumps({"load_address": 0x82000000, "sections": [
            {"start": 0x82000000, "end": 0x82000100, "code": True}]}), encoding="utf-8")
        (self.d / "catalog_inputs.json").write_text(json.dumps({"helper_ranges": [], "functions": [
            {"addr": "82000000", "size": 8, "kind": "function"}]}), encoding="utf-8")
        (self.d / "state").mkdir()
        w = workspace.Workspace(self.d, target=X360 / "target.json")
        self.sig = Signature.generic_for(0x82000000)
        self.src, _ = translate_function(w.dol, 0x82000000, self.sig, w.facts, w.bodies[0x82000000])

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_judge_cap_not_core_cap(self):
        if hasattr(compile_all, "judge_lint"):
            lint = compile_all.judge_lint()
        else:   # before: the worker linted with core/lint (so this check meets the old code, not a missing name)
            sys.path.insert(0, str(compile_all.DP))
            from core.lint import lint
        meta = {"name": self.sig.name, "signature": self.sig.text}
        self.assertEqual(lint(self.src, meta), [])
        big = self.src + "/* " + "x" * 70000 + " */\n"            # over core/lint's 60 KB, under the judge's 2 MB
        self.assertEqual(lint(big, meta), [])
        huge = self.src + "/* " + "x" * 2_100_000 + " */\n"       # planted: past the judge's own cap, still refused
        self.assertTrue(any("larger than" in e for e in lint(huge, meta)))
        bad = self.src.replace('#include "tc_rt.h"', '#include "tc_rt.h"\n#include <stdio.h>')   # planted: other rules still apply
        self.assertNotEqual(lint(bad, meta), [])


if __name__ == "__main__":
    unittest.main()
