"""GameCube: the 750CL's reciprocal estimates (fres, frsqrte) and the cache/ordering instructions in the oracle.

Ghidra's SLEIGH divides and takes the square root exactly (fres: float2float(1 / fB); frsqrte: 1 / sqrt(fB)); the
chip looks the result up in a table and interpolates, so a program that feeds an estimate into further arithmetic gets
different bits than the exact value gives. The oracle (TcFixups with TcEstimate, core/ghidra_shared/ppc) and the
runtime the translated C calls (ppc_fres, ppc_frsqrte in tc_ppc.c) now give the table result. This test proves, over
the special values, every table row at both ends of its row, both exponent parities of frsqrte, all exponents and a
random sample of operands (about 200,000 bit patterns):
  - TcEstimate (Java), the C runtime (built with MSVC, /W4 /WX) and a Python version written from the instruction's
    definition give the same bits;
  - the three copies of the four tables are identical (Java source, C source, Python's parse of the Java source);
  - the estimate's relative error is within the architecture's bounds (frsqrte 1/32, fres 1/256) on every normal
    operand in range, which catches a mistyped table entry the copies agreeing with each other would not;
and checks the special cases (zero, infinity, NaN, negative, denormal, out-of-range exponents) and that the oracle's
wiring is in place: the fixup for fres/frsqrte, no-op callbacks for the cache/ordering CALLOTHERs the SLEIGH names,
the dcbz block clear, and the instructions that stay refused (dcbi, dcbz_l, mfmsr, mtmsr, mftb, FPSCR moves).
The tables are the hardware-measured ones Dolphin publishes; this test cannot check them against a console.
Skipped without config.local.json's ghidra_home and a JDK (Java), vcvars (C).

usage: python tests/test_gc_estimate.py
"""
import os
import pathlib
import random
import re
import struct
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
SHARED = TOOL / "core" / "ghidra_shared" / "ppc"
JAVA = (SHARED / "TcEstimate.java").read_text(encoding="utf-8")
CSRC = (RT / "tc_ppc.c").read_text(encoding="utf-8")

MANT, SIGN, EXP = (1 << 52) - 1, 1 << 63, 0x7FF << 52
QNAN, INF = 0x7FF8000000000000, 0x7FF0000000000000


def java_table(name):
    m = re.search(r"int\[\] " + name + r" = \{(.*?)\};", JAVA, re.S)
    return [int(x, 16) for x in re.findall(r"-?0x[0-9a-f]+", m.group(1))]


def c_table(name):
    m = re.search(r"s32 " + name + r"\[32\] = \{(.*?)\};", CSRC, re.S)
    return [int(x, 16) for x in re.findall(r"-?0x[0-9a-f]+", m.group(1))]


TABLES = {n: java_table(n.upper()) for n in ("frsqrte_base", "frsqrte_dec", "fres_base", "fres_dec")}


def trunc_div(a, b):
    q = abs(a) // abs(b)
    return q if (a < 0) == (b < 0) else -q


