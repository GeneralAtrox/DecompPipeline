/* The sound output: at each vertical blank the AICA (aica.c) mixes the 44,100 Hz the chip runs at over the console's
 * NTSC blank (aica_vblank_frames: 735.735 frames as 735 or 736), so its voices, read positions and CD tracks move with
 * the game's tick as on the console, the same every run under --fixed-clock. The host's tick is 1/60 s, so the device
 * stream plays those frames 1.001 times as fast (44,144 a second) and never runs ahead of the device. In real-clock
 * play a slow scene (the game ticking at half the blanks) would starve the device, so tick_realtime keeps the mix
 * ahead of the clock by the longest recent gap between ticks, counts the underruns and logs them every 10 s.
 * The frames go to the base's output (base/audio_out.c) and to --audio-dump FILE.wav. The device is off by
 * default in --headless and --fixed-clock runs (automated ones; --fast-until's is on, silent until real time);
 * --audio on|off decides otherwise. --volume 0..100 scales the device's output only. For the audio check
 * (tools/audio_check.py): --audio-keys FILE logs each voice start, "K frame voice level dipan step frames", and key off,
 * "F frame voice 0 0 0 0", frame counted as the dump's; --audio-cd off leaves the CD out of the mix (the drive still
 * plays), so the dump holds the voices alone. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <windows.h>
#include "audio_out.h"
#include "aica.h"
#include "gfx.h"
#include "media/media.h"
#include "prof.h"
#include "session.h"

#define TICK_BYTES (AICA_VBLANK_MAX_FRAMES * 4u)
#define QUEUE_MAX (8u * TICK_BYTES) /* ~133 ms queued: further ticks are dropped until the device catches up */
#define OUT_RATE ((double)AICA_RATE * PORT_VBLANK_HZ * 1001.0 / 60000.0) /* frames the device takes a second */
#define LOOKAHEAD_MAX (0.5 * AICA_RATE) /* the most a slow scene queues ahead: 500 ms */
#define QUEUE_HARD (LOOKAHEAD_MAX * 1.5 * 4u + 4 * TICK_BYTES) /* bytes: the stream's own limit */
#define SLOW_SCENE_GAP (1.5 / PORT_VBLANK_HZ) /* a mean gap over this is a slow scene: 40 blanks a second */
#define LONG_GAP (2.0 / PORT_VBLANK_HZ) /* a gap the queue's floor cannot cover */
#define LONG_GAPS_SLOW 1.5 /* two long gaps inside about a second are a slow scene too; one is a spike, which must not raise the mix */
#define MIX_BUDGET_NS 6000000u /* the most a tick spends mixing ahead: a dear mix must cost a gap, not the game's time */

static int g_device = -1; /* --audio: -1 the default, 0 off, 1 on */
static const char *g_dump_path, *g_keys_path;
/* Win32 file calls, not the CRT's: the watchdog suspends the game thread mid-tick, and the CRT's exit flush would
 * wait for the stream lock it holds. */
static HANDLE g_dump = INVALID_HANDLE_VALUE, g_keys = INVALID_HANDLE_VALUE;
static u32 g_dump_bytes;
static AudioOut *g_stream;
static int g_started;
static u64 g_ticks; /* vertical blanks mixed */
/* Real-clock output accounting (tick_realtime): frames put, the clock they play against, the longest recent gap
 * between ticks, and the underruns (the device ran dry), in all and in the log's current 10 s window. */
static u64 g_put_frames, g_t0, g_last_tick, g_win_t0, g_underruns, g_starved_frames, g_win_starved;
static u32 g_win_underruns, g_win_ticks;
static double g_peak_gap, g_mean_gap, g_long_gaps, g_win_gap;
static int g_starving, g_ahead = 1; /* --audio-ahead off: the old fixed queue, for comparing underruns */

