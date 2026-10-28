"""fipr and ftrv round once (fipr-once 2026-10-27): every side against exact rational arithmetic.
The SH-4 computes FIPR and FTRV as one hardware inner product per result (the software manual; Hitachi's Hot Chips IX
paper): no rounded intermediate values. The reference emulator, Flycast (core/hw/sh4/interpr/sh4_fpu.cpp fipr/ftrv,
core/hw/sh4/dyna/shil_canonical.h innerProduct), computes each product in double, sums the four in element order
0..3 in double and converts the sum once to single, in the host mode FPSCR sets (MXCSR RC toward zero for RM=1,
DAZ|FTZ for DN=1: sh4_core_regs.cpp setHostRoundingMode). DP models that, bit for bit:
1. ref(): Python fractions, no float anywhere: the exact products, each double sum rounded onto the 53-bit grid to
   nearest (RM=0) or toward zero (RM=1), the sum rounded once onto binary32, under FPSCR.DN=1 (denormal sources and
   results are signed zeros; IEEE signs for zero sums); a double sum in [2^-126 - 2^-150, 2^-126) is "B" (the oracle
   rejects the vector: TcRound.nearMinNormal).
2. The C: sh4_fipr and sh4_ftrv, the same text in tc_fpu.c, rd_sh4.c and the port's sh4_rt.c, built 32-bit (the
   readable runner, the port) and 64-bit (the judge's runner, xemu), run with MXCSR as the runners set it.
3. The oracle: TcRound.fipr through TcFpu's DN steps (tests/java/TcFiprStub.java); with ONCE=0 the p-code's seven
   roundings, which must differ from one rounding on the separating cases (what the judge tells apart).
ftrv is checked as four rows (XMTRX row 0 = a, row 1 = -a, rows 2 and 3 zero): row r is fipr(row r, v).
FIPR_N sets the random count (default 20000).
Run: python -W error tests/test_fipr.py
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
from fractions import Fraction

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "port" / "tools"), str(ROOT / "tests")]
from test_fmac import CORNERS, f2b, is_inf, is_nan, show, text, vcvars  # noqa: E402
from test_fsca_rm import CP, jdk  # noqa: E402

SH4 = ROOT / "targets" / "dreamcast_sh4"
SIDES = [SH4 / "runtime" / "tc_fpu.c", SH4 / "runtime_rd" / "rd_sh4.c", ROOT / "port" / "runtime" / "sh4_rt.c"]
N = int(os.environ.get("FIPR_N", "20000"))
MIN = Fraction(1, 2 ** 126)


def fipr_source(p):
    """The fipr/ftrv block with its comment, as the file has it."""
    return re.search(r"/\* fipr FVm,FVn and ftrv XMTRX,FVn:.*?\nvoid sh4_ftrv\(f32 v\[4\]\)\n\{\n.*?\n\}\n", text(p),
                     re.S).group(0)


# ---------- the exact reference ----------
def val(b):
    """bits -> (Fraction, sign) with FPSCR.DN: a denormal is a signed zero. Finite only."""
    s, ex, fr = b >> 31, (b >> 23) & 0xff, b & 0x7fffff
    if ex == 0:
        return Fraction(0), s
    v = Fraction(fr | 0x800000) * Fraction(2) ** (ex - 150)
    return (-v if s else v), s


def grid(x, bits, emin, rm):
    """|x| > 0 rounded onto a binary grid of `bits` significant bits (exponent floor emin), signed."""
    s, a = x < 0, abs(x)
    top = a.numerator.bit_length() - a.denominator.bit_length()
    if Fraction(2) ** top > a:
        top -= 1
    q = Fraction(2) ** (max(top, emin) - bits + 1)
    m, rem = divmod(a, q)
    if rm == 0 and (rem > q / 2 or (rem == q / 2 and m % 2)):
        m += 1
    return -(m * q) if s else m * q


def ref(a, b, rm):
    """fipr(a, b) as Flycast computes it: result bits, "N" (a NaN) or "B" (the oracle rejects the vector)."""
    if any(map(is_nan, a + b)):
        return "N"
    if any(map(is_inf, a + b)):  # IEEE in double: inf * 0 and inf - inf are NaNs, else the infinity's sign
        sgn = set()
        for x, y in zip(a, b):
            if is_inf(x) or is_inf(y):
                if (not is_inf(x) and val(x)[0] == 0) or (not is_inf(y) and val(y)[0] == 0):
                    return "N"
                sgn.add((x >> 31) ^ (y >> 31))
        return "N" if len(sgn) > 1 else sgn.pop() << 31 | 0x7f800000
    t, tneg = None, 0
    for x, y in zip(a, b):
        (vx, sx), (vy, sy) = val(x), val(y)
        p, pneg = vx * vy, sx ^ sy  # exact in double: 48 bits
        if t is None:
            t, tneg = p, pneg
            continue
        if t == 0 and p == 0:
            tneg = tneg & pneg  # -0 only when both are -0 (RN and RZ)
        elif t + p == 0:
            t, tneg = Fraction(0), 0  # exact cancellation: +0
        else:
            t = grid(t + p, 53, -1022, rm)
    if t == 0:
        return tneg << 31
    if MIN - Fraction(1, 2 ** 150) <= abs(t) < MIN:
        return "B"
    r = grid(t, 24, -126, rm)
    s = int(t < 0)  # a sum that rounds to zero keeps its sign (IEEE conversion, then the flush)
    if abs(r) < MIN:
        return s << 31  # flushed (FPSCR.DN)
    if abs(r) >= Fraction(2) ** 128:
        return s << 31 | (0x7f800000 if rm == 0 else 0x7f7fffff)
    return f2b(float(r))  # exact: r is on the binary32 grid


def ref_ftrv(a, v, rm):
    """ftrv with XMTRX row 0 = a, row 1 = -a, rows 2 and 3 zero."""
    neg = [x ^ 0x80000000 for x in a]
    return [ref(a, v, rm), ref(neg, v, rm), ref([0] * 4, v, rm), ref([0] * 4, v, rm)]


# ---------- cases ----------
def fl(m, e, s=0):
    return f2b((-1) ** s * m * 2.0 ** e)


def ordinary(rnd, lo=-30, hi=10):
    return fl(rnd.getrandbits(24) | 1 << 23, rnd.randrange(lo, hi) - 23, rnd.randrange(2))


SEPARATING = [  # one rounding and seven differ (1376d6a's port test; TcFpu.once's purpose)
    ([0x3f800000, f2b(2.0 ** -30), 0xbf800000, 0], [0x3f800000] * 3 + [0]),  # 1 + 2^-30 - 1: once 2^-30, seven 0
    ([0x3f800000, f2b(2.0 ** -24), f2b(2.0 ** -25), 0], [0x3f800000] * 3 + [0]),  # 1 + 2^-24 + 2^-25
]


def gen(n, seed=2):
    rnd = random.Random(seed)
    out = list(SEPARATING)
    cs = CORNERS
    for i in range(len(cs) ** 2):  # every corner pair in each lane position, the rest ordinary
        x, y, k = cs[i % len(cs)], cs[i // len(cs)], i % 4
        a, b = [ordinary(rnd) for _ in range(4)], [ordinary(rnd) for _ in range(4)]
        a[k], b[k] = x, y
        out.append((a, b))
    while len(out) < n + len(cs) ** 2:
        k = rnd.randrange(6)
        if k == 0:  # any bits
            out.append(([rnd.getrandbits(32) for _ in range(4)], [rnd.getrandbits(32) for _ in range(4)]))
        elif k == 1:  # cancellation: lane 3 cancels lanes 0..2 to within a few ulps
            a, b = [ordinary(rnd) for _ in range(4)], [ordinary(rnd) for _ in range(4)]
            s = sum(struct.unpack("<f", struct.pack("<I", x))[0] * struct.unpack("<f", struct.pack("<I", y))[0]
                    for x, y in zip(a[:3], b[:3]))
            a[3], b[3] = (f2b(-s) + rnd.randrange(-3, 4)) & 0xffffffff, 0x3f800000
            out.append((a, b))
        elif k == 2:  # small integers: mostly exact
            out.append(([f2b(float(rnd.randrange(-4096, 4096))) for _ in range(4)],
                        [f2b(float(rnd.randrange(-4096, 4096))) for _ in range(4)]))
        elif k == 3:  # around the smallest normal
            out.append(([ordinary(rnd, -70, -50) for _ in range(4)], [ordinary(rnd, -80, -60) for _ in range(4)]))
        elif k == 4:  # overflow and the largest values
            out.append(([ordinary(rnd, 60, 104) for _ in range(4)], [ordinary(rnd, 20, 50) for _ in range(4)]))
        else:  # ordinary game values (unit vectors, matrices, positions)
            out.append(([ordinary(rnd) for _ in range(4)], [ordinary(rnd) for _ in range(4)]))
    return out


def agree(want, got):
    return got == "N" if want == "N" else want == "B" or got == want


def mismatches(cases, rm, got):
    """got: per case [fipr, ftrv row 0..3]."""
    bad = []
    for (a, b), g in zip(cases, got):
        want = [ref(a, b, rm)] + ref_ftrv(a, b, rm)
        for k, (w, x) in enumerate(zip(want, g)):
            if not agree(w, x):
                bad.append("%s | %s rm%d #%d: want %s got %s" % (" ".join("%08x" % v for v in a),
                                                                  " ".join("%08x" % v for v in b), rm, k, show(w),
                                                                  show(x)))
    return bad


# ---------- the C sides ----------
HARNESS = """#define _CRT_SECURE_NO_WARNINGS
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <emmintrin.h>
#include <xmmintrin.h>
typedef float f32;
typedef unsigned int u32;
static u32 g_xf[16];
static f32 u2f(u32 u) { f32 f; memcpy(&f, &u, 4); return f; }
static u32 f2u(f32 f) { u32 u; memcpy(&u, &f, 4); return u; }
%s
int main(int argc, char **argv)
{
    FILE *f;
    long n, i;
    int k;
    u32 *in, *out;
    if (argc < 4) return 2;
    f = fopen(argv[2], "rb");
    fseek(f, 0, SEEK_END);
    n = ftell(f) / 32;
    fseek(f, 0, SEEK_SET);
    in = (u32 *)malloc((size_t)n * 32);
    out = (u32 *)malloc((size_t)n * 20);
    if (fread(in, 32, (size_t)n, f) != (size_t)n) return 3;
    fclose(f);
    _mm_setcsr((_mm_getcsr() & ~0x6000u) | 0x8040u | (u32)strtoul(argv[1], NULL, 16)); /* DAZ|FTZ, RC */
    for (i = 0; i < n; i++) {
        f32 a[4], b[4];
        for (k = 0; k < 4; k++) {
            a[k] = u2f(in[8 * i + k]);
            b[k] = u2f(in[8 * i + 4 + k]);
        }
        out[5 * i] = f2u(sh4_fipr(a, b));
        for (k = 0; k < 16; k++) g_xf[k] = 0;
        for (k = 0; k < 4; k++) {
            g_xf[4 * k] = in[8 * i + k];
            g_xf[4 * k + 1] = in[8 * i + k] ^ 0x80000000u;
        }
        sh4_ftrv(b);
        for (k = 0; k < 4; k++) out[5 * i + 1 + k] = f2u(b[k]);
    }
    f = fopen(argv[3], "wb");
    fwrite(out, 20, (size_t)n, f);
    fclose(f);
    return 0;
}
"""


def build_c(d, bits):
    (d / f"fp{bits}.c").write_text(HARNESS % fipr_source(SIDES[0]), encoding="utf-8")
    vc = str(vcvars(bits)).replace("/", "\\")
    r = subprocess.run(f'cmd /d /s /c ""{vc}" >nul 2>&1 && cl /nologo /std:c11 /W4 /WX /O2 fp{bits}.c"', cwd=d,
                       capture_output=True, text=True, errors="replace", timeout=300)
    return r, d / f"fp{bits}.exe"


def run_c(exe, d, cases, rm):
    src, dst = d / "cases.bin", d / f"out{rm}.bin"
    src.write_bytes(b"".join(struct.pack("<8I", *a, *b) for a, b in cases))
    subprocess.run([str(exe), "6000" if rm else "0", str(src), str(dst)], check=True, timeout=600)
    got = struct.unpack(f"<{5 * len(cases)}I", dst.read_bytes())
    return [["N" if is_nan(g) else g for g in got[5 * i:5 * i + 5]] for i in range(len(cases))]


class Reference(unittest.TestCase):
    def test_known(self):
        one = 0x3f800000
        (a, b), (c, _) = SEPARATING
        self.assertEqual(ref(a, b, 0), 0x30800000)  # 1 + 2^-30 - 1 = 2^-30: the double sum keeps it
        self.assertEqual(ref(a, b, 1), 0x30800000)
        self.assertEqual(ref(c, b, 1), one)  # 1 + 2^-24 + 2^-25 toward zero: 1
        self.assertEqual(ref(c, b, 0), one + 1)  # to nearest: 1 + 2^-23 (above the half ulp)
        self.assertEqual(ref([0x80000000] * 4, [one] * 4, 0), 0x80000000)  # -0 sums: -0
        self.assertEqual(ref([one, one, 0, 0], [one, 0xbf800000, 0, 0], 1), 0)  # exact cancellation: +0
        self.assertEqual(ref([0x7f7fffff] * 2 + [0] * 2, [0x40000000, one, 0, 0], 1), 0x7f7fffff)  # toward zero
        self.assertEqual(ref([0x7f7fffff] * 2 + [0] * 2, [0x40000000, one, 0, 0], 0), 0x7f800000)
        self.assertEqual(ref([0x7f800000, 0, 0, 0], [0, one, one, one], 0), "N")
        self.assertEqual(ref([0x00000001, one, 0, 0], [one, one, 0, 0], 0), one)  # a denormal source is zero
        self.assertEqual(ref([0x00800000, 0, 0, 0], [0x3f000000, 0, 0, 0], 0), 0)  # 2^-127: flushed
        self.assertEqual(ref([0x00800000, 0, 0, 0], [f2b(1 - 2.0 ** -24), 0, 0, 0], 0), "B")  # the band

    def test_known_ref_negative_product(self):
        self.assertEqual(ref([0x3f800000, 0, 0, 0], [0xbf800000, 0, 0, 0], 1), 0xbf800000)


@unittest.skipUnless(os.name == "nt" and vcvars(32) and vcvars(64), "no 32- and 64-bit Visual Studio tools")
class C(unittest.TestCase):
    def test_same_text(self):
        """One sh4_fipr/sh4_ftrv in all three runtimes (tested once below), and the declarations agents see."""
        self.assertEqual(len({fipr_source(p) for p in SIDES}), 1)
        for h in (SH4 / "runtime" / "tc_rt.h", SH4 / "runtime_rd" / "rd_rt.h"):
            self.assertIn("f32 sh4_fipr(const f32 a[4], const f32 b[4]);", text(h))
        self.assertIn("x->fr[vn + 3] = U(sh4_fipr(a, b));", text(SH4 / "xemu" / "xe_fpu.c"))
        self.assertIn("out[row] = sh4_fipr(m, v);", text(SH4 / "xemu" / "xe_fpu.c"))

    def test_exact(self):
        cases = gen(N)
        work = ROOT / "tests" / ".work"
        work.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="fipr_", dir=work) as d:
            d = pathlib.Path(d)
            for bits in (32, 64):
                r, exe = build_c(d, bits)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                for rm in (0, 1):
                    bad = mismatches(cases, rm, run_c(exe, d, cases, rm))
                    self.assertEqual(bad[:10], [], f"{bits}-bit, {len(bad)} of {5 * len(cases)}")


@unittest.skipUnless(CP and jdk("javac") and jdk("java"), "no ghidra_home or JDK on this machine")
class Oracle(unittest.TestCase):
    def run_stub(self, cases, rm, once):
        work = ROOT / "tests" / ".work"
        work.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="fiprj_", dir=work) as out:
            src = os.pathsep.join(str(SH4 / d) for d in ("ghidra_scripts", "template"))
            r = subprocess.run([jdk("javac"), "-nowarn", "-proc:none", "-d", out, "-cp", CP, "-sourcepath", src,
                                str(ROOT / "tests" / "java" / "TcFiprStub.java")], capture_output=True, text=True,
                               timeout=300)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            f = pathlib.Path(out, "cases.txt")
            f.write_text("".join(" ".join("%08x" % v for v in a + b) + "\n" for a, b in cases), encoding="ascii")
            r = subprocess.run([jdk("java"), "-cp", out + os.pathsep + CP, "TcFiprStub", str(f), str(rm),
                                str(int(once))], capture_output=True, text=True, timeout=1800)
            self.assertEqual(r.returncode, 0, r.stderr)
            rows = [[g if g in ("N", "B") else int(g, 16) for g in line.split()] for line in r.stdout.splitlines()]
            self.assertEqual(len(rows), len(cases))
            return rows

    def test_exact(self):
        cases = gen(N)
        for rm in (0, 1):
            got = self.run_stub(cases, rm, True)
            bad = mismatches(cases, rm, got)
            bad += ["%r rm%d: not rejected" % (c, rm) for c, g in zip(cases, got) if ref(*c, rm) == "B" and g[0] != "B"]
            self.assertEqual(bad[:10], [], f"{len(bad)} of {5 * len(cases)}")

    def test_seven_differs(self):
        """The p-code's seven roundings (what float multiplies and adds compute) differ on the separating cases."""
        for rm in (0, 1):
            once, seven = self.run_stub(SEPARATING, rm, True), self.run_stub(SEPARATING, rm, False)
            self.assertEqual(seven[0][0], 0)  # 1 + 2^-30 rounds to 1 in single, then - 1
            self.assertNotEqual(once[0][0], seven[0][0])
            self.assertEqual(once[0][1:3], [0x30800000, 0xb0800000])  # ftrv rows 0 and 1: +-fipr


