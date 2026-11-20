/* The host side of the picture: what the game's Direct3D 9 calls become on the port base's Direct3D 11 device. There is no
 * command stream and no hardware model: xb_d3d.c turns each entry the game calls into a call here with the values already read
 * from guest memory.
 *
 * The base owns the window, the device, the swap chain and the frame target (base/present.h). The game's targets and full-screen
 * textures are made at the output's scale when the first one is made (a later change of window size scales the picture), and
 * the game's frame is shown in the frame target at its own shape, never stretched. */
#define COBJMACROS
#include <windows.h>
#include <d3d11_1.h>
#include <d3dcompiler.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include "xb.h"
#include "xb_game.h"
#include "console.h"
#include "xb_xenos_colour.h"
#include "present.h"

#pragma comment(lib, "d3dcompiler.lib")
#pragma comment(lib, "dxguid.lib")   /* IID_ID3D11DeviceContext1, for ClearView */

struct XbHostTex {
    ID3D11Texture2D *tex;
    ID3D11ShaderResourceView *srv;
    ID3D11RenderTargetView *rtv;
    ID3D11DepthStencilView *dsv;
    unsigned w, h;
    int format, depth;
    unsigned remade;
    int cube;
    struct XbHostTex *alt;   /* the header's host texture at another shape it has described (xb_gpu_tex) */
};

typedef struct {
    u32 addr;
    ID3D11VertexShader *vs;
    ID3D11PixelShader *ps;
    ID3D11GeometryShader *gs;
    ID3DBlob *vs_code;              /* the vertex shader compiled here, when the build gave no bytecode */
    const void *vs_bytes;           /* the vertex shader's bytecode, for its input layouts: the build's or vs_code's */
    SIZE_T vs_len;
    ID3D11InputLayout *layout;
    unsigned layout_n;
} HostShader;
extern const unsigned char xb_shader_dxbc[];   /* gen_xbox.py's shader_dxbc.obj: every converted shader compiled at build time */
static void shaders_precreate_start(void);

static ID3D11Device *s_dev;
static XbHostTex *depth_tex(void);
static void depth_srv(XbHostTex *t);
static ID3D11DeviceContext *s_ctx;
static DWORD s_gfx_thread;   /* the thread the window belongs to: the one that draws */
static unsigned s_game_w, s_game_h, s_tgt_w, s_tgt_h;
static float s_scale;
static int s_ready;
static ID3D11Buffer *s_kit_cb;                      /* b2 in every converted shader: viewport off, alpha test */
static ID3D11Buffer *s_cb[2];                      /* vertex and pixel float constants, 256 float4 each */
static float s_const[2][256 * 4];
static ID3D11SamplerState *s_sampler;
static ID3D11RasterizerState *s_raster;
static ID3D11BlendState *s_blend;
static ID3D11DepthStencilState *s_depth_off;
static ID3D11VertexShader *s_zq_vs;              /* XB_DEPTH=1: the depth-only quad of a partial depth/stencil clear */
static ID3D11RasterizerState *s_raster_scissor;
static ID3D11DepthStencilState *s_zclear[4];      /* by clear flags >> 8: depth, stencil, both */
static HostShader *s_shader;             /* one for each row of xb_shader_table, made before play (shaders_precreate_start) or at its first draw */
static volatile LONG *s_shader_state;    /* each row's: 0 not made, 1 being made, 2 ready */
static unsigned s_shader_n;
static u32 s_vs_addr, s_ps_addr;
static int s_target_bias[4];   /* each slot's RB_COLOR_INFO exponent bias (xb_gpu_target_bias) */
static int s_target_fmt[4] = {-1, -1, -1, -1};   /* each slot's guest colour format (xb_gpu_target_format), -1 unknown */
static XbHostTex *s_target[4], *s_depth, *s_tex[16];
static u32 s_tex_guest[16];
static struct { float x, y, w, h, zmin, zmax; } s_vp;
static ID3D11VertexShader *s_blit_vs;
static ID3D11PixelShader *s_blit_ps;
/* The display's gamma ramp (xb_gpu_gamma_ramp, xb_xenos_gamma.h): present goes through s_ramp_ps with the ramp in b4 while one is set */
static ID3D11PixelShader *s_ramp_ps;
static ID3D11Buffer *s_ramp_cb;
static float s_ramp[256][4];
static int s_ramp_on, s_ramp_dirty;
static u32 s_ramp_sum;
static unsigned s_frames, s_draws, s_draws0, s_draws_last, s_draws_nothing, s_up_verts;   /* s_up_verts: vertices sent to the host GPU in new buffers */   /* s_draws0: s_draws when the last frame was shown; s_draws_last: the draws of that frame */

#include "xb_gpu_window.h"   /* the window kept answering while its thread waits */
static void fcap_clear(const char *kind, u32 flags, const float *rgba, float z, u32 stencil, unsigned rects);   /* xb_gpu_fcap.h */
static void fcap_resolve(XbHostTex *src, XbHostTex *dst, int exp_bias);
static void fcap_present(XbHostTex *src);
static void fcap_draw_data(unsigned nverts, unsigned nattr, const float *vdata);   /* XB_FCAP_DRAWS */
static void fcap_draw_after(void);

static void gpu_fail(const char *what, HRESULT hr)
{
    char d[96];
    snprintf(d, sizeof d, "%s failed, HRESULT %08lx", what, (unsigned long)hr);
    xb_stop("d3d_host", d, (u32)hr);
}

static ID3DBlob *compile(const char *src, const char *profile, const char *what)
{
    ID3DBlob *code = 0, *err = 0;
    HRESULT hr = D3DCompile(src, strlen(src), what, 0, 0, "main", profile, 0, 0, &code, &err);
    if (FAILED(hr)) {
        port_log("d3d host: %s did not compile: %s\n", what, err ? (const char *)ID3D10Blob_GetBufferPointer(err) : "(no message)");
        gpu_fail(what, hr);
    }
    if (err) ID3D10Blob_Release(err);
    return code;
}

