/* The kit's sound output: one tick's mix (44100 Hz, stereo, 16 bit) to the base's output, base/audio_out.c, and to a WAV file with XB_WAV=FILE, which is how
 * a headless or fixed-clock run is checked: no device is opened there. The device's volume is the base's; the WAV file is never scaled.
 *
 * Nearest existing: port/saturn/sat_audio.c (the same output and WAV file; it is that console's, and this kit stands on its own mixer). */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "xb.h"
#include "audio_out.h"
#include "present.h"

enum { RATE = XB_SOUND_RATE, TICK_FRAMES = RATE / 60, QUEUE_TICKS = 8 };

static AudioOut *s_out;
static FILE *s_wav;
static u32 s_wav_frames, s_mixed, s_dropped;

static void wav_header(u32 frames)
{
    u32 bytes = frames * 4u;
    u32 v[] = {36u + bytes, RATE, RATE * 4u, bytes};
    fseek(s_wav, 0, SEEK_SET);
    fwrite("RIFF", 1, 4, s_wav);
    fwrite(&v[0], 4, 1, s_wav);
    fwrite("WAVEfmt \x10\0\0\0\x01\0\x02\0", 1, 16, s_wav);
    fwrite(&v[1], 4, 2, s_wav);
    fwrite("\x04\0\x10\0" "data", 1, 8, s_wav);
    fwrite(&v[3], 4, 1, s_wav);
}

/* One tick's sound, from the tick's end */
void xb_sound_tick(void)
{
    static int opened;
    static s32 mix[TICK_FRAMES * 2];
    static s16 out[TICK_FRAMES * 2];
    if (!xb_sound_up()) return;
    if (!opened) {
        const char *wav = getenv("XB_WAV");
        opened = 1;
        if (wav && !fopen_s(&s_wav, wav, "wb")) wav_header(0);
        if (!g_gfx.headless) {
            s_out = audio_out_open(RATE);
            if (s_out) audio_out_start(s_out);
        }
    }
    memset(mix, 0, sizeof mix);
    xb_sound_mix(mix, TICK_FRAMES);
    for (unsigned i = 0; i < TICK_FRAMES * 2; i++) out[i] = (s16)(mix[i] > 32767 ? 32767 : mix[i] < -32768 ? -32768 : mix[i]);
    s_mixed += TICK_FRAMES;
    if (s_wav) {
        fwrite(out, 4, TICK_FRAMES, s_wav);
        s_wav_frames += TICK_FRAMES;
    }
    if (!s_out) return;
    if (audio_out_queued(s_out) >= QUEUE_TICKS * TICK_FRAMES * 4u) {
        s_dropped += TICK_FRAMES;   /* the device is behind: this tick's sound is lost */
        return;
    }
    audio_out_put(s_out, out, TICK_FRAMES * 4u);
}

void xb_sound_close(void)
{
    u32 pcm, xma;
    xb_sound_counts(&pcm, &xma);
    port_log("sound: %u frames mixed, %u dropped because the device queue was full; %u PCM cues and %u XMA cues played\n", s_mixed, s_dropped, pcm, xma);
    if (!s_wav) return;
    wav_header(s_wav_frames);
    fclose(s_wav);
    s_wav = NULL;
}
