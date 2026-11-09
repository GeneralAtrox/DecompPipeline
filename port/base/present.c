/* The frame target and presenting it. The frame is the chosen shape (gfx_aspect.c: 4:3, 16:9, 21:9, any W:H, or the
 * window's own) as large as the output allows (4:3 on a 2560x1600 screen: 2133x1600); presenting clears the back
 * buffer to the border colour and copies the frame into its middle (pillarbox or letterbox), then presents with the swap chain's sync interval 0, with tearing where allowed (--vsync
 * on: 1). Headless runs only count the frame. With --ssaa K the renderer draws into a K-times larger target whose
 * mips are built after each render; mip log2(K) is then exactly frame-sized, the box-filtered average of K x K
 * samples, and is copied into the frame. */
#include <string.h>
#include "present.h"
#include "console.h"
#include "host.h"
#include "work_count.h"
#include "base.h"

static ID3D11RenderTargetView *g_back_rtv;
ID3D11RenderTargetView *gfx_back_rtv(void) { return g_back_rtv; }
static u32 g_border = 0xff000000u;

void gfx_set_border(u32 argb) { g_border = argb; }
u32 gfx_border(void) { return g_border; }

/* The frame's shape. Not chosen (--aspect, the menu): a game that supports a wider view (game.json "widescreen")
 * follows the window in a play run, and everything else is 4:3, as is every check (headless or on the fixed clock:
 * regress goldens, lockstep, emulator comparisons). The game's own culling widens by port_widescreen() and
 * port_tallscreen(), which are 1 at 4:3. */
f32 gfx_aspect(void)
{
    if (g_gfx.aspect_set) return g_gfx.aspect;
    return console_widens() && !g_gfx.headless && !host_fixed_clock() ? 0.0f : 4.0f / 3.0f;
}

/* The menu's and the settings file's choice, taking effect at once. */
int gfx_set_aspect(const char *text)
{
    f32 r;
    if (!gfx_aspect_parse(text, &r)) return 0;
    g_gfx.aspect = r;
    g_gfx.aspect_set = 1;
    if (g_gfx.frame) gfx_resize(g_gfx.out_w, g_gfx.out_h);
    return 1;
}

/* The shape in use, in the settings file's words. */
const char *gfx_aspect_text(void)
{
    static char buf[16];
    return gfx_aspect_name(gfx_aspect(), buf);
}

f32 port_widescreen(void) { return g_gfx.wide_k > 1.0f ? g_gfx.wide_k : 1.0f; }
f32 port_tallscreen(void) { return g_gfx.tall_k > 1.0f ? g_gfx.tall_k : 1.0f; }
/* The shape was chosen (--aspect, the menu) or follows the window: anything but a check at its original 4:3. */
int port_shape_chosen(void) { return g_gfx.aspect_set || gfx_aspect() == 0.0f; }

/* The 640x480 screen is the frame's middle, so a view centred on it (the full screen, or a wipe opening from the
 * centre) grows by the same share: W by W * (wide - 1), H by H * (tall - 1), half to each side. A view off centre
 * (a render target's) keeps its own. A call inside a widened one (a renderer calling its twin) leaves it as it is. */
static int g_wide_cull_depth;

static int wide_centred(f32 at, f32 size, f32 screen)
{
    f32 d = at + size * 0.5f - screen * 0.5f;
    return d > -0.5f && d < 0.5f;
}

int port_wide_cull_begin(u32 viewport, f32 saved[4])
{
    f32 *v = (f32 *)(uintptr_t)viewport; /* W, H, X, Y */
    f32 k = port_widescreen() - 1.0f, ky = port_tallscreen() - 1.0f;
    f32 side, over;
    int i;
    if (g_wide_cull_depth || (k <= 0.0f && ky <= 0.0f) || !wide_centred(v[2], v[0], 640.0f)
        || !wide_centred(v[3], v[1], 480.0f)) {
        return 0;
    }
    for (i = 0; i < 4; i++) saved[i] = v[i];
    side = v[0] * 0.5f * k;
    over = v[1] * 0.5f * ky;
    v[0] += 2.0f * side;
    v[1] += 2.0f * over;
    v[2] -= side;
    v[3] -= over;
    g_wide_cull_depth = 1;
    return 1;
}

void port_wide_cull_end(u32 viewport, const f32 saved[4])
{
    f32 *v = (f32 *)(uintptr_t)viewport;
    int i;
    for (i = 0; i < 4; i++) v[i] = saved[i];
    g_wide_cull_depth = 0;
}

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

int gfx_resize(unsigned out_w, unsigned out_h)
{
    D3D11_TEXTURE2D_DESC d;
    GfxFrameShape sh;
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
    gfx_frame_shape(out_w, out_h, gfx_aspect(), &sh); /* the largest rectangle of the shape, centred */
    g_gfx.frame_w = sh.w;
    g_gfx.frame_h = sh.h;
    g_gfx.frame_x = sh.x;
    g_gfx.frame_y = sh.y;
    g_gfx.wide_k = sh.wide_k;
    g_gfx.tall_k = sh.tall_k;
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
    gfx_lock();
    ID3D11DeviceContext_ClearRenderTargetView(g_gfx.ctx, g_gfx.frame_rtv, c);
    gfx_unlock();
}

/* The latency object counts frames the swap chain can take; one taken here is kept for the next gfx_show. */
static int g_can_show;
int gfx_can_show(void)
{
    if (!g_gfx.latency || g_can_show) return 1;
    g_can_show = WaitForSingleObject(g_gfx.latency, 0) == WAIT_OBJECT_0;
    return g_can_show;
}

