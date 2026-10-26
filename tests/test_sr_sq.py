"""SR and store-queue-only functions takeable (proof-gates item 3). TcCatalog counted any ldc/stc as sys_ops and any
constant at 0xe0000000 or above as hw_refs, and orch.tier excludes either, so Spawn's serial driver (8c05bbe4..
8c05c338: the mask raised to 15 around its buffer updates) and its store-queue draw calls (8c0515c0, 8c057010, ...)
were never queued, though the oracle models the store queues (TcSq) and now SR (TcSr). Pass and fail controls:
1. the catalog: `stc sr` / `ldc sr` (and .l) count sr_ops, every other control-register op still sys_ops; the
   store-queue window and QACR0/1 count sq_refs, every other hardware address still hw_refs (tests/java/TcSrStub);
2. orch.tier: sr_ops or sq_refs alone make a function hard; with any hw_refs or sys_ops it stays excluded;
3. the wiring: TcSr locked, SR set at entry and checked after each step (TcOracle), the mask read at exit (TcExit),
   lines I and IM (TcVectors), sh4_special stc sr / ldc sr with their helpers (TcPrepare, agent.py);
4. the vector's mask is its own: the same inputs give the same mask, and the masks cover 0..15;
5. both runtimes' SR word (compiled from their source): line I sets MD=1 and the mask, sh4_ldc_sr keeps only the
   SR bits an ldc writes, line IM reports the mask, and nothing without a line I; both runners parse I and report IM;
6. the judge: IM compared when the original has it, a wrong or missing mask fails, no IM: nothing compared.
The oracle itself needs Ghidra: the re-catalog and the re-prepare of the 16 functions judge it.
Synthetic data. Run: python -W error tests/test_sr_sq.py.
"""
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "port" / "tools"))
import game_config  # noqa: E402
from test_sync import TMP  # noqa: E402,F401  (sets DECOMP_PROJECT when run alone)
from core import agent, compare, orch  # noqa: E402
from test_tail import CP, SH4, TOOL, jdk  # noqa: E402

SCRIPTS = SH4 / "ghidra_scripts"


def text(p):
    return p.read_bytes().decode("utf-8").replace("\r\n", "\n")


