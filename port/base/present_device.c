/* The Direct3D 11 device and its swap chain on the host's window (base/host_window.cpp: full screen borderless at
 * the desktop mode by default, --window for a window). The device is on the high-performance adapter (--warp: the
 * WARP software rasterizer, the same images on any machine); --headless opens no window and draws only the frame
 * target. Everything runs on the game thread, which is the thread that made the window. */
#include <stdlib.h>
#include <string.h>
#include "present.h"
#include "console.h"
#include "host.h"
#include "show_count.h"
#pragma warning(push)
#pragma warning(disable : 4201)
#include <d3dcompiler.h>
#pragma warning(pop)

GfxState g_gfx;

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

/* --present-stall-us N: every Present takes N us longer (a test of a slow display: a 300 Hz desktop, 11-07, 7 ms
 * each; under --present-nowait a Present refused that long). */
static unsigned g_stall_us;
unsigned gfx_present_stall_us(void) { return g_stall_us; }
static int g_nowait_off; /* --present-nowait off: the thread of in-between frames' Present waits for the display, holding gfx_lock */
int gfx_present_nowait_wanted(void) { return !g_nowait_off; }

int gfx_option(const char *arg, const char *value)
{
    int n = console_present_option(arg, value);
    if (n) return n;
    if (!strcmp(arg, "--headless") || !strcmp(arg, "--no-window")) return g_gfx.headless = 1;
    if (!strcmp(arg, "--warp")) return g_gfx.warp = 1;
    if (!strcmp(arg, "--present-stall-us") && value) return g_stall_us = (unsigned)strtoul(value, NULL, 10), 2;
    if (!strcmp(arg, "--present-nowait") && value) return g_nowait_off = !strcmp(value, "off"), 2;
    if (!strcmp(arg, "--vsync") && value) {
        g_gfx.vsync = strcmp(value, "off") != 0;
        return 2;
    }
    if (host_window_option(arg, value)) return 2;
    if (!strcmp(arg, "--aspect") && value) { /* fit|4:3|16:9|21:9|W:H */
        if (gfx_aspect_parse(value, &g_gfx.aspect))
            g_gfx.aspect_set = 1;
        else
            port_log("gfx: --aspect %s: not fit, 4:3, 16:9, 21:9 or W:H\n", value);
        return 2;
    }
    if (!strcmp(arg, "--widescreen") && value) { /* the old switch: on is --aspect fit, off --aspect 4:3 */
        g_gfx.aspect = strcmp(value, "off") ? 0.0f : 4.0f / 3.0f;
        g_gfx.aspect_set = 1;
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
    return 0;
}

static IDXGIAdapter1 *high_performance_adapter(IDXGIFactory6 *f6)
{
    IDXGIAdapter1 *a = NULL;
    if (f6 && SUCCEEDED(IDXGIFactory6_EnumAdapterByGpuPreference(f6, 0, DXGI_GPU_PREFERENCE_HIGH_PERFORMANCE,
                                                                 &IID_IDXGIAdapter1, (void **)&a)))
        return a;
    return NULL;
}

/* "gfx: drawing on <card>": the DXGI adapter of the device in use, made by the base or by the game (hybrid laptops:
 * the discrete GPU is asked for by host_main.cpp's exports and by high_performance_adapter). */
static void log_drawing_on(ID3D11Device *dev)
{
    IDXGIDevice *xd = NULL;
    IDXGIAdapter *a = NULL;
    DXGI_ADAPTER_DESC d;
    if (FAILED(ID3D11Device_QueryInterface(dev, &IID_IDXGIDevice, (void **)&xd))) return;
    if (SUCCEEDED(IDXGIDevice_GetAdapter(xd, &a))) {
        if (SUCCEEDED(IDXGIAdapter_GetDesc(a, &d)))
            port_log("gfx: drawing on %ls (%u MB)\n", d.Description, (u32)(d.DedicatedVideoMemory >> 20));
        IDXGIAdapter_Release(a);
    }
    IDXGIDevice_Release(xd);
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
    log_drawing_on(g_gfx.dev);
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

/* The start-up picture. The window is shown when it is made (host_window_open), and before this the first Present was
 * the game's first frame, after the whole of the game's start-up (loading, the disc, its first tick): for that long the
 * window showed nothing of ours, on build box A and the user's laptop about 2.3 s of an unpainted or stale window. The 1 s
 * forced Present (present.c's show_overdue) could not cover it: it is reached only from present_show, so only once the
 * game hands a frame over; it guards against lost latency counts, it is no keep-alive. So the swap chain's back buffer
 * is cleared to the border colour and presented once, straight after the swap chain is made, with a latency count like
 * any frame (show_count.c), so the counts stay whole. Headless runs and checks make no swap chain: nothing changes there,
 * and no game frame, frame number or dump moves (g_gfx.frames counts the game's frames, runtime/gfx/gfx_present.c).
 * The log gives both times from the window being made: this picture's, and the game's first frame's (gfx_note_shown). */
static LARGE_INTEGER g_window_qpc;
static double g_window_after_ms; /* the window made, ms after the process started */
static int g_game_shown;

static void window_made(void)
{
    FILETIME created, x1, x2, x3, now;
    QueryPerformanceCounter(&g_window_qpc);
    GetSystemTimePreciseAsFileTime(&now);
    if (GetProcessTimes(GetCurrentProcess(), &created, &x1, &x2, &x3)) {
        ULARGE_INTEGER a, b;
        a.LowPart = created.dwLowDateTime, a.HighPart = created.dwHighDateTime;
        b.LowPart = now.dwLowDateTime, b.HighPart = now.dwHighDateTime;
        g_window_after_ms = (double)(b.QuadPart - a.QuadPart) / 10000.0; /* 100 ns units */
    }
}

static double ms_since_window(void)
{
    LARGE_INTEGER now, f;
    QueryPerformanceCounter(&now);
    QueryPerformanceFrequency(&f);
    return (double)(now.QuadPart - g_window_qpc.QuadPart) * 1000.0 / (double)f.QuadPart;
}

static void startup_present(void)
{
    float c[4];
    u32 b = gfx_border();
    ID3D11RenderTargetView *rtv = gfx_back_rtv();
    if (!rtv || !gfx_can_show()) {
        port_log("gfx: no start-up picture: the display takes no frame yet\n");
        return;
    }
    c[0] = (float)((b >> 16) & 0xffu) / 255.0f, c[1] = (float)((b >> 8) & 0xffu) / 255.0f, c[2] = (float)(b & 0xffu) / 255.0f;
    c[3] = 1.0f;
    ID3D11DeviceContext_ClearRenderTargetView(g_gfx.ctx, rtv, c);
    HRESULT hr = IDXGISwapChain1_Present(g_gfx.swap, 0u, !g_gfx.vsync && g_gfx.tearing ? DXGI_PRESENT_ALLOW_TEARING : 0u);
    if (FAILED(hr)) {
        port_log("gfx: the start-up picture's Present failed (%08lx)\n", (unsigned long)hr);
        return;
    }
    show_count_used(g_gfx.latency);
    port_log("gfx: start-up picture presented %.1f ms after the window was made (the window %.1f ms after the process started)\n",
             ms_since_window(), g_window_after_ms);
}

/* present.c, after each Present of a frame: the first one's time, once (the window showed only the start-up picture until then) */
void gfx_note_shown(void)
{
    if (g_game_shown || !g_window_qpc.QuadPart) return;
    g_game_shown = 1;
    port_log("gfx: the game's first frame presented %.1f ms after the window was made (%.1f ms after the process started)\n",
             ms_since_window(), ms_since_window() + g_window_after_ms);
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
    } else if (!host_window_open(port_console.game, &g_gfx.out_w, &g_gfx.out_h, &g_gfx.fullscreen)) {
        return 0;
    } else {
        window_made();
    }
    if (FAILED(CreateDXGIFactory2(0, &IID_IDXGIFactory2, (void **)&factory))) {
        port_log("gfx: CreateDXGIFactory2 failed\n");
        return 0;
    }
    ok = create_device(factory) && (g_gfx.headless || create_swap_chain(factory)) &&
         gfx_resize(g_gfx.out_w, g_gfx.out_h);
    IDXGIFactory2_Release(factory);
    if (ok && g_gfx.swap) startup_present();
    if (ok)
        port_log("gfx: output %ux%u, frame %ux%u%s\n", g_gfx.out_w, g_gfx.out_h, g_gfx.frame_w, g_gfx.frame_h,
                 g_gfx.headless ? " (headless)" : g_gfx.tearing ? ", tearing allowed" : "");
    if (ok && (g_gfx.draw != g_gfx.frame || g_gfx.aniso > 1u))
        port_log("gfx: look prototypes: drawing at %ux%u, anisotropic filtering %u\n", g_gfx.draw_w, g_gfx.draw_h,
                 g_gfx.aniso > 1u ? g_gfx.aniso : 0u);
    return ok;
}

/* gfx_init for a game that made its own device (base/native_game.h, a native game: no console under it). The window,
 * the swap chain and the frame target are the same as gfx_init's; the swap chain is made from the factory the game's
 * device came from, so it works on whatever adapter the game chose (WARP included). The base holds a reference to the
 * device and its immediate context until gfx_shutdown. */
int gfx_init_device(ID3D11Device *dev)
{
    IDXGIDevice *xd = NULL;
    IDXGIAdapter *adapter = NULL;
    IDXGIFactory2 *factory = NULL;
    int ok;
    if (!dev || g_gfx.dev) return 0;
    if (g_gfx.headless) {
        if (!g_gfx.out_w) g_gfx.out_w = 640, g_gfx.out_h = 480;
    } else if (!host_window_open(port_console.game, &g_gfx.out_w, &g_gfx.out_h, &g_gfx.fullscreen)) {
        return 0;
    } else {
        window_made();
    }
    ID3D11Device_AddRef(dev);
    g_gfx.dev = dev;
    ID3D11Device_GetImmediateContext(dev, &g_gfx.ctx);
    if (SUCCEEDED(ID3D11Device_QueryInterface(dev, &IID_IDXGIDevice, (void **)&xd)) &&
        SUCCEEDED(IDXGIDevice_GetAdapter(xd, &adapter)))
        IDXGIAdapter_GetParent(adapter, &IID_IDXGIFactory2, (void **)&factory);
    if (adapter) {
        DXGI_ADAPTER_DESC d;
        if (SUCCEEDED(IDXGIAdapter_GetDesc(adapter, &d)))
            port_log("gfx: the game's own Direct3D 11 device, feature level %x, adapter %ls\n",
                     (u32)ID3D11Device_GetFeatureLevel(dev), d.Description);
        IDXGIAdapter_Release(adapter);
    }
    if (xd) IDXGIDevice_Release(xd);
    log_drawing_on(dev);
    if (!factory && !g_gfx.headless) {
        port_log("gfx: the game's device gives no DXGI factory for a swap chain\n");
        return 0;
    }
    ok = (g_gfx.headless || create_swap_chain(factory)) && gfx_resize(g_gfx.out_w, g_gfx.out_h);
    if (factory) IDXGIFactory2_Release(factory);
    if (ok && g_gfx.swap) startup_present();
    if (ok)
        port_log("gfx: output %ux%u, frame %ux%u%s\n", g_gfx.out_w, g_gfx.out_h, g_gfx.frame_w, g_gfx.frame_h,
                 g_gfx.headless ? " (headless)" : g_gfx.tearing ? ", tearing allowed" : "");
    return ok;
}

static ID3DBlob *compile(pD3DCompile fn, const char *hlsl, const char *name, const char *entry, const char *target)
{
    ID3DBlob *code = NULL, *err = NULL;
    HRESULT hr = fn(hlsl, strlen(hlsl), name, NULL, NULL, entry, target, 0, 0, &code, &err);
    if (FAILED(hr)) port_log("video: %s shader %s failed (%08lx): %s\n", name, entry, (unsigned long)hr, err ? (const char *)ID3D10Blob_GetBufferPointer(err) : "");
    if (err) ID3D10Blob_Release(err);
    return code;
}

int gfx_shaders(const char *hlsl, const char *name, ID3D11VertexShader **vs, ID3D11PixelShader **ps)
{
    HMODULE dll = LoadLibraryW(L"d3dcompiler_47.dll");
    pD3DCompile fn = dll ? (pD3DCompile)(void *)GetProcAddress(dll, "D3DCompile") : NULL;
    ID3DBlob *v, *p;
    HRESULT hr;
    if (!fn) {
        port_log("video: no d3dcompiler_47.dll\n");
        return 0;
    }
    v = compile(fn, hlsl, name, "vs_main", "vs_5_0");
    p = compile(fn, hlsl, name, "ps_main", "ps_5_0");
    hr = v && p ? S_OK : E_FAIL;
    if (SUCCEEDED(hr)) hr = ID3D11Device_CreateVertexShader(g_gfx.dev, ID3D10Blob_GetBufferPointer(v), ID3D10Blob_GetBufferSize(v), NULL, vs);
    if (SUCCEEDED(hr)) hr = ID3D11Device_CreatePixelShader(g_gfx.dev, ID3D10Blob_GetBufferPointer(p), ID3D10Blob_GetBufferSize(p), NULL, ps);
    if (v) ID3D10Blob_Release(v);
    if (p) ID3D10Blob_Release(p);
    return SUCCEEDED(hr);
}
