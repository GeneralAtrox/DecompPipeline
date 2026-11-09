/* VDP2: composes the picture from the scroll planes NBG0..NBG3, the VDP1 sprite layer and the back screen into
 * sat_frame (0x00RRGGBB). Per pixel each layer gives a colour and a priority; the highest priority wins, and a layer
 * with colour calculation on blends with the one under it. Cell and bitmap planes, 1- and 2-word pattern names, the
 * colour modes, scroll and zoom, colour offset, RBG0 (rotation parameter A, sat_vdp2_rot.c), windows and line scroll
 * (sat_vdp2_win.c); wider than the screen (sat_wide.c). Not yet: RBG1, rotation parameter B, vertical cell scroll, the sprite window, mosaic.
 *
 * Nearest existing: pvr/ (PowerVR) - a different chip; nothing Saturn-side to extend. */
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include "sat.h"
#include "sat_sys.h"
#include "sat_vdp.h"
#include "work_count.h"
#include "bands.h"

u32 sat_frame[SAT_MAX_W * SAT_MAX_H];
u32 sat_frame_w = 320, sat_frame_h = 224;
u32 sat_layer_mask = 0xffffffffu; /* --layers HEX: bit 0 the sprite layer, bits 1..4 NBG0..NBG3, bit 5 RBG0 (a test aid: which layer draws what) */

enum { L_SPR, L_NBG0, L_NBG1, L_NBG2, L_NBG3, L_RBG0, L_BACK, NLAYERS };

typedef SatPlane Nbg;
static Nbg g_n[5]; /* NBG0..NBG3, RBG0 */
static u32 g_hist[2048]; /* NBG1 colour indexes drawn (a test aid) */
static u32 g_spctl, g_ccctl, g_clofen, g_clofsl, g_ramctl, g_tvmd;
static int g_off[2][3]; /* colour offsets A and B: r, g, b */
static u32 g_cram[2048]; /* colour memory as RGB, made once a frame: it cannot change while the picture is composed */
static u32 g_cram_mask;
/* each band of the picture's own scratch (bands.h): the name last read from a layer's map, so the rest of that cell's
 * dots need not read it again; RBG0's line; the NBG1 colour indexes it drew */
typedef struct {
    struct { u32 key, charnum, pal; int hf, vf; } cell[5];
    SatRotLine rot;
    u32 hist[2048], rx[SAT_MAX_W], ry[SAT_MAX_W]; /* and RBG0's map dots on the line */
    u8 rok[SAT_MAX_W];
} Band;
static Band g_band[BANDS_MAX];
static u32 g_w, g_h, g_bklines, g_ww, g_ox; /* the screen's size and back screen mode, for the bands; the picture's width, the screen's x in it */
static int g_act[5], g_nact, g_pan; /* the planes that can show (on, a priority, not masked), in layer order; the panel (sat_panel.c) */

static u32 R(u32 o) { return ((u32)sat_v2_reg[o] << 8) | sat_v2_reg[o + 1]; }
static u32 vb(u32 a) { return sat_v2_vram[a & 0x7ffffu]; }
static u32 vw(u32 a) { return (vb(a) << 8) | vb(a + 1u); }
static u32 vl(u32 a) { return (vw(a) << 16) | vw(a + 2u); }

static u32 rgb555(u32 w)
{
    u32 r = w & 31u, g = (w >> 5) & 31u, b = (w >> 10) & 31u;
    return ((r << 3 | r >> 2) << 16) | ((g << 3 | g >> 2) << 8) | (b << 3 | b >> 2);
}
static void cram_load(void)
{
    u32 mode = (g_ramctl >> 12) & 3u, n = mode == 1u ? 2048u : 1024u;
    g_cram_mask = n - 1u;
    for (u32 i = 0; i < n; i++) {
        if (mode == 2u) {
            u32 o = i * 4u, w = ((u32)sat_v2_cram[o] << 24) | ((u32)sat_v2_cram[o + 1] << 16) | ((u32)sat_v2_cram[o + 2] << 8) | sat_v2_cram[o + 3];
            g_cram[i] = ((w & 0xffu) << 16) | (w & 0xff00u) | ((w >> 16) & 0xffu);
        } else {
            g_cram[i] = rgb555(((u32)sat_v2_cram[i * 2u] << 8) | sat_v2_cram[i * 2u + 1u]);
        }
    }
}

