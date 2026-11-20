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

#ifdef TC_PORT
/* The port kit (targets/xbox360_ppc/port): guest memory is one host reserve at tc_base and a guest address is an offset
 * into it, so an access is one host load or store and one byte swap, in place. The judge build leaves TC_PORT unset and
 * links the declarations below. */
#include <intrin.h>
extern u8 *tc_base;
u32 ppc_single_bits(f64 x);
u64 tc_timebase(void);               /* mftb: the host clock at the console's 49.875 MHz */
static __forceinline u8 rd8(u32 a) { return tc_base[a]; }
static __forceinline u16 rd16(u32 a) { return _byteswap_ushort(*(const u16 *)(tc_base + a)); }
static __forceinline u32 rd32(u32 a) { return _byteswap_ulong(*(const u32 *)(tc_base + a)); }
static __forceinline u64 rd64(u32 a) { return _byteswap_uint64(*(const u64 *)(tc_base + a)); }
static __forceinline f32 rdf32(u32 a) { union { u32 i; f32 f; } v; v.i = rd32(a); return v.f; }
static __forceinline f64 rdf64(u32 a) { union { u64 i; f64 f; } v; v.i = rd64(a); return v.f; }
static __forceinline void wr8(u32 a, u8 v) { tc_base[a] = v; }
static __forceinline void wr16(u32 a, u16 v) { *(u16 *)(tc_base + a) = _byteswap_ushort(v); }
static __forceinline void wr32(u32 a, u32 v) { *(u32 *)(tc_base + a) = _byteswap_ulong(v); }
static __forceinline void wr64(u32 a, u64 v) { *(u64 *)(tc_base + a) = _byteswap_uint64(v); }
static __forceinline void wrf32(u32 a, f32 v) { union { u32 i; f32 f; } x; x.f = v; wr32(a, x.i); }
static __forceinline void wrf32d(u32 a, f64 v) { wr32(a, ppc_single_bits(v)); }
static __forceinline void wrf64(u32 a, f64 v) { union { u64 i; f64 f; } x; x.f = v; wr64(a, x.i); }
static __forceinline u16 rd16r(u32 a) { return *(const u16 *)(tc_base + a); }
static __forceinline u32 rd32r(u32 a) { return *(const u32 *)(tc_base + a); }
static __forceinline void wr16r(u32 a, u16 v) { *(u16 *)(tc_base + a) = v; }
static __forceinline void wr32r(u32 a, u32 v) { *(u32 *)(tc_base + a) = v; }
/* lwarx / ldarx and stwcx. / stdcx.: guest threads run at once on host threads, so the conditional store is a compare and
 * swap against the value the reservation read (a store in between by another thread makes it fail, as on the console).
 * tc_resv_s: 2 = a reservation is held, 1 = the last conditional store was done, 0 = it was not. A conditional store
 * always ends the reservation (Power ISA Book II 4.4.2), so a second one with no load-and-reserve between fails.
 * Not modelled: another thread writing the same value back in between (the compare then still succeeds), and the
 * reservation being lost at an interrupt. */
extern __declspec(thread) u64 tc_resv_v;
extern __declspec(thread) u32 tc_resv_s;
static __forceinline void tc_lwarx(u32 a, u32 z) { (void)z; tc_resv_v = rd32(a); tc_resv_s = 2; }
static __forceinline void tc_ldarx(u32 a, u32 z) { (void)z; tc_resv_v = rd64(a); tc_resv_s = 2; }
static __forceinline u64 tc_resv(u32 a) { (void)a; return tc_resv_v; }
static __forceinline u32 tc_resv_ok(u32 a) { (void)a; return tc_resv_s == 1; }
static __forceinline void tc_stwcx(u32 a, u32 v)
{
    long e = (long)_byteswap_ulong((u32)tc_resv_v);
    tc_resv_s = tc_resv_s == 2 && _InterlockedCompareExchange((volatile long *)(tc_base + a), (long)_byteswap_ulong(v), e) == e;
}
static __forceinline void tc_stdcx(u32 a, u64 v)
{
    __int64 e = (__int64)_byteswap_uint64(tc_resv_v);
    tc_resv_s = tc_resv_s == 2 && _InterlockedCompareExchange64((volatile __int64 *)(tc_base + a), (__int64)_byteswap_uint64(v), e) == e;
}
/* sync, lwsync, eieio (Power ISA Book II 4.4.3, 4.4.1). The host is x64, whose own ordering already keeps load-load,
 * load-store and store-store order; only a later load passing an earlier store is allowed (Intel SDM vol. 3, 8.2.2).
 * sync forbids that too, so it is a locked instruction. lwsync does not need one on this host, but the compiler must
 * also not move a guest load or store across it, and a locked instruction is the one thing it is sure to respect: it
 * is stronger than lwsync asks, never weaker. eieio orders stores only: a store fence. */
