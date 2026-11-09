/* The native movie player (game.json "movie"; docs/RUNTIME_TARGET.md: emulated hardware leaves the play path). When the
 * game calls its movie routine (Robo Pit: MV.BIN's entry, given the file's name, a VDP1 work address and how many pictures
 * must pass before START skips), a play run does not run it on the emulated SH-2s, SCU DMA, VDP1 and sound driver: the
 * host plays the Sega FILM file itself. Cinepak pictures (base/cinepak.c) and the PCM through the base's sound output, each
 * picture shown when the sound reaches its time, centred in the game's screen as the Saturn showed it and scaled to the
 * window by the base. The game's vertical blanks go on as the original's would (scripted presses, the log and a replay
 * keep their blanks): its wait while the file opens and the CD reads ahead, the sound from the blank after its first
 * chunk is read, each picture in the blank its time falls in once read, the end as many blanks after the first picture as
 * the movie lasts. START skips by the original's rule: either pad, at a picture, once that many pictures have been shown;
 * A+B+C+START first calls the game's own check (game.json "reset_call"). The routine then returns what the original
 * returns, as late: 0 played out, game.json "skipped" when skipped, "close_blanks" after the picture that took the skip.
 * Checks, --det and headless runs keep the emulated path; --movie-native always plays here in any run (a proof),
 * --movie-native off (Display > Native movie player) keeps the emulated one in a play run. A replay plays the way it was
 * recorded. --movie-dump DIR[,N] writes every Nth picture shown as a PNG.
 * Nearest existing: sat_cd.c's quick() tells a FILM file by its first sector and the Robo Pit audio check's cpk.py parses
 * one in Python; neither plays a movie. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <windows.h>
#include "sat.h"
#include "sat_sys.h"
#include "sat_vdp.h"
#include "sat_disc.h"
#include "sat_cd.h"
#include "sat_game.h"
#include "cinepak.h"
#include "audio_out.h"
#include "game_log.h"
#include "session.h"
#include "present_cpu.h"

enum { PAD_START = 1u << 3, AHEAD_MS = 200, MAX_W = 704, MAX_H = 512 }; /* START in sat_pad_data's bits (0 = pressed) */
#define NOTE "*movie-native"
#ifndef SAT_MOVIE_OPEN_BLANKS
#define SAT_MOVIE_OPEN_BLANKS 0u /* game.json movie "open_blanks": from the call to the file's first read */
#endif
#ifndef SAT_MOVIE_CLOSE_BLANKS
#define SAT_MOVIE_CLOSE_BLANKS 0u /* "close_blanks": from the picture that took a skip to the return */
#endif

u32 sat_movie_fn; /* the game's movie routine the host plays instead (0: none, or the emulated path in this run) */
static int g_want = -1, g_next = -1, g_play_run, g_audio_on;
static char g_dump[260];
static u32 g_dump_every = 1;
static AudioOut *g_out;
static u32 g_out_rate;
static double g_dev_s; /* the sound the device holds beyond the queue, in seconds */

typedef struct {
    u32 off, len, info1, info2;
} Entry;

typedef struct {
    u32 fad, sectors, hl, w, h, ch, bits, rate, base, n, nvideo, audio_frames;
    Entry *e;
    u8 *buf;
    u32 cap;
    s16 *pcm;
    u32 pcm_cap, next_audio, pushed;
} Movie;

static u32 be32(const u8 *p) { return (u32)p[0] << 24 | (u32)p[1] << 16 | (u32)p[2] << 8 | p[3]; }

int sat_movie_option(const char *a, const char *v)
{
    if (!v) return 0;
    if (!strcmp(a, "--movie-native")) g_want = !strcmp(v, "always") ? 2 : strcmp(v, "off") != 0;
    else if (!strcmp(a, "--movie-dump")) {
        const char *comma = strchr(v, ',');
        size_t n = comma ? (size_t)(comma - v) : strlen(v);
        if (n >= sizeof g_dump) n = sizeof g_dump - 1;
        memcpy(g_dump, v, n);
        g_dump[n] = 0;
        if (comma && atoi(comma + 1) > 0) g_dump_every = (u32)atoi(comma + 1);
    } else return 0;
    return 2;
}

