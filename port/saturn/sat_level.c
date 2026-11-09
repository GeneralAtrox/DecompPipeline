/* An edited arena (game.json level_edit, the game's level schema; Game > Edited arena; --level-mod on|off) and the Saturn
 * side of the port base's level editor (base/level_edit.cpp: Game > Level editor...). Version 0 edits one arena: the
 * layout records the game's own builder turns into placed objects (meshes with their collision, liftable items), the
 * two start points, the fight's light direction, the stands (scenery) and invisible blockers placed in the editor. Every
 * one is a number the game's code reads, or a box the kit adds to its collision, so an edited arena changes no file of
 * the game's: around a few of the game's own functions, named in the schema, the kit hands it the edited numbers.
 *
 *   build (arena, records, defs)  the arena's layout table holds the edited x, y, z while the game builds its live
 *                    objects from it, and its own numbers again after: each object and its collision cylinder are placed
 *                    by the game itself. A record whose kind is not the one the level file names is left as it is.
 *   spawns           after the game computes the two robots' start points (a one-against-one fight), the edited x and z.
 *   light (x, y, z)  the call that turns the fight's light direction into the camera's space gets the edited direction
 *                    in place of the game's constant, while a fight in this arena is on.
 *   stands           while the game draws the arena (draw.arena), its call that puts the stands' model gets a translate
 *                    by the edited offset first (SGL's own push, translate and pop, inside the game's half-height scale).
 *   blockers         after the game's robot-against-objects test for a robot (blockers.collide), which leaves the robot's
 *                    next position at blockers.next_pos, a robot inside a blocker's box (grown by the robot's radius, as
 *                    the game grows its cylinders) is put back out along the shorter way, as the game does for its own.
 *                    Robots obey them; thrown items and the camera do not (the game tests those elsewhere).
 *   Play             --level-play on: the game's own way to a fight. The main menu (play.menu) opens on its fight item
 *                    (play.cursor, the remembered cursor, set after the save's auto-load step); play.lead is pressed
 *                    through the title while the load's step waits for the menu; the auto-load driver then
 *                    presses through its screens (play.steps, base autoload.c, after the load's own steps); at fight setup
 *                    (play.setup), once, the schema's pokes choose the arena and tier. FIGHT's 1 VS 1 / 2 VS 2 choice
 *                    (sat_menus.c) is off for the run: Play is the one-against-one fight.
 * Each applies only while the arena and tier the game has chosen are the edited level's, and the stands and blockers
 * only when the level moves the stands or has a blocker: an untouched level makes no call the original does not.
 *
 * The level is a text file in the data folder, levels/<level>.level.json, apart from the game's data and its save: one
 * entity a line, each with its id, the game's word for its type and its exact numbers in the game's units (whole units
 * for layout records, the stands and blockers, 16.16 for start points and the light), and every field the editor does
 * not change as read. Saving changes only the numbers of the entities the file has and writes its blockers anew, so
 * fields a newer tool added are kept; a missing file, or one without an entity the editor has, is written afresh. The
 * game's original level saved untouched gives the bytes `level_rt.py export` gives (the game's folder, port/level).
 *
 * The switch is read at start: a replay takes the recording's edits (a session note, kept only when on), so the file is
 * not needed to replay it, and a change applies from the next start. Online play keeps it off and greys the entry. Off,
 * sat_level_on is 0 and sat_call.c never calls in, so the game runs as before.
 * Tests: --level-file FILE (another level file), --level-test-save OUT [--level-test-move ID,DX,DY,DZ ...]
 * [--level-test-block X,Y,Z[,HX,H,HZ] ...]: once the image is loaded, the editor's scene is built, the moves made (game
 * units: whole units for layout records, the stands and blockers, 16.16 for spawn-N and sun), blockers placed as the
 * editor's Add blocker places them (whole units), saved to OUT and the run ends.
 *
 * Split at 400 lines, sharing sat_level_priv.h: here the switch, the hooks around the game's calls and the save test;
 * sat_level_file.c reads the level file, sat_level_scene.c builds the editor's scene, sat_level_save.c saves it.
 *
 * Nearest existing: sat_team.c (the game's functions named in game.json, changed around the call, a start-time note) and
 * sat_part.c (a choice kept in the recording, greyed online); neither reads a level or feeds the editor. */
