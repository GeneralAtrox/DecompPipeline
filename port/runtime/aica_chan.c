/* The AICA's voice registers (aica_arm.h) driving the kit's voices (aica.c). The envelope is the chip's AEG: an
 * attenuation of 0..0x3ff (0.09375 dB a step, 96 dB in all) that falls from 0x3ff to 0 in the attack, an exponential
 * approach, then rises linearly in decay 1 up to DL * 32, in decay 2 up to 0x3ff, and in the release after a key off
 * to 0x3ff, where the voice ends. A rate R (0..31) is 2R, raised by the key scale KRS (0..14) and the octave; its
 * time for the whole range halves every 4 steps, as the chip's rate tables give it: a decay or release 118.2 s at 2, an
 * attack 8.1 s at 2 (instant from 62); rates 0 and 1 hold. The output's attenuation is the envelope plus TL (0.375 dB
 * a step), both off with VOFF; DISDL and DIPAN are aica.c's levels. */
#include <math.h>
#include <string.h>
#include "aica_arm.h"

#define EG_MAX 1023.0f
#define EG_ATTACK 0u
#define EG_DECAY1 1u
#define EG_DECAY2 2u
#define EG_RELEASE 3u
#define GAIN_STEPS 2048u /* TL * 4 + the envelope, in 0.09375 dB steps */

typedef struct {
    u32 state;
    float att;       /* the envelope's attenuation, 0..EG_MAX */
    float attack, d1, d2, rr; /* per output frame: the attack's factor, the decays' and release's rise */
    float dl;
    u32 tl;          /* TL * 4, or 0 with VOFF */
    int voff;
} Eg;

u8 g_aica_regs[AICA_REG_SIZE];
static Eg g_eg[AICA_VOICES];
static float g_gain[GAIN_STEPS];

static u32 field(u32 ch, u32 reg, u32 hi, u32 lo)
{
    return aica_reg16(ch * AICA_CHAN_SIZE + reg) >> lo & ((1u << (hi - lo + 1u)) - 1u);
}

/* The chip's rate for R: 2R, plus the key scale unless KRS is 15. */
static u32 eff_rate(u32 ch, u32 r)
{
    s32 rate = (s32)r * 2, krs = (s32)field(ch, 0x14, 13, 10), oct = (s32)field(ch, 0x18, 14, 11);
    if (krs < 15) {
        s32 scale = (krs + (oct >= 8 ? oct - 16 : oct)) * 2;
        rate += (s32)field(ch, 0x18, 9, 9) + (scale > 0 ? scale : 0);
    }
    return rate > 63 ? 63u : (u32)rate;
}

static float rise(u32 rate) /* a decay's or the release's rise per output frame */
{
    if (rate < 2u) return 0.0f;
    double ms = 118200.0 * exp2(-((double)rate - 2.0) / 4.0);
    return (float)(1024.0 / (ms * AICA_RATE / 1000.0));
}

static float attack_factor(u32 rate) /* the attack's fall per output frame: att -= (att + 1) * factor */
{
    if (rate < 2u) return 0.0f;
    if (rate >= 62u) return 1.0f;
    double frames = 8100.0 * exp2(-((double)rate - 2.0) / 4.0) * AICA_RATE / 1000.0;
    return (float)(1.0 - exp2(-10.0 / (frames < 1.0 ? 1.0 : frames)));
}

static void eg_rates(u32 ch)
{
    Eg *e = &g_eg[ch];
    e->attack = attack_factor(eff_rate(ch, field(ch, 0x10, 4, 0)));
    e->d1 = rise(eff_rate(ch, field(ch, 0x10, 10, 6)));
    e->d2 = rise(eff_rate(ch, field(ch, 0x10, 15, 11)));
    e->rr = rise(eff_rate(ch, field(ch, 0x14, 4, 0)));
    e->dl = (float)(field(ch, 0x14, 9, 5) * 32u);
    e->voff = (int)field(ch, 0x28, 6, 6);
    e->tl = e->voff ? 0u : field(ch, 0x28, 15, 8) * 4u;
}

/* The voice's step (16.16): (1024 + FNS) / 1024 times 2 to the signed octave. */
static u32 step_of(u32 ch)
{
    u32 oct = field(ch, 0x18, 14, 11), base = (1024u + field(ch, 0x18, 9, 0)) << 6;
    return oct & 8u ? base >> (16u - oct) : base << oct;
}