static u32 cram_rgb(u32 idx) { return g_cram[idx & g_cram_mask]; }

/* the sprite layer's registers, read once a frame */
static const u8 *g_spr_fb;
static u32 g_spr_sh = 9u, g_spr_ox, g_spr_mask = 0x3ffffu; /* its row shift, x offset and mask: a wide twin's (sat_wide.c) */
static int g_spr_prio[8], g_spr_ratio[8];
static u32 g_spr_craoff;

static void spr_setup(void)
{
    g_spr_fb = sat_wide_spr(sat_v1_display_fb(), &g_spr_sh, &g_spr_ox, &g_spr_mask);
    g_spr_craoff = ((R(0xe6) >> 4) & 7u) * 256u;
    for (u32 i = 0; i < 8u; i++) {
        g_spr_prio[i] = (int)((R(0xf0 + (i & 6u)) >> (8 * (i & 1u))) & 7u);
        g_spr_ratio[i] = (int)((R(0x100 + (i & 6u)) >> (8 * (i & 1u))) & 31u);
    }
}

static void nbg_setup(int n)
{
    Nbg *g = &g_n[n];
    u32 bgon = R(0x20), chctl = n < 2 ? R(0x28) : R(0x2a), pnc = R(0x30 + 2u * (u32)n), plsz = (R(0x3a) >> (2 * n)) & 3u;
    u32 mpofn = (R(0x3c) >> (4 * n)) & 7u, sh = n == 1 ? 8u : 0u;
    static const u8 prio_reg[4] = {0xf8, 0xf8, 0xfa, 0xfa};
    if (n == 0) cram_load(), spr_setup(); /* once a frame, with the first plane */
    if (n == 4) {
        sat_rbg0_setup(g);
        return;
    }
    memset(g, 0, sizeof *g);
    g->on = (bgon >> n) & 1;
    if (!g->on) return;
    g->maps = 2;
    g->tpon = (bgon >> (8 + n)) & 1;
    g->prio = (int)((R(prio_reg[n]) >> ((n & 1) ? 8 : 0)) & 7u);
    g->pn1w = (pnc >> 15) & 1;
    g->cnsm = (pnc >> 14) & 1;
    g->splt = (int)((pnc >> 5) & 7u);
    g->scn = (int)(pnc & 31u);
    g->plw = (plsz & 1u) ? 2 : 1;
    g->plh = (plsz & 2u) ? 2 : 1;
    if (n < 2) {
        g->charpx = (chctl >> sh) & 1u ? 16 : 8;
        g->bitmap = (int)((chctl >> (sh + 1)) & 1u);
        g->cc = (int)((chctl >> (sh + 4)) & 7u);
        g->bmw = ((chctl >> (sh + 2)) & 2u) ? 1024 : 512;
        g->bmh = ((chctl >> (sh + 2)) & 1u) ? 512 : 256;
        g->bmpal = (int)((R(0x2c) >> sh) & 7u) << 4;
    } else {
        g->charpx = (chctl >> (4 * (n - 2))) & 1u ? 16 : 8;
        g->cc = (int)((chctl >> (4 * (n - 2) + 1)) & 1u);
    }
    {
        u32 pagebytes = (u32)(g->charpx == 8 ? 4096 : 1024) * (g->pn1w ? 2u : 4u);
        u32 base = 0x40u + 4u * (u32)n;
        u32 ab = R(base), cd = R(base + 2u);
        u32 mp[4] = {ab & 0x3fu, (ab >> 8) & 0x3fu, cd & 0x3fu, (cd >> 8) & 0x3fu};
        for (int i = 0; i < 4; i++) g->map[i] = (((mpofn << 6) | mp[i]) * pagebytes) & 0x7ffffu;
        g->bmbase = (mpofn << 17) & 0x7ffffu;
    }
    if (n < 2) {
        u32 o = 0x70u + 0x10u * (u32)n;
        g->scx = ((R(o) & 0x7ffu) << 8) | (R(o + 2u) >> 8);
        g->scy = ((R(o + 4u) & 0x7ffu) << 8) | (R(o + 6u) >> 8);
        g->zx = ((R(o + 8u) & 7u) << 8) | (R(o + 10u) >> 8);
        g->zy = ((R(o + 12u) & 7u) << 8) | (R(o + 14u) >> 8);
    } else {
        g->scx = (R(0x90u + 4u * (u32)(n - 2)) & 0x7ffu) << 8;
        g->scy = (R(0x92u + 4u * (u32)(n - 2)) & 0x7ffu) << 8;
    }
    if (!g->zx) g->zx = 0x100;
    if (!g->zy) g->zy = 0x100;
    g->craoff = (int)((R(0xe4) >> (4 * n)) & 7u) << 8;
    g->ccen = (int)((g_ccctl >> n) & 1u);
    g->ccratio = (int)((R(n < 2 ? 0x108 : 0x10a) >> ((n & 1) ? 8 : 0)) & 31u);
}

