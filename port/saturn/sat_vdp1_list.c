/* VDP1's command list: walked from the top of VRAM as the chip walks it (each command's skip and end bits, and its jump:
 * next, assign, call, return), each command drawn by sat_vdp1.c. --v1dump N logs the list of every Nth vertical blank.
 *
 * Ground shadows (game.json saturn.ground_shadows; Display > Shadows, --ground-shadows under|original). VDP1 has no depth
 * buffer: a game sorts its polygons by depth one at a time, so a shadow made of several quads on the floor under a robot
 * sorts its nearer quads after the robot's feet and darkens them (Robo Pit does it on the console too). With "under",
 * a first pass draws the quads the game names as its floor shadows (by draw mode and texture), and the main pass draws
 * everything else in the game's order over them. Nothing standing on the floor farther away reaches down the screen to
 * a nearer floor point, and the floor is VDP2's, under all of VDP1, so only the shadows' order changes. Play runs take
 * "under"; check runs keep the game's order unless told (sat_main.c), so the goldens do not change.
 *
 * Nearest existing: sat_vdp1.c (draws one command; this file picks the order the commands are drawn in). */
#include "sat.h"
#include "sat_sys.h"
#include "sat_vdp.h"
#include "sat_game.h"

u32 sat_v1_dump_every; /* --v1dump N: the command list of every Nth vertical blank goes to the log */
int sat_v1_ground_shadows = -1; /* 1: the game's floor shadows first; 0: the game's order; -1: not set (sat_main.c decides) */

static u32 lw(u32 a) { return ((u32)sat_v1_vram[a & 0x7fffeu] << 8) | sat_v1_vram[(a & 0x7fffeu) + 1]; }
static int sx13(u32 v) { return (int)((s32)(v << 19) >> 19); }

static void dump_cmd(u32 cmd, u32 ctrl)
{
    port_log("v1: %05x ctrl %04x pmod %04x colr %04x srca %04x size %04x v0 %d,%d v1 %d,%d v2 %d,%d v3 %d,%d grda %04x\n", cmd, ctrl, lw(cmd + 4u),
             lw(cmd + 6u), lw(cmd + 8u), lw(cmd + 0xau), sx13(lw(cmd + 0xcu)), sx13(lw(cmd + 0xeu)), sx13(lw(cmd + 0x10u)),
             sx13(lw(cmd + 0x12u)), sx13(lw(cmd + 0x14u)), sx13(lw(cmd + 0x16u)), sx13(lw(cmd + 0x18u)), sx13(lw(cmd + 0x1au)), lw(cmd + 0x1cu));
}

/* one of the quads the game names as its floor shadow: a distorted sprite with that draw mode and texture */
static int ground_shadow(u32 cmd, u32 ctrl)
{
#ifdef SAT_GROUND_SHADOW_PMOD
    return (ctrl & 15u) == 2u && lw(cmd + 4u) == SAT_GROUND_SHADOW_PMOD && lw(cmd + 8u) == SAT_GROUND_SHADOW_SRCA;
#else
    (void)cmd;
    (void)ctrl;
    return 0;
#endif
}

enum { ALL, SHADOWS, REST };

/* one walk: ALL draws every command; SHADOWS only the floor shadows (and the clip and local-coordinate commands, which
 * set where they land); REST everything but the shadows. Returns the number of commands passed. */
static int walk(int pass, int dump)
{
    u32 cmd = 0, ret = 0;
    int n = 0, called = 0;
    while (n < 20000) {
        u32 ctrl = lw(cmd), link = lw(cmd + 2u) * 8u;
        if (ctrl & 0x8000u) break;
        n++;
        if (dump && n <= 3000) dump_cmd(cmd, ctrl);
        if (!(ctrl & 0x4000u)) {
            int draw = pass == ALL || (pass == SHADOWS ? (ctrl & 15u) >= 8u || ground_shadow(cmd, ctrl) : !ground_shadow(cmd, ctrl));
            if (draw) sat_v1_cmd(cmd, ctrl);
        }
        switch ((ctrl >> 12) & 3u) {
        case 0: cmd += 0x20u; break;
        case 1: cmd = link; break;
        case 2: /* call */
            if (!called) ret = cmd + 0x20u;
            called = 1;
            cmd = link;
            break;
        default: /* return */
            cmd = called ? ret : cmd + 0x20u;
            called = 0;
            break;
        }
        cmd &= 0x7ffe0u;
    }
    return n;
}

/* Runs the list from the top of VRAM into fb. Returns the number of commands. */
int sat_v1_run(u8 *fb)
{
    int dump = sat_v1_dump_every && sat_vblank_count() % sat_v1_dump_every == 0 && sat_v1_first_band();
#ifdef SAT_GROUND_SHADOW_PMOD
    if (sat_v1_ground_shadows > 0) {
        sat_v1_begin(fb);
        (void)walk(SHADOWS, 0);
        sat_v1_begin(fb);
        return walk(REST, dump);
    }
#endif
    sat_v1_begin(fb);
    return walk(ALL, dump);
}
