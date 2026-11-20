/* Clearer text for lettering VDP2 draws (sat_text.c does VDP1's sprites): the cells of the scroll planes NBG0..NBG3, and
 * the bitmaps of NBG0 and NBG1 taken as 8x8 tiles of the bitmap (a game that draws its lettering into a bitmap).
 *
 *  1. Once a picture, before it is composed (sat_vdp2.c), each cell plane that can show at 1:1 is walked over the screen,
 *     cell by cell, at the plane's scroll. Each cell's texels (as shown: flips applied) go to the game's `text_cells` hook
 *     (sat_hooks.h), which says whether it is lettering (a tag), by its fingerprint. Answers are kept by the texels, the
 *     palette, the layer and the cell's form, so the game is asked once for each.
 *  2. Lettering cells side by side with the same tag make a block, and a block exactly under another with the same tag and
 *     extent joins it. The game is asked again about each block, with all its texels (row-major as shown), and gives the
 *     block's tag: the same, another (a word it knows), or 0 (not lettering after all).
 *  3. Each block is an item: its box on the picture and a map of the picture's dots saying which item's box a dot is in.
 *     The composer leaves a plane's dot out of the shown picture where its cell is lettering and the map puts that plane's
 *     item there, and works out the colour the dot has with it (sat_text_with), as the sprite path does. sat_text_frame
 *     makes each item's image from those dots, as it does a sprite's.
 *
 * Not covered (those cells stay original): RBG0, a plane zoomed, a bitmap with line or vertical cell scroll on, cells
 * line scroll moves away from the plane's scroll (the map then does not hold them). Off (no hook, the option off, any check or scripted run, a high-
 * resolution picture) nothing here runs and the map is empty: the picture is composed exactly as before. VRAM, CRAM and
 * the registers are only read.
 *
 * Nearest existing: sat_text.c's VDP1 scan (sat_vdp1.c) - sprites have a command and a texture each; a plane's cells have
 * neither, so they are found by walking the plane as the composer reads it (sat_vdp2_name.h). */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "sat.h"
#include "sat_sys.h"
#include "sat_vdp.h"
#include "sat_vdp2_name.h"
#include "console.h"

enum { NAMES = 4096, KNOWN = 16384, GW = SAT_MAX_W / 8 + 2, GH = SAT_MAX_H / 8 + 2, MAXDOT = 64 * 256 };
typedef struct { u32 key, gen; int tag; } Name;
typedef struct { u64 key; int tag, used; } Known;

u16 sat_text_v2_map[SAT_MAX_W * SAT_MAX_H];
u8 sat_text_v2_layer[SAT_V2_ITEMS + 1];
SatTextV2Item sat_text_v2_it[SAT_V2_ITEMS];
int sat_text_v2_n;
u32 sat_text_v2_bm; /* the bitmap planes with lettering this picture (bit n: NBGn) */
static Name g_name[NAMES];
static Known g_known[KNOWN];
static u32 g_gen, g_nknown, g_mapw;
static int g_ctx; /* the game's answer for this picture (text_cells(NULL)): the screen */
static u8 g_tex[MAXDOT * 4];

static u64 fnv(u64 h, const void *p, size_t n)
{
    const u8 *b = (const u8 *)p;
    for (size_t i = 0; i < n; i++) h = (h ^ b[i]) * 1099511628211ull;
    return h;
}

static u32 name_key(int layer, u32 charnum, u32 pal, int hf, int vf)
{
    return (charnum & 0x1ffffu) | (pal & 0x7fu) << 17 | (u32)hf << 24 | (u32)vf << 25 | (u32)layer << 26;
}

/* the composer, a band's cell read: the tag this frame's scan gave the cell with this name (0: not lettering) */
static int name_get(u32 key, int *tag)
{
    for (u32 s = (key * 2654435761u) >> 20, i = 0; i < 64u; i++, s = (s + 1u) % NAMES) {
        if (g_name[s].gen != g_gen) return 0;
        if (g_name[s].key == key) return *tag = g_name[s].tag, 1;
    }
    return 0;
}

