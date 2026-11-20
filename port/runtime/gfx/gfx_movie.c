/* A movie over the game's picture, as a middleware player draws its decoded frames: runtime/media hands over each
 * frame's Y, Cb and Cr planes (gfx_movie_show), which go to three 8-bit textures; at each frame, after the renderer
 * and before the frame is dumped or shown, the frame is drawn over the 640x480 screen (the middle of a wider or
 * taller frame) by a pixel shader that turns YCbCr into RGB (gfx_movie.hlsl). gfx_movie_hide stops drawing it.
 * A game that decodes its own movies (a format the kit has no decoder for) hands each decoded YUV422 picture, the
 * bytes it would load into texture memory, to port_movie_show_yuv422 (platform.h): it is turned into RGBA by the
 * renderer's own conversion (pvr_yuv422_rgba) and drawn the same way, in place of the textured quad the game would
 * draw through the emulated renderer. port_movie_native is the switch: --movie-native on (play runs; the default),
 * off (the emulated renderer), always (any run, a proof); a play recording keeps the choice. */
#include <stdlib.h>
#include <string.h>
#include "gfx.h"
#include "pvr_int.h" /* pvr_full_vs */
#include "gfx_movie_ps.h" /* fxc: g_gfx_movie_ps */
#include "gfx_movie_rgba_ps.h" /* fxc: g_gfx_movie_rgba_ps */
#include "media/media.h"
#include "base.h"
#include "console.h"
#include "session.h"
#include "platform.h"

static ID3D11Texture2D *g_tex[3];
static ID3D11ShaderResourceView *g_srv[3];
static ID3D11PixelShader *g_ps;
static ID3D11SamplerState *g_sampler;
static ID3D11Buffer *g_rect;
static u32 g_w, g_h;
static int g_on, g_broken;
/* the RGBA picture (port_movie_show_yuv422): its texture, shader, size and the host rows it is converted into */
static ID3D11Texture2D *g_rgba_tex;
static ID3D11ShaderResourceView *g_rgba_srv;
static ID3D11PixelShader *g_rgba_ps;
static u32 g_rw, g_rh, *g_rgba, g_rgba_cap;
static int g_rgba_on; /* the picture drawn is the RGBA one (else the three planes) */
static int g_native_want = -1; /* --movie-native: 0 off, 1 on (play runs; the default), 2 always */

/* --movie-check on (a diagnostic; off by default): every frame shown while a movie is on is read back from the frame
 * target and the mean brightness of a 64x48 grid of points inside the 640x480 screen set against the movie picture's
 * own. A shown frame near-black (mean under 8 of 255) where the picture is not (24 or more) is counted, and the first
 * 40 are logged with the thread that showed it; one summary line as each movie is taken down. Each check stalls the
 * show for a read-back, so it is for proof runs only. */
#define CHECK_GX 64u
#define CHECK_GY 48u
static int g_check = -1;
static ID3D11Texture2D *g_stage;
static u32 g_stage_w, g_stage_h;
static u32 g_pic_mean;                                   /* the last picture handed over, 0..255 */
static u32 g_ck_shown, g_ck_black, g_ck_thread, g_ck_pics, g_ck_logged, g_ck_movies, g_ck_bars;

static int check_on(void)
{
    if (g_check < 0) {
        const char *v = host_arg("--movie-check");
        g_check = v && strcmp(v, "off") != 0;
    }
    return g_check;
}

static u32 grid_mean(const u8 *rows, u32 stride, u32 bpp, u32 x, u32 y, u32 w, u32 h)
{
    u64 sum = 0;
    for (u32 j = 0; j < CHECK_GY; j++) {
        const u8 *r = rows + (size_t)(y + (2u * j + 1u) * h / (2u * CHECK_GY)) * stride;
        for (u32 i = 0; i < CHECK_GX; i++) {
            const u8 *p = r + (size_t)(x + (2u * i + 1u) * w / (2u * CHECK_GX)) * bpp;
            sum += bpp == 1u ? p[0] : ((u32)p[0] + p[1] + p[2]) / 3u;
        }
    }
    return (u32)(sum / (CHECK_GX * CHECK_GY));
}

static void check_picture(const u8 *pixels, u32 stride, u32 bpp, u32 w, u32 h)
{
    if (!check_on()) return;
    g_pic_mean = grid_mean(pixels, stride, bpp, 0, 0, w, h);
    g_ck_pics++;
}

