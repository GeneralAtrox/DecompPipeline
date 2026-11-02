/* The SCSP's 32 voices: PCM from sound RAM (16 or 8 bit, looped or one-shot) at the pitch the slot registers give, shaped by
 * the envelope generator (attack, two decays, release) and the total level, sent to the left and right outputs by DISDL and
 * DIPAN. Not modelled: the DSP and its effect sends (EFSDL), the LFOs, FM between slots, noise and the key-rate scaling of the
 * envelope rates. Attack is linear in attenuation here (the chip's curve is steeper at first). Rates and times follow the
 * SCSP's published tables; the output is checked by the WAV a run writes (`--wav`), not by ear alone.
 *
 * Nearest existing: runtime/aica_chan.c (the Dreamcast AICA's voices, a different register layout and the kit's voice model). */
#include <math.h>
#include <string.h>
#include "sat_scsp.h"
#include "sat_sys.h"

enum { NSLOTS = 32, ATTACK = 0, DECAY1, DECAY2, RELEASE };

typedef struct {
    u32 sa, lsa, lea, inc;
    int pcm8, lpctl, ssctl, tl, dl, kyonb;
    float ar_step, d1_step, d2_step, rr_step, gl, gr;
    int keyed, on, state;
    u32 pos, frac;
    float att;
} Slot;

static Slot g_slot[NSLOTS];

static u32 r16(u32 o) { return (u32)sat_scsp_regs[o] << 8 | sat_scsp_regs[o + 1]; }

/* attenuation units (1024 = 96 dB) per output sample for a rate 0..31: the table's times halve every two rates; 0 is no change */
static float rate_step(int rate, float full_ms)
{
    float ms;
    if (rate < 1) return 0.0f;
    ms = full_ms * exp2f(-(float)(2 * rate - 2) / 4.0f);
    return 1024.0f / (ms * (SAT_SCSP_RATE / 1000.0f));
}

static void refresh(u32 n)
{
    Slot *s = &g_slot[n];
    u32 o = n * 0x20u, r0 = r16(o), r8 = r16(o + 8), ra = r16(o + 10), r10 = r16(o + 0x10), r16v = r16(o + 0x16);
    int oct = (int)(r10 >> 11) & 15, disdl = (int)(r16v >> 13) & 7, pan = (int)(r16v >> 8) & 15;
    float lvl = disdl ? exp2f(-(float)(7 - disdl)) : 0.0f, side = pan == 15 ? 0.0f : exp2f(-0.5f * (float)pan);
    s->kyonb = (int)(r0 >> 11) & 1;
    s->ssctl = (int)(r0 >> 7) & 3;
    s->lpctl = (int)(r0 >> 5) & 3;
    s->pcm8 = (int)(r0 >> 4) & 1;
    s->sa = (r0 & 15u) << 16 | r16(o + 2);
    s->lsa = r16(o + 4);
    s->lea = r16(o + 6);
    s->ar_step = rate_step((int)r8 & 31, 8100.0f);
    s->d1_step = rate_step((int)(r8 >> 6) & 31, 118200.0f);
    s->d2_step = rate_step((int)(r8 >> 11) & 31, 118200.0f);
    s->rr_step = rate_step((int)ra & 31, 118200.0f);
    s->dl = (int)(ra >> 5) & 31;
    s->tl = (int)r16(o + 0x0c) & 0xff;
    if (oct >= 8) oct -= 16;
    s->inc = (1024u + (r10 & 0x3ffu)) << 6;
    s->inc = oct >= 0 ? s->inc << oct : s->inc >> -oct;
    s->gl = (r16v >> 8) & 0x10u ? lvl * side : lvl;
    s->gr = (r16v >> 8) & 0x10u ? lvl : lvl * side;
}

void sat_mix_slot_written(u32 reg) { refresh((reg >> 5) & 31u); }