static const char *const blit_src =
    "Texture2D t : register(t0); SamplerState s : register(s0);\n"
    "struct O { float4 p : SV_Position; float2 uv : TEXCOORD0; };\n"
    "O vs(uint i : SV_VertexID) { O o; float2 uv = float2((i << 1) & 2, i & 2); o.uv = uv; o.p = float4(uv * float2(2, -2) + float2(-1, 1), 0, 1); return o; }\n"
    "float4 ps(O i) : SV_Target { return float4(t.Sample(s, i.uv).rgb, 1); }\n"
    "float4 flat(float4 p : SV_Position) : SV_Target { return float4(1, 0, 1, 1); }\n"
    "cbuffer Scale : register(b3) { float4 k; };\n"
    "float4 scale(float4 p : SV_Position) : SV_Target { float4 c = t.Load(int3(p.xy, 0)); if (k.y > 0) c = float4(clamp(c.rgb, 0, k.y), saturate(c.a)); return c * k.x; }\n"
    "float4 zcopy(float4 p : SV_Position) : SV_Target { float z = t.Load(int3(p.xy, 0)).r; z = k.z > 0 ? 1 - z : z; return float4(z, z, z, z); }\n"
    "cbuffer Ramp : register(b4) { float4 rr[256]; };\n"
    "float ramp1(float c, uint ch) { uint x = (uint)(saturate(c) * 1023.0 + 0.5); float4 a = rr[(x >> 3) * 2 + (ch == 2 ? 1 : 0)]; float2 e = ch == 1 ? a.zw : a.xy;"
    " return (e.x + floor(e.y * (float)(x & 7u) / 8.0)) / 1023.0; }\n"
    "float4 ramp(O i) : SV_Target { float3 c = t.Sample(s, i.uv).rgb; return float4(ramp1(c.r, 0), ramp1(c.g, 1), ramp1(c.b, 2), 1); }\n"
    "float4 zq(uint i : SV_VertexID) : SV_Position { float2 uv = float2((i << 1) & 2, i & 2); return float4(uv * float2(2, -2) + float2(-1, 1), k.x, 1); }\n";
/* XB_FLAT_PS=1, a diagnostic: every game draw writes magenta with no blend, to tell geometry that lands nowhere from pixel shaders that write black */
static ID3D11PixelShader *s_flat_ps;
/* a resolve with an exponent bias: the copy, each texel times k.x; with k.y set (XB_COGBOX_7E3=1, xb_xenos_colour.h) the source is first clamped to
 * what a 7e3 target holds, RGB 0..k.y and alpha 0..1 (xb_gpu_draw.h xb_gpu_resolve) */
static ID3D11PixelShader *s_scale_ps;
static ID3D11PixelShader *s_zcopy_ps;   /* XB_DEPTH_RESOLVE: a resolve of the depth surface, the depth in every channel (xb_gpu_resolve) */
static ID3D11Buffer *s_scale_cb;