/* --present-thread: waits a moment for the display to take a frame, holding no lock. */
void gfx_show_wait(void)
{
    if (g_gfx.latency && !g_can_show && WaitForSingleObject(g_gfx.latency, 2) == WAIT_OBJECT_0) g_can_show = 1;
}

static double g_fps;

double gfx_presented_fps(void) { return g_fps; }

/* The frame-rate readout in the log (full screen has no title bar to count in): every 5 s of real time, the frames
 * presented per second, the longest gap between two, where a stutter shows, and the longest Present itself: when it is
 * near the gap, the driver or the graphics card held the frame, not the game. The performance counter is read
 * directly: host_ticks would move the virtual clock. */
static LONGLONG g_present_worst, g_present_last;

/* --present-cost (runtime/frame_cost.c): how long the last Present call took, in microseconds. */
u64 gfx_last_present_us(void)
{
    LARGE_INTEGER f;
    QueryPerformanceFrequency(&f);
    return (u64)g_present_last * 1000000u / (u64)f.QuadPart;
}

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
    port_log("gfx: %.1f frames a second presented over %.1f s, longest gap %.1f ms (%ux%u %s, vsync %s, longest Present %.1f ms, vblank %u)\n",
             shown / secs, secs, (double)worst * 1000.0 / (double)freq.QuadPart, g_gfx.out_w, g_gfx.out_h,
             host_window_state(), g_gfx.vsync ? "on" : "off", (double)g_present_worst * 1000.0 / (double)freq.QuadPart,
             port_vblank_count());
    start = now;
    shown = 0;
    worst = 0;
    g_present_worst = 0;
    static unsigned waits0;
    static unsigned long long us0;
    unsigned n;
    unsigned long long us;
    gfx_lock_waits(&n, &us);
    if (n != waits0)
        port_log("gfx: the game's thread waited %u time(s) for the device context in those %.1f s, %.1f ms in all\n", n - waits0,
                 secs, (double)(us - us0) / 1000.0);
    waits0 = n;
    us0 = us;
}

static void show_inner(void)
{
    float border[4];
    ID3D11Resource *back = NULL;
    work_add(WC_PRESENTS, 1);
    if (!g_gfx.dev) return;
    if (gfx_present_stall_us()) pace_sleep_us(gfx_present_stall_us()); /* --present-stall-us: a test's slow Present */
    /* the swap chain still holds as many frames as it may queue (a hidden or throttled window): this one is dropped,
     * since presenting it would block the game */
    if (!g_gfx.swap || !g_back_rtv || !gfx_can_show()) {
        if (g_gfx.swap && g_back_rtv && g_gfx.dropped++ % 600u == 0u)
            port_log("gfx: %u frame(s) dropped: the display is not taking them (vblank %u)\n", g_gfx.dropped,
                     port_vblank_count());
        if (!gfx_on_present_thread()) gfx_pump();
        return;
    }
    g_can_show = 0;
    argb_to_float(g_border | 0xff000000u, border);
    ID3D11DeviceContext_ClearRenderTargetView(g_gfx.ctx, g_back_rtv, border);
    ID3D11RenderTargetView_GetResource(g_back_rtv, &back);
    ID3D11DeviceContext_CopySubresourceRegion(g_gfx.ctx, back, 0, g_gfx.frame_x, g_gfx.frame_y, 0,
                                              (ID3D11Resource *)g_gfx.frame, 0, NULL);
    console_present_over(back);
    ID3D11Resource_Release(back);
    /* VSync off presents at once, tearing where the swap chain allows it (variable refresh). While the window cannot
     * be seen, a synced present can block far longer than a refresh, so only a test present is made then. */
    static int occluded;
    UINT flags = !g_gfx.vsync && g_gfx.tearing ? DXGI_PRESENT_ALLOW_TEARING : 0u;
    LARGE_INTEGER before, after;
    QueryPerformanceCounter(&before);
    HRESULT hr = IDXGISwapChain1_Present(g_gfx.swap, occluded ? 0u : g_gfx.vsync ? 1u : 0u,
                                         occluded ? DXGI_PRESENT_TEST : flags);
    QueryPerformanceCounter(&after);
    g_present_last = after.QuadPart - before.QuadPart;
    if (g_present_last > g_present_worst) g_present_worst = g_present_last;
    if (FAILED(hr)) port_exit("the display device was lost", 4);
    if ((hr == DXGI_STATUS_OCCLUDED) != occluded) {
        occluded = hr == DXGI_STATUS_OCCLUDED;
        port_log("gfx: the window is %s\n", occluded ? "hidden: frames are not presented" : "visible again");
    }
    if (!occluded) note_presented();
    host_window_frame();
    if (!gfx_on_present_thread()) gfx_pump();
}

void present_show(void) { show_inner(); }

void gfx_shutdown(void)
{
    gfx_lock(); /* kept to the end of the process: the thread that shows in-between frames draws no more */
    if (g_gfx.dropped) port_log("gfx: %u frame(s) dropped in all\n", g_gfx.dropped);
    unsigned n;
    unsigned long long us;
    gfx_lock_waits(&n, &us);
    if (n) port_log("gfx: the game's thread waited %u time(s) for the device context in all, %.1f ms\n", n, (double)us / 1000.0);
    if (g_gfx.latency) CloseHandle(g_gfx.latency);
    g_gfx.latency = NULL;
    release_frame();
    console_present_release();
    if (g_gfx.swap) IDXGISwapChain1_Release(g_gfx.swap);
    if (g_gfx.ctx) ID3D11DeviceContext_Release(g_gfx.ctx);
    if (g_gfx.dev) ID3D11Device_Release(g_gfx.dev);
    g_gfx.swap = NULL;
    g_gfx.ctx = NULL;
    g_gfx.dev = NULL;
}
