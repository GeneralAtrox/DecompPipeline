"""Rebuilds the SH-4 FSCA ROM and its 65,536-entry sine table from first principles.

    python fsca_rom.py                   print the ROM and the table's SHA-256
    python fsca_rom.py --table OUT.bin   also write the table
    python fsca_rom.py --check DUMP.bin  compare the table with a dump of your own
    python fsca_rom.py --rz              the round-toward-zero table (FPSCR.RM = 1)

A table file is sin(2*pi*a/65536) for a = 0..65535 as float32 bits, little-endian, 256 KiB. FSCA's cosine is
sin[(a + 0x4000) & 0xffff], so it is not stored.

Python 3.8+, standard library only. The ROM words are computed in exact rational arithmetic from a 60-digit pi and
sine, so no step depends on the host's floating point.
"""
import argparse
import hashlib
import struct
import sys
from decimal import Decimal, getcontext
from fractions import Fraction

getcontext().prec = 60
N_ANG = 65536
K = 1 << 19            # rounding bias of the multiplier outputs
KC = 0x648340          # quadratic coefficient (credited, see README)


# ---- exact constants ----

def _pi():
    """pi to the context precision (the decimal module's documented recipe)."""
    getcontext().prec += 2
    three = Decimal(3)
    lasts, t, s, n, na, d, da = 0, three, 3, 1, 0, 0, 24
    while s != lasts:
        lasts = s
        n, na = n + na, na + 8
        d, da = d + da, da + 32
        t = (t * n) / d
        s += t
    getcontext().prec -= 2
    return +s


def _sin(x):
    """sin(x) to the context precision by its Taylor series (|x| <= pi/2 here)."""
    getcontext().prec += 2
    i, lasts, s, fact, num, sign = 1, 0, x, 1, x, 1
    while s != lasts:
        lasts = s
        i += 2
        fact *= i * (i - 1)
        num *= x * x
        sign *= -1
        s += num / fact * sign
    getcontext().prec -= 2
    return +s


PI = Fraction(_pi())


def sin_node(m):
    """sin(m*pi/32) as an exact fraction of its 60-digit value; sin(pi/2) is exactly 1."""
    return Fraction(1) if m == 16 else Fraction(_sin(_pi() * m / 32))


def floor(q):
    return q.numerator // q.denominator


def rne(q):
    """Round a fraction to the nearest integer, ties to even."""
    n = floor(q)
    r = q - n
    return n + (r > Fraction(1, 2) or (r == Fraction(1, 2) and n & 1))


def rne24(q):
    """Round a positive fraction to 24 significant bits (float32), ties to even."""
    e = q.numerator.bit_length() - q.denominator.bit_length()
    while Fraction(2) ** e > q:
        e -= 1
    while Fraction(2) ** (e + 1) <= q:
        e += 1
    ulp = Fraction(2) ** (e - 23)
    return rne(q / ulp) * ulp


# ---- the ROM ----

def rom():
    """The 17 node words (C0, L, E, Q) for m = 0..16; node m sits at angle m*pi/32."""
    c0, lw, ew, qw = [0], [0], [0], [0]
    for m in range(1, 17):
        s = sin_node(m)
        # L: the slope s*pi/2 in fixed point, shifted up by E (at most 3) while it stays below 2^23.
        e = 3
        while rne(s * PI / 2 * 2 ** (22 + e)) >= 1 << 23:
            e -= 1
        ew.append(e)
        lw.append(rne(s * PI / 2 * 2 ** (22 + e)))
        qw.append(rne(s * 4 * PI ** 3 / 3 * 64))
        # C0: the node's sine in units of 2^-26, taken from its float32 value F.
        f = rne24(s)
        if e >= 1:
            # The smallest word that rounds to F as a float32.
            c = floor(f * 2 ** 26) - 8
            for _ in range(64):
                if rne24(Fraction(c, 1 << 26)) == f:
                    break
                c += 1
            else:
                sys.exit("no C0 word rounds to F at node %d" % m)
            c0.append(c)
        else:
            # F times pi/2 in the slope's domain: 23 fraction bits plus a jammed low bit, then back by 2/pi.
            mw = 2 * floor(f * PI / 2 * 2 ** 23) + 1
            c0.append(rne(mw * 4 * 2 / PI))
    return c0, lw, ew, qw


# ---- the datapath: patent US6327605 (figs. 28-29), as modelled in skmp's sh4_fpu_approx.h (MIT, see LICENSE) ----

