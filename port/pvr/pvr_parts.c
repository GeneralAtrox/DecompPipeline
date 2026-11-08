/* game.json part_draw: which object part wrote which of a scene's vertices, the ground for in-between frames built
 * from game state (each part re-posed between two ticks with one matrix, everything else drawn as the tick drew it).
 *
 * The game's per-part 3D draw is wrapped (port/tools/part_draw.py): port_part_begin and _end bracket each call, and the
 * SDK's live list write positions (pvr_parts_lists, kamui2_scene.c) say how far each TA list got before and after it.
 * The lists are parsed only at the render (ta_parse.c), so a vertex is tied to its part there by its RAM address:
 * pvr_parts_vertex walks each list's part ranges in step with the parse.
 *
 *   --interp-parts-log on  one line every 600 scenes: the part draws, how many wrote to a list, and how many of the
 *                          scenes' polygon vertices fell inside a part's range (the rest: 2D, text, HUD, effects); and
 *                          the fit check below
 *
 * The fit check (log on only): a rigid part's vertices, as (x w, y w, w) with w = 1/z, are one affine map of its model
 * space in every tick, so between two ticks they are one affine map of each other, whatever the game's matrices and
 * screen mapping are. For each part that moved and kept its vertex count, that 3x4 map is fitted from the vertices
 * (least squares) and the worst vertex's distance in pixels from where the map puts it is counted under 0.5 and 2.
 * If nearly all fit, an in-between frame needs no game matrix: each part is its own vertices through the fitted map
 * blended from identity. Also tried with w = z, in case the TA's z is w itself. On the user's fight recording about 90%
 * of moved parts fit to 0.5 px and 95% to 2 px; the rest were vertices clamped at the near plane and weighted joints.
 * So a part keeps its vertices' order from tick to tick, and the in-between frames need no fit:
 *
 *   --interp-parts on      in-between frames from the parts (with --interp-scene): each vertex of a part that kept its
 *                          object, stamp and vertex count is blended from its own vertex a tick before, in (x w, y w,
 *                          w) with w = 1/z, so it moves as the part and the camera moved, in perspective. Vertices of
 *                          no part (2D, text, HUD, fades) and of a part that is new, gone or changed are drawn as the
 *                          latest tick drew them. Colours and texture coordinates are the latest tick's. No game code
 *                          runs and nothing is paired by screen position, so nothing can ghost. On by default
 *                          for a game with part_draw (PORT_PART_DRAW); off: screen blending (pvr_blend.c)
 *
 * What a blend can't show is held, as the tick before was shown (holds): a scene where half the parts' vertices are new
 * or gone (a cut, one logo for another) gets no in-between frames, and a part that turned back the way it came (a pulse
 * at its peak, a blow that recoils) is drawn where it was a tick before: a straight line through the turn puts it where
 * it never was. Checked against the real frames between with port/tools/interp_truth.py. */
#include <math.h>
#include <stdlib.h>
#include <string.h>
#include "pvr_int.h"
#if __has_include("port_config.h")
#include "port_config.h" /* game.json part_draw */
#endif
#ifndef PORT_PART_DRAW
#define PORT_PART_DRAW 0
#endif

#define MAX_PARTS 4096u
#define MAX_VTX 131072u /* polygon vertices of parts in a scene (a fight has about 14,000) */
#define NONE 0xffffffffu
#define LOG_EVERY 600u
#define RAM_CACHED 0x8c000000u
#define RAM_MASK 0x00ffffffu
#define HOLD_SHARE 2u /* 1 in HOLD_SHARE of a scene's part vertices new or gone: no in-between frames */
#define TURN_COS -0.5 /* a part's two moves (each 1 px or more, rms) more than 120 degrees apart: it turned */

typedef struct Part {
    const void *obj, *part;
    s32 stamp;
    f32 world[16];
    u32 from[PVRX_LISTS], to[PVRX_LISTS];
    u32 first, count; /* its vertices in the sorted store, for the fit check */
} Part;

typedef struct Seen {
    u32 part, pos; /* pos: its place in the store sorted by part */
    f32 x, y, z;
} Seen;

