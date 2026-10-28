/* The AICA's voices and CD input (aica.h), mixed in software. The sound RAM is host memory at its SH-4 address, so
 * the game's copies into it land where a voice reads them. ADPCM is the chip's Yamaha 4-bit form: the low nibble
 * first, each nibble a signed step of the running quantiser (from 127, clamped to 127..24576). */
#include <math.h>
#include <string.h>
#include "aica.h"

#define RAM_MASK (AICA_RAM_SIZE - 1u)
#define FRAC_ONE 0x10000u
#define CHUNK 1024u
/* The release the driver sets on every voice (RR 31, KRS 0): the chip's envelope falls 96 dB, linear in dB, in about
 * 2.7 ms (the AICA manual's decay table at the fastest rates); Flycast's output fades within 2 to 4 ms of a key off. */
#define RELEASE_FRAMES 119u
#define RELEASE_STEP 0.9113f /* 10^(-96 / 20 / 119): one frame's step of the 96 dB fall */

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
    u32 release;          /* key off: output frames of the release left (0: keyed on) */
    u32 delay;            /* output frames of the next mix before a key on within the blank */
    float env;            /* the release's gain, falling RELEASE_DB over RELEASE_FRAMES */
    int own_env;          /* the envelope is g_env's (aica_voice_envelope), not the release above */
    int looped;           /* the voice has passed its loop end, or reached its end (the chip's LP flag) */
} Voice;

static Voice g_voice[AICA_VOICES];
static u8 *g_ram;
static int g_ram_tried;
static float g_cd_gain[4] = {1.0f, 0.0f, 0.0f, 1.0f}; /* CD left to L and R, CD right to L and R */
static u32 g_cd_level[2] = {15, 15}, g_cd_pans[2] = {31, 15}; /* CD left to the left only, right to the right */
static u64 g_mixed; /* output frames mixed */
static int g_cd_muted;
static AicaKeyLog g_key_log;
static u32 g_next_frames = 735u;
static AicaEnvFn g_env;
static AicaSliceFn g_slice;
static u32 g_slice_frames;

void aica_next_frames(u32 frames) { g_next_frames = frames; }

/* The output frames of the next mix that come by phase (0..65536) through the vertical blank. */
static u32 frames_at(u32 phase) { return (u32)((u64)(phase < 65536u ? phase : 65536u) * g_next_frames >> 16); }

void aica_key_log(AicaKeyLog fn) { g_key_log = fn; }
int aica_key_logging(void) { return g_key_log != NULL; }

void aica_key_log_off(u32 v)
{
    if (g_key_log && v < AICA_VOICES) g_key_log(v, g_mixed, 0, 0, 0, 0, 0);
}

void aica_envelope_hook(AicaEnvFn fn) { g_env = fn; }

void aica_voice_envelope(u32 v, int own)
{
    if (v < AICA_VOICES) g_voice[v].own_env = own && g_env;
}

void aica_slice_hook(AicaSliceFn fn, u32 frames)
{
    g_slice = fn;
    g_slice_frames = frames < 1u ? 1u : frames > CHUNK ? CHUNK : frames;
}

void aica_voice_step(u32 v, u32 step)
{
    if (v < AICA_VOICES) g_voice[v].step = step;
}

int aica_voice_looped(u32 v) { return v < AICA_VOICES && g_voice[v].looped; }

u8 *aica_ram(void)
{
    if (!g_ram_tried) {
        g_ram_tried = 1;
        g_ram = (u8 *)host_map_fixed(AICA_RAM_BASE, AICA_RAM_SIZE);
        if (!g_ram) port_log("aica: the sound RAM at %08x cannot be mapped\n", AICA_RAM_BASE);
    }
    return g_ram;
}

/* steps of 3 dB down; 15 steps and more is off. pow, not <math.h>'s inline powf, which is the same (float)pow on
 * x86: an inline powf here would be the one the linker takes for the game's powf too (port_libm.c PORT_OWN_CRT_powf) */
static float atten(u32 steps) { return steps >= 15u ? 0.0f : (float)pow(10.0, (double)(-0.15f * (float)steps)); }