/* The base's window and device, and the kit's own state on them, made when the game first needs a picture. */
static void gpu_init(void)
{
    D3D11_BUFFER_DESC bd;
    D3D11_SAMPLER_DESC smp;
    D3D11_RASTERIZER_DESC rs;
    D3D11_BLEND_DESC bl;
    D3D11_DEPTH_STENCIL_DESC ds;
    HRESULT hr;
    unsigned k;

    if (s_ready) return;
    if (!gfx_init()) xb_stop("d3d_host", "the base's window and device did not open", 0);
    s_gfx_thread = GetCurrentThreadId();
    s_dev = g_gfx.dev;
    s_ctx = g_gfx.ctx;
    s_game_w = port_console.native_w;
    s_game_h = port_console.native_h;
    s_scale = (float)g_gfx.out_w / s_game_w;
    if ((float)g_gfx.out_h / s_game_h < s_scale) s_scale = (float)g_gfx.out_h / s_game_h;
    s_tgt_w = (unsigned)(s_game_w * s_scale + 0.5f);
    s_tgt_h = (unsigned)(s_game_h * s_scale + 0.5f);
    port_log("d3d host: output %ux%u, game targets scaled %.3f to %ux%u\n", g_gfx.out_w, g_gfx.out_h, s_scale, s_tgt_w, s_tgt_h);

    for (k = 0; k < 2; k++) {
        memset(&bd, 0, sizeof bd);
        bd.ByteWidth = 256 * 16;
        bd.Usage = D3D11_USAGE_DYNAMIC;
        bd.BindFlags = D3D11_BIND_CONSTANT_BUFFER;
        bd.CPUAccessFlags = D3D11_CPU_ACCESS_WRITE;
        ID3D11Device_CreateBuffer(s_dev, &bd, 0, &s_cb[k]);
    }
    bd.ByteWidth = 48;   /* xb_vte, xb_at, xb_gam (the gamma channels of fetch slots 0..7 and 8..15, 4 bits a slot) */
    ID3D11Device_CreateBuffer(s_dev, &bd, 0, &s_kit_cb);
    bd.ByteWidth = sizeof s_ramp;
    ID3D11Device_CreateBuffer(s_dev, &bd, 0, &s_ramp_cb);
    bd.ByteWidth = 16;
    ID3D11Device_CreateBuffer(s_dev, &bd, 0, &s_scale_cb);
    memset(&smp, 0, sizeof smp);
    smp.Filter = D3D11_FILTER_MIN_MAG_MIP_LINEAR;
    smp.AddressU = smp.AddressV = smp.AddressW = D3D11_TEXTURE_ADDRESS_CLAMP;
    smp.MaxLOD = D3D11_FLOAT32_MAX;
    ID3D11Device_CreateSamplerState(s_dev, &smp, &s_sampler);
    memset(&rs, 0, sizeof rs);
    rs.FillMode = D3D11_FILL_SOLID;
    rs.CullMode = D3D11_CULL_NONE;
    rs.DepthClipEnable = TRUE;
    rs.ScissorEnable = FALSE;
    ID3D11Device_CreateRasterizerState(s_dev, &rs, &s_raster);
    memset(&bl, 0, sizeof bl);
    bl.RenderTarget[0].RenderTargetWriteMask = D3D11_COLOR_WRITE_ENABLE_ALL;
    ID3D11Device_CreateBlendState(s_dev, &bl, &s_blend);
    memset(&ds, 0, sizeof ds);
    ID3D11Device_CreateDepthStencilState(s_dev, &ds, &s_depth_off);

    {
        /* both entry points live in one source: compile each by name */
        ID3DBlob *code = 0, *err = 0;
        hr = D3DCompile(blit_src, strlen(blit_src), "blit", 0, 0, "vs", "vs_5_0", 0, 0, &code, &err);
        if (FAILED(hr)) gpu_fail("blit vs", hr);
        ID3D11Device_CreateVertexShader(s_dev, ID3D10Blob_GetBufferPointer(code), ID3D10Blob_GetBufferSize(code), 0, &s_blit_vs);
        ID3D10Blob_Release(code);
        hr = D3DCompile(blit_src, strlen(blit_src), "blit", 0, 0, "ps", "ps_5_0", 0, 0, &code, &err);
        if (FAILED(hr)) gpu_fail("blit ps", hr);
        ID3D11Device_CreatePixelShader(s_dev, ID3D10Blob_GetBufferPointer(code), ID3D10Blob_GetBufferSize(code), 0, &s_blit_ps);
        ID3D10Blob_Release(code);
        hr = D3DCompile(blit_src, strlen(blit_src), "blit", 0, 0, "ramp", "ps_5_0", 0, 0, &code, &err);
        if (FAILED(hr)) gpu_fail("gamma ramp ps", hr);
        ID3D11Device_CreatePixelShader(s_dev, ID3D10Blob_GetBufferPointer(code), ID3D10Blob_GetBufferSize(code), 0, &s_ramp_ps);
        ID3D10Blob_Release(code);
        hr = D3DCompile(blit_src, strlen(blit_src), "blit", 0, 0, "scale", "ps_5_0", 0, 0, &code, &err);
        if (FAILED(hr)) gpu_fail("scaled resolve ps", hr);
        ID3D11Device_CreatePixelShader(s_dev, ID3D10Blob_GetBufferPointer(code), ID3D10Blob_GetBufferSize(code), 0, &s_scale_ps);
        ID3D10Blob_Release(code);
        hr = D3DCompile(blit_src, strlen(blit_src), "blit", 0, 0, "zcopy", "ps_5_0", 0, 0, &code, &err);
        if (FAILED(hr)) gpu_fail("depth resolve ps", hr);
        ID3D11Device_CreatePixelShader(s_dev, ID3D10Blob_GetBufferPointer(code), ID3D10Blob_GetBufferSize(code), 0, &s_zcopy_ps);
        ID3D10Blob_Release(code);
        if (xb_depth_on()) {   /* XB_DEPTH=1: the partial depth/stencil clear's quad (depth_rects), drawn under a scissor rectangle */
            hr = D3DCompile(blit_src, strlen(blit_src), "blit", 0, 0, "zq", "vs_5_0", 0, 0, &code, &err);
            if (FAILED(hr)) gpu_fail("depth clear vs", hr);
            ID3D11Device_CreateVertexShader(s_dev, ID3D10Blob_GetBufferPointer(code), ID3D10Blob_GetBufferSize(code), 0, &s_zq_vs);
            ID3D10Blob_Release(code);
            rs.ScissorEnable = TRUE;
            ID3D11Device_CreateRasterizerState(s_dev, &rs, &s_raster_scissor);
            port_log("d3d depth on (XB_DEPTH=0 turns it off): the game's render states 40, 44 and 48 (z enable, compare, z write) drive the host's depth test\n");
        }
        if (getenv("XB_FLAT_PS") && SUCCEEDED(D3DCompile(blit_src, strlen(blit_src), "blit", 0, 0, "flat", "ps_5_0", 0, 0, &code, &err))) {
            ID3D11Device_CreatePixelShader(s_dev, ID3D10Blob_GetBufferPointer(code), ID3D10Blob_GetBufferSize(code), 0, &s_flat_ps);
            ID3D10Blob_Release(code);
        }
    }
    s_ready = 1;
    shaders_precreate_start();
}

/* A host texture for a guest header the game made: full-screen sized ones follow the output's scale. A header that now describes another
 * size gets a new host texture: Too Human makes a 650x650 render target at 200aec90 at start-up, and later its movie and quote card frames
 * are resolved into a 1280x720 texture header at the same address, which Swap shows. Kept at 650x650, the frame was cut to its top-left
 * corner and stretched over the window */
