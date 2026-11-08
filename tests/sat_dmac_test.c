/* The SH-2's DMA controller (sat_mem.c, included whole for its static functions): programs SAR0, DAR0, TCR0, CHCR0 and
 * DMAOR through the bus as Robo Pit's copy routine (fn_06077074) does, and checks what lands in work RAM.
 * Run by tests/test_sat_dmac.py. */
#define _CRT_SECURE_NO_WARNINGS
#include <stdio.h>
#include "sat_mem.c"

static int g_fail;

/* a stand-in for each kit function sat_mem.c calls */
u32 sat_wr_n;
void sat_poll_hint(u32 every) { (void)every; }
void sat_busy_poll(void) {}
u64 sat_clock_ticks(u32 rate) { (void)rate; return 0; }
int sat_sys_det(void) { return 0; }
u32 sat_det_frt(u32 shift) { (void)shift; return 0; }
void port_log(const char *fmt, ...) { (void)fmt; }

static void check(int ok, const char *what)
{
    printf("%s: %s\n", ok ? "ok  " : "FAIL", what);
    if (!ok) g_fail = 1;
}

#define SRC 0x06001000u
#define DST 0x06002000u

static void reset(void)
{
    for (u32 i = 0; i < 0x100u; i += 4u) {
        wr32(SRC + i, 0x11000000u + i);
        wr32(DST + i, 0xeeeeeeeeu);
    }
    wr32(0xffffffb0u, 0); /* DMAOR */
    wr32(0xffffff8cu, 0); /* CHCR0 */
}

/* the game's order: SAR, DAR, TCR, CHCR, then DMAOR's master enable */
static void dma(u32 tcr, u32 chcr, u32 dmaor)
{
    reset();
    wr32(0xffffff80u, SRC);
    wr32(0xffffff84u, DST);
    wr32(0xffffff88u, tcr);
    wr32(0xffffff8cu, chcr);
    wr32(0xffffffb0u, dmaor);
}

/* bytes copied from SRC to DST, then whether everything after them is untouched */
static int copied(u32 bytes)
{
    for (u32 i = 0; i < 0x100u; i += 4u)
        if (rd32(DST + i) != (i < bytes ? 0x11000000u + i : 0xeeeeeeeeu)) return 0;
    return 1;
}

int main(void)
{
    dma(0x20u / 4u, 0x5e01u, 9u); /* 16-byte units, both addresses counting up: TCR holds longwords */
    check(copied(0x20u), "16-byte units: TCR 8 copies 32 bytes, no more");
    check(rd32(0xffffff80u) == SRC + 0x20u && rd32(0xffffff84u) == DST + 0x20u, "16-byte units: SAR and DAR end past the copy");
    check(rd32(0xffffff88u) == 0 && (rd32(0xffffff8cu) & 2u), "TCR runs out and CHCR's TE is set");

    dma(0x80u / 4u, 0x5e01u, 9u);
    check(copied(0x80u), "16-byte units: TCR 0x20 copies 128 bytes");

    dma(0x20u / 4u, 0x5a01u, 9u); /* longword units */
    check(copied(0x20u), "longword units: TCR 8 copies 32 bytes");

    dma(0x20u / 2u, 0x5601u, 9u); /* word units */
    check(copied(0x20u), "word units: TCR 16 copies 32 bytes");

    dma(0x20u / 4u, 0x5e01u, 0u);
    check(copied(0), "DMAOR's master enable off: nothing moves");

    puts(g_fail ? "FAILED" : "all ok");
    return g_fail;
}
