"""The Xbox 360 target's instruction helpers (runtime/tc_ppc.c) against an independent Python model written from
the architecture books, in both fma modes (MSVC; split from test_xbox360_ppc.py).

usage: python tests/test_xbox360_helpers.py
"""
import os
import pathlib
import random
import shutil
import struct
import subprocess
import sys
import unittest
from fractions import Fraction

sys.path[:0] = [str(pathlib.Path(__file__).resolve().parent)]
from test_xbox360_ppc import X360, tmpdir  # noqa: E402
from test_xbox360_glue import cl, vcvars64  # noqa: E402


# ---------- the instruction helpers against a Python model of the architecture ----------

M64 = (1 << 64) - 1


def s32(v):
    v &= 0xFFFFFFFF
    return v - (1 << 32) if v >> 31 else v


def s64(v):
    v &= M64
    return v - (1 << 64) if v >> 63 else v


def mask(x, y):
    """MASK(x, y) of the manuals: bits x..y set, bit 0 the most significant; wraps when x > y."""
    bits = range(x, y + 1) if x <= y else list(range(0, y + 1)) + list(range(x, 64))
    return sum(1 << (63 - b) for b in bits)


def rotl(v, n):
    n %= 64
    return ((v << n) | (v >> (64 - n))) & M64 if n else v


def rotl32(v, n):
    lo = v & 0xFFFFFFFF
    return rotl((lo << 32) | lo, n % 32)


def dbl(bits):
    return struct.unpack(">d", struct.pack(">Q", bits))[0]


def bits(x):
    return struct.unpack(">Q", struct.pack(">d", x))[0]


def round_bin(q, p, emin, emax):
    """The rational q rounded to nearest even in a binary format with p significant bits (subnormals below emin);
    returns a Python float (exact for these formats) or +-inf."""
    if q == 0:
        return 0.0
    sign = -1 if q < 0 else 1
    q = abs(q)
    e = q.numerator.bit_length() - q.denominator.bit_length()
    if Fraction(2) ** e > q:
        e -= 1
    e = max(e, emin)
    ulp = Fraction(2) ** (e - p + 1)
    n, rem = divmod(q, ulp)
    n = int(n)
    if rem > ulp / 2 or (rem == ulp / 2 and n % 2):
        n += 1
    v = n * ulp
    if v >= Fraction(2) ** (emax + 1):
        return sign * float("inf")
    return sign * float(v)


def to_single(q):
    return round_bin(q, 24, -126, 127)


def model_fma(a, c, b, fused, single, sub=False, neg=False):
    A, C, B = dbl(a), dbl(c), dbl(b)
    if any(x != x or x in (float("inf"), float("-inf")) for x in (A, C, B)):
        return None
    B = -B if sub else B
    if fused:
        q = Fraction(A) * Fraction(C) + Fraction(B)
        r = to_single(q) if single else round_bin(q, 53, -1022, 1023)
        if q == 0:
            r = A * C + B  # the sign of an exact zero follows IEEE (Python's double ops give it)
    else:
        p = A * C
        s = p + B
        r = to_single(Fraction(s)) if single and s == s and abs(s) != float("inf") else s
        if single and s == 0:
            r = s
    if r != r:
        return None
    return bits(-r if neg else r)


def model_single_bits(x):
    v = bits(x)
    e = (v >> 52) & 0x7FF
    sel = ((v >> 32) & 0xC0000000) | ((v >> 29) & 0x3FFFFFFF)
    X = dbl(v)
    if 874 <= e <= 896:  # below the single normal range: truncated to a multiple of 2^-149
        return ((v >> 32) & 0x80000000) | int(abs(Fraction(X)) * 2 ** 149)
    if e > 896 and abs(X) <= 3.4028234663852886e38:  # in range: the significand truncated to 24 bits
        q = abs(Fraction(X))
        ex = q.numerator.bit_length() - q.denominator.bit_length()
        if Fraction(2) ** ex > q:
            ex -= 1
        ulp = Fraction(2) ** (ex - 23)
        t = float(int(q / ulp) * ulp)
        want = struct.unpack(">I", struct.pack(">f", t))[0] | ((v >> 32) & 0x80000000)
        assert want == sel, (hex(v), hex(want), hex(sel))
    return sel


def model_conv(x, width, nearest):
    X = dbl(x)
    lo, hi = -(1 << (width - 1)), (1 << (width - 1)) - 1
    if X != X:
        return lo
    if X in (float("inf"), float("-inf")):
        return hi if X > 0 else lo
    if nearest:
        f = Fraction(X)
        n = f.numerator // f.denominator
        r = f - n
        if r > Fraction(1, 2) or (r == Fraction(1, 2) and n % 2):
            n += 1
    else:
        n = int(X)
    return max(lo, min(hi, n))


