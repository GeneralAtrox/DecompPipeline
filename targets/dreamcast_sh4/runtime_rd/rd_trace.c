/* Readable-C runtime: which bytes of a callee's out-buffer the original reads (vectors line T, batch 22).
 *
 * A prototype often types a buffer the callee fills by its element (Gd_GetToc's `u32 *toc`, a 408-byte TOC;
 * Grid_CellOfPoint's `s32 *out_cell`, three words), so sizeof the pointee says too little. Line
 * "T CALL TARGET FIELD GUEST SPAN", run on the matched raw C with the vector's own bytes (no paint): from call CALL to
 * TARGET on (when the original passes GUEST in FIELD), every read of a byte of [GUEST, GUEST + SPAN) the original has
 * not written itself in this run counts; at the end of the vector the runner prints "OT CALL TARGET FIELD END", END
 * the offset just past the last such byte (core/readable_outptr.py delivers that much; SPAN stops at the next stack
 * address a call is passed). Only raw C's rd8..wr32 are watched (rd_mem.c), and only while a T line is set.
 *
 * Raw C fills a call's stack words s0..s7 by reading the eight words at r15 (a set_stack_args / tc_args / set_s
 * helper), whatever the callee takes; those reads are no use of the bytes (TC 8c035af0, 8c04b724, 8c0117f4: a
 * scalar out-local at r15 + 4 would read as 28 bytes). So reads wait in a window of the last eight: at a call, eight
 * word reads of exactly r15 .. r15 + 28 holding the record's s0..s7 are dropped, and any other read counts.
 *
 * A B line (the painted raw runs) watches the writes alone (rd_writes): rd_written tells out_copy which bytes of the
 * out-buffer the original stored to before the call. A stored value can still equal the paint in both runs when it
 * is computed from the painted bytes themselves (TC 8c0117f4: yaw = the first call's output + a small lag keeps the
 * paint's upper bytes), so the paints alone cannot tell it.
 */
#include <string.h>
#include "rd_internal.h"

#define RD_MAX_TRACES 64
#define STACK_SPAN (TCRT_RAM_BASE + TCRT_RAM_SIZE - TCRT_STACK_LO)
#define WINDOW 8

int rd_tracing;
int rd_writes;
static struct { u32 call, target, field, guest, span, end; int armed; } g_tr[RD_MAX_TRACES];
static int g_ntr;
static u8 g_written[STACK_SPAN]; /* 1: the original wrote this guest stack byte in this run */
static struct { u32 a, n, mask; } g_pend[WINDOW]; /* reads not counted yet; mask: bytes unwritten at the read */
static int g_npend;

void rd_trace_add(u32 call, u32 target, u32 field, u32 guest, u32 span)
{
    if (g_ntr >= RD_MAX_TRACES || field >= RD_LOG_FIELDS || guest < TCRT_STACK_LO || span > 0x2000u
        || guest + span > TCRT_RAM_BASE + TCRT_RAM_SIZE) return;
    if (!rd_tracing && !rd_writes) {
        memset(g_written, 0, sizeof g_written);
        g_npend = 0;
    }
    g_tr[g_ntr].call = call;
    g_tr[g_ntr].target = target;
    g_tr[g_ntr].field = field;
    g_tr[g_ntr].guest = guest;
    g_tr[g_ntr].span = span;
    g_tr[g_ntr].end = 0;
    g_tr[g_ntr].armed = 0;
    g_ntr++;
    rd_tracing = 1;
}

static void count(u32 a, u32 n, u32 mask)
{
    for (u32 k = 0; k < n; k++) {
        u32 b = a + k;
        if (!(mask >> k & 1u)) continue;
        for (int i = 0; i < g_ntr; i++)
            if (g_tr[i].armed && b >= g_tr[i].guest && b - g_tr[i].guest < g_tr[i].span
                && b - g_tr[i].guest + 1 > g_tr[i].end) g_tr[i].end = b - g_tr[i].guest + 1;
    }
}

static void flush(void)
{
    for (int i = 0; i < g_npend; i++) count(g_pend[i].a, g_pend[i].n, g_pend[i].mask);
    g_npend = 0;
}

/* The window is a stack-word sweep for this call: eight word reads of base .. base + 28 holding rec's s0..s7. */
static int sweep(const u32 *rec)
{
    u32 base = 0xffffffffu, seen = 0;
    if (g_npend != WINDOW) return 0;
    for (int i = 0; i < WINDOW; i++) base = g_pend[i].a < base ? g_pend[i].a : base;
    for (int i = 0; i < WINDOW; i++) {
        u32 off = g_pend[i].a - base;
        if (g_pend[i].n != 4 || off % 4 || off / 4 >= WINDOW || seen >> off / 4 & 1u) return 0;
        seen |= 1u << off / 4;
    }
    for (int i = 0; i < WINDOW; i++)
        if (*(volatile u32 *)(uintptr_t)(base + 4u * i) != rec[13 + i]) return 0;
    return 1;
}

void rd_trace_call(u32 call, u32 target, const u32 *rec)
{
    if (sweep(rec)) g_npend = 0;
    flush(); /* before arming: a read made before this call never counts for it */
    for (int i = 0; i < g_ntr; i++)
        if (g_tr[i].call == call && g_tr[i].target == target && rec[g_tr[i].field] == g_tr[i].guest) g_tr[i].armed = 1;
}

void rd_trace_read(u32 a, u32 n)
{
    u32 mask = 0;
    for (u32 k = 0; k < n; k++) {
        u32 b = a + k;
        if (b >= TCRT_STACK_LO && b - TCRT_STACK_LO < STACK_SPAN && !g_written[b - TCRT_STACK_LO]) mask |= 1u << k;
    }
    if (g_npend == WINDOW) {
        count(g_pend[0].a, g_pend[0].n, g_pend[0].mask);
        memmove(g_pend, g_pend + 1, sizeof g_pend[0] * (WINDOW - 1));
        g_npend--;
    }
    g_pend[g_npend].a = a;
    g_pend[g_npend].n = n;
    g_pend[g_npend].mask = mask;
    g_npend++;
}

void rd_trace_write(u32 a, u32 n)
{
    for (u32 k = 0; k < n; k++)
        if (a + k >= TCRT_STACK_LO && a + k - TCRT_STACK_LO < STACK_SPAN) g_written[a + k - TCRT_STACK_LO] = 1;
}

void rd_writes_on(void)
{
    if (!rd_tracing && !rd_writes) {
        memset(g_written, 0, sizeof g_written);
        g_npend = 0;
    }
    rd_writes = 1;
}

int rd_written(u32 a)
{
    return (rd_tracing || rd_writes) && a >= TCRT_STACK_LO && a - TCRT_STACK_LO < STACK_SPAN
           && g_written[a - TCRT_STACK_LO];
}

void rd_trace_report(FILE *out)
{
    flush();
    for (int i = 0; i < g_ntr; i++)
        if (g_tr[i].armed) fprintf(out, "OT %u %08x %u %u\n", g_tr[i].call, g_tr[i].target, g_tr[i].field, g_tr[i].end);
}

void rd_trace_reset(void)
{
    g_ntr = 0;
    g_npend = 0;
    rd_tracing = 0;
    rd_writes = 0;
}