static Part g_parts[MAX_PARTS], g_prev[MAX_PARTS];
static u32 g_nparts, g_nprev, g_depth, g_over, g_log, g_nseen, g_seq, g_pair[MAX_PARTS];
int g_parts_blend = PORT_PART_DRAW;
static Seen g_seen[MAX_VTX];
static f32 g_vtx[MAX_VTX][3], g_vprev[MAX_VTX][3], g_vprev2[MAX_VTX][3]; /* 2: two ticks before */
static u32 g_first2[MAX_PARTS], g_pair_was[MAX_PARTS], g_nvprev, g_hold_frame, g_holds;
static u8 g_turned[MAX_PARTS];
static const u32 *g_ptrs; /* the SDK's write positions, one per list (uncached addresses) */
static u32 g_at[PVRX_LISTS], g_last_list = PVRX_LISTS, g_last_addr;
static struct {
    u32 scenes, draws, wrote, nested, vertices, tagged, most, moved, fit[2][2], hist[5], shown, held, turned;
} g_n;

int pvr_parts_option(const char *value) { return g_log = !strcmp(value, "on"), 2; }
int pvr_parts_blend_option(const char *value) { return g_parts_blend = !strcmp(value, "on"), 2; }
u32 pvr_parts_seq(void) { return g_seq; }
int pvr_parts_hold_frame(void) { return g_parts_blend && g_hold_frame; }
int pvr_parts_holds(void) { return g_parts_blend && g_holds; }

static u32 cached(u32 a) { return RAM_CACHED | (a & RAM_MASK); }

void pvr_parts_lists(const u32 *ptrs) { g_ptrs = ptrs; }

void port_part_begin(const void *obj, const void *part, u32 stamp_at, u32 world_at)
{
    Part *p;
    g_n.draws++;
    if (g_depth++) {
        g_n.nested++;
        return;
    }
    if (!g_ptrs || g_nparts >= MAX_PARTS) {
        g_over += g_ptrs != NULL;
        return;
    }
    p = &g_parts[g_nparts];
    p->obj = obj, p->part = part;
    p->stamp = *(const s16 *)((const u8 *)obj + stamp_at);
    memcpy(p->world, (const u8 *)part + world_at, sizeof p->world);
    for (u32 l = 0; l < PVRX_LISTS; l++) p->from[l] = cached(g_ptrs[l]);
}

void port_part_end(void)
{
    Part *p = &g_parts[g_nparts];
    int wrote = 0;
    if (!g_depth || --g_depth || !g_ptrs || g_nparts >= MAX_PARTS) return;
    for (u32 l = 0; l < PVRX_LISTS; l++) {
        p->to[l] = cached(g_ptrs[l]);
        wrote |= p->to[l] > p->from[l];
    }
    if (wrote) g_nparts++, g_n.wrote++;
}

/* ta_parse.c, for each polygon vertex in list order: the part whose range holds it (1-based), 0 for none. */
u32 pvr_parts_vertex(u32 list, u32 addr)
{
    u32 *at = &g_at[list < PVRX_LISTS ? list : 0];
    g_seq = 0;
    if (list >= PVRX_LISTS) return 0;
    if (list != g_last_list || addr < g_last_addr) *at = 0; /* a list or a pass starts again */
    g_last_list = list, g_last_addr = addr;
    g_n.vertices++;
    while (*at < g_nparts && (g_parts[*at].to[list] <= addr || g_parts[*at].to[list] <= g_parts[*at].from[list]))
        ++*at;
    if (*at >= g_nparts || addr < g_parts[*at].from[list]) return 0;
    g_n.tagged++;
    if ((g_log || g_parts_blend) && g_nseen < MAX_VTX) { /* the TA's x, y and z follow the control word */
        const f32 *f = (const f32 *)(uintptr_t)addr;
        g_seen[g_nseen++] = (Seen){*at, 0u, f[1], f[2], f[3]};
        g_seq = g_nseen;
    } else if (g_nseen >= MAX_VTX)
        g_over++;
    return *at + 1u;
}

/* ---- the fit check ---- */

/* Solves the 4x4 system in g's first four columns for the fifth (partial pivoting); 0 if singular. */
static int solve4(double g[4][5])
{
    for (int c = 0; c < 4; c++) {
        int best = c;
        for (int r = c + 1; r < 4; r++)
            if (fabs(g[r][c]) > fabs(g[best][c])) best = r;
        if (fabs(g[best][c]) < 1e-12) return 0;
        for (int k = 0; k < 5; k++) {
            double t = g[c][k];
            g[c][k] = g[best][k];
            g[best][k] = t;
        }
        for (int r = 0; r < 4; r++)
            if (r != c) {
                double f = g[r][c] / g[c][c];
                for (int k = c; k < 5; k++) g[r][k] -= f * g[c][k];
            }
    }
    for (int r = 0; r < 4; r++) g[r][4] /= g[r][r];
    return 1;
}

