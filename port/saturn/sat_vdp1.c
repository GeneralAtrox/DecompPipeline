/* VDP1: runs the command list in its VRAM into the drawing framebuffer (16-bit pixels, 512 per row). Commands: normal,
 * scaled and distorted sprites, polygons, polylines, lines, and the clip / local-coordinate commands. A quad is drawn
 * the way the chip does it: walk the two long edges together and draw a line between the walkers, one texture
 * column per step. Gouraud shading adds a per-channel offset interpolated over the quad; mesh, half-luminance,
 * shadow and half-transparent combine with what is already in the framebuffer.
 *
 * Nearest existing: pvr/ (PowerVR TA-list renderer on D3D11) - different chip and a CPU framebuffer the VDP2 reads. */
#include <string.h>
#include <windows.h>
#include "sat.h"
#include "sat_sys.h"
#include "sat_vdp.h"
#include "sat_walk.h"

enum { CM_BANK16, CM_LUT16, CM_BANK64, CM_BANK128, CM_BANK256, CM_RGB };

typedef struct {
    int x, y;
    u16 g; /* gouraud word at this vertex */
} Pt;

typedef struct {
    u32 pmod;
    u16 colr;
    int gouraud;
    const u8 *tex; /* NULL: a flat colour (polygons and lines) */
    int tw, th, hflip, vflip;
} Draw;

static int g_sysx, g_sysy, g_ux0, g_uy0, g_ux1, g_uy1, g_lx, g_ly;
static u8 *g_fb;

static u32 v1w(u32 a) { return ((u32)sat_v1_vram[a & 0x7fffeu] << 8) | sat_v1_vram[(a & 0x7fffeu) + 1]; }
static int sx13(u32 v) { return (int)((s32)(v << 19) >> 19); }

static u16 fbget(u32 x, u32 y)
{
    u32 o = ((y * 512u + x) * 2u) & 0x3ffffu;
    return (u16)((g_fb[o] << 8) | g_fb[o + 1]);
}

static void fbput(u32 x, u32 y, u16 v)
{
    u32 o = ((y * 512u + x) * 2u) & 0x3ffffu;
    g_fb[o] = (u8)(v >> 8);
    g_fb[o + 1] = (u8)v;
}

static u16 shade(u16 c, u16 g)
{
    int r = c & 31, gr = (c >> 5) & 31, b = (c >> 10) & 31, d_r = (g & 31) - 16, d_g = ((g >> 5) & 31) - 16, d_b = ((g >> 10) & 31) - 16;
    r += d_r;
    gr += d_g;
    b += d_b;
    r = r < 0 ? 0 : r > 31 ? 31 : r;
    gr = gr < 0 ? 0 : gr > 31 ? 31 : gr;
    b = b < 0 ? 0 : b > 31 ? 31 : b;
    return (u16)((c & 0x8000u) | (u32)(b << 10) | (u32)(gr << 5) | (u32)r);
}

static void plot(int x, int y, u16 c, const Draw *d, u16 g)
{
    u32 cc = d->pmod & 7u, clipm = (d->pmod >> 9) & 3u;
    if (x < 0 || y < 0 || x > g_sysx || y > g_sysy) return;
    if (clipm & 1u) { /* bit 9: user clipping on; bit 10 set: draw outside the window only, clear: inside only */
        int inside = x >= g_ux0 && x <= g_ux1 && y >= g_uy0 && y <= g_uy1;
        if ((clipm & 2u) ? inside : !inside) return;
    }
    if (d->pmod & 0x100u && ((x ^ y) & 1)) return; /* mesh */
    if (cc == 4u || cc == 6u || cc == 7u) c = shade(c, g);
    if (cc == 1u) { /* shadow: the pixel already there at half luminance */
        u16 b = fbget((u32)x, (u32)y);
        c = (u16)(((b & 0x7bdeu) >> 1) | (b & 0x8000u));
    } else if (cc == 2u || cc == 6u) {
        c = (u16)(((c & 0x7bdeu) >> 1) | (c & 0x8000u));
    } else if (cc == 3u || cc == 7u) { /* half transparent over an RGB pixel */
        u16 b = fbget((u32)x, (u32)y);
        if (b & 0x8000u) c = (u16)((((c & 0x7bdeu) >> 1) + ((b & 0x7bdeu) >> 1)) | 0x8000u);
    }
    fbput((u32)x, (u32)y, c);
}