XbHostTex *xb_gpu_tex(u32 guest, unsigned w, unsigned h, int format, int target, int depth)
{
    XbHostTex *t;
    D3D11_TEXTURE2D_DESC d;
    HRESULT hr;
    unsigned gw = w, gh = h;
    gpu_init();
    t = (XbHostTex *)xb_host_object(guest, sizeof *t);
    if (target == 2 || depth) {   /* a surface (target 2, or depth): what the GPU draws into, on the console memory no draw samples, so only its size
                                   * as a limit matters, and the host makes it at least the frame's size. Too Human's scene surface 400febc0 reads as
                                   * 640x736 and the game draws a 1280x720 viewport into it; made at its own size it held a quarter of the scene, and a
                                   * depth surface must match its colour surface's size on the host. Textures (resolve destinations) keep their size */
        if (w < s_game_w) w = s_game_w;
        if (h < s_game_h) h = s_game_h;
    }
    if (w == s_game_w) {   /* a full-width surface follows the output scale. The game draws its frame in two tiles, 821d00a8 BeginTiling to 821d0708 EndTiling, on a target of one tile's
                            * height (1280x384, first made by 82093cf0): the host has no tiles and the target is the whole frame */
        if (h < s_game_h) h = s_game_h;
        h = (unsigned)(h * s_scale + 0.5f);
        w = s_tgt_w;
    } else {   /* every other target follows the same scale: bind_targets scales every viewport, and Too Human's post passes draw into 320x180 and
                * 162x162 targets with viewports their own size. Kept at the game's size, the picture came out in their top-left quarter (build box A
                * headless at scale 0.5, 2026-11-06) */
        w = (unsigned)(w * s_scale + 0.5f);
        h = (unsigned)(h * s_scale + 0.5f);
        if (!w) w = 1;
        if (!h) h = 1;
    }
    if (t->tex) {   /* up to four shapes a header keeps a host texture each: Too Human's 200aec90 is a 650x650 target and the 1280x720 frame Swap shows in turn,
                     * every frame, and making one again emptied the frame it held (2026-11-06: every saved menu frame black) */
        XbHostTex *a;
        unsigned n = 0;
        for (a = t; a; a = a->alt, n++)
            if (a->tex && a->w == w && a->h == h && a->format == format && a->depth == depth && (depth || !target || a->rtv)) return a;
        if (t->remade++ < 8) port_log("d3d header %08x is now %ux%u%s: %s (%ux%u)\n", guest, gw, gh, depth ? ", depth" : "", n < 4 ? "a host texture beside its first" : "its host texture is made again", t->w, t->h);
        if (n < 4) {
            a = (XbHostTex *)calloc(1, sizeof *a);
            if (!a) xb_stop("d3d_host", "no memory for another host texture", guest);
            a->alt = t->alt, t->alt = a, t = a;
        } else {
            if (t->srv) ID3D11ShaderResourceView_Release(t->srv);
            if (t->rtv) ID3D11RenderTargetView_Release(t->rtv);
            if (t->dsv) ID3D11DepthStencilView_Release(t->dsv);
            ID3D11Texture2D_Release(t->tex);
            t->tex = 0, t->srv = 0, t->rtv = 0, t->dsv = 0;
        }
    }
    t->format = format, t->depth = depth;
    memset(&d, 0, sizeof d);
    d.Width = t->w = w;
    d.Height = t->h = h;
    d.MipLevels = 1;
    d.ArraySize = 1;
    d.Format = depth ? DXGI_FORMAT_R24G8_TYPELESS : (DXGI_FORMAT)format;
    d.SampleDesc.Count = 1;
    d.Usage = D3D11_USAGE_DEFAULT;
    d.BindFlags = (depth ? D3D11_BIND_DEPTH_STENCIL | D3D11_BIND_SHADER_RESOURCE : D3D11_BIND_SHADER_RESOURCE) | (target ? D3D11_BIND_RENDER_TARGET : 0);
    hr = ID3D11Device_CreateTexture2D(s_dev, &d, 0, &t->tex);
    if (FAILED(hr)) gpu_fail("CreateTexture2D", hr);
    if (depth) {
        D3D11_DEPTH_STENCIL_VIEW_DESC dv;
        memset(&dv, 0, sizeof dv);
        dv.Format = DXGI_FORMAT_D24_UNORM_S8_UINT;
        dv.ViewDimension = D3D11_DSV_DIMENSION_TEXTURE2D;
        ID3D11Device_CreateDepthStencilView(s_dev, (ID3D11Resource *)t->tex, &dv, &t->dsv);
        depth_srv(t);
    } else {
        ID3D11Device_CreateShaderResourceView(s_dev, (ID3D11Resource *)t->tex, 0, &t->srv);
        if (target) ID3D11Device_CreateRenderTargetView(s_dev, (ID3D11Resource *)t->tex, 0, &t->rtv);
    }
    return t;
}

/* A host texture at the size the game made it, filled from memory with xb_gpu_upload: art, which does not follow the output's scale. */
XbHostTex *xb_gpu_tex_plain(u32 guest, unsigned w, unsigned h)
{
    XbHostTex *t;
    D3D11_TEXTURE2D_DESC d;
    HRESULT hr;
    gpu_init();
    t = (XbHostTex *)xb_host_object(guest, sizeof *t);
    if (t->tex) return t;
    memset(&d, 0, sizeof d);
    d.Width = t->w = w;
    d.Height = t->h = h;
    d.MipLevels = 1;
    d.ArraySize = 1;
    d.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
    d.SampleDesc.Count = 1;
    d.Usage = D3D11_USAGE_DEFAULT;
    d.BindFlags = D3D11_BIND_SHADER_RESOURCE;
    hr = ID3D11Device_CreateTexture2D(s_dev, &d, 0, &t->tex);
    if (FAILED(hr)) gpu_fail("CreateTexture2D", hr);
    ID3D11Device_CreateShaderResourceView(s_dev, (ID3D11Resource *)t->tex, 0, &t->srv);
    return t;
}

XbHostTex *xb_gpu_tex_cube(u32 guest, unsigned size)
{
    XbHostTex *t;
    D3D11_TEXTURE2D_DESC d;
    D3D11_SHADER_RESOURCE_VIEW_DESC v;
    HRESULT hr;
    gpu_init();
    t = (XbHostTex *)xb_host_object(guest, sizeof *t);
    if (t->tex) return t;
    memset(&d, 0, sizeof d);
    d.Width = d.Height = t->w = t->h = size;
    d.MipLevels = 1;
    d.ArraySize = 6;
    d.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
    d.SampleDesc.Count = 1;
    d.Usage = D3D11_USAGE_DEFAULT;
    d.BindFlags = D3D11_BIND_SHADER_RESOURCE;
    d.MiscFlags = D3D11_RESOURCE_MISC_TEXTURECUBE;
    hr = ID3D11Device_CreateTexture2D(s_dev, &d, 0, &t->tex);
    if (FAILED(hr)) gpu_fail("CreateTexture2D (cube)", hr);
    memset(&v, 0, sizeof v);
    v.Format = d.Format;
    v.ViewDimension = D3D11_SRV_DIMENSION_TEXTURECUBE;
    v.TextureCube.MipLevels = 1;
    ID3D11Device_CreateShaderResourceView(s_dev, (ID3D11Resource *)t->tex, &v, &t->srv);
    t->cube = 1;
    return t;
}

void xb_gpu_upload_face(XbHostTex *t, unsigned face, const void *rgba, unsigned pitch)
{
    ID3D11DeviceContext_UpdateSubresource(s_ctx, (ID3D11Resource *)t->tex, face, 0, rgba, pitch, 0);   /* one level: subresource = face */
}

