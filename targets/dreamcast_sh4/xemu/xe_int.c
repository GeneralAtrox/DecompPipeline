/* xemu: the SH-4 integer, memory, branch and system instructions, from the SH-4 software manual's descriptions.
 * Branch targets are taken before the delay slot runs (the manual's "temp = PC" order: jsr @Rn and rts use Rn and
 * PR as they were). */
#include <string.h>
#include "xe.h"

#define FN ((op >> 8) & 15u)
#define FM ((op >> 4) & 15u)

static u32 sx8(u32 v) { return (u32)(s32)(signed char)(u8)v; }
static u32 sx16(u32 v) { return (u32)(s32)(short)(u16)v; }
static u32 sx12(u32 v) { return (v & 0x800u) ? (v | 0xfffff000u) : v; }

static void delayed(Xe *x, u32 to)
{
    x->branch = 1;
    x->target = to;
}

static void now(Xe *x, u32 to)
{
    x->branch = 2;
    x->target = to;
}

static void push(Xe *x, u32 n, u32 v)
{
    xe_wr(x->r[n] - 4, v, 4);
    x->r[n] -= 4;
}

static u32 pop(Xe *x, u32 n)
{
    u32 v = xe_rd(x->r[n], 4);
    x->r[n] += 4;
    return v;
}

static int op0(Xe *x, u16 op)
{
    u32 n = FN, m = FM, *R = x->r;
    switch (op & 15) {
    case 2:
        if (m & 8) R[n] = x->bank[m & 7];
        else if (m == 0) R[n] = xe_sr(x);
        else if (m == 1) R[n] = x->gbr;
        else if (m == 2) R[n] = x->vbr;
        else if (m == 3) R[n] = x->ssr;
        else if (m == 4) R[n] = x->spc;
        else return 0;
        return 1;
    case 3:
        if (m == 0) { /* bsrf */
            x->pr = x->pc + 4;
            delayed(x, x->pc + 4 + R[n]);
        } else if (m == 2) {
            delayed(x, x->pc + 4 + R[n]); /* braf */
        } else if (m == 8) {
            xe_pref(R[n]);
        } else if (m == 9 || m == 10 || m == 11) { /* ocbi, ocbp, ocbwb: the cache is not modelled */
        } else if (m == 12) {
            xe_wr(R[n], R[0], 4); /* movca.l */
        } else {
            return 0;
        }
        return 1;
    case 4: xe_wr(R[0] + R[n], R[m], 1); return 1;
    case 5: xe_wr(R[0] + R[n], R[m], 2); return 1;
    case 6: xe_wr(R[0] + R[n], R[m], 4); return 1;
    case 7: x->macl = R[n] * R[m]; return 1;
    case 8:
        if (n) return 0;
        if (m == 0) x->t = 0;
        else if (m == 1) x->t = 1;
        else if (m == 2) x->mach = x->macl = 0;
        else if (m == 4) x->s = 0;
        else if (m == 5) x->s = 1;
        else return 0;
        return 1;
    case 9:
        if (m == 0 && n == 0) return 1; /* nop */
        if (m == 1 && n == 0) {
            x->m = x->q = x->t = 0; /* div0u */
            return 1;
        }
        if (m == 2) {
            R[n] = x->t; /* movt */
            return 1;
        }
        return 0;
    case 10:
        if (m == 0) R[n] = x->mach;
        else if (m == 1) R[n] = x->macl;
        else if (m == 2) R[n] = x->pr;
        else if (m == 3) R[n] = x->sgr;
        else if (m == 5) R[n] = x->fpul;
        else if (m == 6) R[n] = x->fpscr;
        else if (m == 15) R[n] = x->dbr;
        else return 0;
        return 1;
    case 11:
        if (n || m) return 0; /* sleep, rte: not run */
        delayed(x, x->pr);    /* rts */
        return 1;
    case 12: R[n] = sx8(xe_rd(R[0] + R[m], 1)); return 1;
    case 13: R[n] = sx16(xe_rd(R[0] + R[m], 2)); return 1;
    case 14: R[n] = xe_rd(R[0] + R[m], 4); return 1;
    case 15: xe_mac_l(x, n, m); return 1;
    }
    return 0;
}

