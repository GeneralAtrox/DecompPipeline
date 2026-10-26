/* Draws a scene into the frame target, in the PowerVR's order per pass: the background (a clear to the plane's
 * colour and depth), opaque, punch-through, the opaque modifier volumes (pvr_modvol.c), translucent. Translucent
 * modifier volumes are a to-do.
 * Translucent polygons are sorted per pixel in a pass with autosort (pvr_oit.c), else drawn in submission order.
 *
 * One vertex buffer and one per-batch state buffer are uploaded per scene; each batch is one draw with its
 * texture, sampler, blend, depth state and scissor. */
#include <stdlib.h>
#include <string.h>
#include "pvr_int.h"
#include "platform.h"
#include "pvr_vs.h"
#include "pvr_ps.h"
#include <d3d11_1.h> /* ClearView, for a clear within the global clip */

#define ISP_DEPTH_SHIFT 29
#define DEPTH_NEVER 0u
#define PUNCH_REF 1.0f /* PT_ALPHA_REF 0xff: only opaque texels pass */
#define STENCIL_SHADOW 0x80u

typedef struct GpuBatch {
    u32 isp, tsp, tcw, flags;
} GpuBatch;

typedef struct FrameConsts {
    f32 depth_scale, fog_density, punch_ref, shadow_alpha; /* shadow_alpha: 1 - the cheap shadow intensity */
    f32 fog_color[4];
    u32 fog_table[128];
    f32 wide[4]; /* x: 1 / port_widescreen() */
} FrameConsts;

static PvrScene g_scene;
static ID3D11VertexShader *g_vs;
static ID3D11PixelShader *g_ps;
static ID3D11InputLayout *g_layout;
static ID3D11Buffer *g_vb, *g_bb, *g_cb;
static ID3D11ShaderResourceView *g_bb_srv;
static u32 g_vb_cap, g_bb_cap;
static ID3D11Texture2D *g_depth;
static ID3D11DepthStencilView *g_dsv;
static u32 g_depth_w, g_depth_h;
static ID3D11ShaderResourceView **g_srv;
static GpuBatch *g_gpu;
static u32 g_srv_cap;
static int g_failed;

static int init_pipeline(void)
{
    static const D3D11_INPUT_ELEMENT_DESC el[] = {
        {"POSITION", 0, DXGI_FORMAT_R32G32B32_FLOAT, 0, 0, D3D11_INPUT_PER_VERTEX_DATA, 0},
        {"TEXCOORD", 0, DXGI_FORMAT_R32G32_FLOAT, 0, 12, D3D11_INPUT_PER_VERTEX_DATA, 0},
        {"COLOR", 0, DXGI_FORMAT_B8G8R8A8_UNORM, 0, 20, D3D11_INPUT_PER_VERTEX_DATA, 0},
        {"COLOR", 1, DXGI_FORMAT_B8G8R8A8_UNORM, 0, 24, D3D11_INPUT_PER_VERTEX_DATA, 0},
        {"BATCH", 0, DXGI_FORMAT_R32_UINT, 0, 28, D3D11_INPUT_PER_VERTEX_DATA, 0},
    };
    D3D11_BUFFER_DESC d;
    if (g_vs) return 1;
    if (FAILED(ID3D11Device_CreateVertexShader(g_gfx.dev, g_pvr_vs, sizeof g_pvr_vs, NULL, &g_vs)) ||
        FAILED(ID3D11Device_CreatePixelShader(g_gfx.dev, g_pvr_ps, sizeof g_pvr_ps, NULL, &g_ps)) ||
        FAILED(ID3D11Device_CreateInputLayout(g_gfx.dev, el, 5, g_pvr_vs, sizeof g_pvr_vs, &g_layout)))
        return 0;
    memset(&d, 0, sizeof d);
    d.ByteWidth = sizeof(FrameConsts);
    d.Usage = D3D11_USAGE_DYNAMIC;
    d.BindFlags = D3D11_BIND_CONSTANT_BUFFER;
    d.CPUAccessFlags = D3D11_CPU_ACCESS_WRITE;
    return SUCCEEDED(ID3D11Device_CreateBuffer(g_gfx.dev, &d, NULL, &g_cb));
}

