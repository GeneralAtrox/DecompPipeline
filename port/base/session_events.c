/* The session recording's sixth section, EVENTS (session.c's file comment): what a game takes from the window beside its
 * pads, at the tick's pad read (input.c events_read; native_game.h): the text typed into the game's own field, the mouse
 * given straight to the game (PG_MOUSE_POINTER, PG_MOUSE_LOOK) and the picks of the game's own menu commands.
 *   Each entry: u32 pad read, u8 kind, u8 length, then the payload: TEXT the UTF-8 bytes (a longer tick's text is two
 *   entries); MOUSE dx, dy, x, y (the f32 bits), buttons, pressed (u32 each), wheel (s32), captured (u8); MENU tag and
 *   index (s32 each). A mouse entry is written only when it moved, pressed, turned or changed, text and picks only when
 *   there are some: a game that never asks writes the five-section file it always did. Once there is an entry the fifth
 *   section is written even when empty and the header says mark=events1.
 * In a replay the recording's are given and the window's dropped (so a replay never reads the clipboard, the mouse or a
 * menu pick); a recording without the section gives none. Split from session.c at its 400-line cap.
 * Nearest existing: session_other.c - the fifth section, the same kind of split, but fixed-size pad entries. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "base.h"
#include "console.h"
#include "session.h"
#include "session_priv.h"

enum { EV_TEXT = 1, EV_MOUSE = 2, EV_MENU = 3 };
#define MOUSE_LEN 29u
#define MAX_BYTES (256u << 20)

static Buf g_rec, g_play;
static int g_has, g_marked;
static SessionEvents g_tick;               /* this tick's, for the game */
static int g_text_at, g_menu_at;
static PgNativeMouse g_rec_last, g_play_last; /* the last mouse recorded, and replayed */

static void put(u32 read, int kind, const void *p, u32 len)
{
    u8 h[6];
    memcpy(h, &read, 4);
    h[4] = (u8)kind, h[5] = (u8)len;
    if (!g_marked) g_marked = 1, session_mark("events1");
    session_put(&g_rec, h, 6);
    session_put(&g_rec, p, len);
}

static void mouse_bytes(const PgNativeMouse *m, u8 out[MOUSE_LEN])
{
    memcpy(out, &m->dx, 4), memcpy(out + 4, &m->dy, 4), memcpy(out + 8, &m->x, 4), memcpy(out + 12, &m->y, 4);
    memcpy(out + 16, &m->buttons, 4), memcpy(out + 20, &m->pressed, 4), memcpy(out + 24, &m->wheel, 4);
    out[28] = m->captured;
}

static void record(u32 read)
{
    const PgNativeMouse *m = &g_tick.mouse;
    for (int at = 0; at < g_tick.text_n; at += 255) put(read, EV_TEXT, g_tick.text + at, (u32)(g_tick.text_n - at > 255 ? 255 : g_tick.text_n - at));
    if (m->dx != 0.0f || m->dy != 0.0f || m->pressed || m->wheel || m->buttons != g_rec_last.buttons ||
        m->captured != g_rec_last.captured || memcmp(&m->x, &g_rec_last.x, 4) || memcmp(&m->y, &g_rec_last.y, 4)) {
        u8 b[MOUSE_LEN];
        mouse_bytes(m, b);
        put(read, EV_MOUSE, b, MOUSE_LEN);
        g_rec_last = *m;
    }
    for (int i = 0; i < g_tick.menu_n; i++) {
        s32 e[2] = {g_tick.menu[i][0], g_tick.menu[i][1]};
        put(read, EV_MENU, e, 8);
    }
}

