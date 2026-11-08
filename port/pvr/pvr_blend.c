/* Scene blending: the in-between frames of a game that registers no draw function (runtime/frame_extra.c,
 * --interp-scene). The renderer keeps the scene it parsed before this one; an in-between frame draws this scene's
 * batches with each vertex moved part of the way back to where the same vertex was a tick ago.
 *
 * A batch of this scene is matched with the first batch of the old one, at or after the last match and within
 * MATCH_AHEAD, that has the same list, render state, scissor and vertex count (a polygon that appeared or went changes
 * a batch's count, so that batch is drawn as it is), unless an old batch there had every vertex where this one has:
 * then that one, or none if its state differs (a menu button that only changed its picture stays put, and does not
 * slide from the next button with that picture). A triangle is paired only with one that could be the same
 * triangle a tick ago: it moved no further than MOVE_MAX pixels (the 640x480 screen), its depth, texture coordinates and
 * colour stayed near, and its sides kept their length. A camera cut, a different object with the same state, a wrapped
 * texture all draw as they are. Triangles pair by index, unless too many of a batch fail that (a game that sorts a mesh
 * gives the same triangles in another order each tick, and pairing by index shatters it): those batches pair each
 * triangle with the nearest old one that passes, texture coordinates counting most: they stay with a mesh's vertices.
 * Positions blend per vertex, not per triangle: vertices at one place in the current scene take one movement, the
 * average of their paired triangles', so a mesh or a closed shadow volume cannot crack, and a triangle with no pair
 * moves with the neighbours it shares a vertex with. The pairs are worked out once a scene. Colours of a paired triangle
 * blend too, and its texture coordinates unless they stepped far enough to be another picture. The game's own tick is
 * untouched, and no game code runs: nothing is undone.
 * Positions and depth blend in screen space, or (--interp-persp) in the view's x/z, y/z and 1/z. */
#include <xmmintrin.h>
#include <stdlib.h>
#include <string.h>
#include "pvr_int.h"
#include "frame_cost.h"

#define MATCH_AHEAD 24u
#define MOVE_MAX 80.0f /* pixels of a 640x480 screen a vertex may move in a tick and still blend */
#define UV_MAX 0.5f    /* texture units */
#define COLOUR_MAX 64u /* of 255, per channel */
#define SIDE_SLACK 4.0f   /* pixels a triangle's side may change in a tick, or this share of it */
#define SIDE_SHARE 0.35f
#define INDEX_ENOUGH 98u  /* per cent of a batch's triangles that must pair by index, or it pairs by nearness */
#define NEAR_X 32.0f      /* how far across the screen a triangle's middle is looked for by nearness */
#define UV_WEIGHT 1024.0f /* by nearness, pixels that a texture unit counts as */
#define UV_SLIDE (1.0f / 64.0f) /* a bigger step in a tick is another picture (a digit, a flipbook), not a texture scrolling */
#define NO_PAIR 0xffffffffu

typedef struct {
    f32 x, y; /* its middle across, its corners' heights added */
    u32 first;
    int used;
} OldTri;

/* One place in the current scene: the movements of the paired vertices there, added up. A slot is in use when its
 * scene is the current one (Pairs.scene_no), so the table is not cleared for each scene. */
typedef struct {
    u32 x, y, z, count;
    f32 dx, dy, dz;
    u32 scene;
} Place;

/* The pairs of a scene with the one before it. */
typedef struct {
    u32 *pair, pair_cap; /* per current vertex, for a triangle's first: the old triangle's first, or NO_PAIR */
    u32 *at, at_cap;     /* per current vertex, its place */
    Place *place;        /* a table of places, open addressing */
    u32 place_cap;       /* a power of two */
    u32 scene_no;        /* the places' current scene, from 1 */
} Pairs;

static PvrScene g_old;           /* the scene parsed before the current one (its arrays are this file's own) */
static const PvrScene *g_from = &g_old; /* the old scene: g_old, or --interp-early's (pvr_blend_early.c) */
static int g_have;               /* g_from holds a scene */
static PvrVtx *g_out;            /* the blended vertices */
static u32 g_out_cap;
static Pairs g_sets[2], *g_p = g_sets; /* the pairs in use; --interp-early works out the next scene's in the other */
static int g_paired;             /* g_p holds this scene's pairs */
static int g_early, g_next_ok;   /* --interp-early: only pvr_blend_pair_next pairs; it found the next scene's pairs */
static OldTri *g_tri;            /* a batch's old triangles by their middles, for pairing by nearness */
static u32 g_tri_cap;
static u32 g_scenes;             /* scenes begun, for --interp-gpu (pvr_blend_gpu.c) */

