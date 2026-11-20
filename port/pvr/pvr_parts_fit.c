/* The 3x4 map of a part's vertices from one tick to another, for pvr_parts.c. A rigid part's vertices, as (x w, y w, w)
 * with w = 1/z, are one affine map of its model space in every tick, so between two ticks they are one such map of each
 * other, whatever the game's matrices and screen mapping are. It is fitted here by least squares from vertex pairs:
 * in order (a part that kept its vertex count: the fit check), or from pvr_parts_match (a part the game culled or
 * clipped to another count: its pairs are the vertices whose attribute words, texture coordinates and colours, are
 * unique in the part in both ticks; the map then moves every vertex of it, paired or not). */
#include <math.h>
#include <stdlib.h>
#include <string.h>
#include "pvr_int.h"

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

static u32 at(const u32 *idx, u32 k) { return idx ? idx[k] : k; }

/* a[ia[k]] to b[ib[k]] for k < n (NULL: in order); 0 if the pairs leave the map undecided. */
int pvr_parts_fit(const f32 (*a)[3], const f32 (*b)[3], const u32 *ia, const u32 *ib, u32 n, int wz, PartMap *m)
{
    double g[3][4][5];
    if (n < 4u) return 0;
    memset(m, 0, sizeof *m);
    m->wz = wz;
    m->scale = 1e-9;
    for (u32 k = 0; k < n; k++) {
        double h[3];
        homog(a[at(ia, k)], wz, h);
        for (int r = 0; r < 3; r++) m->mean[r] += h[r] / n;
    }
    for (u32 k = 0; k < n; k++) {
        double h[3];
        homog(a[at(ia, k)], wz, h);
        for (int r = 0; r < 3; r++) m->scale = fmax(m->scale, fabs(h[r] - m->mean[r]));
    }
    memset(g, 0, sizeof g);
    for (u32 k = 0; k < n; k++) { /* normal equations, one per output row, on the centred and scaled input */
        double h[3], t[3], u[4];
        homog(a[at(ia, k)], wz, h);
        homog(b[at(ib, k)], wz, t);
        for (int r = 0; r < 3; r++) u[r] = (h[r] - m->mean[r]) / m->scale;
        u[3] = 1.0;
        for (int o = 0; o < 3; o++)
            for (int i = 0; i < 4; i++) {
                for (int j = 0; j < 4; j++) g[o][i][j] += u[i] * u[j];
                g[o][i][4] += u[i] * t[o];
            }
    }
    for (int o = 0; o < 3; o++) {
        for (int i = 0; i < 4; i++) g[o][i][i] += 1e-9 * n; /* a flat part: any map that fits its plane */
        if (!solve4(g[o])) return 0;
        for (int i = 0; i < 4; i++) m->c[o][i] = g[o][i][4];
    }
    return 1;
}

/* The game's near clamp, 0 until seen: the 1/w a game gives a vertex at or behind its near plane, keeping x and y as
 * projected (far off the screen). Its (x w, y w, w) is no point of its part, so no map or blend can move it right: a
 * floor under the eye went far back and was hidden. Seen as two places more than 4096 pixels off the screen at the
 * largest 1/w of those (not the scene's: 2D is nearer); kept from scene to scene, as a scene with none says nothing. */
f32 pvr_parts_near(const PvrVtx *v, u32 n)
{
    static f32 clamp_z;
    const PvrVtx *first = NULL;
    int two = 0;
    for (u32 i = 0; i < n; i++) {
        if (fabsf(v[i].x) <= 4096.0f && fabsf(v[i].y) <= 4096.0f) continue;
        if (!first || v[i].z > first->z) first = &v[i], two = 0;
        else if (v[i].z == first->z && (v[i].x != first->x || v[i].y != first->y)) two = 1;
    }
    if (two) clamp_z = first->z;
    return clamp_z;
}

/* v through the map, as a screen vertex; 0 (out untouched) if it lands behind the eye. */
int pvr_parts_map(const PartMap *m, const f32 v[3], f32 out[3])
{
    double h[3], u[4], y[3];
    homog(v, m->wz, h);
    for (int r = 0; r < 3; r++) u[r] = (h[r] - m->mean[r]) / m->scale;
    u[3] = 1.0;
    for (int o = 0; o < 3; o++) y[o] = m->c[o][0] * u[0] + m->c[o][1] * u[1] + m->c[o][2] * u[2] + m->c[o][3];
    if (!(y[2] > 0.0)) return 0;
    out[0] = (f32)(y[0] / y[2]);
    out[1] = (f32)(y[1] / y[2]);
    out[2] = (f32)(m->wz ? y[2] : 1.0 / y[2]);
    return 1;
}

