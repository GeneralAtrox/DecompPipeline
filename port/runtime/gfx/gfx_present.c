/* The frame target and presenting it. The frame is the 4:3 picture as large as the output allows (2133x1600 on a
 * 2560x1600 screen); presenting clears the back buffer to the border colour and copies the frame into its middle
 * (pillarbox or letterbox), then presents with the swap chain's sync interval 0, with tearing where allowed (--vsync
 * on: 1). Headless runs only count the frame. With --ssaa K the renderer draws into a K-times larger target whose
 * mips are built after each render; mip log2(K) is then exactly frame-sized, the box-filtered average of K x K
 * samples, and is copied into the frame. */
#include <string.h>
#include "gfx.h"
#include "platform.h"
#include "host.h"
#include "scene_dump.h"

static ID3D11RenderTargetView *g_back_rtv;
static u32 g_border = 0xff000000u;

void gfx_set_border(u32 argb) { g_border = argb; }

/* Widescreen is for play: a game that supports it (game.json "widescreen") draws it unless --widescreen off, or the
 * run is a check (headless or on the fixed clock: regress goldens, lockstep, emulator comparisons) and --widescreen
 * on did not ask for it. */
static int wide_on(void)
{
    return PORT_WIDESCREEN && (g_gfx.wide > 0 || (g_gfx.wide < 0 && !g_gfx.headless && !host_fixed_clock()));
}

f32 port_widescreen(void) { return g_gfx.wide_k > 1.0f ? g_gfx.wide_k : 1.0f; }

static void release_frame(void)
{
    if (g_gfx.draw != g_gfx.frame) {
        if (g_gfx.draw_srv) ID3D11ShaderResourceView_Release(g_gfx.draw_srv);
        if (g_gfx.draw_rtv) ID3D11RenderTargetView_Release(g_gfx.draw_rtv);
        if (g_gfx.draw) ID3D11Texture2D_Release(g_gfx.draw);
    }
    g_gfx.draw_srv = NULL;
    g_gfx.draw_rtv = NULL;
    g_gfx.draw = NULL;
    if (g_gfx.frame_rtv) ID3D11RenderTargetView_Release(g_gfx.frame_rtv);
    if (g_gfx.frame) ID3D11Texture2D_Release(g_gfx.frame);
    if (g_back_rtv) ID3D11RenderTargetView_Release(g_back_rtv);
    g_gfx.frame_rtv = NULL;
    g_gfx.frame = NULL;
    g_back_rtv = NULL;
}

static int create_back_rtv(void)
{
    ID3D11Texture2D *back = NULL;
    HRESULT hr = IDXGISwapChain1_GetBuffer(g_gfx.swap, 0, &IID_ID3D11Texture2D, (void **)&back);
    if (SUCCEEDED(hr)) {
        hr = ID3D11Device_CreateRenderTargetView(g_gfx.dev, (ID3D11Resource *)back, NULL, &g_back_rtv);
        ID3D11Texture2D_Release(back);
    }
    if (FAILED(hr)) port_log("gfx: no back buffer view (%08lx)\n", (unsigned long)hr);
    return SUCCEEDED(hr);
}

static u32 ssaa_mip(void) { return g_gfx.ssaa == 4u ? 2u : g_gfx.ssaa == 2u ? 1u : 0u; }

static int create_draw(void)
{
    D3D11_TEXTURE2D_DESC d;
    HRESULT hr;
    g_gfx.draw_w = g_gfx.frame_w << ssaa_mip();
    g_gfx.draw_h = g_gfx.frame_h << ssaa_mip();
    if (!ssaa_mip()) {
        g_gfx.draw = g_gfx.frame;
        g_gfx.draw_rtv = g_gfx.frame_rtv;
        return 1;
    }
    ID3D11Texture2D_GetDesc(g_gfx.frame, &d);
    d.Width = g_gfx.draw_w;
    d.Height = g_gfx.draw_h;
    d.MipLevels = ssaa_mip() + 1u;
    d.MiscFlags = D3D11_RESOURCE_MISC_GENERATE_MIPS;
    hr = ID3D11Device_CreateTexture2D(g_gfx.dev, &d, NULL, &g_gfx.draw);
    if (SUCCEEDED(hr))
        hr = ID3D11Device_CreateRenderTargetView(g_gfx.dev, (ID3D11Resource *)g_gfx.draw, NULL, &g_gfx.draw_rtv);
    if (SUCCEEDED(hr))
        hr = ID3D11Device_CreateShaderResourceView(g_gfx.dev, (ID3D11Resource *)g_gfx.draw, NULL, &g_gfx.draw_srv);
    if (FAILED(hr)) {
        port_log("gfx: no %ux%u supersampled target (%08lx)\n", g_gfx.draw_w, g_gfx.draw_h, (unsigned long)hr);
        return 0;
    }
    return 1;
}

