/* The sound output (audio_out.h): SDL3 audio streams on the default device, each scaled by the menu's volume. A WAV dump or a
 * check's answer is the console's own and never scaled. */
#include <SDL3/SDL.h>
#include "audio_out.h"

enum { MAX_OUTS = 4, GAP_MS = 100 };
#define STEER 0.005 /* audio_out_steer plays this much faster or slower for the queue's distance from its margin */
#define TRIM 0.01    /* and at most this much in all (a percent, a sixth of a semitone): the rest is a steady trim learnt from it */

struct AudioOut {
    SDL_AudioStream *stream;
    u32 rate;
    float ratio;           /* audio_out_ratio's */
    SDL_AtomicInt put;     /* sample frames put (wrapping) */
    SDL_AtomicInt put_ms;  /* SDL_GetTicks() of the last put (0: resting, the silence after it is meant) */
    SDL_AtomicInt ppm;     /* the stream's frequency ratio in millionths, as the device thread reads it */
    SDL_AtomicInt gaps;    /* times the device took more than was put, mid-sound: it played silence */
    SDL_AtomicInt gap_us;  /* the silence */
    u32 took;              /* the device thread's own: frames taken by its count, the remainder, and a gap counting once */
    u64 acc;
    int starving;
    double level;          /* audio_out_steer's: the queue smoothed over about a second of puts (0: a new sound) */
    double trim;           /* and its steady part: a device clock running apart from the host's (kept across sounds) */
};

static AudioOut g_outs[MAX_OUTS];
static int g_volume = 100, g_muted;

/* Each buffer the output's own logical device mixed, on the device's thread: its frames, through the device's rate and the
 * frequency ratio, are frames taken from the stream. Taking more than was put means the device ran out and played silence
 * (a millisecond or more counts). Resting, the count starts again from what the stream still holds. */
static void SDLCALL on_mix(void *user, const SDL_AudioSpec *spec, float *buf, int bytes)
{
    AudioOut *a = (AudioOut *)user;
    u32 put = (u32)SDL_GetAtomicInt(&a->put), pm = (u32)SDL_GetAtomicInt(&a->put_ms);
    (void)buf;
    if (!pm || (u32)SDL_GetTicks() - pm > GAP_MS || spec->freq <= 0) {
        int q = SDL_GetAudioStreamQueued(a->stream);
        a->took = put - (q > 0 ? (u32)q / 4u : 0u), a->acc = 0, a->starving = 0;
        return;
    }
    {
        u64 den = (u64)(u32)spec->freq * 1000000u;
        a->acc += (u64)(u32)(bytes / (int)sizeof(float) / (spec->channels > 0 ? spec->channels : 1)) * a->rate * (u32)SDL_GetAtomicInt(&a->ppm);
        a->took += (u32)(a->acc / den), a->acc %= den;
    }
    {
        int left = (int)(put - a->took);
        if (left >= 0) {
            a->starving = 0;
            return;
        }
        if ((u32)-left >= a->rate / 1000u) {
            if (!a->starving) SDL_AddAtomicInt(&a->gaps, 1);
            a->starving = 1;
            SDL_AddAtomicInt(&a->gap_us, (int)((u64)(u32)-left * 1000000u / a->rate));
        }
        a->took = put; /* the silence took nothing */
    }
}

static void apply_gain(AudioOut *a)
{
    SDL_SetAudioStreamGain(a->stream, g_muted ? 0.0f : (float)g_volume / 100.0f);
}

void audio_set_volume(int percent, int muted)
{
    g_volume = percent < 0 ? 0 : percent > 100 ? 100 : percent;
    g_muted = muted;
    for (int i = 0; i < MAX_OUTS; i++)
        if (g_outs[i].stream) apply_gain(&g_outs[i]);
}

int audio_volume(void) { return g_volume; }