/* The pairs ia[k], ib[k] (k < n) with no clamped vertex (pvr_parts_near: no point of its part); how many are left. */
static u32 unclamped(const f32 (*a)[3], const f32 (*b)[3], u32 *ia, u32 *ib, u32 n, f32 clamp)
{
    u32 k = 0;
    for (u32 j = 0; j < n; j++)
        if (a[ia[j]][2] != clamp && b[ib[j]][2] != clamp) ia[k] = ia[j], ib[k++] = ib[j];
    return k;
}

/* Whether at least min of the pairs, the clamped aside, are one map within 2 px for 9 in 10 (one rigid part). */
static int rigid(const f32 (*a)[3], const f32 (*b)[3], u32 *ia, u32 *ib, u32 n, f32 clamp, u32 min, PartMap *m)
{
    u32 off = 0;
    n = unclamped(a, b, ia, ib, n, clamp);
    if (n < min || !pvr_parts_fit(a, b, ia, ib, n, 0, m)) return 0;
    pvr_parts_fit_worst(m, a, b, ia, ib, n, &off);
    return off * 10u <= n;
}

/* The pairs whose b is within lim pixels of their a through the map, kept in place; how many. */
static u32 near_map(const PartMap *m, const f32 (*a)[3], const f32 (*b)[3], u32 *ia, u32 *ib, u32 n, double lim)
{
    u32 k = 0;
    for (u32 j = 0; j < n; j++) {
        f32 y[3];
        if (pvr_parts_map(m, a[ia[j]], y) && fmax(fabs(y[0] - b[ib[j]][0]), fabs(y[1] - b[ib[j]][1])) <= lim)
            ia[k] = ia[j], ib[k++] = ib[j];
    }
    return k;
}

/* One piece of the pairs: their map, fitted on those it holds within 2 pixels (9 in 10 of them), which are 3 in 5 of the
 * pairs at least and min or more; they are moved to the front. How many, 0 when there is no such piece. */
static u32 piece(const f32 (*a)[3], const f32 (*b)[3], u32 *ia, u32 *ib, u32 n, u32 min, PartMap *m)
{
    u32 all = n, off = 0;
    double lim;
    if (n < min || !pvr_parts_fit(a, b, ia, ib, n, 0, m)) return 0;
    lim = fmin(pvr_parts_fit_worst(m, a, b, ia, ib, n, &off), 4096.0);
    if (off * 10u <= n) return n; /* one rigid piece */
    for (int round = 0; round < 16; round++) {
        lim = fmax(2.0, lim * 0.5);
        n = near_map(m, a, b, ia, ib, n, lim);
        if (n * 5u < all * 3u || n < min || !pvr_parts_fit(a, b, ia, ib, n, 0, m)) return 0;
        off = 0;
        pvr_parts_fit_worst(m, a, b, ia, ib, n, &off);
        if (lim <= 2.0) return off * 10u <= n ? n : 0;
    }
    return 0;
}

static int within(const PartMap *m, const f32 *a, const f32 *b, double lim)
{
    f32 y[3];
    return pvr_parts_map(m, a, y) && fmax(fabs(y[0] - b[0]), fabs(y[1] - b[1])) <= lim;
}

/* A part with another vertex count than a tick before: its map m from now to then, fitted on its larger piece (a
 * helicopter's body). The pairs that map leaves over 2 pixels must be few (1 in 20) or pieces themselves (its rotor,
 * up to 3, each one map within 2 pixels): those pairs come back in oa and ob (*nown of them), each to go from its own
 * vertex a tick before. Left-over pairs that are no piece (a clipped room's stray matches): 0, the part is drawn as now. */
