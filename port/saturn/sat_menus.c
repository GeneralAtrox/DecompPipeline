/* The kit's entries in the game's own menus (game.json menus), drawn with the game's own box, cursor bar, letters and
 * sounds and read from the game's own pad edges, so they look and answer like the game's.
 *
 *   FIGHT asks "1 VS 1" or "2 VS 2" (menus fight_choice; --fight-choice on|off). When the hub's FIGHT calls the robot
 *   select with its mode, the kit holds the call and runs a small box beside the item, each frame as the hub's own loop
 *   runs one (its frame start, its draw with the cursor on FIGHT, then the box, the bar, the two lines and the game's frame
 *   step). UP and DOWN move with the game's cursor sound; A, C or START choose; B goes back to the hub as the game's own
 *   cancel does. 1 VS 1 lets the call run as before. 2 VS 2 with fight_choice "ladder" lets it run as FIGHT too, the
 *   ladder's own fight made two against two by sat_ladder.c (its opponent, a boss once, and helpers by rank; the ladder's
 *   results); without it, 2 VS 2 runs the call with the four-robot mode (the hub's TRAINING path) and, once a robot is
 *   chosen, sets the game's four-robot flag and count as that path does. Either way the team fight is on for this fight
 *   only (sat_team.c), off again when the hub is entered next. While that robot select runs, its title (the game's own
 *   string, drawn by its own routine) says the mode (fight_choice "title": its address and the words, centred in the
 *   original's room and never longer), and the original is put back when the select returns.
 *
 *   JOIN GAME and HOST GAME under the hub's own items (menus online, with game.json netplay; --online-menu on|off). The
 *   hub's box is two lines taller while the hub runs; its cursor goes on past the last item to them (DOWN, the game's
 *   cursor sound) and back (UP: the game's own move); the two lines are written after the hub's draw. A, C or START on one
 *   of them is taken from the game's newly pressed buttons before the hub reads them (the hub would run its debug entry or
 *   nothing) and the screen runs instead (sat_lobby.c). In a game those screens started (both copies, the host's choice
 *   sent to the joiner so the two hubs stay alike) the box is one line taller instead, for STOP HOSTING (sat_online.c, which
 *   also writes the hosted game's status under the hub and its notices in a fight).
 *
 * Each is on by default in an unscripted play run; off in checks, scripted and --det runs unless asked for. Read at start
 * and kept in the recording as a session note when on (the choices themselves are pad reads, so a replay makes the same
 * ones; the online screens are not run in a replay). Off, the hooks are never called.
 *
 * Nearest existing: sat_team.c (game.json names the game's functions and the kit acts around them; its names are drawn
 * with the game's own routine) and sat_logos.c (a start-time switch kept as a session note). */
#include <string.h>
#include "sat.h"
#include "sat_sys.h"
#include "sat_game.h"
#include "sat_net.h"
#include "session.h"
#include "game_log.h"
#include "host.h"

int sat_menus_on; /* sat_call.c calls in only while it is set */
static int g_fight_want = -1, g_online_want = -1; /* --fight-choice, --online-menu (-1: not given: on in a play run) */
void sat_fight_choice_option(const char *value) { g_fight_want = strcmp(value, "off") != 0; }
void sat_online_menu_option(const char *value) { g_online_want = strcmp(value, "off") != 0; }

#ifdef SAT_MENUS
enum { RIGHT = 0x8000u, LEFT = 0x4000u, DOWN = 0x2000u, UP = 0x1000u, START = 0x0800u, A = 0x0400u, C = 0x0200u, B = 0x0100u };
typedef struct {
    u32 hub, draw, frame, frame_start, frame_arg, edge, box, bar, pulse, pulse_dir, space, first, palettes, palette, map, line,
        sound, move_sound;
} MenuGame;
static const MenuGame M = SAT_MENUS;
static int g_busy; /* a kit box is running the game's routines: its own calls are not the hub's */

