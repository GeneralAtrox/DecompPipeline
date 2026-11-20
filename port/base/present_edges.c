/* Bars that carry the picture's edge colours (present.h gfx_carry_edges). A console's 4:3 screen in a wider frame
 * leaves bars beside it that its renderer did not draw; they hold the border colour. Here the screen's leftmost and
 * rightmost pixel columns (topmost and bottommost rows, in a taller frame) are copied into a 2-pixel strip, and the bars
 * are filled from it, each pixel taking the edge pixel of its own row (column): a movie's white edge gives white bars.
 * The screen itself is not touched, and a 4:3 frame has no bars, so nothing is drawn there.
 *
 * Nearest existing: present_cpu.c (draws a texture into the frame with a shader compiled at start-up); it draws the
 * screen, not the bars, and samples, where this copies pixels exactly (Load). */
#include <string.h>
#include "present.h"

static const char k_hlsl[] =
    "Texture2D<float4> e : register(t0);\n"
    "cbuffer R : register(b0) { int4 r; int wide; int black; int2 pad; };\n"
    "float4 vs_main(uint i : SV_VertexID) : SV_Position {\n"
    "    float2 uv = float2((i << 1) & 2, i & 2); return float4(uv * float2(2, -2) + float2(-1, 1), 0, 1); }\n"
    "float4 ps_main(float4 p : SV_Position) : SV_Target {\n"
    "    int2 q = int2(p.xy);\n"
    "    if (black) { if (q.x >= r.x && q.x < r.x + r.z && q.y >= r.y && q.y < r.y + r.w) discard; return float4(0, 0, 0, 1); }\n"
    "    if (wide) { if (q.x >= r.x && q.x < r.x + r.z) discard; return float4(e.Load(int3(q.x < r.x ? 0 : 1, q.y, 0)).rgb, 1); }\n"
    "    if (q.y >= r.y && q.y < r.y + r.w) discard; return float4(e.Load(int3(q.x, q.y < r.y ? 0 : 1, 0)).rgb, 1); }\n";

static ID3D11VertexShader *g_vs;
static ID3D11PixelShader *g_ps;
static ID3D11Buffer *g_cb;
static ID3D11Texture2D *g_strip;
static ID3D11ShaderResourceView *g_srv;
static u32 g_strip_w, g_strip_h;
static int g_broken, g_black_broken;

void gfx_edges_release(void)
{
    if (g_srv) ID3D11ShaderResourceView_Release(g_srv);
    if (g_strip) ID3D11Texture2D_Release(g_strip);
    if (g_cb) ID3D11Buffer_Release(g_cb);
    if (g_ps) ID3D11PixelShader_Release(g_ps);
    if (g_vs) ID3D11VertexShader_Release(g_vs);
    g_srv = NULL;
    g_strip = NULL;
    g_cb = NULL;
    g_ps = NULL;
    g_vs = NULL;
    g_strip_w = g_strip_h = 0;
}

static void fill(const u32 r[4], int wide, int black);

static int make_pipeline(void)
{
    D3D11_BUFFER_DESC b;
    if (!gfx_shaders(k_hlsl, "present_edges", &g_vs, &g_ps)) return 0;
    memset(&b, 0, sizeof b);
    b.ByteWidth = 32;
    b.Usage = D3D11_USAGE_DEFAULT;
    b.BindFlags = D3D11_BIND_CONSTANT_BUFFER;
    return SUCCEEDED(ID3D11Device_CreateBuffer(g_gfx.dev, &b, NULL, &g_cb));
}

/* The strip: 2 x the frame's height (a wide frame), or the frame's width x 2 (a tall one), in the frame's format. */
static int make_strip(u32 w, u32 h)
{
    D3D11_TEXTURE2D_DESC d;
    if (g_srv) ID3D11ShaderResourceView_Release(g_srv);
    if (g_strip) ID3D11Texture2D_Release(g_strip);
    g_srv = NULL;
    g_strip = NULL;
    g_strip_w = g_strip_h = 0;
    memset(&d, 0, sizeof d);
    d.Width = w;
    d.Height = h;
    d.MipLevels = d.ArraySize = 1;
    d.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
    d.SampleDesc.Count = 1;
    d.Usage = D3D11_USAGE_DEFAULT;
    d.BindFlags = D3D11_BIND_SHADER_RESOURCE;
    if (FAILED(ID3D11Device_CreateTexture2D(g_gfx.dev, &d, NULL, &g_strip)) ||
        FAILED(ID3D11Device_CreateShaderResourceView(g_gfx.dev, (ID3D11Resource *)g_strip, NULL, &g_srv)))
        return 0;
    g_strip_w = w;
    g_strip_h = h;
    return 1;
}

