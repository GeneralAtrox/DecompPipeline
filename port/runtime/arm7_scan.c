/* Fast paths for three byte-scan loops (arm7.h), the loops a C compiler makes of "look at each slot of a table
 * for a flag", where the AICA sound driver spends nearly all its time. A scan is run in C, a whole number of iterations
 * at once, with the registers, flags, cycle count and instruction count the interpreter would have after the same
 * iterations. The iteration that leaves the loop, and anything the budget does not cover whole, is left to the
 * interpreter, so the loop's exit and its flags are the interpreter's own.
 *
 * Kind A (7 instructions, 11 cycles an iteration):     Kind B (6 instructions, 12 cycles an iteration):
 *     h:  ldrb rt, [rb]                                    h:  ldrb rt, [rb]
 *         tst  rt, #imm                                        movs rt, rt
 *         bne  out                                             beq  L
 *         add  rb, rb, #step                                   ...
 *         add  rc, rc, #1                                  L:  add  rb, rb, #step
 *         cmp  rc, #lim                                        subs rc, rc, #1
 *         bne  h                                               bne  h
 * Kind C (7 instructions, 11 cycles an iteration, the scan up to an end pointer):
 *     h:  add  rb, rb, #step
 *         cmp  rb, re
 *         moveq rt, #0
 *         beq  out
 *         ldrb rt, [rb]
 *         tst  rt, #imm
 *         beq  h
 * An iteration is skipped when it stays in the loop: A when the byte has no bit of imm and rc + 1 is not lim, B when the
 * byte is 0 and rc - 1 is not 0, C when rb + step is not re and its byte has no bit of imm. A loop is found when a backward branch goes to its head; its words are read again
 * each time it is entered, so code that changes is not mistaken for the loop. */
#include "arm7.h"

#define FIELD_RT(w) (((w) >> 12) & 15u)
#define FIELD_RB(w) (((w) >> 16) & 15u)
#define HEAD_SEEN 1024u

typedef struct {
    int kind;                    /* 0: not a loop, 1: A, 2: B, 3: C */
    u32 rt, rb, rc, imm, step, lim; /* rc: kind C's end register */
} Scan;

static int code_word(const Arm7 *a, u32 addr, u32 *w)
{
    if (addr & 3u || addr + 4u > a->ram_size) return 0;
    const u8 *p = a->ram + (addr & a->ram_mask);
    *w = (u32)p[0] | (u32)p[1] << 8 | (u32)p[2] << 16 | (u32)p[3] << 24;
    return 1;
}

/* The target of the branch word w at addr, if it is a branch with the condition cond. */
static int branch_to(u32 w, u32 addr, u32 cond, u32 *target)
{
    if ((w >> 24) != (0x0au | cond << 4)) return 0;
    *target = addr + 8u + (u32)(((s32)(w << 8)) >> 6);
    return 1;
}

static void decode(const Arm7 *a, u32 h, Scan *s)
{
    u32 w[7], t;
    s->kind = 0;
    for (u32 i = 0; i < 3; i++)
        if (!code_word(a, h + 4u * i, &w[i])) return;
    u32 cb = FIELD_RB(w[0]);
    if ((w[0] & 0xffffff00u) == (0xe2800000u | cb << 16 | cb << 12)) { /* kind C: add rb, rb, #step */
        u32 re = w[1] & 15u, ct = FIELD_RT(w[2]);
        if (w[1] != (0xe1500000u | cb << 16 | re) || cb == 15u || re == 15u || cb == re) return; /* cmp rb, re */
        if (ct == 15u || ct == cb || ct == re || w[2] != (0x03a00000u | ct << 12)) return;       /* moveq rt, #0 */
        for (u32 i = 3; i < 7; i++)
            if (!code_word(a, h + 4u * i, &w[i])) return;
        if (!branch_to(w[3], h + 12u, 0u, &t)) return;                                         /* beq out */
        if (w[4] != (0xe5d00000u | cb << 16 | ct << 12)) return;                               /* ldrb rt, [rb] */
        if ((w[5] & 0xfff0ff00u) != (0xe3100000u | ct << 16)) return;                          /* tst rt, #imm */
        if (!branch_to(w[6], h + 24u, 0u, &t) || t != h) return;                               /* beq h */
        s->kind = 3, s->rt = ct, s->rb = cb, s->rc = re, s->imm = w[5] & 0xffu, s->step = w[0] & 0xffu;
        return;
    }
    if ((w[0] & 0xfff00fffu) != 0xe5d00000u) return; /* ldrb rt, [rb] */
    u32 rt = FIELD_RT(w[0]), rb = FIELD_RB(w[0]);
    if (rt == 15u || rb == 15u || rt == rb) return;
    if ((w[1] & 0xfff0ff00u) == (0xe3100000u | rt << 16)) { /* kind A: tst rt, #imm; bne out */
        u32 bt;
        if (!branch_to(w[2], h + 8u, 1u, &bt)) return;
        for (u32 i = 3; i < 7; i++)
            if (!code_word(a, h + 4u * i, &w[i])) return;
        if ((w[3] & 0xffffff00u) != (0xe2800000u | rb << 16 | rb << 12)) return; /* add rb, rb, #step */
        u32 rc = FIELD_RT(w[4]);
        if (rc == 15u || rc == rb || rc == rt) return;
        if (w[4] != (0xe2800001u | rc << 16 | rc << 12)) return;                 /* add rc, rc, #1 */
        if ((w[5] & 0xffffff00u) != (0xe3500000u | rc << 16)) return;            /* cmp rc, #lim */
        if (!branch_to(w[6], h + 24u, 1u, &t) || t != h) return;                 /* bne h */
        s->kind = 1, s->rt = rt, s->rb = rb, s->rc = rc, s->imm = w[1] & 0xffu, s->step = w[3] & 0xffu, s->lim = w[5] & 0xffu;
        return;
    }
    if (w[1] == (0xe1b00000u | rt << 12 | rt)) { /* kind B: movs rt, rt; beq L; L: add; subs; bne h */
        u32 l;
        if (!branch_to(w[2], h + 8u, 0u, &l)) return;
        for (u32 i = 3; i < 6; i++)
            if (!code_word(a, l + 4u * (i - 3u), &w[i])) return;
        if ((w[3] & 0xffffff00u) != (0xe2800000u | rb << 16 | rb << 12)) return; /* add rb, rb, #step */
        u32 rc = FIELD_RT(w[4]);
        if (rc == 15u || rc == rb || rc == rt) return;
        if (w[4] != (0xe2500001u | rc << 16 | rc << 12)) return;                 /* subs rc, rc, #1 */
        if (!branch_to(w[5], l + 8u, 1u, &t) || t != h) return;                  /* bne h */
        s->kind = 2, s->rt = rt, s->rb = rb, s->rc = rc, s->step = w[3] & 0xffu;
    }
}

