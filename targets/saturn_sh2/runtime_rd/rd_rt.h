/* DecompPipeline Sega Saturn SH-2 readable-C runtime: what readable game code may use besides its own headers.
 *
 * Readable code uses real pointers, structs and named globals. In the verification build the high work RAM is mapped
 * at its Saturn address (0x06000000, and again at its cache-through view 0x26000000), so a guest pointer is a host
 * pointer, and every call to another original function is caught and logged. Guest memory is big endian: every
 * multi-byte value there has a be_ type and is read with be_get and written with be_set (rd_be.h).
 *
 * The SH-2 has no FPU: an f32 is only bits the game keeps (or a software float routine's argument, passed where an
 * int goes), never a float instruction's result. The helpers below are the SH-2 instructions with no portable C
 * equivalent; the port replaces them.
 */
#ifndef RD_RT_H
#define RD_RT_H

#include <stdarg.h> /* va_list, va_start, va_arg, va_end: a variadic function's own readable C */
#include <stdint.h>
#include "rd_be.h"

typedef uint8_t u8;
typedef uint16_t u16;
typedef uint32_t u32;
typedef int8_t s8;
typedef int16_t s16;
typedef int32_t s32;
typedef float f32;
typedef uint64_t u64; /* a 64-bit result in r0:r1 (the SHC runtime's 64-bit multiply helpers) */
typedef int64_t s64;

u32 sh4_r(int index);                                 /* read r8..r14 as runtime state (hand-written code keeps a */
void sh4_set_r(int index, u32 value);                 /* pointer in r14 across calls); write it */
u32 sh4_stc_sr(void);                                 /* stc sr,Rn: SR (the interrupt mask is compared at exit, */
void sh4_ldc_sr(u32 value);                           /* as tc_rt.h); ldc Rm,sr */
u32 tc_hw_read32(u32 reg);                            /* a listed hardware register's load (tc_rt.h): the target's */
u32 sh4_stc_gbr(void);                                /* value; GBR where listed: stc gbr,Rn (compared at exit, */
void sh4_ldc_gbr(u32 value);                          /* as tc_rt.h); ldc Rm,gbr */
void tc_hw_write32(u32 reg, u32 value);               /* a listed hardware register's store (tc_rt.h): compared */
void tc_hw_write16(u32 reg, u16 value);               /* as a memory write; mov.w */

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
