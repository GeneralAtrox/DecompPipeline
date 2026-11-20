/* Kit screens drawn at twice the Saturn's dot size (sat_tlobby.c's TEAM FIGHT lobby and fight selection). While one is
 * up, the picture shown, shot and dumped is the screen's own, made each vertical blank: the game's board as it was when
 * the screen opened (taken a few blanks later, once VDP1 has shown a list of its own: the screen's code draws nothing
 * of the game's), at two window dots a Saturn dot, wider than the 4:3 screen in a wide window (the board's edges
 * mirrored beside it), then the list the screen's code remakes each frame: rectangles, the menus' dark panel (shaded as
 * sat_panel.c shades the game's own), cells of the game's text plane as the game named them (its small and large type,
 * at half or whole size: the letters' own dots and colours, read from VDP2 as the composer reads them), the two marks
 * the small type lacks (? and %, drawn here in its colours) and pictures (robots, sat_robopic.c). The game runs on
 * underneath (its frame start and step, pads, sound and vertical blanks); only the picture is the screen's. In-between
 * frames (sat_inb.c) stop while a screen is up.
 *
 * Nearest existing: sat_menus.c's sat_menu_line and sat_menu_frame (the game's type in its own text map, whole cells at
 * the Saturn's size only) and sat_panel.c (the panel's shade); these screens need half-size letters and pictures. */
#include <stdlib.h>
#include <string.h>
#include "sat.h"
#include "sat_sys.h"
#include "sat_vdp.h"
#include "sat_screen.h"
#include "present_cpu.h"

enum { RECT, PANEL, CELL, MARK, IMAGE, OPS = 6000, WAIT = 10 }; /* VDP1 shows a list a few blanks after it is drawn: the board is taken once the game's are gone */
typedef struct {
    u8 kind, scale;
    s16 x, y, w, h;
    u32 a, b; /* RECT colour; PANEL frame colours; CELL the map word; MARK the mark and slot; IMAGE its size */
    const u32 *img;
} Op;
static Op g_op[OPS];
static int g_nop, g_lost, g_up, g_wait;
static u32 *g_bg, g_bw, g_bh;                   /* the board, in the Saturn's dots */
static u32 *g_base, *g_px, g_pw, g_ph, g_side; /* the board at half dots (sides mirrored), the picture; half dots a side */
static u32 g_ink[16][2], g_inked;              /* each palette slot's letter and shadow colours (g_inked: found) */
static int g_plane = -2;                       /* the text plane (-1: not found) */

/* the two marks the small type has not got, 8 wide and 7 tall (a row a byte, the left dot the top bit) */
static const u8 k_mark[2][7] = {{0x78, 0xc4, 0x0c, 0x18, 0x30, 0x00, 0x30}, {0xc6, 0xcc, 0x18, 0x30, 0x66, 0xc6, 0x00}};

void sat_screen_open(void)
{
    g_up = 1, g_wait = WAIT, g_nop = 0, g_inked = 0, g_plane = -2;
    port_log("screen: a kit screen opens at vertical blank %u (the game's board, then the screen's own list)\n", sat_vblank_count());
}
void sat_screen_close(void)
{
    if (g_up) port_log("screen: closed at vertical blank %u%s\n", sat_vblank_count(), g_lost ? " (some of its list was lost: too long)" : "");
    g_up = 0, g_lost = 0;
}
int sat_screen_on(void) { return g_up; }
int sat_screen_ready(void) { return g_up && g_wait < 0 && g_bg; }
void sat_screen_clear(void) { g_nop = 0; }
int sat_screen_w(void) { return 2 * (int)(g_bw ? g_bw : sat_frame_w); }
int sat_screen_h(void) { return 2 * (int)(g_bh ? g_bh : sat_frame_h); }
int sat_screen_side(void) { return 2 * sat_wide_want; }