/* A depth texture's shader view, its 24 depth bits as a 0..1 value: what a resolve of the depth surface copies (XB_DEPTH_RESOLVE) */
static void depth_srv(XbHostTex *t)
{
    D3D11_SHADER_RESOURCE_VIEW_DESC sv;
    if (!xb_depth_resolve_on()) return;   /* off: depth textures stay without a view, as before */
    memset(&sv, 0, sizeof sv);
    sv.Format = DXGI_FORMAT_R24_UNORM_X8_TYPELESS;
    sv.ViewDimension = D3D11_SRV_DIMENSION_TEXTURE2D;
    sv.Texture2D.MipLevels = 1;
    if (FAILED(ID3D11Device_CreateShaderResourceView(s_dev, (ID3D11Resource *)t->tex, &sv, &t->srv))) t->srv = 0;
}

/* XB_DEPTH_RESOLVE (off by default: =1 opts in. On by default for one build on 2026-11-15, it whitened the intro and title and the menu replays stayed on the title; a copy of 1 - z (XB_DEPTH_RESOLVE_FLIP=1) matched the reference at intro 994 but not 1463; see xb_d3d_draw.h's Resolve and DESIGN_RESOLVE.md): a Resolve of the depth surface copies the host's depth into the destination's
 * host texture, the depth in every channel, as the console's resolve puts the depth surface in memory for a k_24_8_FLOAT fetch. Off, that
 * resolve is skipped as before and the texture sampled there keeps whatever the host held for its memory */
int xb_depth_resolve_on(void)
{
    static int on = -1;
    if (on < 0) { const char *e = getenv("XB_DEPTH_RESOLVE"); on = e && e[0] == '1'; }
    return on && xb_depth_on();
}

/* The depth texture the draws use now (at render target 0's size), or 0 */
XbHostTex *xb_gpu_depth_now(void)
{
    gpu_init();
    return xb_depth_on() ? depth_tex() : (s_depth && s_depth->dsv ? s_depth : 0);
}

/* Depth on (the default since 2026-11-07; "XB_DEPTH=1" in the comments means this): depth testing and writing as the game's render states ask
 * (xb_gpu_draw.h depth_for_draw, comparisons mirrored under an upside-down viewport depth range) and their cull mode (raster_for_draw), draws with no
 * pixel shader made as depth-only passes with colour masked, the depth surface bound at its target's size and partial depth/stencil clears drawn.
 * XB_DEPTH=0: every draw with depth off and no culling, as before 2026-11-07 */
/* XB_DRAW_FAST=0 (a comparison switch) turns off the draw path's shortcuts: a draw that can change nothing left before its vertices are read
 * (xb_gpu_draw_nothing), an indexed draw's vertices gathered only when they must be converted (xb_d3d_vtx.h s_lazy) and the host's vertex buffer kept
 * with the converted vertices (xb_gpu_draw_kept). Too Human's main menu draws about 870 times a frame with 1.1 million vertices, a third of the draws
 * with no pixel shader and z off; each frame read, hashed and uploaded all of them again (build box A, 2026-11-09: the render thread's whole second) */
int xb_draw_fast(void)
{
    static int on = -1;
    if (on < 0) { const char *e = getenv("XB_DRAW_FAST"); on = !(e && e[0] == '0'); }
    return on;
}

int xb_depth_on(void)
{
    static int on = -1, alt;
    if (on < 0) {
        const char *e = getenv("XB_DEPTH"), *a = getenv("XB_DEPTH_ALT");
        on = !(e && e[0] == '0');
        alt = a ? atoi(a) : 0;
    }
    /* XB_DEPTH_ALT=n, a diagnostic: depth on for n frames, off for the next n, and so on, so one run sets the same moments side by side with the
     * switch off and on (a real-clock run never repeats its frames) */
    return on && (alt <= 0 || !((xb_frame_number() / (unsigned)alt) & 1u));
}

/* XB_DEPTH_LOG=n, a diagnostic: every draw and clear of frames n and n+5 in the log with its depth state (with XB_DEPTH_ALT=5 one frame with
 * depth on and one with it off) */
static int zlog_frame(void)
{
    static int at = -2;
    unsigned f;
    if (at == -2) { const char *e = getenv("XB_DEPTH_LOG"); at = e ? atoi(e) : -1; }
    if (at < 0) return 0;
    f = xb_frame_number();
    return f == (unsigned)at || f == (unsigned)at + 5u;
}

int xb_cube_on(void)
{
    static int on = -1;
    if (on < 0) { const char *e = getenv("XB_CUBE"); on = !(e && e[0] == '0'); }
    return on;
}

void xb_gpu_upload(XbHostTex *t, const void *rgba, unsigned pitch)
{
    ID3D11DeviceContext_UpdateSubresource(s_ctx, (ID3D11Resource *)t->tex, 0, 0, rgba, pitch, 0);
}

unsigned xb_gpu_tex_w(const XbHostTex *t) { return t->w; }
unsigned xb_gpu_tex_h(const XbHostTex *t) { return t->h; }
unsigned xb_gpu_scaled_w(unsigned w) { return w == s_game_w ? s_tgt_w : (unsigned)(w * s_scale + 0.5f); }

void xb_gpu_bind_target(unsigned i, XbHostTex *t) { s_target[i & 3] = t; }
void xb_gpu_target_bias(unsigned i, int exp_bias) { s_target_bias[i & 3] = exp_bias; }
void xb_gpu_target_format(unsigned i, int fmt) { s_target_fmt[i & 3] = fmt; }

/* What render target 0's guest format holds of a pixel shader's colour, for the converted shader's epilogue (xb_at.w, tools/shader_convert.py):
 * 1 the fixed-point formats 8_8_8_8, 8_8_8_8_GAMMA and 2_10_10_10 (0, 1, 2), each channel 0..1; 2 the 7e3 float 2_10_10_10_FLOAT (3, 12), colour
 * 0..31.875 and a two-bit alpha; 3 the 16-bit fixed 16_16 and 16_16_16_16 (4, 5), -32..32; 0 a float format, unknown or XB_RT_CLAMP=0, kept as drawn.
 * The host holds every target as 16-bit float, so without it a colour the console clamps went on growing: Too Human draws its scene into 7e3
 * targets and its post passes into 8_8_8_8 ones (the user's log of 2026-11-12) */
