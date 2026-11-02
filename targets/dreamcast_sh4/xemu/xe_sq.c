/* xemu's store queues: the machine's rd/wr, with the queues of a line-S function. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "xe.h"
#include "tc_rt_internal.h"

/* ---------- the store queues (vectors.txt line S; TcSq) ----------
 * With QACR0/1 at area 3 a store to 0xe0000000..0xe3ffffff fills queue SQ[bit 5] at bits 0..4 and changes no RAM;
 * pref @a sends a's queue, 32 bytes, to the RAM block at a's offset. A queue's contents at entry are not in the
 * vector: as the oracle, the stream before the state pointer rN is taken as contiguous, the queues holding the
 * blocks 0x40 and 0x20 before it; a mocked call that moved rN up through the window filled the blocks it passed. */
static u8 g_sq[2][32];
static int g_sq_reg = -1;
/* triage model "sqwt": the oracle's queues (TcSq), stores written through to RAM, a pref sending the bytes stored
 * since and the rest from the block the queue last held, unlogged (TcOracle's writeMemory is not tracked) */
int xe_model_sqwt;
/* triage model "sqlog": with sqwt, a pref's block is logged as writes (core/sq_unaligned.py's answers) */
int xe_model_sqlog;
static u32 g_sq_from[2];
static u8 g_sq_stored[2][32];
/* the bytes a mocked callee stores through r4..r7 (line S after N; TcSq.callee): per queue byte its RAM byte, or 0. A
 * pref sends it from there (the stub stored nothing: the block's own byte stays when that is where it went) */
static u32 g_sq_src[2][32];

static int in_window(u32 a) { return a - 0xe0000000u < 0x04000000u; }

static void sq_fill(u32 from, u32 to)
{
    for (u32 b = from & ~31u; b + 32 <= to; b += 32) {
        for (u32 i = 0; i < 32; i++) {
            u32 a = 0x8c000000u | ((b + i) & 0x00ffffffu);
            g_sq[(b >> 5) & 1][i] = a >= TCRT_VALID_LO ? rd8(a) : 0;
        }
        g_sq_from[(b >> 5) & 1] = 0x8c000000u | (b & 0x00ffffe0u);
        memset(g_sq_stored[(b >> 5) & 1], 0, 32);
        memset(g_sq_src[(b >> 5) & 1], 0, sizeof g_sq_src[0]);
    }
}

/* a mocked call to target: the bytes its line-S entries mark through r4..r7 are the callee's (TcSq.callee) */
void xe_sq_callee(const Xe *x, u32 target)
{
    char *p = tcrt_sq_line + 1;
    strtoul(p, &p, 10);
    for (;;) {
        char *q = p;
        u32 t = (u32)strtoul(p, &p, 16), r = (u32)strtoul(p, &p, 10);
        unsigned long long m[2];
        m[0] = strtoull(p, &p, 16);
        m[1] = strtoull(p, &p, 16);
        if (p == q || r < 4 || r > 7) return;
        for (u32 j = 0; j < 128 && t == target; j++) {
            u32 b = x->r[r] + j;
            if (!((m[j >> 6] >> (j & 63)) & 1) || !in_window(b)) continue;
            g_sq_stored[(b >> 5) & 1][b & 31] = 0;
            g_sq_src[(b >> 5) & 1][b & 31] = 0x8c000000u | (b & 0x00ffffffu);
        }
    }
}

void xe_sq_entry(const Xe *x)
{
    g_sq_reg = -1;
    memset(g_sq, 0, sizeof g_sq);
    memset(g_sq_stored, 0, sizeof g_sq_stored);
    memset(g_sq_src, 0, sizeof g_sq_src);
    g_sq_from[0] = g_sq_from[1] = 0;
    if (!tcrt_sq_window) return;
    for (int i = 8; i < 15 && g_sq_reg < 0; i++) {
        if (in_window(x->r[i])) g_sq_reg = i;
    }
    if (g_sq_reg >= 0) sq_fill(x->r[g_sq_reg] - 0x40u, x->r[g_sq_reg]);
    if (g_sq_reg >= 0) { /* rN inside its line: the bytes below it are the stream's earlier stores, still in the queue */
        u32 p = x->r[g_sq_reg], k = (p >> 5) & 1;
        for (u32 i = 0; i < (p & 31u); i++) {
            u32 a = 0x8c000000u | (((p & ~31u) + i) & 0x00ffffffu);
            g_sq[k][i] = a >= TCRT_VALID_LO ? rd8(a) : 0;
            g_sq_stored[k][i] = 1;
        }
    }
}

u32 xe_rd(u32 a, int size)
{
    xe_ghmem_read(a, size);
    return size == 1 ? rd8(a) : size == 2 ? rd16(a) : rd32(a);
}

void xe_wr(u32 a, u32 v, int size)
{
    if (tcrt_sq_window && in_window(a)) {
        for (int i = 0; i < size; i++) {
            g_sq[(a >> 5) & 1][(a + (u32)i) & 31] = (u8)(v >> (8 * i));
            g_sq_stored[(a >> 5) & 1][(a + (u32)i) & 31] = 1;
            g_sq_src[(a >> 5) & 1][(a + (u32)i) & 31] = 0;
        }
        if (!xe_model_sqwt) return;
    }
    xe_ghmem_write(a, size);
    if (size == 1) wr8(a, (u8)v);
    else if (size == 2) wr16(a, (u16)v);
    else wr32(a, v);
}

void xe_pref(u32 a)
{
    if (!tcrt_sq_window || !in_window(a)) return; /* a cache prefetch: nothing to see */
    u8 *q = g_sq[(a >> 5) & 1];
    u32 dst = 0x8c000000u | (a & 0x00ffffe0u);
    for (u32 j = 0; j < 32; j++) { /* a mocked callee's bytes (sq_callee) */
        u32 s = g_sq_src[(a >> 5) & 1][j];
        if (s) xe_ghmem_read(s, 1);
        if (s) q[j] = rd8(s), g_sq_stored[(a >> 5) & 1][j] = 1;
    }
    memset(g_sq_src[(a >> 5) & 1], 0, sizeof g_sq_src[0]);
    if (xe_model_sqwt) {
        u32 k = (a >> 5) & 1, src = g_sq_from[k] ? g_sq_from[k] : dst;
        u8 out[32];
        xe_ghmem_read(src, 32);
        for (u32 j = 0; j < 32; j++) out[j] = g_sq_stored[k][j] ? q[j] : rd8(src + j);
        if (xe_model_sqlog) { for (u32 j = 0; j < 32; j++) wr8(dst + j, out[j]); } else tcrt_poke(dst, out, 32);
        xe_ghmem_write(dst, 32);
        g_sq_from[k] = dst;
        memset(g_sq_stored[k], 0, 32);
        return;
    }
    xe_ghmem_write(dst, 32);
    for (u32 i = 0; i < 32; i += 4) wr32(dst + i, (u32)q[i] | (u32)q[i + 1] << 8 | (u32)q[i + 2] << 16 | (u32)q[i + 3] << 24);
}

/* rN before a mocked call, and after it: a callee that moved rN up through the window filled the blocks it passed */
u32 xe_sq_ptr(const Xe *x)
{
    return g_sq_reg >= 0 ? x->r[g_sq_reg] : 0;
}

void xe_sq_moved(const Xe *x, u32 before)
{
    if (g_sq_reg >= 0 && in_window(before) && x->r[g_sq_reg] > before) sq_fill(before, x->r[g_sq_reg]);
}
