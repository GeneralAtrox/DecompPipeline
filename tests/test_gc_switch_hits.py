"""GameCube oracle: oracle_summary.json "switch_hits", which cases of each switch the kept vectors reach (report only;
Skies of Arcadia round r11, 2026-11-14, ported from the Xbox 360 target's TcSwitchHits, Too Human 48043fe9).

A PASS whose vectors never enter a switch's cases says nothing about them. TcOracle notes {pc, CTR} at each bcctr
without link it runs in the body (TcSwitchHits.isBcctr: (word & 0xFC0007FF) == 0x4C000420), TcVectors adds a kept
vector's notes, and TcPrepare.summary writes {"bctr": {"target": runs}} when there are any. No verdict, vector or
expected result depends on it.

Before (bbde696e) there is no TcSwitchHits: the wiring checks fail and the stub does not compile.
usage: python tests/test_gc_switch_hits.py
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


class Wiring(unittest.TestCase):
    def test_wiring(self):
        orc = (GC / "ghidra_scripts" / "TcOracle.java").read_text(encoding="utf-8")
        vec = (GC / "ghidra_scripts" / "TcVectors.java").read_text(encoding="utf-8")
        self.assertIn('TcSwitchHits.isBcctr(in.getInt(0) & 0xffffffffL)) res.bctrs.add(new long[] {pc, reg(emu, "CTR")});',
                      orc)
        self.assertIn("switchHits.add(r.bctrs);", vec)
        self.assertIn('"TcSwitchHits.java"', (GC / "target.json").read_text(encoding="utf-8"))


@unittest.skipUnless(CP and jdk("javac") and jdk("java"), "no ghidra_home or JDK on this machine")
class Stub(unittest.TestCase):
    def test_summary(self):
        work = TOOL / "tests" / ".work"
        work.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=work) as out:
            src = os.pathsep.join([str(GC / "ghidra_scripts"), str(GC / "template"), str(SHARED)])
            r = subprocess.run([jdk("javac"), "-nowarn", "-proc:none", "-d", out, "-cp", CP, "-sourcepath", src,
                                str(TOOL / "tests" / "java" / "TcGcSwitchHitsStub.java")],
                               capture_output=True, text=True, timeout=300)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)  # bbde696e: no TcSwitchHits
            r = subprocess.run([jdk("java"), "-cp", out + os.pathsep + CP, "TcGcSwitchHitsStub"],
                               capture_output=True, text=True, timeout=300)
            self.assertEqual(r.returncode, 0, r.stderr)
        got = {k: v.strip() for k, v in (ln.split(":", 1) for ln in r.stdout.splitlines() if ":" in ln)}
        print(r.stdout)
        self.assertEqual(got["bcctr"], "true false true false")  # bctr, bctrl, beqctr, blr
        self.assertEqual(got["summary"], '"switch_hits":{"80001018":{"8000101c":2,"8000102c":1}},')
        self.assertEqual(got["none"], "false")


if __name__ == "__main__":
    unittest.main()