static float target_clamp_mode(void)
{
    static int on = -1;
    int f = s_target_fmt[0];
    if (on < 0) { const char *e = getenv("XB_RT_CLAMP"); on = !(e && e[0] == '0'); }
    if (!on) return 0.0f;
    return f == 0 || f == 1 || f == 2 ? 1.0f : f == 3 || f == 12 ? 2.0f : f == 4 || f == 5 ? 3.0f : 0.0f;
}
void xb_gpu_bind_depth(XbHostTex *t) { s_depth = t; }
void xb_gpu_bind_texture(unsigned i, XbHostTex *t, u32 guest) { s_tex[i & 15] = t; s_tex_guest[i & 15] = guest; }

/* rr[2i] = red base, red delta, green base, green delta of entry i; rr[2i + 1] = blue base, blue delta (10-bit, as floats) */
void xb_gpu_gamma_ramp(const u32 *pairs)
{
    unsigned i, ch;
    u32 sum = 0x811c9dc5u;
    static int made;
    s_ramp_on = pairs != 0;
    if (!pairs) return;
    for (i = 0; i < 384; i++) sum = (sum ^ pairs[i]) * 0x01000193u;
    if (made && sum == s_ramp_sum) return;
    made = 1, s_ramp_sum = sum;
    for (i = 0; i < 128; i++)
        for (ch = 0; ch < 3; ch++) {
            float *e = &s_ramp[2 * i + (ch == 2)][ch == 1 ? 2 : 0];
            e[0] = (float)((pairs[ch * 128 + i] >> 22) & 1023u);
            e[1] = (float)((pairs[ch * 128 + i] >> 6) & 1023u);
        }
    s_ramp_dirty = 1;
}
void xb_gpu_bind_shaders(u32 vs_container, u32 ps_container) { s_vs_addr = vs_container; s_ps_addr = ps_container; }
void xb_gpu_viewport(float x, float y, float w, float h, float zmin, float zmax)
{
    s_vp.x = x; s_vp.y = y; s_vp.w = w; s_vp.h = h; s_vp.zmin = zmin; s_vp.zmax = zmax;
}
void xb_gpu_game_size(unsigned w, unsigned h) { s_game_w = w; s_game_h = h; }

/* The depth surface to bind with render target 0. Direct3D 11 takes a depth view only of its colour targets' size, and refuses the whole bind
 * otherwise (the draws then go to the targets bound before). Off, the game's own surface as before (a pair of other sizes is logged). With
 * XB_DEPTH=1 a depth surface bound with a target of another size gets a host depth texture of the target's size in its chain of shapes */
static XbHostTex *depth_tex(void)
{
    XbHostTex *d = s_depth, *a;
    unsigned w, h;
    if (!d || !d->dsv || !s_target[0] || (s_target[0]->w == d->w && s_target[0]->h == d->h)) return d && d->dsv ? d : 0;
    w = s_target[0]->w, h = s_target[0]->h;
    if (!xb_depth_on()) {
        static int said;
        if (said++ < 4) port_log("d3d target %ux%u bound with a depth surface %ux%u: Direct3D 11 refuses the pair (depth on gives the depth a texture of the target's size)\n", w, h, d->w, d->h);
        return d;
    }
    for (a = d->alt; a; a = a->alt)
        if (a->depth && a->dsv && a->w == w && a->h == h) return a;
    {
        D3D11_TEXTURE2D_DESC td;
        D3D11_DEPTH_STENCIL_VIEW_DESC dv;
        static int said;
        a = (XbHostTex *)calloc(1, sizeof *a);
        if (!a) xb_stop("d3d_host", "no memory for a depth texture", w);
        memset(&td, 0, sizeof td);
        td.Width = a->w = w;
        td.Height = a->h = h;
        td.MipLevels = td.ArraySize = 1;
        td.Format = DXGI_FORMAT_R24G8_TYPELESS;
        td.SampleDesc.Count = 1;
        td.Usage = D3D11_USAGE_DEFAULT;
        td.BindFlags = D3D11_BIND_DEPTH_STENCIL | D3D11_BIND_SHADER_RESOURCE;
        if (FAILED(ID3D11Device_CreateTexture2D(s_dev, &td, 0, &a->tex))) xb_stop("d3d_host", "a depth texture of the target's size was not made", w);
        memset(&dv, 0, sizeof dv);
        dv.Format = DXGI_FORMAT_D24_UNORM_S8_UINT;
        dv.ViewDimension = D3D11_DSV_DIMENSION_TEXTURE2D;
        ID3D11Device_CreateDepthStencilView(s_dev, (ID3D11Resource *)a->tex, &dv, &a->dsv);
        depth_srv(a);
        a->format = d->format, a->depth = 1;
        a->alt = d->alt, d->alt = a;
        if (said++ < 8) port_log("d3d depth surface %ux%u bound with a %ux%u target: a host depth texture of the target's size beside it\n", d->w, d->h, w, h);
        return a;
    }
}

