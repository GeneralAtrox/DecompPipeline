/* Runner-only interface to the runtime. Decompiled code must never include this file. */
#ifndef TC_RT_INTERNAL_H
#define TC_RT_INTERNAL_H

#include <setjmp.h>
#include <stdio.h>
#include "tc_rt.h"

#define TCRT_RAM_BASE 0x8c000000u
#define TCRT_RAM_SIZE 0x01000000u
#define TCRT_VALID_LO 0x8c010000u

/* vectors.txt header line S (TcSq: a function sending a state pointer's store queue): the store-queue window
 * 0xe0000000..0xe3ffffff folds onto RAM too, its four views of the 16 MB, as in the port (port.h GUEST_SQ_BASE). */
extern int tcrt_sq_window;
void tcrt_set_sq_window(void);

/* Main RAM is also visible at 0x0c... (P0); with the MMU off it is the same bytes. Every access and call target is
 * folded to its 0x8c... form, exactly as the oracle does (TcBase.fold: P0, and the window after a line S). */
static inline u32 tcrt_fold(u32 a)
{
    if (tcrt_sq_window && a - 0xe0000000u < 0x04000000u) return 0x8c000000u | (a & 0x00ffffffu);
    return (a >= 0x0c000000u && a < 0x0d000000u) ? (a | 0x80000000u) : a;
}
#define TCRT_STACK_LO 0x8cff0000u
/* P4 operand-cache address array (tc_p4.c): stores there are logged and compared, not faults */
#define TCRT_P4_OCA_LO 0xf4000000u
#define TCRT_P4_OCA_HI 0xf5000000u
#define TCRT_SP 0x8cffe000u

int tcrt_init(const char *image, const char *code_ranges, const char *fsca_table);
void tcrt_reset(void);
void tcrt_set_fpscr(u32 fpscr);          /* FPSCR at entry (vectors.txt line P): its RM sets MXCSR.RC at reset */
void tcrt_poke(u32 addr, const u8 *data, u32 len);
void tcrt_set_xf(const u32 bits[16]);
void tcrt_set_fh(const u32 bits[4]);     /* fr12..fr15 at entry (vectors.txt line H) */
void tcrt_report_fh(FILE *out);          /* fr12..fr15 at exit (line FH): only for vectors with an H line */
void tcrt_set_gs(const u32 bits[7]);     /* r8..r14 at entry (line G) */
void tcrt_report_gs(FILE *out);          /* r8..r14 at exit (line GH): only for vectors with a G line */
void tcrt_set_low(const u32 bits[4]);    /* r0..r3 at entry (line L) */
u32 tcrt_low(int index);                 /* for the generated thunk: a parameter in r0..r3 */
void tcrt_set_retx(u32 value);           /* for the generated thunk: the extra result word (line RETX) */
void tcrt_set_call_returns(const u32 rets[16]);
void tcrt_add_live(u32 target, u32 reg, u32 n, const u32 d[4]); /* header line K: a callee changing rN (TcLive) */
void tcrt_set_draws(const u32 draws[16]); /* line A: the vector's draws for the K callees' deltas */
void tcrt_report(FILE *out, u32 r0, u32 fr0_bits);
/* tc_halt (TcExit): longjmps here; the runner sets it before each vector. tcrt_halted: the vector halted (HALT). */
extern jmp_buf tcrt_halt_jmp;
extern int tcrt_halted;

/* tc_fpu.c: the FPU registers (fr0..fr15, xf0..xf15 = XMTRX, the FPSCR mode bits FR, SZ, PR). */
void tcrt_fpu_reset(void);
void tcrt_set_fq(const u32 bits[16]);    /* fr0..fr15 at entry (line Q): the whole FPU is state (meta fpu_state) */
int tcrt_fpu_state(void);                /* line Q was given */
void tcrt_fpu_result(u32 bits);          /* a mocked call's result, in fr0 as in r0 */
void tcrt_fpu_log(u32 *rec, const TcArgs *args); /* call log fields 5..12, 21..24 and, with line Q, 36..56 */
void tcrt_report_xf(FILE *out);          /* line XF */
void tcrt_report_fq(FILE *out);          /* fr0..fr15 and the mode word at exit (line QH): only with line Q */

/* tc_p4.c: stores to the P4 operand-cache address array, reported as W lines. */
int tcrt_p4_store(u32 addr, u32 value, u32 size); /* 1: addr is in the array (the store is logged) */
void tcrt_p4_reset(void);
void tcrt_p4_report(FILE *out);

#endif
