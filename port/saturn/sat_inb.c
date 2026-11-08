/* In-between frames for the Saturn (Display > In-between frames, --in-between on|off). A play run has them, online play's too
 * (drawn in sat_net.c's waits); a check never does (--det, the fixed clock, --fast, --headless), so goldens never change.
 * The game's picture changes at most once a vertical blank (50 or 60 a second) and the showing thread (base/present_cpu.c)
 * shows 144. Each tick's picture is held back one vertical blank, and the slots between are made here, on the game's
 * thread while it waits for the next vertical blank (sat_sys.c): the chip state behind the two newest pictures, kept as
 * copies (VDP1 VRAM as each list was drawn; VDP2 VRAM, colour RAM and registers as each picture was composed), is blended
 * and drawn by the same VDP1 and VDP2 code, posted, and the live chips are put back as they were.
 *  VDP1: each drawing command of the newer list is paired with one of the older list with the same command, draw mode,
 *  colour, texture and size whose centre is nearest (within 80 dots; SGL sorts by depth, so the order moves), and its
 *  vertices are blended in screen space. An unpaired one is drawn where it is now.
 *  VDP2: NBG0-3 scroll, NBG0/1 line scroll tables and RBG0's rotation parameter A table are blended; a jump of more than
 *  64 dots is a cut and is not blended.
 *
 * Nearest existing: runtime/frame_extra.c with pvr/pvr_blend.c (the Dreamcast's: a parsed PowerVR scene blended per
 * vertex). The Saturn has no scene, only the chips' own lists and tables, so those are blended instead. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <windows.h>
#include "sat.h"
#include "sat_sys.h"
#include "sat_vdp.h"
#include "present_cpu.h"
#include "sat_net.h"

int sat_inb_on = -1; /* --in-between on|off, the menu's; -1 not set: on in a play run */
static int g_allowed = -1, g_live, g_drawing;

enum { NBUF = 4, MAXCMD = 4096, CUT = 64, NEAR_DOTS = 80 };
typedef struct {
    u8 v2[0x80000], cram[0x1000], reg[0x200];
    int v1; /* the VDP1 VRAM copy its sprites were drawn from, -1 unknown */
    u32 vb;
} Pic;
typedef struct {
    u32 at, key;
    int nv, lx, ly, cx, cy, used, off[4], loc[4], x[4], y[4];
} Cmd;
static u8 *g_v1[NBUF];
static int g_fb_buf[2] = {-1, -1}; /* the copy each framebuffer was drawn from */
static Pic *g_pic[2];              /* [0] the older picture, [1] the newer (held back) */
static int g_npic, g_paired_for = -1;
static u32 *g_held, g_held_w, g_held_h, g_held_vb;
static int g_has_held;
static struct {
    u8 v1[0x80000], v2[0x80000], cram[0x1000], reg[0x200], fb[0x40000];
} *g_save;
static Cmd *g_ca, *g_cb;
static int *g_order, *g_pair, g_na, g_nb;
static LONGLONG g_t0, g_period, g_lead;
static int g_k, g_n;
static u32 g_made, g_late, g_hist[80], g_paired;
static u32 g_shot_vb, g_shot_n = 3; /* --in-between-shot VB[+N],PREFIX (a test aid): what is posted for N blanks from VB, */
static char g_shot[260];             /* held in memory and written at exit as PREFIX_VB_K.png */
static struct { u32 *px, w, h, vb; int k; } g_shots[96];
static int g_nshots;

void sat_inb_shot_option(const char *v)
{
    const char *c = strchr(v, ','), *n = strchr(v, '+');
    g_shot_vb = (u32)strtoul(v, NULL, 10);
    if (n && (!c || n < c)) g_shot_n = (u32)strtoul(n + 1, NULL, 10);
    if (c) strncpy(g_shot, c + 1, sizeof g_shot - 1);
}

static int shooting(u32 vb) { return g_shot_vb && g_shot[0] && vb >= g_shot_vb && vb < g_shot_vb + g_shot_n; }

/* k 0: the tick's own picture; 1..n the in-betweens after it */
static void shot(const u32 *px, u32 w, u32 h, u32 vb, int k)
{
    if (!shooting(vb) || g_nshots >= 96 || !(g_shots[g_nshots].px = malloc((size_t)w * h * 4u))) return;
    memcpy(g_shots[g_nshots].px, px, (size_t)w * h * 4u);
    g_shots[g_nshots].w = w, g_shots[g_nshots].h = h, g_shots[g_nshots].vb = vb, g_shots[g_nshots++].k = k;
}

