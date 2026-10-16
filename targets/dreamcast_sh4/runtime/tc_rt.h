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
 * invalidate loop) is no RAM write: write it with wr8/wr16/wr32 like any store; it is logged and compared.
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

/* SH-4 instruction helpers, bit-exact with the reference model. */
s32 sh4_ftrc(f32 x);                                  /* ftrc FRm,FPUL (float -> int conversion) */
f32 sh4_fsrra(f32 x);                                 /* fsrra: 1.0f / sqrt(x) */
f32 sh4_fsqrt_rm(f32 x);                              /* fsqrt: sqrt(x), rounded as FPSCR.RM says (the game's mode) */
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
/* r8..r14 as runtime state (index 8..14), the same way: hand-written display-list code keeps a pointer in r14.
 * When context.md lists them, read their entry values with sh4_r, set them with sh4_set_r where the original
 * leaves them changed (their exit values are compared), and set them before a call to a callee that reads them. */
u32 sh4_r(int index);                                 /* read r8..r14 */
void sh4_set_r(int index, u32 value);                 /* write r8..r14 */

#endif
