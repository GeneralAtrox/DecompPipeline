"""The GameCube oracle's slw / srw results (TcFixups "shift"), on Ghidra's own p-code shifts
(tests/java/TcShiftStub.java): javac and java against the Ghidra jars, no Ghidra application and no project.
Skipped without config.local.json's ghidra_home and a JDK.

The Gekko SLEIGH shifts by the whole of rB, and Ghidra's 4-byte INT_LEFT / INT_RIGHT give 0 for a count of 32 or
more; the 750CL uses rB's low 6 bits (Skies of Arcadia 801ab6e8, 802377f8, 8023fef8, found by ppc-translate). Over
3.6 million (op, rS, rB) triples (every count 0..255 and 0xffffff00..0xffffffff, random words), the test proves:
  - TcFixups.shift is the 750CL's result for every one;
  - Ghidra's result differs from the 750CL's only for rB >= 64 with bit 0x20 clear, so the fixup (which writes only
    when the step's result differs) changes nothing else.

usage: python tests/test_gc_shift.py
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
CASES = {  # name: Ghidra's result, the fixup's
    "slw_1_41": ("00000000", "00000002"),
    "slw_80000000_ffffffc1": ("00000000", "00000000"),
    "slw_12345678_20": ("00000000", "00000000"),
    "slw_12345678_1f": ("00000000", "00000000"),
    "slw_ffffffff_ffffffff": ("00000000", "00000000"),
    "slw_ffffffff_5f": ("00000000", "80000000"),
    "srw_1_41": ("00000000", "00000000"),
    "srw_80000000_ffffffc1": ("00000000", "40000000"),
    "srw_12345678_20": ("00000000", "00000000"),
    "srw_12345678_1f": ("00000000", "00000000"),
    "srw_ffffffff_ffffffff": ("00000000", "00000000"),
    "srw_ffffffff_5f": ("00000000", "00000001"),
}


@unittest.skipUnless(CP and jdk("javac") and jdk("java"), "no ghidra_home or JDK on this machine")
class ShiftResults(unittest.TestCase):
    def test_rule(self):
        work = TOOL / "tests" / ".work"
        work.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="gcshift_", dir=work) as out:
            r = subprocess.run([jdk("javac"), "-nowarn", "-proc:none", "-d", out, "-cp", CP, "-sourcepath",
                                os.pathsep.join([str(GC / "ghidra_scripts"), str(SHARED)]),
                                str(TOOL / "tests" / "java" / "TcShiftStub.java")],
                               capture_output=True, text=True, timeout=300)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            r = subprocess.run([jdk("java"), "-cp", out + os.pathsep + CP, "TcShiftStub", "300000", "1"],
                               capture_output=True, text=True, timeout=600)
            self.assertEqual(r.returncode, 0, r.stderr)
        lines = r.stdout.splitlines()
        counts = {k: int(v) for k, v in (ln.split() for ln in lines if not ln.startswith(("example", "case")))}
        examples = [ln for ln in lines if ln.startswith("example")]
        cases = {ln.split()[1]: tuple(ln.split()[2:4]) for ln in lines if ln.startswith("case")}
        self.assertGreater(counts.get("tried", 0), 3_000_000)
        self.assertGreater(counts.get("differ", 0), 1000, "Ghidra's full-width shift no longer shows: re-check")
        for key in ("fix_wrong", "differ_outside"):
            self.assertEqual(counts.get(key, 0), 0, f"{key}: " + "; ".join(e for e in examples if key in e))
        self.assertEqual(cases, CASES)


class Wiring(unittest.TestCase):
    def test_text(self):
        fix = (GC / "ghidra_scripts" / "TcFixups.java").read_text(encoding="utf-8")
        self.assertIn('} else if (m.matches("s[lr]w\\\\.?")) {', fix)
        self.assertIn("long r = shift(op, srcBits[0], srcBits[1]);\n"
                      "            if ((bits(emu, dest) & 0xffffffffL) != r) {", fix)  # only a step that differs
        self.assertIn('emu.writeRegister("cr0", BigInteger.valueOf(lt | (bits(emu, "cr0") & 1)));', fix)


if __name__ == "__main__":
    unittest.main()