static u32 w16(const u8 *m, u32 a) { a &= 0x7fffeu; return ((u32)m[a] << 8) | m[a + 1u]; }
static void p16(u8 *m, u32 a, u32 v) { a &= 0x7fffeu; m[a] = (u8)(v >> 8); m[a + 1u] = (u8)v; }
static s32 r32(const u8 *m, u32 a) { return (s32)((w16(m, a) << 16) | w16(m, a + 2u)); }
static void p32(u8 *m, u32 a, s32 v) { p16(m, a, (u32)v >> 16); p16(m, a + 2u, (u32)v & 0xffffu); }
static int sx13(u32 v) { return (int)((s32)(v << 19) >> 19); }
static s32 mix(s32 a, s32 b, int t) { return a + (s32)(((s64)b - a) * t >> 8); }
static int fbi(const u8 *fb) { return fb == sat_v1_fb[0] ? 0 : 1; }

int sat_inb_drawing(void) { return g_drawing; }
int sat_inb_live(void) { return g_live; }

/* the first vertical blank of the run decides whether this run may have them, and makes room */
static void decide(void)
{
    LARGE_INTEGER f;
    g_allowed = !sat_video_headless() && cpu_present_threaded() && !host_fixed_clock() && (sat_net_on() || (!sat_sys_fast() && !sat_sys_det()));
    if (sat_inb_on < 0) sat_inb_on = g_allowed;
    if (!g_allowed) return;
    for (int i = 0; i < NBUF; i++) g_v1[i] = (u8 *)malloc(0x80000);
    g_pic[0] = (Pic *)malloc(sizeof(Pic)), g_pic[1] = (Pic *)malloc(sizeof(Pic));
    g_save = malloc(sizeof *g_save), g_held = (u32 *)malloc(SAT_MAX_W * SAT_MAX_H * 4);
    g_ca = (Cmd *)malloc(MAXCMD * sizeof(Cmd)), g_cb = (Cmd *)malloc(MAXCMD * sizeof(Cmd));
    g_order = (int *)malloc(MAXCMD * sizeof(int)), g_pair = (int *)malloc(MAXCMD * sizeof(int));
    if (!g_v1[NBUF - 1] || !g_pic[1] || !g_save || !g_held || !g_cb || !g_pair) g_allowed = 0;
    QueryPerformanceFrequency(&f);
    g_period = f.QuadPart / (sat_sys_hz() ? sat_sys_hz() : 50u);
    g_lead = f.QuadPart / 500; /* 2 ms until the first ones are timed */
    port_log("in-between: %s (%s)\n", g_allowed && sat_inb_on ? "on" : "off", g_allowed ? "a play run" : "not a play run: none");
}

/* sat_vdp.c: VDP1 has just drawn its list into fb */
void sat_inb_drawn(const u8 *fb)
{
    int b, o = g_fb_buf[1 - fbi(fb)];
    if (!g_live) return;
    for (b = 0; b < NBUF; b++)
        if (b != o && !(g_npic > 0 && g_pic[1]->v1 == b) && !(g_npic > 1 && g_pic[0]->v1 == b)) break;
    memcpy(g_v1[b], sat_v1_vram, sizeof sat_v1_vram);
    g_fb_buf[fbi(fb)] = b;
}

/* sat_video.c, before a vertical blank's picture is composed: the chips' state behind it */
void sat_inb_vblank(void)
{
    Pic *p;
    if (g_allowed < 0) decide();
    if (g_live != (sat_inb_on > 0 && g_allowed > 0)) { /* switched: start clean */
        g_live = sat_inb_on > 0 && g_allowed > 0;
        g_npic = g_has_held = 0;
        g_fb_buf[0] = g_fb_buf[1] = -1;
    }
    if (!g_live) return;
    p = g_pic[0], g_pic[0] = g_pic[1], g_pic[1] = p;
    memcpy(p->v2, sat_v2_vram, sizeof p->v2);
    memcpy(p->cram, sat_v2_cram, sizeof p->cram);
    memcpy(p->reg, sat_v2_reg, sizeof p->reg);
    p->v1 = g_fb_buf[fbi(sat_v1_display_fb())];
    p->vb = sat_vblank_count();
    if (g_npic < 2) g_npic++;
    g_paired_for = -1;
}

