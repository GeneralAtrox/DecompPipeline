/* The session recording: everything a play run takes from outside the game, from boot, so the run can be replayed
 * exactly (the scene dump, runtime/scene_dump.c, saves it). The game runs one simulation step per vertical blank it
 * waits for, so it sees the same things in the same order if it gets the same inputs:
 *   - player 1's pad, per read (host_pads_read): kept only when it changes;
 *   - the SDK timer (Sy_TimerGetCount), the game's own clock: every value, as zigzag varint deltas;
 *   - the RTC (host_local_time) and the save files read (host_save_read), whole;
 *   - the sound chip's blanks mixed ahead of the clock in real-clock play (session_mix_ahead), with the saves as notes.
 * In-between frames (--present interp) are undone afterwards, so what they read is not kept.
 *   --replay FILE   the game gets the recording's values instead, then live ones once it runs out (--live-input on)
 *   --replay-until V  the recording runs out at vertical blank V: resuming a crashed run a little before its end
 *   --record off    keeps nothing
 * Replay with --fixed-clock 1 (as fast as the host allows); the recording checks that each pad change comes at the
 * vertical blank it came at, and logs where a replay first goes out of step.
 * File: "PORTREC1", u32 header length and header text (the version and the arguments), then four sections (pad,
 * timer, RTC, saves), each u32 length and bytes, then u32 pad reads and u32 vertical blanks at the end. */
#include <stdlib.h>
#include <string.h>
#include "base.h"
#include "console.h"
#include "session.h"

#define MAX_BYTES (256u << 20) /* recording stops past this much (hours of play) */
#define SAVE_NAME 32u

typedef struct Buf {
    u8 *p;
    u32 n, cap, at; /* at: the replay's read position */
} Buf;

enum { PAD, TIMER, RTC, SAVES, SECTIONS };

typedef struct PadEntry {
    u32 read, vblank, buttons;
    u8 l, r;
    s8 x, y;
} PadEntry;

static Buf g_rec[SECTIONS], g_play[SECTIONS];
static int g_record = 1, g_replay, g_full, g_out_of_step, g_ended;
static u32 g_reads, g_play_reads, g_bytes, g_until;
static HostPad g_last, g_play_pad;
static s32 g_timer_last, g_play_timer, g_timer_shift;
static int g_timer_shifted;

int session_option(const char *arg, const char *value)
{
    if (!strcmp(arg, "--record") && value) {
        g_record = strcmp(value, "off") != 0;
        return 2;
    }
    if (!strcmp(arg, "--replay-until") && value) {
        g_until = (u32)strtoul(value, NULL, 10);
        return 2;
    }
    if (strcmp(arg, "--replay") || !value) return 0;
    g_replay = session_load(value);
    return 2;
}

/* Whether the replay still gives the values: off from --replay-until's vertical blank, for the rest of the run. */
static int playing(void)
{
    if (g_replay && g_until && port_vblank_count() >= g_until) {
        g_replay = 0;
        g_ended = 1;
        port_log("session: the replay ends at vertical blank %u (--replay-until), pad read %u; %s from here\n", g_until,
                 g_reads, input_live() ? "live input" : "no input (--live-input on reads the real pads)");
    }
    return g_replay;
}

static void put(Buf *b, const void *p, u32 n)
{
    if (!g_record || g_full || port_extra_drawing()) return;
    if (g_bytes + n > MAX_BYTES) {
        g_full = 1;
        port_log("session: recording stopped at %u MB (vertical blank %u)\n", MAX_BYTES >> 20, port_vblank_count());
        return;
    }
    if (b->n + n > b->cap) {
        u32 cap = b->cap ? b->cap * 2u : 4096u;
        while (cap < b->n + n) cap *= 2u;
        u8 *q = (u8 *)realloc(b->p, cap);
        if (!q) {
            g_full = 1;
            return;
        }
        b->p = q;
        b->cap = cap;
    }
    memcpy(b->p + b->n, p, n);
    b->n += n;
    g_bytes += n;
}

