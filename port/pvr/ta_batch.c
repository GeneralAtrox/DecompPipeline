/* Turns the TA's polygons into batches of triangles.
 *
 * A strip's triangle k is vertices k, k+1, k+2; odd triangles swap their first two so that every triangle of a strip
 * has the first one's winding, as the PowerVR sees them. The ISP word's culling mode then drops triangles by the
 * sign of their screen-space area (y down: positive is clockwise on screen): mode 2 culls counter-clockwise, mode 3
 * clockwise. Flat-shaded polygons (no Gouraud bit) take the colours of each triangle's last vertex. */
#include <stdlib.h>
#include <string.h>
#include "pvr_int.h"
#include "platform.h"

#define ISP_CULL_SHIFT 27
#define ISP_GOURAUD (1u << 23)
#define CULL_CCW 2u
#define CULL_CW 3u

void tb_reset(PvrScene *s)
{
    s->nvtx = 0;
    s->nbatch = 0;
    s->passes = 0;
    s->max_z = 0.0f;
    s->strip_n = 0;
    s->open = 0;
    memset(s->range, 0, sizeof s->range);
}

static void *grow(void *p, u32 *cap, u32 need, u32 size)
{
    u32 n = *cap ? *cap : 4096u;
    void *q;
    while (n < need) n *= 2u;
    if (n == *cap) return p;
    q = realloc(p, (size_t)n * size);
    if (!q) port_exit("pvr: out of memory for the scene", 4);
    *cap = n;
    return q;
}

void tb_poly(PvrScene *s, u32 list, u32 isp, u32 tsp, u32 tcw, u32 flags, const u16 clip[4])
{
    PvrBatch *b = s->nbatch ? &s->batch[s->nbatch - 1u] : NULL;
    s->strip_n = 0;
    if (s->open && b && b->list == list && b->isp == isp && b->tsp == tsp && b->tcw == tcw && b->flags == flags &&
        !memcmp(b->clip, clip, sizeof b->clip))
        return;
    s->batch = grow(s->batch, &s->bcap, s->nbatch + 1u, sizeof *s->batch);
    b = &s->batch[s->nbatch++];
    b->list = list;
    b->first = s->nvtx;
    b->count = 0;
    b->isp = isp;
    b->tsp = tsp;
    b->tcw = tcw;
    b->flags = flags;
    memmove(b->clip, clip, sizeof b->clip);
    s->open = 1;
}

static int finite_depth(f32 z) { return z > 0.0f && z < 3.0e38f; }

static void emit(PvrScene *s, const PvrVtx *a, const PvrVtx *b, const PvrVtx *c)
{
    PvrBatch *bt = &s->batch[s->nbatch - 1u];
    u32 cull = (bt->isp >> ISP_CULL_SHIFT) & 3u;
    f32 area = (b->x - a->x) * (c->y - a->y) - (b->y - a->y) * (c->x - a->x);
    PvrVtx *o;
    if (g_pvr_debug & PVR_DBG_NOCULL) cull = 0;
    if ((cull == CULL_CCW && area < 0.0f) || (cull == CULL_CW && area > 0.0f)) return;
    if (!finite_depth(a->z) || !finite_depth(b->z) || !finite_depth(c->z)) {
        pvr_todo("pvr: a vertex with 1/w <= 0 or not finite (list %u)", bt->list);
        return;
    }
    s->vtx = grow(s->vtx, &s->vcap, s->nvtx + 3u, sizeof *s->vtx);
    o = &s->vtx[s->nvtx];
    o[0] = *a;
    o[1] = *b;
    o[2] = *c;
    if (!(bt->isp & ISP_GOURAUD)) {
        o[0].base = o[1].base = c->base;
        o[0].offset = o[1].offset = c->offset;
    }
    for (u32 i = 0; i < 3u; i++) {
        o[i].batch = s->nbatch - 1u;
        if (o[i].z > s->max_z) s->max_z = o[i].z;
    }
    s->nvtx += 3u;
    bt->count += 3u;
}

void tb_vertex(PvrScene *s, const PvrVtx *v, int end_of_strip)
{
    if (!s->open) return;
    if (s->strip_n >= 2u) {
        if (s->strip_n & 1u)
            emit(s, &s->strip[1], &s->strip[0], v);
        else
            emit(s, &s->strip[0], &s->strip[1], v);
    }
    s->strip[0] = s->strip[1];
    s->strip[1] = *v;
    s->strip_n++;
    if (end_of_strip) s->strip_n = 0;
}

void tb_quad(PvrScene *s, const PvrVtx v[4])
{
    if (!s->open) return;
    emit(s, &v[0], &v[1], &v[2]);
    emit(s, &v[0], &v[2], &v[3]);
    s->strip_n = 0;
}

void tb_tri(PvrScene *s, const PvrVtx v[3])
{
    if (!s->open) return;
    emit(s, &v[0], &v[1], &v[2]);
    s->strip_n = 0;
}