u32 sat_text_v2_tag(int layer, u32 charnum, u32 pal, int hf, int vf)
{
    int tag = 0;
    return name_get(name_key(layer, charnum, pal, hf, vf), &tag) && tag > 0 ? (u32)tag : 0u;
}

static void name_put(u32 key, int tag)
{
    for (u32 s = (key * 2654435761u) >> 20, i = 0; i < 64u; i++, s = (s + 1u) % NAMES)
        if (g_name[s].gen != g_gen || g_name[s].key == key) {
            g_name[s].key = key, g_name[s].gen = g_gen, g_name[s].tag = tag;
            return;
        }
}

static Known *known(u64 key)
{
    if (g_nknown > KNOWN * 3 / 4) memset(g_known, 0, sizeof g_known), g_nknown = 0;
    for (u32 s = (u32)(key % KNOWN);; s = (s + 1u) % KNOWN)
        if (!g_known[s].used || g_known[s].key == key) return &g_known[s];
}

static int bits(int cc) { return cc == 0 ? 4 : cc == 1 ? 8 : cc <= 3 ? 16 : 32; }
static u32 csz(const SatPlane *g) { return g->bitmap ? 8u : (u32)g->charpx; }

/* a bitmap's dot (x, y) as stored */
static u32 bm_dot(const SatPlane *g, u32 x, u32 y)
{
    u32 i = (y & ((u32)g->bmh - 1u)) * (u32)g->bmw + (x & ((u32)g->bmw - 1u)), b;
    switch (g->cc) {
    case 0: b = sat_v2_vram[(g->bmbase + i / 2u) & 0x7ffffu]; return (i & 1u) ? b & 15u : b >> 4;
    case 1: return sat_v2_vram[(g->bmbase + i) & 0x7ffffu];
    case 2:
    case 3: return sat_v2n_w(g->bmbase + i * 2u);
    default: return sat_v2n_w(g->bmbase + i * 4u) << 16 | sat_v2n_w(g->bmbase + i * 4u + 2u);
    }
}

/* a bitmap tile (its top left in the name's character number: x | y << 16) with nothing drawn in it */
static int bm_empty(const SatPlane *g, const SatV2Name *nm)
{
    u32 rb = (u32)bits(g->cc), x = nm->charnum & 0xffffu, y = nm->charnum >> 16;
    for (u32 r = 0; r < 8u; r++) {
        u32 a = g->bmbase + (((y + r) & ((u32)g->bmh - 1u)) * (u32)g->bmw + x) * (u32)bits(g->cc) / 8u;
        for (u32 i = 0; i < rb; i++)
            if (sat_v2_vram[(a + i) & 0x7ffffu]) return 0;
    }
    return 1;
}

/* a cell's dot (cx, cy) as stored, its raw value */
static u32 cell_dot(u32 charnum, int cc, u32 cs, u32 cx, u32 cy)
{
    u32 cellbytes = cc == 0 ? 32u : cc == 1 ? 64u : cc <= 3 ? 128u : 256u, base = charnum * 0x20u + (cs == 16u ? ((cy / 8u) * 2u + cx / 8u) * cellbytes : 0u), b;
    cx &= 7u, cy &= 7u;
    switch (cc) {
    case 0: b = sat_v2_vram[(base + cy * 4u + cx / 2u) & 0x7ffffu]; return (cx & 1u) ? b & 15u : b >> 4;
    case 1: return sat_v2_vram[(base + cy * 8u + cx) & 0x7ffffu];
    case 2:
    case 3: return sat_v2n_w(base + (cy * 8u + cx) * 2u);
    default: return sat_v2n_w(base + (cy * 8u + cx) * 4u) << 16 | sat_v2n_w(base + (cy * 8u + cx) * 4u + 2u);
    }
}

