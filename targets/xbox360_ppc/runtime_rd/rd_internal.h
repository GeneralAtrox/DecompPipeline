/* Shared by the Xbox 360 readable-C verification runtime's own files. Readable code never includes this. */
#ifndef RD_INTERNAL_H
#define RD_INTERNAL_H

#include "tc_rt_internal.h"

#define RD_MAX_CALLS 4096 /* TcOracle.CALL_LIMIT */
#define RD_RETS 16        /* scripted results per vector (lines C and D); a later call returns 0 */
#define RD_PAGE 0x1000u

/* Generated per build (protos.c): prototypes of known original functions, for logging their arguments.
 * params: one letter per parameter: c/C = s8/u8, h/H = s16/u16, i/I = s32/u32, L = a 64-bit integer, P = a pointer
 * (the host's 64 bits; a guest pointer's upper word is 0), f = f32, d = f64. An integer reaches its register extended
 * to 64 bits as its type says (c, h, i sign-extended; C, H, I zero-extended), as the original leaves an int of that
 * type. A '*' after them marks a variadic function (refused by the readable judge).
 * ret: 'f' f32 and 'd' f64 (xmm0), '?' unknown (guessed at the call), anything else rax.
 * regs: "" (the Xbox 360 convention: ints r3..r10, floats f1..f13, counted apart, then the stack doublewords s0..s7),
 * or one character per parameter: '0'..'7' = r3..r10 (a float there: its bits as a double's), 'a'..'m' = f1..f13,
 * 'S'..'Z' = the stack doubleword s0..s7 (r1+0x50+8N at the call), 'x' = past s7 (not in the call record), '.' = by
 * the convention. The x64 host passes parameter k in rcx/rdx/r8/r9 or xmm0..xmm3 by position (k < 4), else at
 * [rsp+8+8k] at the call. */
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
int rd_page_guarded(u64 addr);
void rd_page_open(u64 addr);
void rd_page_close(u64 addr);
void rd_arm(void);
int rd_guest_copy(u32 p, u32 n, u8 *out, const u8 *in); /* the stricter judge's (rd_veh.c) */

/* rd_veh.c */
int rd_veh_install(void);
void rd_calls_reset(void);
void rd_calls_report(FILE *out);
u32 rd_code_read(void);
void rd_set_self(u64 host, u32 guest); /* the function under test (the readable thunk names it) */
u64 rd_self_guest(u64 word); /* the guest address for the host address of the function under test (rd_set_self) */

#endif