static Op *add(int kind, int x, int y, int w, int h)
{
    Op *o;
    if (g_nop >= OPS) return g_lost++, NULL;
    o = &g_op[g_nop++];
    memset(o, 0, sizeof *o);
    o->kind = (u8)kind, o->x = (s16)x, o->y = (s16)y, o->w = (s16)w, o->h = (s16)h, o->scale = 1;
    return o;
}
void sat_screen_rect(int x, int y, int w, int h, u32 rgb)
{
    Op *o = add(RECT, x, y, w, h);
    if (o) o->a = rgb;
}
void sat_screen_panel(int x, int y, int w, int h, u32 border, u32 inner)
{
    Op *o = add(PANEL, x, y, w, h);
    if (o) o->a = border, o->b = inner;
}
void sat_screen_cell(int x, int y, u32 word, int scale)
{
    Op *o = add(CELL, x, y, 8 * scale, 8 * scale);
    if (o) o->a = word, o->scale = (u8)scale;
}
int sat_screen_text(int x, int y, const char *s, u32 slot, int scale)
{
    int x0 = x;
    u32 red = slot & 0x100u;
    slot &= 0xffu;
    for (; *s; s++, x += 8 * scale) {
        Op *o;
        if (*s == ' ') continue;
        if (*s != '?' && *s != '%') {
            if ((o = add(CELL, x, y, 8 * scale, 8 * scale)) != NULL) o->a = sat_menu_cell(*s, slot), o->b = red, o->scale = (u8)scale;
            continue;
        }
        if ((o = add(MARK, x, y, 8 * scale, 8 * scale)) != NULL) o->a = *s == '%', o->b = slot | red, o->scale = (u8)scale;
    }
    return x - x0;
}
/* s broken into lines of at most n letters at its spaces; the number of lines (at most 4) */
int sat_screen_wrap(const char *s, int n, char out[4][32])
{
    int k = 0, len = 0;
    out[0][0] = 0;
    for (const char *w = s; *w && k < 4;) {
        int wl = 0;
        while (w[wl] && w[wl] != ' ') wl++;
        if (len && len + 1 + wl > n) {
            if (++k == 4) break;
            out[k][0] = 0, len = 0;
        }
        if (len) out[k][len++] = ' ';
        for (int i = 0; i < wl && len < 31; i++) out[k][len++] = w[i];
        out[k][len] = 0;
        w += wl;
        while (*w == ' ') w++;
    }
    return k < 4 ? k + 1 : 4;
}

void sat_screen_image(int x, int y, int w, int h, const u32 *argb, int iw, int ih)
{
    Op *o = add(IMAGE, x, y, w, h);
    if (o) o->img = argb, o->a = (u32)iw, o->b = (u32)ih;
}

/* ---- the picture ---- */
static void dot(int x, int y, u32 c)
{
    x += (int)g_side;
    if (x >= 0 && y >= 0 && (u32)x < g_pw && (u32)y < g_ph) g_px[(u32)y * g_pw + (u32)x] = c;
}
static void fill(int x, int y, int w, int h, u32 c)
{
    for (int j = 0; j < h; j++)
        for (int i = 0; i < w; i++) dot(x + i, y + j, c);
}

/* the plane whose map holds the game's text map (once a screen: the game does not move it while one is up) */
static const SatPlane *text_plane(void)
{
    u32 at = sat_menu_map() & 0x7ffffu;
    if (g_plane == -2) {
        g_plane = -1;
        for (int n = 0; n < 4 && g_plane < 0; n++) {
            const SatPlane *g = sat_v2_plane(n);
            u32 page = (u32)(g->charpx == 8 ? 4096 : 1024) * (g->pn1w ? 2u : 4u) * (u32)g->plw * (u32)g->plh;
            for (int i = 0; i < 4 && g->on && !g->bitmap; i++)
                if (at >= g->map[i] && at < g->map[i] + page) g_plane = n;
        }
        port_log("screen: the game's text map %08x is %s\n", sat_menu_map(), g_plane < 0 ? "on no plane shown: no letters" : "on a plane shown");
        if (g_plane >= 0) port_log("screen: text plane NBG%d: %d-dot cells, colour mode %d, one-word names %d\n", g_plane,
                                   sat_v2_plane(g_plane)->charpx, sat_v2_plane(g_plane)->cc, sat_v2_plane(g_plane)->pn1w);
    }
    return g_plane < 0 ? NULL : sat_v2_plane(g_plane);
}