/* Display > Native movie player: from the next start, as Fast disc loads */
int sat_movie_get(void) { return g_next >= 0 ? g_next : sat_movie_fn != 0; }
void sat_movie_set(int on) { g_next = on != 0; }

void sat_movie_start(int play_run, int audio_on)
{
#ifdef SAT_MOVIE_FN
    u8 on = 0;
    int want = g_want == 2 || (play_run && g_want != 0);
    if (session_note_replayed(NOTE, &on, 1)) want = on != 0;
    else if (session_replaying()) want = 0;
    if (want) on = 1, session_save_read(NOTE, &on, 1, 1);
    sat_movie_fn = want ? SAT_MOVIE_FN : 0u;
    g_play_run = play_run, g_audio_on = audio_on && play_run;
    port_log("movie: %s\n", want ? "the native player (Cinepak and its sound on the host)" : "the game's own player, emulated");
#else
    (void)play_run, (void)audio_on;
#endif
}

#ifdef SAT_MOVIE_FN
u32 sat_movie_find(const char *path, u32 *sectors) { return sat_disc_find(path, sectors, NULL); }

static int movie_open(Movie *m, const char *name)
{
    u8 head[16], *h;
    u32 at;
    memset(m, 0, sizeof *m);
    m->fad = sat_disc_find(name, &m->sectors, NULL);
    if (!m->fad || !sat_disc_file_bytes(m->fad, 0, 16, head) || memcmp(head, "FILM", 4)) return 0;
    m->hl = be32(head + 4);
    if (m->hl < 16 || m->hl > (1u << 20) || !(h = (u8 *)malloc(m->hl))) return 0;
    if (!sat_disc_file_bytes(m->fad, 0, m->hl, h)) return free(h), 0;
    for (at = 16; at + 8 <= m->hl;) {
        u32 len = be32(h + at + 4);
        if (len < 8 || at + len > m->hl) break;
        if (!memcmp(h + at, "FDSC", 4) && len >= 26 && !memcmp(h + at + 8, "cvid", 4)) {
            m->h = be32(h + at + 12), m->w = be32(h + at + 16);
            m->ch = h[at + 21], m->bits = h[at + 22], m->rate = (u32)h[at + 24] << 8 | h[at + 25];
        } else if (!memcmp(h + at, "STAB", 4) && len >= 16) {
            m->base = be32(h + at + 8), m->n = be32(h + at + 12);
            if (m->n > (len - 16) / 16u) m->n = (len - 16) / 16u;
            m->e = (Entry *)malloc((size_t)m->n * sizeof *m->e);
            for (u32 i = 0; m->e && i < m->n; i++) {
                const u8 *p = h + at + 16 + 16 * i;
                m->e[i] = (Entry){be32(p), be32(p + 4), be32(p + 8), be32(p + 12)};
                if (m->e[i].info1 != 0xffffffffu) m->nvideo++;
                else if (m->ch && m->bits) m->audio_frames += m->e[i].len / (m->ch * m->bits / 8u);
            }
        }
        at += len;
    }
    free(h);
    if (!m->e || !m->w || !m->h || m->w > MAX_W || m->h > MAX_H || !m->base) return free(m->e), 0;
    return 1;
}

static void movie_close(Movie *m)
{
    free(m->e), free(m->buf), free(m->pcm);
    memset(m, 0, sizeof *m);
}

static u8 *entry_data(Movie *m, const Entry *e)
{
    if (e->len > m->cap) {
        free(m->buf);
        m->cap = e->len;
        m->buf = (u8 *)malloc(m->cap);
    }
    return m->buf && sat_disc_file_bytes(m->fad, m->hl + e->off, e->len, m->buf) ? m->buf : NULL;
}

