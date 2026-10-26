"""The GameCube oracle's single-precision results (TcFixups "single"), on Ghidra's own FloatFormat
(tests/java/TcFixupsStub.java): javac and java against the Ghidra jars, no Ghidra application and no project.
Skipped without config.local.json's ghidra_home and a JDK.

Ghidra's float2float 8 -> 4 returns 0 for every value below the smallest single subnormal 2^-149, where IEEE round to
nearest (the 750CL with FPSCR[RN] = 0, NI = 0; the C's (f32) cast) gives 2^-149 (0x00000001) above 2^-150 (Skies of
Arcadia 800cf988 and 800d1a34, found by ppc-translate). Over 2.6 million values (random bit patterns, the subnormal
range, sums, products, quotients, roots and fused forms of random singles), the test proves:
  - Ghidra's rounding differs from the IEEE cast only for |x| in (2^-150, 2^-149), and does there;
  - TcSingle.singleResult (core/ghidra_shared/ppc, which TcFixups calls) gives the IEEE cast's value for every
    non-NaN x, Ghidra's value as its first element, and a different value only inside that range: every other
    result of the oracle stays what it was;
  - TcSingle.singleSource is Java's double arithmetic (the SLEIGH's 8-byte f+, f-, f*, f/, sqrt) for every op.

usage: python tests/test_gc_single.py
"""
import json
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
SHARED = TOOL / "core" / "ghidra_shared" / "ppc"  # TcSingle: the rule, shared with the Xbox 360 target


@unittest.skipUnless(CP and jdk("javac") and jdk("java"), "no ghidra_home or JDK on this machine")
class Single(unittest.TestCase):
    def test_flush_range_only(self):
        work = TOOL / "tests" / ".work"
        work.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="gcsingle_", dir=work) as out:
            r = subprocess.run([jdk("javac"), "-nowarn", "-proc:none", "-d", out, "-cp", CP, "-sourcepath",
                                os.pathsep.join([str(GC / "ghidra_scripts"), str(SHARED)]),
                                str(TOOL / "tests" / "java" / "TcFixupsStub.java")],
                               capture_output=True, text=True, timeout=300)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            r = subprocess.run([jdk("java"), "-cp", out + os.pathsep + CP, "TcFixupsStub", "200000", "1"],
                               capture_output=True, text=True, timeout=600)
            self.assertEqual(r.returncode, 0, r.stderr)
        counts = {k: int(v) for k, v in (ln.split() for ln in r.stdout.splitlines() if not ln.startswith("example"))}
        examples = [ln for ln in r.stdout.splitlines() if ln.startswith("example")]
        self.assertGreater(counts.get("tried_f2f", 0), 2_000_000)
        self.assertGreater(counts.get("f2f_flush", 0), 1000, "Ghidra's flush no longer shows: re-check TcFixups")
        for key in ("f2f_other", "fix_wrong", "fix_moved", "fix_ghidra") + tuple(
                "src_" + op for op in ("fadds", "fsubs", "fmuls", "fdivs", "fmadds", "fmsubs", "fnmadds", "fsqrts",
                                       "frsp")):
            self.assertEqual(counts.get(key, 0), 0, f"{key}: " + "; ".join(e for e in examples if key in e))
        self.assertIn("example f2f_flush 3690000000000001 00000000 00000001", examples)  # just above 2^-150


class Wiring(unittest.TestCase):
    def test_text(self):
        fix = (GC / "ghidra_scripts" / "TcFixups.java").read_text(encoding="utf-8")
        orc = (GC / "ghidra_scripts" / "TcOracle.java").read_text(encoding="utf-8")
        self.assertIn('if (r != null && r[0] != r[1] && bits(emu, dest) == r[0]) put(emu, r[1]);', fix)  # only the flushed step
        self.assertIn('kind.equals("single") ? TcSingle.singleResult(op, TcSingle.singleSource(op, srcBits)) : null;', fix)
        self.assertNotIn("static long[] singleResult", fix)  # one implementation: TcSingle's
        tj = json.loads((GC / "target.json").read_text(encoding="utf-8"))
        self.assertEqual(tj["shared_scripts"], ["ppc"])
        self.assertIn("TcSingle.java", tj["locked_scripts"])  # compiled with TcFixups in the staging bundle
        self.assertIn("fix.before(emu, in);", orc)
        self.assertIn("if (stepped && fault[0] == null) {\n                    fix.after(emu, ram, bank.stored, bank.reads);", orc)


if __name__ == "__main__":
    unittest.main()
