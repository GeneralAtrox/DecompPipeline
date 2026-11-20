"""GameCube oracle: "genretptr CALLEE [OFF]", a hint for loops whose every allocator result is stored through, and
results for calls up to the 1024th (Skies of Arcadia round r10, 2026-11-14).

Before: a hint could set a call's result only for calls 0..15 (template TcInputs `retN`, "bad call index"), and a
result after the 16th became a pointer only through TcVectors.lateRepair (16 results per repair, 12 repairs), up to
TcOracle.RETS = 64; every later call returned 0. Skies 80197270 (15 passes of 5 calls, 802268e8's and 801e1c7c's
results stored through: 75 calls), 80195f9c (80 passes of 801d68c4 x3 + 801d4f0c, then about 80 more: about 400
calls) and 80131a04 had no valid vector: each run stored through a 0 past the 64th call.

The rule:
1. TcOracle.RETS and tc_rt_internal.h TCRT_RETS are 1024 (both C runners read up to it; a call past it returns 0).
   Line Q (scripted r4, TcCallFx) still stops at call 64 (TcOracle.Q_CALLS), as before.
2. A hints file line "genretptr CALLEE [OFF]" is the oracle's (TcVectors.retPtrHints): it never reaches the
   workspace's TcInputs.applyHints, which would refuse it. While vectors are chosen (TcVectors.runGrow), a mocked call
   k < RETS to CALLEE whose scripted result is not one of our pointers gets a fresh chunk sized for a field at OFF
   (TcCallFx.grow), put in memory at once untracked, and the vector runs again until a run sets none (at most
   GROW_RUNS reruns, else invalid): the kept run had every input at entry, as the C runner has.
3. "genretptr CALLEE OFF PTR.." also makes each chunk's words at PTR.. scratch pointers (Skies 80131a04 stores through
   its 801d68c4 results' +4 word, 32 times a run: more than the repairs reach).
4. Without such a line, runGrow is one runOracle: every other function's harness is as before (hooks.retptr_hint, the
   save_helper_frame stamp scope; RETS reaches only a run past 64 calls: hooks.late_calls).

Stub (tests/java/TcGcRetPtrStub.java, no Ghidra program): a stand-in run of n calls storing through each CALLEE
result. Before (d4e36f14) the stub does not compile (no retPtrHints, runGrow, TcCallFx.grow) and RETS is 64.
usage: python tests/test_gc_retptr.py
"""
import os
import pathlib
import re
import sys
import tempfile
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(TOOL), str(TOOL / "tests")]
from test_tail import CP, jdk  # noqa: E402

GC = TOOL / "targets" / "gamecube_ppc"
SHARED = TOOL / "core" / "ghidra_shared" / "ppc"


class Wiring(unittest.TestCase):
    def test_limits(self):
        orc = (GC / "ghidra_scripts" / "TcOracle.java").read_text(encoding="utf-8")
        h = (GC / "runtime" / "tc_rt_internal.h").read_text(encoding="utf-8")
        self.assertEqual(re.search(r"static final int RETS = (\d+);", orc).group(1), "1024")  # d4e36f14: 64
        self.assertEqual(re.search(r"#define TCRT_RETS (\d+)", h).group(1), "1024")
        self.assertIn("if (calls < Q_CALLS && calleeSetsR4(pc)) {", orc)
        vec = (GC / "ghidra_scripts" / "TcVectors.java").read_text(encoding="utf-8")
        self.assertIn("applyHints(v, rest);", vec)
        self.assertEqual(vec.count("runGrow(v);"), 3)  # the first run, the repairs' and the last resorts' runs
        self.assertNotIn("runOracle(v);\n", vec.split("Result runGrow(Vec v) {")[0])

    def test_ptr_fields(self):  # without a JDK the stub is skipped: the form of the PTR fields here (59a821c2: none)
        orc = (GC / "ghidra_scripts" / "TcOracle.java").read_text(encoding="utf-8")
        fx = (GC / "ghidra_scripts" / "TcCallFx.java").read_text(encoding="utf-8")
        self.assertIn("final Map<Long, long[]> retPtr = new HashMap<>();", orc)
        self.assertIn("static int grow(TcOracle o, TcInputs.Vec v, int k, long[] s) {", fx)
        self.assertIn("o.genPut(v.patchData.get(n), (int) at, o.scrPtr());", fx)