static void homog(const f32 v[3], int wz, double h[3])
{
    double w = wz ? v[2] : (v[2] != 0.0f ? 1.0 / v[2] : 0.0);
    h[0] = v[0] * w;
    h[1] = v[1] * w;
    h[2] = w;
}

/* The worst pixel distance of a part's vertices a tick before from its vertices now through the fitted 3x4 map. */
static double fit_error(const Part *now, const Part *was, int wz, u32 *off)
{
    const f32(*a)[3] = (const f32(*)[3])&g_vtx[now->first];
    const f32(*b)[3] = (const f32(*)[3])&g_vprev[was->first];
    double mean[3] = {0, 0, 0}, scale = 1e-9, g[3][4][5], worst = 0.0;
    u32 n = now->count;
    for (u32 k = 0; k < n; k++) {
        double h[3];
        homog(a[k], wz, h);
        for (int r = 0; r < 3; r++) mean[r] += h[r] / n;
    }
    for (u32 k = 0; k < n; k++) {
        double h[3];
        homog(a[k], wz, h);
        for (int r = 0; r < 3; r++) scale = fmax(scale, fabs(h[r] - mean[r]));
    }
    memset(g, 0, sizeof g);
    for (u32 k = 0; k < n; k++) { /* normal equations, one per output row, on the centred and scaled input */
        double h[3], t[3], u[4];
        homog(a[k], wz, h);
        homog(b[k], wz, t);
        for (int r = 0; r < 3; r++) u[r] = (h[r] - mean[r]) / scale;
        u[3] = 1.0;
        for (int o = 0; o < 3; o++)
            for (int i = 0; i < 4; i++) {
                for (int j = 0; j < 4; j++) g[o][i][j] += u[i] * u[j];
                g[o][i][4] += u[i] * t[o];
            }
    }
    for (int o = 0; o < 3; o++) {
        for (int i = 0; i < 4; i++) g[o][i][i] += 1e-9 * n; /* a flat part: any map that fits its plane */
        if (!solve4(g[o])) return 1e9;
    }
    for (u32 k = 0; k < n; k++) {
        double h[3], u[4], y[3];
        homog(a[k], wz, h);
        for (int r = 0; r < 3; r++) u[r] = (h[r] - mean[r]) / scale;
        u[3] = 1.0;
        for (int o = 0; o < 3; o++) y[o] = g[o][0][4] * u[0] + g[o][1][4] * u[1] + g[o][2][4] * u[2] + g[o][3][4];
        if (y[2] == 0.0) return 1e9;
        double e = fmax(fabs(y[0] / y[2] - b[k][0]), fabs(y[1] / y[2] - b[k][1]));
        worst = fmax(worst, e);
        *off += e > 2.0;
    }
    return worst;
}

/* This scene's vertices sorted by part, and each part paired with the same object part a tick before (its first
 * unpaired draw with the same object, part and stamp), if it has as many vertices. */
static void pair_parts(void)
{
    static u32 start[MAX_PARTS + 1u];
    static u8 used[MAX_PARTS];
    memset(start, 0, (g_nparts + 1u) * sizeof start[0]);
    for (u32 k = 0; k < g_nseen; k++) start[g_seen[k].part + 1u]++;
    for (u32 p = 0; p < g_nparts; p++) { /* counts to firsts */
        g_parts[p].first = start[p];
        g_parts[p].count = start[p + 1u];
        start[p + 1u] += start[p];
    }
    for (u32 k = 0; k < g_nseen; k++) {
        g_seen[k].pos = start[g_seen[k].part]++;
        memcpy(g_vtx[g_seen[k].pos], &g_seen[k].x, sizeof g_vtx[0]);
    }
    memset(used, 0, g_nprev);
    for (u32 p = 0; p < g_nparts; p++) {
        const Part *now = &g_parts[p];
        g_pair[p] = NONE;
        for (u32 q = 0; q < g_nprev; q++) {
            const Part *was = &g_prev[q];
            if (used[q] || was->obj != now->obj || was->part != now->part || was->stamp != now->stamp) continue;
            used[q] = 1;
            if (was->count == now->count) g_pair[p] = q;
            break;
        }
    }
}

