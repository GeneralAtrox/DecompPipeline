/* The parts' in-between frame (--interp-parts, pvr_parts.c): a scene's vertices blended from where pvr_parts_from
 * found each a tick before. Apart from pvr_parts.c only for its 400-line cap; it reads nothing but the scene. */
#include <stdlib.h>
#include <string.h>
#include "pvr_int.h"

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
