"""QACR0/1 stores and store-queue pointers in any register (r15). Spawn 8c05fec2 (Lib_RenderModelAlpha) failed every
oracle run with "write outside RAM" in two ways:
- it sets QACR0/1 (0xff000038/0xff00003c) from a TA list pointer before its sends: no P4 store but the operand-cache
  address array's was logged (TcExit, tc_p4.c, rd_p4.c), so the oracle stopped there;
- it sends through r6 = 0xe0000000 | (a TA list pointer's low 26 bits), built with `mov #-0x20,r6; shll8; shll16;
  or r2,r6`, then `pref @r6`; TcSq.of followed only r8..r14, so no window and no harness line S.
One planted case and one control each:
1. TcExit.p4 (stub, tests/java/TcP4Stub.java) logs a store to QACR0/1, still the OCA's, and nothing beside them;
2. TcSq.Window finds 8c05fec2's r6, a pool constant's register and a copy of it; a plain constant, a shifted window
   pointer, a register written since and an r0..r7 register across a call are not window pointers; Window.storeBase
   names the register of every store form (Spawn 8c058b70 stores through a built r4 and a callee sends the queue:
   TcSq.of takes it), and of no load or other instruction;
3. a TcSq on a built register writes line S and meta sq_window for it, and draws nothing (no state register);
4. the judge compares QACR stores (core/compare.judged);
5. both runners log QACR stores as W lines and nothing beside them: tc_p4.c's store log (raw C's wr32) and rd_p4.c's
   page fault path (readable C's pointer stores, a constant address's `mov moffs32, eax` too), compiled from source
   and linked as the runner is (readable_check.link_flags: no ASLR; with it QACR0/1's page was taken in 26 of 300
   runs and the program crashed); rd_p4_init fails loudly, naming the page, when it cannot have one (a second init).
6. a pref of a queue whose rest (bytes not stored since) comes from an old block in code is a code read (R8) the C
   would make copying it (TcSq.codeRest: Spawn 8c057ec0/8c058030/8c058150, r11 drawn by code at 8c030400): the
   oracle run faults there; a queue stored whole, and old blocks in data, read no code.
The oracle run itself needs Ghidra: the scoped re-prepare of 8c05fec2 judges it.
Synthetic data. Run: python -W error tests/test_p4_window.py (also from test_tooling.py).
"""
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "port" / "tools"))
from test_sync import TMP  # noqa: E402,F401  (sets DECOMP_PROJECT when run alone)
from test_tail import CP, jdk  # noqa: E402

from core import compare, readable_check  # noqa: E402
import game_config  # noqa: E402

SH4 = ROOT / "targets" / "dreamcast_sh4"
WORK = ROOT / "tests" / ".work"


