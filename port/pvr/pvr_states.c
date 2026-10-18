/* D3D11 state objects for the PowerVR's render state, created once per distinct state.
 *
 * Blending (TSP 31..26): the eight PowerVR factors; for the source "other colour" is the destination, for the
 * destination it is the source. The opaque and punch-through lists do not blend. Depth (ISP 31..29, 26): the eight
 * compare modes on 1/w, which the depth buffer holds scaled, so "greater" means nearer as on the PowerVR; the
 * translucent list never writes depth. The opaque and punch-through lists also write stencil bit 0x80 as the
 * reference value gives it (set for shadow polygons, pvr_modvol.c), so it follows the visible polygon. Sampling
 * (TSP 18..8): flip and clamp per axis, point / bilinear / trilinear, and the mip-map D adjust as a LOD bias.
 * --aniso N (a look-review prototype) makes the filtered modes anisotropic; point sampling stays point. */
#include <math.h>
#include <string.h>
#include "pvr_int.h"
#include "platform.h"

#define TSP_SRC_SHIFT 29
#define TSP_DST_SHIFT 26
#define TSP_SRC_SELECT (1u << 25)
#define TSP_DST_SELECT (1u << 24)
#define TSP_FLIP_U (1u << 18)
#define TSP_FLIP_V (1u << 17)
#define TSP_CLAMP_U (1u << 16)
#define TSP_CLAMP_V (1u << 15)
#define TSP_FILTER_SHIFT 13
#define TSP_DADJ_SHIFT 8
#define ISP_DEPTH_SHIFT 29
#define ISP_ZWRITE_OFF (1u << 26)
#define SAMPLERS 64u
#define STENCIL_SHADOW 0x80u
#define DEPTH_STATES 32u /* mode, depth write, shadow stencil */

static ID3D11BlendState *g_blend[65]; /* [64]: no blending */
static ID3D11DepthStencilState *g_depth[DEPTH_STATES];
static ID3D11RasterizerState *g_raster;
static struct {
    u32 key;
    ID3D11SamplerState *s;
} g_samplers[SAMPLERS];
static u32 g_nsamplers;

static D3D11_BLEND factor(u32 f, int src, int alpha)
{
    switch (f) {
    case 0: return D3D11_BLEND_ZERO;
    case 1: return D3D11_BLEND_ONE;
    case 2: return alpha ? (src ? D3D11_BLEND_DEST_ALPHA : D3D11_BLEND_SRC_ALPHA)
                         : (src ? D3D11_BLEND_DEST_COLOR : D3D11_BLEND_SRC_COLOR);
    case 3: return alpha ? (src ? D3D11_BLEND_INV_DEST_ALPHA : D3D11_BLEND_INV_SRC_ALPHA)
                         : (src ? D3D11_BLEND_INV_DEST_COLOR : D3D11_BLEND_INV_SRC_COLOR);
    case 4: return D3D11_BLEND_SRC_ALPHA;
    case 5: return D3D11_BLEND_INV_SRC_ALPHA;
    case 6: return D3D11_BLEND_DEST_ALPHA;
    default: return D3D11_BLEND_INV_DEST_ALPHA;
    }
}

ID3D11BlendState *pvr_blend_state(u32 list, u32 tsp)
{
    u32 key = list == PVRX_LIST_TRANSLUCENT ? (tsp >> TSP_DST_SHIFT) & 63u : 64u;
    D3D11_BLEND_DESC d;
    if (g_blend[key]) return g_blend[key];
    if (key < 64u && (tsp & (TSP_SRC_SELECT | TSP_DST_SELECT))) pvr_todo("pvr: the secondary accumulation buffer");
    memset(&d, 0, sizeof d);
    d.RenderTarget[0].RenderTargetWriteMask = D3D11_COLOR_WRITE_ENABLE_ALL;
    if (key < 64u) {
        u32 src = key >> 3, dst = key & 7u;
        d.RenderTarget[0].BlendEnable = TRUE;
        d.RenderTarget[0].SrcBlend = factor(src, 1, 0);
        d.RenderTarget[0].DestBlend = factor(dst, 0, 0);
        d.RenderTarget[0].BlendOp = D3D11_BLEND_OP_ADD;
        d.RenderTarget[0].SrcBlendAlpha = factor(src, 1, 1);
        d.RenderTarget[0].DestBlendAlpha = factor(dst, 0, 1);
        d.RenderTarget[0].BlendOpAlpha = D3D11_BLEND_OP_ADD;
        pvr_seen("blend source %u, destination %u", src, dst);
    }
    if (FAILED(ID3D11Device_CreateBlendState(g_gfx.dev, &d, &g_blend[key]))) return NULL;
    return g_blend[key];
}

