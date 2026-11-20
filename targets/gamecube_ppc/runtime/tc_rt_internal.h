/* Runner-only interface to the GameCube runtime. Decompiled code must never include this file. */
#ifndef TC_RT_INTERNAL_H
#define TC_RT_INTERNAL_H

#include <stdio.h>
#include "tc_rt.h"

#define TCRT_RAM_BASE 0x80000000u
#define TCRT_RAM_SIZE 0x01800000u
#define TCRT_STACK_LO 0x817f0000u /* writes at and above it (the stack window) are not compared */
#define TCRT_SP 0x817fe000u

/* The uncached view of MEM1 (0xc0000000..0xc17fffff) is the same bytes: every access and call target is folded
 * to its 0x80... form, exactly as the oracle does (TcBase.fold). */
static inline u32 tcrt_fold(u32 a)
{
    return (a - 0xc0000000u < TCRT_RAM_SIZE) ? a - 0x40000000u : a;
}

/* The hardware registers (target.json memory.mmio; tests/test_gc_io.py compares): a store is logged in order and
 * reported as an IO line, a read faults. */
static inline int tcrt_mmio(u32 a)
{
    return (a - 0xcc000000u < 0x10000u) || (a - 0xe0000000u < 0x4000u);
}

int tcrt_init(const char *dol, const char *code_ranges);
void tcrt_reset(void);
void tcrt_poke(u32 addr, const u8 *data, u32 len);
/* Scripted call results per vector (vectors.txt C and D; TcOracle.RETS): 16 words, then any later call's the oracle's
 * repair or a genretptr hint made a pointer (TcOracle.lateOf). A word a line does not have is 0, and so is a call's
 * past TCRT_RETS. */
#define TCRT_RETS 1024
void tcrt_set_call_returns(const u32 rets[TCRT_RETS], const u64 frets[TCRT_RETS]);
void tcrt_report(FILE *out, u32 r3, u64 f1_bits, u32 r4);
void tcrt_set_hw(const u32 *words); /* vectors.txt H: what reads of hardware registers answer, in turn */
/* vectors.txt X (after tcrt_reset, which clears it): a helper entry's r11, r14..r31 (gpr[0..17]) and f14..f31's bits
 * (fpr[0..17]) at entry (tc_inr, tc_inf); r14..r31 and f14..f31 are also what the exit frame reports unless tc_xr. */
void tcrt_set_frame(u32 r11, const u32 *gpr, const u64 *fpr);
/* tc_strict.h (included by tc_rt.c): the stricter judge's vectors.txt lines J and U (header), N and W (per
 * vector, after tcrt_reset, which forgets them), and its report (CS lines). */
void tcrt_strict_line(const char *line);
void tcrt_strict_reset(void);
int tcrt_strict_flags(void);
void tcrt_strict_report(FILE *out);
/* tc_fx.h (included by tc_rt.c): vectors.txt Q and K, per vector after tcrt_reset (which forgets them). */
void tcrt_fx_line(const char *line);

#endif