void gfx_carry_edges(void)
{
    ID3D11DeviceContext *cx = g_gfx.ctx;
    D3D11_BOX box;
    int wide = g_gfx.wide_k > 1.0f;
    u32 r[4], sw, sh;
    if (!g_gfx.frame_rtv || g_broken || (!wide && g_gfx.tall_k <= 1.0f)) return;
    gfx_screen_rect(g_gfx.frame_w, g_gfx.frame_h, g_gfx.wide_k, g_gfx.tall_k, r);
    if ((wide && !r[0]) || (!wide && !r[1])) return; /* a frame a pixel wider than the screen: no bar to fill */
    sw = wide ? 2u : g_gfx.frame_w;
    sh = wide ? g_gfx.frame_h : 2u;
    if ((!g_vs && !make_pipeline()) || ((sw != g_strip_w || sh != g_strip_h) && !make_strip(sw, sh))) {
        port_log("gfx: no edge-colour bars; the bars keep the border colour\n");
        g_broken = 1;
        return;
    }
    for (u32 k = 0; k < 2u; k++) {
        box.left = wide ? r[0] + k * (r[2] - 1u) : 0u;
        box.top = wide ? 0u : r[1] + k * (r[3] - 1u);
        box.right = wide ? box.left + 1u : g_gfx.frame_w;
        box.bottom = wide ? g_gfx.frame_h : box.top + 1u;
        box.front = 0u;
        box.back = 1u;
        ID3D11DeviceContext_CopySubresourceRegion(cx, (ID3D11Resource *)g_strip, 0, wide ? k : 0u, wide ? 0u : k, 0,
                                                  (ID3D11Resource *)g_gfx.frame, 0, &box);
    }
    fill(r, wide, 0);
}

/* present.h: the bars black (a movie on) */
void gfx_black_bars(void)
{
    int wide = g_gfx.wide_k > 1.0f;
    u32 r[4];
    if (!g_gfx.frame_rtv || g_black_broken || (!wide && g_gfx.tall_k <= 1.0f)) return;
    gfx_screen_rect(g_gfx.frame_w, g_gfx.frame_h, g_gfx.wide_k, g_gfx.tall_k, r);
    if ((wide && !r[0]) || (!wide && !r[1])) return;
    if (!g_vs && !make_pipeline()) {
        port_log("gfx: no black bars for movies; the bars keep what was drawn there\n");
        g_black_broken = 1;
        return;
    }
    fill(r, wide, 1);
}

/* present.h, for proofs */
u32 gfx_bars_lit(const u8 *rows, u32 stride, u32 w, u32 h, const u32 scr[4])
{
    u32 lit = 0;
    for (u32 side = 0; side < 4u; side++) { /* left, right, top, bottom */
        u32 x0 = side == 1u ? scr[0] + scr[2] : 0u, y0 = side == 3u ? scr[1] + scr[3] : 0u;
        u32 bw = side == 0u ? scr[0] : side == 1u ? w - x0 : w, bh = side == 2u ? scr[1] : side == 3u ? h - y0 : h;
        if (!bw || !bh || x0 >= w || y0 >= h) continue;
        for (u32 j = 0; j < 16u; j++)
            for (u32 i = 0; i < 16u; i++) {
                const u8 *p = rows + (size_t)(y0 + (2u * j + 1u) * bh / 32u) * stride + (size_t)(x0 + (2u * i + 1u) * bw / 32u) * 4u;
                lit += p[0] > 8u || p[1] > 8u || p[2] > 8u;
            }
    }
    return lit;
}

/* The bars drawn: from the strip (the edge colours), or black */
static void fill(const u32 r[4], int wide, int black)
{
    ID3D11DeviceContext *cx = g_gfx.ctx;
    ID3D11ShaderResourceView *none = NULL;
    D3D11_VIEWPORT vp;
    int cb[8];
    memset(cb, 0, sizeof cb);
    for (int i = 0; i < 4; i++) cb[i] = (int)r[i];
    cb[4] = wide;
    cb[5] = black;
    ID3D11DeviceContext_UpdateSubresource(cx, (ID3D11Resource *)g_cb, 0, NULL, cb, 0, 0);
    memset(&vp, 0, sizeof vp);
    vp.Width = (FLOAT)g_gfx.frame_w;
    vp.Height = (FLOAT)g_gfx.frame_h;
    vp.MaxDepth = 1.0f;
    ID3D11DeviceContext_OMSetRenderTargets(cx, 1, &g_gfx.frame_rtv, NULL);
    ID3D11DeviceContext_OMSetBlendState(cx, NULL, NULL, 0xffffffffu);
    ID3D11DeviceContext_OMSetDepthStencilState(cx, NULL, 0);
    ID3D11DeviceContext_RSSetState(cx, NULL);
    ID3D11DeviceContext_RSSetViewports(cx, 1, &vp);
    ID3D11DeviceContext_IASetInputLayout(cx, NULL);
    ID3D11DeviceContext_IASetPrimitiveTopology(cx, D3D11_PRIMITIVE_TOPOLOGY_TRIANGLELIST);
    ID3D11DeviceContext_VSSetShader(cx, g_vs, NULL, 0);
    ID3D11DeviceContext_PSSetShader(cx, g_ps, NULL, 0);
    ID3D11DeviceContext_PSSetShaderResources(cx, 0, 1, &g_srv);
    ID3D11DeviceContext_PSSetConstantBuffers(cx, 0, 1, &g_cb);
    ID3D11DeviceContext_Draw(cx, 3, 0);
    ID3D11DeviceContext_PSSetShaderResources(cx, 0, 1, &none);
    ID3D11DeviceContext_OMSetRenderTargets(cx, 0, NULL, NULL);
}