static void check(void)
{
    for (u32 p = 0; p < g_nparts; p++) {
        const Part *now = &g_parts[p], *was = g_pair[p] != NONE ? &g_prev[g_pair[p]] : NULL;
        if (!was || now->count < 4u || !memcmp(&g_vtx[now->first], &g_vprev[was->first], now->count * sizeof g_vtx[0]))
            continue;
        g_n.moved++;
        for (int wz = 0; wz < 2; wz++) {
            u32 off = 0;
            double e = fit_error(now, was, wz, &off);
            g_n.fit[wz][0] += e <= 0.5;
            g_n.fit[wz][1] += e <= 2.0;
            if (!wz) g_n.hist[e <= 0.5 ? 0 : e <= 2 ? 1 : e <= 8 ? 2 : e <= 64 ? 3 : 4]++;
            if (!wz && e > 2.0 && g_n.shown < 8u) { /* what a part that doesn't fit looks like */
                f32 lo[3] = {1e30f, 1e30f, 1e30f}, hi[3] = {-1e30f, -1e30f, -1e30f};
                for (u32 k = 0; k < now->count; k++)
                    for (int r = 0; r < 3; r++) {
                        lo[r] = fminf(lo[r], g_vtx[now->first + k][r]);
                        hi[r] = fmaxf(hi[r], g_vtx[now->first + k][r]);
                    }
                g_n.shown++;
                port_log("parts: no fit: obj %p part %p stamp %d, %u vertices, %u over 2 px, worst %.1f px; "
                         "x %.0f-%.0f y %.0f-%.0f 1/w %.4g-%.4g\n", now->obj, now->part, (int)now->stamp,
                         now->count, off, e, lo[0], hi[0], lo[1], hi[1], lo[2], hi[2]);
            }
        }
    }
}

/* The holds (see the top): whether this scene holds, and which parts turned since the two ticks before. */
static void holds(void)
{
    static u8 hit[MAX_PARTS];
    u32 kept = 0, changed = 0;
    memset(hit, 0, g_nprev);
    g_holds = 0;
    for (u32 p = 0; p < g_nparts; p++) {
        const Part *now = &g_parts[p];
        u32 q = g_pair[p], r = q != NONE ? g_pair_was[q] : NONE;
        double d = 0.0, n1 = 0.0, n2 = 0.0;
        g_turned[p] = 0;
        if (q == NONE) changed += now->count;
        else hit[q] = 1, kept += now->count;
        for (u32 k = 0; r != NONE && k < now->count; k++) {
            const f32 *a = g_vtx[now->first + k], *b = g_vprev[g_prev[q].first + k], *c = g_vprev2[g_first2[r] + k];
            for (int i = 0; i < 2; i++) { /* across and down the screen */
                double v1 = b[i] - c[i], v2 = a[i] - b[i];
                d += v1 * v2, n1 += v1 * v1, n2 += v2 * v2;
            }
        }
        g_turned[p] = r != NONE && n1 >= now->count && n2 >= now->count && d < TURN_COS * sqrt(n1 * n2);
        g_holds += g_turned[p];
    }
    for (u32 q = 0; q < g_nprev; q++) changed += hit[q] ? 0u : g_prev[q].count;
    g_hold_frame = changed && changed * HOLD_SHARE >= kept + changed;
    g_n.held += g_hold_frame, g_n.turned += g_holds;
}

/* ta_parse.c, a scene parsed (the game drew all its parts before the render): where each of its vertices was a tick
 * before, for --interp-parts; then this scene's parts are the ones the next scene pairs with. */
