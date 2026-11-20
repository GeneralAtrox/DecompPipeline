/* The SCSP's timers against the sound driver's handler (sat_scsp.c, included whole with SAT_SOUND_CPU). A stand-in 68000
 * plays a sequencer's tick: on timer A's interrupt it writes the reload value back and clears the interrupt, as Dragon
 * Force's driver does. One second of sound (60 frames of 735 samples on the fixed clock) must then hold as many ticks as
 * the chip gives, 44100 / (256 - reload) << prescale, within one. The kit ran the 68000 a step of 16 samples ahead and
 * raised the interrupt only after the step, so each tick lost the rest of its step and the music ran slow.
 * Run by tests/test_sat_scsp_timer.py. */
#define _CRT_SECURE_NO_WARNINGS
#include <stdio.h>
#include "sat_scsp.c"

M68k m68k;
int g_work_on;
u64 g_work_now[WC_N];
static int g_fail, g_ipl;
static u32 g_ticks, g_reload, g_shift;

/* a stand-in for each kit function sat_scsp.c calls */
void port_log(const char *fmt, ...) { (void)fmt; }
void sat_map(u32 lo, u32 hi, const char *name, SatRead rd, SatWrite wr) { (void)lo, (void)hi, (void)name, (void)rd, (void)wr; }
u32 sat_vblank_count(void) { return 0; }
u32 sat_sys_hz(void) { return 60; }
long long sat_sys_now(void) { return 0; }
int sat_sys_fast(void) { return 1; }
int sat_sys_virtual(void) { return 1; }
void sat_perf_audio(long long ticks) { (void)ticks; }
void sat_scu_raise(u32 bit) { (void)bit; }
void sat_busy_poll(void) {}
void sat_audio_push(const s16 *stereo, u32 frames) { (void)stereo, (void)frames; }
void sat_mix_slot_written(u32 reg) { (void)reg; }
void sat_mix_keyex(void) {}
void sat_mix_sample(s32 *left, s32 *right) { *left = *right = 0; }
void sat_mix_reset(void) {}
u32 sat_mix_active(void) { return 0; }
u32 sat_mix_call_address(u32 slot) { (void)slot; return 0; }
void m68k_reset(void) {}
void m68k_set_ipl(int level) { g_ipl = level, m68k.ipl = (u8)level; }

/* the driver: the handler runs as soon as the interrupt is up, at the start of the clocks it is given */
u32 m68k_run(u32 cycles)
{
    if (g_ipl) {
        g_ticks++;
        sat_snd_wr(0x100418, 2, g_shift << 8 | g_reload); /* TACTL, TIMA: the next tick */
        sat_snd_wr(0x100422, 2, 0x40);                     /* SCIRE: timer A's interrupt taken */
    }
    return cycles;
}

static void run(u32 reload, u32 shift)
{
    u32 want = 44100u / ((256u - reload) << shift);
    g_ticks = 0, g_reload = reload, g_shift = shift;
    sat_scsp_cpu(1);
    sat_snd_wr(0x100424, 2, 0x40); /* SCILV0: timer A on level 1 */
    sat_snd_wr(0x10041e, 2, 0x40); /* SCIEB: timer A's interrupt on */
    sat_snd_wr(0x100418, 2, shift << 8 | reload);
    for (int f = 0; f < 60; f++) sat_scsp_frame();
    printf("%s timer A reload %u prescale %u: %u ticks in a second, the chip gives %u\n", g_ticks + 1u >= want && g_ticks <= want + 1u ? "ok  " : "FAIL",
           reload, 1u << shift, g_ticks, want);
    g_fail += !(g_ticks + 1u >= want && g_ticks <= want + 1u);
    sat_snd_wr(0x10041e, 2, 0);
    sat_snd_wr(0x100422, 2, 0x7ff);
    sat_scsp_cpu(0);
}

int main(void)
{
    run(175, 0); /* 81 samples a tick */
    run(0xd0, 1); /* 96 */
    run(0xf8, 0); /* 8: shorter than a step */
    run(0x80, 2); /* 512 */
    puts(g_fail ? "FAILED" : "all ok");
    return g_fail != 0;
}