/* sat_video.c: the picture just composed is shown one vertical blank later; the one held back goes now */
void sat_inb_post(void)
{
    LARGE_INTEGER now;
    unsigned fps = cpu_present_fps() ? cpu_present_fps() : 144u, hz = sat_sys_hz() ? sat_sys_hz() : 50u;
    if (g_has_held) sat_wide_post(g_held, g_held_w, g_held_h, g_held_vb), shot(g_held, g_held_w, g_held_h, g_held_vb, 0);
    memcpy(g_held, sat_frame, (size_t)sat_frame_w * sat_frame_h * 4u);
    g_held_w = sat_frame_w, g_held_h = sat_frame_h, g_held_vb = sat_vblank_count();
    g_has_held = 1;
    QueryPerformanceCounter(&now);
    g_t0 = now.QuadPart;
    g_n = (int)((fps + hz - 1u) / hz) - 1;
    g_k = 1;
}

/* ---- VDP1: the list's drawing commands, each with the vertices to blend (positions with the local coordinate added).
 * The key is the command, draw mode, colour, texture and size; a polygon or line (4-6) keys on command and draw mode only:
 * SGL lights its colour each frame and leaves old words where a sprite keeps its texture and size (a robot's polygons
 * changed keys from one blank to the next and swapped or lost partners, so the robot tore in between). */
static void take(const u8 *m, u32 cmd, u32 ctrl, int lx, int ly, Cmd *c)
{
    static const int k_off[7][4] = {{0x0c}, {0x0c, 0x14}, {0x0c, 0x10, 0x14, 0x18}, {0}, {0x0c, 0x10, 0x14, 0x18}, {0x0c, 0x10, 0x14, 0x18}, {0x0c, 0x10}};
    static const int k_nv[7] = {1, 2, 4, 0, 4, 4, 2};
    u32 comm = ctrl & 15u, h = 2166136261u, k[5] = {ctrl & 0x0f3fu, w16(m, cmd + 4u), w16(m, cmd + 6u), w16(m, cmd + 8u), w16(m, cmd + 0xau)};
    int np = 0;
    for (int i = 0; i < (comm >= 4u ? 2 : 5); i++) h = (h ^ k[i]) * 16777619u; /* a polygon or line: its colour is lit anew */
    c->at = cmd, c->key = h, c->lx = lx, c->ly = ly, c->used = 0, c->cx = c->cy = 0, c->nv = k_nv[comm];
    for (int i = 0; i < c->nv; i++) c->off[i] = k_off[comm][i], c->loc[i] = 1;
    if (comm == 1u && (ctrl >> 8) & 15u) c->off[1] = 0x10, c->loc[1] = 0; /* a scaled sprite by a zoom point: width, height */
    for (int i = 0; i < c->nv; i++) {
        c->x[i] = sx13(w16(m, cmd + (u32)c->off[i])) + (c->loc[i] ? lx : 0);
        c->y[i] = sx13(w16(m, cmd + (u32)c->off[i] + 2u)) + (c->loc[i] ? ly : 0);
        if (c->loc[i]) c->cx += c->x[i], c->cy += c->y[i], np++;
    }
    if (np) c->cx /= np, c->cy /= np;
}

/* walked as sat_vdp1_list.c walks it: skip, jump, call and return */
static int listing(const u8 *m, Cmd *c)
{
    u32 cmd = 0, ret = 0;
    int n = 0, steps = 0, called = 0, lx = 0, ly = 0;
    while (steps++ < 20000 && n < MAXCMD) {
        u32 ctrl = w16(m, cmd), link = w16(m, cmd + 2u) * 8u, comm = ctrl & 15u;
        if (ctrl & 0x8000u) break;
        if (!(ctrl & 0x4000u)) {
            if (comm == 10u) lx = sx13(w16(m, cmd + 0xcu)), ly = sx13(w16(m, cmd + 0xeu));
            else if (comm <= 6u && comm != 3u) take(m, cmd, ctrl, lx, ly, &c[n++]);
        }
        switch ((ctrl >> 12) & 3u) {
        case 0: cmd += 0x20u; break;
        case 1: cmd = link; break;
        case 2: if (!called) ret = cmd + 0x20u; called = 1, cmd = link; break;
        default: cmd = called ? ret : cmd + 0x20u, called = 0; break;
        }
        cmd &= 0x7ffe0u;
    }
    return n;
}

static int by_key(const void *a, const void *b)
{
    const Cmd *x = &g_ca[*(const int *)a], *y = &g_ca[*(const int *)b];
    return x->key != y->key ? (x->key < y->key ? -1 : 1) : (x->cx > y->cx) - (x->cx < y->cx);
}