static int ensure_depth(void)
{
    D3D11_TEXTURE2D_DESC d;
    if (g_dsv && g_depth_w == g_gfx.draw_w && g_depth_h == g_gfx.draw_h) return 1;
    if (g_dsv) ID3D11DepthStencilView_Release(g_dsv);
    if (g_depth) ID3D11Texture2D_Release(g_depth);
    g_dsv = NULL;
    g_depth = NULL;
    memset(&d, 0, sizeof d);
    d.Width = g_gfx.draw_w;
    d.Height = g_gfx.draw_h;
    d.MipLevels = 1;
    d.ArraySize = 1;
    d.Format = DXGI_FORMAT_D32_FLOAT_S8X24_UINT; /* the stencil is for modifier volumes */
    d.SampleDesc.Count = 1;
    d.Usage = D3D11_USAGE_DEFAULT;
    d.BindFlags = D3D11_BIND_DEPTH_STENCIL;
    if (FAILED(ID3D11Device_CreateTexture2D(g_gfx.dev, &d, NULL, &g_depth)) ||
        FAILED(ID3D11Device_CreateDepthStencilView(g_gfx.dev, (ID3D11Resource *)g_depth, NULL, &g_dsv)))
        return 0;
    g_depth_w = g_gfx.draw_w;
    g_depth_h = g_gfx.draw_h;
    return 1;
}

/* A dynamic buffer of at least `bytes`, recreated larger when needed. */
static int ensure_buffer(ID3D11Buffer **b, u32 *cap, u32 bytes, UINT bind, u32 stride)
{
    D3D11_BUFFER_DESC d;
    u32 n = *cap ? *cap : 1u << 20;
    if (*b && bytes <= *cap) return 1;
    while (n < bytes) n *= 2u;
    if (*b) ID3D11Buffer_Release(*b);
    *b = NULL;
    memset(&d, 0, sizeof d);
    d.ByteWidth = n;
    d.Usage = D3D11_USAGE_DYNAMIC;
    d.BindFlags = bind;
    d.CPUAccessFlags = D3D11_CPU_ACCESS_WRITE;
    if (stride) {
        d.MiscFlags = D3D11_RESOURCE_MISC_BUFFER_STRUCTURED;
        d.StructureByteStride = stride;
    }
    if (FAILED(ID3D11Device_CreateBuffer(g_gfx.dev, &d, NULL, b))) return 0;
    *cap = n;
    return 1;
}

static int upload(ID3D11Buffer *b, const void *data, u32 bytes)
{
    D3D11_MAPPED_SUBRESOURCE m;
    if (FAILED(ID3D11DeviceContext_Map(g_gfx.ctx, (ID3D11Resource *)b, 0, D3D11_MAP_WRITE_DISCARD, 0, &m))) return 0;
    memmove(m.pData, data, bytes);
    ID3D11DeviceContext_Unmap(g_gfx.ctx, (ID3D11Resource *)b, 0);
    return 1;
}

