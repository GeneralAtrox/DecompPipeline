/* The base's presenter for a CPU-buffer frame (present_cpu.h): the console's frame goes into a texture and is drawn
 * nearest-neighbour as the 4:3 screen in the middle of the frame target (present.c), which present_show puts on the
 * window like any other console's. A frame composed at the screen's width shows bars beside it in a wider frame target;
 * a console that composes more of its world to the sides posts the frame with its screen's width (cpu_present_post_wide),
 * and the sides are drawn beside the screen at the same dot size: nothing is stretched or cropped.
 *
 * The shaders are small enough to compile at start-up, with the compiler Windows carries (d3dcompiler_47.dll).
 *
 * --present-thread on|off|auto (auto, the default: on in a play run): frames are shown by a thread of their own at the
 * frame-rate cap (--fps, the menu's; 144 a second unless chosen, the target's rate; 0 as many as the display takes), so
 * a tick and its picture, however long, take nothing from a shown frame. The console's thread posts each finished frame (cpu_present_post), copied into one of
 * three buffers; the showing thread takes the newest whole one, and shows it again while no newer one is ready; it draws
 * only when the display will take the frame (gfx_can_show). No game code runs there. The device context is shared under gfx_lock (host.h); the window's events stay on the console's
 * thread. --present-check on: each buffer shown is hashed against the frame posted into it, a frame taken while a newer
 * one was posted and a frame older than one already shown are counted, so a torn or stale picture shows in the log's
 * "present check" line. The check's copy waits halfway (copy_frame), so a fault in the hand-over shows in most frames.
 *
 * Nearest existing: runtime/frame_thread.c, the Dreamcast's thread of in-between frames: it draws blended scenes with
 * the PVR renderer and is held by it; this one shows a finished CPU buffer, which any CPU-composed console can use. */
#include <stdlib.h>
#include <string.h>
#include "present_cpu.h"
#include "present.h"
#include "host.h"
#include "play_stats.h"
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
static u32 g_tex_w, g_tex_h, g_shown_w, g_shown_h, g_shown_sw, g_post_sw; /* sw: the screen's width in the frame */

static int g_thread_mode = -1, g_check; /* --present-thread: 1 on, 0 off, -1 auto; --present-check on */
static volatile LONG g_fps = -1;         /* --fps, the menu's cap: shown frames a second, 0 none, -1 not chosen (144) */

int cpu_present_option(const char *arg, const char *value)
{
    if (!value) return gfx_option(arg, value);
    if (!strcmp(arg, "--fps")) g_fps = atoi(value);
    else if (!strcmp(arg, "--present-thread")) g_thread_mode = !strcmp(value, "on") ? 1 : !strcmp(value, "off") ? 0 : -1;
    else if (!strcmp(arg, "--present-check")) g_check = !strcmp(value, "on");
    else if (strcmp(arg, "--present") && strcmp(arg, "--interp-frames")) return gfx_option(arg, value);
    return 2;
}

void cpu_present_set_fps(unsigned fps) { g_fps = (LONG)fps; }
unsigned cpu_present_fps(void) { return g_fps < 0 ? 144u : (unsigned)g_fps; }

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

/* The frame into the texture; 0 if there is none for it. */
static int upload(const u32 *xrgb, unsigned w, unsigned h, unsigned sw)
{
    D3D11_MAPPED_SUBRESOURCE m;
    ID3D11DeviceContext *cx = g_gfx.ctx;
    if ((w != g_tex_w || h != g_tex_h) && !make_texture(w, h)) return 0;
    if (FAILED(ID3D11DeviceContext_Map(cx, (ID3D11Resource *)g_tex, 0, D3D11_MAP_WRITE_DISCARD, 0, &m))) return 0;
    for (unsigned y = 0; y < h; y++) memcpy((u8 *)m.pData + y * m.RowPitch, xrgb + y * w, w * 4u);
    ID3D11DeviceContext_Unmap(cx, (ID3D11Resource *)g_tex, 0);
    g_shown_w = w;
    g_shown_h = h;
    g_shown_sw = sw ? sw : w;
    return 1;
}

