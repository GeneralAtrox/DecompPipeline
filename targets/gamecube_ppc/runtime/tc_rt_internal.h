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

int tcrt_init(const char *dol, const char *code_ranges);
void tcrt_reset(void);
void tcrt_poke(u32 addr, const u8 *data, u32 len);
void tcrt_set_call_returns(const u32 rets[16], const u64 frets[16]);
void tcrt_report(FILE *out, u32 r3, u64 f1_bits, u32 r4);
/* tc_strict.h (included by tc_rt.c): the stricter judge's vectors.txt lines J and U (header), N and W (per
 * vector, after tcrt_reset, which forgets them), and its report (CS lines). */
void tcrt_strict_line(const char *line);
void tcrt_strict_reset(void);
int tcrt_strict_flags(void);
void tcrt_strict_report(FILE *out);

#endif
