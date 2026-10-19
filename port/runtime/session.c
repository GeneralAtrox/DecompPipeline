/* The session recording: everything a play run takes from outside the game, from boot, so the run can be replayed
 * exactly (the scene dump, runtime/scene_dump.c, saves it). The game runs one simulation step per vertical blank it
 * waits for, so it sees the same things in the same order if it gets the same inputs:
 *   - the pad, per read (host_pad_read): kept only when it changes;
 *   - the SDK timer (Sy_TimerGetCount), the game's own clock: every value, as zigzag varint deltas;
 *   - the RTC (host_local_time) and the save files read (host_save_read), whole.
 * In-between frames (--present interp) are undone afterwards, so what they read is not kept.
 *   --replay FILE   the game gets the recording's values instead, then live ones once it runs out
 *   --record off    keeps nothing
 * Replay with --fixed-clock 1 (as fast as the host allows); the recording checks that each pad change comes at the
 * vertical blank it came at, and logs where a replay first goes out of step.
 * File: "PORTREC1", u32 header length and header text (the version and the arguments), then four sections (pad,
 * timer, RTC, saves), each u32 length and bytes, then u32 pad reads and u32 vertical blanks at the end. */
#include <stdlib.h>
#include <string.h>
#include "port.h"
#include "platform.h"
#include "session.h"
#include "lockstep.h"

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
static u32 g_reads, g_play_reads, g_bytes;
static HostPad g_last, g_play_pad;
static s32 g_timer_last, g_play_timer;

int session_option(const char *arg, const char *value)
{
    if (!strcmp(arg, "--record") && value) {
        g_record = strcmp(value, "off") != 0;
        return 2;
    }
    if (strcmp(arg, "--replay") || !value) return 0;
    g_replay = session_load(value);
    return 2;
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
    if (!g_replay || port_extra_drawing() || b->at + n > b->n) return NULL;
    b->at += n;
    return b->p + b->at - n;
}

void session_pad(HostPad *pad)
{
    u32 read = g_reads++;
    if (g_replay && read < g_play_reads) {
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
    } else if (lockstep_pad(pad)) {
        /* the paired emulator frame's pad (lockstep.c) */
    } else if (g_replay && !g_ended++) {
        port_log("session: the recording ends at pad read %u (vertical blank %u); live input from here\n", read,
                 port_vblank_count());
    }
    if (read == 0 || memcmp(pad, &g_last, sizeof g_last)) {
        PadEntry e = {read, port_vblank_count(), pad->buttons, pad->l, pad->r, pad->x, pad->y};
        put(&g_rec[PAD], &e, sizeof e);
        g_last = *pad;
    }
}

s32 session_timer(s32 live)
{
    s32 v = lockstep_timer(live);
    if (g_replay && !port_extra_drawing() && g_play[TIMER].at < g_play[TIMER].n) {
        u32 z = 0, shift = 0;
        const u8 *c;
        while ((c = take(TIMER, 1)) != NULL) {
            z |= (u32)(*c & 0x7f) << shift;
            shift += 7;
            if (!(*c & 0x80)) break;
        }
        g_play_timer += (s32)((z >> 1) ^ (0u - (z & 1u)));
        v = g_play_timer;
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
    lockstep_time(t);
    const u8 *p = take(RTC, sizeof *t);
    if (p) memcpy(t, p, sizeof *t);
    put(&g_rec[RTC], t, sizeof *t);
}

/* host_save_read's result for name: -2 when the recording has none (read the file), else the recorded result. */
int session_save_replayed(const char *name, void *buf, u32 size)
{
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
