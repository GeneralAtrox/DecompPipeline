/* The AICA's voices and CD input (aica.h), mixed in software. The sound RAM is host memory at its SH-4 address, so
 * the game's copies into it land where a voice reads them. ADPCM is the chip's Yamaha 4-bit form: the low nibble
 * first, each nibble a signed step of the running quantiser (from 127, clamped to 127..24576). */
#include <math.h>
#include <string.h>
#include "aica.h"

#define RAM_MASK (AICA_RAM_SIZE - 1u)
#define FRAC_ONE 0x10000u
#define CHUNK 1024u

typedef struct {
    u32 addr, frames, format;
    u32 step;             /* frames per output frame, 16.16 */
    u32 pos, frac;        /* the frame being played and the 16-bit fraction past it */
    u32 loop_start, end;
    int loop, playing;
    s32 cur, next;        /* the samples at pos and after it */
    u32 dpos;             /* ADPCM: the next frame to decode */
    s32 signal, quant;
    float gain_l, gain_r;
    u32 level, pan;
} Voice;

static Voice g_voice[AICA_VOICES];
static u8 *g_ram;
static int g_ram_tried;
static float g_cd_gain[4] = {1.0f, 0.0f, 0.0f, 1.0f}; /* CD left to L and R, CD right to L and R */
static u32 g_cd_level[2] = {15, 15}, g_cd_pans[2] = {0, 31};

u8 *aica_ram(void)
{
    if (!g_ram_tried) {
        g_ram_tried = 1;
        g_ram = (u8 *)host_map_fixed(AICA_RAM_BASE, AICA_RAM_SIZE);
        if (!g_ram) port_log("aica: the sound RAM at %08x cannot be mapped\n", AICA_RAM_BASE);
    }
    return g_ram;
}

/* steps of 3 dB down; 15 steps and more is off */
static float atten(u32 steps) { return steps >= 15u ? 0.0f : powf(10.0f, -0.15f * (float)steps); }

static void pan_gains(u32 level, u32 pan, float *l, float *r)
{
    float g = level ? atten(15u - level) : 0.0f;
    *l = pan < 16u ? g * atten(15u - pan) : g;
    *r = pan < 16u ? g : g * atten(pan - 16u);
}

static s32 pcm(const Voice *v, u32 i)
{
    if (v->format == AICA_PCM16) {
        u32 a = v->addr + 2u * i;
        return (s16)(g_ram[a & RAM_MASK] | g_ram[(a + 1u) & RAM_MASK] << 8);
    }
    return (s32)(s8)g_ram[(v->addr + i) & RAM_MASK] * 256;
}

static s32 adpcm_step(Voice *v)
{
    static const s32 diff[8] = {1, 3, 5, 7, 9, 11, 13, 15};
    static const s32 scale[8] = {0xe6, 0xe6, 0xe6, 0xe6, 0x133, 0x199, 0x200, 0x266};
    u8 b = g_ram[(v->addr + v->dpos / 2u) & RAM_MASK];
    u32 n = v->dpos & 1u ? (u32)b >> 4 : b & 15u;
    s32 d = v->quant * diff[n & 7u] / 8;
    v->signal += n & 8u ? -d : d;
    v->signal = v->signal < -32768 ? -32768 : v->signal > 32767 ? 32767 : v->signal;
    v->quant = v->quant * scale[n & 7u] >> 8;
    v->quant = v->quant < 0x7f ? 0x7f : v->quant > 0x6000 ? 0x6000 : v->quant;
    v->dpos++;
    return v->signal;
}

/* The sample at frame i. ADPCM decodes in order: a jump restarts the decoder from frame 0, except a ring's (the
 * stream form's) wrap to its loop start, where the decoder runs on. */
static s32 fetch(Voice *v, u32 i, int wrapped)
{
    if (v->format < AICA_ADPCM) return pcm(v, i);
    if (i != v->dpos) {
        if (wrapped && v->format == AICA_ADPCM_STREAM) {
            v->dpos = i;
        } else {
            v->dpos = 0;
            v->signal = 0;
            v->quant = 0x7f;
            while (v->dpos < i) adpcm_step(v);
        }
    }
    return adpcm_step(v);
}

static s32 following(Voice *v)
{
    if (v->pos + 1u < v->end) return fetch(v, v->pos + 1u, 0);
    return v->loop ? fetch(v, v->loop_start, 1) : 0;
}

void aica_voice_sample(u32 v, u32 addr, u32 frames, u32 format)
{
    if (v >= AICA_VOICES) return;
    Voice *p = &g_voice[v];
    p->playing = 0;
    p->addr = addr & RAM_MASK;
    p->frames = frames < AICA_MAX_FRAMES ? frames : AICA_MAX_FRAMES;
    p->format = format & 3u;
    p->pos = p->frac = 0;
}