class Wiring(unittest.TestCase):
    def test_text(self):
        fpu, rnd = text(SH4 / "ghidra_scripts" / "TcFpu.java"), text(SH4 / "ghidra_scripts" / "TcRound.java")
        self.assertIn('static final Set<String> ONCE = Set.of("fmac", "fipr", "ftrv");', fpu)
        self.assertIn("if (!facts.fpscrModel && !once) continue;", fpu)  # with or without fpscr_model, as fmac
        self.assertIn("TcRound.apply(op, rn, 0, st.pr, false);", fpu)  # the p-code check: seven roundings
        self.assertIn("TcRound.apply(op, rz, st.rm, st.pr, true);", fpu)  # the result: one
        self.assertIn('regs.put("fr" + (op.n + 3), fb(once ? fipr(x, y, rm) : t));', rnd)
        self.assertIn("if (once) v[row] = fipr(x, in, rm);", rnd)
        self.assertIn("TcFpu.once(this, proto, seed)", text(SH4 / "ghidra_scripts" / "TcPrepare.java"))
        from core import agent
        self.assertEqual((agent.SPECIAL_HELPER["fipr"], agent.SPECIAL_HELPER["ftrv"]), ("sh4_fipr", "sh4_ftrv"))


if __name__ == "__main__":
    unittest.main()
