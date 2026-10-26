/* Shared by the GameCube readable-C verification runtime's own files. Readable code never includes this. */
#ifndef RD_INTERNAL_H
#define RD_INTERNAL_H

#include "tc_rt_internal.h"

#define RD_MAX_CALLS 4096 /* TcOracle.CALL_LIMIT */
#define RD_RETS 16        /* scripted results per vector (lines C and D); a later call returns 0 */
#define RD_PAGE 0x1000u
#define RD_UNCACHED_BASE 0xc0000000u /* the uncached view of MEM1: the same section mapped a second time */

/* Generated per build (protos.c): prototypes of known original functions, for logging their arguments.
 * params: one letter per parameter: i = 32-bit int or pointer, c/C = s8/u8, h/H = s16/u16, f = f32, d = f64,
 * L = a 64-bit integer (8 cdecl bytes; a GPR pair, high word first). A '*' after them marks a variadic function
 * (refused by the readable judge: its variable arguments' types are unknown here).
 * ret: 'f' f32 and 'd' f64 (x87 st(0)), 'L' 64-bit (edx:eax), anything else eax.
 * regs: "" (the PowerPC EABI convention: ints r3..r10, floats f1..f8, independently, a 64-bit int in an odd-even
 * register pair, then the stack words), or one character per parameter: '0'..'7' = r3..r10 (a 64-bit int: its high
 * word there, the low word in the next), 'a'..'h' = f1..f8 (an int there too: its bits as a double's), 'S'..'Z' =
 * the stack word s0..s7 (r1+8+4N at the call), 'x' = a stack word past s7 (not in the call record), '.' = by the
 * convention, after any register named before it. */
typedef struct {
    u32 addr;
    const char *params;
    char ret;
    const char *regs;
} RdProto;
extern const RdProto rd_protos[];
extern const int rd_nprotos;

/* rd_mem.c */
int rd_is_code(u32 addr);
int rd_page_guarded(u32 addr);
void rd_page_open(u32 addr);
void rd_page_close(u32 addr);
void rd_arm(void);

/* rd_veh.c */
int rd_veh_install(void);
void rd_calls_reset(void);
void rd_calls_report(FILE *out);
u32 rd_code_read(void);
void rd_set_self(u32 host, u32 guest); /* the function under test (the readable thunk names it) */
u32 rd_self_guest(u32 word); /* the guest address for the host address of the function under test (rd_set_self) */

#endif
