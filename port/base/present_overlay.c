/* Pictures drawn over a CPU-buffer frame at the window's own resolution (present_cpu.h cpu_present_overlay): a console
 * composes its frame at its native size, which present_cpu.c scales up nearest-neighbour; a picture posted with the frame
 * (Dragon Force's clearer text, port/saturn/sat_text.c) is drawn after that, at its place in the frame's dots, from an
 * image made at several times the native size, filtered, so lettering is as sharp as the window allows.
 *
 * Images are premultiplied 0xAARRGGBB, never changed once posted, and each has an id no other image ever has: a texture
 * is made from one the first time it is drawn and kept by id (TEX entries, the oldest replaced when full). The console
 * keeps an image's pixels until no slot can still show it (sat_text.c frees only images unused for 8 posts; the showing
 * thread takes only the newest of 3 slots). A console may make an image's texture ahead, on any thread (D3D11 devices are
 * free-threaded), with cpu_present_prepare: the frame that first shows it then only draws it.
 *
 * Nearest existing: present_cpu.c's own frame draw (one texture over the screen, point-sampled): an overlay needs its
 * own textures, blending and a linear sampler, so it is kept here and present_cpu.c calls it. */
#include <string.h>
#include "present_cpu.h"
#include "present_overlay.h"
#include "present.h"
#include "host.h"
#pragma warning(push)
#pragma warning(disable : 4201)
#include <d3dcompiler.h>
#pragma warning(pop)

static const char k_hlsl[] =
    "Texture2D t : register(t0); SamplerState s : register(s0);\n"
    "struct V { float4 p : SV_Position; float2 uv : TEXCOORD0; };\n"
    "float4 ps_over(V i) : SV_Target { return t.Sample(s, i.uv); }\n";

enum { TEX = 1024 };
static struct {
    u32 id, used;
    ID3D11Texture2D *tex;
    ID3D11ShaderResourceView *srv;
} g_tex[TEX];
static ID3D11PixelShader *g_ps;
static ID3D11SamplerState *g_lin;
static ID3D11BlendState *g_blend;
static u32 g_clock;

int pov_init(void *compile_fn)
{
    pD3DCompile fn = (pD3DCompile)compile_fn;
    ID3DBlob *code = NULL, *err = NULL;
    D3D11_SAMPLER_DESC sd;
    D3D11_BLEND_DESC bd;
    if (!fn || FAILED(fn(k_hlsl, sizeof k_hlsl - 1, "present_overlay", NULL, NULL, "ps_over", "ps_5_0", 0, 0, &code, &err))) {
        if (err) ID3D10Blob_Release(err);
        port_log("video: no overlay shader: pictures over the frame are not drawn\n");
        return 0;
    }
    if (FAILED(ID3D11Device_CreatePixelShader(g_gfx.dev, ID3D10Blob_GetBufferPointer(code), ID3D10Blob_GetBufferSize(code), NULL, &g_ps))) g_ps = NULL;
    ID3D10Blob_Release(code);
    memset(&sd, 0, sizeof sd);
    sd.Filter = D3D11_FILTER_MIN_MAG_MIP_LINEAR;
    sd.AddressU = sd.AddressV = sd.AddressW = D3D11_TEXTURE_ADDRESS_CLAMP;
    sd.MaxLOD = D3D11_FLOAT32_MAX;
    if (FAILED(ID3D11Device_CreateSamplerState(g_gfx.dev, &sd, &g_lin))) g_lin = NULL;
    memset(&bd, 0, sizeof bd);
    bd.RenderTarget[0].BlendEnable = TRUE; /* premultiplied: the picture plus what is under it times (1 - its alpha) */
    bd.RenderTarget[0].SrcBlend = bd.RenderTarget[0].SrcBlendAlpha = D3D11_BLEND_ONE;
    bd.RenderTarget[0].DestBlend = bd.RenderTarget[0].DestBlendAlpha = D3D11_BLEND_INV_SRC_ALPHA;
    bd.RenderTarget[0].BlendOp = bd.RenderTarget[0].BlendOpAlpha = D3D11_BLEND_OP_ADD;
    bd.RenderTarget[0].RenderTargetWriteMask = D3D11_COLOR_WRITE_ENABLE_ALL;
    if (FAILED(ID3D11Device_CreateBlendState(g_gfx.dev, &bd, &g_blend))) g_blend = NULL;
    return g_ps && g_lin && g_blend;
}