class Hook(unittest.TestCase):
    def test_retptr_hint(self):
        import importlib.util
        from unittest import mock
        from test_sync import TMP  # noqa: F401  (a project for core.paths)
        from core import paths
        spec = importlib.util.spec_from_file_location("gc_hooks", GC / "hooks.py")
        hooks = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(hooks)
        work = TOOL / "tests" / ".work"
        work.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=work) as d:
            pathlib.Path(d, "80000100.txt").write_text("genptr r3\n  genretptr 0x801d68c4 0x14\n", encoding="utf-8")
            pathlib.Path(d, "80000200.txt").write_text("genptr r3\n# genretptr 0x801d68c4\n", encoding="utf-8")
            with mock.patch.object(paths, "HINTS", pathlib.Path(d)):
                got = [hooks.retptr_hint(a) for a in ("80000100", "80000200", "80000300")]
        self.assertEqual(got, [True, False, False])


@unittest.skipUnless(CP and jdk("javac") and jdk("java"), "no ghidra_home or JDK on this machine")
class Stub(unittest.TestCase):
    def test_cases(self):
        import subprocess
        work = TOOL / "tests" / ".work"
        work.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=work) as out:
            src = os.pathsep.join([str(GC / "ghidra_scripts"), str(GC / "template"), str(SHARED)])
            r = subprocess.run([jdk("javac"), "-nowarn", "-proc:none", "-d", out, "-cp", CP, "-sourcepath", src,
                                str(TOOL / "tests" / "java" / "TcGcRetPtrStub.java")],
                               capture_output=True, text=True, timeout=300)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)  # d4e36f14: no retPtrHints, runGrow, grow
            r = subprocess.run([jdk("java"), "-cp", out + os.pathsep + CP, "TcGcRetPtrStub"],
                               capture_output=True, text=True, timeout=300)
            self.assertEqual(r.returncode, 0, r.stderr)
        got = {k: v.strip() for k, v in (ln.split(":", 1) for ln in r.stdout.splitlines() if ":" in ln)}
        print(r.stdout)
        self.assertEqual(got["parse"], "1=[2,3] 801d68c4=[20] 801e1c7c=[0] | genptr r3 | # note | genretptr")  # 59a821c2: no PTR
        self.assertTrue(got["no_hint_80"].startswith("ok false runs 1 "), got["no_hint_80"])
        self.assertEqual(got["hint_80"], "ok true runs 2 own 60 bad 0")  # the 60 CALLEE results set, then a clean run
        self.assertTrue(got["hint_700"].startswith("ok true runs 2 "), got["hint_700"])  # past the old 64
        self.assertIn("bad 0", got["hint_700"])  # the heap runs out: scratch pointers, still ours
        self.assertTrue(got["other_callee"].startswith("ok false runs 1 "), got["other_callee"])
        self.assertEqual(got["ptr_field"], "ok true runs 2 own 60 bad 0")  # 59a821c2: the +4 words are random
        self.assertTrue(got["no_ptr_field"].startswith("ok false runs 2 "), got["no_ptr_field"])
        self.assertEqual(got["plain_run"], "ok false grew false own 0 bad 60")
        self.assertEqual(got["kept"], "ok true grew false tail 23 len 1008")  # calls 16..38 (39 is OTHER's)
        self.assertEqual(got["limit"], "true -1 -1 -1 rets 1024")


if __name__ == "__main__":
    unittest.main()