#include <intrin.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <windows.h>
#include "sat.h"
#include "sat_sys.h"
#include "sat_net.h"
#include "sat_disc.h"
#include "console.h"
#include "sat_game.h"
#include "session.h"
#include "game_log.h"
#include "host.h"
#include "level_edit.h"
#include "sat_level_priv.h"

int sat_level_on; /* the switch, in force since the start (sat_call.c tests it before every call) */

#ifdef SAT_LEVEL_EDIT
#define NOTE "*level-edit"
static const AutoLoadStep k_play[] = SAT_LEVEL_PLAY_STEPS;

static int g_want = -1, g_play_want, g_play, g_play_cursor, g_play_done;
static char g_file[260], g_test_save[260];
static struct {
    char id[32];
    s32 d[3];
} g_moves[MAXMOVE];
static int g_nmoves, g_ntblock;
static s32 g_tblock[MAXBLOCK][6];
static Edits E;
static u16 g_orig[MAXREC][3]; /* the table's own x, y, z while the build runs */
static u32 g_patched, g_hits[6]; /* builds, start points, light calls, plays, stands drawn, robots held */
static int g_in_draw, g_pushed;
static u32 g_held[4];

static int here(void) { return (s16)p16(G.arena_var) == (s16)G.arena && (s16)p16(G.tier_var) == (s16)G.tier; }
static int stands_moved(void) { return E.has_stands && (E.stands[0] || E.stands[1] || E.stands[2]); }

int sat_level_option(const char *a, const char *v)
{
    if (!v) return 0;
    if (!strcmp(a, "--level-mod")) g_want = strcmp(v, "off") != 0;
    else if (!strcmp(a, "--level-play")) {
        g_play_want = !strcmp(v, "on");
        if (g_play_want) sat_fight_choice_option("off"); /* Play is the one-against-one fight: FIGHT does not ask */
    }
    else if (!strcmp(a, "--level-file")) snprintf(g_file, sizeof g_file, "%s", v);
    else if (!strcmp(a, "--level-test-save")) snprintf(g_test_save, sizeof g_test_save, "%s", v);
    else if (!strcmp(a, "--level-test-move")) {
        const char *c = strchr(v, ',');
        if (c && g_nmoves < MAXMOVE) {
            snprintf(g_moves[g_nmoves].id, sizeof g_moves[g_nmoves].id, "%.*s", (int)(c - v), v);
            if (sscanf(c + 1, "%d,%d,%d", &g_moves[g_nmoves].d[0], &g_moves[g_nmoves].d[1], &g_moves[g_nmoves].d[2]) == 3) g_nmoves++;
        }
    } else if (!strcmp(a, "--level-test-block")) {
        s32 *b = g_tblock[g_ntblock < MAXBLOCK ? g_ntblock : 0];
        int n = sscanf(v, "%d,%d,%d,%d,%d,%d", &b[0], &b[1], &b[2], &b[3], &b[4], &b[5]);
        if (n == 3) b[3] = (s32)G.bsize[0], b[4] = (s32)G.bsize[1], b[5] = (s32)G.bsize[2];
        if ((n == 3 || n == 6) && g_ntblock < MAXBLOCK) g_ntblock++;
    } else return 0;
    return 2;
}

/* ---- the level file ---- */
void sat_lv_level_path(char *out, size_t cap)
{
    const char *d = host_data_folder();
    size_t n = d ? strlen(d) : 0;
    if (g_file[0]) snprintf(out, cap, "%s", g_file);
    else snprintf(out, cap, "%s%slevels\\%s.level.json", d ? d : "", n && d[n - 1] != '\\' && d[n - 1] != '/' ? "\\" : "", SAT_LEVEL_SLUG);
}

