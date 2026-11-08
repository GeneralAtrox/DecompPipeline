/* The spin words of translator C (game.json's sources name one with port_spin_word): see rd32 in tx_rt.c. */
#include <stdint.h>
#include "platform.h"
#include "port.h"
#include "tx_rt.h"

/* A word the game busy-waits on until an interrupt handler changes it (the game's sources name it: port_spin_word),
 * in a loop the judge's TcSpin cannot turn into tc_spin (it stores counters each pass). Read SPIN_READS times
 * unchanged within one vertical blank, the read waits for the next one, as the interrupt comes while the console
 * spins (sdk/ac_digi.c's rule for a polled voice). Up to SPIN_WORDS words; none unless a game names one. */
#define SPIN_WORDS 4
#define SPIN_READS 64u
#define AREA_MASK 0x1fffffffu
volatile int port_tx_spin_on; /* tx_rt.c's rd32 looks a word up while one is named */
/* tc_rt_inline.h's rd32 goes out of line only for an address these match: a game that named one word used to send every
 * read of translated C through two calls (14% of the samples of a game's fight). None named: all-ones, matching
 * only that address, whose read is the same either way. */
u32 port_tx_spin_bits = 0xffffffffu, port_tx_spin_mask = 0xffffffffu;
static u32 g_spin_word[SPIN_WORDS] = {AREA_MASK, AREA_MASK, AREA_MASK, AREA_MASK}, g_spin_value[SPIN_WORDS],
                                      g_spin_reads[SPIN_WORDS], g_spin_blank[SPIN_WORDS];

void port_spin_word(u32 addr)
{
    for (int i = 0; i < SPIN_WORDS; i++) {
        if (g_spin_word[i] != AREA_MASK && g_spin_word[i] != (addr & AREA_MASK)) continue;
        g_spin_word[i] = addr & AREA_MASK;
        port_tx_spin_on = 1;
        port_tx_spin_mask = AREA_MASK;
        for (int j = 1; j < SPIN_WORDS && g_spin_word[j] != AREA_MASK; j++)
            port_tx_spin_mask &= ~(g_spin_word[j] ^ g_spin_word[0]);
        port_tx_spin_bits = g_spin_word[0] & port_tx_spin_mask;
        port_log("tx: spin word %08x: a read unchanged %u times within a vertical blank waits for the next\n", addr,
                 SPIN_READS);
        return;
    }
    port_log("tx: spin word %08x not kept (%d words at most)\n", addr, SPIN_WORDS);
}

static u32 spin_read(int i, u32 addr, u32 value)
{
    u32 blank = port_vblank_count();
    if (value != g_spin_value[i] || blank != g_spin_blank[i]) {
        g_spin_value[i] = value;
        g_spin_blank[i] = blank;
        g_spin_reads[i] = 0;
    } else if (++g_spin_reads[i] >= SPIN_READS) {
        port_wait_vblank();
        g_spin_reads[i] = 0;
        g_spin_blank[i] = port_vblank_count();
        value = *(const volatile u32 *)(uintptr_t)addr;
        g_spin_value[i] = value;
    }
    return value;
}

u32 port_tx_spin_read(u32 addr, u32 value)
{
    for (int i = 0; i < SPIN_WORDS && g_spin_word[i] != AREA_MASK; i++)
        if ((addr & AREA_MASK) == g_spin_word[i]) return spin_read(i, addr, value);
    return value;
}

/* Readable C's way to a spin word (gen_port.py: game.json spin_words): the word's address, after one read of it counted
 * as translator C's rd32 counts it, so a readable loop waiting on it waits for the next vertical blank as the
 * translated loop does. Named on first use, so the game's sources need not call port_spin_word for it. */
volatile void *port_rd_spin(unsigned int addr)
{
    static u32 named[SPIN_WORDS];
    static int nnamed;
    int i = 0;
    while (i < nnamed && named[i] != (addr & AREA_MASK)) i++;
    if (i == nnamed && nnamed < SPIN_WORDS) {
        port_spin_word(addr);
        named[nnamed++] = addr & AREA_MASK;
    }
    port_tx_spin_read(addr, *(const volatile u32 *)(uintptr_t)addr);
    return (volatile void *)(uintptr_t)addr;
}