/* a switch read at start: the recording's note, else off in a replay, else --NAME, else on in an unscripted play run */
static int start_note(const char *note, int want, int play_run, int in_match)
{
    u8 on = 0;
    if (session_note_replayed(note, &on, 1)) on = on != 0;
    else if (session_replaying()) on = 0;
    else on = (u8)(want < 0 ? play_run && !input_scripted() : want);
    if (on && sat_net_on() && !in_match) on = 0; /* a match: two players, one against one, the kit's entries are not offered */
    if (on) session_save_read(note, &on, 1, 1);
    return on;
}

/* the kit's reads have no side effect (sat_team.c's reason) */
static u32 p16(u32 a) { return (sat_peek32(a & ~3u) >> (16u - 8u * (a & 2u))) & 0xffffu; }
static u32 p8(u32 a) { return (sat_peek32(a & ~3u) >> (24u - 8u * (a & 3u))) & 0xffu; }
static u32 edges(void) { return ~p16(M.edge) & 0xffffu; } /* the game keeps its newly pressed buttons active-low */
static u32 call(u32 fn, u32 r4, u32 r5, u32 r6, u32 r7)
{
    TcArgs c;
    memset(&c, 0, sizeof c);
    c.r[0] = r4, c.r[1] = r5, c.r[2] = r6, c.r[3] = r7;
    return tc_call(fn, &c);
}

/* a letter as the game's small type draws it ("$s": a space's cell, else the first letter's cell + 2 a letter, in the
 * game's order: digits, letters, ! . -) in the palette slot the hub's items use */
static u16 cell_in(char ch, u32 slot)
{
    u32 g = ch == ' ' ? 0u : ch >= '0' && ch <= '9' ? (u32)ch - 47u : ch >= 'A' && ch <= 'Z' ? (u32)ch - 28u : ch >= 'a' && ch <= 'z' ? (u32)ch - 60u
          : ch == '.' ? 90u : ch == '-' ? 91u : 89u;
    return (u16)((g ? M.first + 2u * g : M.space) | (p16(M.palettes + 2u * (slot & 15u)) << 12));
}
static u16 cell(char ch) { return cell_in(ch, M.palette); }
static void text(u32 x, u32 y, const char *s, u32 width)
{
    for (u32 i = 0; i < width; i++) wr16(M.map + y * M.line + 2u * (x + i), cell(*s ? *s++ : ' '));
}
void sat_menu_sound(void) { call(M.sound, M.move_sound, 0, 0, 0); }

/* a line of the small type written straight into the game's text map, `width` cells (spaces past the words), in palette
 * slot `slot` (~0u: the hub items'), outside any hub frame: sat_online.c's status lines and notices */
void sat_menu_line(u32 x, u32 y, const char *s, u32 width, u32 slot)
{
    u32 k = slot == ~0u ? M.palette : slot;
    for (u32 i = 0; i < width; i++) wr16(M.map + y * M.line + 2u * (x + i), cell_in(*s ? *s++ : ' ', k));
}

/* one frame of a kit box over the hub: the hub's frame start and draw (its cursor on `hub_row`), a box of `n` lines at
 * box[] (x, y, width) with the bar on line `sel` (none when below 0), the game's frame step; the newly pressed buttons */
u32 sat_menu_frame(u32 hub_row, const u32 box[3], const char *const *lines, int n, int sel)
{
    u32 cnt = p16(M.pulse), dir = p8(M.pulse_dir), x = box[0], y = box[1];
    g_busy++;
    call(M.frame_start, sat_peek32(M.frame_arg), 1, 0, 0);
    call(M.draw, hub_row, 0, 0, 0);
    call(M.box, x, y, box[2], (u32)n);
    if (sel >= 0) {
        call(M.bar, (u32)((s32)(8 * x) - 172) << 16, (u32)((s32)(8 * (y + (u32)sel)) - 114) << 16, box[2], 0);
        wr16(M.pulse, (u16)cnt), wr8(M.pulse_dir, (u8)dir); /* the hub's bar steps the pulse: once a frame, as before */
    }
    for (int i = 0; i < n; i++) text(x, y + 1u + (u32)i, lines[i], box[2]);
    call(M.frame, 0, 0, 0, 0);
    g_busy--;
    return edges();
}

