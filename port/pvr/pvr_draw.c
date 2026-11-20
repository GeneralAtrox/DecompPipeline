/* Draws a scene into the frame target, in the PowerVR's order per pass: the background (a clear to the plane's
 * colour and depth), opaque, punch-through, the opaque modifier volumes (pvr_modvol.c), translucent, and its
 * modifier volumes (pvr_modvol.c, on the autosort fragments).
 * Translucent polygons are sorted per pixel in a pass with autosort (pvr_oit.c), else drawn in submission order
 * (then the translucent modifier volumes are a to-do).
 *
 * One vertex buffer and one per-batch state buffer are uploaded per scene; each batch is one draw with its
 * texture, sampler, blend, depth state and scissor. */
#include <stdlib.h>
#include <string.h>
#include "pvr_int.h"
#include "platform.h"
#include "prof.h"
#include "play_stats.h"
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
    f32 wide[4]; /* x: 1 / port_widescreen(), y: 1 / port_tallscreen() */
} FrameConsts;

static PvrScene g_scenes[PVR_SLOTS], *g_sc = g_scenes; /* a scene per slot of the frame (pvr_blend_hold.c) */
static ID3D11VertexShader *g_vs;
static ID3D11PixelShader *g_ps;
static ID3D11InputLayout *g_layout;
static ID3D11Buffer *g_vb, *g_bb, *g_cb;
static ID3D11ShaderResourceView *g_bb_srv;
static u32 g_vb_cap, g_bb_cap;
static ID3D11Texture2D *g_depth;
static ID3D11DepthStencilView *g_dsv;
static u32 g_depth_w, g_depth_h;
static ID3D11ShaderResourceView **g_srv, **g_kept; /* kept: an in-between frame's slot's own (prepare_batches) */
static GpuBatch *g_gpu;
static u32 g_srv_cap;
static int g_failed, g_gpu_blend, g_vb_own; /* --interp-gpu: this frame blends on the GPU; g_vb holds the scene */
static const char *const k_timers[PVR_T_END - PVR_T_TEX] = {"pvr textures", "pvr batch and constant upload", "pvr vertex upload",
    "pvr clears", "pvr pipeline bind", "pvr batch states", "pvr draw calls", "pvr modifier volumes and autosort",
    "pvr wait for the graphics lock", "pvr ta log once"}; /* the PVR_T_* timers (pvr_int.h) */

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
    perf_kit_timers(k_timers, PVR_T_END - PVR_T_TEX);
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

/* Textures for every batch (decoded on first use), and the batch states the pixel shader reads. */
static int prepare_batches(void)
{
    PvrScene *s = g_sc;
    u32 n = s->nbatch ? s->nbatch : 1u;
    u64 t = perf_timer_begin();
    int ok;
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
        g_srv[i] = g_kept ? g_kept[i] : bt->flags & PVR_BF_TEXTURE ? pvr_tex_get(bt->tcw, bt->tsp) : NULL;
        g_gpu[i].isp = bt->isp;
        g_gpu[i].tsp = bt->tsp;
        g_gpu[i].tcw = bt->tcw;
        g_gpu[i].flags = g_srv[i] ? bt->flags : bt->flags & ~PVR_BF_TEXTURE;
    }
    pvr_wide_scene(s);
    perf_timer_end(PVR_T_TEX, t);
    t = perf_timer_begin();
    if (g_bb_srv && n * sizeof(GpuBatch) > g_bb_cap) {
        ID3D11ShaderResourceView_Release(g_bb_srv);
        g_bb_srv = NULL;
    }
    if (!pvr_buffer_ensure(&g_bb, &g_bb_cap, n * (u32)sizeof(GpuBatch), D3D11_BIND_SHADER_RESOURCE, sizeof(GpuBatch)))
        return 0;
    if (!g_bb_srv) {
        D3D11_SHADER_RESOURCE_VIEW_DESC v;
        memset(&v, 0, sizeof v);
        v.Format = DXGI_FORMAT_UNKNOWN;
        v.ViewDimension = D3D11_SRV_DIMENSION_BUFFER;
        v.Buffer.NumElements = g_bb_cap / (u32)sizeof(GpuBatch);
        if (FAILED(ID3D11Device_CreateShaderResourceView(g_gfx.dev, (ID3D11Resource *)g_bb, &v, &g_bb_srv))) return 0;
    }
    ok = pvr_buffer_upload(g_bb, g_gpu, n * (u32)sizeof(GpuBatch));
    perf_timer_end(PVR_T_UPLOAD, t);
    return ok;
}

