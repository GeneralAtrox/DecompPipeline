"""FPSCR as state on the SH-4 (judge gap 3). The runtimes had no FPSCR to read: Spawn 8c010080 (`sts fpscr,r4`, RM
cleared, `lds r4,fpscr`) and Toy Commander 8c0611d8 (the V, Z and O enables set or cleared) passed with C that never
touches FPSCR. Now a function whose body has an sts or lds of FPSCR gets it as state (TcFpscr), and nothing else
changes. Pass and fail controls:
1. TcFpscr (tests/java/TcFpscrStub): which instructions are float operations, sts or lds of FPSCR; the entry FPSCR is
   the project's with FLAG, CAUSE and RM drawn per vector (the same inputs give the same FPSCR; RM the run mode's,
   0 without fpscr_model and the project's with it, or the other; PR, SZ and FR kept only under fpscr_model), and
   with no sts or lds in the body the project's, unchanged; lines N, FS and FC;
2. the wiring: TcFpscr locked, its hooks in TcPrepare, TcOracle, TcExit, TcHw and TcVectors, the scripts under 400
   lines, meta fpscr_state and its context text (agent.py);
3. both judge runtimes (compiled from their source): line N sets FPSCR, sh4_lds_fpscr keeps bits 0..21, sh4_sts_fpscr
   reads it back with the FR, SZ and PR the FPU runs in, lines FS and FC, and nothing without a line N; both runners
   parse N and report FS, FC; each logged call records FPSCR; the port's runtime has the same two helpers;
4. the judge: FS and FC compared when the original has them, a wrong or missing FPSCR fails, no FS: nothing compared.
The oracle itself needs Ghidra: the re-prepares in docs/proofs/judge-gaps.md judge it.
Synthetic data. Run: python -W error tests/test_dc_fpscr.py.
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
from core import agent, compare  # noqa: E402
from test_tail import CP, SH4, TOOL, jdk  # noqa: E402

SCRIPTS = SH4 / "ghidra_scripts"


def text(p):
    return p.read_bytes().decode("utf-8").replace("\r\n", "\n")


@unittest.skipUnless(CP and jdk("javac") and jdk("java"), "no ghidra_home or JDK on this machine")
class Fpscr(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (TOOL / "tests" / ".work").mkdir(parents=True, exist_ok=True)
        cls.tmp = tempfile.TemporaryDirectory(prefix="fpscr_", dir=TOOL / "tests" / ".work")
        cls.out = pathlib.Path(cls.tmp.name)
        src = os.pathsep.join(str(SH4 / d) for d in ("ghidra_scripts", "template"))
        r = subprocess.run([jdk("javac"), "-nowarn", "-proc:none", "-d", str(cls.out), "-cp", CP, "-sourcepath", src,
                            str(TOOL / "tests" / "java" / "TcFpscrStub.java")],
                           capture_output=True, text=True, timeout=300)
        if r.returncode:
            raise AssertionError(r.stdout + r.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_stub(self, cases):
        f = self.out / "cases.txt"
        f.write_text(cases, encoding="utf-8")
        r = subprocess.run([jdk("java"), "-cp", str(self.out) + os.pathsep + CP, "TcFpscrStub", str(f)],
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return [ln.split(": ", 1)[1] for ln in r.stdout.split("\n") if ": " in ln]

    def test_kinds(self):  # as TcLoads.text gives them: Ghidra's text, lower case, no delay-slot "_"
        got = self.run_stub("""
K sts fpscr,r4
K sts.l fpscr,@-r15
K lds r4,fpscr
K lds.l @r15+,fpscr
K fadd fr1,fr2
K fcmp/gt fr4,fr5
K float fpul,fr0
K ftrc fr0,fpul
K fsca fpul,dr0
K sts fpul,r0          # FPUL is not FPSCR
K lds r1,fpul
K fmov fr1,fr2         # moves, loads of constants, abs and neg set no CAUSE
K fldi1 fr0
K fneg fr3
K fschg
""")
        self.assertEqual(got, ["2", "2", "4", "4"] + ["1"] * 5 + ["0"] * 6)

    def test_access(self):  # only sts and lds of FPSCR make it state: a float operation alone leaves the body as before
        got = self.run_stub("""