static void bind_targets(void)
{
    ID3D11RenderTargetView *rtv[4] = {0, 0, 0, 0};
    D3D11_VIEWPORT vp;
    XbHostTex *dt = depth_tex();
    unsigned i;
    for (i = 0; i < 4; i++) rtv[i] = s_target[i] ? s_target[i]->rtv : 0;
    ID3D11DeviceContext_OMSetRenderTargets(s_ctx, 4, rtv, dt ? dt->dsv : 0);
    {
        static int said;
        if (!said++) port_log("d3d viewport %g %g %g %g, scale %g\n", (double)s_vp.x, (double)s_vp.y, (double)s_vp.w, (double)s_vp.h, (double)s_scale);
    }
    vp.TopLeftX = s_vp.x * s_scale;
    vp.TopLeftY = s_vp.y * s_scale;
    vp.Width = s_vp.w * s_scale;
    vp.Height = s_vp.h * s_scale;
    vp.MinDepth = s_vp.zmin;
    vp.MaxDepth = s_vp.zmax;
    /* Too Human sets its viewports with the depth range 1..0. The host refuses a viewport whose least depth is above its greatest and keeps the one before it, so
     * every such draw went into whatever viewport the last frame shown, resolve or depth clear had left: the whole target, 1280x720, until the window was made
     * larger, and then the frame's 2560x1440, which drew the top-left quarter at twice the size (the maximise zoom, 2026-11-09; read back at the draws
     * 2026-11-11). The range those left was always 0..1, which the mirrored z comparisons (xb_gpu_draw.h) stand on, so that is the range given here.
     * XB_VP_VALID=0 is the behaviour before */
    if (vp.MinDepth > vp.MaxDepth) {
        static int valid = -1;
        if (valid < 0) { const char *e = getenv("XB_VP_VALID"); valid = !(e && e[0] == '0'); }
        if (valid) vp.MinDepth = 0, vp.MaxDepth = 1;
    }
    ID3D11DeviceContext_RSSetViewports(s_ctx, 1, &vp);
    if (s_resize_trace > 0) {   /* XB_RESIZE_AT's trace: each change of what is bound */
        static unsigned tw, th;
        static float vw, vh;
        unsigned w0 = s_target[0] ? s_target[0]->w : 0, h0 = s_target[0] ? s_target[0]->h : 0;
        if (w0 != tw || h0 != th || vp.Width != vw || vp.Height != vh) {
            tw = w0, th = h0, vw = vp.Width, vh = vp.Height, s_resize_trace--;
            port_log("resize trace: target %ux%u, viewport %g %g %gx%g (game %gx%g), scale %g, frame %u\n", w0, h0, (double)vp.TopLeftX, (double)vp.TopLeftY, (double)vw, (double)vh, (double)s_vp.w, (double)s_vp.h, (double)s_scale, s_frames);
        }
    }
    {   /* XB_VP_LOG=n, a diagnostic: after n binds, each change of the host target's size, the viewport or the thread, for 600 lines */
        static long seen, left = 600;
        static unsigned lw, lh, lvw, lvh;
        static DWORD lt;
        static const char *e;
        static volatile long e_read;   /* read once: getenv each bind cost 1 ms a gameplay frame */
        if (!e_read) e = getenv("XB_VP_LOG"), e_read = 1;
        unsigned tw = s_target[0] ? s_target[0]->w : 0, th = s_target[0] ? s_target[0]->h : 0;
        if (e && seen++ >= atol(e) && left > 0 && (tw != lw || th != lh || (unsigned)vp.Width != lvw || (unsigned)vp.Height != lvh || GetCurrentThreadId() != lt)) {
            left--;
            lw = tw, lh = th, lvw = (unsigned)vp.Width, lvh = (unsigned)vp.Height, lt = GetCurrentThreadId();
            port_log("vp bind %ld: host target %ux%u, viewport %ux%u (game %gx%g), thread %lu\n", seen, tw, th, lvw, lvh, (double)s_vp.w, (double)s_vp.h, (unsigned long)lt);
        }
    }
}

/* XB_EDRAM: a host texture cleared whole to rgba, the value its tiles' newest writer left (xb_d3d_edram.h) */
void xb_gpu_clear_tex(XbHostTex *t, const float *rgba)
{
    gpu_init();
    if (t && t->rtv) ID3D11DeviceContext_ClearRenderTargetView(s_ctx, t->rtv, rgba);
}

/* clear flags of the platform: colour 0xF0, depth 0x100, stencil 0x200 */
void xb_gpu_clear(u32 flags, const float *rgba, float z, u32 stencil)
{
    gpu_init();
    fcap_clear("whole", flags, rgba, z, stencil, 0);
    if ((flags & 0xF0) && s_target[0] && s_target[0]->rtv) ID3D11DeviceContext_ClearRenderTargetView(s_ctx, s_target[0]->rtv, rgba), xb_edram_cleared(s_target[0], rgba);
    if (zlog_frame())
        port_log("zclear %u: flags %x z %g stencil %x, target %ux%u, depth %ux%u%s\n", xb_frame_number(), flags, (double)z, stencil, s_target[0] ? s_target[0]->w : 0,
                 s_target[0] ? s_target[0]->h : 0, s_depth ? s_depth->w : 0, s_depth ? s_depth->h : 0, xb_depth_on() ? ", depth on" : "");
    if ((flags & 0x300) && s_depth && s_depth->dsv) {
        XbHostTex *dt = xb_depth_on() ? depth_tex() : s_depth;   /* XB_DEPTH=1: the depth texture the draws use, at the target's size */
        ID3D11DeviceContext_ClearDepthStencilView(s_ctx, dt->dsv,
                                                   ((flags & 0x100) ? D3D11_CLEAR_DEPTH : 0) | ((flags & 0x200) ? D3D11_CLEAR_STENCIL : 0), z, (UINT8)stencil);
    }
}

/* XB_DEPTH=1: a clear of part of the depth/stencil surface, which a view clear cannot do: a quad at depth z over each rectangle (a scissor), depth
 * compared always and written, stencil replaced with the clear's value, no colour target bound. The game's state comes back with bind_targets
 * and the next draw, which binds everything it uses again */
