/* The base's presenter for a CPU-buffer frame (present_cpu.h): the console's frame goes into a texture and is drawn
 * nearest-neighbour as the 4:3 screen in the middle of the frame target (present.c), which present_show puts on the
 * window like any other console's. A CPU frame is composed at its own width, so a wider frame target shows bars beside
 * it: nothing is stretched or cropped, and the world is not widened until the console composes it wider.
 *
 * The shaders are small enough to compile at start-up, with the compiler Windows carries (d3dcompiler_47.dll). */
#include <string.h>
#include "present_cpu.h"
#include "present.h"
#include "host.h"
#pragma warning(push)
#pragma warning(disable : 4201)
#include <d3dcompiler.h>
#pragma warning(pop)

static const char k_hlsl[] =
    "Texture2D t : register(t0); SamplerState s : register(s0);\n"
    "struct V { float4 p : SV_Position; float2 uv : TEXCOORD0; };\n"
    "V vs_main(uint i : SV_VertexID) {\n"
    "    V o; o.uv = float2((i << 1) & 2, i & 2); o.p = float4(o.uv * float2(2, -2) + float2(-1, 1), 0, 1); return o; }\n"
    "float4 ps_main(V i) : SV_Target { return float4(t.Sample(s, i.uv).rgb, 1); }\n";

static ID3D11VertexShader *g_vs;
static ID3D11PixelShader *g_ps;
static ID3D11SamplerState *g_sampler;
static ID3D11Texture2D *g_tex;
static ID3D11ShaderResourceView *g_srv;
static u32 g_tex_w, g_tex_h, g_shown_w, g_shown_h;

int cpu_present_option(const char *arg, const char *value)
{
    if (value && (!strcmp(arg, "--present") || !strcmp(arg, "--fps") || !strcmp(arg, "--interp-frames"))) return 2;
    return gfx_option(arg, value);
}

static ID3DBlob *compile(pD3DCompile fn, const char *entry, const char *target)
{
    ID3DBlob *code = NULL, *err = NULL;
    HRESULT hr = fn(k_hlsl, sizeof k_hlsl - 1, "present_cpu", NULL, NULL, entry, target, 0, 0, &code, &err);
    if (FAILED(hr)) port_log("video: shader %s failed (%08lx): %s\n", entry, (unsigned long)hr, err ? (const char *)ID3D10Blob_GetBufferPointer(err) : "");
    if (err) ID3D10Blob_Release(err);
    return code;
}

static int make_pipeline(void)
{
    HMODULE dll = LoadLibraryW(L"d3dcompiler_47.dll");
    pD3DCompile fn = dll ? (pD3DCompile)(void *)GetProcAddress(dll, "D3DCompile") : NULL;
    ID3DBlob *vs, *ps;
    D3D11_SAMPLER_DESC sd;
    HRESULT hr;
    if (!fn) {
        port_log("video: no d3dcompiler_47.dll\n");
        return 0;
    }
    vs = compile(fn, "vs_main", "vs_5_0");
    ps = compile(fn, "ps_main", "ps_5_0");
    if (!vs || !ps) return 0;
    hr = ID3D11Device_CreateVertexShader(g_gfx.dev, ID3D10Blob_GetBufferPointer(vs), ID3D10Blob_GetBufferSize(vs), NULL, &g_vs);
    if (SUCCEEDED(hr)) hr = ID3D11Device_CreatePixelShader(g_gfx.dev, ID3D10Blob_GetBufferPointer(ps), ID3D10Blob_GetBufferSize(ps), NULL, &g_ps);
    ID3D10Blob_Release(vs);
    ID3D10Blob_Release(ps);
    memset(&sd, 0, sizeof sd);
    sd.Filter = D3D11_FILTER_MIN_MAG_MIP_POINT;
    sd.AddressU = sd.AddressV = sd.AddressW = D3D11_TEXTURE_ADDRESS_CLAMP;
    sd.MaxLOD = D3D11_FLOAT32_MAX;
    if (SUCCEEDED(hr)) hr = ID3D11Device_CreateSamplerState(g_gfx.dev, &sd, &g_sampler);
    if (FAILED(hr)) port_log("video: no shaders (%08lx)\n", (unsigned long)hr);
    return SUCCEEDED(hr);
}

