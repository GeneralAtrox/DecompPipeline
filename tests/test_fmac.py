"""fmac rounds once (SH-4 software manual 6.4; 9.36 normal_fmac): every side against exact integer arithmetic.
1. ref(): the exact value fr0*frm + frn (Python integers: no rounding anywhere), rounded once onto binary32 to nearest
   (RM=0) or toward zero (RM=1), under FPSCR.DN=1: denormal sources are signed zeros, a denormal result a signed
   zero; IEEE signs for zero sums (the manual's FMAC special-case table); an exact value in [2^-126 - 2^-150, 2^-126)
   is "B" (the oracle rejects the vector: TcRound.nearMinNormal).
2. The C: sh4_fmac, the same text in tc_rt.c, rd_sh4.c and the port's sh4_rt.c, built 32-bit (the readable runner,
   the port) and 64-bit (the judge's runner, xemu), run with MXCSR as the runners set it (DAZ|FTZ, RC from FPSCR.RM).
3. The oracle: TcRound.fma through TcFpu's DN steps (tests/java/TcFmacStub.java).
Cases: every combination of 26 corner operands (zeros, infinities, NaN, extremes, denormals, ties), then random ones
built to hit the hard spots: cancellation, products exactly halfway between two floats plus a tiny addend (where a
double sum rounded to nearest and then to float would round twice), overflow, the smallest normal. FMAC_N sets the
random count (default 20000; the proof ran 2,000,000 per mode).
Wiring (needs Ghidra to run): TcFpu recomputes fmac with and without fpscr_model, checks the p-code with two
roundings and keeps one; xemu and the agent rules use sh4_fmac.
Run: python -W error tests/test_fmac.py
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

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "port" / "tools"), str(ROOT / "tests")]
import game_config  # noqa: E402
from test_fsca_rm import CP, jdk  # noqa: E402  (Ghidra's jars and the JDK, as the fsca stub uses them)

SH4 = ROOT / "targets" / "dreamcast_sh4"
SIDES = [SH4 / "runtime" / "tc_rt.c", SH4 / "runtime_rd" / "rd_sh4.c", ROOT / "port" / "runtime" / "sh4_rt.c"]
N = int(os.environ.get("FMAC_N", "20000"))


def text(p):
    return p.read_text(encoding="utf-8").replace("\r\n", "\n")


def fmac_source(p):
    """sh4_fmac with its comment, as the file has it."""
    return re.search(r"/\* fmac FR0,FRm,FRn:.*?\nf32 sh4_fmac\(f32 fr0, f32 frm, f32 frn\)\n\{\n.*?\n\}\n", text(p),
                     re.S).group(0)


# ---------- the exact reference ----------
def split(b):
    """bits -> (sign, integer mantissa, exponent) with value = m * 2^e, a denormal as a signed zero (FPSCR.DN)."""
    s, ex, fr = b >> 31, (b >> 23) & 0xff, b & 0x7fffff
    if ex == 0:
        return s, 0, 0
    return s, fr | 0x800000, ex - 150


def is_nan(b):
    return (b & 0x7f800000) == 0x7f800000 and b & 0x7fffff


def is_inf(b):
    return (b & 0x7fffffff) == 0x7f800000


def ref(a, b, c, rm):
    """fr0*frm + frn rounded once: result bits, "N" (a NaN) or "B" (the oracle rejects the vector)."""
    if is_nan(a) or is_nan(b) or is_nan(c):
        return "N"
    sa, ma, ea = split(a)
    sb, mb, eb = split(b)
    sc, mc, ec = split(c)
    sp = sa ^ sb
    if is_inf(a) or is_inf(b):
        if (not is_inf(a) and ma == 0) or (not is_inf(b) and mb == 0):
            return "N"  # inf * 0
        if is_inf(c) and sc != sp:
            return "N"  # inf - inf
        return sp << 31 | 0x7f800000
    if is_inf(c):
        return c
    mp, ep = ma * mb, ea + eb
    if mp == 0 and mc == 0:
        return (sp if sp == sc else 0) << 31  # IEEE: equal signs keep theirs, opposite give +0 (RN and RZ)
    if mp == 0:
        return c
    e = min(ep, ec) if mc else ep
    n = (-mp if sp else mp) << (ep - e)
    if mc:
        n += (-mc if sc else mc) << (ec - e)
    if n == 0:
        return 0  # exact cancellation: +0
    s, n = int(n < 0), abs(n)
    lo, hi = (1 << 24) - 1, 1 << 24  # the band [2^-126 - 2^-150, 2^-126) in units of 2^-150
    x, k = (n << (e + 150), 0) if e >= -150 else (n, -150 - e)
    if (lo << k) <= x < (hi << k):
        return "B"
    top = e + n.bit_length() - 1  # 2^top <= value < 2^(top+1)
    q = max(top, -126) - 23  # the result's quantum
    sh = q - e
    if sh <= 0:
        r = n << -sh
    else:
        r, rem, half = n >> sh, n & ((1 << sh) - 1), 1 << (sh - 1)
        if rm == 0 and (rem > half or (rem == half and r & 1)):
            r += 1
    if r == 1 << 24:
        r, q = r >> 1, q + 1
    if r < 1 << 23:
        return s << 31  # a denormal result (or zero): flushed (FPSCR.DN)
    if q + 150 >= 255:
        return s << 31 | (0x7f800000 if rm == 0 else 0x7f7fffff)  # overflow: infinity, toward zero the largest
    return s << 31 | (q + 150) << 23 | (r - (1 << 23))


# ---------- cases ----------
def f2b(x):
    return struct.unpack("<I", struct.pack("<f", x))[0]


CORNERS = [0x00000000, 0x80000000, 0x3f800000, 0xbf800000, 0x7f800000, 0xff800000, 0x7fc00000, 0x7f7fffff,
           0xff7fffff, 0x00800000, 0x80800000, 0x00000001, 0x807fffff, 0x3f800001, 0x3fffffff, 0x4b800001,
           0x33800000, 0xb3800000, 0x1f800000, 0x5f800000, 0x00800001, 0x3f000000, 0x40400000, 0xc0400000,
           0x34000000, 0x7f000000]


def bits_of(m, e, s=0):
    """The float m * 2^e (m < 2^24), as bits."""
    return f2b((-1) ** s * m * 2.0 ** e)


def gen(n, seed=1):
    rnd = random.Random(seed)
    out = [(a, b, c) for a in CORNERS for b in CORNERS for c in CORNERS]
    while len(out) < len(CORNERS) ** 3 + n:
        k = rnd.randrange(7)
        if k == 0:  # any bits
            out.append(tuple(rnd.getrandbits(32) for _ in range(3)))
            continue
        if k == 1:  # cancellation: c near -(a*b)
            a = bits_of(rnd.getrandbits(24) | 1 << 23, rnd.randrange(-60, 40), rnd.randrange(2))
            b = bits_of(rnd.getrandbits(24) | 1 << 23, rnd.randrange(-60, 40), rnd.randrange(2))
            p = struct.unpack("<f", struct.pack("<I", a))[0] * struct.unpack("<f", struct.pack("<I", b))[0]
            c = (f2b(-p) + rnd.randrange(-3, 4)) & 0xffffffff
            out.append((a, b, c))
            continue
        if k == 2:  # a product exactly halfway between two floats (25 significant bits), a tiny addend or none
            while True:
                ma, mb = rnd.getrandbits(13) | 1, rnd.getrandbits(13) | 1
                if (ma * mb).bit_length() == 25:
                    break
            ea, eb = rnd.randrange(-40, 30), rnd.randrange(-40, 30)
            a, b = bits_of(ma, ea, rnd.randrange(2)), bits_of(mb, eb, rnd.randrange(2))
            tiny = rnd.choice([0, bits_of(1 << 23, ea + eb - rnd.randrange(30, 120), rnd.randrange(2))])
            out.append((a, b, tiny))
            continue
        if k == 3:  # overflow and the largest values
            a = bits_of(rnd.getrandbits(24) | 1 << 23, rnd.randrange(40, 104), rnd.randrange(2))
            b = bits_of(rnd.getrandbits(24) | 1 << 23, rnd.randrange(0, 40), rnd.randrange(2))
            c = rnd.choice([0x7f7fffff, 0xff7fffff, rnd.getrandbits(32)])
            out.append((a, b, c))
            continue
        if k == 4:  # around the smallest normal
            a = bits_of(rnd.getrandbits(24) | 1 << 23, rnd.randrange(-90, -50), rnd.randrange(2))
            b = bits_of(rnd.getrandbits(24) | 1 << 23, rnd.randrange(-100, -60), rnd.randrange(2))
            c = rnd.choice([0, 0x00800000, 0x80800000, (0x00800000 + rnd.randrange(64)) | rnd.randrange(2) << 31])
            out.append((a, b, c))
            continue
        if k == 5:  # small integers: mostly exact
            out.append(tuple(f2b(float(rnd.randrange(-4096, 4096))) for _ in range(3)))
            continue
        a = bits_of(rnd.getrandbits(24) | 1 << 23, rnd.randrange(-30, 10), rnd.randrange(2))  # ordinary game values
        b = bits_of(rnd.getrandbits(24) | 1 << 23, rnd.randrange(-30, 10), rnd.randrange(2))
        out.append((a, b, bits_of(rnd.getrandbits(24) | 1 << 23, rnd.randrange(-50, 20), rnd.randrange(2))))
    return out


def agree(want, got):
    return got == "N" if want == "N" else want == "B" or got == want


# ---------- the C sides ----------
HARNESS = """#define _CRT_SECURE_NO_WARNINGS
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <emmintrin.h>
#include <xmmintrin.h>
typedef float f32;
typedef unsigned int u32;
%s
int main(int argc, char **argv)
{
    FILE *f;
    long n, i;
    u32 *in, *out;
    if (argc < 4) return 2;
    f = fopen(argv[2], "rb");
    fseek(f, 0, SEEK_END);
    n = ftell(f) / 12;
    fseek(f, 0, SEEK_SET);
    in = (u32 *)malloc((size_t)n * 12);
    out = (u32 *)malloc((size_t)n * 4);
    if (fread(in, 12, (size_t)n, f) != (size_t)n) return 3;
    fclose(f);
    _mm_setcsr((_mm_getcsr() & ~0x6000u) | 0x8040u | (u32)strtoul(argv[1], NULL, 16)); /* DAZ|FTZ, RC */
    for (i = 0; i < n; i++) {
        f32 a, b, c, r;
        memcpy(&a, &in[3 * i], 4);
        memcpy(&b, &in[3 * i + 1], 4);
        memcpy(&c, &in[3 * i + 2], 4);
        r = sh4_fmac(a, b, c);
        memcpy(&out[i], &r, 4);
    }
    f = fopen(argv[3], "wb");
    fwrite(out, 4, (size_t)n, f);
    fclose(f);
    return 0;
}
"""


def vcvars(bits):
    if bits == 32:
        return game_config.vcvars32()
    from core import paths
    vc = paths.config().get("vcvars")
    return pathlib.Path(vc) if vc and pathlib.Path(vc).is_file() else None


def build_c(d, bits):
    (d / f"fm{bits}.c").write_text(HARNESS % fmac_source(SIDES[0]), encoding="utf-8")
    vc = str(vcvars(bits)).replace("/", "\\")
    r = subprocess.run(f'cmd /d /s /c ""{vc}" >nul 2>&1 && cl /nologo /std:c11 /W4 /WX /O2 fm{bits}.c"', cwd=d,
                       capture_output=True, text=True, errors="replace", timeout=300)
    return r, d / f"fm{bits}.exe"


def run_c(exe, d, cases, rm):
    src, dst = d / "cases.bin", d / f"out{rm}.bin"
    src.write_bytes(b"".join(struct.pack("<3I", *c) for c in cases))
    subprocess.run([str(exe), "6000" if rm else "0", str(src), str(dst)], check=True, timeout=600)
    got = struct.unpack(f"<{len(cases)}I", dst.read_bytes())
    return ["N" if is_nan(g) else g for g in got]


def show(x):
    return x if isinstance(x, str) else "%08x" % x


def mismatches(cases, rm, got):
    bad = []
    for c, g in zip(cases, got):
        w = ref(*c, rm)
        if not agree(w, g):
            bad.append("%08x %08x %08x rm%d: want %s got %s" % (c + (rm, show(w), show(g))))
    return bad


class Reference(unittest.TestCase):
    def test_known(self):
        """The reference on hand-checked cases: one rounding where two would differ."""
        one, ulp = 0x3f800000, 0x3f800001  # 1.0, 1 + 2^-23
        h = f2b(1 + 2.0 ** -12)  # (1 + 2^-12)^2 - 1 = 2^-11 + 2^-24: once, exact; twice, the product's tie loses 2^-24
        self.assertEqual(ref(h, h, 0xbf800000, 0), f2b(2.0 ** -11 + 2.0 ** -24))
        self.assertEqual(ref(ulp, ulp, 0, 0), 0x3f800002)  # 1 + 2^-22 (+2^-46 below the half ulp)
        self.assertEqual(ref(ulp, ulp, 0, 1), 0x3f800002)
        # a product halfway between 1 + 2^-22 and 1 + 2^-21's neighbour, plus a tiny positive: up when rounded once
        a, b = f2b(1 + 2.0 ** -12), f2b(1 + 2.0 ** -12)  # (1 + 2^-12)^2 = 1 + 2^-11 + 2^-24: halfway
        self.assertEqual(ref(a, b, 0, 0), f2b(1 + 2.0 ** -11))  # tie to even
        self.assertEqual(ref(a, b, 0x00800000, 0), f2b(1 + 2.0 ** -11) + 1)  # just above halfway: up
        self.assertEqual(ref(a, b, 0x80800000, 1), f2b(1 + 2.0 ** -11))  # toward zero
        self.assertEqual(ref(0x00000000, one, 0x80000000, 0), 0x00000000)  # +0 + -0 = +0
        self.assertEqual(ref(0x80000000, one, 0x80000000, 1), 0x80000000)  # -0 + -0 = -0
        self.assertEqual(ref(one, one, 0xbf800000, 1), 0)  # exact cancellation: +0
        self.assertEqual(ref(0x7f7fffff, 0x40000000, 0xff7fffff, 0), 0x7f7fffff)  # 2max - max: no overflow in between
        self.assertEqual(ref(0x7f7fffff, 0x40000000, 0, 1), 0x7f7fffff)  # overflow toward zero: the largest
        self.assertEqual(ref(0x7f7fffff, 0x40000000, 0, 0), 0x7f800000)
        self.assertEqual(ref(0x7f800000, 0, one, 0), "N")
        self.assertEqual(ref(0x00000001, one, one, 0), one)  # a denormal source is zero
        self.assertEqual(ref(0x00800000, 0x3f000000, 0, 0), 0)  # 2^-127: flushed
        self.assertEqual(ref(0x00800000, f2b(1 - 2.0 ** -24), 0, 0), "B")  # 2^-126 - 2^-150: the band


@unittest.skipUnless(os.name == "nt" and vcvars(32) and vcvars(64), "no 32- and 64-bit Visual Studio tools")
class C(unittest.TestCase):
    def test_same_text(self):
        """One sh4_fmac in all three runtimes (tested once below), and the declarations agents see."""
        self.assertEqual(len({fmac_source(p) for p in SIDES}), 1)
        for h in (SH4 / "runtime" / "tc_rt.h", SH4 / "runtime_rd" / "rd_rt.h"):
            self.assertIn("f32 sh4_fmac(f32 fr0, f32 frm, f32 frn);", text(h))

    def test_exact(self):
        cases = gen(N)
        work = ROOT / "tests" / ".work"
        work.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="fmac_", dir=work) as d:
            d = pathlib.Path(d)
            for bits in (32, 64):
                r, exe = build_c(d, bits)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                for rm in (0, 1):
                    bad = mismatches(cases, rm, run_c(exe, d, cases, rm))
                    self.assertEqual(bad[:10], [], f"{bits}-bit, {len(bad)} of {len(cases)}")


@unittest.skipUnless(CP and jdk("javac") and jdk("java"), "no ghidra_home or JDK on this machine")
class Oracle(unittest.TestCase):
    def test_exact(self):
        cases = gen(N)
        work = ROOT / "tests" / ".work"
        work.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="fmacj_", dir=work) as out:
            src = os.pathsep.join(str(SH4 / d) for d in ("ghidra_scripts", "template"))
            r = subprocess.run([jdk("javac"), "-nowarn", "-proc:none", "-d", out, "-cp", CP, "-sourcepath", src,
                                str(ROOT / "tests" / "java" / "TcFmacStub.java")], capture_output=True, text=True,
                               timeout=300)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            f = pathlib.Path(out, "cases.txt")
            f.write_text("".join("%08x %08x %08x\n" % c for c in cases), encoding="ascii")
            for rm in (0, 1):
                r = subprocess.run([jdk("java"), "-cp", out + os.pathsep + CP, "TcFmacStub", str(f), str(rm)],
                                   capture_output=True, text=True, timeout=1800)
                self.assertEqual(r.returncode, 0, r.stderr)
                got = [g if g in ("N", "B") else int(g, 16) for g in r.stdout.split()]
                self.assertEqual(len(got), len(cases))
                bad = mismatches(cases, rm, got)
                bad += ["%08x %08x %08x rm%d: not rejected" % (c + (rm,)) for c, g in zip(cases, got)
                        if ref(*c, rm) == "B" and g != "B"]
                self.assertEqual(bad[:10], [], f"{len(bad)} of {len(cases)}")


class Wiring(unittest.TestCase):
    def test_text(self):
        fpu, rnd = text(SH4 / "ghidra_scripts" / "TcFpu.java"), text(SH4 / "ghidra_scripts" / "TcRound.java")
        self.assertIn('if (st.snap == null && list.stream().anyMatch(o -> o.kind.equals("fmac")))', fpu)
        self.assertIn("if (!facts.fpscrModel && !fmac) continue;", fpu)
        self.assertIn("TcRound.apply(op, rn, 0, st.pr, false);", fpu)  # the p-code check: two roundings
        self.assertIn("TcRound.apply(op, rz, st.rm, st.pr, true);", fpu)  # the result: one
        self.assertNotIn('"fr0*fr" + op.m', fpu)  # no intermediate product to reject
        self.assertIn("regs.put(a[2], fb(once ? fma(x, y, z, rm) : add(mul(x, y, rm), z, rm)));", rnd)
        xe = text(SH4 / "xemu" / "xe_fpu.c")
        self.assertIn("x->fr[n] = U(sh4_fmac(F(x->fr[0]), F(x->fr[m]), F(x->fr[n])));", xe)
        from core import agent
        self.assertEqual(agent.SPECIAL_HELPER["fmac"], "sh4_fmac")
        self.assertIn('m.matches("fsca|fsrra|fsqrt|fmac|', text(SH4 / "ghidra_scripts" / "TcPrepare.java"))


if __name__ == "__main__":
    unittest.main()