@unittest.skipUnless(CP and jdk("javac") and jdk("java"), "no ghidra_home or JDK on this machine")
class Oracle(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        WORK.mkdir(parents=True, exist_ok=True)
        cls.tmp = tempfile.TemporaryDirectory(prefix="p4_", dir=WORK)
        cls.out = pathlib.Path(cls.tmp.name)
        src = os.pathsep.join(str(SH4 / d) for d in ("ghidra_scripts", "template"))
        r = subprocess.run([jdk("javac"), "-nowarn", "-proc:none", "-d", str(cls.out), "-cp", CP, "-sourcepath", src,
                            str(ROOT / "tests" / "java" / "TcP4Stub.java")],
                           capture_output=True, text=True, timeout=300)
        if r.returncode:
            raise AssertionError(r.stdout + r.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_stub(self, cases):
        f = self.out / "cases.txt"
        f.write_text(cases, encoding="utf-8")
        r = subprocess.run([jdk("java"), "-cp", str(self.out) + os.pathsep + CP, "TcP4Stub", str(f)],
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return {k: v.strip() for k, v in (ln.split(":", 1) for ln in r.stdout.splitlines() if ":" in ln)}

    def test_qacr_logged(self):
        got = self.run_stub("\n".join("P " + c for c in (
            "ff000038 ff000040",  # QACR0 and QACR1 (8c05ffe0/e2): logged
            "ff00003c ff000040",
            "f4000020 f4000024",  # control: the OCA, as before
            "ff000040 ff000044",  # beside them: not logged (still "write outside RAM")
            "ff000034 ff00003c",
            "8c100000 8c100004",  # RAM: the RAM path, not this one
        )))
        self.assertEqual([got[k] for k in ("P ff000038 ff000040", "P ff00003c ff000040", "P f4000020 f4000024")],
                         ["true"] * 3)
        self.assertEqual([got[k] for k in ("P ff000040 ff000044", "P ff000034 ff00003c", "P 8c100000 8c100004")],
                         ["false"] * 3)

    def test_window_registers(self):
        got = self.run_stub("\n".join([
            "Q spawn e6e0 4618 4628 262b 6546>5 7640",  # 8c05fec2: mov #-0x20,r6; shll8; shll16; or r2,r6; add
            "Q pool d10a=e0000000 6313",                # a pool constant, and a copy of it
            "Q call e8e0 4818 4828 e6e0 4618 4628 call",  # r6 is the callee's after a call; r8 is kept
            "Q plain e620 4618 4628 262b",              # control: 0x20000000 is no window address
            "Q shifted d10a=e0000000 4108",             # control: a window pointer shifted is not one
            "Q written e6e0 4618 4628 262b 6646>6",     # control: written since (mov.l @r4+,r6)
        ]))
        self.assertEqual(got["Q spawn"], "r6")
        self.assertEqual(got["Q pool"], "r1 r3")
        self.assertEqual(got["Q call"], "r8")
        self.assertEqual([got["Q plain"], got["Q shifted"], got["Q written"]], ["", "", ""])

    def test_store_base(self):
        stores = ["2432", "2430", "2431", "2436", "1435", "8045", "8145", "0434", "0436", "f43a", "f43b", "f437",
                  "04c3", "4422", "4413"]  # mov.l/b/w @r4, @-r4, @(d,r4), mov.b/w r0,@(d,r4), @(r0,r4), fmov x3,
        # movca.l, sts.l pr,@-r4, stc.l gbr,@-r4
        loads = ["6432", "6436", "5435", "8445", "0c4c", "f438", "f439", "f436", "243b", "2437", "2438", "0083",
                 "4408", "4426"]  # mov.l @r3,r4, @r3+, @(d,r3), mov.b @(d,r4),r0, @(r0,r4), fmov loads, or, div0s,
        # tst, pref, shll2, lds.l @r4+,pr
        got = self.run_stub("B " + " ".join(stores + loads) + "\n")
        self.assertEqual([got["B " + w] for w in stores], ["4"] * len(stores))
        self.assertEqual([got["B " + w] for w in loads], ["-1"] * len(loads))
        b70 = self.run_stub("Q b70 d101=e0000000 2e1b 66e3 7620 64e3\n")  # 8c058bee..f6: r14 |= window, copies
        self.assertEqual(b70["Q b70"], "r1 r4 r6 r14")

    def test_header_and_meta(self):
        got = self.run_stub("H -1 r6\nH 14 r14\n")
        self.assertEqual(got["H r6"], 'S 6 "r6"')  # a built pointer: nothing drawn, queues empty, no crash
        self.assertEqual(got["H r14"], 'S 14 "r14"')  # control: the state register, as before

    def test_code_rest(self):
        code = self.out / "code.txt"
        code.write_text("8c030000 8c031000\n", encoding="ascii")
        got = self.run_stub("".join(f"C {code} {c}\n" for c in (
            "e0030440", "e0030440 e0030400+32", "e0030440 e0030400+31", "e0100440")))
        self.assertEqual(got["C e0030440"], "8c030400 8c030420")  # r11 drawn by code: both old blocks are code
        self.assertEqual(got["C e0030440 e0030400+32"], "- 8c030420")  # queue 0 stored whole: no rest to copy
        self.assertEqual(got["C e0030440 e0030400+31"], "8c03041f 8c030420")  # one byte of rest: still a code read
        self.assertEqual(got["C e0100440"], "- -")  # control: old blocks in data, as before


class Judge(unittest.TestCase):
    def test_qacr_judged(self):
        self.assertTrue(compare.judged(0xFF000038) and compare.judged(0xFF00003F))
        self.assertFalse(compare.judged(0xFF000040) or compare.judged(0xFF000034))
        exp = {"done": True, "faults": [], "r0": 0, "fr0": 0, "writes": {0xFF000038: 0x10}, "calls": [], "xf": [0] * 16}
        act = dict(exp, writes={})
        d = compare.compare_vector({"ret": "void", "callees": []}, b"", {"patches": []}, exp, act)
        self.assertEqual([x.split(":")[0] for x in d], ["memory 0xff000038"])  # a left-out QACR store is a miss


RAW = r"""#include <stdio.h>
#include "tc_rt_internal.h"
int main(void)
{
    tcrt_p4_reset();
    printf("in %d %d %d\n", tcrt_p4_store(0xff000038u, 0x10u, 4), tcrt_p4_store(0xff00003cu, 0x11u, 4),
           tcrt_p4_store(0xf4000020u, 0, 4));
    printf("out %d %d %d\n", tcrt_p4_store(0xff000040u, 1, 4), tcrt_p4_store(0xff000034u, 1, 4),
           tcrt_p4_store(0xff00003eu, 1, 4));
    tcrt_p4_report(stdout);
    return 0;
}
"""

RD = r"""#include <stdio.h>
#include <windows.h>
#include "rd_internal.h"
static LONG CALLBACK handler(EXCEPTION_POINTERS *ep)
{
    EXCEPTION_RECORD *er = ep->ExceptionRecord;
    if (er->ExceptionCode == EXCEPTION_SINGLE_STEP)
        return rd_p4_step() ? EXCEPTION_CONTINUE_EXECUTION : EXCEPTION_CONTINUE_SEARCH;
    if (er->ExceptionCode == EXCEPTION_ACCESS_VIOLATION
        && rd_p4_fault((u32)er->ExceptionInformation[1], er->ExceptionInformation[0] == 1, ep->ContextRecord))
        return EXCEPTION_CONTINUE_EXECUTION;
    return EXCEPTION_CONTINUE_SEARCH;
}
__declspec(noinline) void qacr0(u32 v) { *(volatile u32 *)0xff000038u = v; } /* mov ds:[0ff000038h], eax */
__declspec(noinline) void put(volatile u32 *p, u32 v) { *p = v; }
static int faults(volatile u32 *p)
{
    __try {
        put(p, 1);
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return 1;
    }
    return 0;
}
int main(void)
{
    AddVectoredExceptionHandler(1, handler);
    rd_p4_reset();
    if (!rd_p4_init()) return 3;
    qacr0(0x10);
    put((volatile u32 *)0xff00003cu, 0x11);
    printf("out %d %d\n", faults((volatile u32 *)0xff000040u), faults((volatile u32 *)0xff000034u));
    tcrt_p4_report(stdout);
    printf("again %d\n", rd_p4_init()); /* both pages are taken now (by the first init) */
    return 0;
}
"""


@unittest.skipUnless(os.name == "nt" and game_config.vcvars32(), "no 32-bit Visual Studio tools on this machine")
class Runners(unittest.TestCase):
    def build_run(self, text, flags, srcs):
        d = pathlib.Path(tempfile.mkdtemp(dir=WORK if WORK.is_dir() else None))
        try:
            (d / "p.c").write_text(text, encoding="utf-8")
            vc = str(game_config.vcvars32()).replace("/", "\\")
            inc = " ".join(f'/I "{SH4 / x}"' for x in ("runtime", "runtime_rd"))
            files = " ".join(f'"{SH4 / s}"' for s in srcs)
            link = " ".join(readable_check.link_flags({}))
            r = subprocess.run(f'cmd /d /s /c ""{vc}" >nul 2>&1 && cl /nologo /W4 /WX {flags} {inc} p.c {files} '
                               f'{link}"', cwd=d, capture_output=True, text=True, errors="replace", timeout=300)
            self.assertEqual(r.returncode, 0, r.stdout[-3000:])
            p = subprocess.run([str(d / "p.exe")], capture_output=True, text=True, timeout=60)
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            return (p.stdout + p.stderr).split("\n")
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_raw_log(self):
        out = self.build_run(RAW, "/Od", ["runtime/tc_p4.c"])
        self.assertIn("in 1 1 1", out)
        self.assertIn("out 0 0 0", out)  # beside QACR0/1, or across its end: a fault (check), as before
        self.assertIn("W f4000020 00000000", out)
        self.assertIn("W ff000038 1000000011000000", out)

    def test_readable_log(self):
        out = self.build_run(RD, "/O2", ["runtime_rd/rd_p4.c", "runtime/tc_p4.c"])
        self.assertIn("out 1 1", out)  # beside QACR0/1: the store still faults
        self.assertIn("W ff000038 1000000011000000", out)
        self.assertIn("again 0", out)  # a page it cannot have: init fails, and says which
        self.assertIn("runner: cannot map the P4 operand-cache array at f4000000 (error 487)", out)
        self.assertIn("runner: cannot map QACR0/1's page at ff000000 (error 487)", out)


if __name__ == "__main__":
    unittest.main(warnings="error")
