/* Widescreen for the Saturn kit (docs/RUNTIME_TARGET.md "Any aspect ratio"): a game whose game.json says "widescreen"
 * shows more of its world to the sides in a play run (Fit window by default, or the Display > Aspect choice), with
 * nothing stretched: the picture is composed wider than the screen, and the screen sits in its middle at its own shape.
 *
 *  The width. Each vertical blank the screen's width (320 or 352; a high-resolution mode is not widened) is widened to
 *  the frame target's shape (port_widescreen), rounded up to an even width: ox dots more on each side, at most OX_MAX. A
 *  run with no window takes an --aspect it was given (a test's switch: --det --aspect 16:9 composes as a 16:9 window
 *  would); every other check has ox 0, and with ox 0 every path below is skipped, so the picture is the Saturn's.
 *  "widescreen": "rbg0" widens only pictures that show RBG0 (a 3D floor, as Robo Pit's fight): a 2D screen's planes
 *  are drawn for the screen alone (past it they hold the map's unused fill), so its menus keep their width and bars.
 *  The log. "composed" lines are this decision, blank by blank; a black picture composed wide shows nothing more (Robo
 *  Pit keeps RBG0 on over an empty map through its logos and movies), so "shown" lines follow the last lit picture's
 *  width: what the player sees.
 *  VDP1. Its list draws into host framebuffers 1024 dots wide (WS), twins of the guest's two 512-dot ones, x shifted by
 *  ox (sat_vdp1.c): clip rectangles that reach the screen's left or right edge reach the wide edge instead, the erase
 *  that reaches an edge covers the side too (sat_wide_erase), and a CPU write into the guest framebuffer goes into its
 *  twin. After each list the twin's dots ox..ox+511 are copied back, so a game reading its framebuffer sees what it drew.
 *  An in-between frame (sat_inb.c) saves and puts back the twin with the guest one.
 *  VDP2. The composer (sat_vdp2.c) makes Ww = screen + 2 ox dots a line, each at screen x - ox: scroll planes and RBG0
 *  carry on past the screen's edge, windows that reach an edge reach the wide edge (sat_vdp2_win.c), the sprite layer is
 *  read from the twin. The base's presenter (present_cpu.c) is told the screen's width with each frame, so it draws the
 *  screen at the 4:3 size it always has and the sides beside it, at the same dot size.
 *  The game's culling (game.json "wide_cull": SGL's system work and the functions that read its window). SGL keeps
 *  the window it culls against in its work area (GBR, 0x060ffc00 in SGL 3.0x): +120/+124 the left/right edge from the
 *  centre (slCheckOnScreen), +132 width:height and +144 the centre from the left/top (each vertex's clip code), +140 and
 *  +152 the same with the margin past which a polygon is dropped (slWindow writes all of them). When the window is the
 *  screen's full width, those words are widened by ox on each side as one of the listed readers is entered, and put
 *  back if the view goes back to 4:3; the game's own values are kept and widened again whenever it writes new ones.
 *
 * Nearest existing: base/present.c's port_wide_cull_begin and pvr/ (the Dreamcast's widening: the renderer draws past
 * the 640x480 screen and the game's viewport is widened while it culls); the Saturn composes its picture on the CPU, so
 * the widening is in the chips' own drawing here. */
#include <math.h>
#include <stdlib.h>
#include <string.h>
#include "sat.h"
#include "sat_sys.h"
#include "sat_vdp.h"
#include "sat_game.h"
#include "present.h"
#include "present_cpu.h"

enum { WS = 1024, ROWS = 256, WBYTES = WS * ROWS * 2, OX_MAX = 256 };

int sat_wide_ox;  /* this vertical blank's: dots added on each side of the screen; 0 the Saturn's picture */
u32 sat_wide_sw;  /* the screen width they were added to */
int sat_wide_now; /* the picture being composed: its ox (0 when its mode is not the one widened) */
static u8 *g_fb[2], *g_save; /* the twins of sat_v1_fb[0] and [1]; an in-between frame's copy of one */
static struct { u32 w, sw; } g_recent[4]; /* the last pictures' widths and their screens', for sat_wide_post */
static unsigned g_nrecent;

static u8 *twin(const u8 *guest) { return guest == sat_v1_fb[0] ? g_fb[0] : guest == sat_v1_fb[1] ? g_fb[1] : NULL; }

/* the guest framebuffer into its twin at ox, the sides empty: when widening starts or its width changes */
static void fill_twin(int i, int ox)
{
    memset(g_fb[i], 0, WBYTES);
    for (u32 y = 0; y < ROWS; y++) memcpy(g_fb[i] + (y * WS + (u32)ox) * 2u, sat_v1_fb[i] + y * 1024u, 1024u);
}

