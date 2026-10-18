/* The sound output: at each vertical blank the AICA (aica.c) mixes 735 frames, the 44,100 Hz the chip runs at over a
 * 1/60 s tick, so its voices and read positions move with the game's tick, the same every run under --fixed-clock.
 * The frames go to an SDL3 audio stream (the default device) and to --audio-dump FILE.wav. The device is off by
 * default in --headless and --fixed-clock runs (automated ones; --fast-until's is on, silent until real time);
 * --audio on|off decides otherwise. --volume 0..100 scales the device's output only. */
#include <stdlib.h>
#include <string.h>
#include <windows.h>
#include <SDL3/SDL.h>
#include "aica.h"
#include "gfx.h"

#define TICK_FRAMES (AICA_RATE / PORT_VBLANK_HZ)
#define TICK_BYTES (TICK_FRAMES * 4u)
#define QUEUE_MAX (8u * TICK_BYTES) /* ~133 ms queued: further ticks are dropped until the device catches up */

static int g_device = -1; /* --audio: -1 the default, 0 off, 1 on */
static const char *g_dump_path;
/* Win32 file calls, not the CRT's: the watchdog suspends the game thread mid-tick, and the CRT's exit flush would
 * wait for the stream lock it holds. */
static HANDLE g_dump = INVALID_HANDLE_VALUE;
static u32 g_dump_bytes;
static SDL_AudioStream *g_stream;
static int g_started;
static int g_volume = 100, g_muted; /* --volume 0..100, and the host's mute (menu, focus); the dump is not scaled */

static void apply_gain(void)
{
    if (g_stream) SDL_SetAudioStreamGain(g_stream, g_muted ? 0.0f : (float)g_volume / 100.0f);
}

void audio_set_volume(int percent, int muted)
{
    g_volume = percent < 0 ? 0 : percent > 100 ? 100 : percent;
    g_muted = muted;
    apply_gain();
}

int audio_volume(void) { return g_volume; }

int audio_option(const char *arg, const char *value)
{
    if (!strcmp(arg, "--audio") && value) {
        g_device = strcmp(value, "off") != 0;
        return 2;
    }
    if (!strcmp(arg, "--volume") && value) {
        audio_set_volume(atoi(value), g_muted);
        return 2;
    }
    if (!strcmp(arg, "--audio-dump") && value) {
        g_dump_path = value;
        return 2;
    }
    return 0;
}

static void put32(u8 *p, u32 v)
{
    for (int i = 0; i < 4; i++) p[i] = (u8)(v >> (8 * i));
}

/* The WAV header for the bytes written so far, rewritten each tick so the file is whole when the run ends. */
static void dump_header(void)
{
    u8 h[44];
    memcpy(h, "RIFF\0\0\0\0WAVEfmt \x10\0\0\0\x01\0\x02\0\0\0\0\0\0\0\0\0\x04\0\x10\0data\0\0\0\0", 44);
    put32(h + 4, 36u + g_dump_bytes);
    put32(h + 24, AICA_RATE);
    put32(h + 28, AICA_RATE * 4u);
    put32(h + 40, g_dump_bytes);
    DWORD put;
    SetFilePointer(g_dump, 0, NULL, FILE_BEGIN);
    WriteFile(g_dump, h, sizeof h, &put, NULL);
    SetFilePointer(g_dump, 0, NULL, FILE_END);
}

static void open_device(void)
{
    SDL_AudioSpec spec;
    static const s16 silence[2 * TICK_FRAMES * 2] = {0};
    spec.format = SDL_AUDIO_S16;
    spec.channels = 2;
    spec.freq = (int)AICA_RATE;
    if (!SDL_InitSubSystem(SDL_INIT_AUDIO)) {
        port_log("audio: no SDL audio (%s)\n", SDL_GetError());
        return;
    }
    g_stream = SDL_OpenAudioDeviceStream(SDL_AUDIO_DEVICE_DEFAULT_PLAYBACK, &spec, NULL, NULL);
    if (!g_stream) {
        port_log("audio: no output device (%s)\n", SDL_GetError());
        return;
    }
    SDL_PutAudioStreamData(g_stream, silence, (int)sizeof silence); /* two ticks of headroom */
    apply_gain();
    SDL_ResumeAudioStreamDevice(g_stream);
}

static void tick(void *arg)
{
    static s16 frames[2 * TICK_FRAMES];
    (void)arg;
    aica_mix(frames, TICK_FRAMES);
    if (g_stream && !(host_fixed_clock() && host_fast_until()) && SDL_GetAudioStreamQueued(g_stream) < (int)QUEUE_MAX)
        SDL_PutAudioStreamData(g_stream, frames, (int)TICK_BYTES);
    if (g_dump != INVALID_HANDLE_VALUE) {
        DWORD put;
        WriteFile(g_dump, frames, TICK_BYTES, &put, NULL);
        g_dump_bytes += TICK_BYTES;
        dump_header();
    }
}

void audio_start(void)
{
    if (g_started) return;
    g_started = 1;
    if (g_device < 0) g_device = !g_gfx.headless && (!host_fixed_clock() || host_fast_until());
    if (g_device) open_device();
    if (g_dump_path) {
        g_dump = CreateFileA(g_dump_path, GENERIC_WRITE, FILE_SHARE_READ, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL,
                             NULL);
        if (g_dump == INVALID_HANDLE_VALUE) port_log("audio: cannot write %s\n", g_dump_path);
        else dump_header();
    }
    port_log("audio: %u Hz stereo, mixed at each vertical blank; output %s%s%s\n", AICA_RATE,
             g_stream ? "to the default device" : "off", g_dump != INVALID_HANDLE_VALUE ? ", dumped to " : "",
             g_dump != INVALID_HANDLE_VALUE ? g_dump_path : "");
    port_add_vblank_task(tick, NULL);
}