/* The current scene is about to change. */
void pvr_blend_new_scene(void) { g_paired = 0, g_scenes++; }
u32 pvr_blend_scene(void) { return g_scenes; }

/* Keeps a copy of s as the old scene. */
void pvr_blend_keep(const PvrScene *s)
{
    pvr_blend_new_scene();
    if (pvr_blend_truth_skip()) return; /* --interp-truth: the scene two before stays */
    if (s->nvtx > g_old.vcap) {
        PvrVtx *v = realloc(g_old.vtx, (size_t)s->nvtx * sizeof *v);
        if (!v) { g_have = 0; return; }
        g_old.vtx = v;
        g_old.vcap = s->nvtx;
    }
    if (s->nbatch > g_old.bcap) {
        PvrBatch *b = realloc(g_old.batch, (size_t)s->nbatch * sizeof *b);
        if (!b) { g_have = 0; return; }
        g_old.batch = b;
        g_old.bcap = s->nbatch;
    }
    if (s->nvtx) memcpy(g_old.vtx, s->vtx, (size_t)s->nvtx * sizeof *s->vtx);
    if (s->nbatch) memcpy(g_old.batch, s->batch, (size_t)s->nbatch * sizeof *s->batch);
    g_old.nvtx = s->nvtx;
    g_old.nbatch = s->nbatch;
    memcpy(g_old.range, s->range, sizeof g_old.range);
    g_old.passes = s->passes;
    g_from = &g_old;
    g_have = s->nvtx != 0;
}

int pvr_blend_have(void) { return g_have && !pvr_parts_hold_frame(); }

static int same_state(const PvrBatch *a, const PvrBatch *b)
{
    return a->list == b->list && a->isp == b->isp && a->tsp == b->tsp && a->tcw == b->tcw && a->flags == b->flags &&
           a->count == b->count && !memcmp(a->clip, b->clip, sizeof a->clip);
}

/* Is every one of n vertices c where o's was on the screen? */
static int stayed(const PvrVtx *o, const PvrVtx *c, u32 n)
{
    u32 v = 0;
    while (v < n && o[v].x == c[v].x && o[v].y == c[v].y) v++;
    return n && v == n;
}

static f32 mix(f32 a, f32 b, f32 t) { return a + (b - a) * t; }
static f32 dist(f32 a, f32 b) { return a < b ? b - a : a - b; }

/* Per channel (a byte each) of two packed colours, a + (b - a) * t. */
static u32 mix_argb(u32 a, u32 b, f32 t)
{
    u32 r = 0;
    for (u32 shift = 0; shift < 32u; shift += 8u) {
        f32 ca = (f32)(a >> shift & 255u), cb = (f32)(b >> shift & 255u);
        r |= (u32)(mix(ca, cb, t) + 0.5f) << shift;
    }
    return r;
}

/* The instruction, not sqrtf: port_libm defines that for the game's code. */
static f32 side(const PvrVtx *a, const PvrVtx *b)
{
    f32 dx = a->x - b->x, dy = a->y - b->y;
    return _mm_cvtss_f32(_mm_sqrt_ss(_mm_set_ss(dx * dx + dy * dy)));
}

static int colour_near(u32 a, u32 b)
{
    for (u32 shift = 0; shift < 32u; shift += 8u) {
        u32 ca = a >> shift & 255u, cb = b >> shift & 255u;
        if ((ca > cb ? ca - cb : cb - ca) > COLOUR_MAX) return 0;
    }
    return 1;
}

/* Could c be the triangle o, a tick later? */
static int same_triangle(const PvrVtx *o, const PvrVtx *c)
{
    for (u32 i = 0; i < 3u; i++) {
        f32 zmax = c[i].z > o[i].z ? c[i].z : o[i].z, so = side(&o[i], &o[(i + 1u) % 3u]), sc = side(&c[i], &c[(i + 1u) % 3u]);
        f32 longer = so > sc ? so : sc, allowed = longer * SIDE_SHARE > SIDE_SLACK ? longer * SIDE_SHARE : SIDE_SLACK;
        if (dist(o[i].x, c[i].x) > MOVE_MAX || dist(o[i].y, c[i].y) > MOVE_MAX || dist(o[i].z, c[i].z) > zmax * 0.5f ||
            dist(o[i].u, c[i].u) > UV_MAX || dist(o[i].v, c[i].v) > UV_MAX || dist(so, sc) > allowed ||
            !colour_near(o[i].base, c[i].base))
            return 0;
    }
    return 1;
}

