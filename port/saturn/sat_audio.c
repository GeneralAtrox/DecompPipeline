/* The sound out: the SCSP's mixed samples (44100 Hz, stereo, 16 bit), one video frame at a time, to the base's output
 * (base/audio_out.c), and to a WAV file with `--wav FILE` (which is how a headless or fixed-clock run is checked: no device is
 * opened there). The device's volume is the base's; the WAV file (a check's answer) is never scaled.
 *
 * Nearest existing: sat_cdda.c (the disc's audio tracks, 2352-byte sectors rather than frames of mixed samples); the two play
 * side by side and the system mixes them. */
#include <stdio.h>
#include <string.h>
#include "sat.h"
#include "sat_scsp.h"
#include "audio_out.h"

enum { QUEUE_FRAMES = 8, MAX_FRAMES = 2048 };

static AudioOut *g_out;
static FILE *g_wav;
static u32 g_wav_frames, g_dropped, g_pushed;

void sat_audio_enable(int on)
{
    if (!on || g_out) return;
    g_out = audio_out_open(SAT_SCSP_RATE);
    if (!g_out) {
        port_log("audio: no output, the sound stays silent\n");
        return;
    }
    audio_out_start(g_out);
}

void sat_audio_wav(const char *path)
{
    u8 head[44] = {0};
    g_wav = fopen(path, "wb");
    if (g_wav) fwrite(head, 1, sizeof head, g_wav); /* the sizes are filled in when the run ends */
}

static void le32(u8 *p, u32 v)
{
    for (int i = 0; i < 4; i++) p[i] = (u8)(v >> (8 * i));
}

void sat_audio_close(void)
{
    if (g_pushed) port_log("audio: %u frames of sound mixed, %u dropped because the device queue was full\n", g_pushed, g_dropped);
    if (!g_wav) return;
    {
        u8 h[44];
        u32 bytes = g_wav_frames * 4u;
        memcpy(h, "RIFF\0\0\0\0WAVEfmt \x10\0\0\0\x01\0\x02\0", 24);
        le32(h + 4, 36u + bytes);
        le32(h + 24, SAT_SCSP_RATE);
        le32(h + 28, SAT_SCSP_RATE * 4u);
        h[32] = 4, h[33] = 0, h[34] = 16, h[35] = 0;
        memcpy(h + 36, "data", 4);
        le32(h + 40, bytes);
        fseek(g_wav, 0, SEEK_SET);
        fwrite(h, 1, sizeof h, g_wav);
    }
    fclose(g_wav);
    g_wav = NULL;
}

void sat_audio_push(const s16 *stereo, u32 frames)
{
    if (frames > MAX_FRAMES) frames = MAX_FRAMES;
    g_pushed += frames;
    if (g_wav) {
        fwrite(stereo, 4, frames, g_wav);
        g_wav_frames += frames;
    }
    if (!g_out) return;
    if (audio_out_queued(g_out) >= QUEUE_FRAMES * frames * 4u) {
        g_dropped += frames; /* the device is behind: this frame's sound is lost */
        return;
    }
    audio_out_put(g_out, stereo, frames * 4u);
}