int cpu_present_open(void)
{
    if (!gfx_init()) return 0;
    return make_pipeline();
}

/* A texture of the frame's size, made again when the console changes mode (a Saturn's 320x224 to 704x480). */
static int make_texture(u32 w, u32 h)
{
    D3D11_TEXTURE2D_DESC d;
    HRESULT hr;
    if (g_srv) ID3D11ShaderResourceView_Release(g_srv);
    if (g_tex) ID3D11Texture2D_Release(g_tex);
    g_srv = NULL;
    g_tex = NULL;
    g_tex_w = g_tex_h = 0;
    memset(&d, 0, sizeof d);
    d.Width = w;
    d.Height = h;
    d.MipLevels = 1;
    d.ArraySize = 1;
    d.Format = DXGI_FORMAT_B8G8R8X8_UNORM; /* 0x00RRGGBB in memory */
    d.SampleDesc.Count = 1;
    d.Usage = D3D11_USAGE_DYNAMIC;
    d.BindFlags = D3D11_BIND_SHADER_RESOURCE;
    d.CPUAccessFlags = D3D11_CPU_ACCESS_WRITE;
    hr = ID3D11Device_CreateTexture2D(g_gfx.dev, &d, NULL, &g_tex);
    if (SUCCEEDED(hr)) hr = ID3D11Device_CreateShaderResourceView(g_gfx.dev, (ID3D11Resource *)g_tex, NULL, &g_srv);
    if (FAILED(hr)) {
        port_log("video: no %ux%u texture (%08lx)\n", w, h, (unsigned long)hr);
        return 0;
    }
    g_tex_w = w;
    g_tex_h = h;
    return 1;
}

void cpu_present(const u32 *xrgb, unsigned w, unsigned h)
{
    D3D11_MAPPED_SUBRESOURCE m;
    D3D11_VIEWPORT vp;
    u32 r[4];
    ID3D11DeviceContext *cx = g_gfx.ctx;
    if (!g_vs || !g_gfx.frame_rtv) {
        gfx_pump();
        return;
    }
    if ((w != g_tex_w || h != g_tex_h) && !make_texture(w, h)) return;
    if (FAILED(ID3D11DeviceContext_Map(cx, (ID3D11Resource *)g_tex, 0, D3D11_MAP_WRITE_DISCARD, 0, &m))) return;
    for (unsigned y = 0; y < h; y++) memcpy((u8 *)m.pData + y * m.RowPitch, xrgb + y * w, w * 4u);
    ID3D11DeviceContext_Unmap(cx, (ID3D11Resource *)g_tex, 0);
    g_shown_w = w;
    g_shown_h = h;
    gfx_clear(0);
    gfx_screen_rect(g_gfx.frame_w, g_gfx.frame_h, g_gfx.wide_k, g_gfx.tall_k, r);
    vp.TopLeftX = (FLOAT)r[0];
    vp.TopLeftY = (FLOAT)r[1];
    vp.Width = (FLOAT)r[2];
    vp.Height = (FLOAT)r[3];
    vp.MinDepth = 0.0f;
    vp.MaxDepth = 1.0f;
    ID3D11DeviceContext_OMSetRenderTargets(cx, 1, &g_gfx.frame_rtv, NULL);
    ID3D11DeviceContext_RSSetViewports(cx, 1, &vp);
    ID3D11DeviceContext_IASetPrimitiveTopology(cx, D3D11_PRIMITIVE_TOPOLOGY_TRIANGLELIST);
    ID3D11DeviceContext_VSSetShader(cx, g_vs, NULL, 0);
    ID3D11DeviceContext_PSSetShader(cx, g_ps, NULL, 0);
    ID3D11DeviceContext_PSSetSamplers(cx, 0, 1, &g_sampler);
    ID3D11DeviceContext_PSSetShaderResources(cx, 0, 1, &g_srv);
    ID3D11DeviceContext_Draw(cx, 3, 0);
    present_show();
}

void cpu_present_view(PortView *v)
{
    v->out_w = g_gfx.out_w;
    v->out_h = g_gfx.out_h;
    v->frame_w = g_shown_w;
    v->frame_h = g_shown_h;
    v->presented_fps = (float)gfx_presented_fps();
}