/* The replay's next n bytes of a section, or NULL at its end. */
static const u8 *take(int s, u32 n)
{
    Buf *b = &g_play[s];
    if (!playing() || port_extra_drawing() || b->at + n > b->n) return NULL;
    b->at += n;
    return b->p + b->at - n;
}

void session_pad(HostPad *pad)
{
    u32 read = g_reads++;
    if (playing() && read < g_play_reads) {
        PadEntry pe;
        while (g_play[PAD].at + sizeof pe <= g_play[PAD].n) {
            memcpy(&pe, g_play[PAD].p + g_play[PAD].at, sizeof pe);
            if (pe.read > read) break;
            take(PAD, sizeof pe);
            if (pe.vblank != port_vblank_count() && !g_out_of_step++)
                port_log("session: replay out of step at pad read %u: recorded at vertical blank %u, now %u\n", read,
                         pe.vblank, port_vblank_count());
            g_play_pad.buttons = pe.buttons;
            g_play_pad.l = pe.l;
            g_play_pad.r = pe.r;
            g_play_pad.x = pe.x;
            g_play_pad.y = pe.y;
        }
        *pad = g_play_pad;
    } else if (console_lockstep_pad(pad)) {
        /* the paired emulator frame's pad (lockstep.c) */
    } else if (g_replay && !g_ended++) {
        port_log("session: the recording ends at pad read %u (vertical blank %u); %s from here\n", read,
                 port_vblank_count(), input_live() ? "live input" : "no input (--live-input on reads the real pads)");
    }
    if (read == 0 || memcmp(pad, &g_last, sizeof g_last)) {
        PadEntry e = {read, port_vblank_count(), pad->buttons, pad->l, pad->r, pad->x, pad->y};
        put(&g_rec[PAD], &e, sizeof e);
        g_last = *pad;
    }
}

s32 session_timer(s32 live)
{
    s32 v = console_lockstep_timer(live);
    if (playing() && !port_extra_drawing() && g_play[TIMER].at < g_play[TIMER].n) {
        u32 z = 0, shift = 0;
        const u8 *c;
        while ((c = take(TIMER, 1)) != NULL) {
            z |= (u32)(*c & 0x7f) << shift;
            shift += 7;
            if (!(*c & 0x80)) break;
        }
        g_play_timer += (s32)((z >> 1) ^ (0u - (z & 1u)));
        v = g_play_timer;
    } else if (g_until && g_ended) { /* resumed: the live timer goes on from the replay's, never back */
        if (!g_timer_shifted) g_timer_shifted = 1, g_timer_shift = g_play_timer - v;
        v += g_timer_shift;
    }
    if (!port_extra_drawing()) {
        u32 d = (u32)(v - g_timer_last), z = (d << 1) ^ (0u - (d >> 31));
        u8 bytes[5];
        u32 n = 0;
        do {
            bytes[n++] = (u8)((z & 0x7f) | (z > 0x7f ? 0x80 : 0));
            z >>= 7;
        } while (z);
        put(&g_rec[TIMER], bytes, n);
        g_timer_last = v;
    }
    return v;
}

void session_time(HostTime *t)
{
    console_lockstep_time(t);
    const u8 *p = take(RTC, sizeof *t);
    if (p) memcpy(t, p, sizeof *t);
    put(&g_rec[RTC], t, sizeof *t);
}

/* Blanks of the sound chip mixed ahead of the clock in real-clock play (gfx_audio.c tick_realtime): the game sees its
 * voices and a movie's sound move on, so a replay mixes as many at the same blank. Kept as a note with the blank it
 * came at: an older recording has none, and a note the replay has passed is dropped rather than read as a save. */
#define AHEAD_NAME "*audio-ahead"
static int ahead_mode(void); /* DP_SESSION_AHEAD (below) */

void session_mix_ahead(u32 blanks)
{
    u32 e[2] = {port_vblank_count(), blanks};
    if (blanks) session_save_read(AHEAD_NAME, e, sizeof e, 1);
}

