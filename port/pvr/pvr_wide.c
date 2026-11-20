/* A pack image wider than its texture (index word wide=W: its middle W columns are the texture): a backdrop with art
 * for a frame wider than 4:3. Host-side only, like the pack: guest memory, timing and game logic never see it.
 *
 * Every draw of such a texture samples the image's middle, so a 4:3 frame shows what a plain image would. In a wider
 * frame a draw spanning the screen's width grows about the screen's centre, with its texture coordinates in step (no
 * stretch, the middle stays put), up to the frame's edges or the image's, whichever comes first; past the image's
 * ends the frame keeps the background colour. The other draws in that scene that span the screen's width (a menu's
 * highlight bar) grow to the same width. Everything else stays where the game put it.
 *
 * It runs once a scene's textures are decoded, because the renderer learns which image a texture has only then, so
 * even a texture's first scene is right. Nearest existing: widen_full_screen in ta_batch.c, which only grows untextured
 * full-screen fades and knows no textures, and pvr_pack_rim, the same look-up for another word. */
#include "pvr_int.h"
#include "platform.h"

#define SPAN_SLOP 0.5f        /* a draw spanning the screen may start or stop this far inside it */
#define SCREEN_END_MAX 641.0f /* a draw reaching past this already fills a wider frame (widen_full_screen) */

/* The batch's horizontal extent. */
static void extent(const PvrScene *s, const PvrBatch *b, f32 *x0, f32 *x1)
{
    const PvrVtx *v = s->vtx + b->first;
    *x0 = *x1 = v[0].x;
    for (u32 i = 1; i < b->count; i++) {
        *x0 = v[i].x < *x0 ? v[i].x : *x0;
        *x1 = v[i].x > *x1 ? v[i].x : *x1;
    }
}

static int spans_screen(f32 x0, f32 x1) { return x0 <= SPAN_SLOP && x1 >= 640.0f - SPAN_SLOP && x1 <= SCREEN_END_MAX; }

/* Grows the batch about the screen's centre, its texture coordinates in step around their value at that centre. */
static void grow_batch(const PvrScene *s, const PvrBatch *b, f32 k)
{
    PvrVtx *v = s->vtx + b->first;
    const PvrVtx *lo = v, *hi = v;
    f32 u_centre;
    for (u32 i = 1; i < b->count; i++) {
        lo = v[i].x < lo->x ? &v[i] : lo;
        hi = v[i].x > hi->x ? &v[i] : hi;
    }
    u_centre = hi->x > lo->x ? lo->u + (hi->u - lo->u) * (320.0f - lo->x) / (hi->x - lo->x) : lo->u;
    for (u32 i = 0; i < b->count; i++) {
        v[i].x = 320.0f + (v[i].x - 320.0f) * k;
        if (b->flags & PVR_BF_TEXTURE) v[i].u = u_centre + (v[i].u - u_centre) * k;
    }
}

void pvr_wide_scene(PvrScene *s)
{
    f32 frame = port_widescreen(), reach = 0.0f;
    u32 backdrop = s->nbatch;
    for (u32 i = 0; i < s->nbatch; i++) {
        PvrBatch *b = &s->batch[i];
        PvrVtx *v = s->vtx + b->first;
        f32 image, x0, x1;
        if (!(b->flags & PVR_BF_TEXTURE) || !b->count) continue;
        image = pvr_pack_wide(b->tcw, b->tsp);
        if (image <= 0.0f) continue;
        for (u32 j = 0; j < b->count; j++) v[j].u = 0.5f + (v[j].u - 0.5f) / image; /* the texture is the image's middle */
        extent(s, b, &x0, &x1);
        if (spans_screen(x0, x1)) {
            reach = frame < image ? frame : image;
            backdrop = i;
        }
    }
    if (reach <= 1.0f) return;
    for (u32 i = 0; i < s->nbatch; i++) {
        PvrBatch *b = &s->batch[i];
        f32 x0, x1;
        if (!b->count || b->list == PVRX_LIST_OPAQUE_MOD || b->list == PVRX_LIST_TRANS_MOD) continue;
        extent(s, b, &x0, &x1);
        if (i == backdrop || spans_screen(x0, x1)) grow_batch(s, b, reach);
    }
}
