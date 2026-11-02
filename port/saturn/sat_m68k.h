/* The Motorola 68000 that runs the Saturn's sound driver (sat_m68k*.c). The sound RAM and the SCSP's registers are its
 * bus (sat_scsp.c provides sat_snd_rd / sat_snd_wr). Cycle counts are the bus accesses (4 clocks a word) plus the
 * multiply, divide and shift extras: close enough to place the driver's timer interrupts, not a cycle-exact model. */
#ifndef SAT_M68K_H
#define SAT_M68K_H
#include "sat.h"

typedef struct {
    u32 d[8], a[8], pc, other_sp, cyc;
    u16 sr;
    u8 ipl, stopped;
} M68k;

extern M68k m68k;

void m68k_reset(void);          /* SSP and PC from the first two long words of the bus */
u32 m68k_run(u32 cycles);       /* runs at least `cycles` clocks (the last instruction may overrun); returns the clocks used */
void m68k_set_ipl(int level);   /* the interrupt priority lines, 0..7 */

/* the bus, in sat_scsp.c: size 1, 2 or 4 bytes, big-endian */
u32 sat_snd_rd(u32 addr, int size);
void sat_snd_wr(u32 addr, int size, u32 value);

#endif