/* an immutable texture of the image and its view (the view holds the texture) */
static ID3D11ShaderResourceView *make_srv(const PresentImage *im)
{
    D3D11_TEXTURE2D_DESC d;
    D3D11_SUBRESOURCE_DATA s;
    ID3D11Texture2D *t = NULL;
    ID3D11ShaderResourceView *v = NULL;
    memset(&d, 0, sizeof d);
    d.Width = im->w, d.Height = im->h, d.MipLevels = 1, d.ArraySize = 1, d.Format = DXGI_FORMAT_B8G8R8A8_UNORM;
    d.SampleDesc.Count = 1, d.Usage = D3D11_USAGE_IMMUTABLE, d.BindFlags = D3D11_BIND_SHADER_RESOURCE;
    s.pSysMem = im->px, s.SysMemPitch = im->w * 4u, s.SysMemSlicePitch = 0;
    if (FAILED(ID3D11Device_CreateTexture2D(g_gfx.dev, &d, &s, &t))) return NULL;
    if (FAILED(ID3D11Device_CreateShaderResourceView(g_gfx.dev, (ID3D11Resource *)t, NULL, &v))) v = NULL;
    ID3D11Texture2D_Release(t);
    return v;
}

void cpu_present_prepare(PresentImage *im)
{
    if (g_ps && g_gfx.dev && !im->srv && im->w && im->h) im->srv = make_srv(im);
}

void cpu_present_unprepare(PresentImage *im)
{
    if (im->srv) ID3D11ShaderResourceView_Release((ID3D11ShaderResourceView *)im->srv), im->srv = NULL;
}

/* the image's texture: made ahead (cpu_present_prepare), else kept by id, made the first time (the oldest entry goes
 * when every one is taken) */
static ID3D11ShaderResourceView *texture(const PresentImage *im)
{
    if (im->srv) return (ID3D11ShaderResourceView *)im->srv;
    u32 slot = im->id % TEX, old = slot;
    D3D11_TEXTURE2D_DESC d;
    D3D11_SUBRESOURCE_DATA s;
    for (u32 i = 0; i < 8u; i++) { /* a short probe: the id, an empty entry, else the least recently used of the 8 */
        u32 j = (im->id + i) % TEX;
        if (g_tex[j].id == im->id && g_tex[j].srv) return g_tex[j].used = g_clock, g_tex[j].srv;
        if (!g_tex[j].srv) { old = j; break; }
        if (g_tex[j].used < g_tex[old].used) old = j;
    }
    slot = old;
    if (g_tex[slot].srv) ID3D11ShaderResourceView_Release(g_tex[slot].srv), g_tex[slot].srv = NULL;
    if (g_tex[slot].tex) ID3D11Texture2D_Release(g_tex[slot].tex), g_tex[slot].tex = NULL;
    memset(&d, 0, sizeof d);
    d.Width = im->w, d.Height = im->h, d.MipLevels = 1, d.ArraySize = 1, d.Format = DXGI_FORMAT_B8G8R8A8_UNORM;
    d.SampleDesc.Count = 1, d.Usage = D3D11_USAGE_IMMUTABLE, d.BindFlags = D3D11_BIND_SHADER_RESOURCE;
    s.pSysMem = im->px, s.SysMemPitch = im->w * 4u, s.SysMemSlicePitch = 0;
    if (FAILED(ID3D11Device_CreateTexture2D(g_gfx.dev, &d, &s, &g_tex[slot].tex))) return g_tex[slot].tex = NULL, NULL;
    if (FAILED(ID3D11Device_CreateShaderResourceView(g_gfx.dev, (ID3D11Resource *)g_tex[slot].tex, NULL, &g_tex[slot].srv))) return NULL;
    g_tex[slot].id = im->id, g_tex[slot].used = g_clock;
    return g_tex[slot].srv;
}

/* After the frame is drawn into the target through viewport fv (the frame's fw x fh dots over it): each picture at its
 * dots, with the same vertex shader (a triangle over the viewport). */
void pov_draw(const PresentOverlay *o, unsigned n, const void *frame_vp, unsigned fw, unsigned fh)
{
    const D3D11_VIEWPORT *fv = (const D3D11_VIEWPORT *)frame_vp;
    ID3D11DeviceContext *cx = g_gfx.ctx;
    float sx = fv->Width / (float)fw, sy = fv->Height / (float)fh, f[4] = {0, 0, 0, 0};
    if (!n || !g_ps || !fw || !fh) return;
    g_clock++;
    ID3D11DeviceContext_PSSetShader(cx, g_ps, NULL, 0);
    ID3D11DeviceContext_PSSetSamplers(cx, 0, 1, &g_lin);
    ID3D11DeviceContext_OMSetBlendState(cx, g_blend, f, 0xffffffffu);
    for (unsigned i = 0; i < n; i++) {
        D3D11_VIEWPORT v = *fv;
        ID3D11ShaderResourceView *srv = o[i].img ? texture(o[i].img) : NULL;
        if (!srv) continue;
        v.TopLeftX = fv->TopLeftX + o[i].x * sx, v.TopLeftY = fv->TopLeftY + o[i].y * sy, v.Width = o[i].w * sx, v.Height = o[i].h * sy;
        ID3D11DeviceContext_RSSetViewports(cx, 1, &v);
        ID3D11DeviceContext_PSSetShaderResources(cx, 0, 1, &srv);
        ID3D11DeviceContext_Draw(cx, 3, 0);
    }
    ID3D11DeviceContext_OMSetBlendState(cx, NULL, f, 0xffffffffu);
}
