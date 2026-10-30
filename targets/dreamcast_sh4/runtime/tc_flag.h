/* A run flag (project.json "run_flags", TcCalls; vectors.txt header line f, kept across vectors), the runtimes' side:
 * the Nth mocked call (from 1) to the listed callee in a vector stores 0 into the word, as the callee's own store,
 * after the strict judge's writes (TcStrict.call). The settable form (core/run_flag_set, line "f OFFSET CALLEE N REG
 * BITS") sets BITS in the word as it is at that call instead; the runner works out each vector's word (rREG at the
 * entry + OFFSET) and gives it with tcrt_set_run_flag before the vector runs, and the bits with tcrt_set_run_bits.
 * Exactly one file of each runtime includes it (tc_rt.c; the readable runtime's rd_veh.c), after defining tcs_read
 * and tcs_write (tc_strict.h's: only where the oracle reads and writes); it resets g_flag_seen per vector.
 * Nearest existing: the copies of this block that were in tc_rt.c and rd_veh.c (both over 400 lines: they may not
 * grow), now this one file. */
#ifndef TC_FLAG_H
#define TC_FLAG_H

static u32 g_flag_word, g_flag_callee, g_flag_n, g_flag_seen, g_flag_bits;

void tcrt_set_run_flag(u32 word, u32 callee, u32 n)
{
    g_flag_word = tcrt_fold(word);
    g_flag_callee = tcrt_fold(callee);
    g_flag_n = n;
}

void tcrt_set_run_bits(u32 bits) { g_flag_bits = bits; }

static void flag_apply(u32 target)
{
    u8 b[4] = {0};
    if (!g_flag_n || target != g_flag_callee || ++g_flag_seen != g_flag_n) return;
    if (g_flag_bits && !tcs_read(g_flag_word, 4, b)) return; /* the oracle sets nothing it cannot read (TcBase.inRam) */
    for (int k = 0; k < 4; k++) b[k] |= (u8)(g_flag_bits >> (8 * k));
    tcs_write(g_flag_word, 4, b); /* only where the oracle writes (TcStrict.writable) */
}

#endif
