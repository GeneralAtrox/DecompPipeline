/* Autosorted translucency (pvr_oit.hlsl): a per-pixel list head image, a fragment pool, and a copy of the opaque
 * picture the resolve blends over. pvr_draw.c draws a pass's translucent batches between pvr_oit_begin and
 * pvr_oit_resolve, then binds its own pipeline again. Adapted from Lodoss's render_d3d_transparency (MIT, the
 * user's own), without its bands and peeling: a pixel keeps at most OIT_MAX fragments (pvr_oit.hlsl) and the pool
 * holds OIT_PER_PIXEL per pixel on average, up to OIT_POOL_MAX (times the samples per pixel with --ssaa, halved
 * until the device takes it). Fragments past the pool's end are dropped, and which ones depends on the order the GPU
 * appended them. So each pass's fragment count is read back (a few frames late, never waiting on the GPU), and a
 * pool that a pass overflowed grows to that count and a half, up to OIT_POOL_GROW_MAX (or what the device takes).
 * The exit report gives the peak and any overflow. */
#include <string.h>
#include "pvr_int.h"
#include "platform.h"
#include "pvr_gather.h"
#include "pvr_full.h"
#include "pvr_resolve.h"

#define OIT_NODE_BYTES 20u
#define OIT_PER_PIXEL 12u /* the busiest pass measured drew 8.8 a pixel (2.69 M fragments at 640x480) */
#define OIT_POOL_MAX (24u * 1024u * 1024u)      /* 480 MB to start with: 7 a pixel at 2133x1600 */
#define OIT_POOL_GROW_MAX (36u * 1024u * 1024u) /* 720 MB: the most an overflowing pass grows it to */

static ID3D11PixelShader *g_gather, *g_resolve;
static ID3D11VertexShader *g_full;
static ID3D11Texture2D *g_heads, *g_copy;
static ID3D11UnorderedAccessView *g_heads_uav, *g_nodes_uav;
static ID3D11ShaderResourceView *g_heads_srv, *g_nodes_srv, *g_copy_srv;
static ID3D11Buffer *g_nodes;
static u32 g_w, g_h;
static int g_broken;
#define OIT_COUNTS 4u
static ID3D11Buffer *g_counts[OIT_COUNTS]; /* staging copies of the pool's counter, read back in turn */
static int g_counted[OIT_COUNTS];
static u32 g_count_next, g_count_read;
static u32 g_pool, g_grow, g_peak, g_overflows; /* the pool's size; an overflowing pass's count; the largest count */
static u32 g_pool_max;                          /* the most it may grow to (lowered if the device refuses more) */
static u32 g_logged;                            /* the largest overflowing count logged */

static void release(void)
{
    IUnknown **all[] = {(IUnknown **)&g_heads_uav, (IUnknown **)&g_heads_srv, (IUnknown **)&g_heads,
                        (IUnknown **)&g_nodes_uav, (IUnknown **)&g_nodes_srv, (IUnknown **)&g_nodes,
                        (IUnknown **)&g_copy_srv, (IUnknown **)&g_copy};
    for (u32 i = 0; i < sizeof all / sizeof all[0]; i++)
        if (*all[i]) {
            IUnknown_Release(*all[i]);
            *all[i] = NULL;
        }
    for (u32 i = 0; i < OIT_COUNTS; i++) {
        if (g_counts[i]) ID3D11Buffer_Release(g_counts[i]);
        g_counts[i] = NULL;
        g_counted[i] = 0;
    }
    g_count_next = g_count_read = 0;
}

static int make_heads(ID3D11Device *dev)
{
    D3D11_TEXTURE2D_DESC d;
    memset(&d, 0, sizeof d);
    d.Width = g_w;
    d.Height = g_h;
    d.MipLevels = 1;
    d.ArraySize = 1;
    d.Format = DXGI_FORMAT_R32_UINT;
    d.SampleDesc.Count = 1;
    d.BindFlags = D3D11_BIND_UNORDERED_ACCESS | D3D11_BIND_SHADER_RESOURCE;
    return SUCCEEDED(ID3D11Device_CreateTexture2D(dev, &d, NULL, &g_heads)) &&
           SUCCEEDED(ID3D11Device_CreateUnorderedAccessView(dev, (ID3D11Resource *)g_heads, NULL, &g_heads_uav)) &&
           SUCCEEDED(ID3D11Device_CreateShaderResourceView(dev, (ID3D11Resource *)g_heads, NULL, &g_heads_srv));
}

/* the pool: OIT_PER_PIXEL fragments a pixel up to OIT_POOL_MAX, or at least `least` (from an overflowing pass) up
 * to OIT_POOL_GROW_MAX (both times the samples per pixel) */