A sts fpscr,r4
A sts.l fpscr,@-r15
A lds r4,fpscr
A lds.l @r15+,fpscr
A fadd fr1,fr2
A fipr fv0,fv4
A ftrv xmtrx,fv0
A fcmp/gt fr4,fr5
A sts fpul,r0
A fschg
""")
        self.assertEqual(got, ["true"] * 4 + ["false"] * 6)

    def test_entry(self):
        rows = [f"E 0 00040001 {i:08x} 8c100000 {i * 7919:08x} 0 3f800000" for i in range(64)]
        a = self.run_stub("\n".join(rows) + "\n")
        self.assertEqual(a, self.run_stub("\n".join(rows) + "\n"))  # the vector's own: the same inputs, the same FPSCR
        vals = [int(x, 16) for x in a]
        self.assertTrue(all(v & ~0x0003f07f == 0x00040000 for v in vals))  # ENABLE and DN the project's
        self.assertGreater(len({v & 0x0003f07c for v in vals}), 32)          # FLAG and CAUSE vary
        self.assertEqual({v & 3 for v in vals}, {0, 1})                       # RM the run mode's (0) or the other
        self.assertGreater(sum(v & 3 for v in vals), 16)
        self.assertLess(sum(v & 3 for v in vals), 48)
        model = [int(x, 16) for x in self.run_stub("\n".join(r.replace("E 0", "E 1") for r in rows) + "\n")]
        self.assertEqual([v & 3 for v in model], [(v & 3) ^ 1 for v in vals])  # with fpscr_model the run mode RM=1
        modes = self.run_stub("E 0 00380001 1 2 3 4 0\nE 1 00380001 1 2 3 4 0\nO 00380001\nO 00040001\n")
        self.assertEqual(int(modes[0], 16) & 0x00380000, 0)            # PR, SZ, FR 0 without fpscr_model, as before
        self.assertEqual(int(modes[1], 16) & 0x00380000, 0x00380000)   # and the project's with it
        self.assertEqual(modes[2:], ["00380001", "00040001"])          # no sts or lds of FPSCR: unchanged

    def test_lines(self):
        got = self.run_stub("X 0004a001 00040000\nX 00040001 00040e01 00040001 00040e01\n")
        self.assertEqual(got, ["/N 0004a001|FS 00040000/|", "/N 00040001|FS 00040e01/FC 00040001 00040e01/|"])


class Wiring(unittest.TestCase):
    def test_scripts(self):
        locked = json.loads(text(SH4 / "target.json"))["locked_scripts"]
        self.assertIn("TcFpscr.java", locked)
        for name in ("TcFpscr.java", "TcOracle.java", "TcVectors.java", "TcPrepare.java", "TcExit.java", "TcHw.java"):
            self.assertLessEqual(len(text(SCRIPTS / name).splitlines()), 400, name)
        prepare = text(SCRIPTS / "TcPrepare.java")
        self.assertLess(prepare.index("body = TcTail.extent("), prepare.index("TcFpscr.on = TcFpscr.of(listing, body,"))
        self.assertIn('if (TcFpscr.on != null) extra.append(",\\n  \\"fpscr_state\\": true");', prepare)
        oracle = text(SCRIPTS / "TcOracle.java")
        self.assertIn("long fp = TcFpscr.entry(facts.fpscrEntry, v);", oracle)
        self.assertIn("if (facts.fpscrModel || TcFpscr.on != null) {", oracle)
        self.assertIn("res.fs = TcFpscr.start();", oracle)
        self.assertIn("res.calls.add(callRecord(emu, pc) + TcTrack.call(pc)); TcFpscr.call(emu);", oracle)
        self.assertIn("fpu.before(emu, pca, reject); TcFpscr.before(emu, loadView, pca, reject);", oracle)
        self.assertIn("String fpuWhy = fpu.after(emu, pca, zeroed); TcTrack.step(emu, loadView, pca, specs, pend);\n", oracle)  # no
        # rejection after an lds: modes are state
        self.assertIn("TcFpscr.exit(emu);", text(SCRIPTS / "TcExit.java"))
        self.assertIn("TcFpscr.exit(r.fs) + TcTrack.exit(r.tr);", text(SCRIPTS / "TcHw.java"))
        self.assertIn('.append(TcFpscr.line(r.fs)).append(TcFpscr.Sz.line()).append("\\nC");', text(SCRIPTS / "TcVectors.java"))

    def test_context(self):
        self.assertIn("## FPSCR is state", agent.FPSCR_STATE)
        self.assertRegex(" ".join(agent.FPSCR_STATE), r"sh4_sts_fpscr\(\).*sh4_lds_fpscr\(v\)")
        src = text(ROOT / "core" / "agent.py")
        self.assertIn('    if meta.get("fpscr_state"):\n        lines += FPSCR_STATE\n', src)

    def test_runtimes(self):
        for p in (SH4 / "runtime" / "runner.c", SH4 / "runtime_rd" / "rd_runner.c"):
            src = text(p)
            self.assertIn("} else if (line[0] == 'N') {", src, p.name)
            self.assertIn("tcrt_set_fpscr_state((u32)strtoul(line + 1, NULL, 16));", src, p.name)
            # the strict judge's two-field N must not shadow TcFpscr's one-field N (decision 1, runner.c:114)
            self.assertIn("} else if ((line[0] == 'N' && strict_key(line)) || line[0] == 'W') {", src, p.name)
            self.assertIn("tcrt_report_gbr(out);\n            tcrt_report_fpscr(out);", src, p.name)
        for p in (SH4 / "runtime" / "tc_rt.c", SH4 / "runtime_rd" / "rd_veh.c"):
            self.assertRegex(text(p), r"static void log_call\([^)]*\)\n\{\n    tcrt_fpscr_call\(\);\n", p.name)
        for p in (SH4 / "runtime" / "tc_fpu.c", SH4 / "runtime_rd" / "rd_sh4.c"):
            self.assertIn("    g_fpscr = 0;\n    g_fs = 0;\n    g_nfc = 0;\n}\n", text(p), p.name)
        for p in (SH4 / "runtime" / "tc_rt.h", SH4 / "runtime_rd" / "rd_rt.h"):
            self.assertRegex(text(p), r"\nu32 sh4_sts_fpscr\(void\);.*\nvoid sh4_lds_fpscr\(u32 value\);", p.name)
        port = text(ROOT / "port" / "runtime" / "sh4_rt.c")
        self.assertIn("u32 sh4_sts_fpscr(void) { return (g_fpscr & ~(MODE_FR | MODE_SZ | MODE_PR)) | g_mode; }", port)
        self.assertIn("void sh4_lds_fpscr(u32 v)\n{", port)
        self.assertIn("port_extra_state(&g_fpscr, sizeof g_fpscr);", port)  # saved and restored with the FPU modes


MAIN = """#include <stdio.h>
typedef unsigned int u32;
#define MODE_FR 0x00200000u
#define MODE_SZ 0x00100000u
#define MODE_PR 0x00080000u
static u32 g_mode;
%s
int main(void)
{
    FILE *out = stdout;
    tcrt_report_fpscr(out);                 /* no line N: nothing */
    tcrt_set_fpscr_state(0xffffffffu);      /* only bits 0..21 */
    printf("%%08x %%08x\\n", sh4_sts_fpscr(), g_mode);
    tcrt_report_fpscr(out);                 /* no call: FS alone */
    tcrt_fpscr_call();
    sh4_lds_fpscr(sh4_sts_fpscr() & ~0x003c0003u);
    printf("%%08x %%08x\\n", sh4_sts_fpscr(), g_mode);
    tcrt_fpscr_call();
    g_mode = MODE_PR;                       /* fpchg: sts reads the FPU's PR */
    tcrt_report_fpscr(out);
    return 0;
}
"""


@unittest.skipUnless(os.name == "nt" and game_config.vcvars32(), "no 32-bit Visual Studio tools on this machine")
class FpscrWord(unittest.TestCase):
    def test_both(self):
        work = ROOT / "tests" / ".work"
        d = pathlib.Path(tempfile.mkdtemp(dir=work if work.is_dir() else None))
        try:
            vc = str(game_config.vcvars32()).replace("/", "\\")
            for p in (SH4 / "runtime" / "tc_fpu.c", SH4 / "runtime_rd" / "rd_sh4.c"):
                block = re.search(r"#define FPSCR_BITS .*?void tcrt_report_fpscr\(FILE \*out\)\n\{.*?\n\}\n", text(p),
                                  re.S).group(0)
                (d / "p.c").write_text(MAIN % block, encoding="utf-8")
                r = subprocess.run(f'cmd /d /s /c ""{vc}" >nul 2>&1 && cl /nologo /W4 /WX p.c"', cwd=d,
                                   capture_output=True, text=True, errors="replace", timeout=300)
                self.assertEqual(r.returncode, 0, p.name + r.stdout[-2000:])
                out = subprocess.run([str(d / "p.exe")], capture_output=True, text=True, timeout=60)
                self.assertEqual(out.stdout.split("\n")[:5], [
                    "003fffff 00380000", "FS 003fffff",
                    "0003fffc 00000000",
                    "FS 000bfffc", "FC 003fffff 0003fffc"], p.name)
        finally:
            shutil.rmtree(d, ignore_errors=True)


KEY = """#include <stdio.h>
#include <stdlib.h>
%s
int main(void)
{
    static const char *lines[] = {"N 0006e074\\n", "N c0611d8 2\\n", "N 6e074 \\n", "N 1 0\\n", "N 40001"};
    for (int i = 0; i < 5; i++) printf("%%d", strict_key(lines[i]));
    return 0;
}
"""


@unittest.skipUnless(os.name == "nt" and game_config.vcvars32(), "no 32-bit Visual Studio tools on this machine")
class NLines(unittest.TestCase):
    def test_strict_key(self):  # TcStrict's "N seed vid" goes to the strict judge, TcFpscr's "N fpscr" sets FPSCR
        work = ROOT / "tests" / ".work"
        d = pathlib.Path(tempfile.mkdtemp(dir=work if work.is_dir() else None))
        try:
            vc = str(game_config.vcvars32()).replace("/", "\\")
            for p in (SH4 / "runtime" / "runner.c", SH4 / "runtime_rd" / "rd_runner.c"):
                src = text(p)
                block = re.search(r"static int hexval\(int c\)\n\{.*?\n\}\n", src, re.S).group(0) + re.search(
                    r"static int strict_key\(const char \*line\)\n\{.*?\n\}\n", src, re.S).group(0)
                (d / "k.c").write_text(KEY % block, encoding="utf-8")
                r = subprocess.run(f'cmd /d /s /c ""{vc}" >nul 2>&1 && cl /nologo /W4 /WX k.c"', cwd=d,
                                   capture_output=True, text=True, errors="replace", timeout=300)
                self.assertEqual(r.returncode, 0, p.name + r.stdout[-2000:])
                out = subprocess.run([str(d / "k.exe")], capture_output=True, text=True, timeout=60)
                self.assertEqual(out.stdout, "01010", p.name)
        finally:
            shutil.rmtree(d, ignore_errors=True)


def result(fpscr=None, calls=None):
    out = {"done": True, "faults": [], "r0": 0, "fr0": 0, "writes": {}, "calls": [], "xf": [0] * 16, "cov": set()}
    if fpscr is not None:
        out["fpscr"] = fpscr
    if calls is not None:
        out["fpscr_calls"] = calls
    return out


class Judge(unittest.TestCase):
    def judge(self, exp, act):
        return compare.compare_vector({"ret": "void", "callees": []}, b"", {"patches": []}, exp, act)

    def test_parse(self):
        with tempfile.TemporaryDirectory() as t:
            p = pathlib.Path(t) / "expected.txt"
            p.write_text("V 0 dev\nRET 0 0\nFS 00040000\nFC 0004a001 00040000\nE\nV 1 dev\nRET 0 0\nE\n",
                         encoding="utf-8")
            res = compare.parse_results(p)
        self.assertEqual((res[0].get("fpscr"), res[0].get("fpscr_calls")), (0x00040000, [0x0004a001, 0x00040000]))
        self.assertEqual((res[1].get("fpscr"), res[1].get("fpscr_calls")), (None, None))

    def test_exit(self):
        self.assertEqual(self.judge(result(0x00040000), result(0x00040000)), [])
        self.assertTrue(self.judge(result(0x00040000), result(0x00040001)))  # RM left set (Spawn's matched C)
        self.assertTrue(self.judge(result(0x00040e01), result()))            # no FS: the C never wrote FPSCR state
        self.assertEqual(self.judge(result(), result()), [])
        self.assertEqual(self.judge(result(), result(0x00040001)), [])       # no sts / lds in the original

    def test_calls(self):
        exp = result(0x00040000, [0x0004a001, 0x00040000])
        self.assertEqual(self.judge(exp, result(0x00040000, [0x0004a001, 0x00040000])), [])
        self.assertTrue(self.judge(exp, result(0x00040000, [0x0004a001, 0x00040001])))  # written after, not before
        self.assertTrue(self.judge(exp, result(0x00040000, [0x0004a001])))              # a call short
        self.assertTrue(self.judge(result(0x00040000), result(0x00040000, [0x00040000])))  # a call too many


if __name__ == "__main__":
    unittest.main()
