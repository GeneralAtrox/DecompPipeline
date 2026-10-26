/* The Direct3D 11 device and its swap chain on the host's window (host/host_window.cpp: full screen borderless at
 * the desktop mode by default, --window for a window). The device is on the high-performance adapter (--warp: the
 * WARP software rasterizer, the same images on any machine); --headless opens no window and draws only the frame
 * target. Everything runs on the game thread, which is the thread that made the window. */
#include <stdlib.h>
#include <string.h>
#include "gfx.h"
#include "platform.h"
#include "host.h"

GfxState g_gfx = {.wide = -1};

void gfx_set_vsync(int on) { g_gfx.vsync = on; }

int gfx_vsync(void) { return g_gfx.vsync; }

static int parse_size(const char *v, u32 *w, u32 *h)
{
    char *end;
    unsigned long x = strtoul(v, &end, 10);
    if (*end != 'x' && *end != 'X') return 0;
    unsigned long y = strtoul(end + 1, &end, 10);
    if (*end || x < 64 || y < 48 || x > 16384 || y > 16384) return 0;
    *w = (u32)x;
    *h = (u32)y;
    return 1;
}

int gfx_option(const char *arg, const char *value)
{
    if (!strcmp(arg, "--headless")) return g_gfx.headless = 1;
    if (!strcmp(arg, "--warp")) return g_gfx.warp = 1;
    if (!strcmp(arg, "--vsync") && value) {
        g_gfx.vsync = strcmp(value, "off") != 0;
        return 2;
    }
    if (host_window_option(arg, value)) return 2;
    if (!strcmp(arg, "--widescreen") && value) {
        g_gfx.wide = strcmp(value, "off") != 0;
        return 2;
    }
    if (!strcmp(arg, "--size") && value && parse_size(value, &g_gfx.out_w, &g_gfx.out_h)) return 2;
    /* look-review prototypes (PHASE_D section 9.5), off by default */
    if (!strcmp(arg, "--ssaa") && value) {
        g_gfx.ssaa = (u32)strtoul(value, NULL, 10);
        return 2;
    }
    if (!strcmp(arg, "--aniso") && value) {
        g_gfx.aniso = (u32)strtoul(value, NULL, 10);
        if (g_gfx.aniso > 16u) g_gfx.aniso = 16u;
        return 2;
    }
    return gfx_capture_option(arg, value);
}

static IDXGIAdapter1 *high_performance_adapter(IDXGIFactory6 *f6)
{
    IDXGIAdapter1 *a = NULL;
    if (f6 && SUCCEEDED(IDXGIFactory6_EnumAdapterByGpuPreference(f6, 0, DXGI_GPU_PREFERENCE_HIGH_PERFORMANCE,
                                                                 &IID_IDXGIAdapter1, (void **)&a)))
        return a;
    return NULL;
}

static int create_device(IDXGIFactory2 *factory)
{
    static const D3D_FEATURE_LEVEL levels[] = {D3D_FEATURE_LEVEL_11_1, D3D_FEATURE_LEVEL_11_0};
    IDXGIFactory6 *f6 = NULL;
    IDXGIAdapter1 *adapter = NULL;
    D3D_FEATURE_LEVEL got;
    HRESULT hr;
    if (!g_gfx.warp && SUCCEEDED(IDXGIFactory2_QueryInterface(factory, &IID_IDXGIFactory6, (void **)&f6))) {
        adapter = high_performance_adapter(f6);
        IDXGIFactory6_Release(f6);
    }
    hr = D3D11CreateDevice((IDXGIAdapter *)adapter,
                           g_gfx.warp ? D3D_DRIVER_TYPE_WARP : adapter ? D3D_DRIVER_TYPE_UNKNOWN : D3D_DRIVER_TYPE_HARDWARE,
                           NULL, 0, levels, 2, D3D11_SDK_VERSION, &g_gfx.dev, &got, &g_gfx.ctx);
    if (adapter) {
        DXGI_ADAPTER_DESC1 d;
        if (SUCCEEDED(IDXGIAdapter1_GetDesc1(adapter, &d)))
            port_log("gfx: adapter %ls, %u MB\n", d.Description, (u32)(d.DedicatedVideoMemory >> 20));
        IDXGIAdapter1_Release(adapter);
    }
    if (FAILED(hr)) {
        port_log("gfx: D3D11CreateDevice failed (%08lx)\n", (unsigned long)hr);
        return 0;
    }
    port_log("gfx: Direct3D 11 device, feature level %x%s\n", (u32)got, g_gfx.warp ? " (WARP)" : "");
    return 1;
}