static int op2(Xe *x, u16 op)
{
    u32 n = FN, m = FM, *R = x->r, t;
    switch (op & 15) {
    case 0: xe_wr(R[n], R[m], 1); return 1;
    case 1: xe_wr(R[n], R[m], 2); return 1;
    case 2: xe_wr(R[n], R[m], 4); return 1;
    case 4: xe_wr(R[n] - 1, R[m], 1); R[n] -= 1; return 1;
    case 5: xe_wr(R[n] - 2, R[m], 2); R[n] -= 2; return 1;
    case 6: xe_wr(R[n] - 4, R[m], 4); R[n] -= 4; return 1;
    case 7:
        x->q = R[n] >> 31;
        x->m = R[m] >> 31;
        x->t = x->q != x->m;
        return 1;
    case 8: x->t = (R[n] & R[m]) == 0; return 1;
    case 9: R[n] &= R[m]; return 1;
    case 10: R[n] ^= R[m]; return 1;
    case 11: R[n] |= R[m]; return 1;
    case 12:
        t = R[n] ^ R[m];
        x->t = !(t & 0xffu) || !(t & 0xff00u) || !(t & 0xff0000u) || !(t & 0xff000000u);
        return 1;
    case 13: R[n] = (R[m] << 16) | (R[n] >> 16); return 1;
    case 14: x->macl = (R[n] & 0xffffu) * (R[m] & 0xffffu); return 1;
    case 15: x->macl = (u32)((s32)sx16(R[n]) * (s32)sx16(R[m])); return 1;
    }
    return 0;
}

static int op3(Xe *x, u16 op)
{
    u32 n = FN, m = FM, *R = x->r, a = R[n], b = R[m], v;
    unsigned long long p;
    switch (op & 15) {
    case 0: x->t = a == b; return 1;
    case 2: x->t = a >= b; return 1;
    case 3: x->t = (s32)a >= (s32)b; return 1;
    case 4: xe_div1(x, n, m); return 1;
    case 5:
        p = (unsigned long long)a * b;
        x->mach = (u32)(p >> 32);
        x->macl = (u32)p;
        return 1;
    case 6: x->t = a > b; return 1;
    case 7: x->t = (s32)a > (s32)b; return 1;
    case 8: R[n] = a - b; return 1;
    case 10:
        v = a - b;
        R[n] = v - x->t;
        x->t = (a < v) | (v < R[n]);
        return 1;
    case 11:
        R[n] = a - b;
        x->t = ((a ^ b) & (a ^ R[n])) >> 31;
        return 1;
    case 12: R[n] = a + b; return 1;
    case 13:
        p = (unsigned long long)((long long)(s32)a * (s32)b);
        x->mach = (u32)(p >> 32);
        x->macl = (u32)p;
        return 1;
    case 14:
        v = a + b;
        R[n] = v + x->t;
        x->t = (a > v) | (v > R[n]);
        return 1;
    case 15:
        R[n] = a + b;
        x->t = (~(a ^ b) & (a ^ R[n])) >> 31;
        return 1;
    }
    return 0;
}