static int ahead_next(u32 e[2])
{
    const Buf *b = &g_play[SAVES];
    if (!playing() || port_extra_drawing() || b->at + SAVE_NAME + 16u > b->n ||
        strncmp((const char *)b->p + b->at, AHEAD_NAME, SAVE_NAME))
        return 0;
    memcpy(e, b->p + b->at + SAVE_NAME + 8u, 8);
    return 1;
}

u32 session_mix_ahead_replayed(void)
{
    static int told;
    u32 e[2], n = 0;
    if (ahead_mode() == 2) return 0;
    while (ahead_next(e) && e[0] <= port_vblank_count()) {
        g_play[SAVES].at += SAVE_NAME + 16u;
        if (e[0] == port_vblank_count()) n += e[1];
        else if (!told++)
            port_log("session: replay passed the sound mixed ahead at vertical blank %u (now %u)\n", e[0],
                     port_vblank_count());
    }
    return n;
}

/* A save or note read while notes of sound mixed ahead are at the read position: the notes the replay has passed are
 * dropped (as session_mix_ahead_replayed drops them), and those for this blank or later stay, for their blank. The
 * result is where the first entry past them starts. DP_SESSION_AHEAD=old: every one dropped, as before 11-07 (it lost
 * the sound of a later blank when a game read a note mid-run); =drop: a planted fault, no blank mixed ahead. */
#define AHEAD_ENTRY (SAVE_NAME + 16u)

static int ahead_mode(void)
{
    static int mode = -1;
    char *v = NULL;
    size_t n;
    if (mode < 0) {
        mode = !_dupenv_s(&v, &n, "DP_SESSION_AHEAD") && v ? !strcmp(v, "old") ? 1 : !strcmp(v, "drop") ? 2 : 0 : 0;
        free(v);
    }
    return mode;
}

static u32 ahead_past(void)
{
    Buf *b = &g_play[SAVES];
    u32 e[2], q;
    while (ahead_next(e) && (ahead_mode() == 1 || e[0] < port_vblank_count())) b->at += AHEAD_ENTRY;
    for (q = b->at; ahead_next(e) && q + AHEAD_ENTRY <= b->n && !strncmp((const char *)b->p + q, AHEAD_NAME, SAVE_NAME);)
        q += AHEAD_ENTRY;
    return q;
}

/* The entry of len bytes at q is moved to the read position, the notes before it behind it. */
static int ahead_lift(u32 q, u32 len)
{
    Buf *b = &g_play[SAVES];
    u8 *t;
    if (q == b->at) return 1;
    if (!(t = malloc(len))) return 0;
    memcpy(t, b->p + q, len);
    memmove(b->p + b->at + len, b->p + b->at, q - b->at);
    memcpy(b->p + b->at, t, len);
    free(t);
    return 1;
}

/* host_save_read's result for name: -2 when the recording has none (read the file), else the recorded result. */
int session_save_replayed(const char *name, void *buf, u32 size)
{
    Buf *b = &g_play[SAVES];
    u32 q = ahead_past(), len;
    if (q > b->at && q + SAVE_NAME + 8u > b->n) return -2; /* only notes left: none is a save */
    if (q > b->at) { /* the save after the notes is the one read: it is taken from there */
        memcpy(&len, b->p + q + SAVE_NAME + 4, 4);
        len = SAVE_NAME + 8u + (len < b->n - q - SAVE_NAME - 8u ? len : b->n - q - SAVE_NAME - 8u);
        if (!ahead_lift(q, len)) return -2;
    }
    const u8 *h = take(SAVES, SAVE_NAME + 8u);
    s32 got;
    u32 n;
    if (!h) return -2;
    memcpy(&got, h + SAVE_NAME, 4);
    memcpy(&n, h + SAVE_NAME + 4, 4);
    const u8 *data = n ? take(SAVES, n) : NULL;
    if (strncmp((const char *)h, name, SAVE_NAME) || (n && (n != size || !data))) {
        port_log("session: replay reads save %s, the recording has %.32s: the file is read\n", name,
                 (const char *)h);
        return -2;
    }
    if (data) memcpy(buf, data, size);
    return got;
}

