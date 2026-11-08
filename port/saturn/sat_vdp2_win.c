/* VDP2's windows and line scroll, for the composer (sat_vdp2.c). Per frame: the two rectangle windows (WPSX..WPEY), each
 * layer's window control (WCTLA..WCTLC) and NBG0/NBG1's line scroll setting (SCRCTL, LSTA). Per picture line: each window's
 * dots on the line (the line window table when LWTA's bit 15 is on), and the NBGs' scroll and X step, which line scroll
 * sets from its table. Per dot: whether a window hides the layer there. The rules are Mednafen's (ss/vdp2_render.cpp,
 * GetCWV and the line scroll in its line drawing): a window's area bit picks inside (0) or outside (1) as the part it
 * hides; OR or AND (bit 7) joins the enabled windows; a line scroll value adds to the layer's scroll register, one table
 * entry per 1, 2, 4 or 8 lines (X, then Y, then X step, as enabled). Robo Pit's fight shows the sky (NBG1) only above
 * its horizon through window 0 and moves the sky sideways through NBG1's line scroll.
 * A picture wider than the screen (sat_wide.c): a window reaching the screen's left or right edge reaches the wide edge.
 * Not yet: the sprite window (its layers are drawn as if the sprite window bit were 0), the colour calculation and
 * rotation parameter windows, vertical cell scroll, double-density interlace: each is logged once when a game turns it on.
 *
 * Nearest existing: sat_vdp2_rot.c - RBG0's coordinates beside the composer the same way; sat_vdp2.c is at the kit's
 * file size limit. */
#include <string.h>
#include "sat.h"
#include "sat_sys.h"
#include "sat_vdp.h"

static struct {
    u32 xs[2], xe[2], ys[2], ye[2], lwta[2], hires;
    int lw[2];
    u8 tab[6][4], on[6]; /* per layer (sprite, NBG0..NBG3, RBG0): hidden for (in W0) | (in W1) << 1 */
    u32 ls[2], lsa[2], lsstep[2]; /* NBG0/NBG1: SCRCTL byte, table address, bytes per entry */
} g_w;

static u32 R(u32 o) { return ((u32)sat_v2_reg[o] << 8) | sat_v2_reg[o + 1]; }
static u32 vw(u32 a) { return ((u32)sat_v2_vram[a & 0x7fffeu] << 8) | sat_v2_vram[(a & 0x7fffeu) + 1u]; }

static void once(u32 bit, const char *what)
{
    static u32 said;
    if (!(said & bit)) {
        said |= bit;
        port_log("vdp2: %s is not drawn yet\n", what);
    }
}

/* Mednafen's GetCWV with the sprite window input 0: 1 where the layer is hidden */
static int hidden(u32 ctrl, int in0, int in1)
{
    int logic = (int)((ctrl >> 7) & 1u), e0 = (ctrl & 2u) != 0, e1 = (ctrl & 8u) != 0, se = (ctrl & 0x20u) != 0;
    int w0 = e0 ? in0 ^ (int)(ctrl & 1u) : logic, w1 = e1 ? in1 ^ (int)((ctrl >> 2) & 1u) : logic;
    int sw = se ? (int)((ctrl >> 4) & 1u) : logic;
    return logic ? (w0 & w1 & sw) : (w0 | w1 | sw);
}

void sat_win_setup(void)
{
    static const u8 wreg[6] = {0xd4, 0xd1, 0xd0, 0xd3, 0xd2, 0xd5}; /* each layer's byte: WCTLC high, WCTLA low/high, WCTLB, WCTLC low */
    u32 tvmd = R(0);
    memset(&g_w, 0, sizeof g_w);
    g_w.hires = (tvmd & 2u) != 0;
    if (((tvmd >> 6) & 3u) == 3u) once(1, "double-density interlace for windows and line scroll");
    for (int d = 0; d < 2; d++) {
        u32 o = 0xc0u + 8u * (u32)d, lo = 0xd8u + 4u * (u32)d;
        g_w.xs[d] = R(o) & 0x3ffu;
        g_w.ys[d] = R(o + 2u) & 0x1ffu;
        g_w.xe[d] = R(o + 4u) & 0x3ffu;
        g_w.ye[d] = R(o + 6u) & 0x1ffu;
        g_w.lw[d] = (R(lo) & 0x8000u) != 0;
        g_w.lwta[d] = (((R(lo) & 7u) << 16) | (R(lo + 2u) & 0xfffeu)) * 2u;
    }
    for (int l = 0; l < 6; l++) {
        u32 c = sat_v2_reg[wreg[l]] & 0xbfu;
        if (c & 0x20u) once(2, "the sprite window");
        for (int m = 0; m < 4; m++) {
            g_w.tab[l][m] = (u8)hidden(c, m & 1, m >> 1);
            g_w.on[l] |= g_w.tab[l][m];
        }
    }
    if ((sat_v2_reg[0xd6] & 0x2au) && R(0xec)) once(4, "the colour calculation window");
    if (sat_v2_reg[0xd7] & 0x2au) once(8, "the rotation parameter window");
    for (int n = 0; n < 2; n++) {
        u32 sc = (R(0x9a) >> (8 * n)) & 0xffu, o = 0xa0u + 4u * (u32)n;
        g_w.ls[n] = sc & 0x3eu;
        g_w.lsa[n] = (((R(o) & 7u) << 16) | (R(o + 2u) & 0xfffeu)) * 2u;
        g_w.lsstep[n] = 4u * (((sc >> 1) & 1u) + ((sc >> 2) & 1u) + ((sc >> 3) & 1u));
        if ((sc & 1u) && ((R(0x20) >> n) & 1u)) once(16, "vertical cell scroll");
    }
}

