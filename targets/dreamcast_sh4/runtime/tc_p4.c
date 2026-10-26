/* P4 operand-cache address array stores (0xF4000000..0xF4FFFFFF): the game invalidates the cache by writing its
 * address array (Toy Commander 8c04c230: 512 stores of 0 at a stride of 0x20). Such a store changes no RAM; the
 * oracle (TcOracle.collect) logs each address written with its last value as W lines, and so does the runner here,
 * so the stores are compared like memory writes. Reads stay faults (what they return is cache state, not modelled);
 * the oracle faults on them too (TcFold.p4Read), also on a read of bytes the run stored there. Stores to QACR0/1
 * (0xFF000038..0xFF00003F, the store queues' area: Spawn 8c05fec2) are logged the same way (TcExit.p4). */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "tc_rt_internal.h"

#define MAX_P4 8192 /* bytes; a run storing more distinct bytes there gets a fault */

typedef struct {
    u32 addr;
    u8 value;
} P4Byte;

static P4Byte g_p4[MAX_P4];
static u32 g_np4;
static int g_p4_overflow;

static void p4_log(u32 addr, u32 value, u32 size)
{
    for (u32 b = 0; b < size; b++) {
        u32 a = addr + b, k = 0;
        u8 v = (u8)(value >> (8 * b));
        while (k < g_np4 && g_p4[k].addr != a) k++;
        if (k == g_np4) {
            if (g_np4 == MAX_P4) {
                g_p4_overflow = 1;
                continue;
            }
            g_p4[g_np4++].addr = a;
        }
        g_p4[k].value = v;
    }
}

int tcrt_p4_store(u32 addr, u32 value, u32 size)
{
    if (!tcrt_p4_logged(addr, size)) return 0;
    p4_log(addr, value, size);
    return 1;
}

/* A store to a hardware register the project lists for the function (tc_rt.h, rd_rt.h; TcHw): logged as the P4
 * stores are, so it is compared as the oracle's W line for it (TcExit.p4). */
void tc_hw_write32(u32 reg, u32 value) { p4_log(reg, value, 4); }

void tc_hw_write16(u32 reg, u16 value) { p4_log(reg, value, 2); }

/* Listed cache block ops (TcHw): no-ops, memory is coherent in the test run as in the oracle's. */
void sh4_ocbi(u32 addr) { (void)addr; }

void sh4_ocbp(u32 addr) { (void)addr; }

void sh4_ocbwb(u32 addr) { (void)addr; }

void tcrt_p4_reset(void)
{
    g_np4 = 0;
    g_p4_overflow = 0;
}

static int cmp_p4(const void *a, const void *b)
{
    u32 x = ((const P4Byte *)a)->addr, y = ((const P4Byte *)b)->addr;
    return x < y ? -1 : (x > y);
}

void tcrt_p4_report(FILE *out)
{
    if (g_p4_overflow) fprintf(out, "FAULT too-many-p4-stores\n");
    qsort(g_p4, g_np4, sizeof g_p4[0], cmp_p4);
    u32 i = 0;
    while (i < g_np4) {
        u32 start = i;
        while (i + 1 < g_np4 && g_p4[i + 1].addr == g_p4[i].addr + 1) i++;
        i++;
        fprintf(out, "W %08x ", g_p4[start].addr);
        for (u32 k = start; k < i; k++) fprintf(out, "%02x", g_p4[k].value);
        fputc('\n', out);
    }
}
