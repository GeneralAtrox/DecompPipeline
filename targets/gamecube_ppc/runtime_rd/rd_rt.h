/* DecompPipeline GameCube PowerPC (Gekko) readable-C runtime: what readable game code may use besides its own headers.
 *
 * Readable code uses real pointers, structs and named globals. In the verification build MEM1 is mapped at its
 * GameCube address (0x80000000, and again at its uncached view 0xc0000000), so a guest pointer is a host pointer,
 * and every call to another original function is caught and logged. Guest memory is big endian: every multi-byte
 * value there has a be_ type and is read with be_get and written with be_set (rd_be.h).
 *
 * Floating point: a PowerPC float register holds a double. f64 is that register; f32 is only what the original
 * rounds to single (a single-precision instruction's result is (f64)(f32)(double operation), lfs and stfs move
 * singles). The helpers below are the PowerPC instructions with no exact portable C equivalent; the port replaces
 * them.
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

/* stfs of a register holding any double (the 750CL's store conversion, not a rounding): for a value a single
 * holds, the same as be_set with (f32)value. */
void be_set_stfs(be_f32 *p, f64 value);

s32 ppc_fctiwz(f64 x);               /* fctiwz: toward zero, saturating (NaN and < -2^31 -> 0x80000000) */
s32 ppc_fctiw(f64 x);                /* fctiw: to nearest even, saturating */
u32 ppc_single_bits(f64 x);          /* the word stfs stores from a register holding x */
f64 ppc_frsp(f64 x);                 /* frsp: (f64)(f32)x */
f64 ppc_fmadds(f64 a, f64 c, f64 b); /* fmadds fD,fA,fC,fB: (f64)(f32)((a * c) + b), each step in double */
f64 ppc_fmsubs(f64 a, f64 c, f64 b); /* fmsubs: (f64)(f32)((a * c) - b) */
f64 ppc_fnmadds(f64 a, f64 c, f64 b); /* fnmadds: -(f64)(f32)((a * c) + b) */
f64 ppc_fnmsubs(f64 a, f64 c, f64 b); /* fnmsubs: -(f64)(f32)((a * c) - b) */
f64 ppc_fsel(f64 a, f64 c, f64 b);   /* fsel fD,fA,fC,fB: a >= 0.0 ? c : b (NaN a gives b) */
f64 ppc_fabs(f64 x);                 /* fabs: clears the sign bit */
f64 ppc_fnabs(f64 x);                /* fnabs: sets the sign bit */
u32 ppc_cntlzw(u32 x);               /* cntlzw: leading zeros, 32 for 0 */
u32 ppc_rlwinm(u32 s, int sh, int mb, int me);         /* rlwinm rA,rS,SH,MB,ME */
u32 ppc_rlwimi(u32 a, u32 s, int sh, int mb, int me);  /* rlwimi rA,rS,SH,MB,ME (a: rA before) */
s32 ppc_divw(s32 a, s32 b);          /* divw (0x80000000 / -1 = 0x80000000) */
u32 ppc_divwu(u32 a, u32 b);         /* divwu */
u32 ppc_mulhw(s32 a, s32 b);         /* mulhw: the high word of the 64-bit product */
u32 ppc_mulhwu(u32 a, u32 b);        /* mulhwu */

#endif
