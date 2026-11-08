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
#include "sat_sys.h"

enum { QUEUE_MS = 160, MAX_FRAMES = 2048, LEAD_MAX = 2048 };

static AudioOut *g_out;
static FILE *g_wav;
static u32 g_wav_frames, g_dropped, g_drops, g_pushed, g_held, g_relead;
static u32 g_dry, g_qn, g_qmin = 0xffffffffu, g_qmax; /* the device's queue as each frame came: empty (ran dry), least, most */
static u64 g_qsum;
static int g_silent = 1, g_was_held = 1;
static u32 g_lead, g_leads;   /* silence put where a sound starts: the device's buffer plus 10 ms, the margin the queue then keeps
                               * (audio_out_steer) while the game's frames come */
static u32 g_gaps, g_gap_us;  /* the output's gaps as of the last frame put (not the run's end, whose sound runs out on purpose) */
static float g_smin = 1, g_smax = 1;
static u64 g_last_ns;
static double g_late, g_late_max; /* ms a frame's sound came after its 20 ms, the most lately (fading) and the most of the run */

int sat_audio_silent(void) { return g_silent; } /* the last frame of sound was all zero */

void sat_audio_enable(int on)
{
    if (!on || g_out) return;
    g_out = audio_out_open(SAT_SCSP_RATE);
    if (!g_out) {
        port_log("audio: no output, the sound stays silent\n");
        return;
    }
    g_lead = audio_out_device_us(g_out) * (u64)SAT_SCSP_RATE / 1000000u + SAT_SCSP_RATE / 100u;
    if (g_lead > LEAD_MAX) g_lead = LEAD_MAX;
    port_log("audio: %.1f ms of silence leads each sound (the device's buffer and 10 ms), a margin then kept by playing up to 1 %% faster or slower\n", g_lead * 1000.0 / SAT_SCSP_RATE);
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
    if (g_pushed) port_log("audio: %u frames of sound mixed, %u dropped because the device queue was full (%u ms), %u silent ones not queued on a black screen\n", g_pushed, g_dropped, (u32)QUEUE_MS, g_held);
    if (g_qn) { /* ran dry: the queue was empty when the next video frame's sound came mid-sound (silence running it dry is a new start) */
        u32 us = g_gap_us, gaps = g_gaps;
        port_log("audio: the device queue ran dry (under 1 ms queued) mid-sound %u times in %u video frames (and in silence %u times, each then led again); queued %.1f ms on average (least %.1f, most %.1f)\n", g_dry, g_qn, g_relead,
                 (double)g_qsum * 1000.0 / g_qn / SAT_SCSP_RATE, (double)g_qmin * 1000.0 / SAT_SCSP_RATE, (double)g_qmax * 1000.0 / SAT_SCSP_RATE);
        port_log("audio: the device played silence mid-sound %u times (%.1f ms in all); %u sounds started with %.1f ms of margin, raised by up to %.1f ms for late frames; played %+.2f..%+.2f %% fast to keep it, %+.3f %% of it steady (the device's clock against the system's)\n",
                 gaps, us / 1000.0, g_leads, g_lead * 1000.0 / SAT_SCSP_RATE, g_late_max < 40 ? g_late_max : 40.0, (g_smin - 1) * 100.0, (g_smax - 1) * 100.0,
                 audio_out_trim(g_out) * 100.0);
    }
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
    u32 i;
    if (frames > MAX_FRAMES) frames = MAX_FRAMES;
    g_pushed += frames;
    for (i = 0; i < frames * 2u && !stereo[i]; i++) {}
    g_silent = i == frames * 2u;
    if (g_wav) {
        fwrite(stereo, 4, frames, g_wav);
        g_wav_frames += frames;
    }
    if (!g_out) return;
    if (sat_black_fast() && g_silent) {
        g_held += frames; /* a black screen running fast: its silence is not queued, so the sound after it is not late */
        if (!g_was_held) audio_out_rest(g_out);
        g_was_held = 1;
        return;
    }
    {
        static const s16 lead[(LEAD_MAX + 1024) * 2] = {0};
        u32 q = audio_out_queued(g_out) / 4u, per = SAT_SCSP_RATE / (sat_sys_hz() ? sat_sys_hz() : 50u); /* frames the device still holds; a video frame's */
        u64 now = audio_out_now_ns();
        u32 us = 0, gaps = audio_out_gaps(g_out, &us);
        if (gaps != g_gaps && gaps <= 20u) /* the first ones, with where the game was */
            port_log("audio: the device played silence before vertical blank %u (%.1f ms; %u so far); %.1f ms queued now\n", sat_vblank_count(),
                     (us - g_gap_us) / 1000.0, gaps, q * 1000.0 / SAT_SCSP_RATE);
        g_gaps = gaps, g_gap_us = us;
        if (g_was_held || (g_silent && q < SAT_SCSP_RATE / 1000u)) {
            /* A sound starts, or silence ran the queue dry (the same, unheard): the margin first, so that with this frame's
             * sound the queue holds the margin and a video frame more, as after every steady frame. This frame's sound alone
             * may be a few ms (the first after a black screen ran fast), and the margin without it is gone by the next. */
            u32 want = g_lead + per, have = q + frames;
            if (!g_was_held) audio_out_rest(g_out), g_relead++;
            if (have < want) audio_out_put(g_out, lead, (want - have) * 4u);
            g_leads++;
        } else {
            double late = (double)(now - g_last_ns) / 1e6 - frames * 1000.0 / SAT_SCSP_RATE, add;
            float s;
            g_late = late > g_late ? late : g_late * 0.998; /* fades to a third in 500 frames */
            g_late_max = g_late > g_late_max ? g_late : g_late_max;
            add = g_late < 40.0 ? g_late : 40.0;
            s = audio_out_steer(g_out, q, g_lead + (u32)(add * SAT_SCSP_RATE / 1000.0));
            g_smin = s < g_smin ? s : g_smin, g_smax = s > g_smax ? s : g_smax;
            if (q < SAT_SCSP_RATE / 1000u && ++g_dry <= 20u)
                port_log("audio: the device queue ran dry mid-sound at vertical blank %u, %.1f ms after the frame before (%u frames of sound)\n", sat_vblank_count(), (now - g_last_ns) / 1e6, frames);
            g_qn++, g_qsum += q, g_qmin = q < g_qmin ? q : g_qmin, g_qmax = q > g_qmax ? q : g_qmax;
        }
        g_was_held = 0, g_last_ns = now;
    }
    if (audio_out_queued(g_out) / 4u >= SAT_SCSP_RATE / 1000u * QUEUE_MS) { /* in time, whatever this frame's size */
        if (++g_drops <= 5u)
            port_log("audio: the device queue held %.1f ms at vertical blank %u: %u frames of sound dropped\n", audio_out_queued(g_out) / 4u * 1000.0 / SAT_SCSP_RATE, sat_vblank_count(), frames);
        g_dropped += frames; /* the device has stopped taking: this frame's sound is lost */
        return;
    }
    audio_out_put(g_out, stereo, frames * 4u);
}