int pvr_parts_map_part(const f32 (*a)[3], const f32 (*b)[3], const u32 *ka, u32 na, const u32 *kb, u32 nb, f32 clamp,
                       u32 min, u32 *ia, u32 *ib, u32 cap, PartMap *m, u32 *oa, u32 *ob, u32 *nown)
{
    u32 all = unclamped(a, b, ia, ib, pvr_parts_match(ka, na, kb, nb, ia, ib, cap), clamp), k = 0, done = 0;
    *nown = 0;
    if ((k = piece(a, b, ia, ib, all, min, m)) == 0) return 0;
    if (k == all) return 1; /* one rigid part, as before the pieces */
    k = 0;
    all = unclamped(a, b, ia, ib, pvr_parts_match(ka, na, kb, nb, ia, ib, cap), clamp);
    for (u32 j = 0; j < all; j++)
        if (!within(m, a[ia[j]], b[ib[j]], 2.0)) oa[k] = ia[j], ob[k++] = ib[j];
    for (int pieces = 0; (k - done) * 20u > all; pieces++) {
        PartMap m2;
        u32 rest = k - done;
        if (pieces == 3) return 0;
        memcpy(ia, oa + done, rest * sizeof ia[0]);
        memcpy(ib, ob + done, rest * sizeof ib[0]);
        if (!piece(a, b, ia, ib, rest, min, &m2)) return 0;
        for (u32 j = done; j < k; j++)
            if (within(&m2, a[oa[j]], b[ob[j]], 2.0)) {
                u32 ta = oa[j], tb = ob[j];
                oa[j] = oa[done], ob[j] = ob[done];
                oa[done] = ta, ob[done++] = tb;
            }
        if (k - done == rest) return 0;
    }
    *nown = done;
    return 1;
}

/* Whether two of a's n vertices in one place (a corner its polygons share, the same bytes) were in two places in b.
 * One pass through a hash of a's places (each vertex against the first in its place): this runs for every part that
 * kept its count in every scene, on the game's thread, so not a sort (14,000 vertices a fight scene). */
static u32 *g_tab, g_tab_cap;

static int split(const f32 (*a)[3], const f32 (*b)[3], u32 n)
{
    u32 size = 16u;
    while (size < 2u * n) size <<= 1;
    if (size > g_tab_cap) {
        u32 *t = realloc(g_tab, (size_t)size * sizeof *t);
        if (!t) return 1; /* no table: taken as split, so the fit decides */
        g_tab = t, g_tab_cap = size;
    }
    memset(g_tab, 0, (size_t)size * sizeof *g_tab);
    for (u32 j = 0; j < n; j++) {
        u32 w[3], h;
        memcpy(w, a[j], sizeof w);
        h = w[0] * 0x9e3779b1u ^ w[1] * 0x85ebca6bu ^ w[2] * 0xc2b2ae35u;
        for (h = (h ^ h >> 15) & (size - 1u);; h = (h + 1u) & (size - 1u)) {
            u32 k = g_tab[h];
            if (!k) {
                g_tab[h] = j + 1u;
                break;
            }
            if (!memcmp(a[k - 1u], a[j], sizeof a[0])) {
                if (memcmp(b[k - 1u], b[j], sizeof b[0])) return 1;
                break;
            }
        }
    }
    return 0;
}

/* A part that kept its count: whether its n vertices are in the order they were a tick before. Clipped otherwise to the
 * same count they are not (the floor under the eye, paired in order, was torn apart: a corner drawn twice came from two
 * places). A rigid part in order is one map; one that is not (clipped, its cut corners new; weighted joints) is taken as
 * in order when every corner drawn more than once, now or a tick before, was so in the same places both ticks. */
int pvr_parts_in_order(const f32 (*a)[3], const f32 (*b)[3], u32 n, f32 clamp, u32 *ia, u32 *ib)
{
    PartMap m;
    /* in order = rigid or no corner split; the split test first, as it is one pass and the fit is not: the fit runs
     * only for a part with a split corner (the same answer either way) */
    if (n < 4u || (!split(a, b, n) && !split(b, a, n))) return 1;
    for (u32 j = 0; j < n; j++) ia[j] = ib[j] = j;
    return rigid(a, b, ia, ib, n, clamp, 4u, &m);
}

/* The worst pixel distance of the pairs' b from their a through the map; *off counts those over 2 pixels. */
double pvr_parts_fit_worst(const PartMap *m, const f32 (*a)[3], const f32 (*b)[3], const u32 *ia, const u32 *ib, u32 n,
                           u32 *off)
{
    double worst = 0.0;
    for (u32 k = 0; k < n; k++) {
        f32 y[3];
        if (!pvr_parts_map(m, a[at(ia, k)], y)) return 1e9;
        double e = fmax(fabs(y[0] - b[at(ib, k)][0]), fabs(y[1] - b[at(ib, k)][1]));
        worst = fmax(worst, e);
        *off += e > 2.0;
    }
    return worst;
}

static int by_key(const void *x, const void *y)
{
    u64 a = *(const u64 *)x, b = *(const u64 *)y;
    return a < b ? -1 : a > b;
}

