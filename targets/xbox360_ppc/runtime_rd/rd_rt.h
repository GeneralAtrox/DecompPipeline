/* DecompPipeline Xbox 360 (Xenon PowerPC) readable-C runtime: what readable game code may use besides its own headers.
 *
 * Readable code uses real pointers, structs and named globals. In the verification build (a 64-bit host program) the
 * title's image (0x82000000), the test heap (0x40000000), the stack (0x70000000) and the processor block r13 points
 * at (0x7fff0000) are mapped at their own addresses, so a guest pointer is a host pointer, and every call to another
 * original function is caught and logged. Guest memory is big endian: every multi-byte value there has a be_ type and
 * is read with be_get and written with be_set (rd_be.h).
 *
 * Registers are 64-bit: a parameter or result declared u32/s32 is the low word, zero- or sign-extended into the
 * register as its type says; u64/s64 is the whole register. A pointer is the 32-bit guest address.
 *
 * Floating point: a float register holds a double. f64 is that register; f32 is only what the original rounds to
 * single (a single-precision instruction's result is (f64)(f32)(double operation); lfs and stfs move singles). The
 * helpers below are the PowerPC instructions with no exact portable C equivalent, declared exactly as the raw runtime's
 * tc_rt.h declares them (tests/test_x360_readable.py checks it); the port replaces them.
 */
#ifndef RD_RT_H
#define RD_RT_H

#include <stdarg.h>
#include <stdint.h>
#include "rd_be.h"

typedef uint8_t u8;
typedef uint16_t u16;
typedef uint32_t u32;
typedef uint64_t u64;
typedef int8_t s8;
typedef int16_t s16;
typedef int32_t s32;
typedef int64_t s64;
typedef float f32;
typedef double f64;

/* stfs of a register holding any double (the architecture's store conversion, not a rounding): for a value a single
 * holds, the same as be_set with (f32)value. */
void be_set_stfs(be_f32 *p, f64 value);

/* PowerPC instruction helpers, bit-exact with the reference model (the oracle). Integer helpers take and return
 * whole 64-bit registers. */
u64 ppc_rlwinm(u64 s, int sh, int mb, int me);         /* rlwinm rA,rS,SH,MB,ME (upper word: see DESIGN.md) */
u64 ppc_rlwnm(u64 s, u64 b, int mb, int me);           /* rlwnm rA,rS,rB,MB,ME */
u64 ppc_rlwimi(u64 a, u64 s, int sh, int mb, int me);  /* rlwimi rA,rS,SH,MB,ME (a: rA before) */
u64 ppc_rldicl(u64 s, int sh, int mb);                 /* rldicl rA,rS,SH,MB (clrldi, srdi, extrdi) */
u64 ppc_rldicr(u64 s, int sh, int me);                 /* rldicr rA,rS,SH,ME (sldi, clrrdi) */
u64 ppc_rldic(u64 s, int sh, int mb);                  /* rldic */
u64 ppc_rldimi(u64 a, u64 s, int sh, int mb);          /* rldimi (a: rA before) */
u64 ppc_rldcl(u64 s, u64 b, int mb);                   /* rldcl */
u64 ppc_rldcr(u64 s, u64 b, int me);                   /* rldcr */
u64 ppc_slw(u64 s, u64 b);                             /* slw: (u32)(s << (b & 63)), 0 for shifts of 32..63 */
u64 ppc_srw(u64 s, u64 b);                             /* srw */
u64 ppc_sraw(u64 s, u64 b, u32 *ca);                   /* sraw: sign-extended; *ca = XER[CA] (ca may be NULL) */
u64 ppc_srawi(u64 s, int sh, u32 *ca);                 /* srawi */
u64 ppc_sld(u64 s, u64 b);                             /* sld: 0 for shifts of 64..127 */
u64 ppc_srd(u64 s, u64 b);                             /* srd */
u64 ppc_srad(u64 s, u64 b, u32 *ca);                   /* srad */
u64 ppc_sradi(u64 s, int sh, u32 *ca);                 /* sradi */
u64 ppc_cntlzw(u64 s);                                 /* cntlzw: leading zeros of the low word, 32 for 0 */
u64 ppc_cntlzd(u64 s);                                 /* cntlzd: 64 for 0 */
u64 ppc_mullw(u64 a, u64 b);                           /* mullw: the 64-bit product of the low words, signed */
u64 ppc_mulhw(u64 a, u64 b);                           /* mulhw: high word of the signed product, zero-extended */
u64 ppc_mulhwu(u64 a, u64 b);                          /* mulhwu */
u64 ppc_mulhd(u64 a, u64 b);                           /* mulhd */
u64 ppc_mulhdu(u64 a, u64 b);                          /* mulhdu */
u64 ppc_divw(u64 a, u64 b);                            /* divw: sign-extended quotient (never called with b = 0 in a test) */
u64 ppc_divwu(u64 a, u64 b);                           /* divwu: zero-extended quotient */
u64 ppc_divd(u64 a, u64 b);                            /* divd */
u64 ppc_divdu(u64 a, u64 b);                           /* divdu */
u64 ppc_addc(u64 a, u64 b, u32 *ca);                   /* addc/addic: a + b, *ca = carry out of bit 0 (64-bit) */
u64 ppc_adde(u64 a, u64 b, u32 ca_in, u32 *ca);        /* adde (addze: b = 0; addme: b = ~0) */
u64 ppc_subfc(u64 a, u64 b, u32 *ca);                  /* subfc/subfic rD,rA,rB: b - a, *ca = no borrow */
u64 ppc_subfe(u64 a, u64 b, u32 ca_in, u32 *ca);       /* subfe rD,rA,rB: ~a + b + ca_in (subfze: b = 0; subfme: b = ~0) */

