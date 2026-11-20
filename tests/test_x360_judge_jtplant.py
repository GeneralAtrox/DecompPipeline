"""The plant for the oracle's switch tables (TcJumpTables): a translated switch with one wrong case above index 0 FAILs
once the oracle keeps the vectors that take it, where it PASSed before.

MSVC puts a switch's table in the code range right after the bctr. Before TcJumpTables, the oracle (TcBank) took a
read within 16 bytes of the pc for an instruction fetch and any other read of the code range for R8, so only index 0
(its entry 12 bytes after the table load) kept its vector: a wrong case at index 1 was never run by the judge.
  - Plant (no tools): dp_judge's --plant (dp_judge.plant) applies PLANT to the translated switch of
    tests/test_x360_table_in_code.py's shape once, on case 1 only.
  - Judge (javac, java and cl): which indices keep their vector comes from the oracle's own bank (X360TrackStub's J
    case: TcBank with the table resolved), today's rule worked out beside it; the translated C, planted and not, is
    built and run on one vector per index and compared (x360_compare). Planted: PASS on today's vectors, FAIL on the
    kept ones. Not planted: PASS on both.
The same plant on Too Human's functions through dp_judge --plant: DESIGN.md "Switch tables in code".
usage: python tests/test_x360_judge_jtplant.py
"""
import os
import shutil
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_x360_judge_exit import BASE, translate  # noqa: E402
from test_x360_judge_gaps import RT, SCRIPTS, SHARED, X360, cl, cmp, vcvars64, x360_check  # noqa: E402
from test_tail import CP, jdk  # noqa: E402
from test_xbox360_ppc import TOOL, tmpdir  # noqa: E402

sys.path.insert(0, str(X360 / "translator" / "tools"))
import dp_judge  # noqa: E402

# one wrong case above index 0: case 1 also stores to a heap word the original never writes (any switch's C, by regex)
PLANT = [r"(case 0x1u:)", r"\1 wr32(0x40080000u, ~rd32(0x40080000u));", "switch case 1"]
TABLE = BASE + 0x20
WORDS = [0x2B030002,                    # cmplwi cr6,r3,2
         0x41990040,                    # bgt cr6,+0x40: return 0
         0x3D800000 | (TABLE >> 16),    # lis r12,table@h
         0x398C0000 | (TABLE & 0xFFFF),  # addi r12,r12,table@l
         0x5460103A,                    # rlwinm r0,r3,2,0,29
         0x7C0C002E,                    # lwzx r0,r12,r0: the table load, at +0x14
         0x7C0903A6,                    # mtctr r0
         0x4E800420,                    # bctr
         BASE + 0x2C, BASE + 0x34, BASE + 0x3C,  # +0x20: the table, in the function's code
         0x38600001, 0x4E800020,        # li r3,1; blr
         0x38600002, 0x4E800020,        # li r3,2; blr
         0x38600003, 0x4E800020,        # li r3,3; blr
         0x38600000, 0x4E800020]        # li r3,0; blr
INDICES = range(6)
META = {"name": "fn_82010000", "ret": "u64", "signature": "u64 fn_82010000(u64 p, u64 q)", "callees": [],
        "params": [{"name": "p", "kind": "u64", "storage": "r3"}, {"name": "q", "kind": "u64", "storage": "r4"}]}


def source():
    return translate(WORDS, "fn_82010000")


def want(i):
    return i + 1 if i <= 2 else 0


class Plant(unittest.TestCase):
    def test_plant_hits_case_one_only(self):
        src = source()
        self.assertIn("switch (sw_)", src)      # the switch on the index (xenontranslate/jtab.py), no table read
        self.assertNotIn("rd32(0x82010", src)
        got, n = dp_judge.plant(src, PLANT)
        self.assertEqual(n, 1)
        self.assertEqual(got.replace("case 0x1u: wr32(0x40080000u, ~rd32(0x40080000u));", "case 0x1u:"), src)