/* The recording's entries for this read (and any it passed), into the tick. */
static void replay(u32 read)
{
    PgNativeMouse *m = &g_tick.mouse;
    *m = g_play_last; /* held: buttons, the pointer, captured; this read's only: moves, presses, notches */
    m->dx = m->dy = 0.0f, m->pressed = 0, m->wheel = 0;
    while (g_play.at + 6u <= g_play.n) {
        const u8 *h = g_play.p + g_play.at, *p = h + 6;
        u32 at, len = h[5];
        memcpy(&at, h, 4);
        if (at > read || g_play.at + 6u + len > g_play.n) break;
        g_play.at += 6u + len;
        if (h[4] == EV_TEXT && g_tick.text_n + (int)len <= SESSION_TEXT_MAX) {
            memcpy(g_tick.text + g_tick.text_n, p, len);
            g_tick.text_n += (int)len;
        } else if (h[4] == EV_MOUSE && len == MOUSE_LEN) {
            memcpy(&m->dx, p, 4), memcpy(&m->dy, p + 4, 4), memcpy(&m->x, p + 8, 4), memcpy(&m->y, p + 12, 4);
            memcpy(&m->buttons, p + 16, 4), memcpy(&m->pressed, p + 20, 4), memcpy(&m->wheel, p + 24, 4);
            m->captured = p[28];
            g_play_last = *m;
        } else if (h[4] == EV_MENU && len == 8u && g_tick.menu_n < SESSION_MENU_MAX) {
            s32 e[2];
            memcpy(e, p, 8);
            g_tick.menu[g_tick.menu_n][0] = e[0], g_tick.menu[g_tick.menu_n][1] = e[1];
            g_tick.menu_n++;
        }
    }
}

void session_events_read(SessionEvents *live)
{
    u32 read = session_reads() ? session_reads() - 1u : 0u; /* session_pad has counted this read */
    if (port_extra_drawing()) return;
    g_text_at = g_menu_at = 0;
    if (session_replaying()) {
        memset(&g_tick, 0, sizeof g_tick);
        g_tick.mouse_on = live->mouse_on; /* the mode is the game's own: the same in the replay */
        replay(read);
    } else {
        g_tick = *live;
    }
    record(read); /* a replay's recording keeps them too */
}

int session_events_replayed(void) { return g_has && session_replaying(); }
int session_events_any(void) { return g_rec.n != 0; }

int session_events_write(FILE *f)
{
    return !g_rec.n || (fwrite(&g_rec.n, 4, 1, f) == 1 && fwrite(g_rec.p, g_rec.n, 1, f) == 1);
}

int session_events_load(FILE *f)
{
    u32 n = 0;
    if (fread(&n, 4, 1, f) != 1) return 1; /* none: a recording from before the section, or without events */
    if (n > MAX_BYTES || (g_play.p = (u8 *)malloc(n + 1u)) == NULL || (n && fread(g_play.p, n, 1, f) != 1)) return 0;
    g_play.n = n, g_play.at = 0, g_has = 1;
    port_log("session: replay uses text/mouse events (%u bytes): the window's text, mouse and menu picks are ignored\n", n);
    return 1;
}

/* ---- the game's side (native_game.h) ---- */
int pg_native_text_take(char *utf8, int cap)
{
    int n = g_tick.text_n - g_text_at;
    if (!utf8 || cap < 2 || n <= 0) {
        if (utf8 && cap > 0) utf8[0] = 0;
        return 0;
    }
    if (n > cap - 1) { /* whole characters: never a UTF-8 sequence cut */
        n = cap - 1;
        while (n > 0 && ((u8)g_tick.text[g_text_at + n] & 0xc0) == 0x80) n--;
        if (!n) return utf8[0] = 0, 0;
    }
    memcpy(utf8, g_tick.text + g_text_at, (size_t)n);
    utf8[n] = 0;
    g_text_at += n;
    return n;
}

int pg_native_mouse(PgNativeMouse *m)
{
    if (!m) return 0;
    if (!g_tick.mouse_on) {
        memset(m, 0, sizeof *m);
        return 0;
    }
    *m = g_tick.mouse;
    return 1;
}

int pg_native_menu_take(int *tag, int *index)
{
    if (g_menu_at >= g_tick.menu_n) return 0;
    if (tag) *tag = g_tick.menu[g_menu_at][0];
    if (index) *index = g_tick.menu[g_menu_at][1];
    g_menu_at++;
    return 1;
}