s32 ppc_fctiwz(f64 x);               /* fctiwz: toward zero, saturating (NaN and < -2^31 -> 0x80000000) */
s32 ppc_fctiw(f64 x);                /* fctiw: to nearest even, saturating */
s64 ppc_fctidz(f64 x);               /* fctidz: toward zero, saturating (NaN and < -2^63 -> 0x8000000000000000) */
s64 ppc_fctid(f64 x);                /* fctid: to nearest even, saturating */
u64 ppc_fctiw_fpr(s32 v);            /* the whole register fctiw/fctiwz leave (stfd of it): the oracle's model */
f64 ppc_fcfid(u64 bits);             /* fcfid: the doubleword (as s64) to the nearest double */
u32 ppc_single_bits(f64 x);          /* the word stfs stores from a register holding x */
f64 ppc_frsp(f64 x);                 /* frsp: (f64)(f32)x */
f64 ppc_fmadd(f64 a, f64 c, f64 b);  /* fmadd fD,fA,fC,fB: a * c + b (rounding: target.json "fma", DESIGN.md) */
f64 ppc_fmsub(f64 a, f64 c, f64 b);  /* fmsub: a * c - b */
f64 ppc_fnmadd(f64 a, f64 c, f64 b); /* fnmadd: -(a * c + b) */
f64 ppc_fnmsub(f64 a, f64 c, f64 b); /* fnmsub: -(a * c - b) */
f64 ppc_fmadds(f64 a, f64 c, f64 b); /* fmadds: a * c + b, rounded to single */
f64 ppc_fmsubs(f64 a, f64 c, f64 b); /* fmsubs */
f64 ppc_fnmadds(f64 a, f64 c, f64 b); /* fnmadds */
f64 ppc_fnmsubs(f64 a, f64 c, f64 b); /* fnmsubs */
f64 ppc_fsqrt(f64 x);                /* fsqrt */
f64 ppc_fsqrts(f64 x);               /* fsqrts: rounded to single */
f64 ppc_fsel(f64 a, f64 c, f64 b);   /* fsel fD,fA,fC,fB: a >= 0.0 ? c : b (NaN a gives b) */
f64 ppc_fabs(f64 x);                 /* fabs: clears the sign bit */
f64 ppc_fnabs(f64 x);                /* fnabs: sets the sign bit */
f64 ppc_fneg(f64 x);                 /* fneg: flips the sign bit */

#endif