static int create_swap_chain(IDXGIFactory2 *factory)
{
    HWND hwnd = (HWND)host_window_hwnd();
    IDXGIFactory5 *f5 = NULL;
    BOOL tearing = FALSE;
    DXGI_SWAP_CHAIN_DESC1 d;
    if (SUCCEEDED(IDXGIFactory2_QueryInterface(factory, &IID_IDXGIFactory5, (void **)&f5))) {
        if (FAILED(IDXGIFactory5_CheckFeatureSupport(f5, DXGI_FEATURE_PRESENT_ALLOW_TEARING, &tearing, sizeof tearing)))
            tearing = FALSE;
        IDXGIFactory5_Release(f5);
    }
    memset(&d, 0, sizeof d);
    d.Width = g_gfx.out_w;
    d.Height = g_gfx.out_h;
    d.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
    d.SampleDesc.Count = 1;
    d.BufferUsage = DXGI_USAGE_RENDER_TARGET_OUTPUT;
    d.BufferCount = 2;
    d.SwapEffect = DXGI_SWAP_EFFECT_FLIP_DISCARD;
    /* waitable: a present never has to block the game (gfx_show drops a frame instead) */
    d.Flags = (tearing ? DXGI_SWAP_CHAIN_FLAG_ALLOW_TEARING : 0u) | DXGI_SWAP_CHAIN_FLAG_FRAME_LATENCY_WAITABLE_OBJECT;
    HRESULT hr = IDXGIFactory2_CreateSwapChainForHwnd(factory, (IUnknown *)g_gfx.dev, hwnd, &d, NULL, NULL,
                                                      &g_gfx.swap);
    if (FAILED(hr)) {
        port_log("gfx: CreateSwapChainForHwnd failed (%08lx)\n", (unsigned long)hr);
        return 0;
    }
    IDXGIFactory2_MakeWindowAssociation(factory, hwnd, DXGI_MWA_NO_ALT_ENTER);
    g_gfx.tearing = tearing != FALSE;
    g_gfx.swap_flags = d.Flags;
    IDXGISwapChain2 *s2 = NULL;
    if (SUCCEEDED(IDXGISwapChain1_QueryInterface(g_gfx.swap, &IID_IDXGISwapChain2, (void **)&s2))) {
        IDXGISwapChain2_SetMaximumFrameLatency(s2, 2);
        g_gfx.latency = IDXGISwapChain2_GetFrameLatencyWaitableObject(s2);
        IDXGISwapChain2_Release(s2);
    }
    return 1;
}

int gfx_init(void)
{
    IDXGIFactory2 *factory = NULL;
    int ok;
    if (g_gfx.headless) {
        if (!g_gfx.out_w) {
            g_gfx.out_w = 640;
            g_gfx.out_h = 480;
        }
    } else if (!host_window_open(PORT_GAME_NAME, &g_gfx.out_w, &g_gfx.out_h, &g_gfx.fullscreen)) {
        return 0;
    }
    if (FAILED(CreateDXGIFactory2(0, &IID_IDXGIFactory2, (void **)&factory))) {
        port_log("gfx: CreateDXGIFactory2 failed\n");
        return 0;
    }
    ok = create_device(factory) && (g_gfx.headless || create_swap_chain(factory)) &&
         gfx_resize(g_gfx.out_w, g_gfx.out_h);
    IDXGIFactory2_Release(factory);
    if (ok)
        port_log("gfx: output %ux%u, frame %ux%u%s\n", g_gfx.out_w, g_gfx.out_h, g_gfx.frame_w, g_gfx.frame_h,
                 g_gfx.headless ? " (headless)" : g_gfx.tearing ? ", tearing allowed" : "");
    if (ok && (g_gfx.draw != g_gfx.frame || g_gfx.aniso > 1u))
        port_log("gfx: look prototypes: drawing at %ux%u, anisotropic filtering %u\n", g_gfx.draw_w, g_gfx.draw_h,
                 g_gfx.aniso > 1u ? g_gfx.aniso : 0u);
    return ok;
}
