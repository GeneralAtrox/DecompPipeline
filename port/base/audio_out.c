/* The sound output (audio_out.h): SDL3 audio streams on the default device, each scaled by the menu's volume. A WAV dump or a
 * check's answer is the console's own and never scaled. */
#include <SDL3/SDL.h>
#include "audio_out.h"
#include "perf.h"

enum { MAX_OUTS = 8, GAP_MS = 100 };
#define STEER 0.005 /* audio_out_steer plays this much faster or slower for the queue's distance from its margin */
#define TRIM 0.01    /* and at most this much in all (a percent, a sixth of a semitone): the rest is a steady trim learnt from it */

struct AudioOut {
    SDL_AudioStream *stream;
    u32 rate;
    u32 frame_bytes;       /* one sample frame: 4 for 16-bit stereo */
    float gain;            /* audio_out_gain's (1 unless the game sets one) */
    void (*need)(void *user, AudioOut *a, u32 bytes);
    void *need_user;
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
        a->took = put - (q > 0 ? (u32)q / a->frame_bytes : 0u), a->acc = 0, a->starving = 0;
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
    SDL_SetAudioStreamGain(a->stream, g_muted ? 0.0f : a->gain * (float)g_volume / 100.0f);
}

void audio_set_volume(int percent, int muted)
{
    g_volume = percent < 0 ? 0 : percent > 100 ? 100 : percent;
    g_muted = muted;
    for (int i = 0; i < MAX_OUTS; i++)
        if (g_outs[i].stream) apply_gain(&g_outs[i]);
}

int audio_volume(void) { return g_volume; }

AudioOut *audio_out_open(u32 rate) { return audio_out_open_format(rate, 2, 0); }

AudioOut *audio_out_open_format(u32 rate, u32 channels, int is_float)
{
    SDL_AudioSpec spec;
    AudioOut *a = NULL;
    for (int i = 0; i < MAX_OUTS && !a; i++)
        if (!g_outs[i].stream) a = &g_outs[i];
    if (!a) {
        port_log("audio: more than %d outputs\n", MAX_OUTS);
        return NULL;
    }
    if (channels < 1 || channels > 8) {
        port_log("audio: %u channels: not 1..8\n", channels);
        return NULL;
    }
    spec.format = is_float ? SDL_AUDIO_F32 : SDL_AUDIO_S16;
    spec.channels = (int)channels;
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
    a->frame_bytes = channels * (is_float ? 4u : 2u), a->gain = 1.0f, a->need = NULL, a->need_user = NULL;
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
    SDL_AddAtomicInt(&a->put, (int)(bytes / a->frame_bytes)); /* counted first: a count ahead of the stream can only miss a gap */
    {
        u64 began = perf_timer_begin();
        SDL_PutAudioStreamData(a->stream, frames, (int)bytes);
        perf_timer_end(PERF_T_AUDIO, began);
    }
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

static void SDLCALL on_need(void *user, SDL_AudioStream *stream, int additional, int total)
{
    AudioOut *a = (AudioOut *)user;
    (void)stream;
    (void)total;
    if (a->need && additional > 0) a->need(a->need_user, a, (u32)additional);
}

void audio_out_pull(AudioOut *a, void (*need)(void *user, AudioOut *a, u32 bytes), void *user)
{
    SDL_LockAudioStream(a->stream);
    a->need = need, a->need_user = user;
    SDL_UnlockAudioStream(a->stream);
    SDL_SetAudioStreamGetCallback(a->stream, need ? on_need : NULL, need ? a : NULL);
}

void audio_out_gain(AudioOut *a, float gain)
{
    a->gain = gain < 0 ? 0.0f : gain > 1 ? 1.0f : gain;
    apply_gain(a);
}

void audio_out_pause(AudioOut *a) { SDL_PauseAudioStreamDevice(a->stream); }

void audio_out_lock(AudioOut *a, int on)
{
    if (!a || !a->stream) return;
    if (on) SDL_LockAudioStream(a->stream);
    else SDL_UnlockAudioStream(a->stream);
}

void audio_out_flush(AudioOut *a) { SDL_FlushAudioStream(a->stream); }

void audio_out_close(AudioOut *a)
{
    if (!a || !a->stream) return;
    SDL_SetAudioStreamGetCallback(a->stream, NULL, NULL);
    SDL_SetAudioPostmixCallback(SDL_GetAudioStreamDevice(a->stream), NULL, NULL);
    SDL_DestroyAudioStream(a->stream);
    a->stream = NULL;
}
u32 audio_out_device_us(AudioOut *a)
{
    SDL_AudioSpec spec;
    int frames = 0;
    if (!SDL_GetAudioDeviceFormat(SDL_GetAudioStreamDevice(a->stream), &spec, &frames) || frames <= 0 || spec.freq <= 0) return 0;
    return (u32)((u64)frames * 1000000u / (u32)spec.freq);
}
