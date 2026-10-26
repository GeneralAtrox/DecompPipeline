/* Runner-only interface to the runtime. Decompiled code must never include this file. */
#ifndef TC_RT_INTERNAL_H
#define TC_RT_INTERNAL_H

#include <setjmp.h>
#include <stdio.h>
#include "tc_rt.h"

/* The SH-4 helpers of the shared runtime files (tc_fpu.c, tc_p4.c, tc_rt.c): no SH-2 code calls them; they stay so
 * the call records and logs keep the Dreamcast runner's formats (FPU fields 0). Not in tc_rt.h: agents never see them. */
s32 sh4_ftrc(f32 x);                                  /* ftrc FRm,FPUL (float -> int conversion) */
f32 sh4_fsrra(f32 x);                                 /* fsrra: 1.0f / sqrt(x) */
f32 sh4_fsqrt_rm(f32 x);                              /* fsqrt: sqrt(x), rounded as FPSCR.RM says (the game's mode) */
f32 sh4_fmac(f32 fr0, f32 frm, f32 frn);              /* fmac FR0,FRm,FRn: fr0 * frm + frn, rounded once */
void sh4_fsca(u32 fpul, f32 *sin_out, f32 *cos_out);  /* fsca FPUL,DRn: sin to FRn, cos to FRn+1 */
f32 sh4_fipr(const f32 a[4], const f32 b[4]);         /* fipr FVm,FVn: ((a0*b0 + a1*b1) + a2*b2) + a3*b3 */
void sh4_ftrv(f32 v[4]);                              /* ftrv XMTRX,FVn: v = XMTRX * v */
f32 sh4_xf(int index);                                /* read back-bank register xf0..xf15 (XMTRX) */
void sh4_set_xf(int index, f32 value);                /* write back-bank register xf0..xf15 */
/* fr12..fr15 as runtime state (index 12..15): the vector library keeps a vector there across calls (fv12).
 * When context.md lists them, their entry values come from sh4_fr, their exit values are compared (set them
 * with sh4_set_fr where the original writes them), and a callee that reads them is compared on them at the call. */
f32 sh4_fr(int index);                                /* read fr0..fr15 (fr0..fr11: only when the whole FPU is state) */
void sh4_set_fr(int index, f32 value);                /* write fr0..fr15 */
/* The whole FPU as state, for a function that switches FPU modes (context.md says when): fr0..fr15 (sh4_fr), XMTRX
 * (sh4_xf) and the FPSCR mode bits FR, SZ and PR are set at entry and compared at exit and at every call. The
 * helpers do what the instruction does to that state. tc_call leaves all of it unchanged except fr0 (= its result). */
void sh4_frchg(void);                                 /* frchg: swap fr0..fr15 with xf0..xf15, toggle FPSCR.FR */
void sh4_fschg(void);                                 /* fschg: toggle FPSCR.SZ (fmov moves 64-bit pairs while set) */
void sh4_fpchg(void);                                 /* fpchg: toggle FPSCR.PR */
void sh4_ftrv_fv(int n);                              /* ftrv XMTRX,FVn: fr n..n+3 = XMTRX * fr n..n+3 (n = 0,4,8,12) */
void sh4_fipr_fv(int m, int n);                       /* fipr FVm,FVn: fr n+3 = sh4_fipr(fr m..m+3, fr n..n+3) */
f32 tc_callf(u32 target, const TcArgs *args);
void sh4_ocbi(u32 addr);
void sh4_ocbp(u32 addr);
void sh4_ocbwb(u32 addr);

/* Saturn work RAM: low 0x00200000..0x002fffff and high 0x06000000..0x060fffff, one buffer of two 1 MB banks, low at
 * offset 0 and high at TCRT_BANK (offsets sort as the addresses do). A workspace's binary is a work RAM high image
 * from 0x06000000 (tools/overlay_image.py; its padding is uninitialized in the oracle, zero here). */
#define TCRT_LOW_BASE 0x00200000u
#define TCRT_HIGH_BASE 0x06000000u
#define TCRT_BANK 0x00100000u
#define TCRT_RAM_SIZE (2u * TCRT_BANK)
#define TCRT_IMAGE_BASE TCRT_HIGH_BASE

/* [a, a+size) in one bank: its buffer offset in *off, else 0 */
static inline int tcrt_ram_off(u32 a, u32 size, u32 *off)
{
    if (a - TCRT_LOW_BASE <= TCRT_BANK - size) {
        *off = a - TCRT_LOW_BASE;
        return 1;
    }
    if (a - TCRT_HIGH_BASE <= TCRT_BANK - size) {
        *off = TCRT_BANK + (a - TCRT_HIGH_BASE);
        return 1;
    }
    return 0;
}

