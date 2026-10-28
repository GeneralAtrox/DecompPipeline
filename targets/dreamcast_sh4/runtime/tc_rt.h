/* DecompPipeline Dreamcast SH-4 runtime: the ONLY interface a decompiled function may use.
 *
 * All guest pointers are u32 guest addresses (Dreamcast main RAM 0x8c010000..0x8cffffff),
 * accessed through the rd and wr functions below. Memory is little endian, as on the SH-4.
 * Every float operation must be single precision (f32), one operation at a time,
 * in the same order as the original instructions.
 */
#ifndef TC_RT_H
#define TC_RT_H

#include <stdint.h>

typedef uint8_t u8;
typedef uint16_t u16;
typedef uint32_t u32;
typedef int8_t s8;
typedef int16_t s16;
typedef int32_t s32;
typedef uint64_t u64; /* only for a function context.md says returns a 64-bit value (in r0:r1) */
typedef float f32;

/* Guest memory. A store to the P4 operand-cache address array (0xf4000000..0xf4ffffff, e.g. a cache
 * invalidate loop) or to QACR0/1 (0xff000038/0xff00003c, the store queues' area) is no RAM write: write it with
 * wr8/wr16/wr32 like any store; it is logged and compared.
 * A load from it is a fault, in the original's test run too (it returns cache state, not the bytes stored). */
u8 rd8(u32 addr);
u16 rd16(u32 addr);
u32 rd32(u32 addr);
f32 rdf32(u32 addr);
void wr8(u32 addr, u8 value);
void wr16(u32 addr, u16 value);
void wr32(u32 addr, u32 value);
void wrf32(u32 addr, f32 value);

/* Calls to other original functions (including calls through function pointers).
 * Fill r[0..3] = r4..r7 and fr[0..7] = fr4..fr11 exactly as the original sets them
 * before the jsr/bsr/jmp, and s[0..7] = the words at @(0,r15)..@(28,r15) at the call: the callee's
 * stack arguments (its 5th+ integer and 9th+ float parameters, in order; "s0" is @(0,r15)).
 * rlo[0..3] = r0..r3 at the call, only for a callee that context.md says takes them (SHC runtime helpers).
 * A call through a function pointer (a target context.md does not list) is compared on the argument registers the
 * original set since its entry or its last call, in order up to the first it did not set (r4..r7; fr4..fr11 by
 * pairs): fill those as it leaves them, its own parameters included.
 * Every call the original makes must be made, in the same order, with the same target.
 * A tail call (jmp/bra to another function) is `return tc_call(...)`. */
typedef struct {
    u32 r[4];
    f32 fr[8];
    u32 s[8];
    u32 rlo[4];
} TcArgs;

u32 tc_call(u32 target, const TcArgs *args);  /* returns the callee's r0 */
f32 tc_callf(u32 target, const TcArgs *args); /* returns the callee's fr0 */

/* The original's endless loop (`bra .`, e.g. an error halt): call tc_halt() where the original reaches it, after the
 * tc_call when it is a callee that never returns. It does not return; the test run reports HALT there. */
#if defined(_MSC_VER)
__declspec(noreturn)
#endif
void tc_halt(void);

/* The original's wait loop that only an interrupt or the hardware ends (TcSpin: on that input the loop repeats with
 * nothing changing, e.g. a flag a handler sets): keep the loop and its condition, and call tc_spin() as its body:
 * `while (flag == 0 && cnt == start) tc_spin();`, never tc_spin() alone and never anywhere else. A loop that calls
 * functions calls it last in its body: `while (vbl < end) { tc_call(poll, &a); tc_spin(); }` (TcSpin proves such a
 * loop never ends and halts after the first pass, its calls compared). The test run reports HALT SPIN there; the port waits for the next vertical blank and the loop tests its condition again. */
void tc_spin(void);

/* SH-4 instruction helpers, bit-exact with the reference model. */
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
u32 sh4_sz(void);                                     /* FPSCR.SZ: 1 while fmov moves 64-bit pairs (by_mode) */

/* FPSCR, for a function whose original reads or writes it (meta fpscr_state, TcFpscr: Spawn 8c010080, Toy Commander
 * 8c0611d8): sh4_sts_fpscr() where the original has sts FPSCR (or sts.l), sh4_lds_fpscr(v) where it has lds ...,FPSCR
 * (or lds.l; the SH-4 keeps bits 0..21). The test run gives each vector its entry FPSCR and compares FPSCR at exit and
 * at every call, PR, SZ and FR included (an lds sets them, as fpchg, fschg and frchg do). The FPU keeps rounding and
 * flushing as at entry: the test run never has a float operation after a change of RM, DN or the enables. */
u32 sh4_sts_fpscr(void);                              /* sts FPSCR,Rn */
void sh4_lds_fpscr(u32 value);                        /* lds Rm,FPSCR */
void sh4_ftrv_fv(int n);                              /* ftrv XMTRX,FVn: fr n..n+3 = XMTRX * fr n..n+3 (n = 0,4,8,12) */
void sh4_fipr_fv(int m, int n);                       /* fipr FVm,FVn: fr n+3 = sh4_fipr(fr m..m+3, fr n..n+3) */
/* r8..r14 as runtime state (index 8..14), the same way: hand-written display-list code keeps a pointer in r14.
 * When context.md lists them, read their entry values with sh4_r, set them with sh4_set_r where the original
 * leaves them changed (their exit values are compared), and set them before a call to a callee that reads them. */
u32 sh4_r(int index);                                 /* read r8..r14 */
void sh4_set_r(int index, u32 value);                 /* write r8..r14 */
/* SR, for the original's `stc sr,Rn` and `ldc Rm,sr` (context.md lists them: masking interrupts around an update).
 * The test run starts each vector with MD=1 and the vector's interrupt mask (SR bits 4..7), and compares the mask
 * at exit: read SR where the original does with sh4_stc_sr, write it where it does with sh4_ldc_sr. */
u32 sh4_stc_sr(void);                                 /* stc sr,Rn */
void sh4_ldc_sr(u32 value);                           /* ldc Rm,sr */

/* Hardware the project lists for this function (context.md says which; project.json "hw_ops"): a load from a listed
 * hardware register is tc_hw_read32(register), which returns the target's fixed value (a retail Dreamcast's). GBR,
 * where listed, is state: 0 at entry and compared at exit; write it where the original does with sh4_ldc_gbr, read
 * it with sh4_stc_gbr. */
u32 tc_hw_read32(u32 reg);                            /* mov.l @Rn,Rm from a listed hardware register */
u32 sh4_stc_gbr(void);                                /* stc gbr,Rn */
void sh4_ldc_gbr(u32 value);                          /* ldc Rm,gbr */
/* A store to a listed hardware register is tc_hw_write32(register, value) (mov.l) or tc_hw_write16 (mov.w): the test
 * run compares it as a memory write. Listed cache block ops are sh4_ocbi / sh4_ocbp / sh4_ocbwb(address), where the
 * original has them: they do nothing in the test run (memory is coherent there). */
void tc_hw_write32(u32 reg, u32 value);               /* mov.l Rm,@Rn to a listed hardware register */
void tc_hw_write16(u32 reg, u16 value);               /* mov.w Rm,@Rn to a listed hardware register */
void sh4_ocbi(u32 addr);                              /* ocbi @Rn */
void sh4_ocbp(u32 addr);                              /* ocbp @Rn */
void sh4_ocbwb(u32 addr);                             /* ocbwb @Rn */

#endif