void gfx_resolve(void)
{
    if (!g_gfx.draw_srv) return;
    ID3D11DeviceContext_GenerateMips(g_gfx.ctx, g_gfx.draw_srv);
    ID3D11DeviceContext_CopySubresourceRegion(g_gfx.ctx, (ID3D11Resource *)g_gfx.frame, 0, 0, 0, 0,
                                              (ID3D11Resource *)g_gfx.draw, ssaa_mip(), NULL);
}

int gfx_resize(u32 out_w, u32 out_h)
{
    D3D11_TEXTURE2D_DESC d;
    HRESULT hr;
    release_frame();
    if (g_gfx.swap) {
        hr = IDXGISwapChain1_ResizeBuffers(g_gfx.swap, 0, out_w, out_h, DXGI_FORMAT_UNKNOWN, g_gfx.swap_flags);
        if (FAILED(hr)) {
            port_log("gfx: ResizeBuffers %ux%u failed (%08lx)\n", out_w, out_h, (unsigned long)hr);
            return 0;
        }
        if (!create_back_rtv()) return 0;
    }
    g_gfx.out_w = out_w;
    g_gfx.out_h = out_h;
    /* the largest 4:3 rectangle (widescreen: as wide as the output, up to 16:9), centred */
    g_gfx.frame_h = out_h;
    g_gfx.frame_w = wide_on() ? (out_h * 16u + 4u) / 9u : (out_h * 4u + 1u) / 3u;
    if (g_gfx.frame_w > out_w && wide_on() && out_w * 3u >= out_h * 4u) {
        g_gfx.frame_w = out_w;
    } else if (g_gfx.frame_w > out_w) {
        g_gfx.frame_w = out_w;
        g_gfx.frame_h = (out_w * 3u + 2u) / 4u;
    }
    g_gfx.frame_x = (out_w - g_gfx.frame_w) / 2u;
    g_gfx.wide_k = wide_on() ? (f32)(g_gfx.frame_w * 3u) / (f32)(g_gfx.frame_h * 4u) : 1.0f;
    g_gfx.frame_y = (out_h - g_gfx.frame_h) / 2u;
    memset(&d, 0, sizeof d);
    d.Width = g_gfx.frame_w;
    d.Height = g_gfx.frame_h;
    d.MipLevels = 1;
    d.ArraySize = 1;
    d.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
    d.SampleDesc.Count = 1;
    d.Usage = D3D11_USAGE_DEFAULT;
    d.BindFlags = D3D11_BIND_RENDER_TARGET | D3D11_BIND_SHADER_RESOURCE;
    hr = ID3D11Device_CreateTexture2D(g_gfx.dev, &d, NULL, &g_gfx.frame);
    if (SUCCEEDED(hr))
        hr = ID3D11Device_CreateRenderTargetView(g_gfx.dev, (ID3D11Resource *)g_gfx.frame, NULL, &g_gfx.frame_rtv);
    if (FAILED(hr)) {
        port_log("gfx: no %ux%u frame target (%08lx)\n", g_gfx.frame_w, g_gfx.frame_h, (unsigned long)hr);
        return 0;
    }
    return create_draw();
}

static void argb_to_float(u32 argb, float out[4])
{
    out[0] = (float)((argb >> 16) & 0xffu) / 255.0f;
    out[1] = (float)((argb >> 8) & 0xffu) / 255.0f;
    out[2] = (float)(argb & 0xffu) / 255.0f;
    out[3] = (float)(argb >> 24) / 255.0f;
}

void gfx_clear(u32 argb)
{
    float c[4];
    if (!g_gfx.frame_rtv) return;
    argb_to_float(argb | 0xff000000u, c);
    ID3D11DeviceContext_ClearRenderTargetView(g_gfx.ctx, g_gfx.frame_rtv, c);
}

void gfx_frame_done(void)
{
    if (!g_gfx.dev) return;
    gfx_capture_frame(g_gfx.frames);
    scene_dump_poll(g_gfx.frames);
    g_gfx.frames++;
}

/* The latency object counts frames the swap chain can take; one taken here is kept for the next gfx_show. */
static int g_can_show;
int gfx_can_show(void)
{
    if (!g_gfx.latency || g_can_show) return 1;
    g_can_show = WaitForSingleObject(g_gfx.latency, 0) == WAIT_OBJECT_0;
    return g_can_show;
}