static int by_x(const void *a, const void *b)
{
    f32 x = ((const OldTri *)a)->x, y = ((const OldTri *)b)->x;
    return x < y ? -1 : x > y;
}

/* Each triangle of c gets the nearest unused triangle of o that could be it. */
static void pair_by_nearness(Pairs *ps, const PvrScene *old, const PvrScene *cur, const PvrBatch *c, const PvrBatch *o)
{
    u32 n = c->count / 3u;
    if (n > g_tri_cap) {
        OldTri *t = realloc(g_tri, (size_t)n * sizeof *t);
        if (!t) return; /* the pairs by index stand */
        g_tri = t;
        g_tri_cap = n;
    }
    for (u32 k = 0; k < n; k++) {
        const PvrVtx *v = &old->vtx[o->first + k * 3u];
        g_tri[k].x = (v[0].x + v[1].x + v[2].x) / 3.0f;
        g_tri[k].y = v[0].y + v[1].y + v[2].y;
        g_tri[k].first = o->first + k * 3u;
        g_tri[k].used = 0;
    }
    qsort(g_tri, n, sizeof *g_tri, by_x);
    for (u32 k = 0; k < n; k++) {
        const PvrVtx *v = &cur->vtx[c->first + k * 3u];
        f32 x = (v[0].x + v[1].x + v[2].x) / 3.0f, y = v[0].y + v[1].y + v[2].y, best = 0.0f;
        u32 lo = 0, hi = n, found = n;
        while (lo < hi) {
            u32 mid = lo + (hi - lo) / 2u;
            if (g_tri[mid].x < x - NEAR_X) lo = mid + 1u;
            else hi = mid;
        }
        for (u32 i = lo; i < n && g_tri[i].x <= x + NEAR_X; i++) {
            const PvrVtx *w;
            f32 away;
            if (g_tri[i].used) continue;
            away = dist(g_tri[i].x, x) + dist(g_tri[i].y, y) / 3.0f;
            if (found != n && !(away < best)) continue; /* what is added below is not negative: it cannot come nearer */
            w = &old->vtx[g_tri[i].first];
            for (u32 c2 = 0; c2 < 3u; c2++) away += (dist(w[c2].u, v[c2].u) + dist(w[c2].v, v[c2].v)) * UV_WEIGHT;
            if ((found == n || away < best) && same_triangle(w, v)) {
                found = i;
                best = away;
            }
        }
        if (found != n) g_tri[found].used = 1;
        ps->pair[c->first + k * 3u] = found != n ? g_tri[found].first : NO_PAIR;
    }
}

/* The place of a vertex, added if new. A place's sums do not depend on its slot, only on the order of the vertices. */
static u32 place_of(Pairs *ps, const PvrVtx *v)
{
    u32 x, y, z, h;
    memcpy(&x, &v->x, 4), memcpy(&y, &v->y, 4), memcpy(&z, &v->z, 4);
    h = x * 73856093u ^ y * 19349663u ^ z * 83492791u;
    h ^= h >> 16; /* the low bits of a float's pattern are mostly zero: mix the high ones down (murmur3's finish) */
    h *= 0x85ebca6bu;
    h ^= h >> 13;
    h *= 0xc2b2ae35u;
    h ^= h >> 16;
    h &= ps->place_cap - 1u;
    while (ps->place[h].scene == ps->scene_no && (ps->place[h].x != x || ps->place[h].y != y || ps->place[h].z != z))
        h = (h + 1u) & (ps->place_cap - 1u);
    if (ps->place[h].scene != ps->scene_no) {
        ps->place[h].x = x;
        ps->place[h].y = y;
        ps->place[h].z = z;
        ps->place[h].count = 0;
        ps->place[h].dx = ps->place[h].dy = ps->place[h].dz = 0.0f;
        ps->place[h].scene = ps->scene_no;
    }
    return h;
}