def ref_frsqrte(b):
    mant, sign, exp = b & MANT, b & SIGN, b & EXP
    if mant == 0 and exp == 0:
        return sign | INF
    if exp == EXP:
        return (QNAN if sign else 0) if mant == 0 else b | (1 << 51)
    if sign:
        return QNAN
    if exp == 0:  # denormal: shift the leading one up to bit 52, one exponent step per shift
        shifts = 53 - mant.bit_length()
        mant = (mant << shifts) & MANT
        exp = (1 - shifts) << 52  # negative for a denormal: Python's & works as on 64-bit two's complement
    lsb = exp & (1 << 52)
    e = ((0x3FF << 52) - trunc_div(exp - (0x3FE << 52), 2)) & EXP
    i = (lsb | mant) >> 37
    t = TABLES
    return (e | ((t["frsqrte_base"][i // 2048] + t["frsqrte_dec"][i // 2048] * (i % 2048)) << 26)) & (2**64 - 1)


def ref_fres(b):
    mant, sign, exp = b & MANT, b & SIGN, b & EXP
    if mant == 0 and exp == 0:
        return sign | INF
    if exp == EXP:
        return sign if mant == 0 else b | (1 << 51)
    if exp < (895 << 52):
        return sign | 0x47EFFFFFE0000000
    if exp >= (1149 << 52):
        return sign
    e = (0x7FD << 52) - exp
    i = mant >> 37
    t = TABLES
    return sign | e | ((t["fres_base"][i // 1024] - (t["fres_dec"][i // 1024] * (i % 1024) + 1) // 2) << 29)


def f(bits):
    return struct.unpack("<d", struct.pack("<Q", bits))[0]


def operands():
    rnd = random.Random(7)
    ops = [0, SIGN, INF, SIGN | INF, QNAN, SIGN | QNAN, INF | 1, 0x7FFFFFFFFFFFFFFF, 0xFFF0000000000001,
           1, MANT, SIGN | 1, SIGN | MANT, 1 << 52, (1 << 52) | MANT]
    for x in (1.0, 2.0, 0.5, 4.0, 0.25, 3.0, 10.0, 1e-3, 1e30, 1e-30, 1.0e38, 1.17549435e-38, 3.4e38,
              -1.0, -2.0, -0.5, 5e-324, 2.2250738585072014e-308):
        ops.append(struct.unpack("<Q", struct.pack("<d", x))[0])
    for e in range(0, 2048):  # every exponent, the first, a middle and the last mantissa of it, both signs
        for m in (0, 1, MANT >> 1, MANT):
            ops.append((e << 52) | m)
    for e in (0x37E, 0x37F, 0x380, 0x47C, 0x47D, 0x47E, 0x3FD, 0x3FE, 0x3FF, 0x400):  # fres range edges, parities
        for row in range(32):
            for m in (row << 47, (row << 47) | ((1 << 47) - 1)):  # first and last mantissa of each fres / frsqrte row
                ops.append((e << 52) | m)
    for _ in range(150000):
        ops.append(rnd.getrandbits(64) if rnd.random() < 0.3 else (rnd.randrange(0x370, 0x490) << 52) | rnd.getrandbits(52))
    return sorted(set(o & (2**64 - 1) for o in ops))


C_DRIVER = r"""
#define _CRT_SECURE_NO_WARNINGS
#include <stdio.h>
#include <string.h>
#include "tc_rt.h"

int main(int argc, char **argv)
{
    FILE *in = argc > 1 ? fopen(argv[1], "r") : NULL;
    unsigned long long b;
    if (!in) return 2;
    while (fscanf(in, "%llx", &b) == 1) {
        f64 x, r, s;
        u64 rb, sb;
        memcpy(&x, &b, 8);
        r = ppc_frsqrte(x);
        s = ppc_fres(x);
        memcpy(&rb, &r, 8);
        memcpy(&sb, &s, 8);
        printf("%016llx %016llx %016llx\n", b, (unsigned long long)rb, (unsigned long long)sb);
    }
    return 0;
}
"""


class Tables(unittest.TestCase):
    def test_copies_agree(self):
        for name in TABLES:
            self.assertEqual(len(TABLES[name]), 32, name)
            self.assertEqual(c_table(name), TABLES[name], name)

    def test_special_cases(self):  # the definitions, by hand
        self.assertEqual(ref_frsqrte(0), INF)
        self.assertEqual(ref_frsqrte(SIGN), SIGN | INF)
        self.assertEqual(ref_frsqrte(INF), 0)
        self.assertEqual(ref_frsqrte(SIGN | INF), QNAN)
        self.assertEqual(ref_frsqrte(f_bits(-4.0)), QNAN)
        self.assertEqual(ref_frsqrte(INF | 1), INF | 1 | (1 << 51))
        self.assertEqual(ref_fres(0), INF)
        self.assertEqual(ref_fres(SIGN), SIGN | INF)
        self.assertEqual(ref_fres(INF), 0)
        self.assertEqual(ref_fres(SIGN | INF), SIGN)
        self.assertEqual(ref_fres((1 << 52) | 5), 0x47EFFFFFE0000000)  # below 2^-128: the largest single
        self.assertEqual(ref_fres(f_bits(2.0**127)), 0)  # at or above 2^126: zero


def f_bits(x):
    return struct.unpack("<Q", struct.pack("<d", x))[0]


@unittest.skipUnless(CP and jdk("javac") and jdk("java") and vcvars(64), "no ghidra_home, JDK or vcvars on this machine")
class Estimates(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        work = TOOL / "tests" / ".work"
        work.mkdir(parents=True, exist_ok=True)
        cls.tmp = tempfile.TemporaryDirectory(prefix="gcest_", dir=work)
        d = pathlib.Path(cls.tmp.name)
        cls.ops = operands()
        (d / "ops.txt").write_text("\n".join(f"{o:016x}" for o in cls.ops) + "\n", encoding="utf-8")
        r = subprocess.run([jdk("javac"), "-nowarn", "-proc:none", "-d", str(d), "-cp", CP, "-sourcepath", str(SHARED),
                            str(TOOL / "tests" / "java" / "TcEstimateStub.java")],
                           capture_output=True, text=True, timeout=300)
        if r.returncode:
            raise AssertionError(r.stdout + r.stderr)
        r = subprocess.run([jdk("java"), "-cp", str(d) + os.pathsep + CP, "TcEstimateStub", str(d / "ops.txt")],
                           capture_output=True, text=True, timeout=300)
        if r.returncode:
            raise AssertionError(r.stdout + r.stderr)
        cls.java = [tuple(int(x, 16) for x in ln.split()) for ln in r.stdout.splitlines()]
        (d / "drv.c").write_text(C_DRIVER, encoding="utf-8")
        vc = str(vcvars(64)).replace("/", "\\")
        src = " ".join(f'"{RT / n}"' for n in ("tc_rt.c", "tc_ppc.c"))
        r = subprocess.run(f'cmd /d /s /c ""{vc}" >nul 2>&1 && cl /nologo /std:c11 /O2 /W4 /WX /fp:strict /I"{RT}" '
                           f'drv.c {src}"', cwd=d, capture_output=True, text=True, errors="replace", timeout=300)
        if r.returncode:
            raise AssertionError(r.stdout[-3000:] + r.stderr[-2000:])
        r = subprocess.run([str(d / "drv.exe"), str(d / "ops.txt")], capture_output=True, text=True, timeout=300)
        if r.returncode:
            raise AssertionError(r.stdout + r.stderr)
        cls.c = [tuple(int(x, 16) for x in ln.split()) for ln in r.stdout.splitlines()]

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_three_copies_agree(self):
        self.assertEqual(len(self.java), len(self.ops))
        self.assertEqual(len(self.c), len(self.ops))
        bad = []
        for o, j, c in zip(self.ops, self.java, self.c):
            want = (o, ref_frsqrte(o), ref_fres(o))
            if j != want or c != want:
                bad.append((f"{o:016x}", [f"{x:016x}" for x in want[1:]], [f"{x:016x}" for x in j[1:]],
                            [f"{x:016x}" for x in c[1:]]))
        self.assertEqual(bad[:5], [], f"{len(bad)} differ (operand, python, java, c)")

    def test_error_bounds(self):
        checked = {"frsqrte": 0, "fres": 0}
        for o, rs, rf in ((j[0], j[1], j[2]) for j in self.java):
            if o & SIGN or not (o & EXP) or (o & EXP) == EXP:  # normal, positive operands only
                continue
            x = f(o)
            est = f(rs)
            exact = 1.0 / (x ** 0.5)
            self.assertLessEqual(abs(est - exact) / exact, 1 / 32, f"frsqrte {o:016x}")
            checked["frsqrte"] += 1
            if 895 << 52 <= (o & EXP) < 1149 << 52:
                est = f(rf)
                exact = 1.0 / x
                self.assertLessEqual(abs(est - exact) / exact, 1 / 256, f"fres {o:016x}")
                checked["fres"] += 1
        self.assertGreater(checked["frsqrte"], 100000)
        self.assertGreater(checked["fres"], 20000)


class Wiring(unittest.TestCase):
    def test_text(self):
        fix = (GC / "ghidra_scripts" / "TcFixups.java").read_text(encoding="utf-8")
        self.assertIn('m.matches("fres\\\\.?|frsqrte\\\\.?")', fix)
        self.assertIn("put(emu, op.equals(\"fres\") ? TcEstimate.fres(operand) : TcEstimate.frsqrte(operand));", fix)
        self.assertIn("TcFixups.install(emu, fn.getEntryPoint().getAddressSpace());",
                      (GC / "ghidra_scripts" / "TcOracle.java").read_text(encoding="utf-8"))
        m = re.search(r'if \(m\.matches\("([^"]*)"\)\) \{', fix[fix.index("static String refuse"):])
        refused = m.group(1).replace("\\\\", "\\")
        for kept in ("dcbi", "dcbz_l", "dcbzl", "mfmsr", "mtmsr", "mftb"):
            self.assertRegex(kept, "^(" + refused + ")$", kept + " must stay refused")
        for gone in ("dcbz", "dcbf", "dcbst", "dcbt", "dcbtst", "icbi", "sync", "isync", "eieio", "fres", "frsqrte"):
            self.assertNotRegex(gone, "^(" + refused + ")$", gone + " is modelled now")
        # every no-op userop is a name the SLEIGH defines
        for name in ("sync", "instructionSynchronize", "enforceInOrderExecutionIO", "dataCacheBlockFlush",
                     "dataCacheBlockStore", "dataCacheBlockTouch", "dataCacheBlockTouchForStore",
                     "instructionCacheBlockInvalidate", "dataCacheBlockClearToZero"):
            self.assertIn('"' + name + '"', fix, name)
        self.assertIn("long ea = ms.getValue(op.getInput(1)) & 0xffffffe0L;", fix)  # the 32-byte block
        self.assertIn("ms.setChunk(new byte[32], ram, ea, 32);", fix)
        self.assertIn("ps_rsqrte", (GC / "translator" / "ppctranslate" / "translate.py").read_text(encoding="utf-8"))
        self.assertIn('Call("ppc_frsqrte", [b], "f64")', (GC / "translator" / "ppctranslate" / "translate.py")
                      .read_text(encoding="utf-8"))
        self.assertIn('"ppc_fres", "ppc_frsqrte"', (GC / "rtx.py").read_text(encoding="utf-8"))
        self.assertIn("TcEstimate.java", (GC / "target.json").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
