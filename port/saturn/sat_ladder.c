/* 2 VS 2 as a fight on the game's own FIGHT ladder (game.json menus fight_choice "ladder"; sat_menus.c's 2 VS 2). The
 * game's FIGHT runs as in 1 VS 1: its robot select and SELECT ENEMY pick the ladder's opponent into seat 1, and the fight
 * set-up swaps a boss in for it (its copy in slot 121, its arena, the ending for the last one). Then, before the set-up
 * loads the robots, the kit makes the fight two against two:
 *
 *   seats      side A is seats 0 to N/2-1 (the player, then teammates), side B is N/2 to N-1 (sat_team.c's sides). The
 *              ladder's opponent (the boss, once) goes in seat N/2; helpers fill the other seats, enemies first. N is
 *              fight_choice "ladder" seats (4; 6 and 8 take the same rule).
 *   helpers    from the game's ranking (its sorted list), nearest the opponent's rank: one below, one above, two below,
 *              and so on. A helper is a ladder robot (in use, listed as an opponent, not special: never a boss), not the
 *              player's robot, not the opponent and not one already picked, so no robot fights twice.
 *   the fight  the game's four-robot fight, as TRAINING runs it (its flag, its count, the CPU flags of seats 1 on), so
 *              sat_team.c plays it two against two. The arena and the boss are the ladder's (the flag is set after them).
 *   results    when the fight is over and the game's results begin, seat 1 is the opponent again, the four-robot flag
 *              is clear and robots 0 and 1's out flags say whether each side is out (as sat_team.c shows the game's own
 *              fight-over test), so the game's own ladder results run: the points, the rank and, after the last boss, the
 *              ending.
 *   SELECT ENEMY  above the information panel, the helpers that would come with the robot the cursor is on: HELPER
 *              (theirs) and PARTNER (yours), the names drawn by the game's own name routine, again whenever the cursor
 *              moves (the select's step runs each frame; the list's top and cursor give the robot). A boss keeps its own
 *              panel (NO DATA).
 *
 * The game.json words: setup (the fight set-up), at (a call the set-up makes after its boss swap and before it loads the
 * robots), after (the fight's results), robot (the set-up of one robot, its seat in r4), draw (SELECT ENEMY's draw of the
 * robot under the cursor: seat in r4, record in r5), records, record (its size), sorted and sorted_n (the ranking and its
 * length), seats, flags (the robots' flags, their step, the CPU and out flags' offsets), boss (the set-up's boss-swapped flag and the opponent it kept),
 * won (the fight's won flag), fields (in use, listed, rank, special and its kind: offsets in a record), copies (the first slot the
 * game copies robots into: not a ladder robot), name_draw, show (x, y of the two lines), energy (energy and robot step, for
 * --ladder-test-weak), size (the robots in the fight), list (the opponent list's builder, the player's record in r6) and
 * cursor (SELECT ENEMY's step each frame, mode in r6; the list's top and cursor; past the step's r7, the list's address).
 * --ladder-test-rank N (a test): while the opponent list is built the player's rank reads N (its own again after), so a
 * scripted run reaches a boss's part of the ladder; a note in the log.
 * --ladder-test-weak on (a test): side B's robots start a ladder fight with 1 energy, so a scripted run can win it; a
 * note in the log. After every ladder fight's results the log has the player's rank before and after and which bytes of
 * its record changed (the points and the rank), 1 VS 1 and 2 VS 2 alike.
 *
 * Nearest existing: sat_team.c's names (the game's own routine draws a seat's name) and the hub's TRAINING path (seats 1
 * to 3 filled from the game's ranking, at 0x06040640 in Robo Pit), which picks at random, may repeat and copies the
 * player's level; this keeps the ladder's opponent and picks by rank. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "sat.h"
#include "sat_sys.h"
#include "sat_game.h"
#include "game_log.h"

#if defined(SAT_MENUS) && defined(SAT_MENUS_LADDER)
typedef struct {
    u32 select, setup, at, after, robot, draw, records, record, copies, sorted, sorted_n, seats, cpu, out, cpu_step, boss, boss_rec;
    u32 won, in_use, listed, rank, special, kind, name, name_draw, x, y, energy, energy_step, n, four, count;
    u32 list, step, top, cur, step_list;
} LadderGame;
static const LadderGame G = SAT_MENUS_LADDER;
enum { MAXN = 8 };
static int g_armed, g_fight, g_in_setup, g_shown, g_weak;
static u32 g_player, g_rank0, g_cmp_n, g_shown_opp = ~0u, g_specials, g_test_rank, g_rank_rec = ~0u, g_rank_was;
static u8 g_before[128];

void sat_ladder_test_option(const char *value) { g_weak = strcmp(value, "off") != 0; }
void sat_ladder_rank_option(const char *value) { g_test_rank = (u32)strtoul(value, NULL, 10); }
static u32 p16(u32 a) { return (sat_peek32(a & ~3u) >> (16u - 8u * (a & 2u))) & 0xffffu; }
static u32 p8(u32 a) { return (sat_peek32(a & ~3u) >> (24u - 8u * (a & 3u))) & 0xffu; }
static u32 rec(u32 r) { return G.records + r * G.record; }
static u32 seat(u32 k) { return p16(G.seats + 2u * k); }
static int ladder_robot(u32 r) { return r < G.copies && p8(rec(r) + G.in_use) && p8(rec(r) + G.listed) == 1 && !p8(rec(r) + G.special); }

/* the helpers for the opponent opp (its own record, a boss's too), nearest its rank first; returns how many */
static int helpers(u32 opp, u32 player, u32 *out, int want)
{
    u32 n = p16(G.sorted_n), c = p16(rec(opp) + G.rank);
    int got = 0;
    c = c ? c - 1u : 0u;
    if (n > 200u) n = 200u;
    for (u32 d = 1; d < 2u * n && got < want; d++) {
        u32 i = d & 1u ? c + (d + 1u) / 2u : c - d / 2u, r; /* one below in rank, then one above, then two below, ... */
        int seen = 0;
        if ((d & 1u) == 0 && d / 2u > c) continue;
        if (i >= n) continue;
        r = p16(G.sorted + 2u * i);
        for (int k = 0; k < got; k++) seen |= out[k] == r;
        if (seen || r == opp || r == player || !ladder_robot(r)) continue;
        out[got++] = r;
    }
    return got;
}