typedef struct { int cost, j, i; } Edge;
enum { CAND = 3, MAXEDGE = CAND * MAXCMD, SHAPE = 24 };
static Edge g_edge[MAXEDGE];
static int by_cost(const void *a, const void *b)
{
    const Edge *x = a, *y = b;
    if (x->cost != y->cost) return x->cost < y->cost ? -1 : 1;
    return x->j != y->j ? (x->j < y->j ? -1 : 1) : (x->i > y->i) - (x->i < y->i);
}

/* once a vertical blank: the newer list's commands paired with the older's. A candidate shares the key, sits within
 * NEAR_DOTS and keeps its shape (each vertex within SHAPE dots of its place about the centre); the cheapest pairs over the
 * whole list go first, so like polygons side by side (a robot's) don't swap partners. The older list is sorted by key and
 * centre X, so each looks only at its window, and keeps its CAND cheapest */
static void pair(const u8 *older, const u8 *newer)
{
    int ne = 0;
    g_na = listing(older, g_ca), g_nb = listing(newer, g_cb), g_paired = 0;
    for (int i = 0; i < g_na; i++) g_order[i] = i;
    qsort(g_order, (size_t)g_na, sizeof(int), by_key);
    for (int j = 0; j < g_nb; j++) {
        const Cmd *b = &g_cb[j];
        int lo = 0, hi = g_na, nc = 0;
        Edge c[CAND + 1];
        g_pair[j] = -1;
        while (lo < hi) {
            const Cmd *m = &g_ca[g_order[(lo + hi) / 2]];
            if (m->key < b->key || (m->key == b->key && m->cx < b->cx - NEAR_DOTS)) lo = (lo + hi) / 2 + 1;
            else hi = (lo + hi) / 2;
        }
        for (; lo < g_na && g_ca[g_order[lo]].key == b->key && g_ca[g_order[lo]].cx <= b->cx + NEAR_DOTS; lo++) {
            const Cmd *a = &g_ca[g_order[lo]];
            int dx = a->cx - b->cx, dy = a->cy - b->cy, cost = dx * dx + dy * dy, ok = a->nv == b->nv && cost <= NEAR_DOTS * NEAR_DOTS;
            for (int v = 0; ok && v < b->nv; v++) {
                int ex = a->x[v] - b->x[v] - (b->loc[v] ? dx : 0), ey = a->y[v] - b->y[v] - (b->loc[v] ? dy : 0);
                cost += ex * ex + ey * ey, ok = ex * ex + ey * ey <= SHAPE * SHAPE;
            }
            if (!ok || (nc == CAND && cost >= c[CAND - 1].cost)) continue;
            int q = nc < CAND ? nc++ : CAND - 1;
            for (; q > 0 && c[q - 1].cost > cost; q--) c[q] = c[q - 1];
            c[q].cost = cost, c[q].j = j, c[q].i = g_order[lo];
        }
        for (int q = 0; q < nc; q++) g_edge[ne++] = c[q];
    }
    qsort(g_edge, (size_t)ne, sizeof(Edge), by_cost);
    for (int e = 0; e < ne; e++)
        if (!g_ca[g_edge[e].i].used && g_pair[g_edge[e].j] < 0) g_ca[g_edge[e].i].used = 1, g_pair[g_edge[e].j] = g_edge[e].i, g_paired++;
}

/* the paired commands' vertices at t/256 of the way from the older list to the newer, into the live VRAM */
static void v1_blend(int t)
{
    for (int j = 0; j < g_nb; j++) {
        const Cmd *a, *b = &g_cb[j];
        if (g_pair[j] < 0) continue;
        a = &g_ca[g_pair[j]];
        for (int i = 0; i < b->nv; i++) {
            p16(sat_v1_vram, b->at + (u32)b->off[i], (u32)(mix(a->x[i], b->x[i], t) - (b->loc[i] ? b->lx : 0)) & 0xffffu);
            p16(sat_v1_vram, b->at + (u32)b->off[i] + 2u, (u32)(mix(a->y[i], b->y[i], t) - (b->loc[i] ? b->ly : 0)) & 0xffffu);
        }
    }
}

/* ---- VDP2 */
static u32 R2(const u8 *r, u32 o) { return ((u32)r[o] << 8) | r[o + 1u]; }
static void W2(u32 o, u32 v) { sat_v2_reg[o] = (u8)(v >> 8), sat_v2_reg[o + 1u] = (u8)v; }

