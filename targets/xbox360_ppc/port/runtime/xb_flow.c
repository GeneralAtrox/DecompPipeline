/* The game's screen flow, in the log and the title bar: which movie plays (its name, the frames shown with its picture, how long), when the
 * title and the main menu are reached, and loading. Each comes from the game's own markers:
 * - a movie: its file opened and closed (xb_file.c) and each shown frame its quad drew in (xb_gpu_draw.h, pixel shader XB_MOVIE_PS);
 * - the screens: the C functions the game's menu scripts call (XB_FLOW_CALLS in xb_game.h, met in tc_call), whose text names the screen
 *   (XB_FLOW_STATES): each call is a "game:" line, each screen a "state:" line;
 * - loading: the game's file opens, from the first after a quiet second to the next quiet second.
 * The title bar shows the movie, else loading, else the screen, after the frame rate (host_window_title_extra).
 * Nearest existing: xb_file.c's "opened at" line (a movie's start only) and XB_TRACE (every call of a function, for bring-up). */
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include "xb.h"
#include "xb_game.h"
#include "console.h"
#include "host.h"

#define QUIET_BLANKS 60u   /* a second of vertical blanks with no file opened ends a load */
#define MOVIES 4           /* movies open at once: the title's background movie opens while a logo plays */

typedef struct {
    char name[64];
    int open, played;      /* its file's open handles; started by the game's playmovie (stopmovie ends it) */
    u32 from, frames, pictures;
    ULONGLONG t;
} Movie;

static SRWLOCK s_lock = SRWLOCK_INIT;
static ULONGLONG s_t0;
static Movie s_mv[MOVIES];
static char s_screen[48], s_played[64];
static int s_loading;
static u32 s_load_from, s_load_last, s_load_files;
static ULONGLONG s_load_t;
static volatile LONG s_picture;

static double secs(ULONGLONG since) { return (GetTickCount64() - since) / 1000.0; }

static void show(void)   /* under s_lock: the newest movie, else loading, else the screen */
{
    char t[96];
    const Movie *m = NULL;
    int i;
    for (i = 0; i < MOVIES; i++)
        if (s_mv[i].name[0] && (!m || s_mv[i].from >= m->from)) m = &s_mv[i];
    if (m) sprintf_s(t, sizeof t, "movie %s", m->name);
    else if (s_loading) sprintf_s(t, sizeof t, s_screen[0] ? "%s, loading" : "loading", s_screen);
    else sprintf_s(t, sizeof t, "%s", s_screen);
    host_window_title_extra(t);
}

static void auto_marker(const char *text);

static void movie_end(Movie *m, const char *why)   /* under s_lock */
{
    auto_marker("movie end");   /* XB_AUTO_A may start at a movie's end: Too Human's IntroSoundStart comes only after a press */
    if (!m->name[0]) return;
    port_log("state: movie end %s (%s) at %.1f s, vertical blank %u: %u frames shown, %u with a movie picture, %.1f s from vertical blank %u\n", m->name,
             why, secs(s_t0), port_vblank_count(), m->frames, m->pictures, secs(m->t), m->from);
    memset(m, 0, sizeof *m);
}

static Movie *movie_named(const char *base)
{
    int i;
    for (i = 0; i < MOVIES; i++)
        if (s_mv[i].name[0] && !_stricmp(s_mv[i].name, base)) return &s_mv[i];
    return NULL;
}

static const char *base_of(const char *path)
{
    const char *b = strrchr(path, '\\');
    return b ? b + 1 : path;
}

static LONG s_files_opened;

unsigned xb_flow_file_count(void) { return (unsigned)s_files_opened; }

