"""The GameCube oracle's NaN results (TcFixups with TcNaN, core/ghidra_shared/ppc), on Ghidra's own FloatFormat
(tests/java/TcNaNStub.java): javac and java against the Ghidra jars, no Ghidra application and no project.
Skipped without config.local.json's ghidra_home and a JDK.

Ghidra makes every NaN it computes or converts the canonical one (an invalid operation's negative), where the
PowerPC keeps the operand's payload (Xbox 360 Too Human 82635508: lfs of 0xffffffff, fmuls, fadds, stfs, then the
word in an integer register: the hardware's 0xffffffff, the oracle's 0x7fc00000). The test proves, over 1.9 million
operand sets of every arithmetic op the fixup covers (NaNs of any payload, quiet and signalling, infinities, zeros,
singles):
  - Ghidra's result is a NaN exactly when IEEE's is, so the fixup (which fires only on a NaN result) never touches
    any other value;
  - TcNaN.arith always gives a quiet NaN;
and checks the PowerPC rules on fixed cases: lfs widens a single NaN's payload without quieting; the first NaN of
frA, frB, frC (fields, not assembly order) is the result, quieted; a single result keeps the top 23 fraction bits;
an invalid operation gives 0x7ff8000000000000; fnm* do not negate a NaN; fneg and fabs change only the sign.

usage: python tests/test_gc_nan.py
"""
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(TOOL), str(TOOL / "tests")]

from test_tail import CP, jdk  # noqa: E402

GC = TOOL / "targets" / "gamecube_ppc"
SHARED = TOOL / "core" / "ghidra_shared" / "ppc"
CASES = {
    "lfs_ffffffff": "ffffffffe0000000",
    "lfs_snan_7f800001": "7ff0000020000000",
    "fmuls_toohuman_0": "ffffffffe0000000",
    "stfs_toohuman": "00000000ffffffff",
    "fadd_snan_1": "7ff8000000000001",
    "fadd_qa_qb": "7ff8123456789abc",
    "fadd_1_qb": "fff8000000000042",
    "fadds_qa_1": "7ff8123440000000",
    "fmadd_c_before_b": "fff8000000000042",
    "fnmadd_qb": "fff8000000000042",
    "fsub_inf_inf": "7ff8000000000000",
    "fdiv_0_0": "7ff8000000000000",
    "ghidra_fdiv_0_0": "fff8000000000000",  # the bug: Ghidra's invalid-operation NaN is negative
    "ghidra_lfs_ffffffff": "7ff8000000000000",  # the bug: the payload is gone at the load
    "fneg_qb": "7ff8000000000042",
    "fabs_qb": "7ff8000000000042",
    "fneg_snan": "fff0000000000001",
}


@unittest.skipUnless(CP and jdk("javac") and jdk("java"), "no ghidra_home or JDK on this machine")
class NaNResults(unittest.TestCase):
    def test_rules(self):
        work = TOOL / "tests" / ".work"
        work.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="gcnan_", dir=work) as out:
            r = subprocess.run([jdk("javac"), "-nowarn", "-proc:none", "-d", out, "-cp", CP, "-sourcepath",
                                os.pathsep.join([str(GC / "ghidra_scripts"), str(SHARED)]),
                                str(TOOL / "tests" / "java" / "TcNaNStub.java")],
                               capture_output=True, text=True, timeout=300)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            r = subprocess.run([jdk("java"), "-cp", out + os.pathsep + CP, "TcNaNStub", "100000", "1"],
                               capture_output=True, text=True, timeout=600)
            self.assertEqual(r.returncode, 0, r.stderr)
        lines = r.stdout.splitlines()
        counts = {k: int(v) for k, v in (ln.split() for ln in lines if not ln.startswith(("example", "case")))}
        examples = [ln for ln in lines if ln.startswith("example")]
        cases = {ln.split()[1]: ln.split()[2] for ln in lines if ln.startswith("case")}
        self.assertGreater(counts.get("tried", 0), 1_800_000)
        for key in ("when_ghidra", "loud"):
            self.assertEqual(counts.get(key, 0), 0, f"{key}: " + "; ".join(e for e in examples if key in e))
        self.assertEqual(cases, CASES)


class Wiring(unittest.TestCase):
    def test_text(self):
        fix = (GC / "ghidra_scripts" / "TcFixups.java").read_text(encoding="utf-8")
        self.assertIn("if (TcNaN.nan(bits(emu, dest))) put(emu, TcNaN.arith(op, srcBits));", fix)
        self.assertIn("if (TcNaN.nan(operand)) put(emu, TcNaN.sign(op, operand));", fix)
        self.assertIn("if (rd[2] == 4 && TcNaN.nan32(rd[3])) put(emu, TcNaN.load(rd[3]));", fix)
        self.assertIn("ps1 = null;\n        if (in == null) return;", fix)  # no step inherits the last one's ps1


if __name__ == "__main__":
    unittest.main()
