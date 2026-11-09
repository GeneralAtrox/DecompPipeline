/* Team fights, two against two (game.json team_fight; the in-game FIGHT > 2 VS 2, sat_menus.c, turns one on for that fight;
 * --team-fight on|off, a test switch, for the whole run). A game whose own code
 * already runs four robots in one fight (Robo Pit's TRAINING MODE: the player against three CPU robots) is turned into
 * two sides: robots 0 and 1 (side A) against robots 2 and 3 (side B), robot n the seat of pad n + 1. A seat whose pad is
 * there is a player's; an empty seat is the game's own CPU. Nothing is added to the fight itself: around a few of the
 * game's own functions, named in game.json, the kit changes which robot each one is told about.
 *
 *   control (robot)  the per-robot input step. Each seat's CPU flag is set from its pad (and put back when the four-robot
 *                    fight is over); a player's seat past the game's two pads reads its own pad when the game reads one
 *                    (pad_read), when the game would take a pad at all (gate).
 *   brain (self, target)  a CPU robot's AI. The game hands every CPU robot robot 0; the kit hands it the nearest robot of
 *                    the other side that is not out, measured as the game's own nearest search measures.
 *   setup (robot)    the robot set-up. The four-robot path gives each CPU robot a third of the energy (one shared bar);
 *                    a team fight gives every robot the full energy.
 *   sides            functions that read robot 0's and robot 1's "out" flags as the player's side and the other (the
 *                    fight-over check, the results). While one runs, robot 0's flag says side A is out and robots 1 to
 *                    3 say side B is out, so the game decides the fight by sides. The flags it did not change are put back.
 *   camera (robot)   the follow camera's nearest search: while it runs the camera robot's teammate reads as out, so the
 *                    camera turns to the nearest enemy, never to a teammate.
 *   hud              the energy bars: while they are drawn the four-robot flag reads clear, so the game draws robot 0's
 *                    and robot 1's own bars (its two-player HUD); the kit then draws robots 2 and 3 on the next two
 *                    lines with the game's own bar cells, side A's colour for robots 0 and 1, side B's for 2 and 3.
 *   frames (lines)   the bar frames: four lines instead of two.
 *   names            the fight's HUD set-up draws robot 0's name and, on the four-robot path, a label for the rest;
 *                    after it the kit draws robots 1 to 3's names on the next three lines with the game's own name routine.
 *   shared camera (view)  one camera for the whole fight, the one every player watches (camera 0, the four-robot path's
 *                    only view). The game's own side-on camera for two robots (the mode that frames robots 0 and 1, its
 *                    two-player view) runs for it every frame, fed two points in place of robots 0 and 1: either side of
 *                    the centre of the robots still in, along the group's long axis (their spread runs across the
 *                    picture), span/16 of the group's radius apart and at least near (how far apart in height counts as
 *                    reach, so a jump stays in frame), aimed between the highest robot and the lowest. For that call the
 *                    game's camera height is raised by lift/16 of the camera's distance, so it looks down over the ring's
 *                    rim. Centre, axis and span ease towards the robots once a game frame, each step capped (no jump at a
 *                    ring-out); the axis turns the short way, under a degree a frame, and holds while the group has no
 *                    long axis. Robots 0 and 1 and the height are put back after the call. The mode stays set for the
 *                    frame, so the game draws robot 0's body (its first-person view hides it) and no arrows.
 *   warning (view)   the WARNING / TURN BACK sign the game flashes while the view's robot (robot 0) is past the ring's
 *                    edge. Once robot 0 is out it is held back (for that call the ring-out flag reads set, as when the
 *                    fight is over); while robot 0 is in it shows as in the original. It stays robot 0's alone: one sign
 *                    on the shared picture could not say whose robot it means.
 *   ringout          the ring-out test. In the four-robot fight the game ends the fight when robot 0 falls out of the
 *                    ring (the player lost); in a team fight that ends it only when robot 1, its teammate, is out too.
 * Hits between robots are the game's own: it tests every robot's weapons against every other robot whatever the side
 * (the original's training mode lets its CPU robots hit each other), so teammates can hurt each other.
 *
 * The switch is read at start: a replay takes the recording's (a note, kept only when on). The in-game 2 VS 2 turns it on
 * for one fight (sat_team_once) and off when the hub is entered again (sat_team_done). Online play is one against one: a
 * match keeps it off and the game offers no 2 VS 2. Off,
 * sat_team_on is 0 and sat_call.c never calls in, so the game runs as before. On, outside the four-robot fight the kit
 * only looks (side-effect-free peeks), so the game runs as before there too.
 *
 * Nearest existing: sat_part.c (a start-time choice kept in the recording as a session note, an entry greyed online)
 * and base/pad_twin.c (the game names a function in game.json and the kit acts when it is entered). */