static void depth_rects(u32 flags, float z, u32 stencil, const D3D11_RECT *hr, unsigned n)
{
    XbHostTex *d = depth_tex();
    D3D11_VIEWPORT vp;
    D3D11_MAPPED_SUBRESOURCE m;
    unsigned k, which = (flags >> 8) & 3;
    if (!d || !s_zq_vs || !which) return;
    if (zlog_frame()) port_log("zrects %u: flags %x z %g, %u rectangles, the first %ld,%ld..%ld,%ld\n", xb_frame_number(), flags, (double)z, n, hr[0].left, hr[0].top, hr[0].right, hr[0].bottom);
    if (!s_zclear[which]) {
        D3D11_DEPTH_STENCIL_DESC ds;
        memset(&ds, 0, sizeof ds);
        ds.DepthEnable = (flags & 0x100) != 0;
        ds.DepthWriteMask = D3D11_DEPTH_WRITE_MASK_ALL;
        ds.DepthFunc = D3D11_COMPARISON_ALWAYS;
        ds.StencilEnable = (flags & 0x200) != 0;
        ds.StencilReadMask = ds.StencilWriteMask = 0xff;
        ds.FrontFace.StencilFailOp = ds.FrontFace.StencilDepthFailOp = D3D11_STENCIL_OP_KEEP;
        ds.FrontFace.StencilPassOp = D3D11_STENCIL_OP_REPLACE;
        ds.FrontFace.StencilFunc = D3D11_COMPARISON_ALWAYS;
        ds.BackFace = ds.FrontFace;
        ID3D11Device_CreateDepthStencilState(s_dev, &ds, &s_zclear[which]);
    }
    if (SUCCEEDED(ID3D11DeviceContext_Map(s_ctx, (ID3D11Resource *)s_scale_cb, 0, D3D11_MAP_WRITE_DISCARD, 0, &m))) {
        float k4[4] = {z, 0, 0, 0};
        memcpy(m.pData, k4, sizeof k4);
        ID3D11DeviceContext_Unmap(s_ctx, (ID3D11Resource *)s_scale_cb, 0);
    }
    ID3D11DeviceContext_OMSetRenderTargets(s_ctx, 0, 0, d->dsv);
    vp.TopLeftX = vp.TopLeftY = 0;
    vp.Width = (float)d->w;
    vp.Height = (float)d->h;
    vp.MinDepth = 0;
    vp.MaxDepth = 1;
    ID3D11DeviceContext_RSSetViewports(s_ctx, 1, &vp);
    ID3D11DeviceContext_RSSetState(s_ctx, s_raster_scissor);
    ID3D11DeviceContext_OMSetDepthStencilState(s_ctx, s_zclear[which], stencil & 255u);
    ID3D11DeviceContext_IASetInputLayout(s_ctx, 0);
    ID3D11DeviceContext_IASetPrimitiveTopology(s_ctx, D3D11_PRIMITIVE_TOPOLOGY_TRIANGLELIST);
    ID3D11DeviceContext_VSSetShader(s_ctx, s_zq_vs, 0, 0);
    ID3D11DeviceContext_VSSetConstantBuffers(s_ctx, 3, 1, &s_scale_cb);
    ID3D11DeviceContext_GSSetShader(s_ctx, 0, 0, 0);
    ID3D11DeviceContext_PSSetShader(s_ctx, 0, 0, 0);
    for (k = 0; k < n; k++) {
        ID3D11DeviceContext_RSSetScissorRects(s_ctx, 1, &hr[k]);
        ID3D11DeviceContext_Draw(s_ctx, 3, 0);
    }
    ID3D11DeviceContext_RSSetState(s_ctx, s_raster);
    ID3D11DeviceContext_OMSetDepthStencilState(s_ctx, s_depth_off, 0);
    bind_targets();
}

/* A clear of n rectangles {x1, y1, x2, y2} in the game's pixels. Colour through ClearView, which takes rectangles. Depth and stencil
 * views have no rectangle clear: rectangles that cover the whole surface clear it, and part of one stops until a game needs it */
void xb_gpu_clear_rects(u32 flags, const float *rgba, float z, u32 stencil, u32 rects, unsigned n)
{
    static ID3D11DeviceContext1 *c1;
    static int said;
    D3D11_RECT hr[8];
    s32 r[32];
    unsigned i;
    fcap_clear("rects", flags, rgba, z, stencil, n);
    gpu_init();
    if (n > 8) xb_stop("d3d_clear", "a clear of more than 8 rectangles", n);
    for (i = 0; i < n * 4; i++) r[i] = (s32)rd32(rects + 4 * i);
    if (said++ < 8) port_log("d3d Clear of %u rectangles, the first %d,%d..%d,%d, flags %x\n", n, r[0], r[1], r[2], r[3], flags);
    for (i = 0; i < n && i < 8; i++) {
        hr[i].left = (LONG)(r[4 * i] * s_scale + 0.5f);
        hr[i].top = (LONG)(r[4 * i + 1] * s_scale + 0.5f);
        hr[i].right = (LONG)(r[4 * i + 2] * s_scale + 0.5f);
        hr[i].bottom = (LONG)(r[4 * i + 3] * s_scale + 0.5f);
    }
    if ((flags & 0xF0) && s_target[0] && s_target[0]->rtv) {
        HRESULT h;
        if (!c1 && FAILED(h = ID3D11DeviceContext_QueryInterface(s_ctx, &IID_ID3D11DeviceContext1, (void **)&c1))) gpu_fail("ID3D11DeviceContext1", h);
        ID3D11DeviceContext1_ClearView(c1, (ID3D11View *)s_target[0]->rtv, rgba, hr, i);
        xb_edram_written(s_target[0]);
    }
    if ((flags & 0x300) && s_depth && s_depth->dsv) {
        unsigned k, whole = 0;
        for (k = 0; k < i; k++)
            if (hr[k].left <= 0 && hr[k].top <= 0 && hr[k].right >= (LONG)s_depth->w && hr[k].bottom >= (LONG)s_depth->h) whole = 1;
        if (!whole && xb_depth_on()) {   /* XB_DEPTH=1: a depth-only quad per rectangle */
            static int said_quad;
            if (!said_quad++) port_log("d3d depth/stencil clear of part of the surface (%ld,%ld..%ld,%ld, flags %x): a depth-only quad per rectangle\n",
                                       hr[0].left, hr[0].top, hr[0].right, hr[0].bottom, flags);
            depth_rects(flags, z, stencil, hr, i);
            return;
        }
        if (!whole) {   /* first stop: Too Human at about frame 700. Every draw runs with depth off (s_depth_off) so nothing reads it yet;
                         * XB_DEPTH=1 draws a depth-only quad per rectangle (depth_rects) */
            static int said_part;
            if (!said_part++) port_log("d3d depth/stencil clear of part of the surface (%ld,%ld..%ld,%ld, flags %x) skipped: the host keeps depth off\n",
                                       hr[0].left, hr[0].top, hr[0].right, hr[0].bottom, flags);
            return;
        }
        xb_gpu_clear(flags & 0x300, rgba, z, stencil);
    }
}

#include "xb_gpu_draw.h"
#include "xb_gpu_fcap.h"   /* the frame capture's records */
