"""The Xbox 360 judge with registers read before they are set (xenontranslate/exit_form.seed_judge): a function that
reads r5 or f1 outside its signature, r12 (a funclet's parent frame) or the CR (mfcr) is translated in the judge's form
(it was refused before), reads them at entry from the vector (TcOracle.setEntry seeds r3..r10 and f1..f13 from
vectors.txt R and F, and r12 and the CR fields a function reads first from A; the runner's tcx_begin takes the same
lines), and its C, built with the judge runtime and run by runner.exe, gives the result worked out from the PowerPC
ISA. Planted wrong C is rejected: the register read as 0 (the old silent form) and the register off by one.
Needs cl (config.local.json vcvars), so it runs on build box A or build box B.
usage: python tests/test_x360_judge_seed.py
"""
import pathlib
import re
import shutil
import struct
import subprocess
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_x360_judge_exit import DISPATCH, RT, THUNK_META, translate  # noqa: E402
from test_xbox360_glue import cl, vcvars64  # noqa: E402
from test_xbox360_ppc import tmpdir  # noqa: E402
import x360_check  # noqa: E402
import x360_compare as cmp  # noqa: E402

F1 = 0.1
F1_BITS = struct.unpack(">Q", struct.pack(">d", F1))[0]
R12, CR = 0x40001234, 0x9ABCDEF1   # the vectors' A line (entry seeds): r12 (32 bits, as TcArgs.r12) and the CR
PLANTS = {   # name: (words, the vector's R line r3..r9 (r10 is the dispatch), the r3 the ISA gives)
    "r": ([0x7C632A14,      # add r3,r3,r5: r5 is no parameter
           0x4E800020],
          (0x100, 0, 0x2000), 0x2100),
    "f": ([0xD8230000,      # stfd f1,0(r3): f1 is no parameter; r3 points into the stack area, below the frame
           0xE8630000,      # ld r3,0(r3): f1's bits
           0x4E800020],
          (0x700FD000, 0, 0), F1_BITS),
    "r12": ([0x7C636214,    # add r3,r3,r12: r12 is no parameter (a funclet's parent frame)
             0x4E800020],
            (0x100, 0, 0), 0x100 + R12),
    "cr": ([0x7C600026,     # mfcr r3: every CR field read before it is set (a CR save)
            0x4E800020],
           (0, 0, 0), CR),
}
MUTANTS = {   # name: (plant, pattern, replacement, how many: 1, or 0 for every one)
    "r5 as 0": ("r", r"tcx_in_r\((0x)?5u\)", "0ull", 1),
    "r5 off by one": ("r", r"tcx_in_r\((0x)?5u\)", r"(tcx_in_r(5u) + 1ull)", 1),
    "f1 as 0": ("f", r"tcx_in_f\((0x)?1u\)", "0.0", 1),
    "f1 off by one": ("f", r"tcx_in_f\((0x)?1u\)", r"(tcx_in_f(1u) + 1.0)", 1),
    "r12 as 0": ("r12", r"tcx_in_r\((0xc|12)u\)", "0ull", 1),
    "r12 off by one": ("r12", r"tcx_in_r\((0xc|12)u\)", r"(tcx_in_r(12u) + 1ull)", 1),
    "cr as 0": ("cr", r"tcx_in_cr\((0x)?[0-9a-f]+u\)", "0u", 0),
    "cr0.lt as 0": ("cr", r"tcx_in_cr\((0x)?0u\)", "0u", 1),
}


class Seeded(unittest.TestCase):
    def test_judged_and_plants_rejected(self):
        if not vcvars64():
            print("SKIPPED: needs cl")
            self.skipTest("needs cl (config.local.json vcvars)")
        runs = [(k, k, None) for k in PLANTS] + [(f"m{i}", MUTANTS[m][0], m) for i, m in enumerate(MUTANTS)]
        d = tmpdir("x360seed_")
        try:
            files = []
            for fn, k, m in runs:
                src = translate(PLANTS[k][0], f"seed_{fn}")       # the judge's form (no port, no exit state)
                self.assertRegex(src, r"tcx_in_(r|f|cr)\(")
                if m:
                    want = MUTANTS[m][3]
                    src, n = re.subn(MUTANTS[m][1], MUTANTS[m][2], src, count=want)
                    self.assertTrue(n == want or want == 0 and n > 1, f"{m}: {n} in\n{src}")
                (d / f"seed_{fn}.c").write_text(src, encoding="utf-8")
                files.append(f"seed_{fn}.c")
            (d / "work.c").write_text(DISPATCH.format(
                decls="".join(f"u64 seed_{fn}(u64 p, u64 q);\n" for fn, _, _ in runs),
                cases="".join(f"    case {z}: return seed_{fn}(p, q);\n" for z, (fn, _, _) in enumerate(runs))),
                encoding="utf-8")
            (d / "thunk.c").write_text(x360_check.gen_thunk(THUNK_META), encoding="utf-8")
            srcs = " ".join(f'"{RT / s}"' for s in x360_check.SOURCES)
            rc, out = cl(d, f'/DTC_FMA_FUSED=0 /I"{RT}" /Ferunner.exe work.c thunk.c {" ".join(files)} {srcs}')
            self.assertEqual(rc, 0, out[-3000:])
            (d / "image.bin").write_bytes(bytes(0x10400))
            (d / "code.txt").write_text("82010000 82010010\n", encoding="utf-8")
            vecs = []
            for z, (fn, k, _) in enumerate(runs):
                p, q, r5 = PLANTS[k][1]
                vecs.append(f"V {z} dev\nR {p:x} {q:x} {r5:x} 0 0 0 0 {z:x}\nF {F1_BITS:x} {' '.join(['0'] * 12)}\n"
                            f"A {R12:x} {CR:x}\nE\n")
            (d / "vectors.txt").write_text("".join(vecs), encoding="utf-8")
            run = subprocess.run([str(d / "runner.exe"), "image.bin", "code.txt", "vectors.txt", "all", "out.txt"],
                                 cwd=d, capture_output=True, text=True, timeout=60)
            self.assertEqual(run.returncode, 0, run.stderr)
            got = cmp.parse_results(d / "out.txt")
        finally:
            shutil.rmtree(d, ignore_errors=True)
        for z, (fn, k, m) in enumerate(runs):
            with self.subTest(fn=fn, mutant=m):
                want = PLANTS[k][2]
                if m is None:
                    self.assertEqual(got[z]["r3"], want)
                else:
                    self.assertNotEqual(got[z]["r3"], want)


if __name__ == "__main__":
    unittest.main()
