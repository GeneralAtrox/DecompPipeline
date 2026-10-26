/* DecompPipeline GameCube PowerPC (Gekko) runtime: the ONLY interface a decompiled function may use.
 *
 * All guest pointers are u32 guest addresses (MEM1 0x80000000..0x817fffff; the uncached view 0xc0000000.. is the
 * same bytes), accessed through the rd and wr functions below. Memory is BIG endian, as on the GameCube: rd32
 * returns the word the PowerPC loads, whatever the host byte order.
 *
 * Floating point: a PowerPC float register (f0..f31) always holds a double. Use f64 for float registers and
 * f32 only for what the original rounds to single: a single-precision instruction's result (fadds, fmuls,
 * frsp, ...) is (f64)(f32)(double operation), and lfs / stfs move singles to and from memory.
 */
#ifndef TC_RT_H
#define TC_RT_H

#include <stdint.h>

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

/* Guest memory, big endian. */
u8 rd8(u32 addr);
u16 rd16(u32 addr);
u32 rd32(u32 addr);
u64 rd64(u32 addr);
f32 rdf32(u32 addr);                 /* lfs reads this (then (f64) of it, exactly) */
f64 rdf64(u32 addr);                 /* lfd */
void wr8(u32 addr, u8 value);
void wr16(u32 addr, u16 value);
void wr32(u32 addr, u32 value);
void wr64(u32 addr, u64 value);
void wrf32(u32 addr, f32 value);     /* stfs of a register holding a single (the usual case) */
void wrf32d(u32 addr, f64 value);    /* stfs of a register holding any double: the 750CL's store conversion */
void wrf64(u32 addr, f64 value);     /* stfd */

/* Calls to other original functions (including calls through function pointers: bctrl, blrl).
 * Fill r[0..7] = r3..r10 and f[0..7] = f1..f8 exactly as the original sets them before the bl, and s[0..7] =
 * the words at r1+8, r1+12, ... r1+36 at the call (stack arguments). A call through a function pointer (a target
 * context.md does not list) is compared on the argument registers the original set since its entry or its last call
 * (after a call: its results r3 and f1), in order up to the first it did not set (r3..r10; f1..f8): fill those as
 * it leaves them, its own parameters included. Every call the original makes must be made,
 * in the same order, with the same target. A tail call (b to another function) is `return tc_call(...)`.
 * tc_call returns the callee's r3, tc_callf its f1. (The test harness's mocked callee sets both.) Where the original
 * reads both after one call, take the other from tc_lastr() (r3) or tc_lastf() (f1): the last call's result, no
 * new call. */
typedef struct {
    u32 r[8];
    f64 f[8];
    u32 s[8];
} TcArgs;

u32 tc_call(u32 target, const TcArgs *args);
f64 tc_callf(u32 target, const TcArgs *args);
u32 tc_lastr(void);  /* the last call's r3 (0 before any call) */
f64 tc_lastf(void);  /* the last call's f1 (0 before any call) */

/* PowerPC instruction helpers, bit-exact with the reference model (the oracle). */
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
s32 ppc_divw(s32 a, s32 b);          /* divw (0x80000000 / -1 = 0x80000000; never called with b = 0 in a test) */
u32 ppc_divwu(u32 a, u32 b);         /* divwu */
u32 ppc_mulhw(s32 a, s32 b);         /* mulhw: the high word of the 64-bit product */
u32 ppc_mulhwu(u32 a, u32 b);        /* mulhwu */

#endif