static __forceinline void tc_sync(u32 a, u32 z) { (void)a; (void)z; __faststorefence(); }
static __forceinline void tc_lwsync(u32 a, u32 z) { (void)a; (void)z; __faststorefence(); }
static __forceinline void tc_eieio(u32 a, u32 z) { (void)a; (void)z; _mm_sfence(); }
#else
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

#endif

/* Calls to other original functions (including calls through function pointers, bctrl, and calls to kernel/XAM
 * imports through their thunks). Fill r[0..7] = r3..r10 (all 64 bits) and f[0..12] = f1..f13 exactly as the
 * original sets them before the bl, and s[0..7] = the doublewords at r1+0x50, r1+0x58, ... at the call (stack
 * arguments). A call through a function pointer (a target context.md does not list) is compared on the argument
 * registers the original set since its entry or its last call (after a call: its results r3 and f1), in order up to
 * the first it did not set (r3..r10; f1..f13): fill those as it leaves them, its own parameters included. Every call
 * the original makes must be made, in the same order, with the same target. A tail call (b to another function) is
 * `return tc_call(...)`. tc_call returns the callee's r3, tc_callf its f1 (the test harness's mocked callee sets
 * both). Where the original reads both after one call, take the other from tc_lastr() (r3) or tc_lastf() (f1): the
 * last call's result, no new call. After the call the other volatile registers (r0, r4..r12, f0, f2..f13, ctr)
 * are 0, except those context.md says the callee keeps, which hold what they held before it. */
typedef struct {
    u64 r[8];
    f64 f[13];
    u64 s[8];
    u64 nv[18];  /* the caller's r14..r31, filled only for a call to a funclet (port form)
     */
    u32 r12[1];  /* the caller's r12 (port form): a funclet receives its parent frame in it
    */
    u32 sp[1];   /* the caller's r1: the port kit's translation form (Facts.port) starts a callee on it; the judge leaves it 0 */
    u32 ip[1];   /* the call's own guest address (port form, a function with C++ EH tables, eh.json): the runtime's frame walk; else 0 */
} TcArgs;

u64 tc_call(u32 target, const TcArgs *args);
f64 tc_callf(u32 target, const TcArgs *args);
u64 tc_lastr(void);  /* the last call's r3 (0 before any call) */
f64 tc_lastf(void);  /* the last call's f1 (0 before any call) */

/* The judge's exit record (tc_exit.c), used only by the translator's exit-state form (xenontranslate/exit_form.py), never
 * by judged C: before each return it records the final value of every register the function writes; the runner (run
 * with "exit") prints them with the entry value of every other one. tcx_in_*: a register's value at entry (r3..r10 and
 * f1..f13 the vector's, r14..r31, f14..f31 and CR2..CR4 the vector's seeds, r1 the stack pointer, else 0). */
u64 tcx_in_r(u32 n);
f64 tcx_in_f(u32 n);
u32 tcx_in_cr(u32 bit);              /* CR bit 0..31 (CR0.LT is bit 0) */
void tcx_r(u32 n, u64 value);
void tcx_f(u32 n, f64 value);
void tcx_cr(u32 bit, u32 value);
void tcx_xer(u32 k, u32 value);      /* 0 CA, 1 SO, 2 OV */
void tcx_ctr(u64 value);
void tcx_lr(u64 value);
void tcx_fpscr(u32 value);
void tcx_v(u32 n, const u32 *w);     /* vN's four words, element 0 first */

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