def kept_by_oracle(out):
    """{index: kept} from the oracle's bank: index i's table load reads table + 4 * i (stub function at 82000000)."""
    cases = "".join(f"J 82000014 {0x82000020 + 4 * i:08x} 4\n" for i in INDICES if i <= 2)
    f = out / "cases.txt"
    f.write_text(cases, encoding="utf-8")
    r = subprocess.run([jdk("java"), "-Xmx256m", "-cp", str(out) + os.pathsep + CP, "X360TrackStub", str(f)],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    got = [ln.split(": ", 1)[1] for ln in r.stdout.splitlines()]
    return {i: i > 2 or got[i] == "data" for i in INDICES}  # an index above the bound reads no table


def kept_today(i):
    return i > 2 or 0x20 + 4 * i < 0x14 + 16  # the rule before: only a read inside the fetch window was no R8


@unittest.skipUnless(CP and jdk("javac") and jdk("java") and vcvars64(), "needs ghidra_home, a JDK and cl")
class Judge(unittest.TestCase):
    def run_c(self, d, src):
        (d / "work.c").write_text(src, encoding="utf-8")
        srcs = " ".join(f'"{RT / s}"' for s in x360_check.SOURCES)
        rc, out = cl(d, f'/DTC_FMA_FUSED=0 /I"{RT}" /Ferunner.exe work.c thunk.c {srcs}')
        self.assertEqual(rc, 0, out[-3000:])
        run = subprocess.run([str(d / "runner.exe"), "image.bin", "code.txt", "vectors.txt", "all", "out.txt"],
                             cwd=d, capture_output=True, text=True, timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr)
        return cmp.parse_results(d / "out.txt")

    def verdict(self, got, kept):
        bad = []
        for i in INDICES:
            if kept[i]:
                exp = {"done": True, "faults": [], "r3": want(i), "f1": 0, "writes": {}, "calls": [], "cov": set()}
                bad += cmp.compare_vector(META, bytes(0x100), {"patches": []}, exp, got[i])
        return "FAIL" if bad else "PASS"

    def test_wrong_case_fails_once_kept(self):
        d = tmpdir("x360jt_")
        try:
            src = os.pathsep.join(str(p) for p in (SCRIPTS, SHARED, X360 / "template"))
            r = subprocess.run([jdk("javac"), "-nowarn", "-proc:none", "-J-Xmx512m", "-d", str(d), "-cp", CP,
                                "-sourcepath", src, str(TOOL / "tests" / "java" / "X360TrackStub.java")],
                               capture_output=True, text=True, timeout=300)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            kept = kept_by_oracle(d)
            today = {i: kept_today(i) for i in INDICES}
            self.assertEqual([i for i in INDICES if not today[i]], [1, 2])     # dropped before
            self.assertEqual([i for i in INDICES if not kept[i]], [])          # kept now
            (d / "thunk.c").write_text(x360_check.gen_thunk(META), encoding="utf-8")
            (d / "image.bin").write_bytes(bytes(0x10400))
            (d / "code.txt").write_text("82010000 8201004c\n", encoding="utf-8")
            (d / "vectors.txt").write_text("".join(f"V {i} dev\nR {i:x} 0 0 0 0 0 0 0\nF {' '.join(['0'] * 13)}\nE\n"
                                                   for i in INDICES), encoding="utf-8")
            clean = self.run_c(d, source())
            planted, n = dp_judge.plant(source(), PLANT)
            self.assertEqual(n, 1)
            wrong = self.run_c(d, planted)
        finally:
            shutil.rmtree(d, ignore_errors=True)
        self.assertEqual((self.verdict(clean, today), self.verdict(clean, kept)), ("PASS", "PASS"))
        self.assertEqual((self.verdict(wrong, today), self.verdict(wrong, kept)), ("PASS", "FAIL"))


if __name__ == "__main__":
    unittest.main()
