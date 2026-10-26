/* DecompPipeline Sega Saturn SH-2 runtime: the ONLY interface a decompiled function may use.
 *
 * All guest pointers are u32 guest addresses (Saturn work RAM high 0x06000000..0x060fffff and low
 * 0x00200000..0x002fffff, also seen through the cache-through view 0x26..., 0x202...), accessed through the rd and
 * wr functions below. Memory is BIG endian, as on the SH-2: rd16/rd32 return the value the CPU loads and wr16/wr32
 * store it as the CPU does, so never assemble a word from bytes yourself. The SH-2 has no FPU: a float in memory is
 * only bits (rdf32/wrf32 move them), and no instruction does float arithmetic.
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

/* Guest memory: work RAM only. Any other address (VDP1, VDP2, SCU, SMPC, SCSP, the CD block, the SH-2's own
 * registers) is a fault, in the original's test run too, unless context.md lists it as hardware (tc_hw_* below). */
u8 rd8(u32 addr);
u16 rd16(u32 addr);
u32 rd32(u32 addr);
f32 rdf32(u32 addr);
void wr8(u32 addr, u8 value);
void wr16(u32 addr, u16 value);
void wr32(u32 addr, u32 value);
void wrf32(u32 addr, f32 value);

/* Calls to other original functions (including calls through function pointers and the engine's pointer tables).
 * Fill r[0..3] = r4..r7 exactly as the original sets them before the jsr/bsr/jmp (fr[] stays 0: no FPU), and
 * s[0..7] = the words at @(0,r15)..@(28,r15) at the call: the callee's stack arguments (its 5th+ parameters, in
 * order; "s0" is @(0,r15)).
 * rlo[0..3] = r0..r3 at the call, only for a callee that context.md says takes them (SHC runtime helpers).
 * A call through a function pointer (a target context.md does not list) is compared on the argument registers the
 * original set since its entry or its last call, r4..r7 in order up to the first it did not set: fill those as it
 * leaves them, its own parameters included.
 * Every call the original makes must be made, in the same order, with the same target.
 * A tail call (jmp/bra to another function) is `return tc_call(...)`. */
typedef struct {
    u32 r[4];
    f32 fr[8];
    u32 s[8];
    u32 rlo[4];
} TcArgs;

u32 tc_call(u32 target, const TcArgs *args);  /* returns the callee's r0 */

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

/* r8..r14 as runtime state (index 8..14): code that keeps a value in a register across functions.
 * When context.md lists them, read their entry values with sh4_r, set them with sh4_set_r where the original
 * leaves them changed (their exit values are compared), and set them before a call to a callee that reads them. */
u32 sh4_r(int index);                                 /* read r8..r14 */
void sh4_set_r(int index, u32 value);                 /* write r8..r14 */
/* SR, for the original's `stc sr,Rn` and `ldc Rm,sr` (context.md lists them: masking interrupts around an update).
 * The test run starts each vector with the vector's interrupt mask (SR bits 4..7, I3..I0), and compares the mask
 * at exit: read SR where the original does with sh4_stc_sr, write it where it does with sh4_ldc_sr. */
u32 sh4_stc_sr(void);                                 /* stc sr,Rn */
void sh4_ldc_sr(u32 value);                           /* ldc Rm,sr */

/* Hardware the project lists for this function (context.md says which; project.json "hw_ops"): a load from a listed
 * hardware register is tc_hw_read32(register), tc_hw_read16 or tc_hw_read8 by the load's size (context.md gives the
 * register's width), which returns the project's fixed value for it. GBR,
 * where listed, is state: 0 at entry and compared at exit; write it where the original does with sh4_ldc_gbr, read
 * it with sh4_stc_gbr. */
u32 tc_hw_read32(u32 reg);                            /* mov.l @Rn,Rm from a listed hardware register */
u16 tc_hw_read16(u32 reg);                            /* mov.w @Rn,Rm (before its sign extension) */
u8 tc_hw_read8(u32 reg);                              /* mov.b @Rn,Rm (before its sign extension): FTCSR 0xfffffe11 */
u32 sh4_stc_gbr(void);                                /* stc gbr,Rn */
void sh4_ldc_gbr(u32 value);                          /* ldc Rm,gbr */
/* A store to a listed hardware register is tc_hw_write32(register, value) (mov.l) or tc_hw_write16 (mov.w): the test
 * run compares it as a memory write. */
void tc_hw_write32(u32 reg, u32 value);               /* mov.l Rm,@Rn to a listed hardware register */
void tc_hw_write16(u32 reg, u16 value);               /* mov.w Rm,@Rn to a listed hardware register */
void tc_hw_write8(u32 reg, u8 value);                 /* mov.b Rm,@Rn to a listed hardware register */

#endif
