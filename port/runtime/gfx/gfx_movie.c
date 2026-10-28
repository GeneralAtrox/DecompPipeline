/* A movie over the game's picture, as a middleware player draws its decoded frames: runtime/media hands over each
 * frame's Y, Cb and Cr planes (gfx_movie_show), which go to three 8-bit textures; at each frame, after the renderer
 * and before the frame is dumped or shown, the frame is drawn over the 640x480 screen (the middle of a widescreen
 * frame) by a pixel shader that turns YCbCr into RGB (gfx_movie.hlsl). gfx_movie_hide stops drawing it. */
#include <string.h>
#include "gfx.h"
#include "pvr_int.h" /* pvr_full_vs */
#include "gfx_movie_ps.h" /* fxc: g_gfx_movie_ps */
#include "media/media.h"

static ID3D11Texture2D *g_tex[3];
static ID3D11ShaderResourceView *g_srv[3];
static ID3D11PixelShader *g_ps;
static ID3D11SamplerState *g_sampler;
static ID3D11Buffer *g_rect;
static u32 g_w, g_h;
static int g_on, g_broken;

void gfx_movie_release(void)
{
    for (int i = 0; i < 3; i++) {
        if (g_srv[i]) ID3D11ShaderResourceView_Release(g_srv[i]);
        if (g_tex[i]) ID3D11Texture2D_Release(g_tex[i]);
        g_srv[i] = NULL;
        g_tex[i] = NULL;
    }
    if (g_ps) ID3D11PixelShader_Release(g_ps);
    if (g_sampler) ID3D11SamplerState_Release(g_sampler);
    if (g_rect) ID3D11Buffer_Release(g_rect);
    g_ps = NULL;
    g_sampler = NULL;
    g_rect = NULL;
    g_w = g_h = 0;
    g_on = 0;
}

static int make(u32 w, u32 h)
{
    ID3D11Device *dev = g_gfx.dev;
    D3D11_TEXTURE2D_DESC t;
    D3D11_SAMPLER_DESC s;
    D3D11_BUFFER_DESC b;
    for (int i = 0; i < 3; i++) {
        if (g_srv[i]) ID3D11ShaderResourceView_Release(g_srv[i]);
        if (g_tex[i]) ID3D11Texture2D_Release(g_tex[i]);
        g_srv[i] = NULL;
        g_tex[i] = NULL;
        memset(&t, 0, sizeof t);
        t.Width = i ? w / 2u : w;
        t.Height = i ? h / 2u : h;
        t.MipLevels = t.ArraySize = 1;
        t.Format = DXGI_FORMAT_R8_UNORM;
        t.SampleDesc.Count = 1;
        t.Usage = D3D11_USAGE_DEFAULT;
        t.BindFlags = D3D11_BIND_SHADER_RESOURCE;
        if (FAILED(ID3D11Device_CreateTexture2D(dev, &t, NULL, &g_tex[i])) ||
            FAILED(ID3D11Device_CreateShaderResourceView(dev, (ID3D11Resource *)g_tex[i], NULL, &g_srv[i])))
            return 0;
    }
    if (!g_ps && FAILED(ID3D11Device_CreatePixelShader(dev, g_gfx_movie_ps, sizeof g_gfx_movie_ps, NULL, &g_ps)))
        return 0;
    if (!g_sampler) {
        memset(&s, 0, sizeof s);
        s.Filter = D3D11_FILTER_MIN_MAG_MIP_LINEAR;
        s.AddressU = s.AddressV = s.AddressW = D3D11_TEXTURE_ADDRESS_CLAMP;
        s.MaxLOD = D3D11_FLOAT32_MAX;
        if (FAILED(ID3D11Device_CreateSamplerState(dev, &s, &g_sampler))) return 0;
    }
    if (!g_rect) {
        memset(&b, 0, sizeof b);
        b.ByteWidth = 16;
        b.Usage = D3D11_USAGE_DEFAULT;
        b.BindFlags = D3D11_BIND_CONSTANT_BUFFER;
        if (FAILED(ID3D11Device_CreateBuffer(dev, &b, NULL, &g_rect))) return 0;
    }
    g_w = w;
    g_h = h;
    return 1;
}

void gfx_movie_show(const u8 *y, const u8 *cb, const u8 *cr, u32 y_stride, u32 c_stride, u32 w, u32 h)
{
    const u8 *planes[3] = {y, cb, cr};
    if (!g_gfx.dev || g_broken || w < 2u || h < 2u) return;
    if ((w != g_w || h != g_h) && !make(w, h)) {
        port_log("gfx: no movie textures; movies are not drawn\n");
        g_broken = 1;
        return;
    }
    for (int i = 0; i < 3; i++)
        ID3D11DeviceContext_UpdateSubresource(g_gfx.ctx, (ID3D11Resource *)g_tex[i], 0, NULL, planes[i],
                                              i ? c_stride : y_stride, 0);
    g_on = 1;
}

void gfx_movie_hide(void) { g_on = 0; }

void gfx_movie_draw(void)
{
    ID3D11DeviceContext *cx = g_gfx.ctx;
    ID3D11VertexShader *vs;
    ID3D11ShaderResourceView *none[3] = {NULL, NULL, NULL};
    D3D11_VIEWPORT vp;
    float rect[4];
    media_movie_frame(); /* the picture of the sound heard now (gfx_movie_show) */
    if (!g_on || !g_gfx.frame_rtv || !(vs = pvr_full_vs())) return;
    /* the 640x480 screen: the frame's height, 4:3, in its middle */
    rect[3] = (float)g_gfx.frame_h;
    rect[2] = rect[3] * 4.0f / 3.0f;
    rect[0] = ((float)g_gfx.frame_w - rect[2]) * 0.5f;
    rect[1] = 0.0f;
    memset(&vp, 0, sizeof vp);
    vp.TopLeftX = rect[0];
    vp.Width = rect[2];
    vp.Height = rect[3];
    vp.MaxDepth = 1.0f;
    ID3D11DeviceContext_UpdateSubresource(cx, (ID3D11Resource *)g_rect, 0, NULL, rect, 0, 0);
    ID3D11DeviceContext_OMSetRenderTargets(cx, 1, &g_gfx.frame_rtv, NULL);
    ID3D11DeviceContext_OMSetBlendState(cx, NULL, NULL, 0xffffffffu);
    ID3D11DeviceContext_OMSetDepthStencilState(cx, NULL, 0);
    ID3D11DeviceContext_RSSetState(cx, NULL);
    ID3D11DeviceContext_RSSetViewports(cx, 1, &vp);
    ID3D11DeviceContext_IASetInputLayout(cx, NULL);
    ID3D11DeviceContext_IASetPrimitiveTopology(cx, D3D11_PRIMITIVE_TOPOLOGY_TRIANGLELIST);
    ID3D11DeviceContext_VSSetShader(cx, vs, NULL, 0);
    ID3D11DeviceContext_PSSetShader(cx, g_ps, NULL, 0);
    ID3D11DeviceContext_PSSetShaderResources(cx, 0, 3, g_srv);
    ID3D11DeviceContext_PSSetSamplers(cx, 0, 1, &g_sampler);
    ID3D11DeviceContext_PSSetConstantBuffers(cx, 0, 1, &g_rect);
    ID3D11DeviceContext_Draw(cx, 3, 0);
    ID3D11DeviceContext_PSSetShaderResources(cx, 0, 3, none);
}