AudioOut *audio_out_open(u32 rate)
{
    SDL_AudioSpec spec;
    AudioOut *a = NULL;
    for (int i = 0; i < MAX_OUTS && !a; i++)
        if (!g_outs[i].stream) a = &g_outs[i];
    if (!a) {
        port_log("audio: more than %d outputs\n", MAX_OUTS);
        return NULL;
    }
    spec.format = SDL_AUDIO_S16;
    spec.channels = 2;
    spec.freq = (int)rate;
    if (!SDL_InitSubSystem(SDL_INIT_AUDIO)) {
        port_log("audio: no SDL audio (%s)\n", SDL_GetError());
        return NULL;
    }
    a->stream = SDL_OpenAudioDeviceStream(SDL_AUDIO_DEVICE_DEFAULT_PLAYBACK, &spec, NULL, NULL);
    if (!a->stream) {
        port_log("audio: no output device (%s)\n", SDL_GetError());
        return NULL;
    }
    a->rate = rate, a->ratio = 1.0f, a->level = 0, a->trim = 0, a->took = 0, a->acc = 0, a->starving = 0;
    SDL_SetAtomicInt(&a->put, 0), SDL_SetAtomicInt(&a->put_ms, 0), SDL_SetAtomicInt(&a->ppm, 1000000);
    SDL_SetAtomicInt(&a->gaps, 0), SDL_SetAtomicInt(&a->gap_us, 0);
    SDL_SetAudioPostmixCallback(SDL_GetAudioStreamDevice(a->stream), on_mix, a);
    apply_gain(a);
    return a;
}

void audio_out_start(AudioOut *a) { SDL_ResumeAudioStreamDevice(a->stream); }

void audio_out_put(AudioOut *a, const void *frames, u32 bytes)
{
    SDL_AddAtomicInt(&a->put, (int)(bytes / 4u)); /* counted first: a count ahead of the stream can only miss a gap */
    SDL_PutAudioStreamData(a->stream, frames, (int)bytes);
    SDL_SetAtomicInt(&a->put_ms, (int)((Uint32)SDL_GetTicks() | 1u));
}

u32 audio_out_gaps(AudioOut *a, u32 *missing_us)
{
    if (missing_us) *missing_us = (u32)SDL_GetAtomicInt(&a->gap_us);
    return (u32)SDL_GetAtomicInt(&a->gaps);
}

u32 audio_out_queued(AudioOut *a)
{
    int n = SDL_GetAudioStreamQueued(a->stream);
    return n < 0 ? 0u : (u32)n;
}

void audio_out_clear(AudioOut *a)
{
    SDL_ClearAudioStream(a->stream);
    audio_out_rest(a);
}

static void set_ratio(AudioOut *a, float r)
{
    SDL_SetAtomicInt(&a->ppm, (int)(r * 1e6f + 0.5f));
    SDL_SetAudioStreamFrequencyRatio(a->stream, r);
}

void audio_out_rest(AudioOut *a) /* what follows is a new sound: at the plain speed */
{
    SDL_SetAtomicInt(&a->put_ms, 0);
    if (a->level > 0) set_ratio(a, a->ratio);
    a->level = 0;
}

void audio_out_ratio(AudioOut *a, float ratio) { a->ratio = ratio, set_ratio(a, ratio); }

/* Before each put of a steady sound: `queued` frames are waiting, `target` is the margin wanted. Off it, the sound plays up
 * to STEER faster or slower (in proportion, full at twice or none of the target), and a trim learns what stays off (at most
 * 0.1 % a second), so neither a device clock running apart from the host's (up to TRIM) nor a late frame's catch-up drains the
 * margin or grows the latency: the queue settles at the target. Returns the speed now. */
float audio_out_steer(AudioOut *a, u32 queued, u32 target)
{
    double e, r;
    a->level = a->level > 0 ? a->level + ((double)queued - a->level) / 50.0 : (double)queued + 0.5;
    e = target ? (a->level - target) / (double)target : 0;
    e = e > 1 ? 1 : e < -1 ? -1 : e;
    a->trim += e * 2e-5; /* 50 puts a second */
    a->trim = a->trim > TRIM ? TRIM : a->trim < -TRIM ? -TRIM : a->trim;
    r = 1.0 + a->trim + e * STEER;
    r = r > 1 + TRIM ? 1 + TRIM : r < 1 - TRIM ? 1 - TRIM : r;
    r = SDL_floor(r * 10000.0 + 0.5) / 10000.0; /* steps of 0.01 %: no call for a change too small to hear */
    if ((int)(r * a->ratio * 1e6 + 0.5) != SDL_GetAtomicInt(&a->ppm)) set_ratio(a, (float)(r * a->ratio));
    return (float)r;
}

float audio_out_trim(AudioOut *a) { return (float)a->trim; }
u64 audio_out_now_ns(void) { return SDL_GetTicksNS(); }
u32 audio_out_device_us(AudioOut *a)
{
    SDL_AudioSpec spec;
    int frames = 0;
    if (!SDL_GetAudioDeviceFormat(SDL_GetAudioStreamDevice(a->stream), &spec, &frames) || frames <= 0 || spec.freq <= 0) return 0;
    return (u32)((u64)frames * 1000000u / (u32)spec.freq);
}