/* the texel at (u, v) as the framebuffer word, or -1 for transparent / -2 for the line's end code */
static int texel(const Draw *d, int u, int v)
{
    u32 cm = (d->pmod >> 3) & 7u, dot, w;
    int ecd = (d->pmod >> 7) & 1, spd = (d->pmod >> 6) & 1;
    if (cm == CM_RGB) {
        w = ((u32)d->tex[(v * d->tw + u) * 2] << 8) | d->tex[(v * d->tw + u) * 2 + 1];
        return w == 0 && !spd ? -1 : (int)w;
    }
    if (cm <= CM_LUT16) {
        dot = d->tex[(v * d->tw + u) >> 1];
        dot = (u & 1) ? dot & 15u : dot >> 4;
        if (dot == 15u && !ecd) return -2;
    } else {
        dot = d->tex[v * d->tw + u];
        if (dot == 255u && !ecd) return -2;
    }
    if (dot == 0 && !spd) return -1;
    switch (cm) {
    case CM_BANK16: return (int)((d->colr & 0xfff0u) | dot);
    case CM_LUT16: return (int)v1w((u32)(d->colr & 0xffffu) * 8u + dot * 2u);
    case CM_BANK64: return (int)((d->colr & 0xffc0u) | (dot & 63u));
    case CM_BANK128: return (int)((d->colr & 0xff80u) | (dot & 127u));
    default: return (int)((d->colr & 0xff00u) | dot);
    }
}

static int iabs(int a) { return a < 0 ? -a : a; }
static int imax(int a, int b) { return a > b ? a : b; }

static u16 gmix(u16 a, u16 b, int num, int den)
{
    int r = (a & 31) + (((b & 31) - (a & 31)) * num) / den, gr = ((a >> 5) & 31) + ((((b >> 5) & 31) - ((a >> 5) & 31)) * num) / den,
        bl = ((a >> 10) & 31) + ((((b >> 10) & 31) - ((a >> 10) & 31)) * num) / den;
    return (u16)((u32)(bl << 10) | (u32)(gr << 5) | (u32)r);
}

static int imin(int a, int b) { return a < b ? a : b; }

/* no pixel of this box can land: plot would refuse every one of them (a game's model can sit far off the screen) */
static int outside(const Draw *d, int x0, int y0, int x1, int y1)
{
    u32 clipm = (d->pmod >> 9) & 3u;
    if (x1 < 0 || y1 < 0 || x0 > g_sysx || y0 > g_sysy) return 1;
    return clipm == 1u && (x1 < g_ux0 || y1 < g_uy0 || x0 > g_ux1 || y0 > g_uy1);
}

/* one line of a quad: from (x0,y0) to (x1,y1), texture row v (or flat colour when d->tex is NULL) */
static void span(const Draw *d, int x0, int y0, int x1, int y1, int v, u16 g0, u16 g1)
{
    int n = imax(iabs(x1 - x0), iabs(y1 - y0)) + 1, steps = n, den = steps > 1 ? steps - 1 : 1;
    u32 cc = d->pmod & 7u;
    int repeat_same = !d->tex && !d->gouraud && (cc == 0u || cc == 2u), last_x = -99999, last_y = -99999; /* the same pixel again would change nothing */
    Walk wx, wy, wu;
    if (outside(d, imin(x0, x1), imin(y0, y1), imax(x0, x1), imax(y0, y1))) return;
    walk_start(&wx, x0, x1 - x0, den);
    walk_start(&wy, y0, y1 - y0, den);
    walk_start(&wu, 0, d->tw, steps);
    for (int i = 0; i < steps; i++, walk_next(&wx), walk_next(&wy), walk_next(&wu)) {
        int x = wx.at, y = wy.at, c;
        if (d->tex) {
            int u = wu.at;
            if (d->hflip) u = d->tw - 1 - u;
            c = texel(d, u, v);
            if (c == -2) return;
            if (c < 0) {
                last_x = x;
                last_y = y;
                continue;
            }
            if (x != last_x && y != last_y && i > 0) { /* as poly_line: no holes */
                u16 eg = d->gouraud ? gmix(g0, g1, i, den) : 0x8210u;
                if ((x > last_x) == (y > last_y)) plot(last_x, y, (u16)c, d, eg);
                else plot(x, last_y, (u16)c, d, eg);
            }
            last_x = x;
            last_y = y;
        } else {
            if (repeat_same && x == last_x && y == last_y) continue;
            last_x = x;
            last_y = y;
            c = d->colr;
        }
        plot(x, y, (u16)c, d, d->gouraud ? gmix(g0, g1, i, den) : 0x8210u);
    }
}