static int op4(Xe *x, u16 op)
{
    u32 n = FN, m = FM, *R = x->r, t;
    s32 sh;
    switch (op & 15) {
    case 12: /* shad */
        sh = (s32)R[m];
        R[n] = sh >= 0 ? R[n] << (sh & 31) : (sh & 31) == 0 ? (u32)((s32)R[n] >> 31) : (u32)((s32)R[n] >> ((~sh & 31) + 1));
        return 1;
    case 13: /* shld */
        sh = (s32)R[m];
        R[n] = sh >= 0 ? R[n] << (sh & 31) : (sh & 31) == 0 ? 0 : R[n] >> ((~sh & 31) + 1);
        return 1;
    case 15: xe_mac_w(x, n, m); return 1;
    }
    if ((op & 0x8f) == 0x83) { push(x, n, x->bank[m & 7]); return 1; }
    if ((op & 0x8f) == 0x87) { x->bank[m & 7] = pop(x, n); return 1; }
    if ((op & 0x8f) == 0x8e) { x->bank[m & 7] = R[n]; return 1; }
    switch (op & 0xff) {
    case 0x00: case 0x20: x->t = R[n] >> 31; R[n] <<= 1; return 1; /* shll, shal */
    case 0x01: x->t = R[n] & 1; R[n] >>= 1; return 1;
    case 0x21: x->t = R[n] & 1; R[n] = (u32)((s32)R[n] >> 1); return 1;
    case 0x04: x->t = R[n] >> 31; R[n] = (R[n] << 1) | x->t; return 1;
    case 0x05: x->t = R[n] & 1; R[n] = (R[n] >> 1) | (x->t << 31); return 1;
    case 0x24: t = R[n] >> 31; R[n] = (R[n] << 1) | x->t; x->t = t; return 1;
    case 0x25: t = R[n] & 1; R[n] = (R[n] >> 1) | (x->t << 31); x->t = t; return 1;
    case 0x08: R[n] <<= 2; return 1;
    case 0x09: R[n] >>= 2; return 1;
    case 0x18: R[n] <<= 8; return 1;
    case 0x19: R[n] >>= 8; return 1;
    case 0x28: R[n] <<= 16; return 1;
    case 0x29: R[n] >>= 16; return 1;
    case 0x10: R[n] -= 1; x->t = R[n] == 0; return 1;
    case 0x11: x->t = (s32)R[n] >= 0; return 1;
    case 0x15: x->t = (s32)R[n] > 0; return 1;
    case 0x1b:
        t = xe_rd(R[n], 1);
        x->t = t == 0;
        xe_wr(R[n], t | 0x80u, 1);
        return 1;
    case 0x0b: x->pr = x->pc + 4; delayed(x, R[n]); return 1; /* jsr */
    case 0x2b: delayed(x, R[n]); return 1;                  /* jmp */
    case 0x02: push(x, n, x->mach); return 1;
    case 0x12: push(x, n, x->macl); return 1;
    case 0x22: push(x, n, x->pr); return 1;
    case 0x52: push(x, n, x->fpul); return 1;
    case 0x62: push(x, n, x->fpscr); return 1;
    case 0x03: push(x, n, xe_sr(x)); return 1;
    case 0x13: push(x, n, x->gbr); return 1;
    case 0x23: push(x, n, x->vbr); return 1;
    case 0x32: push(x, n, x->sgr); return 1;
    case 0x33: push(x, n, x->ssr); return 1;
    case 0x43: push(x, n, x->spc); return 1;
    case 0xf2: push(x, n, x->dbr); return 1;
    case 0x06: x->mach = pop(x, n); return 1;
    case 0x16: x->macl = pop(x, n); return 1;
    case 0x26: x->pr = pop(x, n); return 1;
    case 0x56: x->fpul = pop(x, n); return 1;
    case 0x66: xe_set_fpscr(x, pop(x, n)); return 1;
    case 0x07: xe_set_sr(x, pop(x, n)); return 1;
    case 0x17: x->gbr = pop(x, n); return 1;
    case 0x27: x->vbr = pop(x, n); return 1;
    case 0x37: x->ssr = pop(x, n); return 1;
    case 0x47: x->spc = pop(x, n); return 1;
    case 0xf6: x->dbr = pop(x, n); return 1;
    case 0x0a: x->mach = R[n]; return 1;
    case 0x1a: x->macl = R[n]; return 1;
    case 0x2a: x->pr = R[n]; return 1;
    case 0x5a: x->fpul = R[n]; return 1;
    case 0x6a: xe_set_fpscr(x, R[n]); return 1;
    case 0x0e: xe_set_sr(x, R[n]); return 1;
    case 0x1e: x->gbr = R[n]; return 1;
    case 0x2e: x->vbr = R[n]; return 1;
    case 0x3e: x->ssr = R[n]; return 1;
    case 0x4e: x->spc = R[n]; return 1;
    case 0xfa: x->dbr = R[n]; return 1;
    }
    return 0;
}

static int op6(Xe *x, u16 op)
{
    u32 n = FN, m = FM, *R = x->r, v, a = R[m];
    switch (op & 15) {
    case 0: R[n] = sx8(xe_rd(a, 1)); return 1;
    case 1: R[n] = sx16(xe_rd(a, 2)); return 1;
    case 2: R[n] = xe_rd(a, 4); return 1;
    case 3: R[n] = a; return 1;
    case 4: v = sx8(xe_rd(a, 1)); if (n != m) R[m] += 1; R[n] = v; return 1;
    case 5: v = sx16(xe_rd(a, 2)); if (n != m) R[m] += 2; R[n] = v; return 1;
    case 6: v = xe_rd(a, 4); if (n != m) R[m] += 4; R[n] = v; return 1;
    case 7: R[n] = ~a; return 1;
    case 8: R[n] = (a & 0xffff0000u) | ((a & 0xffu) << 8) | ((a >> 8) & 0xffu); return 1;
    case 9: R[n] = (a << 16) | (a >> 16); return 1;
    case 10:
        v = 0 - a;
        R[n] = v - x->t;
        x->t = (0 < v) | (v < R[n]);
        return 1;
    case 11: R[n] = 0 - a; return 1;
    case 12: R[n] = a & 0xffu; return 1;
    case 13: R[n] = a & 0xffffu; return 1;
    case 14: R[n] = sx8(a); return 1;
    case 15: R[n] = sx16(a); return 1;
    }
    return 0;
}