#include <string.h>
#include "sat.h"
#include "sat_sys.h"
#include "sat_net.h"
#include "console.h"
#include "sat_game.h"
#include "session.h"
#include "game_log.h"

int sat_team_on; /* the switch, in force since the start (sat_call.c tests it before every call) */

#ifdef SAT_TEAM_FIGHT
#define NOTE "*team-fight"
#define N 4
typedef struct {
    u32 count, mode, flags, step, cpu, out, pos, pos_step, energy, robot_step, full;
    u32 control, pad_read, gate_count, gate_after, gate_or, brain, setup, camera, hud, frames, sides[2];
    u32 map, line, first_line, cells, table, cell_add, bar_max, tables[2], fall, ring;
    u32 names_at, name_draw, records, name, record, text_map, name_x, name_len;
    u32 view, cam_mode, pair, span, warn, height, lift, near;
} TeamGame;
static const TeamGame T = SAT_TEAM_FIGHT;

static int g_want = -1;           /* the menu's or --team-fight (-1: not given: off) */
static int g_robot = -1;          /* the robot whose control step is running */
static u8 g_saved[N], g_dirty;    /* each seat's CPU flag before the kit set it */
static u8 g_keep[N], g_wrote[N];  /* out flags while a sides or camera function runs: as they were, as the kit set them */
static u8 g_ring, g_fell;         /* the ring-out flag before the ring-out test ran; robot 0 fell (logged once a fight) */
static u8 g_cam, g_cam_logged, g_cam_in; /* the shared camera's state is set; logged this fight; robots 0-1 moved */
static s32 g_c[3], g_u[2], g_h, g_pk[6], g_hk[2]; /* centre (16.16), axis (unit 65536), half span; robots 0-1, height: was, set */
static u32 g_cam_vb;              /* the vertical blank it last eased at */
static u8 g_warn, g_warn_ring;    /* robot 0's ring-edge sign held back in this call; the ring-out flag before */
static int g_viewing;             /* 1: out flags changed; 2: the four-robot flag cleared for the HUD */
static s32 g_shown[N];            /* the bars as the kit draws them (they move 4 a frame to the energy, as the game's) */
static u32 g_hits[11], g_seatings, g_last_pads = ~0u, g_target[N] = {~0u, ~0u, ~0u, ~0u}, g_logged;
static u8 g_once;                 /* on for one fight (the in-game 2 VS 2) */

void sat_team_option(const char *value) { g_want = strcmp(value, "off") != 0; }

void sat_team_start(void)
{
    u8 on = 0;
    if (session_note_replayed(NOTE, &on, 1)) on = on != 0;
    else if (!session_replaying()) on = g_want > 0;
    if (on && sat_net_on()) port_log("team fight: off in online play (one against one)\n"), on = 0;
    if (on) session_save_read(NOTE, &on, 1, 1);
    sat_team_on = on;
    port_log("team fight: %s\n", on ? "on (the four-robot path is two against two, pads 1 to 4)" : "off");
}

/* the kit's reads have no side effect: rd8/rd16 count towards the busy-wait detector, which would move the game's timing */
static u8 p8(u32 a) { return (u8)(sat_peek32(a & ~3u) >> (24u - 8u * (a & 3u))); }
static u16 p16(u32 a) { return (u16)(sat_peek32(a & ~3u) >> (16u - 8u * (a & 2u))); }
static u32 flag(int i, u32 which) { return T.flags + (u32)i * T.step + which; }
static int side(int i) { return i >= N / 2; }
static int active(void) { return p8(T.mode) != 0 && p16(T.count) == N; } /* the game's four-robot fight */