/* an NBG's scroll: 11 bits of dots and 8 of fraction (rd 0: dots only), blended the short way round */
static void scroll(const Pic *o, const Pic *n, u32 ri, u32 rd, int t)
{
    const s32 full = 0x800 << 8;
    s32 a = (s32)(((R2(o->reg, ri) & 0x7ffu) << 8) | (rd ? R2(o->reg, rd) >> 8 : 0u));
    s32 b = (s32)(((R2(n->reg, ri) & 0x7ffu) << 8) | (rd ? R2(n->reg, rd) >> 8 : 0u));
    s32 d = ((b - a + full / 2) & (full - 1)) - full / 2, v;
    if (d > CUT << 8 || d < -(CUT << 8)) return;
    v = (a + (s32)((s64)d * t >> 8)) & (full - 1);
    W2(ri, (R2(n->reg, ri) & ~0x7ffu) | (u32)(v >> 8));
    if (rd) W2(rd, ((u32)(v & 0xff) << 8) | (R2(n->reg, rd) & 0xffu));
}

static u32 table(const u8 *r, u32 hi, u32 lo) { return ((((R2(r, hi) & 7u) << 16) | (R2(r, lo) & 0xfffeu)) * 2u) & 0x7ffffu; }

static void v2_blend(const Pic *o, const Pic *n, int t)
{
    static const u32 k_scroll[6][2] = {{0x70, 0x72}, {0x74, 0x76}, {0x80, 0x82}, {0x84, 0x86}, {0x90, 0}, {0x92, 0}};
    u32 vres = (R2(n->reg, 0) >> 4) & 3u, h = vres == 0 ? 224u : vres == 1 ? 240u : 256u;
    for (int i = 0; i < 6; i++) scroll(o, n, k_scroll[i][0], k_scroll[i][1], t);
    scroll(o, n, 0x94, 0, t), scroll(o, n, 0x96, 0, t);
    for (u32 g = 0; g < 2; g++) { /* NBG0/1 line scroll: X, Y and zoom entries of 4 bytes, one set per 2^LSS lines */
        u32 c = (R2(n->reg, 0x9a) >> (8u * g)) & 0x3fu, per = ((c >> 1) & 1u) + ((c >> 2) & 1u) + ((c >> 3) & 1u);
        u32 ta = table(o->reg, 0xa0 + 4u * g, 0xa2 + 4u * g), tb = table(n->reg, 0xa0 + 4u * g, 0xa2 + 4u * g);
        if (!per || c != ((R2(o->reg, 0x9a) >> (8u * g)) & 0x3fu)) continue;
        for (u32 e = 0; e < (h >> ((c >> 4) & 3u)) * per; e++) {
            s32 a = r32(o->v2, ta + 4u * e), b = r32(n->v2, tb + 4u * e), d = (s32)((u32)(b - a) << 5) >> 5;
            if (d <= CUT << 16 && d >= -(CUT << 16)) p32(sat_v2_vram, tb + 4u * e, a + (s32)((s64)d * t >> 8));
        }
    }
    if ((R2(o->reg, 0x20) & R2(n->reg, 0x20) & 0x10u)) { /* RBG0's rotation parameter A: start, deltas, matrix, centres, shift, scale */
        u32 ta = table(o->reg, 0xbc, 0xbe), tb = table(n->reg, 0xbc, 0xbe);
        for (u32 f = 0; f <= 0x50; f += 4) {
            if (f >= 0x34 && f <= 0x40) {
                for (u32 s = f; s < f + 4u; s += 2)
                    p16(sat_v2_vram, tb + s, (u32)mix((s16)w16(o->v2, ta + s), (s16)w16(n->v2, tb + s), t) & 0xffffu);
            } else {
                p32(sat_v2_vram, tb + f, mix(r32(o->v2, ta + f), r32(n->v2, tb + f), t));
            }
        }
    }
}