/* Where a vertex is, in what its movement blends in: the screen, or (--interp-persp, pvr_blend_opt.c) x/z, y/z and 1/z,
 * which move in a straight line as the point does in the view. 0 if z is not a 1/w that can be divided by. */
static int where(const PvrVtx *v, f32 p[3])
{
    if (!g_blend_persp) return p[0] = v->x, p[1] = v->y, p[2] = v->z, 1;
    if (!(v->z > 1e-6f)) return 0;
    return p[0] = v->x / v->z, p[1] = v->y / v->z, p[2] = 1.0f / v->z, 1;
}

/* The movement of every vertex, from this scene's pairs. */
static void find_movements(Pairs *ps, const PvrScene *old, const PvrScene *cur)
{
    if (++ps->scene_no == 0u) { /* wrapped: every slot free again */
        for (u32 i = 0; i < ps->place_cap; i++) ps->place[i].scene = 0u;
        ps->scene_no = 1u;
    }
    for (u32 b = 0; b < cur->nbatch; b++)
        for (u32 v = cur->batch[b].first, end = v + cur->batch[b].count; v < end; v++)
            ps->at[v] = place_of(ps, &cur->vtx[v]);
    for (u32 b = 0; b < cur->nbatch; b++)
        for (u32 v = cur->batch[b].first, end = v + cur->batch[b].count; v + 2u < end; v += 3u) {
            if (ps->pair[v] == NO_PAIR) continue;
            for (u32 i = 0; i < 3u; i++) {
                Place *p = &ps->place[ps->at[v + i]];
                f32 o[3], c[3];
                if (!where(&old->vtx[ps->pair[v] + i], o) || !where(&cur->vtx[v + i], c)) continue;
                p->dx += o[0] - c[0];
                p->dy += o[1] - c[1];
                p->dz += o[2] - c[2];
                p->count++;
            }
        }
}

/* Works out the pairs of cur with old into ps; 0 if there is no memory for them. */
static int pair_scene(Pairs *ps, const PvrScene *old, const PvrScene *cur)
{
    if (cur->nvtx > ps->pair_cap) {
        u32 *p = realloc(ps->pair, (size_t)cur->nvtx * sizeof *p);
        if (!p) return 0;
        ps->pair = p;
        ps->pair_cap = cur->nvtx;
    }
    if (cur->nvtx > ps->at_cap) {
        u32 *p = realloc(ps->at, (size_t)cur->nvtx * sizeof *p);
        if (!p) return 0;
        ps->at = p;
        ps->at_cap = cur->nvtx;
    }
    if (cur->nvtx * 2u > ps->place_cap) {
        u32 cap = ps->place_cap ? ps->place_cap : 4096u;
        Place *p;
        while (cap < cur->nvtx * 2u) cap *= 2u;
        p = realloc(ps->place, (size_t)cap * sizeof *p);
        if (!p) return 0;
        for (u32 i = 0; i < cap; i++) p[i].scene = 0u; /* free: scene_no is never 0 when used */
        ps->place = p;
        ps->place_cap = cap;
    }
    memset(ps->pair, 0xff, (size_t)cur->nvtx * sizeof *ps->pair);
    for (u32 p = 0; p < cur->passes; p++)
        for (u32 l = 0; l < PVRX_LISTS; l++) {
            u32 j = old->range[p][l][0], jend = old->range[p][l][1];
            for (u32 i = cur->range[p][l][0]; i < cur->range[p][l][1]; i++) {
                const PvrBatch *c = &cur->batch[i];
                u32 end = jend - j > MATCH_AHEAD ? j + MATCH_AHEAD : jend, k = end, s = end, good = 0;
                for (u32 m = j; m < end && (s == end || !same_state(&old->batch[s], c)); m++) {
                    const PvrBatch *o = &old->batch[m];
                    if (k == end && same_state(o, c)) k = m;
                    if (o->count == c->count && stayed(&old->vtx[o->first], &cur->vtx[c->first], c->count)) s = m;
                }
                if (s != end) k = same_state(&old->batch[s], c) ? s : end; /* it stayed: that one, or none */
                if (k == end) continue;
                j = k + 1u;
                for (u32 v = 0; v + 2u < c->count; v += 3u)
                    if (same_triangle(&old->vtx[old->batch[k].first + v], &cur->vtx[c->first + v])) {
                        ps->pair[c->first + v] = old->batch[k].first + v;
                        good++;
                    }
                if (good * 100u < c->count / 3u * INDEX_ENOUGH) pair_by_nearness(ps, old, cur, c, &old->batch[k]);
            }
        }
    find_movements(ps, old, cur);
    return 1;
}