static void check_summary(void)
{
    if (!check_on() || (!g_ck_shown && !g_ck_pics)) return;
    port_log("movie-check: movie %u taken down at vertical blank %u: %u frame(s) shown, %u near-black where the picture "
             "is not (%u by the in-between thread), %u picture(s), %u with bars not black\n", ++g_ck_movies, port_vblank_count(), g_ck_shown,
             g_ck_black, g_ck_thread, g_ck_pics, g_ck_bars);
    g_ck_shown = g_ck_bars = g_ck_black = g_ck_thread = g_ck_pics = g_ck_logged = 0;
}

void gfx_movie_check_shown(void)
{
    D3D11_TEXTURE2D_DESC t;
    D3D11_MAPPED_SUBRESOURCE map;
    u32 scr[4], mean, bars;
    if (!check_on() || !g_on || !g_gfx.dev || !g_gfx.frame) return;
    if (!g_stage || g_stage_w != g_gfx.frame_w || g_stage_h != g_gfx.frame_h) {
        if (g_stage) ID3D11Texture2D_Release(g_stage);
        g_stage = NULL;
        memset(&t, 0, sizeof t);
        t.Width = g_gfx.frame_w;
        t.Height = g_gfx.frame_h;
        t.MipLevels = t.ArraySize = 1;
        t.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
        t.SampleDesc.Count = 1;
        t.Usage = D3D11_USAGE_STAGING;
        t.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
        if (FAILED(ID3D11Device_CreateTexture2D(g_gfx.dev, &t, NULL, &g_stage))) {
            port_log("movie-check: no read-back texture; off\n");
            g_check = 0;
            return;
        }
        g_stage_w = g_gfx.frame_w;
        g_stage_h = g_gfx.frame_h;
    }
    ID3D11DeviceContext_CopyResource(g_gfx.ctx, (ID3D11Resource *)g_stage, (ID3D11Resource *)g_gfx.frame);
    if (FAILED(ID3D11DeviceContext_Map(g_gfx.ctx, (ID3D11Resource *)g_stage, 0, D3D11_MAP_READ, 0, &map))) return;
    gfx_screen_rect(g_gfx.frame_w, g_gfx.frame_h, g_gfx.wide_k, g_gfx.tall_k, scr);
    mean = grid_mean((const u8 *)map.pData, map.RowPitch, 4u, scr[0], scr[1], scr[2], scr[3]);
    bars = gfx_bars_lit((const u8 *)map.pData, map.RowPitch, g_gfx.frame_w, g_gfx.frame_h, scr);
    ID3D11DeviceContext_Unmap(g_gfx.ctx, (ID3D11Resource *)g_stage, 0);
    g_ck_shown++;
    g_ck_bars += bars != 0u; /* a frame whose bars are not black while the movie is on */
    if (mean < 8u && g_pic_mean >= 24u) {
        int thread = gfx_on_present_thread();
        g_ck_black++;
        g_ck_thread += thread != 0;
        if (g_ck_logged++ < 40u)
            port_log("movie-check: near-black frame shown at vertical blank %u by %s (shown %u, picture %u)\n",
                     port_vblank_count(), thread ? "the in-between thread" : "the tick", mean, g_pic_mean);
    }
}

void gfx_movie_release(void)
{
    for (int i = 0; i < 3; i++) {
        if (g_srv[i]) ID3D11ShaderResourceView_Release(g_srv[i]);
        if (g_tex[i]) ID3D11Texture2D_Release(g_tex[i]);
        g_srv[i] = NULL;
        g_tex[i] = NULL;
    }
    if (g_rgba_srv) ID3D11ShaderResourceView_Release(g_rgba_srv);
    if (g_rgba_tex) ID3D11Texture2D_Release(g_rgba_tex);
    if (g_rgba_ps) ID3D11PixelShader_Release(g_rgba_ps);
    g_rgba_srv = NULL;
    g_rgba_tex = NULL;
    g_rgba_ps = NULL;
    g_rw = g_rh = 0;
    g_rgba_on = 0;
    if (g_ps) ID3D11PixelShader_Release(g_ps);
    if (g_sampler) ID3D11SamplerState_Release(g_sampler);
    if (g_rect) ID3D11Buffer_Release(g_rect);
    g_ps = NULL;
    g_sampler = NULL;
    g_rect = NULL;
    g_w = g_h = 0;
    g_on = 0;
    if (g_stage) ID3D11Texture2D_Release(g_stage);
    g_stage = NULL;
}

