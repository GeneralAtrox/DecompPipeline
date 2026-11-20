/* What an in-between frame (pvr_blend_draw in pvr_draw.c) keeps of the scenes drawn last, a slot per render.
 *
 * A frame is one render, or several (a split screen: pvr_render_part, Kamui 2's KM_RENDER_NOFLIP), each drawing a view
 * into the frame, which the last one shows. Each render has the slot of its place in the frame, with its own scene
 * (pvr_draw.c) and parts a tick before (pvr_parts.c): an in-between frame draws every slot, each blended from the same
 * view a tick before, and none is drawn while the frame is not whole (its views would be from two ticks).
 *
 * The game goes on after a flip that does not wait (Kamui 1's), and may write texture memory, the palette and the
 * registers before an in-between frame of the scene just drawn: the frame keeps the textures that scene was decoded
 * with (a reference each: the cache may drop them) and the registers it was drawn with.
 *
 * The renderer's target outside the clip rectangle is not cleared by a scene (clear_background): what the last scene
 * left there stays under the next one. An in-between frame draws into the same target, so when the clip is not the
 * whole screen the target is kept before the first one of a tick and put back before the next tick's scene, which so
 * sees exactly what it would have without them. Not sooner: an in-between frame is drawn into the frame target itself
 * (without --ssaa the draw target is the frame) and shown from it, so putting it back after each showed the scene
 * drawn last instead (a split screen got no in-between frames). */
#include <stdlib.h>
#include <string.h>
#include "pvr_int.h"
#include "platform.h"

static ID3D11Texture2D *g_hold; /* the target kept around in-between frames */
static u32 g_hold_w, g_hold_h, g_hold_valid, g_hold_used;
typedef struct Slot {
    ID3D11ShaderResourceView **keep; /* the textures of its scene drawn last */
    u32 n, cap;
    PvrState snap; /* the registers it was drawn with */
    PvrPass passes[PVRX_MAX_PASSES];
    int keeps; /* it kept the frame (port_render_keep_frame) */
} Slot;

static Slot g_slots[PVR_SLOTS];
static u32 g_slot, g_next, g_count = 1u;
static int g_part_next, g_partial;

void pvr_render_part(void) { g_part_next = 1; }

void pvr_slot_begin(void)
{
    g_partial = g_part_next, g_part_next = 0;
    g_slot = g_next < PVR_SLOTS ? g_next : PVR_SLOTS - 1u;
    if (g_partial) g_next++;
    else g_count = g_next + 1u, g_next = 0;
}

int pvr_slot_alone(void) { return !g_next && !g_part_next && g_count == 1u; }
u32 pvr_slot(void) { return g_slot; }
u32 pvr_slots(void) { return g_count; }
int pvr_slot_partial(void) { return g_partial; }
int pvr_slot_keeps(u32 slot) { return slot < PVR_SLOTS && g_slots[slot].keeps; }

/* Keeps in the render's slot the n textures of the scene just drawn (a reference each), the registers now, its passes
 * and whether it kept the frame. */
void pvr_retain(ID3D11ShaderResourceView *const *srv, u32 n, const PvrPass *passes, u32 count)
{
    Slot *s = &g_slots[g_slot];
    for (u32 i = 0; i < s->n; i++)
        if (s->keep[i]) ID3D11ShaderResourceView_Release(s->keep[i]);
    s->n = 0;
    if (n > s->cap) {
        void *a = realloc(s->keep, n * sizeof *s->keep);
        if (!a) return;
        s->keep = a;
        s->cap = n;
    }
    for (u32 i = 0; i < n; i++) {
        s->keep[i] = srv[i];
        if (s->keep[i]) ID3D11ShaderResourceView_AddRef(s->keep[i]);
    }
    s->n = n;
    s->snap = g_pvr;
    s->keeps = g_pvr_keep;
    memcpy(s->passes, passes, (count < PVRX_MAX_PASSES ? count : PVRX_MAX_PASSES) * sizeof *passes);
}

const PvrState *pvr_retained(u32 slot, ID3D11ShaderResourceView ***srv, u32 *n, const PvrPass **passes, int *keep)
{
    const Slot *s = &g_slots[slot < PVR_SLOTS ? slot : 0u];
    *srv = s->keep, *n = s->n, *passes = s->passes, *keep = s->keeps;
    return &s->snap;
}

static int clip_is_full(const PvrState *s)
{
    return s->clip[0] == 0u && s->clip[1] == 0u && s->clip[2] + 1u >= 640u && s->clip[3] + 1u >= 480u;
}

static void hold_copy(int save)
{
    D3D11_TEXTURE2D_DESC d;
    ID3D11Resource *draw = (ID3D11Resource *)g_gfx.draw;
    ID3D11Texture2D_GetDesc(g_gfx.draw, &d);
    if (save && (!g_hold || g_hold_w != d.Width || g_hold_h != d.Height)) {
        if (g_hold) ID3D11Texture2D_Release(g_hold);
        g_hold = NULL;
        d.MipLevels = 1;
        d.BindFlags = 0;
        d.MiscFlags = 0;
        d.Usage = D3D11_USAGE_DEFAULT;
        d.CPUAccessFlags = 0;
        if (FAILED(ID3D11Device_CreateTexture2D(g_gfx.dev, &d, NULL, &g_hold))) return;
        g_hold_w = d.Width;
        g_hold_h = d.Height;
    }
    if (!g_hold || (!save && (g_hold_w != d.Width || g_hold_h != d.Height))) return; /* resized since */
    if (save) ID3D11DeviceContext_CopySubresourceRegion(g_gfx.ctx, (ID3D11Resource *)g_hold, 0, 0, 0, 0, draw, 0, NULL);
    else ID3D11DeviceContext_CopySubresourceRegion(g_gfx.ctx, draw, 0, 0, 0, 0, (ID3D11Resource *)g_hold, 0, NULL);
    g_hold_valid = save != 0;
}

/* A new scene is drawn: the target the in-between frames drew over is put back first, and is not kept any more. */
void pvr_hold_reset(void)
{
    if (g_hold_used && g_hold_valid) hold_copy(0);
    g_hold_valid = 0;
}

/* Before an in-between frame: keeps the target if a slot's clip is not the screen. */
void pvr_hold_begin(void)
{
    g_hold_used = 0;
    for (u32 i = 0; i < g_count && i < PVR_SLOTS; i++) g_hold_used |= !clip_is_full(&g_slots[i].snap);
    if (g_hold_used && !g_hold_valid) hold_copy(1);
}