INT_OPS = {  # name: (C call format, Python model, argument kinds)
    "rlwinm": ("ppc_rlwinm({a}, {i}, {j}, {k})", lambda a, b, i, j, k: rotl32(a, i) & mask(j + 32, k + 32)),
    "rlwnm": ("ppc_rlwnm({a}, {b}, {j}, {k})", lambda a, b, i, j, k: rotl32(a, b & 31) & mask(j + 32, k + 32)),
    "rlwimi": ("ppc_rlwimi({b}, {a}, {i}, {j}, {k})",
               lambda a, b, i, j, k: (rotl32(a, i) & mask(j + 32, k + 32)) | (b & ~mask(j + 32, k + 32) & M64)),
    "rldicl": ("ppc_rldicl({a}, {i2}, {j2})", lambda a, b, i, j, k, i2, j2, k2: rotl(a, i2) & mask(j2, 63)),
    "rldicr": ("ppc_rldicr({a}, {i2}, {k2})", lambda a, b, i, j, k, i2, j2, k2: rotl(a, i2) & mask(0, k2)),
    "rldic": ("ppc_rldic({a}, {i2}, {j2})", lambda a, b, i, j, k, i2, j2, k2: rotl(a, i2) & mask(j2, 63 - i2)),
    "rldimi": ("ppc_rldimi({b}, {a}, {i2}, {j2})", lambda a, b, i, j, k, i2, j2, k2:
               (rotl(a, i2) & mask(j2, 63 - i2)) | (b & ~mask(j2, 63 - i2) & M64)),
    "rldcl": ("ppc_rldcl({a}, {b}, {j2})", lambda a, b, i, j, k, i2, j2, k2: rotl(a, b & 63) & mask(j2, 63)),
    "rldcr": ("ppc_rldcr({a}, {b}, {k2})", lambda a, b, i, j, k, i2, j2, k2: rotl(a, b & 63) & mask(0, k2)),
    "slw": ("ppc_slw({a}, {b})", lambda a, b, *_: 0 if b & 32 else ((a & 0xFFFFFFFF) << (b & 31)) & 0xFFFFFFFF),
    "srw": ("ppc_srw({a}, {b})", lambda a, b, *_: 0 if b & 32 else (a & 0xFFFFFFFF) >> (b & 31)),
    "sld": ("ppc_sld({a}, {b})", lambda a, b, *_: 0 if b & 64 else (a << (b & 63)) & M64),
    "srd": ("ppc_srd({a}, {b})", lambda a, b, *_: 0 if b & 64 else a >> (b & 63)),
    "cntlzw": ("ppc_cntlzw({a})", lambda a, *_: 32 - (a & 0xFFFFFFFF).bit_length()),
    "cntlzd": ("ppc_cntlzd({a})", lambda a, *_: 64 - a.bit_length()),
    "mullw": ("ppc_mullw({a}, {b})", lambda a, b, *_: (s32(a) * s32(b)) & M64),
    "mulhw": ("ppc_mulhw({a}, {b})", lambda a, b, *_: ((s32(a) * s32(b)) >> 32) & 0xFFFFFFFF),
    "mulhwu": ("ppc_mulhwu({a}, {b})", lambda a, b, *_: ((a & 0xFFFFFFFF) * (b & 0xFFFFFFFF)) >> 32),
    "mulhd": ("ppc_mulhd({a}, {b})", lambda a, b, *_: ((s64(a) * s64(b)) >> 64) & M64),
    "mulhdu": ("ppc_mulhdu({a}, {b})", lambda a, b, *_: (a * b) >> 64),
}


def _sra(v, n, width):
    """(result, CA) of an arithmetic right shift of the signed width-bit v by n (n >= width: all sign bits)."""
    if n >= width:
        return (-1 if v < 0 else 0), int(v < 0 and v != 0)
    return v >> n, int(v < 0 and (v & ((1 << n) - 1)) != 0)


def _div(x, y):
    q = abs(x) // abs(y)
    return q if (x < 0) == (y < 0) else -q