/* the dot at scroll-space (px, py): 1 and *rgb when it is opaque */
static int nbg_dot(Band *b, const Nbg *g, u32 px, u32 py, u32 *rgb)
{
    u32 base, dot, idx, pal = 0;
    int cx, cy;
    if (g->bitmap) {
        px &= (u32)g->bmw - 1u; /* every size here is a power of two: masks and shifts, not a divide per pixel per layer */
        py &= (u32)g->bmh - 1u;
        cx = (int)px;
        cy = (int)py;
        base = g->bmbase;
        pal = (u32)g->bmpal;
        switch (g->cc) {
        case 0: dot = vb(base + (py * (u32)g->bmw + px) / 2u); dot = (px & 1u) ? dot & 15u : dot >> 4; break;
        case 1: dot = vb(base + py * (u32)g->bmw + px); break;
        case 2:
        case 3: dot = vw(base + (py * (u32)g->bmw + px) * 2u); break;
        default: dot = vl(base + (py * (u32)g->bmw + px) * 4u); break;
        }
    } else {
        u32 planew = 512u * (u32)g->plw, planeh = 512u * (u32)g->plh, cs = (u32)g->charpx, csh = cs == 16u ? 4u : 3u, x, y, pn, charnum, pnaddr, plane, page, cellidx, cellbytes, cxx, cyy, key;
        int hf = 0, vf = 0, layer = (int)(g - g_n);
        px &= planew * (u32)g->maps - 1u;
        py &= planeh * (u32)g->maps - 1u;
        key = (py >> csh) << 16 | (px >> csh);
        x = px & 511u;
        y = py & 511u;
        if (b->cell[layer].key == key) {
            charnum = b->cell[layer].charnum;
            pal = b->cell[layer].pal;
            hf = b->cell[layer].hf;
            vf = b->cell[layer].vf;
            goto named;
        }
        plane = (py >> (8 + g->plh)) * (u32)g->maps + (px >> (8 + g->plw));
        x = px & (planew - 1u);
        y = py & (planeh - 1u);
        page = (y >> 9) * (u32)g->plw + (x >> 9);
        x &= 511u;
        y &= 511u;
        pnaddr = g->map[plane] + page * (u32)(cs == 8u ? 4096 : 1024) * (g->pn1w ? 2u : 4u) + ((y >> csh) * (512u >> csh) + (x >> csh)) * (g->pn1w ? 2u : 4u);
        if (g->pn1w) {
            pn = vw(pnaddr);
            if (g->cnsm) {
                charnum = (pn & 0xfffu) | (((u32)g->scn & 0x1cu) << 10);
            } else {
                charnum = (pn & 0x3ffu) | (((u32)g->scn & 0x1fu) << 10);
                vf = (int)((pn >> 11) & 1u);
                hf = (int)((pn >> 10) & 1u);
            }
            if (cs == 16u) {
                charnum = g->cnsm ? (((pn & 0xfffu) << 2) | ((u32)g->scn & 3u) | (((u32)g->scn & 0x10u) << 10))
                                  : (((pn & 0x3ffu) << 2) | ((u32)g->scn & 3u) | (((u32)g->scn & 0x1cu) << 10));
            }
            pal = g->cc == 1 ? ((pn >> 12) & 7u) << 4 : ((pn >> 12) & 15u) | ((u32)g->splt << 4); /* 256 colours: pn bits 14-12 are the bank */
        } else {
            pn = vl(pnaddr);
            charnum = pn & 0x7fffu;
            pal = (pn >> 16) & 0x7fu;
            vf = (int)((pn >> 31) & 1u);
            hf = (int)((pn >> 30) & 1u);
            if (cs == 16u) charnum <<= 2;
        }
        b->cell[layer].key = key;
        b->cell[layer].charnum = charnum;
        b->cell[layer].pal = pal;
        b->cell[layer].hf = hf;
        b->cell[layer].vf = vf;
named:
        cxx = x & (cs - 1u);
        cyy = y & (cs - 1u);
        if (hf) cxx = cs - 1u - cxx;
        if (vf) cyy = cs - 1u - cyy;
        cellidx = (cyy / 8u) * 2u + cxx / 8u;
        cellbytes = g->cc == 0 ? 32u : g->cc == 1 ? 64u : g->cc <= 3 ? 128u : 256u;
        base = charnum * 0x20u + (cs == 16u ? cellidx * cellbytes : 0u);
        cx = (int)(cxx & 7u);
        cy = (int)(cyy & 7u);
        switch (g->cc) {
        case 0: dot = vb(base + (u32)cy * 4u + (u32)cx / 2u); dot = (cx & 1) ? dot & 15u : dot >> 4; break;
        case 1: dot = vb(base + (u32)cy * 8u + (u32)cx); break;
        case 2:
        case 3: dot = vw(base + ((u32)cy * 8u + (u32)cx) * 2u); break;
        default: dot = vl(base + ((u32)cy * 8u + (u32)cx) * 4u); break;
        }
    }
    switch (g->cc) {
    case 0: if (dot == 0 && !g->tpon) return 0; idx = ((pal & 0x7fu) << 4) | dot; *rgb = cram_rgb(idx + (u32)g->craoff); return 1;
    case 1: if (dot == 0 && !g->tpon) return 0; idx = ((pal & 0x70u) << 4) | dot; if (g == &g_n[1]) b->hist[(idx + (u32)g->craoff) & 0x7ffu]++; *rgb = cram_rgb(idx + (u32)g->craoff); return 1;
    case 2: if ((dot & 0x7ffu) == 0 && !g->tpon) return 0; *rgb = cram_rgb((dot & 0x7ffu) + (u32)g->craoff); return 1;
    case 3: if (!(dot & 0x8000u) && !g->tpon) return 0; *rgb = rgb555(dot); return 1;
    default: if (!(dot & 0x80000000u) && !g->tpon) return 0; *rgb = ((dot & 0xffu) << 16) | (dot & 0xff00u) | ((dot >> 16) & 0xffu); return 1;
    }
}