/* sat_video.c, before each vertical blank's picture: the width this blank's pictures and VDP1 lists use */
void sat_wide_frame(void)
{
#ifdef SAT_WIDESCREEN
    u32 tvmd = ((u32)sat_v2_reg[0] << 8) | sat_v2_reg[1], hres = tvmd & 7u;
    u32 sw = (hres & 2u ? 640u : 320u) + ((hres & 1u) ? (hres & 2u ? 64u : 32u) : 0u);
    f32 k = g_gfx.frame ? port_widescreen() : g_gfx.aspect_set && g_gfx.aspect > 0.0f ? g_gfx.aspect * 0.75f : 1.0f;
    int ox = 0;
#if SAT_WIDESCREEN == 2
    if (!(sat_v2_reg[0x21] & 0x10u)) k = 1.0f; /* "rbg0": a picture without RBG0 (BGON bit 4) is a 2D screen: its own width */
#endif
    if (k > 1.0f && sw <= 352u) {
        u32 ww = ((u32)ceilf((f32)sw * k) + 1u) & ~1u;
        ox = (int)(ww - sw) / 2;
        if (ox > OX_MAX) ox = OX_MAX;
    }
    if (ox && !g_fb[1]) {
        g_fb[0] = (u8 *)malloc(WBYTES), g_fb[1] = (u8 *)malloc(WBYTES), g_save = (u8 *)malloc(WBYTES);
        if (!g_fb[0] || !g_fb[1] || !g_save) port_exit("widescreen: no memory for VDP1's wide framebuffers", 1);
    }
    if (ox && (ox != sat_wide_ox || sw != sat_wide_sw)) {
        fill_twin(0, ox), fill_twin(1, ox);
        port_log("widescreen: the %u-dot screen composed %u wide (%d more each side) from vertical blank %u\n", sw, sw + 2u * (u32)ox, ox, sat_vblank_count());
    } else if (!ox && sat_wide_ox) {
        port_log("widescreen: off (the screen's own width) from vertical blank %u\n", sat_vblank_count());
    }
    sat_wide_ox = ox;
    sat_wide_sw = sw;
#endif
}

/* ---- VDP1 (sat_vdp.c, sat_vdp1.c) ---- */
u8 *sat_wide_fb(const u8 *guest) { return sat_wide_ox ? twin(guest) : NULL; }
int sat_wide_fb_ox(const u8 *fb) { return sat_wide_ox && fb && (fb == g_fb[0] || fb == g_fb[1]) ? sat_wide_ox : 0; }

/* the list drawn into guest's twin: its middle back into guest, as the game would have it */
void sat_wide_back(u8 *guest)
{
    const u8 *w = twin(guest);
    for (u32 y = 0; w && y < ROWS; y++) memcpy(guest + y * 1024u, w + (y * WS + (u32)sat_wide_ox) * 2u, 1024u);
}

/* the erase over guest's twin: a window that reaches the screen's left (x0 0) or right edge covers that side too */
void sat_wide_erase(const u8 *guest, u32 data, u32 x0, u32 y0, u32 x1, u32 y1)
{
    u8 *w = sat_wide_fb(guest);
    u32 ox = (u32)sat_wide_ox, a = x0 ? x0 + ox : 0, b = x1 + ox;
    if (!w) return;
    if (x1 >= sat_wide_sw && b < sat_wide_sw + 2u * ox) b = sat_wide_sw + 2u * ox;
    if (b > WS) b = WS;
    if (a >= b || y0 > y1 || y0 >= ROWS) return;
    for (u32 x = a; x < b; x++) w[(y0 * WS + x) * 2u] = (u8)(data >> 8), w[(y0 * WS + x) * 2u + 1u] = (u8)data;
    for (u32 y = y0 + 1u; y <= y1 && y < ROWS; y++) memcpy(w + (y * WS + a) * 2u, w + (y0 * WS + a) * 2u, (b - a) * 2u); /* the first row's copy */
}

/* a CPU write of size bytes at byte o of guest: the same into its twin */
void sat_wide_cpu(const u8 *guest, u32 o, u32 size, u32 v)
{
    u8 *w = sat_wide_fb(guest);
    u32 at = ((o >> 10) * WS + (u32)sat_wide_ox) * 2u + (o & 1023u);
    for (u32 i = 0; w && i < size; i++) w[at + i] = (u8)(v >> (8u * (size - 1u - i)));
}

/* an in-between frame draws into a framebuffer and puts it back (sat_inb.c): guest into save, and its twin too */
void sat_wide_save(const u8 *guest, u8 *save)
{
    u8 *w = sat_wide_fb(guest);
    memcpy(save, guest, 0x40000);
    if (w) memcpy(g_save, w, WBYTES);
}