static int op8(Xe *x, u16 op)
{
    u32 r = FM, d = op & 15u, i = op & 0xffu, *R = x->r, to = x->pc + 4 + sx8(i) * 2;
    switch (FN) {
    case 0: xe_wr(R[r] + d, R[0], 1); return 1;
    case 1: xe_wr(R[r] + d * 2, R[0], 2); return 1;
    case 4: R[0] = sx8(xe_rd(R[r] + d, 1)); return 1;
    case 5: R[0] = sx16(xe_rd(R[r] + d * 2, 2)); return 1;
    case 8: x->t = R[0] == sx8(i); return 1;
    case 9: if (x->t) now(x, to); return 1;
    case 11: if (!x->t) now(x, to); return 1;
    case 13: if (x->t) delayed(x, to); return 1;
    case 15: if (!x->t) delayed(x, to); return 1;
    }
    return 0;
}

static int opc(Xe *x, u16 op)
{
    u32 d = op & 0xffu, *R = x->r, g = x->gbr, v;
    switch (FN) {
    case 0: xe_wr(g + d, R[0], 1); return 1;
    case 1: xe_wr(g + d * 2, R[0], 2); return 1;
    case 2: xe_wr(g + d * 4, R[0], 4); return 1;
    case 4: R[0] = sx8(xe_rd(g + d, 1)); return 1;
    case 5: R[0] = sx16(xe_rd(g + d * 2, 2)); return 1;
    case 6: R[0] = xe_rd(g + d * 4, 4); return 1;
    case 7: R[0] = (x->pc & ~3u) + 4 + d * 4; return 1; /* mova */
    case 8: x->t = (R[0] & d) == 0; return 1;
    case 9: R[0] &= d; return 1;
    case 10: R[0] ^= d; return 1;
    case 11: R[0] |= d; return 1;
    case 12: x->t = (xe_rd(g + R[0], 1) & d) == 0; return 1;
    case 13: v = xe_rd(g + R[0], 1); xe_wr(g + R[0], v & d, 1); return 1;
    case 14: v = xe_rd(g + R[0], 1); xe_wr(g + R[0], v ^ d, 1); return 1;
    case 15: v = xe_rd(g + R[0], 1); xe_wr(g + R[0], v | d, 1); return 1;
    }
    return 0; /* trapa */
}

void xe_exec(Xe *x, u16 op)
{
    u32 n = FN, m = FM, *R = x->r, ok = 1;
    switch (op >> 12) {
    case 0x0: ok = (u32)op0(x, op); break;
    case 0x1: xe_wr(R[n] + (op & 15u) * 4, R[m], 4); break;
    case 0x2: ok = (u32)op2(x, op); break;
    case 0x3: ok = (u32)op3(x, op); break;
    case 0x4: ok = (u32)op4(x, op); break;
    case 0x5: R[n] = xe_rd(R[m] + (op & 15u) * 4, 4); break;
    case 0x6: ok = (u32)op6(x, op); break;
    case 0x7: R[n] += sx8(op & 0xffu); break;
    case 0x8: ok = (u32)op8(x, op); break;
    case 0x9: R[n] = sx16(xe_rd_text(x->pc + 4 + (op & 0xffu) * 2, 2)); break;
    case 0xa: delayed(x, x->pc + 4 + sx12(op & 0xfffu) * 2); break;
    case 0xb: x->pr = x->pc + 4; delayed(x, x->pc + 4 + sx12(op & 0xfffu) * 2); break;
    case 0xc: ok = (u32)opc(x, op); break;
    case 0xd: R[n] = xe_rd_text((x->pc & ~3u) + 4 + (op & 0xffu) * 4, 4); break;
    case 0xe: R[n] = sx8(op & 0xffu); break;
    default: xe_exec_fpu(x, op); break;
    }
    if (!ok) xe_fail("an instruction xemu does not run", x->pc, op);
}