/* one in-between frame at t/256 of the way from the older picture to the newer */
static void draw(int k, int t)
{
    Pic *o = g_pic[0], *n = g_pic[1];
    LARGE_INTEGER a, b, f;
    u8 *fb;
    double ms;
    sat_perf_wait_begin(); /* not the game's work: the tick's time leaves it out */
    QueryPerformanceCounter(&a);
    g_drawing = 1;
    if (g_paired_for != (int)n->vb) pair(g_v1[o->v1], g_v1[n->v1]), g_paired_for = (int)n->vb;
    memcpy(g_save->v1, sat_v1_vram, sizeof g_save->v1);
    memcpy(g_save->v2, sat_v2_vram, sizeof g_save->v2);
    memcpy(g_save->cram, sat_v2_cram, sizeof g_save->cram);
    memcpy(g_save->reg, sat_v2_reg, sizeof g_save->reg);
    memcpy(sat_v1_vram, g_v1[n->v1], sizeof sat_v1_vram);
    memcpy(sat_v2_vram, n->v2, sizeof n->v2);
    memcpy(sat_v2_cram, n->cram, sizeof n->cram);
    memcpy(sat_v2_reg, n->reg, sizeof n->reg);
    v1_blend(t);
    v2_blend(o, n, t);
    sat_v1_fb_flip(); /* VDP1 draws into the framebuffer VDP2 shows */
    fb = sat_v1_draw_fb();
    sat_wide_save(fb, g_save->fb); /* and its wide twin (sat_wide.c) */
    sat_v1_erase_draw();
    sat_v1_draw();
    sat_v1_fb_flip();
    sat_compose();
    sat_wide_post(sat_frame, sat_frame_w, sat_frame_h, n->vb);
    shot(sat_frame, sat_frame_w, sat_frame_h, o->vb, k);
    sat_wide_restore(fb, g_save->fb);
    memcpy(sat_v1_vram, g_save->v1, sizeof sat_v1_vram);
    memcpy(sat_v2_vram, g_save->v2, sizeof g_save->v2);
    memcpy(sat_v2_cram, g_save->cram, sizeof g_save->cram);
    memcpy(sat_v2_reg, g_save->reg, sizeof g_save->reg);
    g_drawing = 0;
    QueryPerformanceCounter(&b);
    QueryPerformanceFrequency(&f);
    g_lead = (g_lead * 7 + (b.QuadPart - a.QuadPart) * 3 / 2) / 8; /* started that much before it is due */
    ms = (double)(b.QuadPart - a.QuadPart) * 1000.0 / (double)f.QuadPart;
    g_hist[ms * 4 < 79 ? (int)(ms * 4) : 79]++;
    g_made++;
    sat_perf_wait_end();
}

/* when the next in-between frame is due (QueryPerformanceCounter), 0 for none: sat_sys.c's wait wakes for it */
LONGLONG sat_inb_due(void)
{
    if (!g_live || g_npic < 2 || !g_has_held || g_k > g_n || g_pic[0]->v1 < 0 || g_pic[1]->v1 < 0) return 0;
    return g_t0 + g_period * g_k / (g_n + 1) - g_lead;
}

/* sat_sys.c, at a safe point on the real clock: the in-between frame that is due, if there is time before the blank */
void sat_inb_poll(LONGLONG now, LONGLONG next)
{
    LONGLONG due = sat_inb_due();
    int shoot;
    if (!due || now < due) return;
    shoot = shooting(g_pic[0]->vb); /* the test aid: none skipped */
    while (!shoot && g_k < g_n && now >= g_t0 + g_period * (g_k + 1) / (g_n + 1) - g_lead) g_k++, g_late++;
    if (!shoot && now + g_lead > next) {
        g_k = g_n + 1, g_late++;
        return;
    }
    draw(g_k, g_k * 256 / (g_n + 1));
    g_k++;
}

void sat_inb_report(void)
{
    u32 at = 0, seen = 0, p50 = 0, p99 = 0;
    for (int i = 0; i < g_nshots; i++) {
        char p[300];
        snprintf(p, sizeof p, "%s_%05u_%d.png", g_shot, g_shots[i].vb, g_shots[i].k);
        sat_png_write(p, g_shots[i].px, g_shots[i].w, g_shots[i].h), free(g_shots[i].px);
    }
    g_nshots = 0;
    if (!g_made && !g_late) return;
    for (u32 i = 0; i < 80; i++) {
        seen += g_hist[i];
        if (!p50 && seen * 2 >= g_made) p50 = i + 1;
        if (!p99 && seen * 100 >= g_made * 99) p99 = i + 1;
        if (g_hist[i]) at = i + 1;
    }
    port_log("in-between: %u made, %u skipped (no time before the blank); draw ms p50 <%.2f p99 <%.2f worst <%.2f; last "
             "list %d of %d commands paired\n", g_made, g_late, p50 / 4.0, p99 / 4.0, at / 4.0, (int)g_paired, g_nb);
}