/* The chip's DIPAN: bit 4 picks the side turned down (0 the left, 1 the right), bits 0..3 by 3 dB steps, 15 off. */
static void pan_gains(u32 level, u32 dipan, float *l, float *r)
{
    float g = level ? atten(15u - level) : 0.0f;
    float side = g * atten(dipan & 15u);
    *l = dipan & 16u ? g : side;
    *r = dipan & 16u ? side : g;
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

void aica_voice_level(u32 v, u32 level, u32 dipan)
{
    if (v >= AICA_VOICES) return;
    Voice *p = &g_voice[v];
    p->level = level > 15u ? 15u : level;
    p->pan = dipan & 31u;
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
    p->release = 0;
    p->delay = g_slice ? 0u : frames_at(port_vblank_phase(0)); /* a key on mid-blank: the chip starts it there */
    p->looped = 0;
    p->env = 1.0f;
    p->dpos = 0xffffffffu; /* not decoded: the first fetch starts the decoder */
    if (!p->playing) return;
    if (g_key_log) g_key_log(v, g_mixed, 1, p->level, p->pan, p->step, p->end);
    p->cur = fetch(p, p->pos, 0);
    p->next = following(p);
}

/* A key off: the voice plays on through its release (the driver's RR 31, the fastest) and then stops. */
void aica_voice_stop(u32 v)
{
    if (v >= AICA_VOICES || !g_voice[v].playing || g_voice[v].release) return;
    g_voice[v].release = RELEASE_FRAMES;
    if (g_key_log) g_key_log(v, g_mixed, 0, 0, 0, 0, 0);
}

int aica_voice_playing(u32 v) { return v < AICA_VOICES && g_voice[v].playing; }

u32 aica_voice_position(u32 v, u32 phase)
{
    if (v >= AICA_VOICES) return 0;
    const Voice *p = &g_voice[v];
    u32 out = frames_at(phase);
    if (!p->playing || out <= p->delay) return p->pos;
    u64 pos = p->pos + (((u64)(out - p->delay) * p->step + p->frac) >> 16); /* as voice_mix moves it */
    if (pos < p->end) return (u32)pos;
    return p->loop ? p->loop_start + (u32)((pos - p->end) % (p->end - p->loop_start)) : p->end;
}

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

void aica_cd_mute(int mute) { g_cd_muted = mute; }

/* Adds one voice into the accumulators, advancing it by n output frames. */
static void voice_mix(Voice *p, float *acc, u32 n)
{
    u32 i = p->delay < n ? p->delay : n;
    p->delay -= i;
    for (; i < n && p->playing; i++) {
        float s = (float)p->cur + (float)(p->next - p->cur) * (float)p->frac * (1.0f / FRAC_ONE);
        if (p->own_env) {
            float g = g_env((u32)(p - g_voice));
            if (g < 0.0f) { /* the envelope's release has ended */
                p->playing = 0;
                break;
            }
            s *= g;
        } else if (p->release) {
            s *= p->env;
            p->env *= RELEASE_STEP;
            if (--p->release == 0) {
                p->playing = 0;
                break;
            }
        }
        acc[2 * i] += s * p->gain_l;
        acc[2 * i + 1] += s * p->gain_r;
        for (p->frac += p->step; p->frac >= FRAC_ONE && p->playing; p->frac -= FRAC_ONE) {
            if (++p->pos >= p->end) {
                p->looped = 1;
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
        if (g_slice) { /* runtime/aica_arm.c: the sound processor runs up to each slice's mix */
            n = frames < g_slice_frames ? frames : g_slice_frames;
            g_slice(n);
        }
        memset(acc, 0, sizeof(float) * 2u * n);
        if (g_ram)
            for (u32 v = 0; v < AICA_VOICES; v++)
                if (g_voice[v].playing) voice_mix(&g_voice[v], acc, n);
        cdda_read(cd, n);
        if (g_cd_muted) memset(cd, 0, sizeof(s16) * 2u * n);
        for (u32 i = 0; i < n; i++) {
            float l = acc[2 * i] + cd[2 * i] * g_cd_gain[0] + cd[2 * i + 1] * g_cd_gain[2];
            float r = acc[2 * i + 1] + cd[2 * i] * g_cd_gain[1] + cd[2 * i + 1] * g_cd_gain[3];
            stereo[2 * i] = (s16)(l < -32768.0f ? -32768.0f : l > 32767.0f ? 32767.0f : l);
            stereo[2 * i + 1] = (s16)(r < -32768.0f ? -32768.0f : r > 32767.0f ? 32767.0f : r);
        }
        stereo += 2u * n;
        frames -= n;
        g_mixed += n;
    }
}
