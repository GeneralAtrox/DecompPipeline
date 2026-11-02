/* The sound output (audio_out.h): SDL3 audio streams on the default device, each scaled by the menu's volume. A WAV dump or a
 * check's answer is the console's own and never scaled. */
#include <SDL3/SDL.h>
#include "audio_out.h"

enum { MAX_OUTS = 4 };

struct AudioOut {
    SDL_AudioStream *stream;
};

static AudioOut g_outs[MAX_OUTS];
static int g_volume = 100, g_muted;

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
    apply_gain(a);
    return a;
}

void audio_out_start(AudioOut *a) { SDL_ResumeAudioStreamDevice(a->stream); }

void audio_out_put(AudioOut *a, const void *frames, u32 bytes) { SDL_PutAudioStreamData(a->stream, frames, (int)bytes); }

u32 audio_out_queued(AudioOut *a)
{
    int n = SDL_GetAudioStreamQueued(a->stream);
    return n < 0 ? 0u : (u32)n;
}

void audio_out_clear(AudioOut *a) { SDL_ClearAudioStream(a->stream); }

void audio_out_ratio(AudioOut *a, float ratio) { SDL_SetAudioStreamFrequencyRatio(a->stream, ratio); }

u64 audio_out_now_ns(void) { return SDL_GetTicksNS(); }