void xb_flow_file(const char *name)
{
    /* start-up timing, log only: with "window: first frame shown" it tells a slow first load apart from a slow window (the 47.6 s, 2026-11-09) */
    if (InterlockedIncrement(&s_files_opened) == 1)
        port_log("load: first game file opened %.0f ms into the run: %s\n", (double)clock() * 1000.0 / CLOCKS_PER_SEC, name ? name : "?");
    {   /* XB_SLOW_FILES=<ms>, a test: every file open waits that long, a slow disc's first load on a quick one (the window answering test) */
        static int ms = -1;
        if (ms < 0) { const char *e = getenv("XB_SLOW_FILES"); ms = e ? atoi(e) : 0; }
        if (ms > 0) Sleep((DWORD)ms);
    }
    size_t len = strlen(name);
    AcquireSRWLockExclusive(&s_lock);
    if (!s_t0) s_t0 = GetTickCount64();
    if (len > 4 && !_stricmp(name + len - 4, ".wmv")) {
        const char *base = base_of(name);
        Movie *m = movie_named(base);
        int i;
        for (i = 0; !m && i < MOVIES; i++)
            if (!s_mv[i].name[0]) {
                m = &s_mv[i];
                strncpy_s(m->name, sizeof m->name, base, _TRUNCATE);
                m->from = port_vblank_count(), m->t = GetTickCount64(), m->played = !_stricmp(s_played, base);
                port_log("state: movie start %s at %.1f s, vertical blank %u%s\n", m->name, secs(s_t0), m->from, m->played ? "" : " (not by playmovie)");
                show();
            }
        if (m) m->open++;
    } else {
        if (!s_loading) {
            s_loading = 1, s_load_from = port_vblank_count(), s_load_files = 0, s_load_t = GetTickCount64();
            port_log("state: loading start (%s) at %.1f s, vertical blank %u\n", base_of(name), secs(s_t0), s_load_from);
            show();
        }
        s_load_files++;
        s_load_last = port_vblank_count();
    }
    ReleaseSRWLockExclusive(&s_lock);
}

void xb_flow_close(const char *path)
{
    size_t len = strlen(path);
    Movie *m;
    if (len < 4 || _stricmp(path + len - 4, ".wmv")) return;
    AcquireSRWLockExclusive(&s_lock);
    if ((m = movie_named(base_of(path))) && --m->open <= 0) {
        movie_end(m, "its file closed");
        show();
    }
    ReleaseSRWLockExclusive(&s_lock);
}

static void played_end(const char *why)   /* under s_lock: the movies the game's playmovie started */
{
    int i;
    for (i = 0; i < MOVIES; i++)
        if (s_mv[i].played) movie_end(&s_mv[i], why);
}

void xb_flow_movie_picture(void) { s_picture = 1; }

void xb_flow_frame(void)
{
    LONG pic = InterlockedExchange(&s_picture, 0);
    int i, any = 0;
    for (i = 0; i < MOVIES; i++) any |= s_mv[i].name[0] != 0;
    if (!any && !s_loading) return;
    AcquireSRWLockExclusive(&s_lock);
    for (i = 0; i < MOVIES; i++)
        if (s_mv[i].name[0]) s_mv[i].frames++, s_mv[i].pictures += pic != 0;
    if (s_loading && port_vblank_count() - s_load_last >= QUIET_BLANKS) {
        s_loading = 0;
        port_log("state: loading end at %.1f s, vertical blank %u: %u files opened, %.1f s from vertical blank %u\n", secs(s_t0), port_vblank_count(),
                 s_load_files, secs(s_load_t), s_load_from);
        show();
    }
    ReleaseSRWLockExclusive(&s_lock);
}

#ifdef XB_FLOW_CALLS
/* Argument i (from 1) of a call into one of the game's Lua C functions, when it is a string: the stack's base at L+0xc and top at L+8,
 * 8-byte values of a type (4 is a string) and a value, a string's characters 16 bytes into it (Lua 5.0 with a 4-byte number). The other
 * order (Lua 5.1's, the value first) is taken too */
static int lua_text(u32 L, unsigned i, char *out, unsigned size)
{
    u32 top = rd32(L + 8), base = rd32(L + 0xc), tv, s;
    unsigned j;
    out[0] = 0;
    if (!i || base + 8u * i > top || top - base > 0x1000u) return 0;
    tv = base + 8u * (i - 1u);
    if (rd32(tv) == 4u) s = rd32(tv + 4);
    else if (rd32(tv + 4) == 4u) s = rd32(tv);
    else s = 0;
    if (!s) {
        sprintf_s(out, size, "(not a string: %08x %08x)", rd32(tv), rd32(tv + 4));
        return 0;
    }
    for (j = 0; j + 1 < size; j++) {
        u8 c = rd8(s + 16 + j);
        if (!c || c == '\n') break;
        out[j] = c < 32 || c > 126 ? '?' : (char)c;
    }
    out[j] = 0;
    return 1;
}