/* one line of a polygon, as the chip draws it: Bresenham along the long axis, and at each step of the short axis an
 * extra pixel on the right-hand side of the way it travels, where the next line of the quad will be: no holes */
static void poly_line(const Draw *d, int x0, int y0, int x1, int y1, u16 g0, u16 g1)
{
    int dx = iabs(x1 - x0), dy = iabs(y1 - y0), xi = x1 < x0 ? -1 : 1, yi = y1 < y0 ? -1 : 1, x = x0, y = y0;
    int major = imax(dx, dy), den = major > 0 ? major : 1, err = -1 - major, ystep = dy > dx;
    int minor2 = 2 * (ystep ? dx : dy);
    u16 c = d->colr;
    if (outside(d, imin(x0, x1), imin(y0, y1), imax(x0, x1), imax(y0, y1))) return;
    for (int i = 0; i <= major; i++) {
        u16 g = d->gouraud ? gmix(g0, g1, i, den) : 0x8210u;
        err += minor2;
        plot(x, y, c, d, g);
        if (err >= 0) {
            err -= 2 * major;
            if (xi == yi) plot(x, y + yi, c, d, g);
            else plot(x + xi, y, c, d, g);
            if (ystep) x += xi;
            else y += yi;
        }
        if (ystep) y += yi;
        else x += xi;
    }
}

static void quad(const Draw *d, const Pt *p)
{
    int n = imax(imax(iabs(p[3].x - p[0].x), iabs(p[3].y - p[0].y)), imax(iabs(p[2].x - p[1].x), iabs(p[2].y - p[1].y))) + 1;
    int steps = d->tex ? imax(n, d->th) : n;
    if (outside(d, imin(imin(p[0].x, p[1].x), imin(p[2].x, p[3].x)), imin(imin(p[0].y, p[1].y), imin(p[2].y, p[3].y)),
                imax(imax(p[0].x, p[1].x), imax(p[2].x, p[3].x)), imax(imax(p[0].y, p[1].y), imax(p[2].y, p[3].y)))) return;
    int sc = steps > 1 ? steps - 1 : 1;
    Walk lx, ly, rx, ry, wv;
    walk_start(&lx, p[0].x, p[3].x - p[0].x, sc);
    walk_start(&ly, p[0].y, p[3].y - p[0].y, sc);
    walk_start(&rx, p[1].x, p[2].x - p[1].x, sc);
    walk_start(&ry, p[1].y, p[2].y - p[1].y, sc);
    walk_start(&wv, 0, d->tex ? d->th : 0, steps > 0 ? steps : 1);
    if (!d->tex) lx.left = ly.left = rx.left = ry.left = sc / 2; /* to the nearest: two polygons walk a shared edge the same way round */
    for (int i = 0; i < steps; i++, walk_next(&lx), walk_next(&ly), walk_next(&rx), walk_next(&ry), walk_next(&wv)) {
        int v = wv.at;
        if (d->vflip) v = d->th - 1 - v;
        if (!d->tex) poly_line(d, lx.at, ly.at, rx.at, ry.at, d->gouraud ? gmix(p[0].g, p[3].g, i, sc) : 0x8210u, d->gouraud ? gmix(p[1].g, p[2].g, i, sc) : 0x8210u);
        else if (d->gouraud) span(d, lx.at, ly.at, rx.at, ry.at, v, gmix(p[0].g, p[3].g, i, sc), gmix(p[1].g, p[2].g, i, sc));
        else span(d, lx.at, ly.at, rx.at, ry.at, v, 0x8210u, 0x8210u);
    }
}