void sat_wide_restore(u8 *guest, const u8 *save)
{
    u8 *w = sat_wide_fb(guest);
    memcpy(guest, save, 0x40000);
    if (w) memcpy(w, g_save, WBYTES);
}

/* ---- VDP2 (sat_vdp2.c) ---- */
/* the picture for a screen w dots wide: its width, with sat_wide_now set for the composer and the windows */
u32 sat_wide_width(u32 w)
{
    sat_wide_now = w == sat_wide_sw ? sat_wide_ox : 0;
    g_recent[g_nrecent & 3u].w = w + 2u * (u32)sat_wide_now, g_recent[g_nrecent++ & 3u].sw = w;
    return w + 2u * (u32)sat_wide_now;
}

/* the sprite layer's framebuffer for the picture being composed, with its row shift, x offset and byte mask */
const u8 *sat_wide_spr(const u8 *guest, u32 *shift, u32 *ox, u32 *mask)
{
    const u8 *w = sat_wide_now ? twin(guest) : NULL;
    *shift = w ? 10u : 9u;
    *ox = w ? (u32)sat_wide_now : 0u;
    *mask = w ? 0x7ffffu : 0x3ffffu;
    return w ? w : guest;
}

/* a finished picture to the base's presenter, with the width of the screen in it (the newest picture that wide) */
void sat_wide_post(const u32 *px, u32 w, u32 h, u32 vb)
{
    u32 sw = w;
    for (unsigned i = 1; i <= 4u && i <= g_nrecent; i++)
        if (g_recent[(g_nrecent - i) & 3u].w == w) {
            sw = g_recent[(g_nrecent - i) & 3u].sw;
            break;
        }
    cpu_present_post_wide(px, w, h, vb, sw);
}

/* sat_video.c, after each blank's picture: a lit one composed at another width than the last lit one is logged */
void sat_wide_seen(int black)
{
    static int shown;
    if (black || !sat_wide_now == !shown) return;
    shown = sat_wide_now;
    if (shown) port_log("widescreen: shown %u wide from vertical blank %u (the first lit picture composed wide)\n", sat_wide_sw + 2u * (u32)shown, sat_vblank_count());
    else port_log("widescreen: shown at the screen's own width from vertical blank %u (the first lit picture composed at it)\n", sat_vblank_count());
}

/* ---- the game's culling (sat_call.c, on entering a function) ---- */
#ifdef SAT_WIDE_SGL
static const u32 k_cull_at[] = SAT_WIDE_CULL_AT;
static const u32 k_off[6] = {120, 124, 132, 140, 144, 152}; /* SGL's window words, from its work area */
static const int k_add[6] = {-1, 1, 2, 2, 1, 1};            /* each high half widens by this many ox */
static u32 g_orig[6], g_put[6];
static int g_have, g_put_ox, g_logged;

static void poke32(u32 a, u32 v)
{
    u8 *p = sat_ram_h() + (a - SAT_WRAM_H_BASE);
    p[0] = (u8)(v >> 24), p[1] = (u8)(v >> 16), p[2] = (u8)(v >> 8), p[3] = (u8)v;
}

void sat_wide_cull_at(u32 fn)
{
    u32 now[6];
    int ox = sat_wide_ox, i, ours;
    if (!ox && !g_have) return; /* the Saturn's picture: nothing to do, at the cost of one test a call */
    for (i = 0; i < (int)(sizeof k_cull_at / sizeof k_cull_at[0]) && k_cull_at[i] != fn; i++) {}
    if (i == (int)(sizeof k_cull_at / sizeof k_cull_at[0])) return;
    for (i = 0; i < 6; i++) now[i] = sat_peek32(SAT_WIDE_SGL + k_off[i]);
    ours = g_have && !memcmp(now, g_put, sizeof now);
    if (ours && g_put_ox == ox) return;
    if (!ours) memcpy(g_orig, now, sizeof now); /* the game wrote its window since: those are its values */
    if ((g_orig[2] >> 16) < sat_wide_sw) ox = 0;  /* a window narrower than the screen (one player's half) keeps its own */
    for (i = 0; i < 6; i++) g_put[i] = ((g_orig[i] & 0xffff0000u) + ((u32)(k_add[i] * ox) << 16)) | (g_orig[i] & 0xffffu);
    for (i = 0; i < 6; i++) poke32(SAT_WIDE_SGL + k_off[i], g_put[i]);
    if (g_logged++ < 4) port_log("widescreen: SGL's window at vertical blank %u: width %u widened by %d each side (entering %08x)\n", sat_vblank_count(), g_orig[2] >> 16, ox, fn);
    g_have = ox != 0 || sat_wide_ox != 0, g_put_ox = sat_wide_ox;
}
#else
void sat_wide_cull_at(u32 fn) { (void)fn; }
#endif