void sat_level_start(void)
{
    char path[300];
    int on = 0;
    level_edit_provide(&sat_level_provider);
    if (session_note_replayed(NOTE, &E, sizeof E)) on = E.magic == MAGIC;
    else if (!session_replaying()) on = g_want > 0;
    if (on && sat_net_on()) port_log("level: the edited arena is off in online play\n"), on = 0;
    if (on && !session_replaying()) {
        sat_lv_level_path(path, sizeof path);
        if (!sat_lv_load(path, &E)) port_log("level: no edits in %s (the original arena)\n", path);
        E.play = (u32)g_play_want;
        session_save_read(NOTE, &E, sizeof E, 1);
    }
    sat_level_on = on;
    g_play = on && E.play;
    port_log("level: edited arena %s%s\n", on ? "on: " SAT_LEVEL_TITLE : "off", g_play ? ", Play: through the main menu to a fight there" : "");
    if (g_play) autoload_then(SAT_LEVEL_PLAY_LEAD, k_play, sizeof k_play / sizeof k_play[0]);
    if (on)
        game_log_line("level: edited %s: %u layout records, start points %u, light %s, stands %s, %u blockers", SAT_LEVEL_TITLE,
                      (unsigned)__popcnt(E.has_rec), (unsigned)__popcnt(E.has_spawn), E.has_light ? "edited" : "the game's",
                      stands_moved() ? "moved" : "in place", E.nblock);
}

/* a robot's next position put back out of every blocker it is inside (robots' own radius added, as the game does) */
static void hold(u32 robot)
{
    u32 p = G.next_pos + 12u * robot;
    s32 x, y, z, floor_y, r = (s32)G.robot_r;
    if (robot > 3) return;
    x = (s32)sat_peek32(p), z = (s32)sat_peek32(p + 8u);
    y = (s32)sat_peek32(G.spawn_pos + robot * G.spawn_step + 4u); /* where it stands now */
    floor_y = (s32)sat_peek32(G.floor_var);
    for (u32 b = 0; b < E.nblock; b++) {
        const s32 *k = E.block[b];
        s32 bx = k[0] * 65536, bz = k[2] * 65536, base = floor_y + k[1] * 65536, hx = (k[3] + r) * 65536, hz = (k[5] + r) * 65536;
        s32 dx = x - bx, dz = z - bz;
        if (y < base - k[4] * 65536 || y > base + r * 65536) continue; /* above its top or below its foot */
        if (dx <= -hx || dx >= hx || dz <= -hz || dz >= hz) continue;
        if (hx - abs(dx) < hz - abs(dz)) x = bx + (dx < 0 ? -hx : hx);
        else z = bz + (dz < 0 ? -hz : hz);
        wr32(p, (u32)x), wr32(p + 8u, (u32)z);
        g_hits[5]++;
        if (!(g_held[robot] >> b & 1u)) {
            g_held[robot] |= 1u << b;
            game_log_line("level: vb %u robot %u is held back by blocker %u", sat_vblank_count(), robot + 1, b + 1);
        }
    }
}

static void sgl(u32 fn, s32 x, s32 y, s32 z)
{
    TcArgs c;
    memset(&c, 0, sizeof c);
    c.r[0] = (u32)x, c.r[1] = (u32)y, c.r[2] = (u32)z;
    tc_call(fn, &c);
}

