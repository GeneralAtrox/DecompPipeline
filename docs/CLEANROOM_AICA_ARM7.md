# Clean-room note: the AICA's ARM7 and voices (port/runtime/arm7*.c, aica_arm.c, aica_chan.c)

The code in these files was written for the kit from scratch. Nothing in it is copied, translated or adapted from
another project's code: there is no Flycast or other emulator code, and no structure, names or tables were taken over.

## What was used

- **The ARM7 core (arm7.c, arm7_ls.c, arm7.h):** the ARM Architecture Reference Manual. This covers ARMv4 in ARM state
  only (no Thumb): instruction encodings, the condition codes, the barrel shifter including RRX and register-shift
  amounts, the flags, the PSR banking, the exception vectors and return addresses, the LDM/STM rules, and rotated
  unaligned loads. The tests (tests/test_port_arm7.py) build their instructions from the manual's encoding formats.
- **The AICA's behaviour (aica_arm.c, aica_chan.c):** facts about the chip, read in Flycast's source as a reference only
  (core/hw/aica: aica.cpp, sgc_if.cpp; core/hw/arm7: arm7.cpp, arm_mem.cpp). Only these facts were taken:
  - the register map: the voice fields and the common registers 0x2800..0x28bc, 0x2c00, 0x2d00 and 0x2d04;
  - the interrupt bits: 5 for the SH-4, 6..8 for timers A..C, 10 for each sample;
  - L takes the level of the lowest pending enabled interrupt and holds until the ARM writes M's bit 0;
  - the timers count up once every 2^prescale samples and interrupt when they overflow;
  - the ARM gets 512 cycles a sample (22.5792 MHz);
  - the envelope's effective rate: 2R, plus the key scale and octave when KRS is below 15, at most 63;
  - the envelope's timing: a decay or release takes 118.2 s at rate 2 and an attack 8.1 s at rate 2, the time halving
    every 4 rates, and the attack is instant from rate 62;
  - the attenuation steps: 0.375 dB per TL step and 0.09375 dB per envelope step;
  - the monitor registers' layout.
- **The game's side (each game's hw.c):** the game's own SH-4 disassembly (Capstone), written as C. No Flycast code.
- **Measurements:** Flycast (FlycastRedux, local build) ran as the reference for comparison only, through the kit's
  port/tools/audio_check.py: each key on with the voice's registers, the output sample by sample, and the DSP's share.