ID3D11DepthStencilState *pvr_depth_state(u32 list, u32 isp)
{
    static const D3D11_COMPARISON_FUNC funcs[8] = {
        D3D11_COMPARISON_NEVER,   D3D11_COMPARISON_LESS,      D3D11_COMPARISON_EQUAL,         D3D11_COMPARISON_LESS_EQUAL,
        D3D11_COMPARISON_GREATER, D3D11_COMPARISON_NOT_EQUAL, D3D11_COMPARISON_GREATER_EQUAL, D3D11_COMPARISON_ALWAYS};
    u32 mode = (isp >> ISP_DEPTH_SHIFT) & 7u;
    int write = list != PVRX_LIST_TRANSLUCENT && !(isp & ISP_ZWRITE_OFF), mark = list != PVRX_LIST_TRANSLUCENT;
    u32 key = mode * 4u + (u32)write * 2u + (u32)mark;
    D3D11_DEPTH_STENCIL_DESC d;
    if (g_depth[key]) return g_depth[key];
    memset(&d, 0, sizeof d);
    d.DepthEnable = TRUE;
    d.DepthWriteMask = write ? D3D11_DEPTH_WRITE_MASK_ALL : D3D11_DEPTH_WRITE_MASK_ZERO;
    d.DepthFunc = funcs[mode];
    if (mark) {
        d.StencilEnable = TRUE;
        d.StencilReadMask = 0;
        d.StencilWriteMask = STENCIL_SHADOW;
        d.FrontFace.StencilFunc = D3D11_COMPARISON_ALWAYS;
        d.FrontFace.StencilFailOp = d.FrontFace.StencilDepthFailOp = D3D11_STENCIL_OP_KEEP;
        d.FrontFace.StencilPassOp = D3D11_STENCIL_OP_REPLACE;
        d.BackFace = d.FrontFace;
    }
    if (FAILED(ID3D11Device_CreateDepthStencilState(g_gfx.dev, &d, &g_depth[key]))) return NULL;
    return g_depth[key];
}

static D3D11_TEXTURE_ADDRESS_MODE address(u32 tsp, u32 clamp, u32 flip)
{
    if (tsp & clamp) return D3D11_TEXTURE_ADDRESS_CLAMP;
    return tsp & flip ? D3D11_TEXTURE_ADDRESS_MIRROR : D3D11_TEXTURE_ADDRESS_WRAP;
}

ID3D11SamplerState *pvr_sampler_state(u32 tsp)
{
    u32 key = tsp & 0x7ff00u, filter = (tsp >> TSP_FILTER_SHIFT) & 3u, dadj = (tsp >> TSP_DADJ_SHIFT) & 15u;
    D3D11_SAMPLER_DESC d;
    for (u32 i = 0; i < g_nsamplers; i++)
        if (g_samplers[i].key == key) return g_samplers[i].s;
    if (g_nsamplers == SAMPLERS) return g_samplers[0].s;
    memset(&d, 0, sizeof d);
    d.Filter = filter == 0 ? D3D11_FILTER_MIN_MAG_MIP_POINT
             : filter == 1 ? D3D11_FILTER_MIN_MAG_LINEAR_MIP_POINT
                           : D3D11_FILTER_MIN_MAG_MIP_LINEAR;
    d.AddressU = address(tsp, TSP_CLAMP_U, TSP_FLIP_U);
    d.AddressV = address(tsp, TSP_CLAMP_V, TSP_FLIP_V);
    d.AddressW = D3D11_TEXTURE_ADDRESS_CLAMP;
    d.MipLODBias = dadj ? log2f((f32)dadj / 4.0f) : 0.0f;
    d.MaxAnisotropy = 1;
    if (filter != 0u && g_gfx.aniso > 1u) {
        d.Filter = D3D11_FILTER_ANISOTROPIC;
        d.MaxAnisotropy = g_gfx.aniso;
    }
    d.ComparisonFunc = D3D11_COMPARISON_NEVER;
    d.MaxLOD = D3D11_FLOAT32_MAX;
    if (FAILED(ID3D11Device_CreateSamplerState(g_gfx.dev, &d, &g_samplers[g_nsamplers].s))) return NULL;
    g_samplers[g_nsamplers].key = key;
    return g_samplers[g_nsamplers++].s;
}

ID3D11RasterizerState *pvr_raster_state(void)
{
    D3D11_RASTERIZER_DESC d;
    if (g_raster) return g_raster;
    memset(&d, 0, sizeof d);
    d.FillMode = D3D11_FILL_SOLID;
    d.CullMode = D3D11_CULL_NONE; /* culled while parsing, by the ISP's mode */
    d.DepthClipEnable = FALSE;
    d.ScissorEnable = TRUE;
    if (FAILED(ID3D11Device_CreateRasterizerState(g_gfx.dev, &d, &g_raster))) return NULL;
    return g_raster;
}

void pvr_states_release(void)
{
    for (u32 i = 0; i < 65u; i++)
        if (g_blend[i]) ID3D11BlendState_Release(g_blend[i]);
    for (u32 i = 0; i < DEPTH_STATES; i++)
        if (g_depth[i]) ID3D11DepthStencilState_Release(g_depth[i]);
    for (u32 i = 0; i < g_nsamplers; i++) ID3D11SamplerState_Release(g_samplers[i].s);
    if (g_raster) ID3D11RasterizerState_Release(g_raster);
    memset(g_blend, 0, sizeof g_blend);
    memset(g_depth, 0, sizeof g_depth);
    g_nsamplers = 0;
    g_raster = NULL;
}