static u32 spr_word(u32 x, u32 y) { u32 o = (((y << g_spr_sh) + x + g_spr_ox) * 2u) & g_spr_mask; return (u32)g_spr_fb[o] << 8 | g_spr_fb[o + 1]; }
/* the sprite layer at (x, y): 1 with colour and priority when a VDP1 pixel is there */
static int spr_dot(u32 x, u32 y, u32 *rgb, int *prio, int *ccen, int *ratio)
{
    const u8 *fb = g_spr_fb;
    u32 o = (((y << g_spr_sh) + x + g_spr_ox) * 2u) & g_spr_mask, w = ((u32)fb[o] << 8) | fb[o + 1], type = g_spctl & 15u, pr, cc, dc, code;
    if ((g_spctl & 0x20u) && (w & 0x8000u)) { /* RGB data */
        *rgb = rgb555(w);
        pr = 0;
        cc = 0;
    } else {
        switch (type) {
        case 0: pr = w >> 14; cc = (w >> 11) & 7u; dc = w & 0x7ffu; break;
        case 1: pr = w >> 13; cc = (w >> 11) & 3u; dc = w & 0x7ffu; break;
        case 2: pr = (w >> 15) & 1u; cc = (w >> 11) & 7u; dc = w & 0x7ffu; break;
        case 3: pr = (w >> 14) & 3u; cc = (w >> 11) & 3u; dc = w & 0x7ffu; break;
        case 4: pr = (w >> 14) & 3u; cc = (w >> 10) & 7u; dc = w & 0x3ffu; break;
        case 5: pr = (w >> 13) & 7u; cc = (w >> 11) & 3u; dc = w & 0x7ffu; break;
        case 6: pr = (w >> 13) & 7u; cc = (w >> 10) & 7u; dc = w & 0x3ffu; break;
        case 7: pr = (w >> 12) & 7u; cc = (w >> 9) & 7u; dc = w & 0x1ffu; break;
        default: pr = (w >> 7) & 1u; cc = 0; dc = w & 0x7fu; break;
        }
        if (dc == 0) return 0;
        *rgb = cram_rgb(dc + g_spr_craoff);
    }
    code = pr;
    *prio = g_spr_prio[code & 7u];
    *ccen = 0;
    *ratio = 0;
    if (g_ccctl & 0x100u) {
        u32 n = (g_spctl >> 8) & 7u, mode = (g_spctl >> 12) & 3u;
        int ok = mode == 0 ? cc <= n : mode == 1 ? cc == n : mode == 2 ? cc >= n : (w & 0x8000u) != 0;
        *ccen = ok;
        *ratio = g_spr_ratio[cc & 7u];
    }
    return 1;
}
static u32 blend(u32 a, u32 b, int ratio) /* a over b, a's weight (31 - ratio) / 32 */
{
    u32 out = 0;
    for (int s = 0; s <= 16; s += 8) {
        u32 ca = (a >> s) & 0xffu, cb = (b >> s) & 0xffu;
        out |= ((ca * (u32)(31 - ratio) + cb * (u32)(ratio + 1)) / 32u) << s;
    }
    return out;
}
static u32 offset_color(u32 c, int sel)
{
    u32 out = 0;
    for (int i = 0; i < 3; i++) {
        int s = 16 - 8 * i, v = (int)((c >> s) & 0xffu) + g_off[sel][i];
        out |= (u32)(v < 0 ? 0 : v > 255 ? 255 : v) << s;
    }
    return out;
}