@unittest.skipUnless(CP and jdk("javac") and jdk("java"), "no ghidra_home or JDK on this machine")
class Catalog(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (TOOL / "tests" / ".work").mkdir(parents=True, exist_ok=True)
        cls.tmp = tempfile.TemporaryDirectory(prefix="srsq_", dir=TOOL / "tests" / ".work")
        cls.out = pathlib.Path(cls.tmp.name)
        src = os.pathsep.join(str(SH4 / d) for d in ("ghidra_scripts", "template"))
        r = subprocess.run([jdk("javac"), "-nowarn", "-proc:none", "-d", str(cls.out), "-cp", CP, "-sourcepath", src,
                            str(TOOL / "tests" / "java" / "TcSrStub.java")],
                           capture_output=True, text=True, timeout=300)
        if r.returncode:
            raise AssertionError(r.stdout + r.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_stub(self, cases):
        f = self.out / "cases.txt"
        f.write_text(cases, encoding="utf-8")
        r = subprocess.run([jdk("java"), "-cp", str(self.out) + os.pathsep + CP, "TcSrStub", str(f)],
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return [ln.split(": ")[1] for ln in r.stdout.split("\n") if ": " in ln]

    def test_sr_ops(self):  # as TcCatalog sees them: Ghidra's text, lower case
        got = self.run_stub("""
O ldc r0,sr
O ldc.l @r15+,sr
O stc sr,r0
O stc.l sr,@-r15
O ldc r0,gbr        # sys_ops still: GBR, SSR, SPC, VBR, DBR, SGR, banked registers
O stc ssr,r1
O ldc r2,ssr
O stc.l spc,@-r15
O ldc r1,r0_bank
O stc gbr,r0
""")
        self.assertEqual(got, ["true"] * 4 + ["false"] * 6)

    def test_sq(self):
        got = self.run_stub("Q e0000000\nQ e3ffffe0\nQ ff000038\nQ ff00003c\n"  # the window, QACR0/1
                            "Q e4000000\nQ ff000030\nQ a05f8000\nQ dfffffff\nQ ff800000\n")  # other hardware
        self.assertEqual(got, ["true"] * 4 + ["false"] * 5)

    def test_mask_is_the_vectors(self):
        rows = [f"M {i:08x} 8c100000 {i * 7919:08x} 0 3f800000" for i in range(256)]
        a = self.run_stub("\n".join(rows) + "\n")
        self.assertEqual(a, self.run_stub("\n".join(rows) + "\n"))
        self.assertEqual(set(a), {f"{k:x}" for k in range(16)})


class Tier(unittest.TestCase):
    def row(self, **kw):
        f = {k: 0 for k in ("insns", "cyclo", "fpu_special", "fp_mode", "jump_tables", "indirect_calls", "callees",
                            "globals", "hw_refs", "sys_ops")}
        f.update(insns=20, cyclo=2)
        f.update(kw)
        return f

    def test_tiers(self):
        self.assertEqual(orch.tier(self.row()), "easy")
        self.assertEqual(orch.tier(self.row(sr_ops=2)), "hard")
        self.assertEqual(orch.tier(self.row(sq_refs=3)), "hard")
        self.assertEqual(orch.tier(self.row(sr_ops=2, hw_refs=1)), "excluded")
        self.assertEqual(orch.tier(self.row(sq_refs=1, sys_ops=1)), "excluded")
        self.assertEqual(orch.tier(self.row(hw_refs=1)), "excluded")  # an older catalog: no sr_ops / sq_refs keys


class Wiring(unittest.TestCase):
    def test_scripts(self):
        self.assertIn("TcSr.java", json.loads(text(SH4 / "target.json"))["locked_scripts"])
        for name in ("TcSr.java", "TcOracle.java", "TcVectors.java", "TcCatalog.java", "TcPrepare.java"):
            self.assertLessEqual(len(text(SCRIPTS / name).splitlines()), 400, name)
        oracle = text(SCRIPTS / "TcOracle.java")
        self.assertLess(oracle.index("if (sr != null) sr.enter(emu, v);"),
                        oracle.index("emu.writeRegister(emu.getPCRegister(), u(fn.getEntryPoint().getOffset()));"))
        fault = oracle.index("if (!stepped || fault[0] != null) {")
        self.assertLess(fault, oracle.index("String srWhy = sr == null ? null : sr.after(emu, pc);"))
        self.assertIn("if (o.sr != null) res.imask = TcSr.exitMask(emu);", text(SCRIPTS / "TcExit.java"))
        vectors = text(SCRIPTS / "TcVectors.java")
        self.assertIn('vout.append(TcSr.line(sr, v)).append(TcFpscr.line(r.fs)).append("\\nC");', vectors)  # I, N
        self.assertIn(".append('\\n').append(TcHw.exit(r));", vectors)  # IM first (TcSr.exit), then GB
        self.assertIn("return TcSr.exit(r.imask) + (r.gbr < 0 ?", text(SCRIPTS / "TcHw.java"))
        prepare = text(SCRIPTS / "TcPrepare.java")
        self.assertIn("sr = TcSr.of(listing, body);", prepare)
        self.assertIn("if (TcSr.op(in) != null) sh4Special.add(TcSr.op(in));", prepare)
        self.assertEqual((agent.SPECIAL_HELPER["stc sr"], agent.SPECIAL_HELPER["ldc sr"]), ("sh4_stc_sr", "sh4_ldc_sr"))

    def test_runtimes(self):
        for p in (SH4 / "runtime" / "runner.c", SH4 / "runtime_rd" / "rd_runner.c"):
            src = text(p)
            self.assertIn("} else if (line[0] == 'I') {", src, p.name)
            self.assertIn("tcrt_set_sr((u32)strtoul(line + 2, NULL, 16));", src, p.name)
            self.assertIn("tcrt_report(out, r0, frbits);\n            tcrt_report_sr(out);", src, p.name)
        self.assertIn("g_sr = 0;\n    g_has_sr = 0;", text(SH4 / "runtime" / "tc_rt.c"))
        self.assertIn("g_sr = 0;\n    g_has_sr = 0;", text(SH4 / "runtime_rd" / "rd_sh4.c"))
        for p in (SH4 / "runtime" / "tc_rt.h", SH4 / "runtime_rd" / "rd_rt.h"):
            self.assertRegex(text(p), r"\nu32 sh4_stc_sr\(void\);.*\nvoid sh4_ldc_sr\(u32 value\);", p.name)
        port = text(ROOT / "port" / "runtime" / "sh4_rt.c")
        self.assertIn("u32 sh4_stc_sr(void) { return g_sr; }", port)
        self.assertIn("void sh4_ldc_sr(u32 v) { g_sr = v & 0x700083f3u; }", port)


MAIN = """#include <stdio.h>
typedef unsigned int u32;
%s
int main(void)
{
    FILE *out = stdout;
    tcrt_report_sr(out);                    /* no line I: nothing */
    tcrt_set_sr(5);
    printf("%%08x\\n", sh4_stc_sr());
    tcrt_report_sr(out);
    sh4_ldc_sr(sh4_stc_sr() | 0xfffffff0u); /* mask 15: only the bits an ldc writes stay */
    printf("%%08x\\n", sh4_stc_sr());
    tcrt_report_sr(out);
    sh4_ldc_sr(0x400000a0u);
    tcrt_report_sr(out);
    return 0;
}
"""


@unittest.skipUnless(os.name == "nt" and game_config.vcvars32(), "no 32-bit Visual Studio tools on this machine")
class SrWord(unittest.TestCase):
    def test_both(self):
        work = ROOT / "tests" / ".work"
        d = pathlib.Path(tempfile.mkdtemp(dir=work if work.is_dir() else None))
        try:
            vc = str(game_config.vcvars32()).replace("/", "\\")
            for p in (SH4 / "runtime" / "tc_rt.c", SH4 / "runtime_rd" / "rd_sh4.c"):
                block = re.search(r"static u32 g_sr;\n.*?void tcrt_report_sr\(FILE \*out\)\n\{.*?\n\}\n", text(p),
                                  re.S).group(0)
                (d / "p.c").write_text(MAIN % block, encoding="utf-8")
                r = subprocess.run(f'cmd /d /s /c ""{vc}" >nul 2>&1 && cl /nologo /W4 /WX p.c"', cwd=d,
                                   capture_output=True, text=True, errors="replace", timeout=300)
                self.assertEqual(r.returncode, 0, p.name + r.stdout[-2000:])
                out = subprocess.run([str(d / "p.exe")], capture_output=True, text=True, timeout=60)
                self.assertEqual(out.stdout.split("\n")[:5], ["40000050", "IM 5", "700083f0", "IM f", "IM a"], p.name)
        finally:
            shutil.rmtree(d, ignore_errors=True)


def result(imask=None):
    out = {"done": True, "faults": [], "r0": 0, "fr0": 0, "writes": {}, "calls": [], "xf": [0] * 16, "cov": set()}
    if imask is not None:
        out["imask"] = imask
    return out


class Judge(unittest.TestCase):
    def judge(self, exp, act):
        return compare.compare_vector({"ret": "void", "callees": []}, b"", {"patches": []}, exp, act)

    def test_parse(self):
        with tempfile.TemporaryDirectory() as t:
            p = pathlib.Path(t) / "expected.txt"
            p.write_text("V 0 dev\nRET 0 0\nIM f\nE\nV 1 dev\nRET 0 0\nE\n", encoding="utf-8")
            res = compare.parse_results(p)
        self.assertEqual((res[0].get("imask"), res[1].get("imask")), (15, None))

    def test_mask(self):
        self.assertEqual(self.judge(result(3), result(3)), [])
        self.assertTrue(self.judge(result(3), result(15)))  # the mask left raised
        self.assertTrue(self.judge(result(3), result()))    # no IM from the C
        self.assertEqual(self.judge(result(), result()), [])
        self.assertEqual(self.judge(result(), result(7)), [])  # the original has no SR access: nothing compared


if __name__ == "__main__":
    unittest.main()