int audio_option(const char *arg, const char *value)
{
    if (!strcmp(arg, "--audio") && value) {
        g_device = strcmp(value, "off") != 0;
        return 2;
    }
    if (!strcmp(arg, "--volume") && value) {
        audio_set_volume(atoi(value), 0);
        return 2;
    }
    if (!strcmp(arg, "--audio-dump") && value) {
        g_dump_path = value;
        return 2;
    }
    if (!strcmp(arg, "--audio-cd") && value) {
        aica_cd_mute(!strcmp(value, "off"));
        return 2;
    }
    if (!strcmp(arg, "--audio-ahead") && value) {
        g_ahead = strcmp(value, "off") != 0;
        return 2;
    }
    if (!strcmp(arg, "--audio-keys") && value) {
        g_keys_path = value;
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
    static const s16 silence[2 * AICA_VBLANK_MAX_FRAMES * 2] = {0};
    g_stream = audio_out_open(AICA_RATE);
    if (!g_stream) return;
    audio_out_put(g_stream, silence, (u32)sizeof silence); /* two ticks of headroom */
    g_put_frames = sizeof silence / 4u;
    g_t0 = g_win_t0 = audio_out_now_ns();
    audio_out_ratio(g_stream, (float)(PORT_VBLANK_HZ * 1001u) / 60000.0f); /* blanks to ticks */
    audio_out_start(g_stream);
}

/* One blank's mix: the voices, the CD and the decoders move on by n frames. */
static u32 mix_chunk(s16 *frames)
{
    u32 n = aica_vblank_frames(g_ticks++);
    u64 pt = prof_begin(PROF_AUDIO);
    aica_mix(frames, n);
    prof_end(pt);
    pt = prof_begin(PROF_MEDIA);
    media_mix(frames, n); /* the kit's own decoders: ADX music, a movie's sound */
    prof_end(pt);
    aica_next_frames(aica_vblank_frames(g_ticks));
    if (g_dump != INVALID_HANDLE_VALUE) {
        DWORD put;
        WriteFile(g_dump, frames, n * 4u, &put, NULL);
        g_dump_bytes += n * 4u;
        dump_header();
    }
    return n;
}

static void put_chunk(const s16 *frames, u32 n)
{
    audio_out_put(g_stream, frames, n * 4u);
    g_put_frames += n;
}

/* The device plays what was put at OUT_RATE from g_t0 on. The clock gives the frames still queued ahead of it (the
 * backlog); below zero the device ran dry: the shortfall is silence the player heard (an underrun), and the clock is
 * set to where the device would have started again. */
static double backlog_now(u64 now)
{
    double backlog = (double)g_put_frames - (double)(now - g_t0) * OUT_RATE / 1e9;
    if (backlog < 0) {
        if (!g_starving) g_underruns++, g_win_underruns++;
        g_starving = 1;
        g_starved_frames += (u64)-backlog, g_win_starved += (u64)-backlog;
        g_t0 += (u64)(-backlog * 1e9 / OUT_RATE);
        backlog = 0;
    } else if (backlog > 0) {
        g_starving = 0;
    }
    return backlog;
}

/* Real-clock play. The blanks come as fast as the game ticks, which a slow scene (a movie decoded in game code)
 * drops to a half or less, and its gaps outrun a fixed queue: the device starves with the game still running. So
 * the mix is kept ahead of the clock instead: when a tick finds the backlog under a look-ahead, more blanks are
 * mixed, which is the chip running on between the game's ticks as on the console. The look-ahead follows the longest
 * recent gap between ticks (decaying), so 60 blanks a second keeps its ~2-tick queue and a slow scene gets what it
 * needs, up to LOOKAHEAD_MAX; a backlog well over the look-ahead drops the blank, which brings the latency back.
 * Only a slow scene gets it, by the mean gap or by repeated long gaps: a spike in a game near 60 blanks a second used to raise the look-ahead
 * and the extra mixing then held its ticks over a slot (46 blanks a second where the old queue held 59.5). */
static void tick_realtime(s16 *frames, u32 n)
{
    u64 now = audio_out_now_ns();
    double gap = g_last_tick ? (double)(now - g_last_tick) / 1e9 : 0.0;
    g_last_tick = now;
    if (gap > g_win_gap) g_win_gap = gap;
    g_peak_gap = gap > g_peak_gap * 0.985 ? gap : g_peak_gap * 0.985;
    g_mean_gap += (gap - g_mean_gap) * 0.02;
    g_long_gaps += (gap > LONG_GAP ? 1.0 : 0.0) - g_long_gaps * 0.01;
    double look = g_ahead && (g_mean_gap > SLOW_SCENE_GAP || g_long_gaps > LONG_GAPS_SLOW) ? g_peak_gap * 1.25 * OUT_RATE : 0.0;
    look = look < 2.0 * AICA_VBLANK_MAX_FRAMES ? 2.0 * AICA_VBLANK_MAX_FRAMES : look > LOOKAHEAD_MAX ? LOOKAHEAD_MAX : look;
    double backlog = backlog_now(now);
    if (g_ahead ? backlog <= look + 2.0 * AICA_VBLANK_MAX_FRAMES && audio_out_queued(g_stream) < (u32)QUEUE_HARD
                : audio_out_queued(g_stream) < QUEUE_MAX) {
        put_chunk(frames, n);
        backlog += n;
    }
    int extra = 0;
    for (; g_ahead && backlog < look && extra < 64 && audio_out_now_ns() - now < MIX_BUDGET_NS; extra++) {
        n = mix_chunk(frames);
        put_chunk(frames, n);
        backlog += n;
    }
    session_mix_ahead((u32)extra); /* the game sees the voices move on, so a replay mixes these too */
    g_win_ticks++;
    if (now - g_win_t0 >= 10000000000ull) {
        double secs = (double)(now - g_win_t0) / 1e9;
        port_log("audio: %.1f s: %.1f blanks/s, %u underruns (%.0f ms silent), longest tick gap %.0f ms, look-ahead %.0f ms, total %llu underruns (%.0f ms)\n",
                 secs, g_win_ticks / secs, g_win_underruns, g_win_starved * 1000.0 / OUT_RATE, g_win_gap * 1000.0, look * 1000.0 / OUT_RATE,
                 (unsigned long long)g_underruns, g_starved_frames * 1000.0 / OUT_RATE);
        g_win_t0 = now, g_win_ticks = 0, g_win_underruns = 0, g_win_starved = 0, g_win_gap = 0;
    }
}

static void tick(void *arg)
{
    static s16 frames[2 * AICA_VBLANK_MAX_FRAMES];
    (void)arg;
    u32 n = mix_chunk(frames), ahead = session_mix_ahead_replayed();
    if (ahead) { /* a replay: the recorded run mixed these ahead of its clock here */
        session_mix_ahead(ahead);
        for (u32 i = 0; i < ahead; i++) {
            u32 m = mix_chunk(frames);
            if (g_stream && audio_out_queued(g_stream) < QUEUE_MAX) put_chunk(frames, m);
        }
    }
    if (!g_stream || (host_fixed_clock() && host_fast_until())) return;
    if (host_fixed_clock()) { /* deterministic runs: the audio moves only with the game's blanks */
        if (audio_out_queued(g_stream) < QUEUE_MAX) put_chunk(frames, n);
        return;
    }
    tick_realtime(frames, n);
}

static void key_log(u32 v, u64 frame, int on, u32 level, u32 pan, u32 step, u32 frames)
{
    char line[96];
    int n = snprintf(line, sizeof line, "%c %llu %u %u %u %u %u\n", on ? 'K' : 'F', (unsigned long long)frame, v,
                     level, pan, step, frames);
    DWORD put;
    WriteFile(g_keys, line, (DWORD)n, &put, NULL);
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
    if (g_keys_path) {
        g_keys = CreateFileA(g_keys_path, GENERIC_WRITE, FILE_SHARE_READ, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL,
                             NULL);
        if (g_keys != INVALID_HANDLE_VALUE) aica_key_log(key_log);
    }
    port_log("audio: %u Hz stereo, mixed at each vertical blank; output %s%s%s\n", AICA_RATE,
             g_stream ? "to the default device" : "off", g_dump != INVALID_HANDLE_VALUE ? ", dumped to " : "",
             g_dump != INVALID_HANDLE_VALUE ? g_dump_path : "");
    port_add_vblank_task(tick, NULL);
}
