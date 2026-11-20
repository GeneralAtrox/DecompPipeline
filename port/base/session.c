/* The session recording: everything a play run takes from outside the game, from boot, so the run can be replayed
 * exactly (the scene dump, runtime/scene_dump.c, saves it). The game runs one simulation step per vertical blank it
 * waits for, so it sees the same things in the same order if it gets the same inputs:
 *   - player 1's pad, per read (host_pads_read): kept only when it changes;
 *   - players 2..8's pads and whether each is connected, and every player's right stick (session_pad_other): kept
 *     only when they change, so a run only player 1 plays, without the right stick, writes the file it always did;
 *   - the SDK timer (Sy_TimerGetCount), the game's own clock: every value, as zigzag varint deltas;
 *   - the RTC (host_local_time) and the save files read (host_save_read), whole;
 *   - the sound chip's blanks mixed ahead of the clock in real-clock play (session_mix_ahead), with the saves as notes;
 *   - a game's play options as it reads them (port_play_option), with the saves as notes too.
 * In-between frames (--present interp) are undone afterwards, so what they read is not kept.
 *   --replay FILE   the game gets the recording's values instead, then live ones once it runs out (--live-input on)
 *   --replay-until V  the recording runs out at vertical blank V: resuming a crashed run a little before its end
 *   --record off    keeps nothing
 * Replay with --fixed-clock 1 (as fast as the host allows); the recording checks that each pad change comes at the
 * vertical blank it came at, and logs where a replay first goes out of step.
 * File: "PORTREC1", u32 header length and header text (the version and the arguments), then four sections (pad,
 * timer, RTC, saves), each u32 length and bytes, then u32 pad reads and u32 vertical blanks, then, only when it is not
 * empty, a fifth section the same way: players 2..8 and the right sticks (OtherEntry); then, only when a game took text,
 * its own mouse or menu picks, a sixth, EVENTS (session_events.c: the fifth is then always written, the header has
 * mark=events1). A file without them replays as before: players 2..8 and the right sticks keep what the run gives them. */
#include <stdlib.h>
#include <string.h>
#include "base.h"
#include "console.h"
#include "session.h"
#include "session_priv.h"
#if __has_include("port_config.h")
#include "port_config.h" /* game.json session_ahead */
#endif
#ifndef PORT_SESSION_AHEAD /* 1 (session_ahead "old"): a game's replays as DP_SESSION_AHEAD=old, below */
#define PORT_SESSION_AHEAD 0
#endif

#define MAX_BYTES (256u << 20) /* recording stops past this much (hours of play) */
#define SAVE_NAME 32u
typedef struct PadEntry {
    u32 read, vblank, buttons;
    u8 l, r;
    s8 x, y;
} PadEntry;

Buf session_rec[ALL], session_play[ALL]; /* session_priv.h */
int g_session_replay; /* session_priv.h */
static int g_record = 1, g_full, g_out_of_step, g_ended;
static u32 g_reads, g_bytes, g_until;
u32 session_play_reads; /* session_priv.h */
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
    g_session_replay = session_load(value);
    return 2;
}

/* Whether the replay still gives the values: off from --replay-until's vertical blank, for the rest of the run. */
static int playing(void)
{
    if (g_session_replay && g_until && port_vblank_count() >= g_until) {
        g_session_replay = 0;
        g_ended = 1;
        port_log("session: the replay ends at vertical blank %u (--replay-until), pad read %u; %s from here\n", g_until,
                 g_reads, input_live() ? "live input" : "no input (--live-input on reads the real pads)");
    }
    return g_session_replay;
}

void session_put(Buf *b, const void *p, u32 n)
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
const u8 *session_take(int s, u32 n)
{
    Buf *b = &session_play[s];
    if (!playing() || port_extra_drawing() || b->at + n > b->n) return NULL;
    b->at += n;
    return b->p + b->at - n;
}