static void name_at(u32 r, u32 x, u32 y)
{
    TcArgs c;
    memset(&c, 0, sizeof c);
    c.r[0] = rec(r) + G.name, c.r[1] = x, c.r[2] = y, c.s[0] = 1;
    tc_call(G.name_draw, &c);
}

/* SELECT ENEMY: the robot under the cursor is opp; its helpers under the list (or the lines cleared, show 0) */
static void show(u32 opp, int on)
{
    u32 h[MAXN];
    int n = on ? helpers(opp, seat(0), h, (int)G.n - 2) : 0;
    sat_menu_line(G.x, G.y, on ? "HELPER" : "", 20, ~0u), sat_menu_line(G.x, G.y + 1u, on ? "PARTNER" : "", 20, ~0u);
    if (n >= 1) name_at(h[0], G.x + 8u, G.y);
    if (n >= 2) name_at(h[1], G.x + 8u, G.y + 1u);
    if (on && !g_specials++) /* once: where the ladder's special robots (its bosses) stand */
        for (u32 r = 0; r < G.copies; r++)
            if (p8(rec(r) + G.in_use) && p8(rec(r) + G.special))
                game_log_line("ladder: special robot %u: rank %u, kind %u, listed %u", r, p16(rec(r) + G.rank), p8(rec(r) + G.kind),
                              p8(rec(r) + G.listed));
    if (on && opp != g_shown_opp)
        game_log_line("ladder: SELECT ENEMY at vertical blank %u: robot %u (rank %u%s): helpers %d: %u %u", sat_vblank_count(), opp,
                      p16(rec(opp) + G.rank), p8(rec(opp) + G.special) ? ", special: a boss" : "", n, n > 0 ? h[0] : 0u, n > 1 ? h[1] : 0u);
    g_shown = on, g_shown_opp = on ? opp : ~0u;
}

/* in the set-up, after its boss swap: the seats for two against two */
static void arrange(void)
{
    u32 opp = seat(1), own = p8(G.boss) == 1 ? p16(G.boss_rec) : opp, h[MAXN], half = G.n / 2u, k, j = 0;
    int n = helpers(own, seat(0), h, (int)G.n - 2);
    if (n < (int)G.n - 2) {
        game_log_line("ladder: 2 VS 2: only %d helpers for opponent %u; this fight is one against one", n, own);
        return;
    }
    wr16(G.seats + 2u * half, (u16)opp);
    for (k = half + 1u; k < G.n; k++) wr16(G.seats + 2u * k, (u16)h[j++]); /* the enemy side first */
    for (k = 1; k < half; k++) wr16(G.seats + 2u * k, (u16)h[j++]);
    for (k = 1; k < G.n; k++) wr8(G.cpu + k * G.cpu_step, 1);
    wr16(G.count, (u16)G.n), wr8(G.four, 1);
    g_fight = 1;
    game_log_line("ladder: 2 VS 2 at vertical blank %u: the ladder's opponent %u%s (rank %u) in seat %u; seats 0-%u: %u %u %u %u",
                  sat_vblank_count(), own, opp != own ? " as the boss in slot 121" : "", p16(rec(own) + G.rank), half, G.n - 1u,
                  seat(0), seat(1), seat(2), seat(3));
}