/* Textures for every batch (decoded on first use), and the batch states the pixel shader reads. */
static int prepare_batches(void)
{
    PvrScene *s = &g_scene;
    u32 n = s->nbatch ? s->nbatch : 1u;
    if (n > g_srv_cap) {
        void *a = realloc(g_srv, n * sizeof *g_srv), *b;
        if (!a) return 0;
        g_srv = a;
        b = realloc(g_gpu, n * sizeof *g_gpu);
        if (!b) return 0;
        g_gpu = b;
        g_srv_cap = n;
    }
    memset(g_gpu, 0, sizeof *g_gpu);
    for (u32 i = 0; i < s->nbatch; i++) {
        const PvrBatch *bt = &s->batch[i];
        g_srv[i] = bt->flags & PVR_BF_TEXTURE ? pvr_tex_get(bt->tcw, bt->tsp) : NULL;
        g_gpu[i].isp = bt->isp;
        g_gpu[i].tsp = bt->tsp;
        g_gpu[i].tcw = bt->tcw;
        g_gpu[i].flags = g_srv[i] ? bt->flags : bt->flags & ~PVR_BF_TEXTURE;
    }
    if (g_bb_srv && n * sizeof(GpuBatch) > g_bb_cap) {
        ID3D11ShaderResourceView_Release(g_bb_srv);
        g_bb_srv = NULL;
    }
    if (!ensure_buffer(&g_bb, &g_bb_cap, n * (u32)sizeof(GpuBatch), D3D11_BIND_SHADER_RESOURCE, sizeof(GpuBatch)))
        return 0;
    if (!g_bb_srv) {
        D3D11_SHADER_RESOURCE_VIEW_DESC v;
        memset(&v, 0, sizeof v);
        v.Format = DXGI_FORMAT_UNKNOWN;
        v.ViewDimension = D3D11_SRV_DIMENSION_BUFFER;
        v.Buffer.NumElements = g_bb_cap / (u32)sizeof(GpuBatch);
        if (FAILED(ID3D11Device_CreateShaderResourceView(g_gfx.dev, (ID3D11Resource *)g_bb, &v, &g_bb_srv))) return 0;
    }
    return upload(g_bb, g_gpu, n * (u32)sizeof(GpuBatch));
}

static void argb_float(u32 argb, f32 out[4])
{
    out[0] = (f32)((argb >> 16) & 0xffu) / 255.0f;
    out[1] = (f32)((argb >> 8) & 0xffu) / 255.0f;
    out[2] = (f32)(argb & 0xffu) / 255.0f;
    out[3] = (f32)(argb >> 24) / 255.0f;
}

static f32 fog_density(u32 reg)
{
    f32 d = (f32)((reg >> 8) & 0xffu) / 128.0f;
    s32 e = (s32)(s8)(reg & 0xffu);
    for (; e > 0; e--) d *= 2.0f;
    for (; e < 0; e++) d *= 0.5f;
    return d;
}

static f32 frame_consts(void)
{
    FrameConsts c;
    f32 top = g_scene.max_z > g_pvr.bg_xyz[0][2] ? g_scene.max_z : g_pvr.bg_xyz[0][2];
    memset(&c, 0, sizeof c);
    c.depth_scale = top > 0.0f ? 1.0f / top : 1.0f;
    c.fog_density = fog_density(g_pvr.fog_density);
    c.punch_ref = PUNCH_REF;
    c.shadow_alpha = g_pvr.shadow < 0 ? 0.0f : 1.0f - (f32)g_pvr.shadow / 256.0f;
    argb_float(g_pvr.fog_color, c.fog_color);
    for (u32 i = 0; i < 128u; i++) c.fog_table[i] = g_pvr_debug & PVR_DBG_NOFOG ? 0u : g_pvr.fog_table[i];
    c.wide[0] = 1.0f / port_widescreen();
    upload(g_cb, &c, sizeof c);
    return c.depth_scale;
}

/* A screen x (0..640) in the draw target: widescreen puts the screen in its middle, and a clip that spans the
 * screen's width spans the frame's. */
static LONG draw_x(u32 x, int full)
{
    f32 k = port_widescreen();
    if (k <= 1.0f) return (LONG)(x * g_gfx.draw_w / 640u);
    if (full) return x ? (LONG)g_gfx.draw_w : 0;
    return (LONG)((0.5f + ((f32)x - 320.0f) / (640.0f * k)) * (f32)g_gfx.draw_w + 0.5f);
}