static int make_nodes(ID3D11Device *dev, u64 least)
{
    D3D11_BUFFER_DESC d;
    D3D11_UNORDERED_ACCESS_VIEW_DESC u;
    u64 samples = (u64)(g_w / g_gfx.frame_w) * (g_h / g_gfx.frame_h);
    u64 want = (u64)g_w * g_h * OIT_PER_PIXEL, cap = OIT_POOL_MAX * samples;
    u32 count;
    if (want > cap) want = cap;
    if (want < least) want = least;
    if (!g_pool_max) {
        u64 most = OIT_POOL_GROW_MAX * samples;
        g_pool_max = most > 0xffffffffu / OIT_NODE_BYTES ? 0xffffffffu / OIT_NODE_BYTES : (u32)most;
    }
    count = want > g_pool_max ? g_pool_max : (u32)want;
    memset(&d, 0, sizeof d);
    d.BindFlags = D3D11_BIND_UNORDERED_ACCESS | D3D11_BIND_SHADER_RESOURCE;
    d.MiscFlags = D3D11_RESOURCE_MISC_BUFFER_STRUCTURED;
    d.StructureByteStride = OIT_NODE_BYTES;
    memset(&u, 0, sizeof u);
    u.Format = DXGI_FORMAT_UNKNOWN;
    u.ViewDimension = D3D11_UAV_DIMENSION_BUFFER;
    u.Buffer.Flags = D3D11_BUFFER_UAV_FLAG_COUNTER;
    for (;;) {
        d.ByteWidth = count * OIT_NODE_BYTES;
        if (SUCCEEDED(ID3D11Device_CreateBuffer(dev, &d, NULL, &g_nodes))) break;
        if (least && count > g_pool) { /* the device refuses more: keep the size it had, and stop growing */
            port_log("pvr: the device refused an autosort pool of %u fragments\n", count);
            count = g_pool_max = g_pool;
            continue;
        }
        if ((u64)count <= (u64)g_w * g_h) return 0; /* not even one fragment a pixel */
        count /= 2u;
    }
    if (count > OIT_POOL_MAX) port_log("pvr: autosort pool of %u fragments for %ux%u\n", count, g_w, g_h);
    g_pool = count;
    u.Buffer.NumElements = count;
    return g_nodes &&
           SUCCEEDED(ID3D11Device_CreateUnorderedAccessView(dev, (ID3D11Resource *)g_nodes, &u, &g_nodes_uav)) &&
           SUCCEEDED(ID3D11Device_CreateShaderResourceView(dev, (ID3D11Resource *)g_nodes, NULL, &g_nodes_srv));
}

static int make_counts(ID3D11Device *dev)
{
    D3D11_BUFFER_DESC d;
    memset(&d, 0, sizeof d);
    d.ByteWidth = 16;
    d.Usage = D3D11_USAGE_STAGING;
    d.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
    for (u32 i = 0; i < OIT_COUNTS; i++)
        if (FAILED(ID3D11Device_CreateBuffer(dev, &d, NULL, &g_counts[i]))) return 0;
    return 1;
}

/* Reads back the pass counts the GPU has finished, oldest first, without waiting. */
static void read_counts(void)
{
    D3D11_MAPPED_SUBRESOURCE m;
    while (g_counted[g_count_read]) {
        ID3D11Resource *r = (ID3D11Resource *)g_counts[g_count_read];
        u32 n;
        if (ID3D11DeviceContext_Map(g_gfx.ctx, r, 0, D3D11_MAP_READ, D3D11_MAP_FLAG_DO_NOT_WAIT, &m) != S_OK) return;
        n = *(const u32 *)m.pData;
        ID3D11DeviceContext_Unmap(g_gfx.ctx, r, 0);
        g_counted[g_count_read] = 0;
        g_count_read = (g_count_read + 1u) % OIT_COUNTS;
        if (n > g_peak) g_peak = n;
        if (n > g_pool) {
            if (n > g_logged)
                port_log("pvr: a pass drew %u translucent fragments, past the autosort pool of %u (frame %u)\n", n,
                         g_pool, g_gfx.frames);
            if (n > g_logged) g_logged = n;
            g_overflows++;
            if (n > g_grow && g_pool < g_pool_max) g_grow = n;
        }
    }
}

void pvr_oit_report(void)
{
    if (g_pool)
        port_log("pvr: autosort pool of %u fragments; the largest pass drew %u; %u pass(es) overflowed\n", g_pool,
                 g_peak, g_overflows);
}

static int make_copy(ID3D11Device *dev)
{
    D3D11_TEXTURE2D_DESC d;
    ID3D11Texture2D_GetDesc(g_gfx.draw, &d);
    d.BindFlags = D3D11_BIND_SHADER_RESOURCE;
    d.MiscFlags = 0;
    return SUCCEEDED(ID3D11Device_CreateTexture2D(dev, &d, NULL, &g_copy)) &&
           SUCCEEDED(ID3D11Device_CreateShaderResourceView(dev, (ID3D11Resource *)g_copy, NULL, &g_copy_srv));
}