static void put_back(void) /* after a four-robot fight: each seat's CPU flag as the game left it */
{
    for (int i = 0; i < N; i++)
        if (g_dirty & (1u << i)) wr8(flag(i, T.cpu), g_saved[i]);
    if (g_dirty) game_log_line("team fight: the fight is over; the seats' CPU flags are the game's again");
    for (int i = 0; i < N; i++) g_target[i] = ~0u;
    g_dirty = 0, g_last_pads = ~0u, g_fell = 0, g_cam = 0, g_cam_logged = 0;
    memset(g_shown, 0, sizeof g_shown);
}

void sat_team_once(void) { if (!sat_team_on) sat_team_on = 1, g_once = 1; }
void sat_team_done(void)
{
    if (!g_once) return;
    put_back(), sat_team_on = 0, g_once = 0;
    game_log_line("team fight: the 2 VS 2 is over; the next fight is the game's own again");
}

static void seat(int i)
{
    u32 w[N], pads = sat_pad_seats(w), human = (pads >> i) & 1u, now = p8(flag(i, T.cpu));
    if (pads != g_last_pads) {
        g_last_pads = pads, g_seatings++;
        game_log_line("team fight: side A robot 1 pad 1, robot 2 %s; side B robot 3 %s, robot 4 %s", pads & 2u ? "pad 2" : "CPU",
                      pads & 4u ? "pad 3" : "CPU", pads & 8u ? "pad 4" : "CPU");
    }
    if (i == 0) return; /* robot 0 is the first player's, as in the game */
    if (!(g_dirty & (1u << i))) g_saved[i] = (u8)now, g_dirty |= (u8)(1u << i);
    if (now != (u32)!human) wr8(flag(i, T.cpu), (u8)!human);
}

/* the game's distance between two robots, squared: the whole parts of dx, dy and dz, as its nearest search takes them */
static u32 dist2(int a, int b)
{
    u32 d = 0;
    for (u32 k = 0; k < 3; k++) {
        s32 v = (s16)((sat_peek32(T.pos + (u32)a * T.pos_step + 4u * k) - sat_peek32(T.pos + (u32)b * T.pos_step + 4u * k)) >> 16);
        d += (u32)v * (u32)v;
    }
    return d;
}

static u32 nearest_enemy(int self)
{
    int best = -1;
    u32 bd = 0;
    for (int j = 0; j < N; j++) {
        if (side(j) == side(self) || p8(flag(j, T.out))) continue;
        if (best < 0 || dist2(self, j) < bd) best = j, bd = dist2(self, j);
    }
    return best >= 0 ? (u32)best : (side(self) ? 0u : (u32)N / 2u); /* none left standing: still one of the other side */
}

static void view(const u8 want[N])
{
    for (int i = 0; i < N; i++) g_keep[i] = p8(flag(i, T.out)), g_wrote[i] = want[i], wr8(flag(i, T.out), want[i]);
    g_viewing = 1;
}

static void view_back(void) /* a flag the function itself changed stays as it left it */
{
    for (int i = 0; i < N; i++)
        if (p8(flag(i, T.out)) == g_wrote[i]) wr8(flag(i, T.out), g_keep[i]);
    g_viewing = 0;
}

/* one bar as the game draws it (its bar routine): full cells, the part cell, then empty cells up to the bar's length */
static void bar(int line, s32 v, u32 table)
{
    u32 dst = T.map + (u32)line * T.line, src = T.cells + table * T.table, i = 0;
    if (v < 0) v = 0;
    for (; i < (u32)v / 8u; i++) wr16(dst + 2u * i, (u16)(p16(src + 16u) + T.cell_add));
    if (v % 8) wr16(dst + 2u * i, (u16)(p16(src + 2u * (u32)(v % 8)) + T.cell_add)), i++;
    if ((u32)v + 8u <= T.bar_max)
        for (; i < T.bar_max / 8u; i++) wr16(dst + 2u * i, (u16)(p16(src) + T.cell_add));
}

static void hud(void)
{
    for (int k = 1; k < N; k++) {
        s32 e = (s16)p16(T.energy + (u32)k * T.robot_step);
        if (e < 0) e = 0;
        if (g_shown[k] > e) g_shown[k] = g_shown[k] - 4 < e ? e : g_shown[k] - 4;
        else if (g_shown[k] < e) g_shown[k] = g_shown[k] + 4 > e ? e : g_shown[k] + 4;
        bar((int)T.first_line + k, g_shown[k], T.tables[side(k)]);
    }
}

