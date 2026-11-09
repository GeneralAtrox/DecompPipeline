/* The game's half-transparent menu panel drawn by the presenter (game.json menu_panel; --menu-panel original|shader).
 * The game darkens its menus with a VDP1 polygon in mesh mode: one dark dot in two, which a TV's blur made a smooth half
 * shade and which a sharp window shows as a coarse checkerboard of Saturn dots at any size. With the shader, the picture
 * keeps what is under the panel at every dot of it (the composed layers at a mesh hole, the layers under the sprite at a
 * panel dot, or the hole beside it) and marks those dots in the colour's top byte (1 + half the panel's grey); the
 * presenter's panel shader (base/present_cpu.c, cpu_present_panel) mixes each marked dot half and half with the panel's
 * colour at the window's own resolution, which is what the mesh averaged to, in the same place, with clean edges at any
 * size or shape. Shots and the frame dumps do the same mix (sat_shot.c). Where a layer above the panel shows (the menu's
 * letters), nothing changes.
 *
 * A dot belongs to the panel when its sprite word is the panel's colour and the dots beside it on its line are not (the
 * mesh's pattern, not a solid area of that colour), or when two of its four neighbours are (a hole in it). The work is
 * done only for a few vertical blanks after a command with the panel's mode and colour was drawn (sat_panel_mark), so a
 * picture without the panel costs nothing. On in play runs; checks and --det runs keep the original unless asked. The
 * game itself is unchanged: this is only the picture. Not done: the colour offset (a fade) on the panel's own grey, and
 * the GPU VDP1 renderer, which draws the mesh at its own resolution (--vdp1-mesh fine).
 *
 * Nearest existing: sat_wide.c (a change to the composed picture that only a play run shows) and the mesh in sat_vdp1.c's
 * plot (what this replaces on the picture). */
#include <string.h>
#include "sat.h"
#include "sat_sys.h"
#include "sat_game.h"
#include "present_cpu.h"

int sat_panel_on;
u32 sat_panel_seen = ~0u - 64u; /* the vertical blank of the last panel command */
static int g_want = -1;         /* --menu-panel: 1 shader, 0 original, -1 not given (shader in a play run) */
void sat_panel_option(const char *value) { g_want = !strcmp(value, "shader") ? 1 : !strcmp(value, "original") ? 0 : -1; }

/* a marked dot as the presenter shows it: half and half with the panel's grey */
u32 sat_panel_shade(u32 c)
{
    u32 d = ((c >> 24) - 1u) * 2u, out = 0;
    for (int s = 0; s <= 16; s += 8) out |= ((((c >> s) & 0xffu) + d) / 2u) << s;
    return out;
}

#ifdef SAT_MENU_PANEL
typedef struct { u32 pmod, color; } PanelGame;
static const PanelGame P = SAT_MENU_PANEL;
static u32 g_flag;    /* the mark: 1 + half the panel's grey, in the top byte */
static int g_pr = -1; /* the panel's sprite priority as last seen (-1: not yet) */

void sat_panel_start(int play_run)
{
    u32 grey = (P.color & 31u) << 3;
    sat_panel_on = g_want < 0 ? play_run : g_want;
    g_flag = (1u + grey / 2u) << 24;
    cpu_present_panel(sat_panel_on);
    port_log("panel: the menu panel is %s\n", sat_panel_on ? "drawn by the presenter's shader at the window's resolution" : "the game's mesh");
}

void sat_panel_mark(u32 pmod, u32 colr)
{
    if (sat_panel_on && pmod == P.pmod && colr == P.color) sat_panel_seen = sat_vblank_count();
}

int sat_panel_frame(void) { return sat_panel_on && sat_vblank_count() - sat_panel_seen < 4u; }

/* sat_vdp2.c, each dot of a picture that may show the panel: the colour, marked when the panel shows there. spr_pr: the
 * sprite's priority when it is the top layer (else -1); pn: the top scroll plane's priority (-1 none); under: the layers
 * under the sprite; left: the dot before on the line, as it came out */
u32 sat_panel_dot(u32 (*word)(u32 x, u32 y), u32 x, u32 y, u32 rgb, int spr_pr, int pn, u32 under, u32 left)
{
    u32 n;
    if (word(x, y) == P.color) {
        if (spr_pr < 0 || (word(x - 1u, y) == P.color && word(x + 1u, y) == P.color)) return rgb; /* under a plane, or solid */
        g_pr = spr_pr;
        return ((left >> 24) ? left & 0xffffffu : under) | g_flag; /* the hole before it shows what is under, sprites too */
    }
    n = (word(x - 1u, y) == P.color) + (word(x + 1u, y) == P.color) + (word(x, y - 1u) == P.color) + (word(x, y + 1u) == P.color);
    return n < 2u || (g_pr >= 0 && pn > g_pr) ? rgb : rgb | g_flag;
}
#else
void sat_panel_start(int play_run) { (void)play_run, (void)g_want; }
void sat_panel_mark(u32 pmod, u32 colr) { (void)pmod, (void)colr; }
int sat_panel_frame(void) { return 0; }
u32 sat_panel_dot(u32 (*word)(u32 x, u32 y), u32 x, u32 y, u32 rgb, int spr_pr, int pn, u32 under, u32 left)
{
    (void)word, (void)x, (void)y, (void)spr_pr, (void)pn, (void)under, (void)left;
    return rgb;
}
#endif
