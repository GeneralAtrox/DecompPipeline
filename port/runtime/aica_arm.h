/* The AICA as a game's own sound driver sees it (runtime/aica_arm.c, aica_chan.c): the ARM7 (arm7.h) runs the driver
 * from the sound RAM, and the chip's registers drive the kit's voices (aica.h). Off unless the game's game.json sets
 * "aica_arm" (PORT_AICA_ARM) and its SH-4 side releases the ARM's reset (0x2c00).
 *   - Memory, as the ARM sees it: the 2 MB sound RAM at 0 (mirrored to 0x7fffff), the registers at 0x800000: the 64
 *     voices' 0x80 bytes each, the common registers at 0x2800 and the DSP's at 0x3000. Each register is 16 bits at a
 *     4-byte step; a byte write sets its half.
 *   - The voices (aica_chan.c): KYONEX keys every voice on or off as its KYONB says; a key on takes the voice's
 *     sample (SA, PCMS, LSA, LEA, LPCTL), pitch (OCT, FNS), levels (DISDL, DIPAN, TL) and envelope (AR, D1R, D2R,
 *     DL, RR, KRS); later writes of the pitch and levels change a playing voice. The monitor (MSLC at 0x280c) reads a
 *     voice's LP flag, envelope state and level (0x2810) and its position (0x2814).
 *   - Timers A, B and C (0x2890..0x2898): an 8-bit count up by one every 2^prescale samples, the interrupt at its
 *     overflow. The interrupt controller: SCIEB, SCIPD, SCIRE and the levels SCILV0..2, the lowest pending enabled
 *     interrupt's level held in L (0x2d00) and driving the ARM's FIQ until the driver writes M's bit 0 (0x2d04).
 *     Pending bits: 5 the SH-4's (a write of bit 5 to SCIPD), 6..8 the timers, 10 each sample.
 *   - Time: the ARM runs 512 cycles a sample (its 22.5792 MHz over 44,100 Hz), sample by sample with the timers, in
 *     the vertical blank's mix (aica.c's slice hook) up to each slice's frames. While the SH-4 polls a sound-RAM word
 *     (aica_arm_poll) it runs ahead of the mix, up to one blank's frames, then the poll waits for the next blank.
 *     So the same input gives the same sound, run after run.
 *   - The effects DSP is not run: a game whose driver uses it audibly needs it first (audio_check.py reports the
 *     DSP's share of the emulator's output). */
#ifndef AICA_ARM_H
#define AICA_ARM_H

#include "aica.h"

#define AICA_REG_SIZE 0x8000u
#define AICA_CHAN_SIZE 0x80u
#define AICA_ARM_RESET 0x2c00u /* bit 0: the ARM held in reset */

/* The SH-4's side: the registers at 0x00700000 (offset 0..0x7fff), 16 bits each (a 32-bit access moves the low 16). */
u32 aica_arm_sh4_read(u32 offset);
void aica_arm_sh4_write(u32 offset, u32 value);
void aica_arm_poll(void); /* the SH-4 polls a word the driver sets: the driver's time passes */
int aica_arm_running(void);

/* aica_chan.c, for aica_arm.c */
extern u8 g_aica_regs[AICA_REG_SIZE];
static inline u32 aica_reg16(u32 offset) { return (u32)g_aica_regs[offset] | (u32)g_aica_regs[offset + 1u] << 8; }
void aica_chan_write(u32 ch, u32 reg); /* the voice register at reg (0..0x7c) was written */
u32 aica_chan_monitor(u32 ch, u32 reg); /* 0x2810 or 0x2814 for the monitored voice */
float aica_chan_env(u32 v);             /* aica.h's envelope hook */
void aica_chan_reset(void);

#endif
