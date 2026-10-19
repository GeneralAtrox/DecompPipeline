/* xemu: an independent SH-4 interpreter, the second emulator that cross-checks the oracle (tools/xemu_check.py).
 *
 * The oracle's expected results come from Ghidra's p-code emulation (TcOracle). A Ghidra emulation bug would be
 * "truth" for every judge run, so this interpreter runs the same vectors on the original machine code a second
 * way, written from the SH-4 hardware manual rather than from Ghidra's SLEIGH. It links with the unchanged C
 * runtime in place of a decompiled function (it defines tc_thunk), so the vectors, the mocked callees (tc_call,
 * the K live deltas), the memory model, the store and call logs and the report are the runtime's own, and
 * core/compare.py compares its results with expected.txt like any judge run.
 *
 * Our own code, no emulator's source in it (DecompPipeline is published: no GPL code inside it). */
#ifndef XE_H
#define XE_H

#include "tc_rt.h"

#define XE_SENTINEL 0x8cfff000u /* the oracle's return address at entry (TcBase.SENTINEL) */
#define XE_MAX_BODY 512

typedef struct {
    u32 r[16];
    u32 t, s, q, m;           /* SR.T, SR.S, SR.Q, SR.M */
    u32 sr_rest;              /* the other SR bits, as ldc stored them */
    u32 gbr, vbr, ssr, spc, sgr, dbr, bank[8];
    u32 mach, macl, pr;
    u32 fr[16], xf[16];       /* the current bank and the back bank */
    u32 fpscr, fpul;
    u32 pc;                   /* the address of the instruction running */
    int slot;                 /* running a delay slot */
    int branch;               /* 0, 1 = delayed branch to target after the slot, 2 = branch now */
    u32 target;
} Xe;

/* Triage switches (environment variable XEMU_MODEL, a comma list): a disagreement that goes away under one has
 * that rule as its cause. "ftrc": the oracle's ftrc before r15 (Ghidra's p-code truncation); "ftrcnan":
 * the same for a NaN operand only (the hardware's result for a NaN is the least certain) */
extern int xe_model_ftrc;
/* "ghmem": guest memory as Ghidra's emulator gives it to the oracle (xe_ghmem.c) */
extern int xe_model_ghmem;
/* "sqwt": the oracle's store-queue model (TcSq) in place of the hardware's */
extern int xe_model_sqwt;
void xe_ghmem_begin(u32 image_base, u32 image_size);
void xe_ghmem_read(u32 a, int size);
void xe_ghmem_write(u32 a, int size);

/* fetch and run */
u16 xe_fetch(u32 pc);
void xe_fail(const char *why, u32 pc, u32 op); /* an instruction xemu does not run: the vector ends in a CRASH */
void xe_exec(Xe *x, u16 op);
void xe_exec_fpu(Xe *x, u16 op);
void xe_mac_l(Xe *x, u32 n, u32 m);
void xe_mac_w(Xe *x, u32 n, u32 m);
void xe_div1(Xe *x, u32 n, u32 m);

/* memory (the runtime's rd/wr, with the store queues of a line-S function) */
u32 xe_rd(u32 a, int size);
u32 xe_rd_text(u32 a, int size); /* a PC-relative literal */
void xe_wr(u32 a, u32 v, int size);
void xe_pref(u32 a);

/* FPSCR */
#define XE_FPSCR_FR 0x00200000u
#define XE_FPSCR_SZ 0x00100000u
#define XE_FPSCR_PR 0x00080000u
#define XE_FPSCR_DN 0x00040000u
void xe_set_fpscr(Xe *x, u32 v);
u32 xe_sr(const Xe *x);
void xe_set_sr(Xe *x, u32 v);

#endif
