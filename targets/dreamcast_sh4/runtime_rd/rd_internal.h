/* Shared by the readable-C verification runtime's own files. Readable code never includes this. */
#ifndef RD_INTERNAL_H
#define RD_INTERNAL_H

#include "tc_rt_internal.h"

#define RD_MAX_CALLS 4096 /* TcOracle.CALL_LIMIT */
/* The call record of tc_rt.c (TcOracle.callRecord): target, r4..r7, fr4..fr11, s0..s7, fr12..fr15, r0..r3, r8..r14
 * (36 fields), and with line Q fr0..fr3, xf0..xf15 and the FPSCR mode word (57 fields) */
#define RD_LOG_FIELDS 57
#define RD_PAGE 0x1000u
/* The same RAM is mapped a second time here, so pointers the game stored in their 0x0c... (P0) form work too. */
#define RD_MIRROR_BASE (TCRT_RAM_BASE & 0x1fffffffu)

/* Generated per build (thunk.c): prototypes of known original functions, for logging their arguments.
 * params: one letter per parameter: i = 32-bit int or pointer, c/C = s8/u8, h/H = s16/u16, f = f32.
 * ret: 'f' for f32, 'L' for a 64-bit result (edx:eax), anything else is returned in eax.
 * regs: "" (every parameter where the SH-4 C convention puts it), or one character per parameter: '0'..'7' = that
 * parameter is in r0..r7 (a callee outside the convention, such as an SHC runtime helper taking r0..r2, or a float
 * passed in r7), 'a'..'h' = in fr4..fr11 (an int there too: its bits), 's' = the next stack word, 'S'..'Z' = the
 * stack word s0..s7 (@(0..28,r15) at the call), 'x' = a stack word past s7 (not in the call record), '.' = where
 * the convention puts it, after any register named before it.
 * A '*' after the parameter letters marks a variadic function: the caller's remaining cdecl words are the stack
 * words after the named ones (SHC passes the last named parameter and the variable ones on the stack; the readable
 * stage always gives regs for such a function). */
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
void rd_calls_reset(const u32 rets[16]);
void rd_calls_report(FILE *out);
void rd_abi_reset(void);        /* r0..r3 at entry (line L) and the extra result word, cleared per vector */
void rd_report_retx(FILE *out); /* line RETX: the extra result word, when the thunk set one (tcrt_set_retx); line
                                  VAANCHOR: a variadic function's first variable word (rd_set_va_anchor) */
u32 rd_code_read(void);
void rd_set_self(u32 host, u32 guest); /* the function under test (the readable thunk names it) */
u32 rd_self_guest(u32 word); /* the guest address for the host address of the function under test (rd_set_self) */
/* Out-pointers into the caller's stack (vectors lines O/B "O call target field guest size" and D "D call target
 * field hex", cleared per vector; rd_veh.c out_copy). mask[k] 0: byte k is not copied. rd_out_log: where the raw run
 * prints the original's bytes (OB). */
void rd_out_add(u32 call, u32 target, u32 field, u32 guest, u32 size);
void rd_out_data(u32 call, u32 target, u32 field, const u8 *bytes, const u8 *mask, u32 size);
void rd_out_log(FILE *out);
int rd_out_paint(void); /* an O line is set: paint the host stack before the run */
u8 rd_canary(uintptr_t addr); /* the paint byte at a host stack address (rd_runner.c paints before such a run) */

/* rd_trace.c: line T "T call target field guest span" (raw C only): the original's reads of an out-buffer after the
 * call, of bytes it did not write itself; reported as "OT call target field end" (rd_trace_report). */
extern int rd_tracing;
void rd_trace_add(u32 call, u32 target, u32 field, u32 guest, u32 span);
void rd_trace_call(u32 call, u32 target, const u32 *rec);
void rd_trace_read(u32 a, u32 n);
void rd_trace_write(u32 a, u32 n);
void rd_trace_report(FILE *out);
void rd_trace_reset(void);

/* rd_p4.c (stores to the P4 operand-cache address array; the log itself is runtime/tc_p4.c) */
void rd_p4_init(void);
int rd_p4_fault(u32 addr, int is_write, void *context); /* CONTEXT *: 1 = a store there, single-stepped */
int rd_p4_step(void);                                 /* 1: a store was pending and is now logged */
void rd_p4_reset(void);

/* rd_sh4.c */
int rd_sh4_init(const char *fsca_table);
void rd_sh4_reset(void);
void rd_sh4_report(FILE *out);
void rd_sh4_log(u32 *rec, const u32 fr_args[8]); /* call record fields 5..12, 21..24, 29..35 and, with line Q, 36..56 */
int rd_sh4_fpu_state(void);             /* line Q was given: the whole FPU is state */
void rd_sh4_result(u32 bits); /* a trapped call's result, in fr0 as in r0 */
void rd_sh4_set_fq(const u32 bits[16]); /* fr0..fr15 at entry (line Q) */
void rd_sh4_report_fq(FILE *out);       /* line QH: fr0..fr15 and the FPSCR mode word at exit */

#endif
