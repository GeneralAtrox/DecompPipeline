/* xemu: the multiply-accumulates (mac.l, mac.w, with SR.S saturation) and div1, from the SH-4 software manual. */
#include "xe.h"

static u32 sx16(u32 v) { return (u32)(s32)(short)(u16)v; }

void xe_mac_l(Xe *x, u32 n, u32 m)
{
    s32 a = (s32)xe_rd(x->r[n], 4);
    x->r[n] += 4;
    s32 b = (s32)xe_rd(x->r[m], 4);
    x->r[m] += 4;
    long long mac = (long long)((unsigned long long)x->mach << 32 | x->macl);
    long long res = mac + (long long)a * b;
    if (x->s) { /* saturation to 48 bits */
        if (res > 0x00007fffffffffffLL) res = 0x00007fffffffffffLL;
        if (res < -0x0000800000000000LL) res = -0x0000800000000000LL;
    }
    x->mach = (u32)((unsigned long long)res >> 32);
    x->macl = (u32)res;
}

void xe_mac_w(Xe *x, u32 n, u32 m)
{
    s32 a = (s32)sx16(xe_rd(x->r[n], 2));
    x->r[n] += 2;
    s32 b = (s32)sx16(xe_rd(x->r[m], 2));
    x->r[m] += 2;
    long long prod = (long long)a * b;
    if (x->s) { /* a 32-bit saturating sum in MACL; MACH bit 0 marks an overflow */
        long long sum = (long long)(s32)x->macl + prod;
        if (sum > 0x7fffffffLL) {
            x->macl = 0x7fffffffu;
            x->mach |= 1;
        } else if (sum < -0x80000000LL) {
            x->macl = 0x80000000u;
            x->mach |= 1;
        } else {
            x->macl = (u32)sum;
        }
        return;
    }
    long long res = (long long)((unsigned long long)x->mach << 32 | x->macl) + prod;
    x->mach = (u32)((unsigned long long)res >> 32);
    x->macl = (u32)res;
}

void xe_div1(Xe *x, u32 n, u32 m)
{
    u32 *R = x->r, old_q = x->q, tmp0, tmp1;
    x->q = R[n] >> 31;
    R[n] = (R[n] << 1) | x->t;
    tmp0 = R[n];
    if (old_q == x->m) {
        R[n] -= R[m];
        tmp1 = R[n] > tmp0;
    } else {
        R[n] += R[m];
        tmp1 = R[n] < tmp0;
    }
    /* the manual's case table: Q ends as Q ^ M ^ tmp1, in each of its four branches */
    x->q = (x->q ^ x->m ^ tmp1) & 1;
    x->t = x->q == x->m;
}
