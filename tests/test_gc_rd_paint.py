"""GameCube: the readable runner paints the stack before each vector, as the matched runner (runtime/runner.c) does.
Without it a slot the C never sets (a TcArgs filled only in part, Skies of Arcadia 8020cba4, 80213920) read whatever
the last vector left on the host stack, so the readable judge's call lines could change between runs and differ from
the raw judge's for the same C. The test builds the real readable runner (rd_runner.c, rd_mem.c, rd_veh.c, rd_be.c,
tc_ppc.c: ppc_readable's sources, 32-bit, the judge's C flags and link options) with an empty prototype table, an
empty DOL and a function that sets only r3 of its TcArgs (raw C through rd_veh.c's tc_call), runs two vectors twice
and checks:
  - every unset f and s slot of the call reads the pattern the matched runner paints, in both vectors;
  - the two runs' outputs are identical.
Skipped without a 32-bit Visual Studio environment.
usage: python tests/test_gc_rd_paint.py
"""
import pathlib
import subprocess
import sys
import tempfile
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(TOOL), str(TOOL / "tests")]
from test_fmac import vcvars  # noqa: E402

GC = TOOL / "targets" / "gamecube_ppc"
RD, RT = GC / "runtime_rd", GC / "runtime"
SOURCES = [RD / n for n in ("rd_runner.c", "rd_mem.c", "rd_veh.c", "rd_be.c")] + [RT / "tc_ppc.c"]
FLAGS = "/fp:strict /arch:SSE2 /std:c11 /W4 /WX /TC"  # core/readable_check.C_FLAGS
LINK = "/link /LARGEADDRESSAWARE /NXCOMPAT /DYNAMICBASE:NO /FIXED:NO /BASE:0x5a000000"  # readable_check.link_flags
WORK = r"""
#include "tc_rt.h"

void fn_test(void)
{
    TcArgs a;
    a.r[0] = 7;
    tc_call(0x80001000u, &a);
}

void tc_thunk(const u32 *r, const f64 *f, u32 *out_r3, u32 *out_r4, f64 *out_f1)
{
    (void)r;
    (void)f;
    (void)out_r3;
    (void)out_r4;
    (void)out_f1;
    fn_test();
}
"""
PROTOS = '#include "rd_internal.h"\nconst RdProto rd_protos[] = {\n    {0u, "", \'i\', ""},\n};\nconst int rd_nprotos = 0;\n'
VECTORS = "V 0 dev\nR 0 0 0 0 0 0 0 0\nE\nV 1 dev\nR 1 2 3 4 5 6 7 8\nE\n"


@unittest.skipUnless(vcvars(32), "no 32-bit vcvars (MSVC) on this machine")
class PaintedStack(unittest.TestCase):
    def test_unset_slots_read_the_pattern(self):
        work = TOOL / "tests" / ".work"
        work.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="gcrdpaint_", dir=work) as d:
            p = pathlib.Path(d)
            (p / "work.c").write_text(WORK, encoding="utf-8")
            (p / "protos.c").write_text(PROTOS, encoding="utf-8")
            (p / "main.dol").write_bytes(bytes(0x100))
            (p / "ranges.txt").write_text("", encoding="utf-8")
            (p / "vectors.txt").write_text(VECTORS, encoding="utf-8")
            vc = str(vcvars(32)).replace("/", "\\")
            src = " ".join(f'"{s}"' for s in SOURCES)
            r = subprocess.run(f'cmd /d /s /c ""{vc}" >nul 2>&1 && cl /nologo /O1 {FLAGS} /I"{RD}" /I"{RT}" '
                               f'/Ferd_runner.exe work.c protos.c {src} {LINK}"', cwd=d, capture_output=True,
                               text=True, errors="replace", timeout=300)
            self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-2000:])
            outs = []
            for k in (1, 2):
                run = subprocess.run([str(p / "rd_runner.exe"), "main.dol", "ranges.txt", "vectors.txt", "dev",
                                      f"out{k}.txt"], cwd=d, capture_output=True, text=True, timeout=60)
                self.assertEqual(run.returncode, 0, run.stderr)
                outs.append((p / f"out{k}.txt").read_text(encoding="utf-8"))
            self.assertEqual(outs[0], outs[1])
            calls = [ln.split() for ln in outs[0].splitlines() if ln.startswith("CALL")]
            self.assertEqual(len(calls), 2)
            for c in calls:
                self.assertEqual(c[1:3], ["80001000", "00000007"])
                self.assertEqual(c[10:18], ["a5a5a5a5a5a5a5a5"] * 8)  # f1..f8
                self.assertEqual(c[18:], ["a5a5a5a5"] * 8)  # s0..s7

    def test_wiring(self):
        src = (RD / "rd_runner.c").read_text(encoding="utf-8")
        self.assertLess(src.index("rd_arm();"), src.index("paint_stack();"))
        self.assertLess(src.index("paint_stack();"), src.index("int code = run_one("))
        matched = (RT / "runner.c").read_text(encoding="utf-8")
        for text in ("#define TC_PAINT 0xa5a5a5a5a5a5a5a5ull", "static __declspec(noinline) void paint_stack(void)",
                     "volatile u64 pad[1 << 13];", "for (int i = 0; i < (1 << 13); i++) pad[i] = TC_PAINT;"):
            if text in matched:  # the matched runner's paint, where this branch has it: the same block
                self.assertIn(text, src)


if __name__ == "__main__":
    unittest.main()
