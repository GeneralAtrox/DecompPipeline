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
void sh4_fsca(u32 fpul, f32 *sin_out, f32 *cos_out);  /* fsca FPUL,DRn: sin to FRn, cos to FRn+1 */
f32 sh4_fipr(const f32 a[4], const f32 b[4]);         /* fipr FVm,FVn: ((a0*b0 + a1*b1) + a2*b2) + a3*b3 */
void sh4_ftrv(f32 v[4]);                              /* ftrv XMTRX,FVn: v = XMTRX * v */
f32 sh4_xf(int index);                                /* read back-bank register xf0..xf15 (XMTRX) */
void sh4_set_xf(int index, f32 value);                /* write back-bank register xf0..xf15 */
f32 sh4_fr(int index);                                /* read fr0..fr15 (fr12..fr15: vector-library state, fv12) */
void sh4_set_fr(int index, f32 value);                /* write fr0..fr15 */
void sh4_frchg(void);                                 /* frchg: swap fr0..fr15 with xf0..xf15, toggle FPSCR.FR */
void sh4_fschg(void);                                 /* fschg: toggle FPSCR.SZ (fmov moves 64-bit pairs while set) */
void sh4_fpchg(void);                                 /* fpchg: toggle FPSCR.PR */
void sh4_ftrv_fv(int n);                              /* ftrv XMTRX,FVn: fr n..n+3 = XMTRX * fr n..n+3 (n = 0,4,8,12) */
void sh4_fipr_fv(int m, int n);                       /* fipr FVm,FVn: fr n+3 = sh4_fipr(fr m..m+3, fr n..n+3) */
u32 sh4_r(int index);                                 /* read r8..r14 as runtime state (hand-written code keeps a */
void sh4_set_r(int index, u32 value);                 /* pointer in r14 across calls); write it */

/* The original's endless loop (`bra .`, e.g. an error halt): call tc_halt() where the original reaches it, after the
 * call when it is a callee that never returns (TcExit). It does not return: the test run reports HALT there (as
 * tc_rt.h), and the port ends the run with a HALT line in its log. */
#if defined(_MSC_VER)
__declspec(noreturn)
#endif
void tc_halt(void);

#endif
