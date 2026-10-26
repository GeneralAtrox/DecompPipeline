/* DecompPipeline Xbox 360 (Xenon PowerPC) runtime: the ONLY interface a decompiled function may use.
 *
 * Registers are 64-bit: a general register is a u64, and so is every integer parameter, argument and result the
 * runtime passes (TcArgs.r, tc_call). A function whose original only uses the low word may declare u32 or s32
 * parameters (the low word is passed); the result r3 is compared in all 64 bits, so its declared type says how the
 * original leaves the upper word: u32 = zero-extended, s32 = sign-extended, u64/s64 = all 64 bits computed.
 *
 * Guest pointers are u32 effective addresses (Xbox 360 titles use the low 32 bits of a 64-bit address), accessed
 * through the rd and wr functions below. Memory is BIG endian: rd32 returns the word the PowerPC loads.
 *
 * Floating point: a float register (f0..f31) always holds a double. Use f64 for float registers and f32 only for
 * what the original rounds to single: a single-precision instruction's result (fadds, fmuls, frsp, ...) is
 * (f64)(f32)(double operation), and lfs / stfs move singles to and from memory.
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
u64 rd64(u32 addr);                  /* ld */
f32 rdf32(u32 addr);                 /* lfs reads this (then (f64) of it, exactly) */
f64 rdf64(u32 addr);                 /* lfd */
void wr8(u32 addr, u8 value);
void wr16(u32 addr, u16 value);
void wr32(u32 addr, u32 value);
void wr64(u32 addr, u64 value);      /* std */
void wrf32(u32 addr, f32 value);     /* stfs of a register holding a single (the usual case) */
void wrf32d(u32 addr, f64 value);    /* stfs of a register holding any double: the architecture's store conversion */
void wrf64(u32 addr, f64 value);     /* stfd */
u16 rd16r(u32 addr);                 /* lhbrx: little-endian halfword */
u32 rd32r(u32 addr);                 /* lwbrx */
void wr16r(u32 addr, u16 value);     /* sthbrx */
void wr32r(u32 addr, u32 value);     /* stwbrx */

/* Calls to other original functions (including calls through function pointers, bctrl, and calls to kernel/XAM
 * imports through their thunks). Fill r[0..7] = r3..r10 (all 64 bits) and f[0..12] = f1..f13 exactly as the
 * original sets them before the bl, and s[0..7] = the doublewords at r1+0x50, r1+0x58, ... at the call (stack
 * arguments). Every call the original makes must be made, in the same order, with the same target. A tail call
 * (b to another function) is `return tc_call(...)`. tc_call returns the callee's r3, tc_callf its f1 (the test
 * harness's mocked callee sets both). */
typedef struct {
    u64 r[8];
    f64 f[13];
    u64 s[8];
} TcArgs;

u64 tc_call(u32 target, const TcArgs *args);
f64 tc_callf(u32 target, const TcArgs *args);

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