static inline u32 tcrt_ram_addr(u32 off) { return off < TCRT_BANK ? TCRT_LOW_BASE + off : TCRT_HIGH_BASE + off - TCRT_BANK; }

/* vectors.txt header line S: the SH-4's store-queue window. The SH-2 has none (TcSq never writes the line); kept so
 * the runner reads the shared format. */
extern int tcrt_sq_window;
void tcrt_set_sq_window(void);

/* Work RAM is also visible through the cache-through view (address bit 29: 0x26..., 0x202...); it is the same
 * bytes. Every access and call target is folded to its cached form, exactly as the oracle does (TcBase.fold: only
 * the two RAM banks fold; hardware addresses keep the form the code uses). */
static inline u32 tcrt_fold(u32 a)
{
    if (a - (TCRT_HIGH_BASE + 0x20000000u) < TCRT_BANK || a - (TCRT_LOW_BASE + 0x20000000u) < TCRT_BANK)
        return a - 0x20000000u;
    return a;
}
#define TCRT_STACK_LO 0x060f0000u
/* The SH-2 has no P4 area: no store is logged outside RAM (tc_p4.c keeps listed hardware writes, TcHw). */
static inline int tcrt_p4_logged(u32 a, u32 size)
{
    (void)a;
    (void)size;
    return 0;
}
#define TCRT_SP 0x060fe000u
#define TCRT_RETS 64 /* scripted call results per vector (line C, TcOracle.RETS); a later call returns 0 */

int tcrt_init(const char *image, const char *code_ranges, const char *fsca_table);
void tcrt_reset(void);
void tcrt_set_fpscr(u32 fpscr);          /* FPSCR at entry (vectors.txt line P): its RM sets MXCSR.RC at reset */
void tcrt_poke(u32 addr, const u8 *data, u32 len);
void tcrt_set_xf(const u32 bits[16]);
void tcrt_set_fh(const u32 bits[4]);     /* fr12..fr15 at entry (vectors.txt line H) */
void tcrt_report_fh(FILE *out);          /* fr12..fr15 at exit (line FH): only for vectors with an H line */
void tcrt_set_gs(const u32 bits[7]);     /* r8..r14 at entry (line G) */
void tcrt_report_gs(FILE *out);          /* r8..r14 at exit (line GH): only for vectors with a G line */
void tcrt_set_sr(u32 imask);            /* line I: SR at entry, this interrupt mask (TcSr) */
void tcrt_report_sr(FILE *out);         /* line IM: the interrupt mask at exit, only after a line I */
void tcrt_add_hw(u32 reg, u32 value, u32 size); /* header line Y: a listed hardware register, its value, its width */
void tcrt_set_gbr_state(void);          /* header line Z: GBR is state (TcHw) */
void tcrt_report_gbr(FILE *out);        /* line GB: GBR at exit, only after a line Z */
void tcrt_set_low(const u32 bits[4]);    /* r0..r3 at entry (line L) */
u32 tcrt_low(int index);                 /* for the generated thunk: a parameter in r0..r3 */
void tcrt_set_retx(u32 value);           /* for the generated thunk: the extra result word (line RETX) */
void tcrt_set_call_returns(const u32 rets[TCRT_RETS]);
void tcrt_add_live(u32 target, u32 reg, u32 n, const u32 d[4]); /* header line K: a callee changing rN (TcLive) */
void tcrt_set_draws(const u32 draws[16]); /* line A: the vector's draws for the K callees' deltas */
void tcrt_report(FILE *out, u32 r0, u32 fr0_bits);
/* tc_halt (TcExit), tc_spin (TcSpin): longjmp here; the runner sets it before each vector. tcrt_halted: the vector
 * halted (1: HALT, 2: HALT SPIN). */
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

/* tc_strict.h (included by tc_rt.c): the stricter judge's vectors.txt lines J and U (header), N and W (per
 * vector, after tcrt_reset, which forgets them), and its report (CS lines). */
void tcrt_strict_line(const char *line);
void tcrt_strict_reset(void);
int tcrt_strict_flags(void);
void tcrt_strict_report(FILE *out);
void tcrt_fpu_scratch(int i, u32 bits); /* mock_junk: fri (1..11) after a mocked call (tc_fpu.c) */
u32 tcrt_fr_bits(int index);             /* fr0..fr15 as bits (the exit frame, XR) */

/* tc_p4.c: stores to the P4 operand-cache address array and QACR0/1, reported as W lines. */
int tcrt_p4_store(u32 addr, u32 value, u32 size); /* 1: addr is one of them (the store is logged) */
void tcrt_p4_reset(void);
void tcrt_p4_report(FILE *out);

#endif