static void names(void) /* after the HUD set-up: robots 1 to 3's names, over the label the four-robot path drew */
{
    u32 row0 = T.first_line, blank = rd16(T.text_map + (row0 + 1u) * T.line + 2u * (T.name_x + T.name_len));
    for (u32 k = 1; k < N; k++) {
        TcArgs c;
        for (u32 i = 0; i < T.name_len; i++) wr16(T.text_map + (row0 + k) * T.line + 2u * (T.name_x + i), (u16)blank);
        memset(&c, 0, sizeof c);
        c.r[0] = T.name + (u32)(s16)p16(T.records + 2u * k) * T.record, c.r[1] = T.name_x, c.r[2] = row0 + k, c.s[0] = 1;
        tc_call(T.name_draw, &c);
    }
    g_hits[8]++;
}

/* the shared camera (see the header): two points, for the game's side-on camera to frame, in place of robots 0 and 1 */
static s32 at(int i, int k) { return (s32)sat_peek32(T.pos + (u32)i * T.pos_step + 4u * (u32)k); }
static s32 ease(s32 v, s32 t, int shift, s32 cap) /* 1/2^shift of the way to t, at most cap */
{
    s64 d = ((s64)t - v) / ((s64)1 << shift);
    return (s32)(v + (d > cap ? cap : d < -cap ? -cap : d));
}
static u32 root(u64 v)
{
    u64 r = 0, b = (u64)1 << 62;
    while (b > v) b >>= 2;
    for (; b; b >>= 2)
        if (v >= r + b) v -= r + b, r = (r >> 1) + b;
        else r >>= 1;
    return (u32)r;
}

static void shared_camera(void)
{
    s64 m[3] = {0, 0, 0}, a = 0, b = 0, c = 0, e, f;
    s32 t[3], lo = 0, hi = 0, tu[2] = {g_cam ? g_u[0] : 65536, g_cam ? g_u[1] : 0}, th;
    int n = 0, all = 1, in[N];
    u32 r = 0, lag = 0, L, vb = sat_vblank_count();
    for (int i = 0; i < N; i++) all &= p8(flag(i, T.out)) != 0; /* none in (the fight is over): all four */
    for (int i = 0; i < N; i++)
        if ((in[i] = all || !p8(flag(i, T.out))) != 0) {
            for (int k = 0; k < 3; k++) m[k] += at(i, k);
            if (!n++ || at(i, 1) < lo) lo = at(i, 1);
            if (n == 1 || at(i, 1) > hi) hi = at(i, 1);
        }
    for (int k = 0; k < 3; k++) t[k] = (s32)(m[k] / n);
    t[1] = (s32)(((s64)lo + hi) / 2), r = (u32)(((s64)hi - lo) * 3 / 4); /* between the highest and the lowest; a jump is reach */
    for (int i = 0; i < N; i++)
        if (in[i]) {
            s64 dx = (s64)at(i, 0) - t[0], dz = (s64)at(i, 2) - t[2], ux = dx >> 16, uz = dz >> 16;
            u32 d = root((u64)((dx >> 8) * (dx >> 8) + (dz >> 8) * (dz >> 8))) << 8;
            a += ux * ux, b += ux * uz, c += uz * uz;
            if (d > r) r = d;
        }
    e = a - c, f = 2 * b, L = root((u64)(e * e + f * f)); /* the spread's long axis at twice its angle */
    if (L && (s64)L * 4 > a + c) { /* the group is longer one way than the other: that way runs across the picture */
        s32 c2 = (s32)(e * 65536 / L);
        tu[0] = (s32)root((u64)(65536 + c2) * 32768u), tu[1] = (s32)root((u64)(65536 - c2) * 32768u) * (f < 0 ? -1 : 1);
        if (g_cam && (s64)tu[0] * g_u[0] + (s64)tu[1] * g_u[1] < 0) tu[0] = -tu[0], tu[1] = -tu[1]; /* the short way */
    }
    if (g_cam) { /* the centre still on its way: the span covers that too */
        s64 dx = ((s64)g_c[0] - t[0]) >> 8, dz = ((s64)g_c[2] - t[2]) >> 8;
        lag = root((u64)(dx * dx + dz * dz)) << 8;
    }
    th = (s32)((s64)(r + lag) * T.span / 32);
    if (th < (s32)(T.near << 16)) th = (s32)(T.near << 16);
    if (!g_cam) {
        memcpy(g_c, t, sizeof g_c), g_u[0] = tu[0], g_u[1] = tu[1], g_h = th, g_cam = 1, g_cam_vb = vb;
    } else if (vb != g_cam_vb) { /* once a game frame: the game runs its camera more than once a frame */
        for (int k = 0; k < 3; k++) g_c[k] = ease(g_c[k], t[k], 3, k == 1 ? 8 << 16 : 16 << 16);
        g_u[0] = ease(g_u[0], tu[0], 4, 700), g_u[1] = ease(g_u[1], tu[1], 4, 700);
        L = root((u64)((s64)g_u[0] * g_u[0] + (s64)g_u[1] * g_u[1]));
        if (L > 4096u) g_u[0] = (s32)((s64)g_u[0] * 65536 / L), g_u[1] = (s32)((s64)g_u[1] * 65536 / L);
        g_h = th > g_h ? ease(g_h, th, 3, 24 << 16) : ease(g_h, th, 4, 8 << 16); /* wider sooner than narrower */
        g_cam_vb = vb;
    }
    for (int i = 0; i < 2; i++) {
        s32 s = i ? 1 : -1;
        for (int k = 0; k < 3; k++) g_pk[3 * i + k] = at(i, k);
        wr32(T.pos + (u32)i * T.pos_step, (u32)(g_c[0] + s * (s32)((s64)g_u[0] * g_h / 65536)));
        wr32(T.pos + (u32)i * T.pos_step + 4u, (u32)g_c[1]);
        wr32(T.pos + (u32)i * T.pos_step + 8u, (u32)(g_c[2] + s * (s32)((s64)g_u[1] * g_h / 65536)));
    }
    g_hk[0] = (s32)sat_peek32(T.height), g_hk[1] = g_hk[0] + (s32)(((s64)2 * g_h + (200 << 16)) * T.lift / 16);
    wr32(T.height, (u32)g_hk[1]), g_cam_in = 1;
}