/* the box's cells as they were, put back when it closes */
static u16 g_under[6][24];
void sat_menu_keep(const u32 box[3], int n, int put)
{
    for (int i = 0; i < n && i < 6; i++)
        for (u32 k = 0; k < box[2] && k < 24; k++) {
            u32 a = M.map + (box[1] + 1u + (u32)i) * M.line + 2u * (box[0] + k);
            if (put) wr16(a, g_under[i][k]);
            else g_under[i][k] = rd16(a);
        }
}
#endif

#if defined(SAT_MENUS) && defined(SAT_MENUS_FIGHT) && defined(SAT_TEAM_FIGHT)
typedef struct { u32 select, mode, team_mode, cursor, four, count, box[4]; } FightGame;
static const FightGame F = SAT_MENUS_FIGHT;
static int g_fight;      /* the choice is offered this run */
static int g_last;       /* the last choice (0: 1 VS 1), where the cursor starts next time */
static int g_team_wait;  /* 2 VS 2: the robot select is running with the four-robot mode */
static u32 g_asked, g_two;

/* the four-robot select's title: the kit's words over the game's own (put 0), the game's back (put 1) */
static void fight_title(int put)
{
#ifdef SAT_MENUS_FIGHT_TITLE
    static const char want[] = SAT_MENUS_FIGHT_TITLE;
    static u8 was[40];
    static u32 n;
    u32 at = SAT_MENUS_FIGHT_TITLE_AT, w = (u32)sizeof want - 1u, pad;
    if (put) {
        for (u32 i = 0; i < n; i++) wr8(at + i, was[i]);
        n = 0;
        return;
    }
    for (n = 0; n < sizeof was && p8(at + n); n++) was[n] = (u8)p8(at + n);
    if (w > n || n == sizeof was) {
        game_log_line("menus: 2 VS 2: the title \"%s\" does not fit the game's %u letters; the game's is kept", want, n);
        n = 0;
        return;
    }
    pad = (n - w) / 2u;
    for (u32 i = 0; i < n; i++) wr8(at + i, (u8)(i >= pad && i < pad + w ? want[i - pad] : ' '));
    game_log_line("menus: 2 VS 2: the robot select's title reads %s", want);
#else
    (void)put;
#endif
}

/* a choice in a kit box: the line chosen, or -1 for B */
static int choose(u32 hub_row, const u32 box[3], const char *const *lines, int n, int sel)
{
    int r;
    sat_menu_keep(box, n, 0);
    for (;;) {
        u32 e = sat_menu_frame(hub_row, box, lines, n, sel);
        int to = e & UP ? sel - 1 : e & DOWN ? sel + 1 : sel;
        if (to >= 0 && to < n && to != sel) sel = to, sat_menu_sound();
        if (e & (A | C | START)) { r = sel; break; }
        if (e & B) { r = -1; break; }
    }
    sat_menu_keep(box, n, 1);
    return r;
}