def arr(rec, mul):
    """Truncated Booth radix-4 multiply: rec's low 18 bits as 9 signed digits, the low 16 bits of each partial row
    dropped, then the two carry corrections."""
    lo = rec & 0x3ffff
    slo = lo - (0x40000 if lo & 0x20000 else 0)
    total = (rec - slo) * mul
    bits = lo << 1
    m15 = p16 = 0
    for k in range(9):
        d = (bits & 1) + ((bits >> 1) & 1) - 2 * ((bits >> 2) & 1)
        bits >>= 2
        mag = abs(d) * mul
        row = (~mag if d < 0 else mag) << (2 * k)
        total += (row >> 16) << 16
        if k < 3:
            m15 += (row >> 15) & 1
        if k >= 6:
            p16 ^= (row >> 16) & 1
    if m15 >= 2:
        total += 65536
    if p16:
        total -= 65536
    return total


def sm(p, u):
    """Sign-magnitude shift: truncates toward zero."""
    return p >> u if p >= 0 else -((~p) >> u)


def fixed(a, c0, lw, ew, qw):
    """The datapath's signed output for angle a, in units of 2^-26."""
    node = ((a + 512) >> 10) & 63
    r, quad = node & 15, node >> 4
    swap = quad & 1
    m = 16 - r if swap else r
    x = 16 - m
    dx = (((a + 512) & 1023) - 512) * 8192
    sq = (arr(dx, dx) + K) >> 23
    t1 = sm(arr(lw[x], dx) + K, 23 + ew[x])                 # cos-node slope * dx
    a3 = (arr(sq, dx) + K) >> 23
    t3 = sm(arr(qw[x] * 32, a3) + K, 26)                    # cubic term
    i1 = (arr(KC, dx) + K) >> 23
    j = (arr(dx, i1) + K) >> 23
    t2 = sm(arr(lw[m], j) + K, 27 + ew[m])                  # quadratic term
    v = c0[m] + ((t3 - t1) if swap else (t1 - t3)) - t2
    return -v if quad & 2 else v


def to_f32(v, rz=False):
    """float32 bits of v * 2^-26; zero is +0."""
    if not v:
        return 0
    sign, mag = int(v < 0), abs(v)
    p = mag.bit_length() - 1
    sh = p - 23
    if sh <= 0:
        q = mag << -sh
    else:
        q, rem, half = mag >> sh, mag & ((1 << sh) - 1), 1 << (sh - 1)
        if not rz:
            q += rem > half or (rem == half and q & 1)
        if q >> 24:
            q >>= 1
            p += 1
    return sign << 31 | (127 - 26 + p) << 23 | (q & 0x7fffff)


def table(words, rz=False):
    return [to_f32(fixed(a, *words), rz) for a in range(N_ANG)]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--table", metavar="OUT", help="write the sine table (float32, little-endian)")
    ap.add_argument("--check", metavar="DUMP", help="compare with a table dump in the same format")
    ap.add_argument("--rz", action="store_true", help="round-toward-zero table (FPSCR.RM = 1)")
    args = ap.parse_args()

    words = rom()
    print(" m        C0        L  E         Q")
    for m in range(17):
        print("%2d %9d %8d %2d %9d" % (m, words[0][m], words[1][m], words[2][m], words[3][m]))
    tab = table(words, args.rz)
    data = struct.pack("<%dI" % N_ANG, *tab)
    print("table (%s) SHA-256 %s" % ("RZ" if args.rz else "RN", hashlib.sha256(data).hexdigest()))
    if args.table:
        with open(args.table, "wb") as f:
            f.write(data)
        print("wrote", args.table)
    if args.check:
        with open(args.check, "rb") as f:
            raw = f.read()
        if len(raw) != 4 * N_ANG:
            sys.exit("%s: expected %d bytes, got %d" % (args.check, 4 * N_ANG, len(raw)))
        dump = struct.unpack("<%dI" % N_ANG, raw)
        diff = [a for a in range(N_ANG) if dump[a] != tab[a]]
        zero = [a for a in diff if not (dump[a] | tab[a]) & 0x7fffffff]
        print("%d of %d entries equal; %d differ only in the sign of zero" % (N_ANG - len(diff), N_ANG, len(zero)))
        for a in diff[:20]:
            print("  a=0x%04x  dump %08x  ours %08x" % (a, dump[a], tab[a]))
        return 1 if len(diff) > len(zero) else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
