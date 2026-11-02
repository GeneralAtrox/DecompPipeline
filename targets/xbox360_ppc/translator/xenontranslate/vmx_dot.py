"""The vector dot-product helper (xv_dot1) of the vector helpers in vmx.py, in its own file (the 400-line cap)."""
from .vmx import helper

# dot products: exact products summed in double, rounded once to single, in every word
# vmsum3fp128 / vmsum4fp128: the exact sum of the lane products (integer limbs, no rounding until the end), rounded
# once to single, to nearest even; a NaN operand gives the first NaN quieted, 0 * inf and inf - inf the default NaN,
# an infinite product the infinity, a result below 2^-126 zero. The same value as TcVmx.dot.
helper("xv_dot1", """
static u32 xv_dot1(const u32 *aw, const u32 *bw, int n)
{
    u64 L[10], T[10], m[4];
    int e[4], ng[4], k, i, j, emin = 0, have = 0, posinf = 0, neginf = 0, invalid = 0, allneg = 1;
    int sh, q, r, bl = 0, shift, E, up, sticky;
    u32 x, y, sign = 0, qv = 0;
    u64 c, v, d, s;
    for (k = 0; k < n; k++) {
        if (xv_isnan(aw[k])) return aw[k] | 0x00400000u;
        if (xv_isnan(bw[k])) return bw[k] | 0x00400000u;
    }
    for (i = 0; i < 10; i++) L[i] = 0;
    for (k = 0; k < n; k++) {
        int xz, yz, xi, yi, g;
        x = aw[k];
        y = bw[k];
        if ((x & 0x7f800000u) == 0) x &= 0x80000000u;
        if ((y & 0x7f800000u) == 0) y &= 0x80000000u;
        g = ((x ^ y) & 0x80000000u) != 0;
        xz = (x & 0x7fffffffu) == 0;
        yz = (y & 0x7fffffffu) == 0;
        xi = (x & 0x7fffffffu) == 0x7f800000u;
        yi = (y & 0x7fffffffu) == 0x7f800000u;
        m[k] = 0;
        e[k] = 0;
        ng[k] = g;
        if (xi || yi) {
            if (xz || yz) invalid = 1;
            else if (g) neginf = 1;
            else posinf = 1;
            allneg = 0;
            continue;
        }
        if (xz || yz) {
            if (!g) allneg = 0;
            continue;
        }
        allneg = 0;
        m[k] = (u64)((x & 0x7fffffu) | 0x800000u) * (u64)((y & 0x7fffffu) | 0x800000u);
        e[k] = (int)((x >> 23) & 0xffu) + (int)((y >> 23) & 0xffu) - 300;
        if (!have || e[k] < emin) emin = e[k];
        have = 1;
    }
    if (invalid || (posinf && neginf)) return 0x7fc00000u;
    if (posinf) return 0x7f800000u;
    if (neginf) return 0xff800000u;
    for (k = 0; k < n; k++) {
        if (m[k] == 0) continue;
        sh = e[k] - emin;
        q = sh >> 6;
        r = sh & 63;
        for (i = 0; i < 10; i++) T[i] = 0;
        T[q] = m[k] << r;
        if (r) T[q + 1] = m[k] >> (64 - r);
        c = 0;
        for (i = 0; i < 10; i++) {
            if (ng[k]) {
                d = L[i] - T[i];
                v = d - c;
                c = (L[i] < T[i]) || (d < c);
            } else {
                s = L[i] + T[i];
                v = s + c;
                c = (s < L[i]) || (v < s);
            }
            L[i] = v;
        }
    }
    for (i = 0; i < 10; i++) {
        if (L[i]) break;
    }
    if (i == 10) return allneg ? 0x80000000u : 0u;
    if (L[9] >> 63) {
        sign = 0x80000000u;
        c = 1;
        for (i = 0; i < 10; i++) {
            v = ~L[i] + c;
            c = (c && v == 0);
            L[i] = v;
        }
    }
    for (i = 9; i >= 0; i--) {
        if (L[i]) {
            for (j = 63; j >= 0; j--) {
                if ((L[i] >> j) & 1) break;
            }
            bl = 64 * i + j + 1;
            break;
        }
    }
    shift = bl - 24;
    E = bl - 1 + emin;
    if (shift > 0) {
        for (j = 0; j < 24; j++) qv |= (u32)((L[(shift + j) >> 6] >> ((shift + j) & 63)) & 1) << j;
        up = (int)((L[(shift - 1) >> 6] >> ((shift - 1) & 63)) & 1);
        sticky = 0;
        for (j = 0; j < shift - 1; j++) {
            if ((L[j >> 6] >> (j & 63)) & 1) {
                sticky = 1;
                break;
            }
        }
        if (up && (sticky || (qv & 1))) {
            qv++;
            if (qv == 0x1000000u) {
                qv >>= 1;
                E++;
            }
        }
    } else {
        qv = (u32)(L[0] << (24 - bl));
    }
    if (E > 127) return sign | 0x7f800000u;
    if (E < -126) return sign;
    return sign | ((u32)(E + 127) << 23) | (qv & 0x7fffffu);
}""", ["xv_isnan"])