/* sat_call.c, before a call: the arguments it runs with (a changed copy in *tmp) */
const TcArgs *sat_team_enter(u32 fn, const TcArgs *a, TcArgs *tmp)
{
    int r = (s16)a->r[0];
    if ((fn != T.control && fn != T.brain && fn != T.fall && fn != T.frames && fn != T.sides[0] && fn != T.sides[1] && fn != T.camera && fn != T.hud &&
         fn != T.view && fn != T.warn) ||
        sat_cpu_id() != 0)
        return a;
    if (fn == T.control) {
        g_robot = r;
        if (!active()) put_back(), g_robot = -1;
        else if (r >= 0 && r < N) seat(r);
        return a;
    }
    if (!active() || g_viewing) return a;
    if (fn == T.brain && r >= 0 && r < N) {
        *tmp = *a, tmp->r[1] = nearest_enemy(r);
        if (tmp->r[1] != a->r[1]) g_hits[0]++;
        if (tmp->r[1] != g_target[r] && g_logged++ < 200)
            game_log_line("team fight: vb %u CPU robot %d goes for robot %d (the game named %d; distance^2 %u, out %d%d%d%d)",
                          sat_vblank_count(), r + 1, (int)tmp->r[1] + 1, (int)(s16)a->r[1] + 1, dist2(r, (int)tmp->r[1]),
                          p8(flag(0, T.out)), p8(flag(1, T.out)), p8(flag(2, T.out)), p8(flag(3, T.out)));
        g_target[r] = tmp->r[1];
        return tmp;
    }
    if (fn == T.fall) {
        g_ring = (u8)p8(T.ring);
        return a;
    }
    if (fn == T.frames) {
        *tmp = *a, tmp->r[0] = N, g_hits[6]++;
        return tmp;
    }
    if (fn == T.sides[0] || fn == T.sides[1]) {
        u8 f[N], va, vb;
        for (int i = 0; i < N; i++) f[i] = p8(flag(i, T.out));
        va = (u8)(f[0] && f[1]), vb = (u8)(f[2] && f[3]);
        f[0] = va, f[1] = f[2] = f[3] = vb;
        view(f), g_hits[1]++;
    } else if (fn == T.view && r == 0) {
        shared_camera(), wr16(T.cam_mode, (u16)T.pair), g_hits[9]++;
        if (!g_cam_logged) /* once a fight (a count would wrap at 256 calls) */
            g_cam_logged = 1, game_log_line("team fight: vb %u one shared camera frames the robots still in (the game's side-on "
                                            "two-robot camera, widened)", sat_vblank_count());
    } else if (fn == T.warn && r == 0 && p8(flag(0, T.out))) { /* robot 0 is out: its ring-edge sign is held back */
        g_warn = 1, g_warn_ring = (u8)p8(T.ring), g_hits[10]++;
        wr8(T.ring, 1);
    } else if (fn == T.camera && r >= 0 && r < N) {
        u8 f[N];
        for (int i = 0; i < N; i++) f[i] = p8(flag(i, T.out));
        f[r ^ 1] = 1;
        view(f), g_hits[2]++;
    } else if (fn == T.hud) {
        wr8(T.mode, 0), g_viewing = 2, g_hits[3]++;
    }
    return a;
}

