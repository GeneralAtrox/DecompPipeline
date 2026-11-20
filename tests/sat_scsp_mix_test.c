/* The SCSP's direct send (sat_scsp_mix.c, included whole): one voice of a constant 16-bit sample, keyed at full level
 * (TL 0, AR 31, DISDL 7), mixed to left and right by DIPAN. Bit 4 of DIPAN picks the side turned down, as on the Dreamcast's
 * AICA (port/runtime/aica.c): 0 the left, 1 the right; bits 0..3 by 3 dB steps, 15 off.
 * Run by tests/test_sat_scsp_mix.py. */
#define _CRT_SECURE_NO_WARNINGS
#include <math.h>
#include <stdio.h>
#include "sat_scsp_mix.c"

u8 sat_scsp_regs[0x1000];
u8 sat_sound_ram_bytes[0x80000];
static int g_fail;

/* a stand-in for each kit function sat_scsp_mix.c calls */
void port_log(const char *fmt, ...) { (void)fmt; }
u32 sat_vblank_count(void) { return 0; }

static void w16(u32 o, u32 v) { sat_scsp_regs[o] = (u8)(v >> 8), sat_scsp_regs[o + 1] = (u8)v; }

/* slot 0 plays sound RAM 0 (a constant 0x4000) looped, at pitch 1, with this DIPAN; returns the mixed left and right */
static void mix(u32 dipan, s32 *l, s32 *r)
{
    sat_mix_reset();
    memset(sat_scsp_regs, 0, sizeof sat_scsp_regs);
    for (u32 i = 0; i < 0x200u; i += 2) sat_sound_ram_bytes[i] = 0x40, sat_sound_ram_bytes[i + 1] = 0x00;
    sat_scsp_regs[0x401] = 15; /* MVOL: full */
    w16(0x04, 0);              /* LSA */
    w16(0x06, 0xff);           /* LEA */
    w16(0x08, 31);             /* AR 31: full level at once */
    w16(0x0a, 0);              /* DL 0, RR 0 */
    w16(0x0c, 0);              /* TL 0 */
    w16(0x10, 0);              /* octave 0, FNS 0: one sample a sample */
    w16(0x16, 7u << 13 | dipan << 8);
    w16(0x00, 0x1000u | 0x0800u | 0x20u); /* KYONEX, KYONB, loop forward, 16-bit, SA 0 */
    for (u32 o = 0; o < 0x20u; o += 2) sat_mix_slot_written(o);
    sat_mix_keyex();
    sat_mix_sample(l, r);
    sat_mix_sample(l, r);
}

static void check(int ok, const char *what, s32 l, s32 r)
{
    printf("%s %s (left %d, right %d)\n", ok ? "ok  " : "FAIL", what, (int)l, (int)r);
    g_fail += !ok;
}

int main(void)
{
    s32 l, r;
    mix(0x00, &l, &r);
    check(l == 0x4000 && r == 0x4000, "DIPAN 0x00: centre, both sides full", l, r);
    mix(0x1f, &l, &r);
    check(l == 0x4000 && r == 0, "DIPAN 0x1f: the right turned off, the sound on the left", l, r);
    mix(0x0f, &l, &r);
    check(l == 0 && r == 0x4000, "DIPAN 0x0f: the left turned off, the sound on the right", l, r);
    mix(0x12, &l, &r);
    check(l == 0x4000 && r > 0x1f00 && r < 0x2100, "DIPAN 0x12: the right 6 dB down", l, r);
    mix(0x02, &l, &r);
    check(r == 0x4000 && l > 0x1f00 && l < 0x2100, "DIPAN 0x02: the left 6 dB down", l, r);
    puts(g_fail ? "FAILED" : "all ok");
    return g_fail != 0;
}