/* --interp-early (pvr_blend_early.c), the game's thread outside gfx_lock: the pairs of next with cur, into the set not
 * in use (the drawing reads only that one, cur and the old scene) */
void pvr_blend_pair_next(const PvrScene *cur, const PvrScene *next)
{
    g_next_ok = pair_scene(&g_sets[g_p == g_sets], cur, next);
}

/* Holding gfx_lock: the scene that was current is old; the pairs pvr_blend_pair_next found are used. */
void pvr_blend_take(const PvrScene *old)
{
    pvr_blend_new_scene();
    g_p = &g_sets[g_p == g_sets];
    g_from = old;
    g_early = 1;
    g_paired = g_next_ok;
    g_have = old->nvtx != 0 && g_next_ok;
}

/* The scene's vertices with the old scene's blended in, t of the way from the old to the current (0..1); NULL if
 * there is nothing to blend with. Valid until the next call. */
const PvrVtx *pvr_blend_vertices(const PvrScene *cur, f32 t, u32 *blended)
{
    u32 done = 0;
    u64 t0, t1;
    if (cur->has_from) return pvr_parts_blend(cur, pvr_blend_truth_t(t), blended); /* --interp-parts */
    if (!g_have || !cur->nvtx || cur->passes != g_from->passes) return NULL;
    if (cur->nvtx > g_out_cap) {
        PvrVtx *v = realloc(g_out, (size_t)cur->nvtx * sizeof *v);
        if (!v) return NULL;
        g_out = v;
        g_out_cap = cur->nvtx;
    }
    t = pvr_blend_truth_t(t);
    t0 = g_xcost ? xcost_now() : 0u; /* --present-cost */
    if (!g_paired && (g_early || !(g_paired = pair_scene(g_p, g_from, cur)))) return NULL; /* early: taken */
    t1 = t0 ? xcost_now() : 0u;
    memcpy(g_out, cur->vtx, (size_t)cur->nvtx * sizeof *cur->vtx);
    for (u32 b = 0; b < cur->nbatch; b++)
        for (u32 v = cur->batch[b].first, end = v + cur->batch[b].count; v < end; v++) {
            const Place *p = &g_p->place[g_p->at[v]];
            f32 c[3], back;
            if (!p->count || !where(&cur->vtx[v], c)) continue;
            back = (1.0f - t) / (f32)p->count;
            c[0] += p->dx * back, c[1] += p->dy * back, c[2] += p->dz * back;
            if (!g_blend_persp) g_out[v].x = c[0], g_out[v].y = c[1], g_out[v].z = c[2];
            else if (c[2] > 1e-6f) g_out[v].x = c[0] / c[2], g_out[v].y = c[1] / c[2], g_out[v].z = 1.0f / c[2];
        }
    for (u32 b = 0; b < cur->nbatch; b++)
        for (u32 v = cur->batch[b].first, end = v + cur->batch[b].count; v + 2u < end; v += 3u) {
            int slides = 1;
            if (g_p->pair[v] == NO_PAIR) continue;
            for (u32 i = 0; i < 3u; i++) {
                const PvrVtx *o = &g_from->vtx[g_p->pair[v] + i], *c = &cur->vtx[v + i];
                if (dist(o->u, c->u) > UV_SLIDE || dist(o->v, c->v) > UV_SLIDE) slides = 0;
            }
            for (u32 i = 0; i < 3u; i++) {
                const PvrVtx *o = &g_from->vtx[g_p->pair[v] + i], *c = &cur->vtx[v + i];
                if (slides) {
                    g_out[v + i].u = mix(o->u, c->u, t);
                    g_out[v + i].v = mix(o->v, c->v, t);
                }
                g_out[v + i].base = mix_argb(o->base, c->base, t);
                g_out[v + i].offset = mix_argb(o->offset, c->offset, t);
            }
            done++;
        }
    if (t0) {
        xcost_add(XC_PAIR, t1 - t0);
        xcost_add(XC_BLEND, xcost_now() - t1);
    }
    if (blended) *blended = done;
    return g_out;
}