static void key_on(u32 ch)
{
    static const u32 format[4] = {AICA_PCM16, AICA_PCM8, AICA_ADPCM, AICA_ADPCM_STREAM};
    u32 pcms = field(ch, 0x00, 8, 7), sa = field(ch, 0x00, 6, 0) << 16 | aica_reg16(ch * AICA_CHAN_SIZE + 4u);
    u32 lsa = aica_reg16(ch * AICA_CHAN_SIZE + 8u), lea = aica_reg16(ch * AICA_CHAN_SIZE + 12u);
    Eg *e = &g_eg[ch];
    if (pcms == 0) sa &= ~1u;
    aica_voice_sample(ch, sa, AICA_MAX_FRAMES, format[pcms]);
    aica_voice_step(ch, step_of(ch));
    aica_voice_level(ch, field(ch, 0x24, 11, 8), field(ch, 0x24, 4, 0));
    eg_rates(ch);
    e->state = EG_ATTACK;
    e->att = e->attack >= 1.0f ? 0.0f : EG_MAX;
    if (e->att == 0.0f) e->state = EG_DECAY1;
    aica_voice_envelope(ch, 1);
    aica_voice_play(ch, 0, (int)field(ch, 0x00, 9, 9), lsa, lea ? lea : 1u);
}

static void key_off(u32 ch)
{
    if (g_eg[ch].state == EG_RELEASE) return;
    g_eg[ch].state = EG_RELEASE;
    if (aica_voice_playing(ch)) aica_key_log_off(ch);
}

void aica_chan_write(u32 ch, u32 reg)
{
    u32 base = ch * AICA_CHAN_SIZE;
    if (reg == 0x00 && (g_aica_regs[base + 1u] & 0x80u)) { /* KYONEX: every voice as its KYONB says */
        g_aica_regs[base + 1u] &= 0x7fu;
        for (u32 v = 0; v < AICA_VOICES; v++) {
            int kyonb = (g_aica_regs[v * AICA_CHAN_SIZE + 1u] & 0x40u) != 0, off = g_eg[v].state == EG_RELEASE;
            if (kyonb && (off || !aica_voice_playing(v))) key_on(v); /* a voice in its release, or ended */
            else if (!kyonb && !off) key_off(v);
        }
        return;
    }
    if (!aica_voice_playing(ch)) return;
    if (reg == 0x18) {
        aica_voice_step(ch, step_of(ch));
        eg_rates(ch);
    } else if (reg == 0x24) {
        aica_voice_level(ch, field(ch, 0x24, 11, 8), field(ch, 0x24, 4, 0));
    } else if (reg == 0x10 || reg == 0x14 || reg == 0x28) {
        eg_rates(ch);
    }
}

/* 0x2810: LP (bit 15, cleared by the read), the envelope's state (14..13) and level (12..0, 0x1fff at the most);
 * 0x2814: the position. */
u32 aica_chan_monitor(u32 ch, u32 reg)
{
    if (reg == 0x2814) return aica_voice_position(ch, 0) & 0xffffu;
    const Eg *e = &g_eg[ch];
    u32 level = aica_voice_playing(ch) ? (u32)e->att << 3 : 0x1fffu;
    u32 lp = aica_voice_looped(ch) || !aica_voice_playing(ch);
    return lp << 15 | e->state << 13 | (level > 0x1fffu ? 0x1fffu : level);
}

float aica_chan_env(u32 v)
{
    Eg *e = &g_eg[v];
    switch (e->state) {
    case EG_ATTACK:
        e->att -= (e->att + 1.0f) * e->attack;
        if (e->att <= 0.0f) {
            e->att = 0.0f;
            e->state = EG_DECAY1;
        }
        break;
    case EG_DECAY1:
        e->att += e->d1;
        if (e->att >= e->dl) e->state = EG_DECAY2;
        break;
    case EG_DECAY2:
        e->att += e->d2;
        if (e->att > EG_MAX) e->att = EG_MAX;
        break;
    default:
        e->att += e->rr;
        if (e->att >= EG_MAX) return -1.0f;
        break;
    }
    u32 i = e->tl + (e->voff ? 0u : (u32)e->att);
    return i < GAIN_STEPS ? g_gain[i] : 0.0f;
}

void aica_chan_reset(void)
{
    for (u32 i = 0; i < GAIN_STEPS; i++) g_gain[i] = (float)pow(10.0, -0.09375 * (double)i / 20.0);
    memset(g_eg, 0, sizeof g_eg);
    for (u32 v = 0; v < AICA_VOICES; v++) {
        g_eg[v].state = EG_RELEASE;
        g_eg[v].att = EG_MAX;
    }
    aica_envelope_hook(aica_chan_env);
}
