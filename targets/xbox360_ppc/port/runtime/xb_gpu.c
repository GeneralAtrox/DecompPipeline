/* The host side of the picture: what the game's Direct3D 9 calls become on the port base's Direct3D 11 device. There is no
 * command stream and no hardware model: xb_d3d.c turns each entry the game calls into a call here with the values already read
 * from guest memory.
 *
 * The base owns the window, the device, the swap chain and the frame target (base/present.h). The game's targets and full-screen
 * textures are made at the output's scale when the first one is made (a later change of window size scales the picture), and
 * the game's frame is shown in the frame target at its own shape, never stretched. */
#define COBJMACROS
#include <windows.h>
#include <d3d11.h>
#include <d3dcompiler.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "xb.h"
#include "console.h"
#include "present.h"

#pragma comment(lib, "d3dcompiler.lib")

struct XbHostTex {
    ID3D11Texture2D *tex;
    ID3D11ShaderResourceView *srv;
    ID3D11RenderTargetView *rtv;
    ID3D11DepthStencilView *dsv;
    unsigned w, h;
};

typedef struct {
    u32 addr;
    ID3D11VertexShader *vs;
    ID3D11PixelShader *ps;
    ID3D11GeometryShader *gs;
    ID3DBlob *vs_code;
    ID3D11InputLayout *layout;
    unsigned layout_n;
} HostShader;

static ID3D11Device *s_dev;
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
static HostShader s_shader[64];
static unsigned s_shader_n;
static u32 s_vs_addr, s_ps_addr;
static XbHostTex *s_target[4], *s_depth, *s_tex[16];
static u32 s_tex_guest[16];
static struct { float x, y, w, h, zmin, zmax; } s_vp;
static ID3D11VertexShader *s_blit_vs;
static ID3D11PixelShader *s_blit_ps;
static unsigned s_frames, s_draws;

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
    "float4 ps(O i) : SV_Target { return float4(t.Sample(s, i.uv).rgb, 1); }\n";

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
    bd.ByteWidth = 32;
    ID3D11Device_CreateBuffer(s_dev, &bd, 0, &s_kit_cb);
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
    }
    s_ready = 1;
}

/* A host texture for a guest header the game made: full-screen sized ones follow the output's scale. */
XbHostTex *xb_gpu_tex(u32 guest, unsigned w, unsigned h, int format, int target, int depth)
{
    XbHostTex *t;
    D3D11_TEXTURE2D_DESC d;
    HRESULT hr;
    gpu_init();
    t = (XbHostTex *)xb_host_object(guest, sizeof *t);
    if (t->tex) return t;
    if (w == s_game_w) {   /* a full-width surface follows the output scale. The game draws its frame in two tiles, 821d00a8 BeginTiling to 821d0708 EndTiling, on a target of one tile's
                            * height (1280x384, first made by 82093cf0): the host has no tiles and the target is the whole frame */
        if (h < s_game_h) h = s_game_h;
        h = (unsigned)(h * s_scale + 0.5f);
        w = s_tgt_w;
    }
    memset(&d, 0, sizeof d);
    d.Width = t->w = w;
    d.Height = t->h = h;
    d.MipLevels = 1;
    d.ArraySize = 1;
    d.Format = depth ? DXGI_FORMAT_R24G8_TYPELESS : (DXGI_FORMAT)format;
    d.SampleDesc.Count = 1;
    d.Usage = D3D11_USAGE_DEFAULT;
    d.BindFlags = (depth ? D3D11_BIND_DEPTH_STENCIL : D3D11_BIND_SHADER_RESOURCE) | (target ? D3D11_BIND_RENDER_TARGET : 0);
    hr = ID3D11Device_CreateTexture2D(s_dev, &d, 0, &t->tex);
    if (FAILED(hr)) gpu_fail("CreateTexture2D", hr);
    if (depth) {
        D3D11_DEPTH_STENCIL_VIEW_DESC dv;
        memset(&dv, 0, sizeof dv);
        dv.Format = DXGI_FORMAT_D24_UNORM_S8_UINT;
        dv.ViewDimension = D3D11_DSV_DIMENSION_TEXTURE2D;
        ID3D11Device_CreateDepthStencilView(s_dev, (ID3D11Resource *)t->tex, &dv, &t->dsv);
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

void xb_gpu_upload(XbHostTex *t, const void *rgba, unsigned pitch)
{
    ID3D11DeviceContext_UpdateSubresource(s_ctx, (ID3D11Resource *)t->tex, 0, 0, rgba, pitch, 0);
}

unsigned xb_gpu_scaled_w(unsigned w) { return w == s_game_w ? s_tgt_w : w; }

void xb_gpu_bind_target(unsigned i, XbHostTex *t) { s_target[i & 3] = t; }
void xb_gpu_bind_depth(XbHostTex *t) { s_depth = t; }
void xb_gpu_bind_texture(unsigned i, XbHostTex *t, u32 guest) { s_tex[i & 15] = t; s_tex_guest[i & 15] = guest; }
void xb_gpu_bind_shaders(u32 vs_container, u32 ps_container) { s_vs_addr = vs_container; s_ps_addr = ps_container; }
void xb_gpu_viewport(float x, float y, float w, float h, float zmin, float zmax)
{
    s_vp.x = x; s_vp.y = y; s_vp.w = w; s_vp.h = h; s_vp.zmin = zmin; s_vp.zmax = zmax;
}
void xb_gpu_game_size(unsigned w, unsigned h) { s_game_w = w; s_game_h = h; }

static void bind_targets(void)
{
    ID3D11RenderTargetView *rtv[4] = {0, 0, 0, 0};
    D3D11_VIEWPORT vp;
    unsigned i;
    for (i = 0; i < 4; i++) rtv[i] = s_target[i] ? s_target[i]->rtv : 0;
    ID3D11DeviceContext_OMSetRenderTargets(s_ctx, 4, rtv, s_depth ? s_depth->dsv : 0);
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
    ID3D11DeviceContext_RSSetViewports(s_ctx, 1, &vp);
}

/* clear flags of the platform: colour 0xF0, depth 0x100, stencil 0x200 */
void xb_gpu_clear(u32 flags, const float *rgba, float z, u32 stencil)
{
    gpu_init();
    if ((flags & 0xF0) && s_target[0] && s_target[0]->rtv) ID3D11DeviceContext_ClearRenderTargetView(s_ctx, s_target[0]->rtv, rgba);
    if ((flags & 0x300) && s_depth && s_depth->dsv)
        ID3D11DeviceContext_ClearDepthStencilView(s_ctx, s_depth->dsv,
                                                   ((flags & 0x100) ? D3D11_CLEAR_DEPTH : 0) | ((flags & 0x200) ? D3D11_CLEAR_STENCIL : 0), z, (UINT8)stencil);
}

#include "xb_gpu_draw.h"
