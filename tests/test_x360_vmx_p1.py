"""VMX_JUDGE_PLAN.md P1: the plain AltiVec, Cell and VMX128 ops confirmed in TcVmxConfirmed.java, and TcVmx's fix.

The audit (each row's source names it): the AltiVec PEM per op; the Cell BE VMX PEM for lvlx, lvrx, stvlx, stvrx (and
the l forms); Xenia at 95a5c3ee (src/xenia/cpu/ppc/ppc_emit_altivec.cc, ppc_decode_data.h, ppc_opcode_lookup_gen.cc),
read only, as the second reference; the game's own code. One disagreement was found and fixed: vupkhsb128 and
vupklsb128 with VA = 0x60 are the halfword-to-word unpacks (vupkhsh128, vupklsh128); the game feeds their results to
word ops (vcsxwfp128 191 times, vaddsws, vslw128, vsubsws). Neither byte form is confirmed. The translator's decode.py
still names them the byte forms (a follow-up), so a function holding them stays refused.
Checked:
1. TcVmxP1Stub (javac and java only): the game's word at 82a131bc decodes as vupkhsh128 and runs on word lanes; the
   VA = 0 words stay the byte forms; lane order, saturation and SAT, NJ on inputs and results, and NaN choice for a few
   confirmed ops. TCVMX_SCRIPTS points it at another ghidra_scripts (the fails-before: 11cdbc39 decodes vupkhsb128);
2. the list: the 130 confirmed ops, the vupk?sb128 pair absent, every row sourced;
3. the plants (tests/data/x360_vmx_p1_plants.json, for dp_judge --plant on build box B): each (a) pattern applies exactly
   once to the helper text it targets, so a judge PASS on a planted function means the judge missed a wrong answer.
usage: python tests/test_x360_vmx_p1.py
"""
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[1]
X360 = TOOL / "targets" / "xbox360_ppc"
sys.path[:0] = [str(X360 / "tools"), str(X360 / "translator"), str(TOOL / "tests")]

import catalog_refuse  # noqa: E402
from test_tail import jdk  # noqa: E402
from xenontranslate.vmx import H  # noqa: E402

PLANTS = TOOL / "tests" / "data" / "x360_vmx_p1_plants.json"
SCRIPTS = pathlib.Path(os.environ.get("TCVMX_SCRIPTS") or X360 / "ghidra_scripts")
HELPER = {"vspltw": "xv_vspltw", "vmrghw": "xv_vmrghw", "vaddshs": "xv_vaddshs", "vsubshs": "xv_vsubshs",
          "vpkshus": "xv_vpkshus", "denormal input": "xv_wf", "denormal result": "xv_fw", "lvlx": "xv_ldl",
          "lvrx": "xv_ldr"}


@unittest.skipUnless(jdk("javac") and jdk("java"), "no JDK on this machine")
class Java(unittest.TestCase):
    def test_stub(self):
        work = TOOL / "tests" / ".work"
        work.mkdir(parents=True, exist_ok=True)
        out = tempfile.mkdtemp(prefix="vmxp1_", dir=work)
        try:
            r = subprocess.run([jdk("javac"), "-nowarn", "-proc:none", "-d", out, "-sourcepath", str(SCRIPTS),
                                str(TOOL / "tests" / "java" / "TcVmxP1Stub.java")], capture_output=True, text=True,
                               timeout=300)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            r = subprocess.run([jdk("java"), "-cp", out, "TcVmxP1Stub"], capture_output=True, text=True, timeout=60)
        finally:
            shutil.rmtree(out, ignore_errors=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        wrong = [x for x in r.stdout.splitlines() if x.startswith("WRONG")]
        self.assertEqual(wrong, [], r.stdout)
        self.assertIn("ALL OK", r.stdout)


class List(unittest.TestCase):
    def test_confirmed(self):
        ops = catalog_refuse.confirmed_ops()
        self.assertEqual(len(set(ops) - {"vpermwi128", "vrlimi128"}), 130)   # P2 adds those two
        self.assertNotIn("vupkhsb128", ops)
        self.assertNotIn("vupklsb128", ops)
        for op in ("vaddshs", "vmaddfp", "lvlx", "stvrx128", "vmaddcfp128", "vpkuhus128"):
            self.assertIn(op, ops)
        self.assertTrue(all(v.strip() for v in ops.values()))


class Plants(unittest.TestCase):
    def test_each_a_plant_applies_once(self):
        plants = json.loads(PLANTS.read_text(encoding="utf-8"))
        classes = {p[2] for p in plants.values()}
        for cls in ("lane", "saturation", "rounding", "CBEA", "b field"):
            self.assertTrue(any(cls in c for c in classes), cls)
        for addr, (pat, rep, cls) in plants.items():
            if cls.startswith("b field"):
                m = re.match(r"\\bv(\d+)\(\?=", pat)   # a read of a 128-only register
                self.assertTrue(m and int(m.group(1)) >= 64, pat)
                continue
            h = next(h for k, h in HELPER.items() if k in cls)
            new, n = re.subn(pat, rep, H[h])
            self.assertEqual(n, 1, (addr, cls))
            self.assertNotEqual(new, H[h])


if __name__ == "__main__":
    unittest.main()