static void line(const Draw *d, const Pt *a, const Pt *b)
{
    Draw f = *d;
    f.tex = NULL;
    span(&f, a->x, a->y, b->x, b->y, 0, a->g, b->g);
}

static void verts(u32 cmd, Pt *p, int n)
{
    for (int i = 0; i < n; i++) {
        p[i].x = sx13(v1w(cmd + 0x0c + 4u * (u32)i)) + g_lx;
        p[i].y = sx13(v1w(cmd + 0x0e + 4u * (u32)i)) + g_ly;
        p[i].g = 0x8210u;
    }
}

static void gouraud(u32 cmd, Pt *p, Draw *d)
{
    u32 pm = v1w(cmd + 4u), gr = v1w(cmd + 0x1cu) * 8u;
    d->gouraud = (pm & 7u) == 4u || (pm & 7u) >= 6u;
    if (d->gouraud)
        for (int i = 0; i < 4; i++) p[i].g = (u16)v1w(gr + 2u * (u32)i);
}

static void draw_cmd(u32 cmd, u32 ctrl)
{
    Pt p[4];
    Draw d;
    u32 comm = ctrl & 15u;
    memset(&d, 0, sizeof d);
    d.pmod = v1w(cmd + 4u);
    d.colr = (u16)v1w(cmd + 6u);
    switch (comm) {
    case 0: /* normal sprite */
        verts(cmd, p, 4);
        d.colr = (u16)v1w(cmd + 6u);
        d.tex = &sat_v1_vram[(v1w(cmd + 8u) * 8u) & 0x7ffffu];
        {
            u32 size = v1w(cmd + 0xau);
            d.tw = (int)((size >> 8) & 0x3fu) * 8;
            d.th = (int)(size & 0xffu);
        }
        d.hflip = (ctrl >> 4) & 1u;
        d.vflip = (ctrl >> 5) & 1u;
        gouraud(cmd, p, &d);
        {
            int x = p[0].x, y = p[0].y;
            Pt q[4] = {{x, y, p[0].g}, {x + d.tw - 1, y, p[1].g}, {x + d.tw - 1, y + d.th - 1, p[2].g}, {x, y + d.th - 1, p[3].g}};
            quad(&d, q);
        }
        break;
    case 1: { /* scaled sprite */
        u32 zp = (ctrl >> 8) & 15u, size = v1w(cmd + 0xau);
        int xa = sx13(v1w(cmd + 0x0c)) + g_lx, ya = sx13(v1w(cmd + 0x0e)) + g_ly, x0, y0, x1, y1;
        d.tex = &sat_v1_vram[(v1w(cmd + 8u) * 8u) & 0x7ffffu];
        d.tw = (int)((size >> 8) & 0x3fu) * 8;
        d.th = (int)(size & 0xffu);
        d.hflip = (ctrl >> 4) & 1u;
        d.vflip = (ctrl >> 5) & 1u;
        if (zp == 0) {
            x0 = xa;
            y0 = ya;
            x1 = sx13(v1w(cmd + 0x14)) + g_lx;
            y1 = sx13(v1w(cmd + 0x16)) + g_ly;
        } else {
            int w = sx13(v1w(cmd + 0x10)), h = sx13(v1w(cmd + 0x12));
            x0 = (zp & 3u) == 1u ? xa : (zp & 3u) == 2u ? xa - w / 2 : xa - w;
            y0 = (zp >> 2) == 1u ? ya : (zp >> 2) == 2u ? ya - h / 2 : ya - h;
            x1 = x0 + w;
            y1 = y0 + h;
        }
        {
            Pt q[4] = {{x0, y0, 0x8210u}, {x1, y0, 0x8210u}, {x1, y1, 0x8210u}, {x0, y1, 0x8210u}};
            gouraud(cmd, q, &d);
            quad(&d, q);
        }
        break;
    }
    case 2: /* distorted sprite */
        verts(cmd, p, 4);
        d.tex = &sat_v1_vram[(v1w(cmd + 8u) * 8u) & 0x7ffffu];
        {
            u32 size = v1w(cmd + 0xau);
            d.tw = (int)((size >> 8) & 0x3fu) * 8;
            d.th = (int)(size & 0xffu);
        }
        d.hflip = (ctrl >> 4) & 1u;
        d.vflip = (ctrl >> 5) & 1u;
        gouraud(cmd, p, &d);
        quad(&d, p);
        break;
    case 4: /* polygon */
        verts(cmd, p, 4);
        gouraud(cmd, p, &d);
        quad(&d, p);
        break;
    case 5: /* polyline */
    case 6: /* line */
        verts(cmd, p, 4);
        gouraud(cmd, p, &d);
        line(&d, &p[0], &p[1]);
        if (comm == 5) {
            line(&d, &p[1], &p[2]);
            line(&d, &p[2], &p[3]);
            line(&d, &p[3], &p[0]);
        }
        break;
    case 8: /* user clipping */
        g_ux0 = sx13(v1w(cmd + 0x0c));
        g_uy0 = sx13(v1w(cmd + 0x0e));
        g_ux1 = sx13(v1w(cmd + 0x14));
        g_uy1 = sx13(v1w(cmd + 0x16));
        break;
    case 9: /* system clipping */
        g_sysx = sx13(v1w(cmd + 0x14));
        g_sysy = sx13(v1w(cmd + 0x16));
        break;
    case 10: /* local coordinate */
        g_lx = sx13(v1w(cmd + 0x0c));
        g_ly = sx13(v1w(cmd + 0x0e));
        break;
    default: break;
    }
}


