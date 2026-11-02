/* One SH-2's bookkeeping, shared by the call machinery (sat_call.c) and its reports (sat_where.c). */
#ifndef SAT_CPU_H
#define SAT_CPU_H
#include "sat.h"

enum { MASTER = 0, SLAVE = 1, MAXDEPTH = 400 };
typedef struct {
    u32 r[16], gbr, sr, sp, entry_sp, top, in_r[4]; /* in_r: r0..r3 at the current call (a callee that never writes r0 hands it back; SGL's inner routines read r1..r3 as inputs) */
    u32 srlog[8], srlvl[8], nsr; /* the last changes of the interrupt mask: the function and the new level */
    int depth;
    u32 stack[512];  /* the guest functions running now, outermost first */
    u32 last[64], nlast; /* the last calls made, newest at (nlast-1)&63 */
    u32 calls, hint;
    void *fiber;
    int alive;       /* the slave: a program is running on its fiber */
    int tail;        /* a tail jump (tc_tail) is waiting for the running function to return */
    u32 tail_target;
    u32 tail_sp;     /* r15 at a tail jump made from an open frame (tc_tail_sp), else 0 */
    TcArgs tail_args;
    u32 last_top; /* the entry sp of the call that returned last */
    unsigned char popped[MAXDEPTH + 2]; /* per frame: the function restored pr from its stack, so it returned to its caller's caller */
} SatCpu;

extern SatCpu g_cpu[2];
void sat_saved_note(u32 addr, const u32 keep[7]); /* sat_where.c: log a callee that handed back a changed r8..r14 (--watch-saved) */

#endif