void gfx_present(void)
{
    gfx_frame_done();
    gfx_show();
}

static double g_fps;

double gfx_presented_fps(void) { return g_fps; }

/* The frame-rate readout in the log (full screen has no title bar to count in): every 5 s of real time, the frames
 * presented per second and the longest gap between two, where a stutter shows. The performance counter is read
 * directly: host_ticks would move the virtual clock. */
static void note_presented(void)
{
    static LARGE_INTEGER freq, start, last;
    static LONGLONG worst;
    static u32 shown;
    LARGE_INTEGER now;
    QueryPerformanceCounter(&now);
    if (!freq.QuadPart) {
        QueryPerformanceFrequency(&freq);
        start = last = now;
        return;
    }
    if (now.QuadPart - last.QuadPart > worst) worst = now.QuadPart - last.QuadPart;
    last = now;
    shown++;
    if (now.QuadPart - start.QuadPart < 5 * freq.QuadPart) return;
    double secs = (double)(now.QuadPart - start.QuadPart) / (double)freq.QuadPart;
    g_fps = shown / secs;
    port_log("gfx: %.1f frames a second presented over %.1f s, longest gap %.1f ms (%ux%u %s, vsync %s, vblank %u)\n",
             shown / secs, secs, (double)worst * 1000.0 / (double)freq.QuadPart, g_gfx.out_w, g_gfx.out_h,
             host_window_state(), g_gfx.vsync ? "on" : "off", port_vblank_count());
    start = now;
    shown = 0;
    worst = 0;
}

void gfx_show(void)
{
    float border[4];
    ID3D11Resource *back = NULL;
    if (!g_gfx.dev) return;
    /* the swap chain still holds as many frames as it may queue (a hidden or throttled window): this one is dropped,
     * since presenting it would block the game */
    if (!g_gfx.swap || !g_back_rtv || !gfx_can_show()) {
        if (g_gfx.swap && g_back_rtv && g_gfx.dropped++ % 600u == 0u)
            port_log("gfx: %u frame(s) dropped: the display is not taking them (vblank %u)\n", g_gfx.dropped,
                     port_vblank_count());
        gfx_pump();
        return;
    }
    g_can_show = 0;
    argb_to_float(g_border | 0xff000000u, border);
    ID3D11DeviceContext_ClearRenderTargetView(g_gfx.ctx, g_back_rtv, border);
    ID3D11RenderTargetView_GetResource(g_back_rtv, &back);
    ID3D11DeviceContext_CopySubresourceRegion(g_gfx.ctx, back, 0, g_gfx.frame_x, g_gfx.frame_y, 0,
                                              (ID3D11Resource *)g_gfx.frame, 0, NULL);
    ID3D11Resource_Release(back);
    /* VSync off presents at once, tearing where the swap chain allows it (variable refresh). While the window cannot
     * be seen, a synced present can block far longer than a refresh, so only a test present is made then. */
    static int occluded;
    UINT flags = !g_gfx.vsync && g_gfx.tearing ? DXGI_PRESENT_ALLOW_TEARING : 0u;
    HRESULT hr = IDXGISwapChain1_Present(g_gfx.swap, occluded ? 0u : g_gfx.vsync ? 1u : 0u,
                                         occluded ? DXGI_PRESENT_TEST : flags);
    if (FAILED(hr)) port_exit("the display device was lost", 4);
    if ((hr == DXGI_STATUS_OCCLUDED) != occluded) {
        occluded = hr == DXGI_STATUS_OCCLUDED;
        port_log("gfx: the window is %s\n", occluded ? "hidden: frames are not presented" : "visible again");
    }
    if (!occluded) note_presented();
    host_window_frame();
    gfx_pump();
}

void gfx_shutdown(void)
{
    if (g_gfx.dropped) port_log("gfx: %u frame(s) dropped in all\n", g_gfx.dropped);
    if (g_gfx.latency) CloseHandle(g_gfx.latency);
    g_gfx.latency = NULL;
    release_frame();
    if (g_gfx.swap) IDXGISwapChain1_Release(g_gfx.swap);
    if (g_gfx.ctx) ID3D11DeviceContext_Release(g_gfx.ctx);
    if (g_gfx.dev) ID3D11Device_Release(g_gfx.dev);
    g_gfx.swap = NULL;
    g_gfx.ctx = NULL;
    g_gfx.dev = NULL;
}