void session_pad(HostPad *pad)
{
    u32 read = g_reads++;
    if (playing() && read < session_play_reads) {
        PadEntry pe;
        while (session_play[PAD].at + sizeof pe <= session_play[PAD].n) {
            memcpy(&pe, session_play[PAD].p + session_play[PAD].at, sizeof pe);
            if (pe.read > read) break;
            session_take(PAD, sizeof pe);
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
    } else if (g_session_replay && !g_ended++) {
        port_log("session: the recording ends at pad read %u (vertical blank %u); %s from here\n", read,
                 port_vblank_count(), input_live() ? "live input" : "no input (--live-input on reads the real pads)");
    }
    if (read == 0 || memcmp(pad, &g_last, sizeof g_last)) {
        PadEntry e = {read, port_vblank_count(), pad->buttons, pad->l, pad->r, pad->x, pad->y};
        session_put(&session_rec[PAD], &e, sizeof e);
        g_last = *pad;
    }
}

s32 session_timer(s32 live)
{
    s32 v = console_lockstep_timer(live);
    if (playing() && !port_extra_drawing() && session_play[TIMER].at < session_play[TIMER].n) {
        u32 z = 0, shift = 0;
        const u8 *c;
        while ((c = session_take(TIMER, 1)) != NULL) {
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
        session_put(&session_rec[TIMER], bytes, n);
        g_timer_last = v;
    }
    return v;
}

void session_time(HostTime *t)
{
    console_lockstep_time(t);
    const u8 *p = session_take(RTC, sizeof *t);
    if (p) memcpy(t, p, sizeof *t);
    session_put(&session_rec[RTC], t, sizeof *t);
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
    const Buf *b = &session_play[SAVES];
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
        session_play[SAVES].at += SAVE_NAME + 16u;
        if (e[0] == port_vblank_count()) n += e[1];
        else if (!told++)
            port_log("session: replay passed the sound mixed ahead at vertical blank %u (now %u)\n", e[0],
                     port_vblank_count());
    }
    return n;
}

/* A save or note read at notes of sound mixed ahead: those the replay has passed are dropped (as session_mix_ahead_replayed
 * does), those for this blank or later stay; the result is where the first entry past them starts. DP_SESSION_AHEAD=old
 * (a game's default: PORT_SESSION_AHEAD 1): all dropped, as before 11-07, losing a later blank's sound; =drop: a planted fault. */
#define AHEAD_ENTRY (SAVE_NAME + 16u)

static int ahead_mode(void)
{
    static int mode = -1;
    char *v = NULL;
    size_t n;
    if (mode < 0) {
        mode = !_dupenv_s(&v, &n, "DP_SESSION_AHEAD") && v ? !strcmp(v, "old") ? 1 : !strcmp(v, "drop") ? 2 : 0 : PORT_SESSION_AHEAD;
        free(v);
    }
    return mode;
}

static u32 ahead_past(void)
{
    Buf *b = &session_play[SAVES];
    u32 e[2], q;
    while (ahead_next(e) && (ahead_mode() == 1 || e[0] < port_vblank_count())) b->at += AHEAD_ENTRY;
    for (q = b->at; ahead_next(e) && q + AHEAD_ENTRY <= b->n && !strncmp((const char *)b->p + q, AHEAD_NAME, SAVE_NAME);)
        q += AHEAD_ENTRY;
    return q;
}

/* The entry of len bytes at q is moved to the read position, the notes before it behind it. */
static int ahead_lift(u32 q, u32 len)
{
    Buf *b = &session_play[SAVES];
    u8 *t;
    if (q == b->at) return 1;
    t = malloc(len);
    if (!t) return 0;
    memcpy(t, b->p + q, len);
    memmove(b->p + b->at + len, b->p + b->at, q - b->at);
    memcpy(b->p + b->at, t, len);
    free(t);
    return 1;
}

/* host_save_read's result for name: -2 when the recording has none (read the file), else the recorded result. */
int session_save_replayed(const char *name, void *buf, u32 size)
{
    Buf *b = &session_play[SAVES];
    u32 q = ahead_past(), len;
    if (q > b->at && q + SAVE_NAME + 8u > b->n) return -2; /* only notes left: none is a save */
    if (q > b->at) { /* the save after the notes is the one read: it is taken from there */
        memcpy(&len, b->p + q + SAVE_NAME + 4, 4);
        len = SAVE_NAME + 8u + (len < b->n - q - SAVE_NAME - 8u ? len : b->n - q - SAVE_NAME - 8u);
        if (!ahead_lift(q, len)) return -2;
    }
    const u8 *h = session_take(SAVES, SAVE_NAME + 8u);
    s32 got;
    u32 n;
    if (!h) return -2;
    memcpy(&got, h + SAVE_NAME, 4);
    memcpy(&n, h + SAVE_NAME + 4, 4);
    const u8 *data = n ? session_take(SAVES, n) : NULL;
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
    Buf *b = &session_play[SAVES];
    u32 n, q = ahead_past(); /* the note may follow notes of sound mixed ahead: those stay for their blank */
    if (!playing() || port_extra_drawing() || q + SAVE_NAME + 8u > b->n || strncmp((const char *)b->p + q, name, SAVE_NAME)) return 0;
    memcpy(&n, b->p + q + SAVE_NAME + 4, 4);
    if (n != size || q + SAVE_NAME + 8u + n > b->n) return 0;
    memcpy(buf, b->p + q + SAVE_NAME + 8u, size);
    if (!ahead_lift(q, SAVE_NAME + 8u + n)) return 0;
    b->at += SAVE_NAME + 8u + n;
    return 1;
}

int port_play_option(const char *name, int live)
{
    char note[SAVE_NAME] = "*option-";
    s32 v = live;
    strncat_s(note, sizeof note, name, _TRUNCATE);
    if (session_note_replayed(note, &v, sizeof v) && v != live)
        port_log("session: %s is %d in the recording (%d in this run): the recording's is used\n", name, (int)v, live);
    session_save_read(note, &v, sizeof v, 1);
    return v;
}

void session_save_read(const char *name, const void *buf, u32 size, int got)
{
    u8 h[SAVE_NAME + 8u] = {0};
    s32 g = got;
    u32 n = got == 1 ? size : 0u;
    strncpy_s((char *)h, SAVE_NAME, name, _TRUNCATE);
    memcpy(h + SAVE_NAME, &g, 4);
    memcpy(h + SAVE_NAME + 4, &n, 4);
    session_put(&session_rec[SAVES], h, sizeof h);
    if (n) session_put(&session_rec[SAVES], buf, n);
}

u32 session_reads(void) { return g_reads; }
int session_replaying(void) { return g_session_replay; }

/* Words a recording keeps at its header's end, for what a game's play had on that its old recordings did not. */
#define MARKS 256u
static char g_marks[MARKS], g_play_header[4096];

void session_mark(const char *word)
{
    strncat_s(g_marks, MARKS, " mark=", _TRUNCATE);
    strncat_s(g_marks, MARKS, word, _TRUNCATE);
}

int session_marked(const char *word)
{
    char want[64];
    snprintf(want, sizeof want, " mark=%s", word);
    return strstr(g_play_header, want) != NULL;
}

int session_write(const char *path, const char *header)
{
    FILE *f = NULL;
    u32 head = (u32)strlen(header), marks = (u32)strlen(g_marks), len = head + marks, v = port_vblank_count();
    int ok;
    if (!g_record || fopen_s(&f, path, "wb") || !f) return 0;
    ok = fwrite("PORTREC1", 8, 1, f) == 1 && fwrite(&len, 4, 1, f) == 1 && fwrite(header, 1, head, f) == head &&
         (!marks || fwrite(g_marks, 1, marks, f) == marks);
    for (int s = 0; s < SECTIONS && ok; s++)
        ok = fwrite(&session_rec[s].n, 4, 1, f) == 1 && (!session_rec[s].n || fwrite(session_rec[s].p, session_rec[s].n, 1, f) == 1);
    ok = ok && fwrite(&g_reads, 4, 1, f) == 1 && fwrite(&v, 4, 1, f) == 1;
    if (ok && (session_rec[OTHERS].n || session_events_any())) /* players 2..8, only when they played (or before EVENTS) */
        ok = fwrite(&session_rec[OTHERS].n, 4, 1, f) == 1 &&
             (!session_rec[OTHERS].n || fwrite(session_rec[OTHERS].p, session_rec[OTHERS].n, 1, f) == 1) && session_events_write(f);
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
    ok = fread(magic, 8, 1, f) == 1 && !memcmp(magic, "PORTREC1", 8) && fread(&len, 4, 1, f) == 1;
    if (ok) {
        u32 keep = len < sizeof g_play_header - 1u ? len : (u32)sizeof g_play_header - 1u;
        ok = (!keep || fread(g_play_header, keep, 1, f) == 1) && fseek(f, (long)(len - keep), SEEK_CUR) == 0;
    }
    for (int s = 0; s < SECTIONS && ok; s++) {
        ok = fread(&session_play[s].n, 4, 1, f) == 1 && session_play[s].n <= MAX_BYTES &&
             (session_play[s].p = (u8 *)malloc(session_play[s].n + 1u)) != NULL &&
             (!session_play[s].n || fread(session_play[s].p, session_play[s].n, 1, f) == 1);
    }
    ok = ok && fread(&session_play_reads, 4, 1, f) == 1 && fread(&end_vblank, 4, 1, f) == 1;
    if (ok && fread(&session_play[OTHERS].n, 4, 1, f) == 1) /* players 2..8, when the file has them */
        ok = session_play[OTHERS].n <= MAX_BYTES && (session_play[OTHERS].p = (u8 *)malloc(session_play[OTHERS].n + 1u)) != NULL &&
             (!session_play[OTHERS].n || fread(session_play[OTHERS].p, session_play[OTHERS].n, 1, f) == 1) &&
             session_events_load(f); /* the sixth section, EVENTS, when the file has it */
    else
        session_play[OTHERS].n = 0;
    fclose(f);
    if (!ok) {
        port_log("session: %s is not a whole recording\n", path);
        return 0;
    }
    port_log("session: replaying %s: %u pad reads to vertical blank %u, %u timer bytes, %u RTC reads, %u changes of players 2..8 and right sticks\n", path,
             session_play_reads, end_vblank, session_play[TIMER].n, session_play[RTC].n / (u32)sizeof(HostTime),
             session_play[OTHERS].n / (u32)sizeof(OtherEntry));
    return 1;
}
