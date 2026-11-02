/* The AICA's ARM7DI, the sound chip's own processor (runtime/arm7.c, arm7_ls.c), written from the ARM Architecture
 * Reference Manual: ARM state only (the ARM7DI has no Thumb), the ARMv4 instruction set without coprocessors, the
 * six processor modes with their banked registers, and the FIQ the AICA's interrupt controller drives (aica_arm.c).
 * Its memory is the bus the caller gives: rd/wr by size, and the sound RAM itself for instruction fetches and the
 * fast path (addresses below ram_size, masked by ram_mask). An instruction costs one cycle plus its memory and
 * pipeline cycles (ARM7DI timings: N and S cycles counted as one each): a load 3, a store 2, a block transfer n + 2
 * (n + 4 with the PC loaded), a multiply up to 5 and a taken branch 3. */
#ifndef ARM7_H
#define ARM7_H

#include "platform.h"

#define ARM_MODE_USR 0x10u
#define ARM_MODE_FIQ 0x11u
#define ARM_MODE_IRQ 0x12u
#define ARM_MODE_SVC 0x13u
#define ARM_MODE_ABT 0x17u
#define ARM_MODE_UND 0x1bu
#define ARM_MODE_SYS 0x1fu
#define ARM_PSR_N 0x80000000u
#define ARM_PSR_Z 0x40000000u
#define ARM_PSR_C 0x20000000u
#define ARM_PSR_V 0x10000000u
#define ARM_PSR_I 0x80u
#define ARM_PSR_F 0x40u

typedef struct Arm7 Arm7;
typedef struct Arm7Native Arm7Native;
typedef u32 (*Arm7Read)(Arm7 *a, u32 addr, u32 size);      /* size 1, 2 or 4; addr aligned to it */
typedef void (*Arm7Write)(Arm7 *a, u32 addr, u32 value, u32 size);

struct Arm7 {
    u32 r[16];         /* the current mode's registers; r[15] reads as the instruction's address + 8 */
    u32 n, z, c, v;    /* the condition flags, 0 or 1 */
    u32 psr;           /* the CPSR's control bits: I, F and the mode (the flags are n..v) */
    u32 spsr;          /* the current mode's SPSR (none in user and system mode) */
    u32 usr_hi[5];     /* r8..r12 of every mode but FIQ, while in FIQ */
    u32 fiq_hi[5];     /* FIQ's r8..r12, while in another mode */
    u32 bank[6][3];    /* r13, r14 and the SPSR of user/system, FIQ, IRQ, SVC, abort and undefined */
    u32 next;          /* the next instruction's address */
    int fiq;           /* the FIQ input, active */
    s64 cycles;        /* the budget left: arm7_run runs while it is above 0 */
    u64 executed;      /* instructions executed since the reset */
    u64 fiqs;          /* FIQs taken since the reset */
    u64 bus;           /* accesses beyond the sound RAM since the reset */
    u64 scanned;       /* scan iterations run by the fast path (arm7_scan.c) */
    int fault;         /* test only, shows the comparison catches it: 1 skips a scan with one cycle too many, 2 leaves a register wrong, 3 and 4 do the same in every 4096th native block */
    int fast;          /* the scan fast paths are on */
    const Arm7Native *native; /* the driver translated to C (arm7_native.h), or NULL: the interpreter runs everything */
    u8 *pcs;           /* recorder: one byte per word of the RAM, set when an instruction there is executed (NULL: off) */
    u64 native_blocks, native_instr; /* blocks run natively, and the instructions in them */
    u8 scan_map[256];  /* loop heads found, by address */
    u32 scan_seen[1024]; /* branch targets already looked at */
    u8 *ram;
    u32 ram_mask;      /* the sound RAM's mirror mask */
    u32 ram_size;      /* addresses below this are the sound RAM */
    Arm7Read rd;
    Arm7Write wr;
    void *user;
    u32 undefined;     /* undefined instructions taken (logged once) */
};

void arm7_reset(Arm7 *a); /* SVC mode, IRQ and FIQ masked, from address 0 */
void arm7_step_op(Arm7 *a, u32 op, u32 pc); /* what arm7_run does with the word op fetched at pc, from its fetch on */
void arm7_run(Arm7 *a, s64 cycles); /* adds cycles to the budget and runs until it is spent */
u32 arm7_cpsr(const Arm7 *a);
void arm7_set_cpsr(Arm7 *a, u32 value); /* banks the registers when the mode changes */

/* The scan fast paths (arm7_scan.c). */
void arm7_scan_reset(Arm7 *a);
void arm7_scan_probe(Arm7 *a, u32 target); /* a backward branch goes to target: is it a scan loop's head? */
void arm7_scan_run(Arm7 *a);               /* at a loop's head: skip the iterations that stay in the loop */

/* arm7.c's helpers that arm7_ls.c's transfers use. */
u32 arm7_read(Arm7 *a, u32 addr, u32 size);
void arm7_write(Arm7 *a, u32 addr, u32 value, u32 size);
void arm7_exception(Arm7 *a, u32 mode, u32 vector, u32 ret);
u32 arm7_user_reg(Arm7 *a, u32 n);
void arm7_set_user_reg(Arm7 *a, u32 n, u32 value);
u32 arm7_shift_imm(Arm7 *a, u32 op, u32 *carry); /* a register shifted by an immediate amount (bits 11..4) */
void arm7_single(Arm7 *a, u32 op);    /* LDR, STR, LDRB, STRB */
void arm7_half(Arm7 *a, u32 op);      /* LDRH, STRH, LDRSB, LDRSH */
void arm7_block(Arm7 *a, u32 op);     /* LDM, STM */
void arm7_swap(Arm7 *a, u32 op);      /* SWP, SWPB */

#include "arm7_native.h"

#endif