/* XB_AUTO_A="<start>><stop>" (the front-end check, never a play run): pad 1's A is pressed for 6 vertical blanks of every 60, from 45 after a
 * "game:" line or screen whose text holds <start> until one holds <stop>. A recording's presses sit at fixed vertical blanks, and a build that
 * shows frames faster reaches them before the game is ready (to_menu.ptrec's A at 950 came 3 blanks before the title's load began once draws got
 * cheaper, 2026-11-11); presses that follow the game's own markers work at any speed. Too Human: "movie end>IntroSoundStop" skips the
 * rest of the movies and the intro scene and takes the title, and stops before the main menu ("movie end" is the text a movie's end gives) */
static char s_auto_start[48], s_auto_stop[48];
static volatile LONG s_auto_on, s_auto_set = -1;
static u32 s_auto_from;

int xb_flow_auto_set(void)
{
    if (s_auto_set < 0) {
        const char *e = getenv("XB_AUTO_A"), *gt = e ? strchr(e, '>') : NULL;
        if (gt && gt > e && gt[1] && (size_t)(gt - e) < sizeof s_auto_start && strlen(gt + 1) < sizeof s_auto_stop) {
            memcpy(s_auto_start, e, (size_t)(gt - e));
            strcpy_s(s_auto_stop, sizeof s_auto_stop, gt + 1);
            port_log("auto press: A on pad 1 from \"%s\" until \"%s\" (XB_AUTO_A)\n", s_auto_start, s_auto_stop);
            s_auto_set = 1;
        } else s_auto_set = 0;
    }
    return s_auto_set;
}

int xb_flow_auto_a(void)
{
    u32 d;
    if (!s_auto_on) return 0;
    d = port_vblank_count() - s_auto_from;
    return d >= 45u && (d - 45u) % 60u < 6u;
}

static void auto_marker(const char *text)
{
    if (!xb_flow_auto_set()) return;
    if (!s_auto_on && s_auto_set == 1 && strstr(text, s_auto_start)) {
        s_auto_from = port_vblank_count(), s_auto_on = 1;
        port_log("auto press: started at \"%s\", vertical blank %u\n", text, s_auto_from);
    } else if (s_auto_on && strstr(text, s_auto_stop)) {
        s_auto_on = 0, s_auto_set = 2;   /* once */
        port_log("auto press: stopped at \"%s\", vertical blank %u\n", text, port_vblank_count());
    }
}

void xb_flow_call(u32 target, const TcArgs *a)
{
    static const struct { u32 fn; unsigned arg; const char *what; } calls[] = {XB_FLOW_CALLS};
    static const struct { const char *text, *state; } states[] = {XB_FLOW_STATES};
    static volatile LONG said;
    char text[96];
    unsigned i, k;
    for (i = 0; i < sizeof calls / sizeof calls[0] && calls[i].fn != target; i++) {}
    if (i == sizeof calls / sizeof calls[0]) return;
    if (calls[i].arg) (void)lua_text((u32)a->r[0], calls[i].arg, text, sizeof text);
    else text[0] = 0;
    if (calls[i].arg && strstr(text, ".wmv")) {   /* playmovie: the file it opens next is the game's movie */
        AcquireSRWLockExclusive(&s_lock);
        strncpy_s(s_played, sizeof s_played, text, _TRUNCATE);
        ReleaseSRWLockExclusive(&s_lock);
    }
    if (InterlockedIncrement(&said) <= 4000) port_log("game: %s %s (vertical blank %u)\n", calls[i].what, text, port_vblank_count());
    auto_marker(text);
    if (!calls[i].arg && strstr(calls[i].what, "stop")) {   /* stopmovie: the game ended the movie (a press skips it) */
        AcquireSRWLockExclusive(&s_lock);
        played_end("the game stopped it");
        show();
        ReleaseSRWLockExclusive(&s_lock);
    }
    for (k = 0; k < sizeof states / sizeof states[0]; k++)
        if (strstr(text, states[k].text) && strcmp(s_screen, states[k].state)) {
            AcquireSRWLockExclusive(&s_lock);
            if (!s_t0) s_t0 = GetTickCount64();
            played_end(states[k].state);
            strcpy_s(s_screen, sizeof s_screen, states[k].state);
            port_log("state: %s reached at %.1f s, vertical blank %u (game: %s %s)\n", s_screen, secs(s_t0), port_vblank_count(), calls[i].what, text);
            show();
            ReleaseSRWLockExclusive(&s_lock);
            auto_marker(s_screen);
            break;
        }
}
#else
int xb_flow_auto_set(void) { return 0; }
int xb_flow_auto_a(void) { return 0; }
#endif