/* The rate as the chip holds it: an octave (-8..7) and a 10-bit fraction of 44,100 Hz. */
u32 aica_rate_cents(u32 hz, s32 cents) { return (u32)((double)hz * exp2((double)cents / 1200.0)); }

void aica_voice_rate(u32 v, u32 hz)
{
    if (v >= AICA_VOICES) return;
    double r = (double)hz / AICA_RATE;
    if (r <= 0.0) {
        g_voice[v].step = 0;
        return;
    }
    int oct = (int)floor(log2(r));
    oct = oct < -8 ? -8 : oct > 7 ? 7 : oct;
    double fns = floor((r / ldexp(1.0, oct) - 1.0) * 1024.0);
    fns = fns < 0.0 ? 0.0 : fns > 1023.0 ? 1023.0 : fns;
    g_voice[v].step = (u32)(ldexp(1.0 + fns / 1024.0, oct) * FRAC_ONE);
}

void aica_voice_level(u32 v, u32 level, u32 pan)
{
    if (v >= AICA_VOICES) return;
    Voice *p = &g_voice[v];
    p->level = level > 15u ? 15u : level;
    p->pan = pan > 31u ? 31u : pan;
    pan_gains(p->level, p->pan, &p->gain_l, &p->gain_r);
}

void aica_voice_play(u32 v, u32 start, int loop, u32 loop_start, u32 end)
{
    if (v >= AICA_VOICES || !aica_ram()) return;
    Voice *p = &g_voice[v];
    p->end = end && end < p->frames ? end : p->frames;
    p->loop = loop;
    p->loop_start = loop_start < p->end ? loop_start : 0;
    p->playing = start < p->end;
    p->pos = p->playing ? start : p->end;
    p->frac = 0;
    p->dpos = 0xffffffffu; /* not decoded: the first fetch starts the decoder */
    if (!p->playing) return;
    p->cur = fetch(p, p->pos, 0);
    p->next = following(p);
}

void aica_voice_stop(u32 v)
{
    if (v < AICA_VOICES) g_voice[v].playing = 0;
}

int aica_voice_playing(u32 v) { return v < AICA_VOICES && g_voice[v].playing; }

u32 aica_voice_position(u32 v) { return v < AICA_VOICES ? g_voice[v].pos : 0; }

static void cd_gains(void)
{
    for (u32 c = 0; c < 2; c++) pan_gains(g_cd_level[c], g_cd_pans[c], &g_cd_gain[2 * c], &g_cd_gain[2 * c + 1]);
}

void aica_cd_level(u32 left, u32 right)
{
    g_cd_level[0] = left > 15u ? 15u : left;
    g_cd_level[1] = right > 15u ? 15u : right;
    cd_gains();
}

void aica_cd_pan(u32 left, u32 right)
{
    g_cd_pans[0] = left > 31u ? 31u : left;
    g_cd_pans[1] = right > 31u ? 31u : right;
    cd_gains();
}

/* Adds one voice into the accumulators, advancing it by n output frames. */
static void voice_mix(Voice *p, float *acc, u32 n)
{
    for (u32 i = 0; i < n && p->playing; i++) {
        float s = (float)p->cur + (float)(p->next - p->cur) * (float)p->frac * (1.0f / FRAC_ONE);
        acc[2 * i] += s * p->gain_l;
        acc[2 * i + 1] += s * p->gain_r;
        for (p->frac += p->step; p->frac >= FRAC_ONE && p->playing; p->frac -= FRAC_ONE) {
            if (++p->pos >= p->end) {
                if (!p->loop) {
                    p->playing = 0;
                    p->pos = p->end;
                    break;
                }
                p->pos = p->loop_start;
            }
            p->cur = p->next;
            p->next = following(p);
        }
    }
}

void aica_mix(s16 *stereo, u32 frames)
{
    static float acc[2 * CHUNK];
    static s16 cd[2 * CHUNK];
    while (frames) {
        u32 n = frames < CHUNK ? frames : CHUNK;
        memset(acc, 0, sizeof(float) * 2u * n);
        if (g_ram)
            for (u32 v = 0; v < AICA_VOICES; v++)
                if (g_voice[v].playing) voice_mix(&g_voice[v], acc, n);
        cdda_read(cd, n);
        for (u32 i = 0; i < n; i++) {
            float l = acc[2 * i] + cd[2 * i] * g_cd_gain[0] + cd[2 * i + 1] * g_cd_gain[2];
            float r = acc[2 * i + 1] + cd[2 * i] * g_cd_gain[1] + cd[2 * i + 1] * g_cd_gain[3];
            stereo[2 * i] = (s16)(l < -32768.0f ? -32768.0f : l > 32767.0f ? 32767.0f : l);
            stereo[2 * i + 1] = (s16)(r < -32768.0f ? -32768.0f : r > 32767.0f ? 32767.0f : r);
        }
        stereo += 2u * n;
        frames -= n;
    }
}
