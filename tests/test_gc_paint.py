"""GameCube: the runner paints the stack before each vector. Matched C that fills a TcArgs only in part (Skies of
Arcadia 8020cba4, 80213920: `TcArgs a;` with r3.. set and f1..f8 never) left those slots as whatever the last
vector left on the host stack, often a host address, so the call lines in actual.txt changed on every run (found by
waste audit 2). runner.c now fills the stack below main with one pattern (0xa5 bytes) before each vector. The test
builds the real runner (runner.c, tc_rt.c, tc_ppc.c) with ppc_check.build's flags, an empty DOL and a function that
sets only r3 of its TcArgs, runs two vectors twice and checks:
  - every unset f and s slot of the call reads the pattern, in both vectors;
  - the two runs' outputs are identical.
Skipped without config.local.json's vcvars (MSVC).
usage: python tests/test_gc_paint.py
"""
import pathlib
import subprocess
import sys
import tempfile
import unittest

TOOL = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(TOOL), str(TOOL / "tests")]
from test_fmac import vcvars  # noqa: E402

RT = TOOL / "targets" / "gamecube_ppc" / "runtime"
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
VECTORS = "V 0 dev\nR 0 0 0 0 0 0 0 0\nE\nV 1 dev\nR 1 2 3 4 5 6 7 8\nE\n"


@unittest.skipUnless(vcvars(64), "no vcvars (MSVC) on this machine")
class PaintedStack(unittest.TestCase):
    def test_unset_slots_read_the_pattern(self):
        work = TOOL / "tests" / ".work"
        work.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="gcpaint_", dir=work) as d:
            p = pathlib.Path(d)
            (p / "work.c").write_text(WORK, encoding="utf-8")
            (p / "main.dol").write_bytes(bytes(0x100))
            (p / "ranges.txt").write_text("", encoding="utf-8")
            (p / "vectors.txt").write_text(VECTORS, encoding="utf-8")
            vc = str(vcvars(64)).replace("/", "\\")
            src = " ".join(f'"{RT / n}"' for n in ("runner.c", "tc_rt.c", "tc_ppc.c"))
            r = subprocess.run(f'cmd /d /s /c ""{vc}" >nul 2>&1 && cl /nologo /O1 /fp:strict /W4 /WX /TC /I"{RT}" '
                               f'/Ferunner.exe work.c {src}"', cwd=d, capture_output=True, text=True,
                               errors="replace", timeout=300)
            self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-2000:])
            outs = []
            for k in (1, 2):
                run = subprocess.run([str(p / "runner.exe"), "main.dol", "ranges.txt", "vectors.txt", "dev",
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
        src = (RT / "runner.c").read_text(encoding="utf-8")
        self.assertLess(src.index("paint_stack();"), src.index("int code = run_one("))


if __name__ == "__main__":
    unittest.main()