u32 sat_v1_dump_every; /* --v1dump N: the command list of every Nth vertical blank goes to the log */

static void dump_cmd(u32 cmd, u32 ctrl)
{
    port_log("v1: %05x ctrl %04x pmod %04x colr %04x srca %04x size %04x v0 %d,%d v1 %d,%d v2 %d,%d v3 %d,%d grda %04x\n", cmd, ctrl, v1w(cmd + 4u),
             v1w(cmd + 6u), v1w(cmd + 8u), v1w(cmd + 0xau), sx13(v1w(cmd + 0xcu)), sx13(v1w(cmd + 0xeu)), sx13(v1w(cmd + 0x10u)),
             sx13(v1w(cmd + 0x12u)), sx13(v1w(cmd + 0x14u)), sx13(v1w(cmd + 0x16u)), sx13(v1w(cmd + 0x18u)), sx13(v1w(cmd + 0x1au)), v1w(cmd + 0x1cu));
}

/* Runs the list from the top of VRAM. Returns the number of commands drawn. */
int sat_v1_run(u8 *fb)
{
    u32 cmd = 0, ret = 0;
    int n = 0, called = 0, dump = sat_v1_dump_every && sat_vblank_count() % sat_v1_dump_every == 0;
    g_fb = fb;
    g_sysx = 511;
    g_sysy = 255;
    g_ux0 = g_uy0 = 0;
    g_ux1 = 511;
    g_uy1 = 255;
    g_lx = g_ly = 0;
    while (n < 20000) {
        u32 ctrl = v1w(cmd), link = v1w(cmd + 2u) * 8u;
        if (ctrl & 0x8000u) break;
        n++;
        if (dump && n <= 300) dump_cmd(cmd, ctrl);
        if (!(ctrl & 0x4000u)) draw_cmd(cmd, ctrl);
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

void sat_v1_draw(void)
{
    LARGE_INTEGER t0, t1;
    QueryPerformanceCounter(&t0);
    sat_perf_phase(2);
    (void)sat_v1_run(sat_v1_draw_fb());
    sat_perf_phase(0);
    QueryPerformanceCounter(&t1);
    sat_perf_v1(t1.QuadPart - t0.QuadPart);
}