static void load_offsets(void)
{
    for (int k = 0; k < 2; k++)
        for (int i = 0; i < 3; i++) {
            int v = (int)(R(0x114u + 6u * (u32)k + 2u * (u32)i) & 0x1ffu);
            g_off[k][i] = v & 0x100 ? v - 0x200 : v;
        }
}

/* the top two layers at a dot, as they come in layer order: a later one wins only with a higher priority */
typedef struct { int best, second, pb, ps, cce, rat; u32 cb, cs; } Top;
static __inline void top(Top *t, int l, int p, u32 c, int cce, int rat)
{
    if (t->best < 0 || p > t->pb) t->second = t->best, t->ps = t->pb, t->cs = t->cb, t->best = l, t->pb = p, t->cb = c, t->cce = cce, t->rat = rat;
    else if (t->second < 0 || p > t->ps) t->second = l, t->ps = p, t->cs = c;
}

/* the rows of one band (bands.h) of the picture, with that band's scratch. Per line: the layers that can show there
 * (a window hiding one along the whole line drops it) and RBG0's map dots; per dot the top two layers */
static void rows(void *ctx, unsigned band, unsigned bands)
{
    Band *b = &g_band[band];
    u32 w = g_w, h = g_h, bklines = g_bklines, y1 = h * (band + 1u) / bands, x0 = 0u - g_ox;
    (void)ctx;
    for (int n = 0; n < 5; n++) b->cell[n].key = 0xffffffffu;
    b->rot.line = 0xffffffffu;
    memset(b->hist, 0, sizeof b->hist);
    for (u32 y = h * band / bands; y < y1; y++) {
        u32 back = ((R(0xac) & 7u) << 16 | R(0xae)) * 2u, *out = sat_frame + y * g_ww;
        int act[5], each[6], na = 0, spr;
        SatWinLine wl;
        back = rgb555(vw(back + (bklines ? y * 2u : 0u)));
        sat_win_line(&wl, y, g_n);
        spr = (sat_layer_mask & 1u) && !sat_win_span(&wl, 0, x0, g_ww, &each[5]);
        for (int i = 0; i < g_nact; i++)
            if (!sat_win_span(&wl, g_act[i] + 1, x0, g_ww, &each[na]) && (g_act[i] < 4 || sat_rbg0_row(&b->rot, y, x0, g_ww, b->rx, b->ry, b->rok))) act[na++] = g_act[i];
        for (u32 wx = 0, x = 0u - g_ox; wx < g_ww; wx++, x++) { /* x: the screen's, before it when widened */
            Top t = {-1, -1, 0, 0, 0, 0, 0u, 0u};
            u32 rgb;
            int pr, cce, rat;
            if (spr && !(each[5] && sat_win_hide(&wl, 0, x)) && spr_dot(w > 512u ? x / 2u : x, y, &rgb, &pr, &cce, &rat) && pr) top(&t, L_SPR, pr, rgb, cce, rat);
            for (int i = 0; i < na; i++) {
                int n = act[i];
                const Nbg *g = &g_n[n];
                u32 sx = (wl.scx[n] + x * wl.zx[n]) >> 8, sy = wl.scy[n] >> 8;
                if (each[i] && sat_win_hide(&wl, n + 1, x)) continue;
                if (n == 4 && !(b->rok[wx] && (sx = b->rx[wx], sy = b->ry[wx], 1))) continue; /* RBG0: its map dot, or none */
                if (nbg_dot(b, g, sx, sy, &rgb)) top(&t, L_NBG0 + n, g->prio, rgb, g->ccen, g->ccratio);
            }
            if (t.best < 0) rgb = back;
            else {
                rgb = t.cb;
                if (t.cce) rgb = blend(rgb, t.second >= 0 ? t.cs : back, t.rat);
                if ((g_clofen >> (t.best == L_SPR ? 6 : t.best - 1)) & 1u) rgb = offset_color(rgb, (int)((g_clofsl >> (t.best == L_SPR ? 6 : t.best - 1)) & 1u));
            }
            if (t.best < 0 && ((g_clofen >> 5) & 1u)) rgb = offset_color(rgb, (int)((g_clofsl >> 5) & 1u));
            if (g_pan) /* the menu panel: what is under it, marked for the presenter (sat_panel.c) */
                rgb = sat_panel_dot(spr_word, w > 512u ? x / 2u : x, y, rgb, t.best == L_SPR ? t.pb : -1, t.best == L_SPR ? (t.second >= 0 ? t.ps : -1) : t.best >= 0 ? t.pb : -1, t.best == L_SPR && t.second >= 0 ? t.cs : back, wx ? out[wx - 1] : 0u);
            out[wx] = rgb;
        }
    }
}

