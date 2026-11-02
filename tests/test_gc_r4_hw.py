"""GameCube: a 64-bit result's low word (r4) and hardware reads.

1. r3:r4. A callee like OSGetTime returns its low word in r4 and the translator threw it away (r4 = 0 after every call),
   so C code that used it passed the judge's "0 or the junk" alternative or failed by chance. tc_lastr4 gives the oracle's
   junk for the last call (TcJunk slot 4, the same stream), and TcStrict allows 0 only where the callee does not set r4
   (TcFlow.returnsR4: a planted 0 for a real result fails).
2. Hardware reads. A vector scripts what the device answers (vectors.txt H); tcrt_set_hw hands the words out in turn and
   logs each read in the IO order with its kind, and a read compared against a store fails.
Skipped without vcvars (C). Run: python tests/test_gc_r4_hw.py
"""
import importlib.util
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(TOOL), str(TOOL / "tests")]

from test_fmac import vcvars  # noqa: E402
from test_tail import CP, jdk  # noqa: E402

GC = TOOL / "targets" / "gamecube_ppc"
RT = GC / "runtime"
spec = importlib.util.spec_from_file_location("ppc_compare", GC / "ppc_compare.py")
ppc_compare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ppc_compare)
M = (1 << 64) - 1
R4_ORACLE = """public class TcOracle {
    boolean calleeSetsR4(long target) {
        return target == 0x80004000L;
    }
}
"""


def gc_track_stubs(base):
    """tests/java's TcTrack stubs for the GameCube, whose TcTrack.call asks its oracle about r4. R4_ORACLE is named on javac's command
    line, so it wins over the real TcOracle (which needs Ghidra): only 80004000 sets r4 on every path."""
    d = pathlib.Path(base) / "gcsrc"
    d.mkdir()
    (d / "TcOracle.java").write_text(R4_ORACLE, encoding="utf-8")
    out = [str(d / "TcOracle.java")]
    for s in ("TcTrackStub.java", "TcTrackPpcBitsStub.java"):
        text = (TOOL / "tests" / "java" / s).read_text(encoding="utf-8")
        (d / s).write_text(text.replace("TcTrack.call(", "TcTrack.call(new TcOracle(), "), encoding="utf-8")
        out.append(str(d / s))
    return out


def mix(z):
    z = (z + 0x9E3779B97F4A7C15) & M
    z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & M
    z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & M
    return z ^ (z >> 31)


def at(seed, vid, call, slot):
    return mix(mix(mix(mix(seed & M) ^ (vid & M)) ^ (call & M)) ^ (slot & M))


C_DRIVER = r"""
#include <stdio.h>
#include "tc_rt.h"
#include "tc_rt_internal.h"

int main(void)
{
    static const u32 hw[16] = {0x11223344u, 0xffffffffu, 0u, 0xa5a5a5a5u};
    TcArgs a = {0};
    if (!tcrt_init("dummy.dol", "ranges.txt")) return 2;
    tcrt_strict_line("J 1");
    tcrt_strict_line("N 1234 7");
    printf("before %08x\n", tc_lastr4());
    (void)tc_call(0x80100000u, &a);
    printf("one %08x\n", tc_lastr4());
    (void)tc_call(0x80100000u, &a);
    printf("two %08x\n", tc_lastr4());
    tcrt_reset();
    tcrt_strict_line("J 0");
    (void)tc_call(0x80100000u, &a);
    printf("off %08x\n", tc_lastr4());
    tcrt_reset();
    tcrt_set_hw(hw);
    printf("r32 %08x\n", rd32(0xcc006000u));
    wr32(0xcc006004u, 5u);
    printf("r16 %04x\n", rd16(0xcc006000u));
    tcrt_report(stdout, 0, 0, 0);
    puts("--");
    tcrt_reset();
    printf("none %08x\n", rd32(0xcc006000u));
    tcrt_report(stdout, 0, 0, 0);
    return 0;
}
"""


@unittest.skipUnless(vcvars(64), "no vcvars (MSVC) on this machine")
class Runtime(unittest.TestCase):
    def test_runtime(self):
        work = TOOL / "tests" / ".work"
        work.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="gcr4_", dir=work) as d:
            pathlib.Path(d, "drv.c").write_text(C_DRIVER, encoding="utf-8")
            pathlib.Path(d, "dummy.dol").write_bytes(bytes(0x100))
            pathlib.Path(d, "ranges.txt").write_text("", encoding="utf-8")
            vc = str(vcvars(64)).replace("/", "\\")
            src = " ".join(f'"{RT / n}"' for n in ("tc_rt.c", "tc_ppc.c"))
            r = subprocess.run(f'cmd /d /s /c ""{vc}" >nul 2>&1 && cl /nologo /std:c11 /O2 /W4 /WX /I"{RT}" drv.c {src}"',
                               cwd=d, capture_output=True, text=True, errors="replace", timeout=300)
            self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-2000:])
            run = subprocess.run([str(pathlib.Path(d, "drv.exe"))], cwd=d, capture_output=True, text=True, timeout=60)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        first, second = run.stdout.split("--\n")
        got = dict(ln.split(" ", 1) for ln in first.splitlines() if ln.split(" ")[0] in ("before", "one", "two", "off", "r32", "r16"))
        self.assertEqual(got["before"], "00000000")  # no call yet
        self.assertEqual(got["one"], f"{at(0x1234, 7, 0, 4) & 0xffffffff:08x}")  # the same stream as TcJunk slot 4
        self.assertEqual(got["two"], f"{at(0x1234, 7, 1, 4) & 0xffffffff:08x}")
        self.assertEqual(got["off"], "00000000")  # mock_junk off: the old clobber
        self.assertEqual(got["r32"], "11223344")  # in turn
        self.assertEqual(got["r16"], "ffff")
        self.assertEqual([ln for ln in first.splitlines() if ln.startswith(("IO", "FAULT"))],
                         ["IO cc006000 4 11223344 r", "IO cc006004 4 00000005", "IO cc006000 2 ffff r"])
        self.assertIn("FAULT read cc006000", second.splitlines())  # no words scripted: the old fault