void sat_ladder_arm(void) { g_armed = 1; }

/* sat_menus.c, before a call; choosing: the 2 VS 2 robot select is running */
void sat_ladder_enter(u32 fn, const TcArgs *a, int choosing)
{
    if (fn == G.setup && g_armed) g_in_setup = 1;
    else if (fn == G.at && g_in_setup && g_armed) g_armed = 0, arrange();
    else if (fn == G.step && choosing && a->r[2] == 1u && g_shown) { /* SELECT ENEMY, each frame: the robot under the cursor */
        u32 r = p16(sat_peek32(a->r[3] + G.step_list) + 2u * (p16(G.top) + p16(G.cur)));
        if (r != g_shown_opp && r < 200u) show(r, 1);
    } else if (fn == G.list && g_test_rank && a->r[2] < G.copies) {
        g_rank_rec = a->r[2], g_rank_was = p16(rec(g_rank_rec) + G.rank);
        wr16(rec(g_rank_rec) + G.rank, (u16)g_test_rank);
        game_log_line("ladder: TEST (--ladder-test-rank): the opponent list built as if robot %u were rank %u (it is %u)",
                      g_rank_rec, g_test_rank, g_rank_was);
    }
    else if (fn == G.after && seat(0) < G.copies) {
        g_player = seat(0), g_rank0 = p16(rec(g_player) + G.rank), g_cmp_n = G.record < (u32)sizeof g_before ? G.record : (u32)sizeof g_before;
        for (u32 i = 0; i < g_cmp_n; i++) g_before[i] = (u8)p8(rec(g_player) + i);
        game_log_line("ladder: results of a %s fight: won %u, opponent %u, player %u at rank %u", g_fight ? "2 VS 2" : "1 VS 1",
                      p8(G.won), g_fight ? seat(G.n / 2u) : seat(1), g_player, g_rank0);
        if (g_fight) { /* the game's own ladder results, for the opponent: robot 0 out if side A is, robot 1 if side B is */
            u32 a_out = 1, b_out = 1, k;
            for (k = 0; k < G.n; k++) *(k < G.n / 2u ? &a_out : &b_out) &= p8(G.out + k * G.cpu_step) != 0;
            wr8(G.out, (u8)a_out), wr8(G.out + G.cpu_step, (u8)b_out);
            wr16(G.seats + 2u, (u16)seat(G.n / 2u)), wr16(G.count, 2), wr8(G.four, 0), g_fight = 0;
            game_log_line("ladder: 2 VS 2 over: side A %s, side B %s; the results are the ladder's", a_out ? "out" : "in", b_out ? "out" : "in");
        }
    }
}

void sat_ladder_leave(u32 fn, const TcArgs *a, int choosing)
{
    if (fn == G.setup) g_in_setup = 0;
    else if (fn == G.list && g_rank_rec != ~0u) wr16(rec(g_rank_rec) + G.rank, (u16)g_rank_was), g_rank_rec = ~0u;
    else if (fn == G.draw && choosing && a->r[0] == 1u) show(a->r[1] & 0xffffu, 1);
    else if (fn == G.robot && g_weak && (u32)p16(G.count) >= 2u && a->r[0] >= p16(G.count) / 2u && a->r[0] < p16(G.count)) {
        wr16(G.energy + a->r[0] * G.energy_step, 1);
        game_log_line("ladder: TEST (--ladder-test-weak): seat %u starts with 1 energy", a->r[0]);
    } else if (fn == G.after && g_player == seat(0) && g_player < G.copies) {
        char ch[200];
        int at = 0;
        for (u32 i = 0; i < g_cmp_n && at < 180; i++)
            if (g_before[i] != (u8)p8(rec(g_player) + i)) at += snprintf(ch + at, sizeof ch - (size_t)at, " +%u", i);
        ch[at] = 0;
        game_log_line("ladder: after the results: player %u rank %u -> %u; its record changed at%s", g_player, g_rank0,
                      p16(rec(g_player) + G.rank), at ? ch : " no byte");
    }
    if (g_shown && (!choosing || (fn == G.select && a->r[0] == 1u))) show(0, 0); /* SELECT ENEMY is over: its lines go */
}
#else
void sat_ladder_test_option(const char *value) { (void)value; }
void sat_ladder_rank_option(const char *value) { (void)value; }
void sat_ladder_arm(void) {}
void sat_ladder_enter(u32 fn, const TcArgs *a, int choosing) { (void)fn, (void)a, (void)choosing; }
void sat_ladder_leave(u32 fn, const TcArgs *a, int choosing) { (void)fn, (void)a, (void)choosing; }
#endif
