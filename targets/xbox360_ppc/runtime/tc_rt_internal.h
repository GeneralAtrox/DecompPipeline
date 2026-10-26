/* Runner-only interface to the Xbox 360 runtime. Decompiled code must never include this file.
 *
 * The guest memory map (target.json "memory", the oracle's TcBase): four regions, everything else faults.
 *   image   0x82000000..  the loaded title (image.bin from tools/xex.py, imports resolved)
 *   heap    0x40000000..0x40100000  test pointers: scratch 0x40000000..0x40001000, generator heap above it
 *   stack   0x70000000..0x70100000  r1 = 0x700fe000 at entry; writes here are not compared
 *   kernel  0x7fff0000..0x80000000  r13's processor block (0x7fff0000) and the imported kernel variables
 */
#ifndef TC_RT_INTERNAL_H
#define TC_RT_INTERNAL_H

#include <stdio.h>
#include "tc_rt.h"

#define TCRT_IMAGE_BASE 0x82000000u
#define TCRT_IMAGE_MAX 0x0e000000u
#define TCRT_HEAP_BASE 0x40000000u
#define TCRT_HEAP_SIZE 0x00100000u
#define TCRT_STACK_BASE 0x70000000u
#define TCRT_STACK_SIZE 0x00100000u
#define TCRT_KERNEL_BASE 0x7fff0000u
#define TCRT_KERNEL_SIZE 0x00010000u
#define TCRT_SP 0x700fe000u
#define TCRT_CALL_FLOATS 13

int tcrt_init(const char *image, const char *code_ranges);
void tcrt_reset(void);
void tcrt_poke(u32 addr, const u8 *data, u32 len);
void tcrt_set_call_returns(const u64 rets[16], const u64 frets[16]);
void tcrt_report(FILE *out, u64 r3, u64 f1_bits);

#endif