static int fight_enter(u32 fn, const TcArgs **a, TcArgs *tmp, u32 *ret)
{
    static const char *const k_fight[] = {"1 VS 1", "2 VS 2"};
    int c;
    sat_ladder_enter(fn, *a, g_team_wait);
    if (fn == M.hub) sat_team_done(); /* a 2 VS 2 is over: the next fight is the game's own again */
    if (fn != F.select || !g_fight || (*a)->s[0] != F.mode || g_team_wait) return 0;
    g_asked++;
    game_log_line("menus: FIGHT asks 1 VS 1 or 2 VS 2 (vertical blank %u)", sat_vblank_count());
    c = choose(F.cursor, F.box, k_fight, 2, g_last);
    if (c < 0) {
        game_log_line("menus: FIGHT: back to the hub");
        *ret = 0; /* as when the robot select is left with B: the hub plays its cancel sound and stays */
        return 1;
    }
    g_last = c;
    game_log_line("menus: FIGHT: %s", k_fight[c]);
#ifdef SAT_MENUS_LADDER
    (void)tmp;
    if (c == 1) g_team_wait = 1, fight_title(0); /* the game's own FIGHT: the ladder picks the opponent (sat_ladder.c) */
#else
    if (c == 1) *tmp = **a, tmp->s[0] = F.team_mode, *a = tmp, g_team_wait = 1, fight_title(0);
#endif
    return 0;
}

static u32 fight_leave(u32 fn, const TcArgs *a, u32 r)
{
#ifdef SAT_MENUS_LADDER
    const u32 mode = F.mode;
#else
    const u32 mode = F.team_mode;
#endif
    sat_ladder_leave(fn, a, g_team_wait);
    if (fn != F.select || !g_team_wait || a->s[0] != mode) return r;
    g_team_wait = 0, fight_title(1);
    if (r == 1) { /* a robot and its opponent were chosen: the ladder's fight, two against two (sat_ladder.c) */
        g_two++, sat_team_once();
#ifdef SAT_MENUS_LADDER
        sat_ladder_arm();
        game_log_line("menus: 2 VS 2: the ladder's fight, two against two, for this fight");
#else  /* a robot was chosen: the four-robot path, as the hub's TRAINING sets it, played two against two */
        wr8(F.four, 1), wr16(F.count, 4);
        game_log_line("menus: 2 VS 2: the four-robot fight, two against two, for this fight");
#endif
    }
    return r;
}
static int fight_start(int play_run) { return g_fight = start_note("*fight-choice", g_fight_want, play_run, 0); }
static void fight_end(void) { if (g_fight) port_log("menus: FIGHT asked %u times, 2 VS 2 fought %u times\n", g_asked, g_two); }
#else
static int fight_start(int play_run) { (void)play_run, (void)g_fight_want; return 0; }
static int fight_enter(u32 fn, const TcArgs **a, TcArgs *tmp, u32 *ret) { (void)fn, (void)a, (void)tmp, (void)ret; return 0; }
static u32 fight_leave(u32 fn, const TcArgs *a, u32 r) { (void)fn, (void)a; return r; }
static void fight_end(void) {}
#endif

#if defined(SAT_MENUS) && defined(SAT_MENUS_ONLINE) && defined(SAT_NETPLAY)
typedef struct { u32 move, box_h, x, y, rows, box[3]; } OnlineGame;
static const OnlineGame O = SAT_MENUS_ONLINE;
static int g_online, g_in_hub, g_pass; /* g_pass: the hub's cursor moved this pass; its frame step comes next */
static u32 g_extra = 2; /* the hub's lines under its items: JOIN GAME and HOST GAME, or STOP HOSTING in a match */
static u32 g_hub_h = ~0u, g_cur_at, g_cur_before, g_opened;

static void online_enter(u32 fn, const TcArgs *a)
{
    if (fn == M.hub) {
        g_in_hub = 1;
        if (g_hub_h == ~0u) g_hub_h = p16(O.box_h);
        wr16(O.box_h, (u16)(g_hub_h + g_extra)); /* the hub's box takes the lines */
    } else if (fn == O.move && g_in_hub) g_cur_at = a->r[0], g_cur_before = p16(g_cur_at);
}