def model_int_extra(a, b, ca):
    """The ops with a carry or a special case: [(C expression, value)]."""
    out = []
    for name, n, width, val in (("sraw", b & 63, 32, s32(a)), ("srad", b & 127, 64, s64(a)),
                                ("srawi", b & 31, 32, s32(a)), ("sradi", b & 63, 64, s64(a))):
        r, c = _sra(val, n, width)
        arg = f"{b & 31}" if name == "srawi" else f"{b & 63}" if name == "sradi" else f"0x{b:x}ull"
        out.append((f"ppc_{name}(0x{a:x}ull, {arg}, &ca)", r & M64, c))
    for name, x, y in (("divw", s32(a), s32(b)), ("divd", s64(a), s64(b))):
        if y == 0:
            continue
        width = 32 if name == "divw" else 64
        q = _div(x, y)
        if q == 1 << (width - 1):
            q = -q  # MIN / -1 wraps
        out.append((f"ppc_{name}(0x{a:x}ull, 0x{b:x}ull)", q & M64, None))
    if b & 0xFFFFFFFF:
        out.append((f"ppc_divwu(0x{a:x}ull, 0x{b:x}ull)", (a & 0xFFFFFFFF) // (b & 0xFFFFFFFF), None))
    if b:
        out.append((f"ppc_divdu(0x{a:x}ull, 0x{b:x}ull)", a // b, None))
    t = a + b
    out.append((f"ppc_addc(0x{a:x}ull, 0x{b:x}ull, &ca)", t & M64, t >> 64))
    t = a + b + ca
    out.append((f"ppc_adde(0x{a:x}ull, 0x{b:x}ull, {ca}u, &ca)", t & M64, t >> 64))
    out.append((f"ppc_subfc(0x{a:x}ull, 0x{b:x}ull, &ca)", (b - a) & M64, int(b >= a)))
    t = (~a & M64) + b + ca
    out.append((f"ppc_subfe(0x{a:x}ull, 0x{b:x}ull, {ca}u, &ca)", t & M64, t >> 64))
    return out


EDGE64 = [0, 1, 2, 0x7FFFFFFF, 0x80000000, 0xFFFFFFFF, 0x100000000, 0x7FFFFFFFFFFFFFFF, 0x8000000000000000,
          M64, 0xFFFFFFFF80000000, 0x00000000FFFFFFFE, 31, 32, 33, 63, 64, 65, 127, 128]


def int_cases(rng, n=400):
    out = []
    for k in range(n):
        a = EDGE64[k % len(EDGE64)] if k < 2 * len(EDGE64) else rng.getrandbits(64)
        b = EDGE64[(k * 7) % len(EDGE64)] if k < 2 * len(EDGE64) else rng.choice(
            [rng.getrandbits(64), rng.randrange(130), (1 << 64) - rng.randrange(1, 9)])
        out.append((a, b, rng.randrange(32), rng.randrange(32), rng.randrange(32), rng.randrange(64),
                    rng.randrange(64), rng.randrange(64), rng.randrange(2)))
    return out


def float_cases(rng, n=300):
    specials = [0.0, -0.0, 1.0, -1.0, 0.5, 1.5, 2.5, -2.5, 3.5, 1e-310, -1e-310, 1e300, 2147483647.5, 2147483648.0,
                -2147483648.5, -2147483649.0, 4294967296.5, 9.3e18, -9.3e18, 1.401298464324817e-45,
                1.1754943508222875e-38, 1.1754942e-38, 3.4028235677973366e38, 3.5e38, 1 / 3, float("nan"),
                float("inf"), float("-inf"), 7e-46, 1.1]
    out = [bits(x) for x in specials]
    for _ in range(n):
        kind = rng.randrange(4)
        if kind == 0:
            out.append(rng.getrandbits(64))
        elif kind == 1:
            out.append(bits(rng.uniform(-1e10, 1e10)))
        elif kind == 2:
            out.append(bits(struct.unpack(">f", struct.pack(">f", rng.uniform(-100, 100)))[0]))
        else:
            out.append(bits(rng.uniform(-4, 4) * 2.0 ** rng.randrange(-150, -120)))
    return out


def fma_triples(rng, n=300):
    one = 1.0
    t = [  # (a, c, b) where one and two roundings differ, and the single-rounding trap
        (one + 2 ** -30, one - 2 ** -30, -one),
        (one + 2 ** -40, one + 2 ** -40, 2 ** -24 - 2 ** -39),
        (1.100000023841858, 1.100000023841858, 1.100000023841858),
        (2 ** -60, 2 ** -60, 0.0), (3.0, 1 / 3, -1.0), (0.1, 10.0, -1.0), (1e308, 10.0, -1e308),
        (2 ** -537, 2 ** -537, 0.0), (5.0, 0.0, -0.0), (-0.0, 1.0, 0.0),
    ]
    out = [(bits(a), bits(c), bits(b)) for a, c, b in t]
    for _ in range(n):
        f = [struct.unpack(">f", struct.pack(">f", rng.uniform(-8, 8)))[0] for _ in range(3)]
        if rng.randrange(2):
            f = [rng.uniform(-8, 8) * 2.0 ** rng.randrange(-30, 30) for _ in range(3)]
        out.append(tuple(bits(x) for x in f))
    return out


def helper_program(fused):
    rng = random.Random(360)
    checks = []  # (C expression printed as %016llx, expected or None, kind)
    for a, b, i, j, k, i2, j2, k2, ca in int_cases(rng):
        for name, (fmt, model) in INT_OPS.items():
            expr = fmt.format(a=f"0x{a:x}ull", b=f"0x{b:x}ull", i=i, j=j, k=k, i2=i2, j2=j2, k2=k2)
            try:
                want = model(a, b, i, j, k, i2, j2, k2)
            except TypeError:
                want = model(a, b, i, j, k)
            checks.append((expr, want & M64, None))
        for expr, want, c in model_int_extra(a, b, ca):
            checks.append((expr, want, c))
    for x in float_cases(rng):
        X = f"bitsd(0x{x:x}ull)"
        checks.append((f"ppc_single_bits({X})", model_single_bits(dbl(x)), None))
        checks.append((f"(u64)(u32)ppc_fctiwz({X})", model_conv(x, 32, False) & 0xFFFFFFFF, None))
        checks.append((f"(u64)(u32)ppc_fctiw({X})", model_conv(x, 32, True) & 0xFFFFFFFF, None))
        checks.append((f"(u64)ppc_fctidz({X})", model_conv(x, 64, False) & M64, None))
        checks.append((f"(u64)ppc_fctid({X})", model_conv(x, 64, True) & M64, None))
        checks.append((f"dbits(ppc_fcfid(0x{x:x}ull))", bits(float(s64(x))), None))
        checks.append((f"dbits(ppc_fneg({X}))", x ^ (1 << 63), None))
        checks.append((f"dbits(ppc_fnabs({X}))", x | (1 << 63), None))
    for a, c, b in fma_triples(rng):
        A, C, B = (f"bitsd(0x{v:x}ull)" for v in (a, c, b))
        for name, single, sub, neg in (("fmadd", 0, 0, 0), ("fmsub", 0, 1, 0), ("fnmadd", 0, 0, 1),
                                       ("fnmsub", 0, 1, 1), ("fmadds", 1, 0, 0), ("fmsubs", 1, 1, 0),
                                       ("fnmadds", 1, 0, 1), ("fnmsubs", 1, 1, 1)):
            want = model_fma(a, c, b, fused, single, sub, neg)
            if want is not None:
                checks.append((f"dbits(ppc_{name}({A}, {C}, {B}))", want, None))
    lines = ["#include <stdio.h>", "#include <string.h>", '#include "tc_rt.h"',
             "static u64 dbits(f64 x) { u64 u; memcpy(&u, &x, 8); return u; }",
             "static f64 bitsd(u64 u) { f64 x; memcpy(&x, &u, 8); return x; }",
             "int main(void)", "{", "    u32 ca = 0;", "    (void)ca;"]
    for expr, _, c in checks:
        if c is None:
            lines.append(f'    printf("%016llx\\n", (unsigned long long)({expr}));')
        else:
            lines.append(f"    ca = 9u;")
            lines.append(f'    {{ u64 v = {expr}; printf("%016llx %u\\n", (unsigned long long)v, ca); }}')
    lines += ["    return 0;", "}"]
    return "\n".join(lines) + "\n", checks


@unittest.skipUnless(os.name == "nt" and vcvars64(), "no Visual Studio tools on this machine")
class Helpers(unittest.TestCase):
    def check_mode(self, fused):
        src, checks = helper_program(fused)
        d = tmpdir("x360hp_")
        try:
            (d / "t.c").write_text(src, encoding="utf-8")
            rt = X360 / "runtime"
            rc, out = cl(d, f'/DTC_FMA_FUSED={int(fused)} /I"{rt}" /Fet.exe t.c "{rt / "tc_ppc.c"}"', timeout=600)
            self.assertEqual(rc, 0, out[-3000:])
            run = subprocess.run([str(d / "t.exe")], capture_output=True, text=True, timeout=120)
            got = run.stdout.split("\n")
        finally:
            shutil.rmtree(d, ignore_errors=True)
        bad = []
        for (expr, want, c), line in zip(checks, got):
            parts = line.split()
            v = int(parts[0], 16)
            if v != want or (c is not None and int(parts[1]) != c):
                bad.append(f"{expr}: want {want:016x}{'' if c is None else f' ca={c}'}, got {line}")
        self.assertEqual(len(got) - 1, len(checks))
        self.assertEqual(bad[:10], [], f"{len(bad)} of {len(checks)} differ")

    def test_two_roundings(self):
        self.check_mode(False)

    def test_fused(self):
        self.check_mode(True)


if __name__ == "__main__":
    unittest.main()
