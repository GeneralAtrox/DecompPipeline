/* Sound the kit decodes itself (ADX music, a movie's sound), mixed into the output after the AICA's voices: each
 * source hands over 44,100 Hz stereo frames at the vertical blank, so it moves with the game's tick as the AICA does
 * (the same every run under --fixed-clock). A talk is one ADX stream played as ADXT plays it: from a file in host
 * memory, looping as its header says, at a volume in 0.1 dB steps; a stream at another rate is stepped to 44,100 Hz
 * (nearest sample). The first source starts the kit's sound output, as the AICA's modules do. */
#include <math.h>
#include <stdlib.h>
#include <string.h>
#include "aica.h" /* audio_start */
#include "media.h"

#define SOURCES 16u
#define MIX_RATE 44100u

static struct {
    MediaPullFn fn;
    void *ctx;
} g_src[SOURCES];

void media_add(MediaPullFn fn, void *ctx)
{
    audio_start(); /* the output's vertical-blank mix, once */
    for (u32 i = 0; i < SOURCES; i++)
        if (g_src[i].ctx == ctx) return;
    for (u32 i = 0; i < SOURCES; i++)
        if (!g_src[i].fn) {
            g_src[i].fn = fn;
            g_src[i].ctx = ctx;
            return;
        }
}

void media_remove(void *ctx)
{
    for (u32 i = 0; i < SOURCES; i++)
        if (g_src[i].ctx == ctx) g_src[i].fn = NULL, g_src[i].ctx = NULL;
}

void media_mix(s16 *stereo, u32 frames)
{
    static s16 buf[2 * 1024];
    for (u32 i = 0; i < SOURCES; i++) {
        if (!g_src[i].fn) continue;
        for (u32 at = 0; at < frames;) {
            u32 want = frames - at > 1024u ? 1024u : frames - at;
            u32 got = g_src[i].fn(g_src[i].ctx, buf, want);
            for (u32 k = 0; k < 2u * got; k++) {
                s32 v = stereo[2u * at + k] + buf[k];
                stereo[2u * at + k] = (s16)(v > 32767 ? 32767 : v < -32768 ? -32768 : v);
            }
            if (got < want) break;
            at += got;
        }
    }
}

s32 media_gain(s32 centibels)
{
    if (centibels >= 0) return 32768;
    if (centibels <= -960) return 0;
    return (s32)(32768.0 * pow(10.0, centibels / 200.0) + 0.5);
}

struct MediaTalk {
    AdxStream s;
    u8 *buf;
    u32 cap;
    s32 gain;    /* 1.15 */
    s32 state;   /* MEDIA_TALK_* */
    int paused;
    u32 step;    /* the stream's samples per output frame, 16.16 */
    u32 frac;
    s16 last[2]; /* the sample being stepped over */
};

MediaTalk *media_talk_new(void)
{
    MediaTalk *t = calloc(1, sizeof *t);
    if (t) t->gain = 32768;
    return t;
}

void media_talk_free(MediaTalk *t)
{
    if (!t) return;
    media_remove(t);
    free(t->buf);
    free(t);
}

u8 *media_talk_load(MediaTalk *t, u32 size)
{
    media_talk_stop(t);
    if (size > t->cap) {
        u8 *b = realloc(t->buf, size);
        if (!b) return NULL;
        t->buf = b;
        t->cap = size;
    }
    return t->buf;
}

static u32 talk_pull(void *ctx, s16 *out, u32 n)
{
    MediaTalk *t = ctx;
    u32 got = 0;
    if (t->state != MEDIA_TALK_PLAYING || t->paused) return 0;
    if (t->step == 0x10000u) got = adx_read(&t->s, out, n);
    else
        for (; got < n; got++) { /* nearest sample, stepped */
            for (; t->frac >= 0x10000u; t->frac -= 0x10000u)
                if (!adx_read(&t->s, t->last, 1)) break;
            if (t->frac >= 0x10000u) break;
            out[2 * got] = t->last[0], out[2 * got + 1] = t->last[1];
            t->frac += t->step;
        }
    for (u32 k = 0; k < 2u * got; k++) out[k] = (s16)((out[k] * t->gain) >> 15);
    if (got < n && adx_ended(&t->s)) t->state = MEDIA_TALK_PLAYEND;
    return got;
}

int media_talk_start(MediaTalk *t, u32 size)
{
    if (!t->buf || !adx_parse(t->buf, size, &t->s.h)) {
        t->state = MEDIA_TALK_ERROR;
        return 0;
    }
    adx_open(&t->s, t->buf, size, 1);
    t->step = (u32)(((u64)t->s.h.rate << 16) / MIX_RATE);
    t->frac = 0x10000u;
    t->state = MEDIA_TALK_PLAYING;
    t->paused = 0;
    media_add(talk_pull, t);
    return 1;
}

void media_talk_stop(MediaTalk *t)
{
    media_remove(t);
    t->state = MEDIA_TALK_STOP;
}

void media_talk_volume(MediaTalk *t, s32 centibels) { t->gain = media_gain(centibels); }
void media_talk_pause(MediaTalk *t, int on) { t->paused = on; }
void media_talk_loop(MediaTalk *t, int on) { t->s.loops = on != 0; }
s32 media_talk_state(const MediaTalk *t) { return t->state; }
const AdxInfo *media_talk_info(const MediaTalk *t) { return &t->s.h; }
u32 media_talk_pos(const MediaTalk *t) { return t->s.pos; }
u32 media_talk_loops(const MediaTalk *t) { return t->s.loop_count; }
