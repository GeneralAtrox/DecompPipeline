"""The Xbox 360 oracle's pointer seeding (branch x360-judge-ptrseed; TcPtrSeed, TcInputs retN).

Too Human had 17 functions with no usable vectors (NO_VECTORS): every vector of 10 faulted in the oracle, and 7 of one
family kept 1-3. Most store through what a mocked callee returned (bl 8312ec1c, then lwz r31,0x20(r3)), and pointer
repair never looked at call results. Ported from the GameCube target (DecompPipeline b110a1a2): a call's result near the
fault becomes a fresh chunk (TcVectors.repair there), and hints may name a call's result as a location (retN, 95776359).
Here the newest candidate goes first: the call, unless a load near the fault came after it (TcOracle counts the loads
logged before each call). A store outside RAM stops the run where it happens (TcBank.badWrite).
Checked here (no JDK): the wiring in TcOracle and TcVectors, TcPtrSeed pure and locked, TcInputs still lints, sizes.
Phase 2 (build box B: javac against the Ghidra jars): tests/java/X360PtrSeedStub - the pick's cases, repairRet, the repair order, retN, badWrite.
usage: python tests/test_x360_ptrseed.py
"""
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import unittest

sys.path[:0] = [str(pathlib.Path(__file__).resolve().parent)]
from test_xbox360_ppc import TOOL, X360, tmpdir  # noqa: E402
from test_tail import CP, jdk  # noqa: E402
from core import javalint  # noqa: E402

SCRIPTS = X360 / "ghidra_scripts"
SHARED = TOOL / "core" / "ghidra_shared" / "ppc"


def src(p):
    return pathlib.Path(p).read_bytes().decode("utf-8")


class Wiring(unittest.TestCase):
    def test_tcptrseed_pure_and_locked(self):
        s = src(SCRIPTS / "TcPtrSeed.java")
        self.assertEqual(re.findall(r"^import .*", s, re.M), ["import java.util.*;"])
        self.assertIn("TcPtrSeed.java", json.loads(src(X360 / "target.json"))["locked_scripts"])

    def test_oracle_counts_loads_before_each_call(self):
        o = src(SCRIPTS / "TcOracle.java")
        self.assertIn("int seen; List<Integer> callSeen = new ArrayList<>();", o)
        self.assertLess(o.index("res.callSeen.add(res.seen);"), o.index('emu.writeRegister("r3", u64(calls < 16 ? v.rets[calls] : 0));'))
        self.assertIn("res.loads.add(ld);\n                    res.seen++;", o)

    def test_attempt_repair_order(self):
        v = src(SCRIPTS / "TcVectors.java")
        at = v[v.index("Result attempt("):v.index("boolean keep(")]
        self.assertLess(at.index("TcPtrSeed.repair(this, v, r, loads)"), at.index("repairEntry(v, r.faultAddr)"))
        s = src(SCRIPTS / "TcPtrSeed.java")
        rp = s[s.index("static TcInputs.Vec repair("):s.index("static boolean repairRet(")]
        order = ["repairRet(g, v, r, loads, true)", "g.repairVec(v, r.faultAddr, r.faultWrite, loads)", "repairRet(g, v, r, loads, false)"]
        self.assertEqual([rp.index(x) for x in order], sorted(rp.index(x) for x in order))

    def test_bad_write_stops_the_run(self):
        b = src(SCRIPTS / "TcBank.java")
        self.assertIn("long badWrite = -1;", b)
        self.assertIn("f < TcBase.IMAGE_LO && !(TcBase.inRegions(f) && TcBase.inRegions(f + size - 1))", b)
        o = src(SCRIPTS / "TcOracle.java")
        self.assertIn("if (bank.codeRead >= 0 || bank.badWrite >= 0) {", o)
        self.assertIn("res.faultAddr = res.faultWrite ? bank.badWrite : bank.codeRead;", o)

    def test_branch_into_data_is_not_outside(self):
        # 82e40388 (REFUSED on a722231e): a blr to 4000079c, a return address its own copy overwrote, refused the function
        o = src(SCRIPTS / "TcOracle.java")
        self.assertIn("if (prev < 0 && calls > 0 || TcRun.intoData(pc)) {", o)
        self.assertLess(o.index("TcRun.intoData(pc)"), o.index("res.outside = pc;"))
        self.assertIn("return (pc & 0xffffffffL) < TcBase.IMAGE_LO;", src(SCRIPTS / "TcRun.java"))

    def test_no_array_repair(self):
        # tried and left out (TcPtrSeed's header): it cost 82e69df8, 8263d428 and 82d13a18 their vectors
        self.assertNotIn("repairArray", src(SCRIPTS / "TcPtrSeed.java"))

    def test_inputs_ret_location(self):
        s = src(X360 / "template" / "TcInputs.java")
        self.assertIn('p[0].matches("r([3-9]|10)|ret([0-9]|1[0-5])")', s)
        self.assertEqual(javalint.lint_inputs(s), [])

    def test_sizes_and_line_endings(self):
        for p in (SCRIPTS / "TcPtrSeed.java", SCRIPTS / "TcVectors.java", SCRIPTS / "TcOracle.java",
                  X360 / "template" / "TcInputs.java"):
            self.assertLessEqual(len(src(p).splitlines()), 400, p.name)
            self.assertNotIn(b"\r\n", p.read_bytes(), p.name)

    def test_tracked_hints_lint(self):
        for p in sorted((X360 / "games" / "too_human" / "hints").glob("*.txt")):
            self.assertEqual(javalint.lint_hints(src(p)), [], p.name)


@unittest.skipUnless(CP and jdk("javac") and jdk("java"), "no ghidra_home or JDK on this machine (phase 2: build box B)")
class Java(unittest.TestCase):
    def test_stub(self):
        out = tmpdir("x360ptr_")
        try:
            files = [str(p) for p in [*SCRIPTS.glob("*.java"), *SHARED.glob("*.java")]]
            files += [str(X360 / "template" / "TcInputs.java"), str(TOOL / "tests" / "java" / "X360PtrSeedStub.java")]
            r = subprocess.run([jdk("javac"), "-nowarn", "-proc:none", "-J-Xmx512m", "-d", str(out), "-cp", CP, *files],
                               capture_output=True, text=True, timeout=600)
            self.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr[-3000:])
            r = subprocess.run([jdk("java"), "-Xmx256m", "-cp", str(out) + os.pathsep + CP, "X360PtrSeedStub"],
                               capture_output=True, text=True, timeout=120)
        finally:
            shutil.rmtree(out, ignore_errors=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual([x for x in r.stdout.splitlines() if x.startswith("WRONG")], [], r.stdout)
        self.assertIn("ALL OK", r.stdout)


if __name__ == "__main__":
    unittest.main()
