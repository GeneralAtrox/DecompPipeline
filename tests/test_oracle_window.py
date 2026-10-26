"""The producers' stack window (r15): the oracle and both runners log stores below the original's own frame.

The judge compares writes below frame_lo() (tests/test_judge_window.py), but the oracle (TcExit) and both runners
(tc_rt.c, rd_mem.c) cut their W lines at STACK_LO (0x8cff0000), and the input rules (TcProvenance, TcReach, TcCount)
took all of [STACK_LO, SP) as the own frame. No harness held a store to Spawn's save data (0x8cff9000) and no vector
varied a load from it. One planted miss and one control each:
1. TcExit.logged (stub, tests/java/TcFrameStub.java): a store to 0x8cff9000 is logged, the own frame's is not, a
   named global's word in the frame is; TcProvenance.ownFrame (TcReach's rule too) takes the frame from frameLo;
   TcFacts reads "frame_lo" and refuses one outside the stack window;
2. the facts file carries frame_lo when the project sets readable_frame_max, the judge's own bound;
3. no input rule keeps the old bound: TcCount, TcReach and TcProvenance read TcBase.frameLo;
4. both runners' own W report (compiled from their source) logs 0x8cff9000, and the frame word too (the judge leaves
   it out: core/compare.judged).
The oracle runs themselves need Ghidra: the proof restamp of Spawn's save-data writers judges them.
Synthetic data. Run: python -W error tests/test_oracle_window.py (also from test_tooling.py).
"""
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "port" / "tools"))
from test_sync import TMP  # noqa: E402,F401  (sets DECOMP_PROJECT when run alone)
from test_tail import CP, jdk  # noqa: E402

from core import compare, ghidra, paths, readable_stack  # noqa: E402
import game_config  # noqa: E402

SH4 = ROOT / "targets" / "dreamcast_sh4"
SCRIPTS = SH4 / "ghidra_scripts"
WORK = ROOT / "tests" / ".work"