static int make_common(void);

static int make(u32 w, u32 h)
{
    ID3D11Device *dev = g_gfx.dev;
    D3D11_TEXTURE2D_DESC t;
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
    if (!make_common()) return 0;
    g_w = w;
    g_h = h;
    return 1;
}

/* The sampler and the rectangle's constant buffer, shared by both pictures. */
static int make_common(void)
{
    ID3D11Device *dev = g_gfx.dev;
    D3D11_SAMPLER_DESC s;
    D3D11_BUFFER_DESC b;
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
    return 1;
}

static int make_rgba(u32 w, u32 h)
{
    ID3D11Device *dev = g_gfx.dev;
    D3D11_TEXTURE2D_DESC t;
    if (g_rgba_srv) ID3D11ShaderResourceView_Release(g_rgba_srv);
    if (g_rgba_tex) ID3D11Texture2D_Release(g_rgba_tex);
    g_rgba_srv = NULL;
    g_rgba_tex = NULL;
    memset(&t, 0, sizeof t);
    t.Width = w;
    t.Height = h;
    t.MipLevels = t.ArraySize = 1;
    t.Format = DXGI_FORMAT_R8G8B8A8_UNORM; /* the renderer's texture format: pvr_decode's texels */
    t.SampleDesc.Count = 1;
    t.Usage = D3D11_USAGE_DEFAULT;
    t.BindFlags = D3D11_BIND_SHADER_RESOURCE;
    if (FAILED(ID3D11Device_CreateTexture2D(dev, &t, NULL, &g_rgba_tex)) ||
        FAILED(ID3D11Device_CreateShaderResourceView(dev, (ID3D11Resource *)g_rgba_tex, NULL, &g_rgba_srv)))
        return 0;
    if (!g_rgba_ps &&
        FAILED(ID3D11Device_CreatePixelShader(dev, g_gfx_movie_rgba_ps, sizeof g_gfx_movie_rgba_ps, NULL, &g_rgba_ps)))
        return 0;
    if (!make_common()) return 0;
    g_rw = w;
    g_rh = h;
    return 1;
}

static void movie_show_locked(const u8 *y, const u8 *cb, const u8 *cr, u32 y_stride, u32 c_stride, u32 w, u32 h)
{
    const u8 *planes[3] = {y, cb, cr};
    if (!g_gfx.dev || g_broken || w < 2u || h < 2u) return;
    if ((w != g_w || h != g_h) && !make(w, h)) {
        port_log("gfx: no movie textures; movies are not drawn\n");
        g_broken = 1;
        return;
    }
    check_picture(y, y_stride, 1u, w, h);
    for (int i = 0; i < 3; i++)
        ID3D11DeviceContext_UpdateSubresource(g_gfx.ctx, (ID3D11Resource *)g_tex[i], 0, NULL, planes[i],
                                              i ? c_stride : y_stride, 0);
    g_on = 1;
    g_rgba_on = 0;
}

void gfx_movie_hide(void)
{
    check_summary();
    g_on = 0;
}

int gfx_movie_on(void) { return g_on; }

void gfx_movie_draw(void)
{
    media_movie_frame(); /* the picture of the sound heard now (gfx_movie_show) */
    gfx_movie_lay();
}

/* The movie's current picture over the screen, without choosing a newer one: gfx_movie_draw, and the end of a render
 * under --present-thread (gfx_movie_lay_frame), so a frame the in-between thread shows again has the movie on it. */