void pvr_scissor(const PvrBatch *b)
{
    D3D11_RECT r;
    u32 x0 = b->clip[0] > g_pvr.clip[0] ? b->clip[0] : g_pvr.clip[0];
    u32 y0 = b->clip[1] > g_pvr.clip[1] ? b->clip[1] : g_pvr.clip[1];
    u32 x1 = b->clip[2] < g_pvr.clip[2] + 1u ? b->clip[2] : g_pvr.clip[2] + 1u;
    u32 y1 = b->clip[3] < g_pvr.clip[3] + 1u ? b->clip[3] : g_pvr.clip[3] + 1u;
    r.left = draw_x(x0, x0 == 0u && x1 >= 640u);
    r.top = (LONG)(y0 * g_gfx.draw_h / 480u);
    r.right = draw_x(x1, x0 == 0u && x1 >= 640u);
    r.bottom = (LONG)(y1 * g_gfx.draw_h / 480u);
    ID3D11DeviceContext_RSSetScissorRects(g_gfx.ctx, 1, &r);
}

/* Fills the frame with the background colour; with a global clip smaller than the screen only inside it, so the
 * rest keeps what earlier scenes drew there, as the hardware's frame buffer does (a game drawing its split-screen
 * views on alternate frames clips each frame to one view). */
static void clear_background(const f32 bg[4])
{
    ID3D11DeviceContext1 *c1;
    D3D11_RECT r;
    u32 x0 = g_pvr.clip[0], y0 = g_pvr.clip[1], x1 = g_pvr.clip[2] + 1u, y1 = g_pvr.clip[3] + 1u;
    int full = x0 == 0u && x1 >= 640u;
    if ((full && y0 == 0u && y1 >= 480u) ||
        FAILED(ID3D11DeviceContext_QueryInterface(g_gfx.ctx, &IID_ID3D11DeviceContext1, (void **)&c1))) {
        ID3D11DeviceContext_ClearRenderTargetView(g_gfx.ctx, g_gfx.draw_rtv, bg);
        return;
    }
    r.left = draw_x(x0, full);
    r.right = draw_x(x1 < 640u ? x1 : 640u, full);
    r.top = (LONG)(y0 * g_gfx.draw_h / 480u);
    r.bottom = (LONG)((y1 < 480u ? y1 : 480u) * g_gfx.draw_h / 480u);
    ID3D11DeviceContext1_ClearView(c1, (ID3D11View *)g_gfx.draw_rtv, bg, &r, 1);
    ID3D11DeviceContext1_Release(c1);
}

static void draw_list(u32 pass, u32 list)
{
    const PvrScene *s = &g_scene;
    for (u32 i = s->range[pass][list][0]; i < s->range[pass][list][1]; i++) {
        const PvrBatch *b = &s->batch[i];
        ID3D11SamplerState *smp;
        if (!b->count || ((b->isp >> ISP_DEPTH_SHIFT) & 7u) == DEPTH_NEVER) continue;
        smp = pvr_sampler_state(b->tsp);
        ID3D11DeviceContext_PSSetShaderResources(g_gfx.ctx, 0, 1, &g_srv[i]);
        ID3D11DeviceContext_PSSetSamplers(g_gfx.ctx, 0, 1, &smp);
        ID3D11DeviceContext_OMSetBlendState(g_gfx.ctx, pvr_blend_state(list, b->tsp), NULL, 0xffffffffu);
        ID3D11DeviceContext_OMSetDepthStencilState(
            g_gfx.ctx, pvr_depth_state(list, g_pvr_debug & PVR_DBG_NODEPTH ? b->isp | 7u << ISP_DEPTH_SHIFT : b->isp),
            b->flags & PVR_BF_SHADOW ? STENCIL_SHADOW : 0u);
        pvr_scissor(b);
        ID3D11DeviceContext_Draw(g_gfx.ctx, b->count, b->first);
    }
}