@unittest.skipUnless(CP and jdk("javac") and jdk("java"), "no ghidra_home or JDK on this machine")
class Oracle(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        WORK.mkdir(parents=True, exist_ok=True)
        cls.tmp = tempfile.TemporaryDirectory(prefix="frame_", dir=WORK)
        cls.out = pathlib.Path(cls.tmp.name)
        src = os.pathsep.join(str(SH4 / d) for d in ("ghidra_scripts", "template"))
        r = subprocess.run([jdk("javac"), "-nowarn", "-proc:none", "-d", str(cls.out), "-cp", CP, "-sourcepath", src,
                            str(ROOT / "tests" / "java" / "TcFrameStub.java")],
                           capture_output=True, text=True, timeout=300)
        if r.returncode:
            raise AssertionError(r.stdout + r.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_stub(self, cases):
        f = self.out / "cases.txt"
        f.write_text(cases, encoding="utf-8")
        r = subprocess.run([jdk("java"), "-cp", str(self.out) + os.pathsep + CP, "TcFrameStub", str(f)],
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return {k: v.strip() for k, v in (ln.split(":", 1) for ln in r.stdout.splitlines() if ":" in ln)}

    def test_logged(self):
        got = self.run_stub("\n".join([
            "L 8cff9000 8cff9008 8cffd000",           # Spawn's save data: logged (was cut at STACK_LO)
            "L 8cffdf00 8cffdf08 8cffd000",           # the own frame: not
            "L 8cffcff8 8cffd008 8cffd000",           # across the bottom: the part below it
            "L 8c100000 8c100004 8cffd000",           # control: ordinary memory
            "L 8cffdf00 8cffdf08 8cffde00",           # frame_lo lower (a deeper frame): not logged
        ]))
        self.assertEqual(got["L 8cff9000 8cff9008"], "8cff9000-8cff9008")
        self.assertEqual(got["L 8c100000 8c100004"], "8c100000-8c100004")
        self.assertEqual(got["L 8cffcff8 8cffd008"], "8cffcff8-8cffd000")
        self.assertEqual(got["L 8cffdf00 8cffdf08"], "")  # the own frame (both bottoms): not logged

    def test_named_word_in_the_frame(self):
        got = self.run_stub("L 8cffdf00 8cffdf08 8cffd000 8cffdf04\n")
        self.assertEqual(got["L 8cffdf00 8cffdf08"], "8cffdf04-8cffdf08")

    def test_own_frame_inputs(self):
        got = self.run_stub("O 8cffd000 8cff9000\nO 8cffd000 8cffdf00\nO 8cffd000 8cffe000\n")
        self.assertEqual(got["O 8cff9000"], "false")  # a save-data load is an input
        self.assertEqual(got["O 8cffdf00"], "true")
        self.assertEqual(got["O 8cffe000"], "false")  # stack arguments: inputs, as before

    def test_facts(self):
        ok, bad = self.out / "ok.txt", self.out / "bad.txt"
        ok.write_text("frame_lo 8cffdf10\n", encoding="ascii")
        bad.write_text("frame_lo 8cfe0000\n", encoding="ascii")
        got = self.run_stub(f"F -\nF {ok}\n")
        self.assertEqual(got["F"], "8cffdf10")
        self.assertIn("refused", self.run_stub(f"F {bad}\n")["F"])
        self.assertEqual(self.run_stub("F -\n")["F"], "8cffd000")  # default: entry r15 less 0x1000


class Facts(unittest.TestCase):
    def test_frame_lo_fact(self):
        cfg = paths.config()
        self.assertNotIn("frame_lo", ghidra.facts_body({k: v for k, v in cfg.items() if k != "readable_frame_max"}))
        body = ghidra.facts_body(dict(cfg, readable_frame_max="0x2000"))
        self.assertIn("frame_lo 8cffc000\n", body)
        with mock.patch("core.paths.config", return_value={"readable_frame_max": "0x2000"}):
            self.assertEqual(readable_stack.frame_lo(), 0x8CFFC000)  # the judge's bound is the oracle's
        self.assertEqual(ghidra.frame_lo({}), compare.FRAME_LO)

    def test_no_input_rule_keeps_the_old_bound(self):
        for name in ("TcCount", "TcReach", "TcProvenance", "TcExit"):
            src = (SCRIPTS / f"{name}.java").read_text(encoding="utf-8")
            self.assertNotIn("TcBase.STACK_LO", src, name)
        self.assertIn("TcProvenance.ownFrame(a)", (SCRIPTS / "TcReach.java").read_text(encoding="utf-8"))
        self.assertIn("frameLo = facts.frameLo;", (SCRIPTS / "TcPrepare.java").read_text(encoding="utf-8"))
        self.assertIn("namedWords.add(g & ~3L)", (SCRIPTS / "TcPrepare.java").read_text(encoding="utf-8"))


MAIN = """#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef unsigned int u32;
typedef unsigned char u8;
#define TCRT_RAM_BASE 0x8c000000u
#define TCRT_RAM_SIZE 0x01000000u
#define TCRT_VALID_LO 0x8c010000u
#define TCRT_STACK_LO 0x8cff0000u
static u8 *RAM, *g_snapshot, *g_ram;
static u32 g_wlist[16], g_wcount;
static u32 rd_self_guest(u32 w) { return w; }
static int cmp_u32(const void *a, const void *b)
{
    u32 x = *(const u32 *)a, y = *(const u32 *)b;
    return x < y ? -1 : (x > y);
}
static void put(u32 a, u8 v)
{
    RAM[a - TCRT_RAM_BASE] = g_ram[a - TCRT_RAM_BASE] = v;
    g_wlist[g_wcount++] = a - TCRT_RAM_BASE;
}
static void report_tc(FILE *out)
{
%s
}
static void report_rd(FILE *out)
{
%s
}
int main(int argc, char **argv)
{
    RAM = (u8 *)calloc(TCRT_RAM_SIZE, 1);
    g_snapshot = (u8 *)calloc(TCRT_RAM_SIZE, 1);
    g_ram = (u8 *)calloc(TCRT_RAM_SIZE, 1);
    if (!RAM || !g_snapshot || !g_ram || argc < 2) return 2;
    put(0x8c100000u, 1);
    put(0x8cff9000u, 2);
    put(0x8cffdf00u, 3);
    if (argv[1][0] == 't') report_tc(stdout);
    else report_rd(stdout);
    return 0;
}
"""


def report_snippets():
    """Each runner's own W report, as C text: tc_rt.c's (sorted write list) and rd_mem.c's (RAM against snapshot)."""
    tc = (SH4 / "runtime" / "tc_rt.c").read_text(encoding="utf-8").replace("\r\n", "\n")
    rd = (SH4 / "runtime_rd" / "rd_mem.c").read_text(encoding="utf-8").replace("\r\n", "\n")
    a = re.search(r"    qsort\(g_wlist, g_wcount.*?\n    \}\n", tc, re.S).group(0)
    b = re.search(r"    u32 lo = TCRT_VALID_LO - TCRT_RAM_BASE, hi = .*?\n    \}\n.*?\n    \}\n", rd, re.S).group(0)
    return a, b


@unittest.skipUnless(os.name == "nt" and game_config.vcvars32(), "no 32-bit Visual Studio tools on this machine")
class Runners(unittest.TestCase):
    def test_report(self):
        d = pathlib.Path(tempfile.mkdtemp(dir=WORK if WORK.is_dir() else None))
        try:
            (d / "p.c").write_text(MAIN % report_snippets(), encoding="utf-8")
            vc = str(game_config.vcvars32()).replace("/", "\\")
            r = subprocess.run(f'cmd /d /s /c ""{vc}" >nul 2>&1 && cl /nologo /W4 /WX p.c"', cwd=d,
                               capture_output=True, text=True, errors="replace", timeout=300)
            self.assertEqual(r.returncode, 0, r.stdout[-2000:])
            for which in ("tc", "rd"):
                out = subprocess.run([str(d / "p.exe"), which], capture_output=True, text=True, timeout=60).stdout
                lines = out.split("\n")
                self.assertIn("W 8c100000 01", lines, which)  # control
                self.assertIn("W 8cff9000 02", lines, which)  # the save data: logged
                self.assertIn("W 8cffdf00 03", lines, which)  # the frame too: the judge leaves it out
                exp = {"done": True, "faults": [], "r0": 0, "fr0": 0, "writes": {}, "calls": [], "xf": [0] * 16}
                act = dict(exp, writes={0x8C100000: 1, 0x8CFF9000: 2, 0x8CFFDF00: 3})
                d2 = compare.compare_vector({"ret": "void", "callees": []}, b"", {"patches": []}, exp, act)
                self.assertEqual([x.split(":")[0] for x in d2], ["memory 0x8c100000", "memory 0x8cff9000"])
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(warnings="error")