static f32 frame_consts(void)
{
    FrameConsts c;
    u64 t;
    memset(&c, 0, sizeof c);
    c.depth_scale = pvr_depth_scale(g_sc->max_z, g_pvr_drawn->bg_xyz[0][2]);
    c.fog_density = pvr_fog_density(g_pvr_drawn->fog_density);
    c.punch_ref = PUNCH_REF;
    c.shadow_alpha = g_pvr_drawn->shadow < 0 ? 0.0f : 1.0f - (f32)g_pvr_drawn->shadow / 256.0f;
    pvr_argb_float(g_pvr_drawn->fog_color, c.fog_color);
    for (u32 i = 0; i < 128u; i++) c.fog_table[i] = g_pvr_debug & PVR_DBG_NOFOG ? 0u : g_pvr_drawn->fog_table[i];
    c.wide[0] = 1.0f / port_widescreen();
    c.wide[1] = 1.0f / port_tallscreen();
    t = perf_timer_begin();
    pvr_buffer_upload(g_cb, &c, sizeof c);
    perf_timer_end(PVR_T_UPLOAD, t);
    return c.depth_scale;
}

/* Fills the frame with the background colour; with a global clip smaller than the screen only inside it, so the rest
 * keeps what earlier scenes drew there, as the hardware's frame buffer does (split-screen views drawn on alternate
 * frames). In a wider or taller frame a scene that draws nothing past the screen (a menu's 2D art) has the border
 * colour around it (the plane there was hidden by the art); a scene drawing nothing fills the frame (a grey box). */
int g_pvr_framed; /* the scene drawn last left the bars around the screen to the border colour (gfx.h) */
static void clear_background(const f32 bg[4])
{
    ID3D11DeviceContext1 *c1;
    D3D11_RECT r;
    u32 x0 = g_pvr_drawn->clip[0], y0 = g_pvr_drawn->clip[1], x1 = g_pvr_drawn->clip[2] + 1u, y1 = g_pvr_drawn->clip[3] + 1u;
    int full = x0 == 0u && x1 >= 640u, fully = y0 == 0u && y1 >= 480u;
    int framed = g_pvr_framed = full && fully && !g_sc->beyond && g_sc->nvtx && (port_widescreen() > 1.0f || port_tallscreen() > 1.0f);
    if ((full && fully && !framed) ||
        FAILED(ID3D11DeviceContext_QueryInterface(g_gfx.ctx, &IID_ID3D11DeviceContext1, (void **)&c1))) {
        ID3D11DeviceContext_ClearRenderTargetView(g_gfx.ctx, g_gfx.draw_rtv, bg);
        return;
    }
    if (framed) {
        f32 border[4];
        pvr_argb_float(gfx_border() | 0xff000000u, border);
        ID3D11DeviceContext_ClearRenderTargetView(g_gfx.ctx, g_gfx.draw_rtv, border);
        full = fully = 0;
    }
    r.left = pvr_draw_x(x0, full);
    r.right = pvr_draw_x(x1 < 640u ? x1 : 640u, full);
    r.top = pvr_draw_y(y0, fully);
    r.bottom = pvr_draw_y(y1 < 480u ? y1 : 480u, fully);
    pvr_frame_share_clip(&r);
    ID3D11DeviceContext1_ClearView(c1, (ID3D11View *)g_gfx.draw_rtv, bg, &r, 1);
    ID3D11DeviceContext1_Release(c1);
}

static void draw_list(u32 pass, u32 list)
{
    const PvrScene *s = g_sc;
    for (u32 i = s->range[pass][list][0]; i < s->range[pass][list][1]; i++) {
        const PvrBatch *b = &s->batch[i];
        ID3D11SamplerState *smp;
        u64 t;
        if (!b->count || ((b->isp >> ISP_DEPTH_SHIFT) & 7u) == DEPTH_NEVER) continue;
        t = perf_timer_begin();
        smp = pvr_sampler_state(b->tsp, b->uv_out, pvr_tex_pack_mode(g_srv[i]));
        ID3D11DeviceContext_PSSetShaderResources(g_gfx.ctx, 0, 1, &g_srv[i]);
        ID3D11DeviceContext_PSSetSamplers(g_gfx.ctx, 0, 1, &smp);
        ID3D11DeviceContext_OMSetBlendState(g_gfx.ctx, pvr_blend_state(list, b->tsp), NULL, 0xffffffffu);
        ID3D11DeviceContext_OMSetDepthStencilState(
            g_gfx.ctx, pvr_depth_state(list, g_pvr_debug & PVR_DBG_NODEPTH ? b->isp | 7u << ISP_DEPTH_SHIFT : b->isp),
            b->flags & PVR_BF_SHADOW ? STENCIL_SHADOW : 0u);
        pvr_scissor(b);
        perf_timer_end(PVR_T_STATES, t);
        t = perf_timer_begin();
        ID3D11DeviceContext_Draw(g_gfx.ctx, b->count, b->first);
        perf_timer_end(PVR_T_DRAW, t);
    }
}

