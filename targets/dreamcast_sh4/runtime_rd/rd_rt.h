/* DecompPipeline Dreamcast SH-4 readable-C runtime: what readable game code may use besides its own headers.
 *
 * Readable code uses real pointers, structs and named globals. In the verification build the guest RAM is mapped
 * at its Dreamcast address (0x8c000000), so a guest pointer is a host pointer, and every call to another original
 * function is caught and logged. The helpers below are the SH-4 instructions with no portable C equivalent;
 * the port replaces them.
 */
#ifndef RD_RT_H
#define RD_RT_H

#include <stdarg.h> /* va_list, va_start, va_arg, va_end: a variadic function's own readable C */
#include <stdint.h>

typedef uint8_t u8;
typedef uint16_t u16;
typedef uint32_t u32;
typedef int8_t s8;
typedef int16_t s16;
typedef int32_t s32;
typedef float f32;
typedef uint64_t u64; /* a 64-bit result in r0:r1 (the SHC runtime's 64-bit multiply helpers) */
typedef int64_t s64;

s32 sh4_ftrc(f32 x);                                  /* ftrc FRm,FPUL (float -> int conversion) */
f32 sh4_fsrra(f32 x);                                 /* fsrra: 1.0f / sqrt(x) */
f32 sh4_fsqrt_rm(f32 x);                              /* fsqrt: sqrt(x), rounded as FPSCR.RM says (the game's mode) */
f32 sh4_fmac(f32 fr0, f32 frm, f32 frn);              /* fmac FR0,FRm,FRn: fr0 * frm + frn, rounded once */
void sh4_fsca(u32 fpul, f32 *sin_out, f32 *cos_out);  /* fsca FPUL,DRn: sin to FRn, cos to FRn+1 */
f32 sh4_fipr(const f32 a[4], const f32 b[4]);         /* fipr FVm,FVn: a0*b0 + .. + a3*b3, rounded once */
void sh4_ftrv(f32 v[4]);                              /* ftrv XMTRX,FVn: v = XMTRX * v */
/* Double precision (FPSCR.PR=1, set by the original's lds FPSCR): DRn is the pair fr n (high word) : fr n+1 (low
 * word), n even, kept as two f32 variables carrying the bits. The helpers do the double operation on the pairs; the
 * function's C itself never holds a double. Rounding and flushing as the f32 operations (MXCSR: FPSCR.RM, DN). Each
 * follows the FPSCR.PR the run has (sh4_lds_fpscr): with PR=0 it is the single instruction on fr n and fr m, as the
 * SH-4 runs it (fcnvsd and fcnvds, undefined there, report a fault). */
void sh4_dadd(f32 *nh, f32 *nl, f32 mh, f32 ml);      /* fadd DRm,DRn: DRn = DRn + DRm */
void sh4_dsub(f32 *nh, f32 *nl, f32 mh, f32 ml);      /* fsub DRm,DRn: DRn = DRn - DRm */
void sh4_dmul(f32 *nh, f32 *nl, f32 mh, f32 ml);      /* fmul DRm,DRn: DRn = DRn * DRm */
void sh4_ddiv(f32 *nh, f32 *nl, f32 mh, f32 ml);      /* fdiv DRm,DRn: DRn = DRn / DRm */
void sh4_dsqrt(f32 *nh, f32 *nl);                     /* fsqrt DRn */
u32 sh4_dcmpeq(f32 nh, f32 nl, f32 mh, f32 ml);       /* fcmp/eq DRm,DRn: T = DRn == DRm */
u32 sh4_dcmpgt(f32 nh, f32 nl, f32 mh, f32 ml);       /* fcmp/gt DRm,DRn: T = DRn > DRm */
void sh4_dfloat(f32 *nh, f32 *nl, u32 fpul);          /* float FPUL,DRn: DRn = (s32)FPUL */
s32 sh4_dftrc(f32 h, f32 l);                          /* ftrc DRm,FPUL (saturating, as sh4_ftrc) */
void sh4_fcnvsd(f32 *nh, f32 *nl, u32 fpul);          /* fcnvsd FPUL,DRn: DRn = the single in FPUL */
u32 sh4_fcnvds(f32 h, f32 l);                         /* fcnvds DRm,FPUL: FPUL = DRm as a single (its bits) */
f32 sh4_xf(int index);                                /* read back-bank register xf0..xf15 (XMTRX) */
void sh4_set_xf(int index, f32 value);                /* write back-bank register xf0..xf15 */
f32 sh4_fr(int index);                                /* read fr0..fr15 (fr12..fr15: vector-library state, fv12) */
void sh4_set_fr(int index, f32 value);                /* write fr0..fr15 */
void sh4_frchg(void);                                 /* frchg: swap fr0..fr15 with xf0..xf15, toggle FPSCR.FR */
void sh4_fschg(void);                                 /* fschg: toggle FPSCR.SZ (fmov moves 64-bit pairs while set) */
void sh4_fpchg(void);                                 /* fpchg: toggle FPSCR.PR */