class Compare(unittest.TestCase):
    TEXT = "V 0 dev\nRET 00000000 0000000000000000 00000000\n{io}COV 0\nE\n"
    META = {"ret": "void", "callees": [], "params": []}

    def run_pair(self, e, a):
        def parse(lines):
            with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
                f.write(self.TEXT.format(io="".join(x + "\n" for x in lines)))
            try:
                return ppc_compare.parse_results(f.name)[0]
            finally:
                os.unlink(f.name)
        return ppc_compare.compare_vector(self.META, [], {"patches": []}, parse(e), parse(a))

    def test_kind_is_compared(self):
        rd, wr = "IO cc006000 4 00000001 r", "IO cc006000 4 00000001"
        self.assertEqual(self.run_pair([rd], [rd]), [])
        self.assertTrue(self.run_pair([rd], [wr]))  # a store where the original read
        self.assertTrue(self.run_pair([wr], [rd]))
        self.assertTrue(self.run_pair([rd], []))  # the read left out
        self.assertTrue(self.run_pair([rd, wr], [wr, rd]))  # the order of reads and stores


@unittest.skipUnless(CP and jdk("javac") and jdk("java"), "no ghidra_home or JDK on this machine")
class PointerCall(unittest.TestCase):
    def test_r4_after_call(self):
        # r4 counts as set after a callee that sets it on every path, so the next pointer call compares it
        work = TOOL / "tests" / ".work"
        work.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="gcr4j_", dir=work) as d:
            files = gc_track_stubs(d)
            src = os.pathsep.join(str(x) for x in (GC / "ghidra_scripts", GC / "template", GC.parents[1] / "core" / "ghidra_shared" / "ppc"))
            r = subprocess.run([jdk("javac"), "-nowarn", "-proc:none", "-d", d, "-cp", CP, "-sourcepath", src, *files], capture_output=True, text=True, timeout=300)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            f = pathlib.Path(d, "cases.txt")
            f.write_text("T - -\nC 80004000\nC 80002000\nC 80002000\n", encoding="utf-8")
            r = subprocess.run([jdk("java"), "-cp", d + os.pathsep + CP, "TcTrackStub", str(f)], capture_output=True, text=True, timeout=120)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        # 80004000 set r4; 80002000 does not, so after it r4 is junk again
        self.assertEqual([ln.split(": ", 1)[1] for ln in r.stdout.splitlines()], ["|CA", "|CA r3 r4 f1", "|CA r3 f1"])


class Wiring(unittest.TestCase):
    def test_r4(self):
        s = (GC / "ghidra_scripts" / "TcStrict.java").read_text(encoding="utf-8")
        self.assertIn('reg.equals("r4") && last >= 0 && o.calleeSetsR4(last)', s)  # 0 is no alternative for a real result
        t = (GC / "translator" / "ppctranslate" / "translate.py").read_text(encoding="utf-8")
        self.assertIn('Clobber("r4", Call("tc_lastr4", [], "u32"))', t)
        self.assertIn('defined |= {"r3", "r4", "f1"} if any(isinstance(s, CallS)', t)  # a pointer call passes it on
        k = (GC / "ghidra_scripts" / "TcTrack.java").read_text(encoding="utf-8")
        self.assertIn('if (o.calleeSetsR4(target)) d.add("r4");', k)  # and the judge compares it there

    def test_fctiw_saturates(self):
        # x in [2147483647.5, 2^31) rounds to 2^31: the C saturates (tc_ppc.c), and the wrap the oracle had is gone
        f = (GC / "ghidra_scripts" / "TcFixups.java").read_text(encoding="utf-8")
        self.assertIn("w = t >= 0x80000000L ? 0x7fffffffL : t & 0xffffffffL;", f)

    def test_inputs_stay_the_workspaces(self):
        # Vec and repairVec belong to each workspace's own gated TcInputs: the scripts may not need anything new of them
        i = (GC / "template" / "TcInputs.java").read_text(encoding="utf-8")
        self.assertNotIn("hw", i.split("class Vec", 1)[1].split("}", 1)[0])
        self.assertIn("Vec repairVec(Vec v, long faultAddr, boolean write, List<long[]> loads)", i)


if __name__ == "__main__":
    unittest.main()