void sat_mix_keyex(void)
{
    for (u32 n = 0; n < NSLOTS; n++) {
        Slot *s = &g_slot[n];
        refresh(n);
        if (s->kyonb && !s->keyed) {
            s->keyed = s->on = 1;
            s->state = ATTACK;
            s->pos = s->frac = 0;
            s->att = 1023.0f;
            {
                static u32 logged;
                if (logged++ < 60)
                    port_log("scsp: vb %u slot %u key on: %s sa %05x loop %u %04x..%04x pitch x%.3f TL %u DL %u AR %u D1R %u D2R %u RR %u level %.3f/%.3f\n", sat_vblank_count(), n,
                             s->pcm8 ? "8-bit" : "16-bit", s->sa, (unsigned)s->lpctl, s->lsa, s->lea, (double)s->inc / 65536.0, (unsigned)s->tl, (unsigned)s->dl,
                             r16(n * 0x20u + 8) & 31u, r16(n * 0x20u + 8) >> 6 & 31u, r16(n * 0x20u + 8) >> 11, r16(n * 0x20u + 10) & 31u, (double)s->gl,
                             (double)s->gr);
            }
            if ((r16(n * 0x20u + 8) & 31u) == 31u) s->att = 0.0f, s->state = DECAY1;
        } else if (!s->kyonb && s->keyed) {
            s->keyed = 0;
            if (s->on) s->state = RELEASE;
        }
    }
}

u32 sat_mix_call_address(u32 slot) { return (g_slot[slot & 31u].pos >> 12) & 15u; }

static s32 fetch(const Slot *s, u32 i)
{
    if (s->ssctl) return 0;
    if (s->pcm8) return (s32)(s8)sat_sound_ram_bytes[(s->sa + i) & 0x7ffffu] * 256;
    {
        u32 a = (s->sa + i * 2u) & 0x7fffeu;
        return (s16)((u32)sat_sound_ram_bytes[a] << 8 | sat_sound_ram_bytes[a + 1]);
    }
}

static void envelope(Slot *s)
{
    switch (s->state) {
    case ATTACK:
        s->att -= s->ar_step;
        if (s->att <= 0.0f) s->att = 0.0f, s->state = DECAY1;
        break;
    case DECAY1:
        s->att += s->d1_step;
        if (s->att >= (float)(s->dl * 32)) {
            s->att = (float)(s->dl * 32);
            s->state = DECAY2;
        }
        break;
    case DECAY2:
        s->att += s->d2_step;
        if (s->att > 1023.0f) s->att = 1023.0f;
        break;
    default:
        s->att += s->rr_step > 0.0f ? s->rr_step : 1.0f; /* a release rate of 0 would never end: the chip's slowest still does */
        if (s->att >= 1023.0f) s->on = 0;
        break;
    }
}

void sat_mix_sample(s32 *left, s32 *right)
{
    float l = 0.0f, r = 0.0f;
    for (int n = 0; n < NSLOTS; n++) {
        Slot *s = &g_slot[n];
        u32 nx;
        float v, total, gain;
        s32 a, b;
        if (!s->on) continue;
        a = fetch(s, s->pos);
        nx = s->pos + 1u;
        if (s->lpctl && s->lea > s->lsa && nx >= s->lea) nx = s->lsa + (nx - s->lea);
        b = fetch(s, nx);
        v = (float)a + (float)(b - a) * ((float)s->frac / 65536.0f);
        total = s->att + (float)s->tl * 4.0f;
        gain = total >= 1023.0f ? 0.0f : exp2f(-total / 64.0f);
        l += v * gain * s->gl;
        r += v * gain * s->gr;
        envelope(s);
        s->frac += s->inc;
        s->pos += s->frac >> 16;
        s->frac &= 0xffffu;
        if (s->pos >= s->lea) {
            if (s->lpctl && s->lea > s->lsa) s->pos = s->lsa + (s->pos - s->lea) % (s->lea - s->lsa);
            else s->on = 0;
        }
    }
    {
        u32 mv = sat_scsp_regs[0x401] & 15u;
        float m = mv ? exp2f(-0.5f * (float)(15 - (int)mv)) : 0.0f;
        *left = (s32)(l * m);
        *right = (s32)(r * m);
    }
}

u32 sat_mix_active(void)
{
    u32 n = 0;
    for (int i = 0; i < NSLOTS; i++) n += (u32)g_slot[i].on;
    return n;
}

void sat_mix_reset(void) { memset(g_slot, 0, sizeof g_slot); }