static void online_leave(u32 fn)
{
    u32 cur;
    if (sat_net_on()) sat_online_call(fn);
    if (fn == M.hub) {
        g_in_hub = 0, g_pass = 0;
        if (g_hub_h != ~0u) wr16(O.box_h, (u16)g_hub_h);
        if (sat_net_on()) sat_online_hub(0, 0, 0);
        return;
    }
    if (!g_in_hub) return;
    if (fn == M.draw) {
        if (sat_net_on()) sat_online_hub(O.x, O.y + O.rows, 1);
        else text(O.x, O.y + O.rows, "JOIN GAME", 9), text(O.x, O.y + O.rows + 1u, "HOST GAME", 9);
    } else if (fn == O.move) { /* the game's move stops at its last item: on to the kit's lines, with its sound */
        cur = p16(g_cur_at);
        if ((edges() & DOWN) && cur == g_cur_before && cur + 1u >= O.rows && cur + 1u < O.rows + g_extra) wr16(g_cur_at, (u16)(cur + 1u)), sat_menu_sound();
        g_pass = 1;
    } else if (fn == M.frame && g_pass) { /* the hub's frame step: its newly pressed buttons are read next */
        g_pass = 0;
        cur = p16(g_cur_at);
        sat_lobby_note(cur, O.box); /* a match that ended: why, once */
        if (cur < O.rows || !(edges() & (A | C | START))) return;
        wr16(M.edge, (u16)(p16(M.edge) | A | C | START)); /* not the hub's: its entry 12 is a debug screen */
        g_opened++;
        if (sat_net_on()) sat_online_choose();
        else if (session_replaying()) game_log_line("menus: %s: not run while a recording is replayed", cur == O.rows ? "JOIN GAME" : "HOST GAME");
        else if (cur == O.rows) sat_lobby_join(cur, O.box);
        else sat_lobby_host(cur, O.box);
        wr16(M.edge, 0xffffu); /* nothing newly pressed when the hub goes on */
    }
}
static int online_start(int play_run)
{
    g_extra = sat_net_on() ? 1u : 2u;
    return g_online = start_note("*online-menu", g_online_want, play_run, sat_lobby_match);
}
static void online_end(void) { if (g_online) port_log("menus: JOIN GAME or HOST GAME opened %u times\n", g_opened); }
static int online_is(void) { return g_online; }
#else
static int online_start(int play_run) { (void)play_run, (void)g_online_want, (void)sat_lobby_match; return 0; }
static void online_enter(u32 fn, const TcArgs *a) { (void)fn, (void)a; }
static void online_leave(u32 fn) { (void)fn; }
static void online_end(void) {}
static int online_is(void) { return 0; }
#endif

void sat_menus_start(int play_run)
{
    int fight = fight_start(play_run), online = online_start(play_run);
    sat_menus_on = fight || online;
    port_log("menus: %s; %s\n", fight ? "FIGHT asks 1 VS 1 or 2 VS 2 (the game's own box)" : "FIGHT is the game's own (1 VS 1)",
             online ? "JOIN GAME and HOST GAME in the hub" : "no online entries in the hub");
}

#ifdef SAT_MENUS
/* sat_call.c, before a call: 1 and *ret when the kit answers it; *a may become a changed copy in *tmp */
int sat_menus_enter(u32 fn, const TcArgs **a, TcArgs *tmp, u32 *ret)
{
    if (g_busy || sat_cpu_id() != 0) return 0;
    online_enter(fn, *a);
    return fight_enter(fn, a, tmp, ret);
}

/* and after it: its result */
u32 sat_menus_leave(u32 fn, const TcArgs *a, u32 r)
{
    if (g_busy || sat_cpu_id() != 0) return r;
    online_leave(fn);
    return fight_leave(fn, a, r);
}
#else
int sat_menus_enter(u32 fn, const TcArgs **a, TcArgs *tmp, u32 *ret) { (void)fn, (void)a, (void)tmp, (void)ret; return 0; }
u32 sat_menus_leave(u32 fn, const TcArgs *a, u32 r) { (void)fn, (void)a; return r; }
#endif

void sat_menus_end(void) { fight_end(), online_end(); }
int sat_menus_online(void) { return online_is(); }