/* dot (cx, cy) of the 8-dot cell a one-word name names, as the plane draws it: 1 and its colour when it shows */
static int cell_dot(const SatPlane *g, u32 pn, u32 cx, u32 cy, u32 *rgb)
{
    u32 ch, pal, d, base;
    if (!g->pn1w || g->charpx != 8 || g->cc > 1) return 0;
    if (g->cnsm) {
        ch = (pn & 0xfffu) | (((u32)g->scn & 0x1cu) << 10);
    } else {
        ch = (pn & 0x3ffu) | (((u32)g->scn & 0x1fu) << 10);
        if ((pn >> 10) & 1u) cx = 7u - cx;
        if ((pn >> 11) & 1u) cy = 7u - cy;
    }
    pal = g->cc == 1 ? ((pn >> 12) & 7u) << 4 : ((pn >> 12) & 15u) | ((u32)g->splt << 4);
    base = ch * 0x20u;
    if (g->cc == 0) {
        d = sat_v2_vram[(base + cy * 4u + cx / 2u) & 0x7ffffu];
        d = cx & 1u ? d & 15u : d >> 4;
        if (!d) return 0;
        *rgb = sat_cram_rgb((((pal & 0x7fu) << 4) | d) + (u32)g->craoff);
    } else {
        d = sat_v2_vram[(base + cy * 8u + cx) & 0x7ffffu];
        if (!d) return 0;
        *rgb = sat_cram_rgb((((pal & 0x70u) << 4) | d) + (u32)g->craoff);
    }
    return 1;
}

/* a colour with its red and blue swapped (text slot | 0x100: a blue slot drawn red, as the mock-ups' opponents) */
static u32 rb(u32 c, u32 red) { return red ? (c & 0xff00ff00u) | (c >> 16 & 0xffu) | (c & 0xffu) << 16 : c; }

static void draw_cell(const SatPlane *g, const Op *o)
{
    u32 c;
    for (u32 cy = 0; cy < 8u; cy++)
        for (u32 cx = 0; cx < 8u; cx++)
            if (cell_dot(g, o->a, cx, cy, &c))
                fill(o->x + (int)cx * o->scale, o->y + (int)cy * o->scale, o->scale, o->scale, rb(c, o->b & 0x100u));
}

/* a slot's letter and shadow colours: the brightest and the darkest dots of its A */
static void ink(const SatPlane *g, u32 slot)
{
    u32 w = sat_menu_cell('A', slot), c, best = 0, lo = 0x300u * 255u + 1u, k = slot & 15u;
    g_ink[k][0] = 0xffffffu, g_ink[k][1] = 0;
    for (u32 cy = 0; cy < 8u; cy++)
        for (u32 cx = 0; cx < 8u; cx++)
            if (cell_dot(g, w, cx, cy, &c)) {
                u32 sum = (c >> 16 & 255u) + (c >> 8 & 255u) + (c & 255u);
                if (sum >= best) best = sum, g_ink[k][0] = c;
                if (sum < lo) lo = sum, g_ink[k][1] = c;
            }
    g_inked |= 1u << k;
}

static void draw_mark(const SatPlane *g, const Op *o)
{
    const u8 *m = k_mark[o->a & 1u];
    u32 k = o->b & 15u;
    int s = o->scale;
    if (!(g_inked & (1u << k))) ink(g, o->b);
    for (int pass = 0; pass < 2; pass++) /* the shadow a dot right and down, then the mark over it */
        for (int y = 0; y < 7; y++)
            for (int x = 0; x < 8; x++)
                if (m[y] & (0x80u >> x)) {
                    if (pass) fill(o->x + x * s, o->y + y * s, s, s, rb(g_ink[k][0], o->b & 0x100u));
                    else fill(o->x + (x + 1) * s, o->y + y * s, s, 2 * s, rb(g_ink[k][1], o->b & 0x100u)),
                         fill(o->x + x * s, o->y + (y + 1) * s, s, s, rb(g_ink[k][1], o->b & 0x100u));
                }
}

