"""Dreamcast and Saturn: the runner paints the stack before each vector, as the GameCube runner does
(tests/test_gc_paint.py). Matched C that fills a TcArgs only in part leaves its other slots as whatever was on the
host stack. With the strict flags on that was the runner's own leftovers, and by luck the expected 0: Grandia
0605a282's two mutants without `a.r[1] = 0` passed with the flags on and failed with them off (the pre-wave proof's
two lost kills). The test builds the real runner (runner.c, tc_rt.c, tc_fpu.c, tc_p4.c) with check.CL_FLAGS and a
function that sets only r4 of its TcArgs, runs two vectors twice without the strict header and once with it, and
checks:
  - r5..r7 and s0..s7 of each call read the pattern;
  - the two runs' outputs are identical, and the call lines are the same with the flags on.
Skipped without config.local.json's vcvars (MSVC).
usage: python tests/test_sh_paint.py
"""
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
from test_sync import TMP  # noqa: E402,F401  (sets DECOMP_PROJECT when run alone)
from core import check, toolcfg  # noqa: E402

VC64 = toolcfg.load(ROOT).get("vcvars")
WORK = """#include "tc_rt.h"
void fn_test(void);
void fn_test(void)
{
    TcArgs a;
    a.r[0] = 7;
    (void)tc_call(0x%(target)08xu, &a);
}

void tc_thunk(const u32 *r, const f32 *fr, u32 *out_r0, f32 *out_fr0);
void tc_thunk(const u32 *r, const f32 *fr, u32 *out_r0, f32 *out_fr0)
{
    (void)r;
    (void)fr;
    (void)out_r0;
    (void)out_fr0;
    fn_test();
}
"""
TARGETS = {"dreamcast_sh4": (0x8C020000, 0x8C010000), "saturn_sh2": (0x06020000, 0x06000000)}  # callee, code
VECTORS = "V 0 dev\nR 0 0 0 0\nE\nV 1 dev\nR 1 2 3 4\nE\n"
STRICT = "J 15\nV 0 dev\nN 1234 7\nR 0 0 0 0\nE\nV 1 dev\nN 1234 8\nR 1 2 3 4\nE\n"


@unittest.skipUnless(os.name == "nt" and VC64 and pathlib.Path(VC64).is_file(), "no Visual Studio on this machine")
class PaintedStack(unittest.TestCase):
    def test_unset_slots_read_the_pattern(self):
        work = ROOT / "tests" / ".work"
        work.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="shpaint_", dir=work) as d:
            d = pathlib.Path(d)
            cmds = []
            for t, (target, _code) in TARGETS.items():
                b = d / t
                b.mkdir()
                (b / "work.c").write_text(WORK % {"target": target}, encoding="utf-8")
                rt = ROOT / "targets" / t / "runtime"
                cmds.append(f'cd /d "{b}" && cl {" ".join(check.CL_FLAGS)} /TC /I"{rt}" /Fe:runner.exe work.c '
                            + " ".join(f'"{rt / s}"' for s in ("runner.c", "tc_rt.c", "tc_fpu.c", "tc_p4.c")))
            vc = str(VC64).replace("/", "\\")
            r = subprocess.run(f'cmd /d /s /c ""{vc}" >nul 2>&1 && ' + " && ".join(cmds) + '"', capture_output=True,
                               text=True, errors="replace", timeout=600)
            self.assertEqual(r.returncode, 0, r.stdout[-3000:])
            (d / "image.bin").write_bytes(bytes(0x100))
            (d / "fsca.bin").write_bytes(bytes(1 << 20))
            for t, (target, code) in TARGETS.items():
                b = d / t
                (b / "code.txt").write_text(f"{code:08x} {code + 0x100:08x}\n")
                outs = []
                for k, vecs in enumerate((VECTORS, VECTORS, STRICT)):
                    (b / "vectors.txt").write_text(vecs)
                    run = subprocess.run([str(b / "runner.exe"), str(d / "image.bin"), str(b / "code.txt"),
                                          str(d / "fsca.bin"), str(b / "vectors.txt"), "all", str(b / f"out{k}.txt")],
                                         capture_output=True, text=True, timeout=60)
                    self.assertEqual(run.returncode, 0, t + run.stderr)
                    outs.append((b / f"out{k}.txt").read_text(encoding="utf-8"))
                self.assertEqual(outs[0], outs[1], t)
                calls = [[ln.split() for ln in o.splitlines() if ln.startswith("CALL")] for o in outs]
                self.assertEqual(calls[2], calls[0], t)  # the strict flags change no call line
                self.assertEqual(len(calls[0]), 2, t)
                for c in calls[0]:
                    self.assertEqual(c[1:3], [f"{target:08x}", "00000007"], t)
                    self.assertEqual(c[3:6], ["a5a5a5a5"] * 3, t)    # r5..r7
                    self.assertEqual(c[14:22], ["a5a5a5a5"] * 8, t)  # s0..s7

    def test_wiring(self):
        for t in TARGETS:
            src = (ROOT / "targets" / t / "runtime" / "runner.c").read_text(encoding="utf-8")
            self.assertLess(src.index("paint_stack();"), src.index("int code = run_one("), t)


if __name__ == "__main__":
    unittest.main()