/* The frame target, depth buffer and batch pipeline (again after an autosort resolve). */
static void bind_pipeline(void)
{
    ID3D11DeviceContext *cx = g_gfx.ctx;
    D3D11_VIEWPORT vp = {0.0f, 0.0f, (f32)g_gfx.draw_w, (f32)g_gfx.draw_h, 0.0f, 1.0f};
    UINT stride = sizeof(PvrVtx), offset = 0;
    u64 t = perf_timer_begin();
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
    if (g_gpu_blend) pvr_blend_gpu_bind(cx, g_vb);
    perf_timer_end(PVR_T_BIND, t);
}

static void draw_translucent(const PvrPass *pass, u32 p)
{
    const u32 *r = g_sc->range[p][PVRX_LIST_TRANSLUCENT];
    if (pass->autosort && r[1] > r[0] && pvr_oit_begin(g_dsv)) {
        u64 t;
        draw_list(p, PVRX_LIST_TRANSLUCENT);
        t = perf_timer_begin();
        if (pvr_modvol_trans_needed(g_sc, p)) pvr_modvol_trans_draw(g_sc, p);
        pvr_oit_resolve();
        perf_timer_end(PVR_T_PASSES, t);
        bind_pipeline();
    } else {
        if (pvr_modvol_trans_needed(g_sc, p)) pvr_todo("pvr: translucent modifier volumes without autosort");
        draw_list(p, PVRX_LIST_TRANSLUCENT);
    }
}

static int draw_scene(const PvrPass *passes, int reuse)
{
    ID3D11DeviceContext *cx = g_gfx.ctx;
    f32 bg[4], scale;
    u64 t;
    if (!init_pipeline() || !ensure_depth() || ((!reuse || reuse == 3) && !prepare_batches())) return 0;
    t = perf_timer_begin();
    if (g_sc->nvtx && !(reuse == 2 && g_vb_own) && (!pvr_buffer_ensure(&g_vb, &g_vb_cap, g_sc->nvtx * (u32)sizeof(PvrVtx),
        D3D11_BIND_VERTEX_BUFFER, 0) || !pvr_buffer_upload(g_vb, g_sc->vtx, g_sc->nvtx * (u32)sizeof(PvrVtx))))
        return 0;
    perf_timer_end(PVR_T_VERTS, t);
    g_vb_own = !(reuse & 1); /* 2: the scene's own vertices, blended on the GPU; 3: a slot's, blended */
    scale = frame_consts();
    pvr_argb_float(g_pvr_drawn->bg_argb[0] | 0xff000000u, bg);
    t = perf_timer_begin();
    if (!g_pvr_keep) clear_background(bg); /* port_render_keep_frame: the frame as it is (pvr_frame_bars) */
    perf_timer_end(PVR_T_CLEAR, t);
    bind_pipeline();
    for (u32 p = 0; p < g_sc->passes; p++) {
        t = perf_timer_begin();
        if (p == 0 || passes[p].z_clear)
            ID3D11DeviceContext_ClearDepthStencilView(cx, g_dsv, D3D11_CLEAR_DEPTH | D3D11_CLEAR_STENCIL,
                                                      g_pvr_drawn->bg_xyz[0][2] * scale > 1.0f ? 1.0f : g_pvr_drawn->bg_xyz[0][2] * scale, 0);
        perf_timer_end(PVR_T_CLEAR, t);
        draw_list(p, PVRX_LIST_OPAQUE);
        draw_list(p, PVRX_LIST_PUNCH);
        if (g_sc->range[p][PVRX_LIST_OPAQUE_MOD][1] > g_sc->range[p][PVRX_LIST_OPAQUE_MOD][0]) {
            t = perf_timer_begin();
            pvr_modvol_draw(g_sc, p, g_cb);
            perf_timer_end(PVR_T_PASSES, t);
            bind_pipeline();
        }
        draw_translucent(&passes[p], p);
    }
    ID3D11DeviceContext_OMSetRenderTargets(cx, 0, NULL, NULL);
    return 1;
}