/* sat_call.c, after the call: its result */
u32 sat_team_leave(u32 fn, const TcArgs *a, u32 r)
{
    int k = (s16)a->r[0];
    if (sat_cpu_id() != 0) return r;
    if (fn == T.control) {
        g_robot = -1;
    } else if (g_viewing == 2 && fn == T.hud) {
        wr8(T.mode, 1), g_viewing = 0;
        hud();
    } else if (g_viewing == 1 && (fn == T.sides[0] || fn == T.sides[1] || fn == T.camera)) {
        view_back();
    } else if (fn == T.view && g_cam_in) { /* robots 0 and 1 and the height as they were; the mode stays set for the frame */
        for (int i = 0; i < 2; i++)
            for (int j = 0; j < 3; j++) wr32(T.pos + (u32)i * T.pos_step + 4u * (u32)j, (u32)g_pk[3 * i + j]);
        if ((s32)sat_peek32(T.height) == g_hk[1]) wr32(T.height, (u32)g_hk[0]);
        g_cam_in = 0;
    } else if (fn == T.warn && g_warn) {
        wr8(T.ring, g_warn_ring), g_warn = 0;
    } else if (fn == T.names_at && active()) {
        names();
    } else if (fn == T.fall && !g_ring && p8(T.ring) && active() && !(p8(flag(0, T.out)) && p8(flag(1, T.out)))) {
        wr8(T.ring, 0), g_hits[7]++; /* robot 0 fell, robot 1 is still in: side A fights on (the test runs again each frame) */
        if (!g_fell)
            g_fell = 1, game_log_line("team fight: vb %u robot 1 fell out of the ring; robot 2 is still in, so the fight goes on", sat_vblank_count());
    } else if (fn == T.setup && k > 0 && k < N && active()) {
        wr16(T.energy + (u32)k * T.robot_step, (u16)T.full), g_hits[4]++;
    } else if (fn == T.pad_read && g_robot >= 2 && g_robot < N && active() && !p8(flag(g_robot, T.cpu))) {
        u32 w[N], word, live = (s32)sat_peek32(T.gate_count) > (s32)T.gate_after || p8(T.gate_or) != 0;
        sat_pad_seats(w);
        word = (w[g_robot] & 0xffu) << 8 | (w[g_robot] >> 8 & 0xffu); /* the SMPC's two data bytes as one word, as the game reads them */
        g_hits[5]++;
        return live ? (u32)(s32)(s16)word : 0xffffffffu;
    }
    return r;
}

void sat_team_end(void)
{
    if (sat_team_on)
        port_log("team fight: %u seatings; CPU robots sent at another enemy %u times; sides read %u, camera %u, HUD %u, energy %u, "
                 "pads 3-4 read %u, frames %u, ring-outs played on %u, names %u, "
                 "shared camera frames %u, ring-edge signs held back %u\n", g_seatings, g_hits[0], g_hits[1], g_hits[2], g_hits[3], g_hits[4],
                 g_hits[5], g_hits[6], g_hits[7], g_hits[8], g_hits[9], g_hits[10]);
}
#else
void sat_team_option(const char *value) { (void)value; }
void sat_team_start(void) {}
const TcArgs *sat_team_enter(u32 fn, const TcArgs *a, TcArgs *tmp) { (void)fn, (void)tmp; return a; }
u32 sat_team_leave(u32 fn, const TcArgs *a, u32 r) { (void)fn, (void)a; return r; }
void sat_team_end(void) {}
void sat_team_once(void) {}
void sat_team_done(void) {}
#endif