/* FPSCR, for a function whose original reads or writes it (meta fpscr_state, TcFpscr: Spawn 8c010080, Toy Commander
 * 8c0611d8): sh4_sts_fpscr() where the original has sts FPSCR (or sts.l), sh4_lds_fpscr(v) where it has lds ...,FPSCR
 * (or lds.l; the SH-4 keeps bits 0..21). The test run gives each vector its entry FPSCR and compares FPSCR at exit and
 * at every call, PR, SZ and FR included (an lds sets them, as fpchg, fschg and frchg do). The FPU keeps rounding and
 * flushing as at entry: the test run never has a float operation after a change of RM, DN or the enables. */
u32 sh4_sts_fpscr(void);                              /* sts FPSCR,Rn */
void sh4_lds_fpscr(u32 value);                        /* lds Rm,FPSCR */
void sh4_ftrv_fv(int n);                              /* ftrv XMTRX,FVn: fr n..n+3 = XMTRX * fr n..n+3 (n = 0,4,8,12) */
void sh4_fipr_fv(int m, int n);                       /* fipr FVm,FVn: fr n+3 = sh4_fipr(fr m..m+3, fr n..n+3) */
u32 sh4_r(int index);                                 /* read r8..r14 as runtime state (hand-written code keeps a */
void sh4_set_r(int index, u32 value);                 /* pointer in r14 across calls), or the r4..r7 a callee handed
                                                       * back (meta r_out); write it */
u32 sh4_t(void);                                      /* T as a callee left it (meta t_out), and T handed back */
void sh4_set_t(u32 value);                            /* to the caller (meta t_export; tc_rt.h) */
u32 sh4_stc_sr(void);                                 /* stc sr,Rn: SR (the interrupt mask is compared at exit, */
void sh4_ldc_sr(u32 value);                           /* as tc_rt.h); ldc Rm,sr */
u32 tc_hw_read32(u32 reg);                            /* a listed hardware register's load (tc_rt.h): the target's */
u32 sh4_stc_gbr(void);                                /* value; GBR where listed: stc gbr,Rn (compared at exit, */
void sh4_ldc_gbr(u32 value);                          /* as tc_rt.h); ldc Rm,gbr */
void tc_hw_write32(u32 reg, u32 value);               /* a listed hardware register's store (tc_rt.h): compared */
void tc_hw_write16(u32 reg, u16 value);               /* as a memory write; mov.w */
void sh4_ocbi(u32 addr);                              /* listed cache block ops (tc_rt.h): no-ops in the test run */
void sh4_ocbp(u32 addr);
void sh4_ocbwb(u32 addr);

/* The original's endless loop (`bra .`, e.g. an error halt): call tc_halt() where the original reaches it, after the
 * call when it is a callee that never returns (TcExit). It does not return: the test run reports HALT there (as
 * tc_rt.h), and the port ends the run with a HALT line in its log. */
#if defined(_MSC_VER)
__declspec(noreturn)
#endif
void tc_halt(void);

/* The original's wait loop that only an interrupt or the hardware ends (TcSpin): keep the loop and its condition,
 * and call tc_spin() as its body: `while (flag == 0 && cnt == start) tc_spin();`, or last in its body when the loop
 * calls functions (the run halts after their first pass). The test run reports HALT SPIN
 * there (as tc_rt.h); the port waits for the next vertical blank and the loop tests its condition again. */
void tc_spin(void);

#endif