const PvrState *g_pvr_drawn = &g_pvr;
int pvr_render(const PvrPass *passes, u32 count)
{
    u64 pt = prof_begin(PROF_D3D);
    int early = pvr_slot_alone() && pvr_blend_early_parse(g_scenes, passes, count); /* --interp-early: parsed, paired */
    u64 lt = perf_timer_begin();
    gfx_lock();
    perf_timer_end(PVR_T_LOCK, lt);
    pvr_slot_begin(), g_sc = &g_scenes[pvr_slot()]; /* a split screen's view: its own slot */
    port_extra_thread_hold(); /* no in-between frame until this one has been counted (--present-thread) */
    play_stats_draw(1);
    pvr_hold_reset(), pvr_take_keep();
    if (!early && extra_scene()) pvr_blend_keep(g_sc); /* the scene before this one (--interp-scene) */
    early ? pvr_blend_early_take(g_sc) : ta_parse(g_sc, passes, count);
    if (!g_gfx.frame_rtv) {
        prof_end(pt);
        play_stats_draw(0);
        gfx_unlock();
        return 0;
    }
    prof_gpu_begin();
    if (!g_failed && !draw_scene(passes, 0)) {
        port_log("pvr: the Direct3D pipeline could not be set up; frames stay the background colour\n");
        g_failed = 1;
    }
    if (g_failed) gfx_clear(g_pvr.bg_argb[0]);
    else gfx_resolve();
    if (extra_scene() && !g_failed) pvr_retain(g_srv, g_sc->nbatch, passes, count);
    pvr_keep_frame(), pvr_frame_bars();
    prof_gpu_end();
    prof_end(pt);
    play_stats_draw(0);
    if (!pvr_slot_partial()) port_extra_thread_rendered();
    gfx_unlock();
    return !g_failed;
}

/* One slot of an in-between frame: its scene blended (pvr_blend.c), with the textures and registers it was drawn with
 * (the game may be writing g_pvr: --present-thread). Of several, each puts its batch states up again, blended by the CPU */
static int draw_slot(u32 slot, f32 alpha, int multi)
{
    PvrScene *s = g_sc = &g_scenes[slot];
    PvrVtx *own = s->vtx;
    ID3D11ShaderResourceView **srv = g_srv, **kept;
    const PvrPass *passes;
    const PvrVtx *mixed;
    f32 top = s->max_z;
    u32 n;
    int ok;
    g_pvr_drawn = pvr_retained(slot, &kept, &n, &passes, &g_pvr_keep);
    if (n != s->nbatch) return 0;
    *(multi ? &g_kept : &g_srv) = kept;
    g_gpu_blend = !multi && pvr_blend_gpu_ready(s, alpha); /* --interp-gpu */
    mixed = g_gpu_blend ? NULL : pvr_blend_vertices(s, alpha, NULL);
    if (mixed) s->vtx = (PvrVtx *)mixed;
    for (u32 i = 0; mixed && i < s->nvtx; i++) /* a vertex the tick before had nearer (a floor the eye was over): */
        if (mixed[i].z > s->max_z && mixed[i].z <= top * 4.0f) s->max_z = mixed[i].z; /* in the depth range, not clipped */
    ok = draw_scene(passes, multi ? 3 : g_gpu_blend ? 2 : 1);
    s->vtx = own, s->max_z = top, g_srv = srv, g_kept = NULL, g_gpu_blend = 0;
    return ok;
}

/* An in-between frame (--interp-scene): every slot of the frame drawn last, each blended */
int pvr_blend_draw(f32 alpha)
{
    PvrScene *cur = g_sc;
    u32 slots = pvr_slots();
    int keep = g_pvr_keep, ok = 1;
    u64 pt;
    if (!g_gfx.frame_rtv || g_failed || slots > PVR_SLOTS) return 0;
    pt = prof_begin(PROF_D3D);
    pvr_hold_begin();
    for (u32 i = 0; ok && i < slots; i++) ok = draw_slot(i, alpha, slots > 1u);
    g_sc = cur, g_pvr_drawn = &g_pvr, g_pvr_keep = keep;
    prof_end(pt);
    if (ok) { gfx_resolve(); if (g_pvr_framed && gfx_edges_on()) gfx_carry_edges(); }
    if (ok) gfx_capture_extra(), gfx_show();
    return ok != 0;
}

/* Whether an in-between frame can be drawn now: the frame is whole, there is an old scene (of several slots, each blends
 * by its parts), its first render cleared the frame, and no movie overlay (drawn when a frame is counted) is on. */
int pvr_blend_ready(void)
{
    u32 n = pvr_slots();
    int ok = !g_failed && !pvr_slot_partial() && n <= PVR_SLOTS && pvr_blend_have() && !gfx_movie_on();
    for (u32 i = 0; ok && n > 1u && i < n; i++) ok = g_scenes[i].has_from;
    return ok && (n > 1u ? !pvr_slot_keeps(0u) : !g_pvr_keep);
}