/* sat_call.c, before a call */
const TcArgs *sat_level_enter(u32 fn, const TcArgs *a, TcArgs *tmp)
{
    if (sat_cpu_id() != 0) return a;
    if (g_play && !g_play_cursor && fn == G.menu) { /* after the save's auto-load step, which may load the cursor too */
        wr16(G.cursor, (u16)G.cursor_value);
        g_play_cursor = 1;
        game_log_line("level: vb %u Play: the main menu opens on item %u", sat_vblank_count(), G.cursor_value);
    }
    if (g_play && !g_play_done && fn == G.setup) {
        for (u32 i = 0; i < G.npokes && i < MAXPOKE; i++) {
            u32 size = G.pokes[i][0], at = G.pokes[i][1], v = G.pokes[i][2];
            if (size == 1) wr8(at, (u8)v);
            else if (size == 2) wr16(at, (u16)v);
            else wr32(at, v);
        }
        g_play_done = 1, g_hits[3]++;
        game_log_line("level: vb %u Play: the fight is set up in %s", sat_vblank_count(), SAT_LEVEL_TITLE);
    }
    if (fn == G.build && a->r[1] == G.recs && E.has_rec && here()) {
        u32 moved = 0;
        for (int i = 0; i < (int)G.max && i < MAXREC; i++) {
            s16 k = (s16)p16(rec(i));
            if (k == -1) break;
            if (!(E.has_rec >> i & 1u) || E.kind[i] != k) continue;
            for (int j = 0; j < 3; j++) {
                g_orig[i][j] = p16(rec(i) + 2u + 2u * (u32)j);
                if ((s16)g_orig[i][j] != E.pos[i][j]) moved |= 1u << i;
                wr16(rec(i) + 2u + 2u * (u32)j, (u16)E.pos[i][j]);
            }
            g_patched |= 1u << i;
        }
        g_hits[0]++;
        game_log_line("level: vb %u the arena's objects are built from the edited layout (%u records moved)", sat_vblank_count(),
                      (unsigned)__popcnt(moved));
    } else if (fn == G.light_fn && E.has_light && a->r[0] == G.light_args[0] && a->r[1] == G.light_args[1] &&
               a->r[2] == G.light_args[2] && p8(G.fight) && here()) {
        *tmp = *a;
        for (int j = 0; j < 3; j++) tmp->r[j] = (u32)E.light[j];
        if (!g_hits[2]++) game_log_line("level: vb %u the fight's light shines along the edited direction", sat_vblank_count());
        return tmp;
    } else if (fn == G.draw && stands_moved() && here()) {
        g_in_draw = 1;
    } else if (fn == G.put_polygon && g_in_draw && !g_pushed && a->r[0] == sat_peek32(sat_peek32(G.stands_ptr))) {
        /* inside the game's scale (stands_scale_y on Y): the offset's Y grows by its inverse so the stands move whole units */
        sgl(G.push, 0, 0, 0);
        sgl(G.translate, E.stands[0] * 65536, (s32)((s64)E.stands[1] * 65536 * 65536 / (s32)G.stands_scale_y), E.stands[2] * 65536);
        g_pushed = 1;
        if (!g_hits[4]++) game_log_line("level: vb %u the stands are drawn moved by %d, %d, %d", sat_vblank_count(), E.stands[0], E.stands[1], E.stands[2]);
    }
    return a;
}

/* and after it: its result */
u32 sat_level_leave(u32 fn, const TcArgs *a, u32 r)
{
    if (sat_cpu_id() != 0) return r;
    if (fn == G.build && g_patched) {
        for (int i = 0; i < MAXREC; i++)
            if (g_patched >> i & 1u)
                for (int j = 0; j < 3; j++) wr16(rec(i) + 2u + 2u * (u32)j, g_orig[i][j]);
        g_patched = 0;
    } else if (fn == G.spawn_fn && E.has_spawn && (s16)p16(G.count) == 2 && !p8(G.mode) && here()) {
        for (u32 k = 0; k < 2; k++)
            if (E.has_spawn >> k & 1u) {
                wr32(G.spawn_pos + k * G.spawn_step, (u32)E.spawn[k][0]);
                wr32(G.spawn_pos + k * G.spawn_step + 8u, (u32)E.spawn[k][2]);
            }
        g_hits[1]++;
        game_log_line("level: vb %u the robots start at the edited points", sat_vblank_count());
    } else if (fn == G.put_polygon && g_pushed) {
        sgl(G.pop, 0, 0, 0);
        g_pushed = 0;
    } else if (fn == G.draw && g_in_draw) {
        g_in_draw = 0;
    } else if (fn == G.collide && E.nblock && here()) {
        hold(a->r[0]);
    }
    return r;
}