/* Something other than a save that a run keeps with them: an older recording without it stays in step. */
int session_note_replayed(const char *name, void *buf, u32 size)
{
    Buf *b = &g_play[SAVES];
    u32 n, q = ahead_past(); /* the note may follow notes of sound mixed ahead: those stay for their blank */
    if (!playing() || port_extra_drawing() || q + SAVE_NAME + 8u > b->n || strncmp((const char *)b->p + q, name, SAVE_NAME)) return 0;
    memcpy(&n, b->p + q + SAVE_NAME + 4, 4);
    if (n != size || q + SAVE_NAME + 8u + n > b->n) return 0;
    memcpy(buf, b->p + q + SAVE_NAME + 8u, size);
    if (!ahead_lift(q, SAVE_NAME + 8u + n)) return 0;
    b->at += SAVE_NAME + 8u + n;
    return 1;
}

void session_save_read(const char *name, const void *buf, u32 size, int got)
{
    u8 h[SAVE_NAME + 8u] = {0};
    s32 g = got;
    u32 n = got == 1 ? size : 0u;
    strncpy_s((char *)h, SAVE_NAME, name, _TRUNCATE);
    memcpy(h + SAVE_NAME, &g, 4);
    memcpy(h + SAVE_NAME + 4, &n, 4);
    put(&g_rec[SAVES], h, sizeof h);
    if (n) put(&g_rec[SAVES], buf, n);
}

u32 session_reads(void) { return g_reads; }

int session_replaying(void) { return g_replay; }

int session_write(const char *path, const char *header)
{
    FILE *f = NULL;
    u32 len = (u32)strlen(header), v = port_vblank_count();
    int ok;
    if (!g_record || fopen_s(&f, path, "wb") || !f) return 0;
    ok = fwrite("PORTREC1", 8, 1, f) == 1 && fwrite(&len, 4, 1, f) == 1 && fwrite(header, 1, len, f) == len;
    for (int s = 0; s < SECTIONS && ok; s++)
        ok = fwrite(&g_rec[s].n, 4, 1, f) == 1 && (!g_rec[s].n || fwrite(g_rec[s].p, g_rec[s].n, 1, f) == 1);
    ok = ok && fwrite(&g_reads, 4, 1, f) == 1 && fwrite(&v, 4, 1, f) == 1;
    ok = (fclose(f) == 0) && ok;
    return ok && !g_full;
}

int session_load(const char *path)
{
    FILE *f = NULL;
    char magic[8];
    u32 len = 0, end_vblank = 0;
    int ok;
    if (fopen_s(&f, path, "rb") || !f) {
        port_log("session: cannot open the recording %s\n", path);
        return 0;
    }
    ok = fread(magic, 8, 1, f) == 1 && !memcmp(magic, "PORTREC1", 8) && fread(&len, 4, 1, f) == 1 &&
         fseek(f, (long)len, SEEK_CUR) == 0;
    for (int s = 0; s < SECTIONS && ok; s++) {
        ok = fread(&g_play[s].n, 4, 1, f) == 1 && g_play[s].n <= MAX_BYTES &&
             (g_play[s].p = (u8 *)malloc(g_play[s].n + 1u)) != NULL &&
             (!g_play[s].n || fread(g_play[s].p, g_play[s].n, 1, f) == 1);
    }
    ok = ok && fread(&g_play_reads, 4, 1, f) == 1 && fread(&end_vblank, 4, 1, f) == 1;
    fclose(f);
    if (!ok) {
        port_log("session: %s is not a whole recording\n", path);
        return 0;
    }
    port_log("session: replaying %s: %u pad reads to vertical blank %u, %u timer bytes, %u RTC reads\n", path,
             g_play_reads, end_vblank, g_play[TIMER].n, g_play[RTC].n / (u32)sizeof(HostTime));
    return 1;
}