static void draw_panel(const Op *o)
{
    for (int y = 0; y < o->h; y++)
        for (int x = 0; x < o->w; x++) {
            int px = o->x + x + (int)g_side, py = o->y + y, edge = x < 2 || y < 2 || x >= o->w - 2 || y >= o->h - 2;
            int in = x < 4 || y < 4 || x >= o->w - 4 || y >= o->h - 4;
            u32 *p;
            if (px < 0 || py < 0 || (u32)px >= g_pw || (u32)py >= g_ph) continue;
            p = &g_px[(u32)py * g_pw + (u32)px];
            *p = edge ? o->a : in ? o->b : sat_panel_mix(*p, (u32)(px - (int)g_side) >> 1, (u32)py >> 1);
        }
}

static void draw_image(const Op *o)
{
    for (int y = 0; y < o->h; y++)
        for (int x = 0; x < o->w; x++) {
            u32 c = o->img[(u32)(y * (int)o->b / o->h) * o->a + (u32)(x * (int)o->a / o->w)];
            if (c >> 24) dot(o->x + x, o->y + y, c & 0xffffffu);
        }
}

/* the board at half dots, the screen's edges mirrored beside it (a dot left of the screen shows the dot as far right of
 * its left edge; the right side the same) */
static int make_base(u32 side)
{
    u32 pw = 2u * g_bw + 2u * side, ph = 2u * g_bh;
    if (pw * ph > g_pw * g_ph || !g_base) {
        free(g_base), free(g_px);
        g_base = (u32 *)malloc((size_t)pw * ph * 4u), g_px = (u32 *)malloc((size_t)pw * ph * 4u);
        if (!g_base || !g_px) return port_log("screen: no memory for a %ux%u picture\n", pw, ph), g_pw = 0, 0;
    }
    g_pw = pw, g_ph = ph, g_side = side;
    for (u32 y = 0; y < ph; y++)
        for (u32 x = 0; x < pw; x++) {
            int sx = ((int)x - (int)side) >> 1;
            if (sx < 0) sx = -sx;
            if (sx >= (int)g_bw) sx = 2 * ((int)g_bw - 1) - sx;
            if (sx < 0) sx = 0;
            g_base[y * pw + x] = g_bg[(y >> 1) * g_bw + (u32)sx];
        }
    return 1;
}

/* the board from the picture just composed (a panel's marked dot as it shows) */
static int take_board(void)
{
    u32 n = sat_frame_w * sat_frame_h;
    free(g_bg);
    g_bg = (u32 *)malloc((size_t)n * 4u);
    if (!g_bg) return 0;
    for (u32 i = 0; i < n; i++) g_bg[i] = sat_frame[i] >> 24 ? sat_panel_shade(sat_frame[i]) : sat_frame[i] & 0xffffffu;
    g_bw = sat_frame_w, g_bh = sat_frame_h, g_pw = g_ph = 0;
    port_log("screen: the board taken at vertical blank %u (%ux%u); the screen's picture shows from now\n", sat_vblank_count(), g_bw, g_bh);
    return 1;
}

int sat_screen_make(const u32 **px, u32 *w, u32 *h)
{
    const SatPlane *g;
    u32 side = (u32)sat_screen_side() / 2u * 2u;
    if (!g_up) return 0;
    if (g_wait > 0 && --g_wait > 0) return -1;
    if (!g_bg || g_wait == 0) {
        g_wait = -1;
        if (!take_board()) return -1;
    }
    if ((!g_pw || side != g_side) && !make_base(side)) return -1;
    sat_game_screen_grab(); /* the game's pictures taken from what VDP1 drew this frame (the game's hook) */
    memcpy(g_px, g_base, (size_t)g_pw * g_ph * 4u);
    g = text_plane();
    for (int i = 0; i < g_nop; i++) {
        const Op *o = &g_op[i];
        switch (o->kind) {
        case RECT: fill(o->x, o->y, o->w, o->h, o->a); break;
        case PANEL: draw_panel(o); break;
        case CELL: if (g) draw_cell(g, o); break;
        case MARK: if (g) draw_mark(g, o); break;
        default: draw_image(o); break;
        }
    }
    *px = g_px, *w = g_pw, *h = g_ph;
    return 1;
}

void sat_screen_post(u32 vb) { cpu_present_post_wide(g_px, g_pw, g_ph, vb, 2u * g_bw); }
