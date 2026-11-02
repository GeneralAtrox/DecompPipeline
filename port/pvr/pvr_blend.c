/* Scene blending: the in-between frames of a game that registers no draw function (runtime/frame_extra.c,
 * --interp-scene). The renderer keeps the scene it parsed before this one; an in-between frame draws this scene's
 * batches with each vertex moved part of the way back to where the same vertex was a tick ago.
 *
 * A batch of this scene is matched with the first batch of the old one, at or after the last match and within
 * MATCH_AHEAD, that has the same list, render state, scissor and vertex count (a polygon that appeared or went changes
 * a batch's count, so that batch is drawn as it is). A matched batch's vertices pair by index. Each triangle is
 * blended only if it moved no further than MOVE_MAX pixels (the 640x480 screen) and its depth and texture
 * coordinates stayed near: a camera cut, a different object with the same state, a wrapped texture all draw as they
 * are. Colours blend per channel. The game's own tick is untouched, and no game code runs: nothing is undone.
 *
 * Positions and depth blend linearly in screen space: right for the small steps of one tick, a little off for a
 * polygon turning fast in perspective. */
#include <stdlib.h>
#include <string.h>
#include "pvr_int.h"

#define MATCH_AHEAD 24u
#define MOVE_MAX 80.0f /* pixels of a 640x480 screen a vertex may move in a tick and still blend */
#define UV_MAX 0.5f    /* texture units */

static PvrScene g_old;           /* the scene parsed before the current one (its arrays are this file's own) */
static int g_have;               /* g_old holds a scene */
static PvrVtx *g_out;            /* the blended vertices */
static u32 g_out_cap;

/* Keeps a copy of s as the old scene. */
void pvr_blend_keep(const PvrScene *s)
{
    if (s->nvtx > g_old.vcap) {
        PvrVtx *v = realloc(g_old.vtx, (size_t)s->nvtx * sizeof *v);
        if (!v) {
            g_have = 0;
            return;
        }
        g_old.vtx = v;
        g_old.vcap = s->nvtx;
    }
    if (s->nbatch > g_old.bcap) {
        PvrBatch *b = realloc(g_old.batch, (size_t)s->nbatch * sizeof *b);
        if (!b) {
            g_have = 0;
            return;
        }
        g_old.batch = b;
        g_old.bcap = s->nbatch;
    }
    if (s->nvtx) memcpy(g_old.vtx, s->vtx, (size_t)s->nvtx * sizeof *s->vtx);
    if (s->nbatch) memcpy(g_old.batch, s->batch, (size_t)s->nbatch * sizeof *s->batch);
    g_old.nvtx = s->nvtx;
    g_old.nbatch = s->nbatch;
    memcpy(g_old.range, s->range, sizeof g_old.range);
    g_old.passes = s->passes;
    g_have = s->nvtx != 0;
}

int pvr_blend_have(void) { return g_have; }

static int same_state(const PvrBatch *a, const PvrBatch *b)
{
    return a->list == b->list && a->isp == b->isp && a->tsp == b->tsp && a->tcw == b->tcw && a->flags == b->flags &&
           a->count == b->count && !memcmp(a->clip, b->clip, sizeof a->clip);
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

/* Triangle k of a batch: o (the old one) blended toward c (the current) by t, into out; the current one if it moved
 * too far. */
static int blend_triangle(const PvrVtx *o, const PvrVtx *c, PvrVtx *out, f32 t)
{
    for (u32 i = 0; i < 3u; i++) {
        f32 zmax = c[i].z > o[i].z ? c[i].z : o[i].z;
        if (dist(o[i].x, c[i].x) > MOVE_MAX || dist(o[i].y, c[i].y) > MOVE_MAX || dist(o[i].z, c[i].z) > zmax * 0.5f ||
            dist(o[i].u, c[i].u) > UV_MAX || dist(o[i].v, c[i].v) > UV_MAX) {
            memcpy(out, c, 3u * sizeof *c);
            return 0;
        }
    }
    for (u32 i = 0; i < 3u; i++) {
        out[i].x = mix(o[i].x, c[i].x, t);
        out[i].y = mix(o[i].y, c[i].y, t);
        out[i].z = mix(o[i].z, c[i].z, t);
        out[i].u = mix(o[i].u, c[i].u, t);
        out[i].v = mix(o[i].v, c[i].v, t);
        out[i].base = mix_argb(o[i].base, c[i].base, t);
        out[i].offset = mix_argb(o[i].offset, c[i].offset, t);
        out[i].batch = c[i].batch;
    }
    return 1;
}

/* The scene's vertices with the old scene's blended in, t of the way from the old to the current (0..1); NULL if
 * there is nothing to blend with or no triangle matched. Valid until the next call. */
const PvrVtx *pvr_blend_vertices(const PvrScene *cur, f32 t, u32 *blended)
{
    u32 done = 0;
    if (!g_have || !cur->nvtx || cur->passes != g_old.passes) return NULL;
    if (cur->nvtx > g_out_cap) {
        PvrVtx *v = realloc(g_out, (size_t)cur->nvtx * sizeof *v);
        if (!v) return NULL;
        g_out = v;
        g_out_cap = cur->nvtx;
    }
    memcpy(g_out, cur->vtx, (size_t)cur->nvtx * sizeof *cur->vtx);
    for (u32 p = 0; p < cur->passes; p++)
        for (u32 l = 0; l < PVRX_LISTS; l++) {
            u32 j = g_old.range[p][l][0], jend = g_old.range[p][l][1];
            for (u32 i = cur->range[p][l][0]; i < cur->range[p][l][1]; i++) {
                const PvrBatch *c = &cur->batch[i];
                u32 end = jend - j > MATCH_AHEAD ? j + MATCH_AHEAD : jend, k;
                for (k = j; k < end; k++)
                    if (same_state(&g_old.batch[k], c)) break;
                if (k == end) continue;
                j = k + 1u;
                for (u32 v = 0; v + 2u < c->count; v += 3u)
                    done += (u32)blend_triangle(&g_old.vtx[g_old.batch[k].first + v], &cur->vtx[c->first + v],
                                                &g_out[c->first + v], t);
            }
        }
    if (blended) *blended = done;
    return g_out;
}
