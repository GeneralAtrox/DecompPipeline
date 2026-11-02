/* What an in-between frame (pvr_blend_draw in pvr_draw.c) keeps of the scene drawn last.
 *
 * The game goes on after a flip that does not wait (Kamui 1's), and may write texture memory, the palette and the
 * registers before an in-between frame of the scene just drawn: the frame keeps the textures that scene was decoded
 * with (a reference each: the cache may drop them) and the registers it was drawn with.
 *
 * The renderer's target outside the clip rectangle is not cleared by a scene (clear_background): what the last scene
 * left there stays under the next one. An in-between frame draws into the same target, so when the clip is not the
 * whole screen the target is kept before the first one of a tick and put back after each, and the next tick's scene
 * sees exactly what it would have without them. */
#include <stdlib.h>
#include "pvr_int.h"
#include "platform.h"

static ID3D11Texture2D *g_hold; /* the target kept around in-between frames */
static u32 g_hold_w, g_hold_h, g_hold_valid, g_hold_used;
static ID3D11ShaderResourceView **g_keep; /* the textures of the scene drawn last */
static u32 g_keep_n, g_keep_cap;
static PvrState g_snap; /* the registers it was drawn with */

/* Keeps the n textures of the scene just drawn (a reference each) and the registers now. */
void pvr_retain(ID3D11ShaderResourceView *const *srv, u32 n)
{
    for (u32 i = 0; i < g_keep_n; i++)
        if (g_keep[i]) ID3D11ShaderResourceView_Release(g_keep[i]);
    g_keep_n = 0;
    if (n > g_keep_cap) {
        void *a = realloc(g_keep, n * sizeof *g_keep);
        if (!a) return;
        g_keep = a;
        g_keep_cap = n;
    }
    for (u32 i = 0; i < n; i++) {
        g_keep[i] = srv[i];
        if (g_keep[i]) ID3D11ShaderResourceView_AddRef(g_keep[i]);
    }
    g_keep_n = n;
    g_snap = g_pvr;
}

ID3D11ShaderResourceView **pvr_retained(u32 *n)
{
    *n = g_keep_n;
    return g_keep;
}

const PvrState *pvr_retained_state(void) { return &g_snap; }

static int clip_is_full(void)
{
    return g_pvr.clip[0] == 0u && g_pvr.clip[1] == 0u && g_pvr.clip[2] + 1u >= 640u && g_pvr.clip[3] + 1u >= 480u;
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
    if (!g_hold) return;
    if (save) ID3D11DeviceContext_CopySubresourceRegion(g_gfx.ctx, (ID3D11Resource *)g_hold, 0, 0, 0, 0, draw, 0, NULL);
    else ID3D11DeviceContext_CopySubresourceRegion(g_gfx.ctx, draw, 0, 0, 0, 0, (ID3D11Resource *)g_hold, 0, NULL);
    g_hold_valid = save != 0;
}

/* A new scene was drawn: the target is not kept any more. */
void pvr_hold_reset(void) { g_hold_valid = 0; }

/* Before an in-between frame, with the scene's registers in g_pvr: keeps the target if the clip is not the screen. */
void pvr_hold_begin(void)
{
    g_hold_used = !clip_is_full();
    if (g_hold_used && !g_hold_valid) hold_copy(1);
}

/* After it: puts the kept target back. */
void pvr_hold_end(void)
{
    if (g_hold_used && g_hold_valid) hold_copy(0);
}