void sat_compose(void)
{
    work_add(WC_COMPOSES, 1);
    u32 hres, vres, w, h;
    g_tvmd = R(0);
    g_ccctl = R(0xec);
    g_spctl = R(0xe0);
    g_ramctl = R(0x0e);
    g_clofen = R(0x110);
    g_clofsl = R(0x112);
    hres = g_tvmd & 7u;
    vres = (g_tvmd >> 4) & 3u;
    w = (hres & 2u ? 640u : 320u) + ((hres & 1u) ? (hres & 2u ? 64u : 32u) : 0u);
    h = vres == 0 ? 224u : vres == 1 ? 240u : 256u;
    sat_frame_w = g_ww = sat_wide_width(w), g_ox = (g_ww - w) / 2u;
    sat_frame_h = h;
    if (!(g_tvmd & 0x8000u)) {
        memset(sat_frame, 0, g_ww * h * sizeof(u32));
        return;
    }
    for (int i = 0; i < 5; i++) nbg_setup(i);
    for (int n = g_nact = 0; n < 5; n++)
        if (g_n[n].on && g_n[n].prio && ((sat_layer_mask >> (n + 1)) & 1u)) g_act[g_nact++] = n;
    load_offsets();
    sat_win_setup();
    if (sat_vwatch_lo == 0x25f80000u && sat_vblank_count() == sat_vwatch_vb) sat_v2_dump_regs();
    g_w = w;
    g_h = h, g_pan = sat_panel_frame();
    g_bklines = (R(0xac) & 0x8000u) ? 1u : 0u; /* BKCLMD; TVMD bit 8 is the border's mode, not this */
    bands_run(rows, NULL);
    memset(g_hist, 0, sizeof g_hist);
    for (unsigned b = 0; b < bands_count(); b++)
        for (u32 i = 0; i < 2048u; i++) g_hist[i] += g_band[b].hist[i];
}

void sat_v2_hist_report(void)
{
    char line[1024];
    int n = 0;
    for (int i = 0; i < 2048 && n < 900; i++)
        if (g_hist[i]) n += snprintf(line + n, sizeof line - (size_t)n, " %x:%u", i, g_hist[i]);
    port_log("vdp2 NBG1 colour indexes drawn:%s\n", line);
}