void sat_level_end(void)
{
    if (sat_level_on)
        port_log("level: builds from the edited layout %u, start points set %u, light calls %u, Play %u, stands drawn moved %u, "
                 "robots held by blockers %u\n", g_hits[0], g_hits[1], g_hits[2], g_hits[3], g_hits[4], g_hits[5]);
}

/* --level-test-save: once the image is loaded, the scene, the moves, the blockers, the file, and the run ends */
void sat_level_loaded(void)
{
    LevelScene sc;
    char msg[400];
    int ok, nb = 0;
    if (!g_test_save[0]) return;
    if (!sat_lv_scene_open(&sc)) port_exit("level test: the scene could not be built", 1);
    for (int m = 0; m < g_nmoves; m++) {
        u32 i = 0;
        while (i < sc.nent && strcmp(sc.ent[i].id, g_moves[m].id)) i++;
        if (i == sc.nent) {
            port_log("level test: no entity %s\n", g_moves[m].id);
            continue;
        }
        for (int j = 0; j < 3; j++) sc.ent[i].pos[j] += g_moves[m].d[j] * (sc.ent[i].quantum > 1 ? sc.ent[i].quantum : 1);
        port_log("level test: %s moved by %d, %d, %d\n", g_moves[m].id, g_moves[m].d[0], g_moves[m].d[1], g_moves[m].d[2]);
    }
    for (u32 i = 0; i < sc.nent; i++) nb += sc.ent[i].kind == LEVEL_BLOCKER && sc.ent[i].axes;
    for (int b = 0; b < g_ntblock && sc.nent < sc.cap; b++) {
        char id[32], name[32];
        snprintf(id, sizeof id, "%s-%02d", sc.place_tmpl.id, ++nb), snprintf(name, sizeof name, "%s %02d", sc.place_tmpl.name, nb);
        sat_lv_blocker(&sc.ent[sc.nent++], id, name, g_tblock[b]);
        port_log("level test: %s placed at %d, %d, %d (%d x %d x %d)\n", id, g_tblock[b][0], g_tblock[b][1], g_tblock[b][2], g_tblock[b][3],
                 g_tblock[b][4], g_tblock[b][5]);
    }
    ok = sat_lv_scene_save_to(&sc, g_test_save, msg, sizeof msg);
    port_log("level test: %s; %u entities, %u meshes, %u textures\n", msg, sc.nent, sc.nmesh, sc.ntex);
    sat_lv_scene_close(&sc);
    port_exit(ok ? "level test save written" : "level test save failed", ok ? 0 : 1);
}

static int alone(void) { return !sat_net_on(); }
static int get(void) { return g_want > 0; }
static void set(int on) { g_want = on != 0; }
static const PortMenuItem k_item = {"Game", "&Edited arena (" SAT_LEVEL_TITLE ") (from the next start)", PORT_MENU_CHECK, "level-mod", get, set,
                                    NULL, NULL, 0, alone};
const PortMenuItem *sat_level_menu(void) { return &k_item; }
#else
int sat_level_option(const char *a, const char *v) { (void)a, (void)v; return 0; }
void sat_level_start(void) {}
void sat_level_loaded(void) {}
const TcArgs *sat_level_enter(u32 fn, const TcArgs *a, TcArgs *tmp) { (void)fn, (void)tmp; return a; }
u32 sat_level_leave(u32 fn, const TcArgs *a, u32 r) { (void)fn, (void)a; return r; }
void sat_level_end(void) {}
const PortMenuItem *sat_level_menu(void) { return NULL; }
#endif