/* a cell's texels as shown into t (tw dots a row) at (ox, oy): 4 bits a dot two to a byte (high first), a byte, 2 or 4 */
static void cell_put(u8 *t, int tw, int ox, int oy, const SatV2Name *nm, const SatPlane *g)
{
    u32 cs = csz(g);
    int cc = g->cc;
    for (u32 y = 0; y < cs; y++)
        for (u32 x = 0; x < cs; x++) {
            u32 v = g->bitmap ? bm_dot(g, (nm->charnum & 0xffffu) + x, (nm->charnum >> 16) + y)
                              : cell_dot(nm->charnum, cc, cs, nm->hf ? cs - 1u - x : x, nm->vf ? cs - 1u - y : y), i = (u32)(oy + (int)y) * (u32)tw + (u32)(ox + (int)x);
            switch (bits(cc)) {
            case 4: t[i / 2u] = (u8)((i & 1u) ? (t[i / 2u] & 0xf0u) | v : (t[i / 2u] & 0x0fu) | v << 4); break;
            case 8: t[i] = (u8)v; break;
            case 16: t[i * 2u] = (u8)(v >> 8), t[i * 2u + 1u] = (u8)v; break;
            default: t[i * 4u] = (u8)(v >> 24), t[i * 4u + 1u] = (u8)(v >> 16), t[i * 4u + 2u] = (u8)(v >> 8), t[i * 4u + 3u] = (u8)v; break;
            }
        }
}

/* the game's answer about a cell (tag 0) or a block of cells with that tag, asked once for each content */
static int ask(int layer, const SatPlane *g, SatV2Name nm[GH][GW], int r0, int c0, int r1, int c1, int tag, int x, int y)
{
    u32 cs = csz(g);
    int n = c1 - c0 + 1, rows = r1 - r0 + 1, tw = n * (int)cs, th = rows * (int)cs, bytes = tw * th * bits(g->cc) / 8;
    SatTextCells c;
    Known *k;
    u64 key;
    if (tw * th > MAXDOT) return 0;
    for (int r = r0; r <= r1; r++)
        for (int q = c0; q <= c1; q++) cell_put(g_tex, tw, (q - c0) * (int)cs, (r - r0) * (int)cs, &nm[r][q], g);
    c.layer = layer, c.cc = g->cc, c.cs = (int)cs, c.n = n, c.rows = rows, c.tag = tag, c.charnum = nm[r0][c0].charnum, c.pal = nm[r0][c0].pal;
    c.tw = tw, c.th = th, c.tex = g_tex;
    {
        int form[8] = {layer, c.cc, c.cs, n, rows, tag, (int)c.pal, g_ctx}; /* not the character number: the texels say what it is */
        key = fnv(fnv(14695981039346656037ull, g_tex, (size_t)bytes), form, sizeof form);
    }
    if ((k = known(key))->used) return k->tag;
    k->used = 1, k->key = key, k->tag = SAT_G(text_cells)(&c), g_nknown++;
    sat_text_v2_log(&c, k->tag, key, x, y);
    return k->tag;
}