/* The frame target, depth buffer and batch pipeline (again after an autosort resolve). */
static void bind_pipeline(void)
{
    ID3D11DeviceContext *cx = g_gfx.ctx;
    D3D11_VIEWPORT vp = {0.0f, 0.0f, (f32)g_gfx.draw_w, (f32)g_gfx.draw_h, 0.0f, 1.0f};
    UINT stride = sizeof(PvrVtx), offset = 0;
    ID3D11DeviceContext_OMSetRenderTargets(cx, 1, &g_gfx.draw_rtv, g_dsv);
    ID3D11DeviceContext_RSSetViewports(cx, 1, &vp);
    ID3D11DeviceContext_RSSetState(cx, pvr_raster_state());
    ID3D11DeviceContext_IASetInputLayout(cx, g_layout);
    ID3D11DeviceContext_IASetPrimitiveTopology(cx, D3D11_PRIMITIVE_TOPOLOGY_TRIANGLELIST);
    ID3D11DeviceContext_IASetVertexBuffers(cx, 0, 1, &g_vb, &stride, &offset);
    ID3D11DeviceContext_VSSetShader(cx, g_vs, NULL, 0);
    ID3D11DeviceContext_VSSetConstantBuffers(cx, 0, 1, &g_cb);
    ID3D11DeviceContext_PSSetShader(cx, g_ps, NULL, 0);
    ID3D11DeviceContext_PSSetConstantBuffers(cx, 0, 1, &g_cb);
    ID3D11DeviceContext_PSSetShaderResources(cx, 1, 1, &g_bb_srv);
}

static void draw_translucent(const PvrPass *pass, u32 p)
{
    const u32 *r = g_scene.range[p][PVRX_LIST_TRANSLUCENT];
    if (pass->autosort && r[1] > r[0] && pvr_oit_begin(g_dsv)) {
        draw_list(p, PVRX_LIST_TRANSLUCENT);
        pvr_oit_resolve();
        bind_pipeline();
    } else {
        draw_list(p, PVRX_LIST_TRANSLUCENT);
    }
}

static int draw_scene(const PvrPass *passes)
{
    ID3D11DeviceContext *cx = g_gfx.ctx;
    f32 bg[4], scale;
    if (!init_pipeline() || !ensure_depth() || !prepare_batches()) return 0;
    if (g_scene.nvtx && (!ensure_buffer(&g_vb, &g_vb_cap, g_scene.nvtx * (u32)sizeof(PvrVtx), D3D11_BIND_VERTEX_BUFFER, 0) ||
                         !upload(g_vb, g_scene.vtx, g_scene.nvtx * (u32)sizeof(PvrVtx))))
        return 0;
    scale = frame_consts();
    argb_float(g_pvr.bg_argb[0] | 0xff000000u, bg);
    clear_background(bg);
    bind_pipeline();
    for (u32 p = 0; p < g_scene.passes; p++) {
        if (p == 0 || passes[p].z_clear)
            ID3D11DeviceContext_ClearDepthStencilView(cx, g_dsv, D3D11_CLEAR_DEPTH | D3D11_CLEAR_STENCIL,
                                                      g_pvr.bg_xyz[0][2] * scale, 0);
        draw_list(p, PVRX_LIST_OPAQUE);
        draw_list(p, PVRX_LIST_PUNCH);
        if (g_scene.range[p][PVRX_LIST_OPAQUE_MOD][1] > g_scene.range[p][PVRX_LIST_OPAQUE_MOD][0]) {
            pvr_modvol_draw(&g_scene, p, g_cb);
            bind_pipeline();
        }
        if (pvr_modvol_trans_needed(&g_scene, p))
            pvr_todo("pvr: translucent modifier volumes");
        draw_translucent(&passes[p], p);
    }
    ID3D11DeviceContext_OMSetRenderTargets(cx, 0, NULL, NULL);
    return 1;
}

int pvr_render(const PvrPass *passes, u32 count)
{
    ta_parse(&g_scene, passes, count);
    if (!g_gfx.frame_rtv) return 0;
    if (!g_failed && !draw_scene(passes)) {
        port_log("pvr: the Direct3D pipeline could not be set up; frames stay the background colour\n");
        g_failed = 1;
    }
    if (g_failed) gfx_clear(g_pvr.bg_argb[0]);
    else gfx_resolve();
    pvr_keep_frame();
    return !g_failed;
}