ID3D11VertexShader *pvr_full_vs(void)
{
    if (!g_full && FAILED(ID3D11Device_CreateVertexShader(g_gfx.dev, g_pvr_full, sizeof g_pvr_full, NULL, &g_full)))
        g_full = NULL;
    return g_full;
}

static int ready(void)
{
    ID3D11Device *dev = g_gfx.dev;
    if (g_broken) return 0;
    if (!g_gather &&
        (FAILED(ID3D11Device_CreatePixelShader(dev, g_pvr_gather, sizeof g_pvr_gather, NULL, &g_gather)) ||
         !pvr_full_vs() ||
         FAILED(ID3D11Device_CreatePixelShader(dev, g_pvr_resolve, sizeof g_pvr_resolve, NULL, &g_resolve))))
        g_broken = 1;
    if (!g_broken && g_heads) read_counts();
    if (!g_broken && (!g_heads || g_w != g_gfx.draw_w || g_h != g_gfx.draw_h || g_grow > g_pool)) {
        int resized = !g_heads || g_w != g_gfx.draw_w || g_h != g_gfx.draw_h;
        u64 least = !resized && g_grow > g_pool ? (u64)g_grow + g_grow / 2u : 0;
        if (resized) g_pool_max = 0; /* make_nodes sets it again for the new size */
        release();
        g_w = g_gfx.draw_w;
        g_h = g_gfx.draw_h;
        if (!make_heads(dev) || !make_nodes(dev, least) || !make_counts(dev) || !make_copy(dev)) {
            release();
            g_broken = 1;
        }
        g_grow = 0;
    }
    if (g_broken) port_log("pvr: no autosort resources; translucent polygons are drawn in submission order\n");
    return !g_broken;
}

int pvr_oit_begin(ID3D11DepthStencilView *dsv)
{
    static const UINT none[4] = {0xffffffffu, 0xffffffffu, 0xffffffffu, 0xffffffffu};
    ID3D11ShaderResourceView *unbind[3] = {NULL, NULL, NULL};
    ID3D11UnorderedAccessView *uav[2];
    UINT zero[2] = {0, 0};
    if (!ready()) return 0;
    uav[0] = g_heads_uav;
    uav[1] = g_nodes_uav;
    ID3D11DeviceContext_PSSetShaderResources(g_gfx.ctx, 2, 3, unbind);
    ID3D11DeviceContext_ClearUnorderedAccessViewUint(g_gfx.ctx, g_heads_uav, none);
    ID3D11DeviceContext_OMSetRenderTargetsAndUnorderedAccessViews(g_gfx.ctx, 0, NULL, dsv, 0, 2, uav, zero);
    ID3D11DeviceContext_PSSetShader(g_gfx.ctx, g_gather, NULL, 0);
    return 1;
}

void pvr_oit_resolve(void)
{
    ID3D11DeviceContext *cx = g_gfx.ctx;
    ID3D11ShaderResourceView *srv[3], *unbind[3] = {NULL, NULL, NULL};
    D3D11_RECT all = {0, 0, (LONG)g_gfx.draw_w, (LONG)g_gfx.draw_h};
    srv[0] = g_heads_srv;
    srv[1] = g_nodes_srv;
    srv[2] = g_copy_srv;
    if (!g_counted[g_count_next]) {
        ID3D11DeviceContext_CopyStructureCount(cx, g_counts[g_count_next], 0, g_nodes_uav);
        g_counted[g_count_next] = 1;
        g_count_next = (g_count_next + 1u) % OIT_COUNTS;
    }
    ID3D11DeviceContext_OMSetRenderTargets(cx, 1, &g_gfx.draw_rtv, NULL);
    ID3D11DeviceContext_CopyResource(cx, (ID3D11Resource *)g_copy, (ID3D11Resource *)g_gfx.draw);
    ID3D11DeviceContext_OMSetBlendState(cx, NULL, NULL, 0xffffffffu);
    ID3D11DeviceContext_RSSetScissorRects(cx, 1, &all);
    ID3D11DeviceContext_IASetInputLayout(cx, NULL);
    ID3D11DeviceContext_VSSetShader(cx, g_full, NULL, 0);
    ID3D11DeviceContext_PSSetShader(cx, g_resolve, NULL, 0);
    ID3D11DeviceContext_PSSetShaderResources(cx, 2, 3, srv);
    ID3D11DeviceContext_Draw(cx, 3, 0);
    ID3D11DeviceContext_PSSetShaderResources(cx, 2, 3, unbind);
}