/* the sound up to `until` frames pushed: each chunk is signed PCM, 16-bit big-endian or 8-bit, a stereo chunk all its
 * left samples then all its right */
static void audio_push(Movie *m, u32 until)
{
    while (m->pushed < until && m->next_audio < m->n) {
        const Entry *e = &m->e[m->next_audio++];
        u32 bps = m->bits / 8u, frames = m->ch && bps ? e->len / (m->ch * bps) : 0;
        const u8 *d;
        if (e->info1 != 0xffffffffu || !frames || !(d = entry_data(m, e))) continue;
        if (frames > m->pcm_cap) {
            free(m->pcm);
            m->pcm_cap = frames;
            m->pcm = (s16 *)malloc((size_t)frames * 4u);
            if (!m->pcm) return;
        }
        for (u32 i = 0; i < frames; i++)
            for (u32 c = 0; c < 2; c++) {
                const u8 *s = d + ((m->ch > 1 ? c : 0) * frames + i) * bps;
                m->pcm[2 * i + c] = (s16)(bps == 2 ? s[0] << 8 | s[1] : (s8)s[0] * 256);
            }
        if (g_out) audio_out_put(g_out, m->pcm, frames * 4u);
        m->pushed += frames;
    }
}

static double wall(void)
{
    LARGE_INTEGER f, t;
    QueryPerformanceFrequency(&f);
    QueryPerformanceCounter(&t);
    return (double)t.QuadPart / (double)f.QuadPart;
}

/* one vertical blank of the game's: counted (the frame limit, the log, scripted presses), the pads read as the SMPC would */
static u32 g_pad[2], g_there;
static void blank(void)
{
    sat_perf_vblank(); /* the play summary's tick: the player's work since the last blank, its waits left out */
    sat_vblank_counted();
    sat_frame_limit_check();
    game_log_poll(sat_vblank_count());
    g_there = sat_pad_data(g_pad);
}

/* at a picture, once enough have shown: 1 when either pad's START is down; *reset when one holds A+B+C+START */
static int skip_asked(int *reset)
{
    int skip = 0;
    for (u32 p = 0; p < 2; p++)
        if (g_there >> p & 1u) {
            if (!(g_pad[p] & 0x0fu)) *reset = 1;
            if (!(g_pad[p] & PAD_START)) skip = 1;
        }
    return skip;
}

/* the blank by which the CD has read through the chunk: reads from blank rd, spb sectors a blank */
static u32 read_by(const Movie *m, const Entry *e, u32 rd, u32 spb) { return rd + (m->hl + e->off + e->len + 2048u * spb - 1u) / (2048u * spb); }

/* the picture in the game's screen, centred (game.json movie "screen", else the last composed picture's size) */
static u32 canvas[MAX_W * MAX_H];
static void show(const Movie *m, const u32 *px, u32 *hash, u32 *dump_w, u32 *dump_h)
{
#ifdef SAT_MOVIE_SCREEN_W
    u32 W = SAT_MOVIE_SCREEN_W, H = SAT_MOVIE_SCREEN_H;
#else
    u32 W = sat_frame_w, H = sat_frame_h;
#endif
    if (W < m->w || W > MAX_W) W = m->w;
    if (H < m->h || H > MAX_H) H = m->h;
    memset(canvas, 0, (size_t)W * H * 4u);
    for (u32 y = 0; y < m->h; y++) memcpy(canvas + (size_t)(y + (H - m->h) / 2) * W + (W - m->w) / 2, px + (size_t)y * m->w, m->w * 4u);
    for (u32 i = 0; i < m->w * m->h; i += 3) *hash = (*hash ^ px[i]) * 16777619u;
    if (g_play_run && !sat_video_headless()) cpu_present_post(canvas, W, H, sat_vblank_count());
    *dump_w = W, *dump_h = H;
}

static void dump(u32 shown, u32 w, u32 h, const u32 *px) /* --movie-dump, outside the picture's time */
{
    char path[300];
    if (!g_dump[0] || shown % g_dump_every) return;
    snprintf(path, sizeof path, "%s/movie_%05u.png", g_dump, sat_vblank_count());
    sat_png_write(path, px, w, h);
}

