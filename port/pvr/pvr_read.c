/* Frame-buffer read-back (Km_ReadSurface of the frame buffer surface): the last rendered frame as the console's
 * frame buffer holds it, width x height RGB565 (640x480 for the displayed buffer). The frame target is the output's
 * size, so it is read through a staging copy and box-filtered down on the CPU; games read it rarely (a snapshot of
 * the screen behind a pause or menu frame). In-between frames (--present interp) draw over the frame target, so each
 * game frame is kept in a copy while they are on, and the read takes the game's frame, not an in-between one. A frame
 * wider or taller than 4:3 is read as the 640x480 screen in its middle: the game has nothing outside it. A game that
 * draws the picture back over the whole frame asks for the whole frame instead, squeezed to the size it reads
 * (port_read_whole_frame). */
#include <stdlib.h>
#include <string.h>
#include "pvr_int.h"
#include "platform.h"
#include "host.h"
#include "prof.h"

static ID3D11Texture2D *g_tick, *g_staging;
static u32 g_tick_w, g_tick_h, g_staging_w, g_staging_h;
static int g_tick_valid, g_read_whole;

void port_read_whole_frame(int on) { g_read_whole = on; }

/* A copy-target texture like the frame (staging when cpu is set), recreated when the frame's size changes. */
static int ensure(ID3D11Texture2D **t, u32 *w, u32 *h, int cpu)
{
    D3D11_TEXTURE2D_DESC d;
    if (*t && *w == g_gfx.frame_w && *h == g_gfx.frame_h) return 1;
    if (*t) ID3D11Texture2D_Release(*t);
    *t = NULL;
    ID3D11Texture2D_GetDesc(g_gfx.frame, &d);
    d.BindFlags = cpu ? 0u : D3D11_BIND_SHADER_RESOURCE;
    d.Usage = cpu ? D3D11_USAGE_STAGING : D3D11_USAGE_DEFAULT;
    d.CPUAccessFlags = cpu ? D3D11_CPU_ACCESS_READ : 0u;
    d.MiscFlags = 0;
    if (FAILED(ID3D11Device_CreateTexture2D(g_gfx.dev, &d, NULL, t))) return 0;
    *w = g_gfx.frame_w;
    *h = g_gfx.frame_h;
    return 1;
}

void pvr_keep_frame(void)
{
    if (port_extra_drawing()) return;
    g_tick_valid = 0;
    if (!extra_interp() || !g_gfx.frame) return;
    if (!ensure(&g_tick, &g_tick_w, &g_tick_h, 0)) return;
    ID3D11DeviceContext_CopyResource(g_gfx.ctx, (ID3D11Resource *)g_tick, (ID3D11Resource *)g_gfx.frame);
    g_tick_valid = 1;
}

static u16 rgb565(u32 r, u32 g, u32 b)
{
    return (u16)(((r * 31u + 127u) / 255u) << 11 | ((g * 63u + 127u) / 255u) << 5 | (b * 31u + 127u) / 255u);
}

/* The mean of the source pixels under destination pixel (x, y) of a dw x dh picture (an area filter, as the dumps'
 * 640x480 copies), as RGB565. */
static u16 shrink_pixel(const u8 *src, u32 pitch, const u32 scr[4], u32 x, u32 y, u32 dw, u32 dh)
{
    u32 sw = scr[2], sh = scr[3];
    u32 y0 = scr[1] + y * sh / dh, y1 = scr[1] + (y + 1u) * sh / dh;
    u32 x0 = scr[0] + x * sw / dw, x1 = scr[0] + (x + 1u) * sw / dw, sum[3] = {0, 0, 0};
    if (y1 <= y0) y1 = y0 + 1u;
    if (x1 <= x0) x1 = x0 + 1u;
    for (u32 v = y0; v < y1; v++) {
        const u8 *p = src + (size_t)v * pitch + (size_t)x0 * 4u;
        for (u32 u = x0; u < x1; u++, p += 4) {
            sum[0] += p[0];
            sum[1] += p[1];
            sum[2] += p[2];
        }
    }
    u32 n = (y1 - y0) * (x1 - x0);
    return rgb565((sum[0] + n / 2u) / n, (sum[1] + n / 2u) / n, (sum[2] + n / 2u) / n);
}

static void shrink(const u8 *src, u32 pitch, u16 *dst, u32 dw, u32 dh)
{
    u32 scr[4];
    gfx_screen_rect(g_gfx.frame_w, g_gfx.frame_h, g_read_whole ? 1.0f : g_gfx.wide_k, g_read_whole ? 1.0f : g_gfx.tall_k, scr);
    for (u32 y = 0; y < dh; y++)
        for (u32 x = 0; x < dw; x++) dst[(size_t)y * dw + x] = shrink_pixel(src, pitch, scr, x, y, dw, dh);
}

/* Copies the last game frame into the staging texture and maps it for reading; 0 if there is no frame. */
static int map_frame(D3D11_MAPPED_SUBRESOURCE *m)
{
    u64 pt = prof_begin(PROF_READ);
    int ok;
    ID3D11Texture2D *src = g_tick_valid && g_tick_w == g_gfx.frame_w && g_tick_h == g_gfx.frame_h ? g_tick
                                                                                                   : g_gfx.frame;
    if (!src || !ensure(&g_staging, &g_staging_w, &g_staging_h, 1)) {
        prof_end(pt);
        return 0;
    }
    ID3D11DeviceContext_CopyResource(g_gfx.ctx, (ID3D11Resource *)g_staging, (ID3D11Resource *)src);
    ok = !FAILED(ID3D11DeviceContext_Map(g_gfx.ctx, (ID3D11Resource *)g_staging, 0, D3D11_MAP_READ, 0, m));
    prof_end(pt);
    return ok;
}

static int read_frame_locked(void *rgb565_out, u32 width, u32 height)
{
    D3D11_MAPPED_SUBRESOURCE m;
    if (!width || !height || !map_frame(&m)) return 0;
    shrink((const u8 *)m.pData, m.RowPitch, (u16 *)rgb565_out, width, height);
    ID3D11DeviceContext_Unmap(g_gfx.ctx, (ID3D11Resource *)g_staging, 0);
    pvr_seen("frame buffer read back as %ux%u RGB565 (from %ux%u)", width, height, g_gfx.frame_w, g_gfx.frame_h);
    return 1;
}

/* One pixel of the displayed 640x480 buffer: what a read of the frame buffer's video memory returns. */
static int read_pixel_locked(u32 x, u32 y, u16 *rgb565_out)
{
    D3D11_MAPPED_SUBRESOURCE m;
    u32 scr[4];
    if (x >= 640u || y >= 480u || !map_frame(&m)) return 0;
    gfx_screen_rect(g_gfx.frame_w, g_gfx.frame_h, g_gfx.wide_k, g_gfx.tall_k, scr);
    *rgb565_out = shrink_pixel((const u8 *)m.pData, m.RowPitch, scr, x, y, 640u, 480u);
    ID3D11DeviceContext_Unmap(g_gfx.ctx, (ID3D11Resource *)g_staging, 0);
    return 1;
}

int pvr_read_frame(void *rgb565_out, u32 width, u32 height)
{
    gfx_lock();
    int r = read_frame_locked(rgb565_out, width, height);
    gfx_unlock();
    return r;
}

int gfx_read_pixel(u32 x, u32 y, u16 *rgb565_out)
{
    gfx_lock();
    int r = read_pixel_locked(x, y, rgb565_out);
    gfx_unlock();
    return r;
}