/* Sorted (key, index) of n keys, in buf (grown as needed). */
static u64 *sorted(const u32 *key, u32 n, u64 **buf, u32 *cap)
{
    if (n > *cap) {
        u64 *p = realloc(*buf, (size_t)n * sizeof *p);
        if (!p) return NULL;
        *buf = p, *cap = n;
    }
    for (u32 k = 0; k < n; k++) (*buf)[k] = (u64)key[k] << 32 | k;
    qsort(*buf, n, sizeof **buf, by_key);
    return *buf;
}

/* A part with another vertex count than a tick before (a its vertices now, b then): the pairs of its vertices whose
 * key is at one place in both ticks (a corner a strip draws twice is one place), none clamped: each such ia[k] with the
 * first of its key in b, ib[k]; how many (at most cap). And its map m (pvr_parts_map_part) for the rest, no further than
 * those pairs moved: m->lim pixels, under 0 with no map (m then maps nothing: the rest as now). The map alone, fitted on
 * a flat piece's unique keys, threw the part's other corners up to 190 pixels when they had moved under 1 (2026-11-13). */
u32 pvr_parts_map_places(const f32 (*a)[3], const f32 (*b)[3], const u32 *ka, u32 na, const u32 *kb, u32 nb, f32 clamp,
                         u32 min, u32 *ia, u32 *ib, u32 cap, PartMap *m, u32 *oa, u32 *ob)
{
    static u64 *sa, *sb;
    static u32 ca, cb;
    u32 i = 0, j = 0, n = 0, own = 0;
    f32 move = 0.0f;
    int ok = pvr_parts_map_part(a, b, ka, na, kb, nb, clamp, min, ia, ib, cap, m, oa, ob, &own);
    const u64 *x = sorted(ka, na, &sa, &ca), *y = sorted(kb, nb, &sb, &cb);
    if (!ok) memset(m->c, 0, sizeof m->c), m->scale = 1.0; /* maps nothing (pvr_parts_map: w 0) */
    m->lim = -1.0f;
    if (!x || !y) return 0;
    while (i < na && j < nb) {
        u32 kx = (u32)(x[i] >> 32), ky = (u32)(y[j] >> 32), ea = i + 1u, eb = j + 1u;
        int one;
        if (kx != ky) {
            if (kx < ky) i++;
            else j++;
            continue;
        }
        one = a[(u32)x[i]][2] != clamp && b[(u32)y[j]][2] != clamp;
        while (ea < na && (u32)(x[ea] >> 32) == kx) one &= !memcmp(a[(u32)x[ea]], a[(u32)x[i]], sizeof a[0]), ea++;
        while (eb < nb && (u32)(y[eb] >> 32) == ky) one &= !memcmp(b[(u32)y[eb]], b[(u32)y[j]], sizeof b[0]), eb++;
        if (one) move = fmaxf(move, fmaxf(fabsf(a[(u32)x[i]][0] - b[(u32)y[j]][0]), fabsf(a[(u32)x[i]][1] - b[(u32)y[j]][1])));
        for (u32 k = i; one && k < ea && n < cap; k++) ia[n] = (u32)x[k], ib[n++] = (u32)y[j];
        i = ea, j = eb;
    }
    if (ok) m->lim = move + 4.0f;
    return n;
}

/* Pairs of the vertices whose key is unique in both ticks' part: ia[k] in a, ib[k] in b; how many (at most cap). */
u32 pvr_parts_match(const u32 *ka, u32 na, const u32 *kb, u32 nb, u32 *ia, u32 *ib, u32 cap)
{
    static u64 *sa, *sb;
    static u32 ca, cb;
    u32 i = 0, j = 0, n = 0;
    const u64 *x = sorted(ka, na, &sa, &ca), *y = sorted(kb, nb, &sb, &cb);
    if (!x || !y) return 0;
    while (i < na && j < nb && n < cap) {
        u32 a = (u32)(x[i] >> 32), b = (u32)(y[j] >> 32), ea = i + 1u, eb = j + 1u;
        if (a != b) {
            if (a < b) i++;
            else j++;
            continue;
        }
        while (ea < na && (u32)(x[ea] >> 32) == a) ea++;
        while (eb < nb && (u32)(y[eb] >> 32) == b) eb++;
        if (ea - i == 1u && eb - j == 1u) ia[n] = (u32)x[i], ib[n] = (u32)y[j], n++;
        i = ea, j = eb;
    }
    return n;
}