/* x - y: the flags of CMP and SUBS. */
static void sub_flags(Arm7 *a, u32 x, u32 y)
{
    u32 r = x - y;
    a->n = r >> 31;
    a->z = r == 0;
    a->c = x >= y;
    a->v = ((x ^ y) & (x ^ r)) >> 31;
}

static u32 slot(const Arm7 *a, u32 base, u32 step, u64 j, int *ok)
{
    u32 addr = base + (u32)j * step;
    *ok = addr < a->ram_size;
    return *ok ? a->ram[addr & a->ram_mask] : 0u;
}

void arm7_scan_probe(Arm7 *a, u32 target)
{
    u32 i = (target >> 2) & (HEAD_SEEN - 1u);
    Scan s;
    if (a->scan_seen[i] == target) return;
    a->scan_seen[i] = target;
    decode(a, target, &s);
    if (s.kind) a->scan_map[(target >> 2) & 255u] = 1;
}

void arm7_scan_reset(Arm7 *a)
{
    for (u32 i = 0; i < HEAD_SEEN; i++) a->scan_seen[i] = 0xffffffffu;
    for (u32 i = 0; i < 256; i++) a->scan_map[i] = 0;
}

/* At a loop's head (a->next): skips the iterations that stay in the loop and fit the budget whole. */
void arm7_scan_run(Arm7 *a)
{
    Scan s;
    decode(a, a->next, &s);
    if (!s.kind) return;
    s64 b = a->cycles;
    u32 per = s.kind == 2 ? 12u : 11u, need = s.kind == 2 ? 10u : 9u; /* the budget a whole iteration starts with, as the last
                                                                     instruction is started with it above 0 */
    if (b < (s64)need) return;
    u64 kmax = (u64)(b + 2) / per, k = 0;
    u32 base = a->r[s.rb], cnt = a->r[s.rc], last = 0;
    int ok;
    while (k < kmax) {
        u32 byte = slot(a, base, s.step, s.kind == 3 ? k + 1u : k, &ok); /* kind C loads after its add */
        if (!ok) break;
        if (s.kind == 1) {
            if (byte & s.imm || cnt + (u32)k + 1u == s.lim) break;
        } else if (s.kind == 2) {
            if (byte || cnt - (u32)k - 1u == 0) break;
        } else {
            if (byte & s.imm || base + ((u32)k + 1u) * s.step == cnt) break;
        }
        last = byte;
        k++;
    }
    if (!k) return;
    a->r[s.rb] = base + (u32)k * s.step;
    a->r[s.rt] = a->fault == 2 ? last ^ 1u : last;
    if (s.kind == 3) {
        sub_flags(a, a->r[s.rb], cnt);
        a->n = 0, a->z = 1; /* the tst: no bit of imm */
    } else if (s.kind == 1) {
        a->r[s.rc] = cnt + (u32)k;
        sub_flags(a, cnt + (u32)k, s.lim);
    } else {
        a->r[s.rc] = cnt - (u32)k;
        sub_flags(a, cnt - (u32)k + 1u, 1u);
    }
    a->cycles -= (s64)(k * per) + (a->fault == 1);
    a->executed += k * (s.kind == 2 ? 6u : 7u);
    a->scanned += k;
}