/* The texture drawn as the screen in the frame target, which is then shown. */
static void draw_and_show(void)
{
    D3D11_VIEWPORT vp;
    u32 r[4];
    ID3D11DeviceContext *cx = g_gfx.ctx;
    gfx_clear(0);
    gfx_screen_rect(g_gfx.frame_w, g_gfx.frame_h, g_gfx.wide_k, g_gfx.tall_k, r);
    vp.Width = (FLOAT)r[2] * (FLOAT)g_shown_w / (FLOAT)g_shown_sw; /* a frame wider than its screen: the sides beside it */
    vp.TopLeftX = (FLOAT)r[0] - (vp.Width - (FLOAT)r[2]) * 0.5f;
    vp.TopLeftY = (FLOAT)r[1];
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

void cpu_present(const u32 *xrgb, unsigned w, unsigned h)
{
    if (!g_vs || !g_gfx.frame_rtv) {
        gfx_pump();
        return;
    }
    if (upload(xrgb, w, h, g_post_sw)) draw_and_show();
}

/* ---- --present-thread ---- */
#ifndef CREATE_WAITABLE_TIMER_HIGH_RESOLUTION
#define CREATE_WAITABLE_TIMER_HIGH_RESOLUTION 0x00000002
#endif
enum { SLOTS = 3 };
static struct {
    u32 *px;
    u32 cap, w, h, sw, vb, hash;
} g_slot[SLOTS];
static CRITICAL_SECTION g_cs;              /* guards g_newest, g_taken and g_posted */
static int g_up, g_newest = -1, g_taken = -1; /* the slot posted last; the one the showing thread is reading */
static u32 g_posted;
static HANDLE g_wake, g_timer;              /* the first frame; the showing thread's own nap (base/pace.c's is the game's) */
static u32 g_ck_shown, g_ck_new, g_ck_torn, g_ck_stale, g_ck_back, g_ck_vb, g_ck_cap, *g_ck_px;

static u32 hash_px(const u32 *p, u32 n)
{
    u32 h = 2166136261u;
    for (u32 i = 0; i < n; i++) h = (h ^ p[i]) * 16777619u;
    return h;
}

/* A frame into its slot. With --present-check the copy waits 3 ms halfway, so that a slot handed over before its copy
 * is whole would be shown torn in most frames, not one in thousands. */
static void copy_frame(u32 *dst, const u32 *src, u32 n)
{
    u32 half = g_check ? n / 2u : 0u;
    memcpy(dst, src, (size_t)half * 4u);
    if (half) Sleep(3);
    memcpy(dst + half, src + half, (size_t)(n - half) * 4u);
}

/* The performance counter, read directly: host_ticks would move the virtual clock. */
static u64 now_us(void)
{
    LARGE_INTEGER t, f;
    QueryPerformanceCounter(&t);
    QueryPerformanceFrequency(&f);
    return (u64)t.QuadPart * 1000000u / (u64)f.QuadPart;
}

static void nap_until(u64 due)
{
    u64 now = now_us();
    LARGE_INTEGER d;
    if (due > now + 600u && g_timer) {
        d.QuadPart = -(LONGLONG)((due - now - 500u) * 10u); /* relative, in 100 ns; the last half millisecond spins */
        if (SetWaitableTimer(g_timer, &d, 0, NULL, NULL, FALSE)) WaitForSingleObject(g_timer, (DWORD)((due - now) / 1000u) + 2u);
    }
    while (now_us() < due) SwitchToThread(); /* not the tick: the thread that shows frames waits for its time */
}

static DWORD WINAPI show_thread(void *arg)
{
    u64 due = 0;
    u32 seen = 0;
    (void)arg;
    gfx_lock_present_thread();
    WaitForSingleObject(g_wake, INFINITE);
    for (;;) {
        u32 fps = cpu_present_fps();
        u64 period = fps ? 1000000u / fps : 0u, now = now_us();
        int s = -1, ok = 0;
        due = due && now <= due + period ? due + period : now; /* a late frame starts the grid again: no burst after it */
        nap_until(due);
        if (!gfx_can_show()) { /* the display would not take it: drawing it would be wasted */
            gfx_show_wait();
            continue;
        }
        EnterCriticalSection(&g_cs);
        if (g_posted != seen) s = g_taken = g_newest, seen = g_posted;
        if (s >= 0 && g_check) g_ck_stale += g_slot[s].vb != g_slot[g_newest].vb; /* a newer frame was there to take */
        LeaveCriticalSection(&g_cs);
        while (gfx_lock_wanted()) SwitchToThread(); /* the game's thread wants the device: it goes first */
        if (s >= 0 && g_check) { /* the check sees the very bytes shown: a copy of the slot, hashed, is what is uploaded */
            u32 n = g_slot[s].w * g_slot[s].h;
            if (g_ck_cap < n) {
                free(g_ck_px);
                g_ck_px = (u32 *)malloc((size_t)n * 4u);
                g_ck_cap = g_ck_px ? n : 0;
            }
            if (g_ck_px) {
                memcpy(g_ck_px, g_slot[s].px, (size_t)n * 4u);
                g_ck_torn += hash_px(g_ck_px, n) != g_slot[s].hash;
            }
            g_ck_back += g_slot[s].vb < g_ck_vb;
            g_ck_vb = g_slot[s].vb;
        }
        gfx_lock();
        play_stats_draw(1);
        if (s >= 0) ok = upload(g_check && g_ck_px ? g_ck_px : g_slot[s].px, g_slot[s].w, g_slot[s].h, g_slot[s].sw);
        if (g_tex_w) draw_and_show();
        play_stats_draw(0);
        gfx_unlock();
        g_ck_new += ok;
        if (s >= 0) {
            EnterCriticalSection(&g_cs);
            g_taken = -1;
            LeaveCriticalSection(&g_cs);
        }
        g_ck_shown += g_tex_w != 0;
        play_stats_shown();
    }
}

/* The console's thread, once the window is open: auto is on in a play run (a window, the real clock). */
int cpu_present_thread_start(int play_run)
{
    HANDLE t;
    if (!(g_thread_mode < 0 ? play_run : g_thread_mode) || !g_vs || !g_gfx.frame_rtv) return 0;
    InitializeCriticalSection(&g_cs);
    g_wake = CreateEventW(NULL, FALSE, FALSE, NULL);
    g_timer = CreateWaitableTimerExW(NULL, NULL, CREATE_WAITABLE_TIMER_HIGH_RESOLUTION, TIMER_ALL_ACCESS);
    gfx_lock_on();
    t = g_wake ? CreateThread(NULL, 0, show_thread, NULL, 0, NULL) : NULL;
    if (!t) {
        port_log("video: no thread to show frames; the game's thread shows them\n");
        return 0;
    }
    SetThreadPriority(t, THREAD_PRIORITY_ABOVE_NORMAL);
    CloseHandle(t);
    g_up = 1;
    port_log("video: frames shown by a thread of their own, at most %u a second (0: as many as the display takes; --present-thread)\n",
             cpu_present_fps());
    return 1;
}

int cpu_present_threaded(void) { return g_up; }

/* The console's thread: a finished frame. Without the thread it is shown here, as cpu_present. */
void cpu_present_post(const u32 *xrgb, unsigned w, unsigned h, u32 vb) { cpu_present_post_wide(xrgb, w, h, vb, w); }

/* The same for a frame of w dots whose screen is the middle sw of them (the rest is the world beside it). */
void cpu_present_post_wide(const u32 *xrgb, unsigned w, unsigned h, u32 vb, unsigned sw)
{
    int s = 0;
    if (!g_up) {
        g_post_sw = sw, cpu_present(xrgb, w, h), g_post_sw = 0;
        return;
    }
    EnterCriticalSection(&g_cs);
    while (s == g_newest || s == g_taken) s++; /* the showing thread takes only the newest: this one is free */
    LeaveCriticalSection(&g_cs);
    if (g_slot[s].cap < w * h) {
        free(g_slot[s].px);
        g_slot[s].px = (u32 *)malloc((size_t)w * h * 4u);
        if (!g_slot[s].px) port_exit("video: no memory for a frame to show", 1);
        g_slot[s].cap = w * h;
    }
    copy_frame(g_slot[s].px, xrgb, w * h);
    g_slot[s].w = w;
    g_slot[s].h = h;
    g_slot[s].sw = sw;
    g_slot[s].vb = vb;
    if (g_check) g_slot[s].hash = hash_px(xrgb, w * h);
    EnterCriticalSection(&g_cs);
    g_newest = s;
    g_posted++;
    LeaveCriticalSection(&g_cs);
    SetEvent(g_wake);
    gfx_pump();
}

void cpu_present_report(void)
{
    if (g_check && g_up)
        port_log("present check: %u shown, %u of them new frames (%u posted), %u torn, %u not the newest, %u older than one shown before\n",
                 g_ck_shown, g_ck_new, g_posted, g_ck_torn, g_ck_stale, g_ck_back);
}

void cpu_present_view(PortView *v)
{
    v->out_w = g_gfx.out_w;
    v->out_h = g_gfx.out_h;
    v->frame_w = g_shown_w;
    v->frame_h = g_shown_h;
    v->presented_fps = (float)gfx_presented_fps();
}