void sat_win_line(SatWinLine *l, u32 y, const SatPlane *p)
{
    for (int d = 0; d < 2; d++) {
        u32 xs = g_w.xs[d], xe = g_w.xe[d];
        if (g_w.lw[d]) {
            u32 a = g_w.lwta[d] + y * 4u;
            xs = vw(a) & 0x3ffu;
            xe = vw(a + 2u) & 0x3ffu;
        }
        if (xs >= 0x380u) xs = 0; /* Mednafen's: a start past the line is 0, an end past it an empty window */
        if (xe >= 0x380u) xs = 2u, xe = 0;
        if (!g_w.hires) xs >>= 1, xe >>= 1;
        if (y < g_w.ys[d] || y > g_w.ye[d] || xe < xs) {
            l->xs[d] = 0x80000000u; /* no window: no dot of a widened line (from -256) reaches it */
            l->xw[d] = 0;
        } else {
            if (sat_wide_now) xs = xs ? xs : 0u - (u32)sat_wide_now, xe = xe + 1u >= sat_wide_sw ? xe + (u32)sat_wide_now : xe;
            l->xs[d] = xs;
            l->xw[d] = xe - xs;
        }
    }
    memcpy(l->tab, g_w.tab, sizeof l->tab);
    memcpy(l->on, g_w.on, sizeof l->on);
    for (int n = 0; n < 5; n++) {
        l->scx[n] = p[n].scx;
        l->scy[n] = p[n].scy + y * p[n].zy;
        l->zx[n] = p[n].zx;
        if (n < 2 && g_w.ls[n]) {
            u32 lss = (g_w.ls[n] >> 4) & 3u, a = g_w.lsa[n] + (y >> lss) * g_w.lsstep[n];
            if (g_w.ls[n] & 2u) {
                l->scx[n] = (((vw(a) & 0x7ffu) << 8) | (vw(a + 2u) >> 8)) + p[n].scx;
                a += 4u;
            }
            if (g_w.ls[n] & 4u) {
                l->scy[n] = (((vw(a) & 0x7ffu) << 8) | (vw(a + 2u) >> 8)) + p[n].scy + (y & ((1u << lss) - 1u)) * p[n].zy;
                a += 4u;
            }
            if (g_w.ls[n] & 8u) l->zx[n] = ((vw(a) & 7u) << 8) | (vw(a + 2u) >> 8);
        }
    }
}

/* over the n dots of a line from x0: 1 when the layer's windows hide it at every one (it is not drawn on the line), and
 * *each 1 when they may hide it at some (tested per dot), 0 when at none */
int sat_win_span(const SatWinLine *l, int layer, u32 x0, u32 n, int *each)
{
    int can[2][2], all = 1, any = 0; /* can[d][v]: window d's "inside" is v at some dot of the span */
    for (int d = 0; d < 2; d++) {
        u32 a = x0 - l->xs[d];
        can[d][1] = a <= l->xw[d] || l->xs[d] - x0 <= n - 1u;
        can[d][0] = !(a <= l->xw[d] && l->xw[d] - a >= n - 1u);
    }
    for (int m = 0; m < 4; m++)
        if (can[0][m & 1] && can[1][m >> 1]) all &= l->tab[layer][m], any |= l->tab[layer][m];
    *each = l->on[layer] && any;
    return l->on[layer] && all;
}