static int cmp_d(const void *a, const void *b) { double x = *(const double *)a, y = *(const double *)b; return (x > y) - (x < y); }

int sat_movie_play(const TcArgs *a, u32 *ret)
{
    char name[64];
    Movie m;
    Cinepak *cp;
    u32 vb0 = sat_vblank_count(), hz = sat_sys_hz(), shown = 0, next = 0, hash = 2166136261u, slow = 0, nmade = 0, skip_after = 0, i, v;
    u32 spb = sat_cd_data_rate / hz ? sat_cd_data_rate / hz : 1u, rd = vb0 + SAT_MOVIE_OPEN_BLANKS, ko = 0, first, end_vb, stop = 0, reset_at = 0;
    u32 *ready = NULL, *due;
    int det = !g_play_run || sat_sys_fast() || sat_sys_virtual() || sat_sys_det(), playing = 0;
    double t0 = wall(), ta = 0, t = 0, end = 0, drained = 0, made_max = 0, *off = NULL;
#ifdef SAT_MOVIE_SKIP_AFTER_ARG
    skip_after = a->r[SAT_MOVIE_SKIP_AFTER_ARG];
#endif
    for (i = 0; i < sizeof name - 1 && (name[i] = (char)rd8(a->r[SAT_MOVIE_NAME_ARG] + i)) != 0; i++) {}
    name[i] = 0;
    if (!movie_open(&m, name) || !m.nvideo || !(cp = cinepak_open(m.w, m.h))) {
        port_log("movie: %s is not a Cinepak FILM file on the disc: the game's own player runs\n", name);
        movie_close(&m);
        return 0;
    }
    for (i = 0; i < m.n && !ko; i++)
        if (m.e[i].info1 == 0xffffffffu) ko = read_by(&m, &m.e[i], rd, spb) + 1u; /* the sound starts */
    if (!ko) ko = rd;
    off = (double *)malloc((size_t)m.nvideo * sizeof *off);
    ready = (u32 *)malloc((size_t)m.nvideo * 2u * sizeof *ready);
    if (!ready) ready = (u32 *)calloc(2, sizeof *ready), m.nvideo = 0; /* no memory: nothing shown, the blanks still kept */
    due = ready + m.nvideo;
    for (i = v = 0; i < m.n; i++)
        if (m.e[i].info1 != 0xffffffffu && v < m.nvideo) {
            u64 at = (u64)(m.e[i].info1 & 0x7fffffffu) * hz;
            double e = (double)((m.e[i].info1 & 0x7fffffffu) + m.e[i].info2) / m.base;
            if (e > end) end = e;
            ready[v] = read_by(&m, &m.e[i], rd, spb);
            due[v++] = ko + (u32)((at + m.base - 1u) / m.base) - 1u; /* the blank its time falls in */
        }
    if (m.rate && (double)m.audio_frames / m.rate > end) end = (double)m.audio_frames / m.rate;
    first = m.nvideo ? (ready[0] > due[0] ? ready[0] : due[0]) : ko;
    end_vb = first + (u32)(end * hz);
    if (g_audio_on && !det && m.rate && !g_out && (g_out = audio_out_open(m.rate)) != NULL) {
        g_out_rate = m.rate;
        g_dev_s = (double)audio_out_device_us(g_out) / 1e6;
        audio_out_start(g_out);
    }
    if (g_out) audio_out_ratio(g_out, (float)m.rate / (float)g_out_rate);
    port_log("movie: %s %ux%u, %u pictures, %.2f s of %u Hz sound, START skips after %u pictures, from vertical blank %u: sound from "
             "%u, first picture %u, ends %u%s\n", name, m.w, m.h, m.nvideo, (double)m.audio_frames / (m.rate ? m.rate : 1), m.rate, skip_after,
             vb0, ko, first, end_vb, det ? " (its clock is the blanks)" : "");
    for (;;) {
        u32 k = sat_vblank_count(), changed = 0;
        double made_from, now = wall();
        if (det || now - t0 >= (double)(k - vb0 + 1u) / hz) { /* the next blank: each pass in a det run, else on the clock */
            blank();
            k = sat_vblank_count();
            if (k >= ko && !playing) playing = 1, ta = now;
#ifdef SAT_MOVIE_RESET_CALL
            if (reset_at && k == reset_at) { /* the original calls it the blank after the picture */
                TcArgs r;
                memset(&r, 0, sizeof r);
                tc_call(SAT_MOVIE_RESET_CALL, &r);
            }
#endif
            if (stop ? k >= stop : k >= end_vb && (det || !g_out || t >= end)) break;
        }
        if (det) t = ((double)k + 1.0 - ko) / hz;
        else if (playing && g_out) {
            audio_push(&m, (u32)((now - ta) * m.rate) + (u32)(AHEAD_MS * m.rate / 1000u));
            double q = (double)audio_out_queued(g_out) / 4.0, at = ((double)m.pushed - q) / m.rate - g_dev_s;
            if (m.next_audio >= m.n && q == 0) at = (double)m.pushed / m.rate + (now - (drained ? drained : (drained = now)));
            if (at > t) t = at;
        } else if (playing) t = now - ta;
        made_from = wall();
        while (next < m.n && !stop && shown < m.nvideo) {
            const Entry *e = &m.e[next];
            const u8 *d;
            double ts = (double)(e->info1 & 0x7fffffffu) / m.base;
            if (e->info1 == 0xffffffffu) { next++; continue; }
            if (k < ready[shown] || (det ? k < due[shown] : !playing || ts > t)) break;
            if ((d = entry_data(&m, e)) != NULL) cinepak_frame(cp, d, e->len);
            if (off) off[shown] = (t - ts) * 1000.0;
            shown++, next++, changed = 1;
        }
        if (changed) {
            double ms;
            u32 dw, dh;
            int reset = 0;
            show(&m, cinepak_pixels(cp), &hash, &dw, &dh);
            ms = (wall() - made_from) * 1000.0;
            nmade++, slow += ms > 6.67, made_max = ms > made_max ? ms : made_max;
            dump(shown, dw, dh, canvas);
            if (nmade > skip_after && skip_asked(&reset)) stop = k + SAT_MOVIE_CLOSE_BLANKS, reset_at = reset ? k + 1u : 0u;
            if (stop && stop <= k) break;
        }
        if (!det) sat_perf_wait_begin(), pace_wait_us(2000), sat_perf_wait_end();
    }
    if (g_out) audio_out_clear(g_out);
    i = sat_vblank_count() > rd ? (sat_vblank_count() - rd) * spb : 0u; /* the sectors the original's drive has read by now */
    sat_cd_played(m.fad, m.fad + (stop && i < m.sectors ? i : m.sectors), m.fad + m.sectors);
    if (off && shown) {
        qsort(off, shown, sizeof *off, cmp_d);
        port_log("movie: %s %s at vertical blank %u (%u blanks), %u pictures shown (hash %08x); picture after sound: median %.1f ms, "
                 "%.1f..%.1f; making a picture: worst %.2f ms, %u of %u over 6.67 ms\n", name, stop ? reset_at ? "skipped by A+B+C+START" : "skipped" : "played out",
                 sat_vblank_count(), sat_vblank_count() - vb0, shown, hash, off[shown / 2], off[0], off[shown - 1], made_max, slow, nmade);
    }
    free(off), free(ready);
    cinepak_close(cp);
    movie_close(&m);
    *ret = stop ? SAT_MOVIE_SKIPPED : 0u;
    return 1;
}
#else
int sat_movie_play(const TcArgs *a, u32 *ret)
{
    (void)a, (void)ret;
    return 0;
}
u32 sat_movie_find(const char *path, u32 *sectors) { (void)path, (void)sectors; return 0; }
#endif