void gfx_movie_lay(void)
{
    ID3D11DeviceContext *cx = g_gfx.ctx;
    ID3D11VertexShader *vs;
    ID3D11ShaderResourceView *none[3] = {NULL, NULL, NULL};
    D3D11_VIEWPORT vp;
    float rect[4];
    u32 scr[4];
    if (!g_on || !g_gfx.frame_rtv) return;
    vs = pvr_full_vs();
    if (!vs) return;
    /* the 640x480 screen: 4:3, in the middle of the frame */
    gfx_screen_rect(g_gfx.frame_w, g_gfx.frame_h, g_gfx.wide_k, g_gfx.tall_k, scr);
    for (int i = 0; i < 4; i++) rect[i] = (float)scr[i];
    memset(&vp, 0, sizeof vp);
    vp.TopLeftX = rect[0];
    vp.TopLeftY = rect[1];
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
    if (g_rgba_on) {
        ID3D11DeviceContext_PSSetShader(cx, g_rgba_ps, NULL, 0);
        ID3D11DeviceContext_PSSetShaderResources(cx, 3, 1, &g_rgba_srv);
    } else {
        ID3D11DeviceContext_PSSetShader(cx, g_ps, NULL, 0);
        ID3D11DeviceContext_PSSetShaderResources(cx, 0, 3, g_srv);
    }
    ID3D11DeviceContext_PSSetSamplers(cx, 0, 1, &g_sampler);
    ID3D11DeviceContext_PSSetConstantBuffers(cx, 0, 1, &g_rect);
    ID3D11DeviceContext_Draw(cx, 3, 0);
    ID3D11DeviceContext_PSSetShaderResources(cx, 0, 3, none);
    ID3D11DeviceContext_PSSetShaderResources(cx, 3, 1, none);
}

void gfx_movie_show(const u8 *y, const u8 *cb, const u8 *cr, u32 y_stride, u32 c_stride, u32 w, u32 h)
{
    gfx_lock();
    movie_show_locked(y, cb, cr, y_stride, c_stride, w, h);
    gfx_unlock();
}

/* platform.h: a game's own decoded movie picture, YUV422 as it would be loaded into texture memory (stride bytes a
 * row), drawn over the screen from the next frame until port_movie_hide. Converted outside the lock. */
void port_movie_show_yuv422(const void *pixels, u32 w, u32 h, u32 stride)
{
    if (!pixels || w < 2u || h < 2u || stride < w * 2u) return;
    if (w * h > g_rgba_cap) {
        u32 *rows = (u32 *)realloc(g_rgba, (size_t)w * h * 4u);
        if (!rows) return;
        g_rgba = rows;
        g_rgba_cap = w * h;
    }
    pvr_yuv422_rgba((const u8 *)pixels, w, h, stride, g_rgba);
    check_picture((const u8 *)g_rgba, w * 4u, 4u, w, h);
    gfx_lock();
    if (g_gfx.dev && !g_broken) {
        if ((w != g_rw || h != g_rh) && !make_rgba(w, h)) {
            port_log("gfx: no movie texture; movies are not drawn\n");
            g_broken = 1;
        } else {
            ID3D11DeviceContext_UpdateSubresource(g_gfx.ctx, (ID3D11Resource *)g_rgba_tex, 0, NULL, g_rgba, w * 4u, 0);
            g_on = 1;
            g_rgba_on = 1;
        }
    }
    gfx_unlock();
}

void port_movie_hide(void)
{
    gfx_lock();
    check_summary();
    g_on = 0;
    g_rgba_on = 0;
    gfx_unlock();
}

/* --no-window on the command line: a play run that opens nothing (host_main.cpp), unlike --headless. */
static int no_window(void)
{
    int argc = 0;
    char *const *argv = host_args(&argc);
    for (int i = 1; i < argc; i++)
        if (!strcmp(argv[i], "--no-window")) return 1;
    return 0;
}

/* platform.h: whether this movie's picture is drawn natively; a game asks once as each movie starts. A play run
 * (real clock, not a replay, a window or --no-window) follows --movie-native (on unless off); a check run keeps the
 * emulated renderer unless --movie-native always. The choice goes in the recording (port_play_option), so a replay of
 * a play session draws as it was played. */
int port_movie_native(void)
{
    int play_run, live;
    PortView v;
    if (g_native_want < 0) {
        const char *want = host_arg("--movie-native");
        g_native_want = !want ? 1 : !strcmp(want, "always") ? 2 : strcmp(want, "off") != 0;
    }
    console_view(&v);
    play_run = !host_fixed_clock() && !session_replaying() && (!v.headless || no_window());
    live = g_native_want == 2 || (g_native_want == 1 && play_run);
    live = port_play_option("movie-native", live) != 0;
    port_log("movie: picture by %s\n", live ? "the kit (native)" : "the emulated renderer");
    return live;
}