void pvr_parts_from(PvrScene *s)
{
    s->has_from = 0;
    if (!g_log && !g_parts_blend) return;
    pair_parts();
    if (g_log) check();
    if (g_parts_blend) holds();
    if (g_parts_blend && s->nvtx && s->seq) {
        if (s->nvtx > s->from_cap) {
            f32(*f)[3] = realloc(s->from, (size_t)s->nvtx * sizeof *f);
            if (!f) return;
            s->from = f;
            s->from_cap = s->nvtx;
        }
        for (u32 i = 0; i < s->nvtx; i++) {
            const PvrVtx *v = &s->vtx[i];
            u32 k = s->seq[i];
            s->from[i][0] = v->x, s->from[i][1] = v->y, s->from[i][2] = v->z;
            if (k && k <= g_nseen && g_pair[g_seen[k - 1u].part] != NONE) {
                const Seen *e = &g_seen[k - 1u];
                memcpy(s->from[i], g_vprev[g_prev[g_pair[e->part]].first + e->pos - g_parts[e->part].first],
                       sizeof s->from[0]);
                if (g_turned[e->part] && s->from[i][2] > 0.0f) s->from[i][2] = -s->from[i][2]; /* held */
            }
        }
        s->has_from = 1;
    }
    /* --interp-truth: kept only after an odd frame (pvr_blend_keep keeps it at the next render, an even frame), so
     * the in-between frame after an odd frame blends the scene two before */
    if (pvr_blend_truth_t(0.0f) != 0.0f && !pvr_blend_truth_skip()) return;
    for (u32 q = 0; q < g_nprev; q++) g_first2[q] = g_prev[q].first;
    memcpy(g_vprev2, g_vprev, g_nvprev * sizeof g_vtx[0]);
    memcpy(g_pair_was, g_pair, g_nparts * sizeof g_pair[0]);
    memcpy(g_prev, g_parts, g_nparts * sizeof g_parts[0]);
    memcpy(g_vprev, g_vtx, g_nseen * sizeof g_vtx[0]);
    g_nprev = g_nparts, g_nvprev = g_nseen;
}

/* pvr_blend_vertices, a scene with has_from: its vertices t of the way from where they were a tick before, in
 * (x w, y w, w) with w = 1/z, so a part moves as it and the camera did, in perspective. Valid until the next call. */
const PvrVtx *pvr_parts_blend(const PvrScene *cur, f32 t, u32 *blended)
{
    static PvrVtx *out;
    static u32 cap;
    u32 moved = 0;
    if (cur->nvtx > cap) {
        PvrVtx *v = realloc(out, (size_t)cur->nvtx * sizeof *v);
        if (!v) return NULL;
        out = v;
        cap = cur->nvtx;
    }
    memcpy(out, cur->vtx, (size_t)cur->nvtx * sizeof *out);
    for (u32 i = 0; i < cur->nvtx; i++) {
        const f32 *f = cur->from[i];
        PvrVtx *o = &out[i];
        f32 a, b;
        if (f[2] < 0.0f) { o->x = f[0], o->y = f[1], o->z = -f[2], moved++; continue; } /* held: where it was */
        if ((f[0] == o->x && f[1] == o->y && f[2] == o->z) || !(f[2] > 0.0f && f[2] < 3.0e38f)) continue;
        a = (1.0f - t) / f[2], b = t / o->z; /* each end's w, weighted */
        o->x = (a * f[0] + b * o->x) / (a + b);
        o->y = (a * f[1] + b * o->y) / (a + b);
        o->z = 1.0f / (a + b);
        moved++;
    }
    if (blended) *blended = moved / 3u;
    return out;
}

/* kamui2_scene.c kmBeginScene: the parts of the scene before are spent. */
void pvr_parts_scene(void)
{
    if (g_nparts > g_n.most) g_n.most = g_nparts;
    if (g_log && g_ptrs && ++g_n.scenes % LOG_EVERY == 0) {
        port_log("parts: %u scenes: %u part draws, %u wrote to a list (%u nested, %u past the table, most %u in a "
                 "scene); %u of %u polygon vertices in a part's range; of %u parts moved between ticks, one affine "
                 "map fits to 0.5 / 2 pixels: %u / %u (w = 1/z), %u / %u (w = z); worst px (w = 1/z) to 0.5, 2, 8, "
                 "64, more: %u %u %u %u %u; held: %u scenes, %u parts turned\n", g_n.scenes, g_n.draws, g_n.wrote,
                 g_n.nested, g_over, g_n.most, g_n.tagged, g_n.vertices, g_n.moved, g_n.fit[0][0], g_n.fit[0][1],
                 g_n.fit[1][0], g_n.fit[1][1], g_n.hist[0], g_n.hist[1], g_n.hist[2], g_n.hist[3], g_n.hist[4],
                 g_n.held, g_n.turned);
        memset(&g_n, 0, sizeof g_n);
        g_over = 0;
    }
    g_nparts = g_nseen = 0;
    g_last_list = PVRX_LISTS;
}