/* before a picture is composed: the planes' lettering cells, their blocks, the items and the map (1: any) */
int sat_text_v2_scan(const SatPlane *planes, const int *act, int nact, u32 w, u32 h, u32 ww, u32 ox)
{
    static int grid[GH][GW];
    static SatV2Name nm[GH][GW];
    for (int i = 0; i < sat_text_v2_n; i++) { /* last picture's boxes off the map */
        const SatTextV2Item *t = &sat_text_v2_it[i];
        for (int y = t->y; y < t->y + t->h; y++) memset(&sat_text_v2_map[(u32)y * g_mapw + (u32)t->x], 0, (size_t)t->w * sizeof(u16));
    }
    sat_text_v2_n = 0, sat_text_v2_bm = 0, g_gen++;
    if (!SAT_G(text_cells) || !sat_text_wanted() || w > 512u || h > SAT_MAX_H || ww > SAT_MAX_W) return 0;
    if ((g_ctx = SAT_G(text_cells)(NULL)) <= 0) return 0;
    g_mapw = ww;
    if (sat_text_v2_relog()) memset(g_known, 0, sizeof g_known), g_nknown = 0;
    for (int a = 0; a < nact; a++) {
        int n = act[a], cs, x0, y0, cols, rows;
        const SatPlane *g = &planes[n];
        u32 scx, scy;
        if (n > 3 || g->zx != 0x100u || g->zy != 0x100u) continue;
        if (g->bitmap && ((((u32)sat_v2_reg[0x9a] << 8 | sat_v2_reg[0x9b]) >> (8 * n)) & 15u)) continue; /* SCRCTL: line or vertical cell scroll */
        cs = (int)csz(g), scx = g->scx >> 8, scy = g->scy >> 8;
        y0 = -(int)(scy & (u32)(cs - 1)), x0 = -(int)ox - (int)((scx - ox) & (u32)(cs - 1));
        cols = ((int)ww - (int)ox - x0 + cs - 1) / cs, rows = ((int)h - y0 + cs - 1) / cs;
        if (cols > GW || rows > GH) continue;
        for (int r = 0; r < rows; r++)
            for (int c = 0; c < cols; c++) {
                SatV2Name *m = &nm[r][c];
                u32 key;
                if (g->bitmap) { /* a tile: asked about by its contents (known by them), not by a name */
                    m->charnum = ((scx + (u32)(x0 + c * cs)) & ((u32)g->bmw - 1u)) | ((scy + (u32)(y0 + r * cs)) & ((u32)g->bmh - 1u)) << 16;
                    m->pal = (u32)g->bmpal, m->sf = 0, m->hf = m->vf = 0;
                    grid[r][c] = bm_empty(g, m) ? 0 : ask(n, g, nm, r, c, r, c, 0, x0 + c * cs, y0 + r * cs);
                    continue;
                }
                sat_v2_name_at(g, scx + (u32)(x0 + c * cs), scy + (u32)(y0 + r * cs), m);
                key = name_key(n, m->charnum, m->pal, m->hf, m->vf);
                if (!name_get(key, &grid[r][c])) name_put(key, grid[r][c] = ask(n, g, nm, r, c, r, c, 0, x0 + c * cs, y0 + r * cs));
            }
        for (int r = 0; r < rows; r++)
            for (int c = 0; c < cols; c++) {
                int tag = grid[r][c], c1 = c, r1 = r, bt, X, Y, W, H;
                SatTextV2Item *t;
                if (tag <= 0) continue;
                while (c1 + 1 < cols && grid[r][c1 + 1] == tag) c1++;
                for (;;) { /* the row under it: the same tag over exactly the same cells */
                    int same = r1 + 1 < rows && (c == 0 || grid[r1 + 1][c - 1] != tag) && (c1 + 1 >= cols || grid[r1 + 1][c1 + 1] != tag);
                    for (int q = c; same && q <= c1; q++) same = grid[r1 + 1][q] == tag;
                    if (!same) break;
                    r1++;
                }
                for (int rr = r; rr <= r1; rr++)
                    for (int q = c; q <= c1; q++) grid[rr][q] = -1;
                bt = ask(n, g, nm, r, c, r1, c1, tag, x0 + c * cs, y0 + r * cs);
                X = x0 + c * cs + (int)ox, Y = y0 + r * cs, W = (c1 - c + 1) * cs, H = (r1 - r + 1) * cs;
                if (X < 0) W += X, X = 0;
                if (Y < 0) H += Y, Y = 0;
                if (X + W > (int)ww) W = (int)ww - X;
                if (Y + H > (int)h) H = (int)h - Y;
                if (!bt || W <= 0 || H <= 0 || sat_text_v2_n >= SAT_V2_ITEMS) continue;
                t = &sat_text_v2_it[sat_text_v2_n++];
                t->x = X, t->y = Y, t->w = W, t->h = H, t->tag = bt, t->layer = n;
                sat_text_v2_layer[sat_text_v2_n] = (u8)n, sat_text_v2_bm |= (u32)g->bitmap << n;
                for (int y = Y; y < Y + H; y++)
                    for (int x = X; x < X + W; x++) sat_text_v2_map[(u32)y * ww + (u32)x] = (u16)sat_text_v2_n;
            }
    }
    return sat_text_v2_n > 0;
}
