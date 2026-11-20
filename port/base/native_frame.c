/* The game-owned renderer mode's frame (native_game.h): a native game's device joined to the base's window and swap chain,
 * its frames presented, the polls every console makes after a presented frame (the game log, Copy agent prompt, the play
 * summary), the frame-rate cap's pacing, and its options:
 *   --fps N                 the frame-rate cap (the host's play runs pass 144; Display > Frame-rate cap; 0 none)
 *   --shot FRAME,FILE       the frame target of presented frame FRAME (0 the first) to a 24-bit BMP (checks; repeatable)
 *   --frames N              ends the run after N presented frames ("frame limit", exit 0)
 *   --present, --interp-frames   taken with no effect: a native game draws every frame itself
 * then the presenter's (present_device.c gfx_option: --headless, --warp, --window, --aspect, --size ...).
 * PG_DRAW_SPLIT (environment) times a frame's spans and GPU time for finding a slow frame: draw_split.h.
 * Under --fixed-clock each presented frame is one tick of port_console.tick_hz on the clock the game steps by
 * (pg_native_now_us), so a run repeats exactly; headless runs and the fixed clock never wait for the cap.
 *
 * Nearest existing: runtime/gfx/gfx_present.c (the Dreamcast renderer's present and polls) and present_cpu.c (a CPU
 * frame's) - both make the device themselves (gfx_init); here the game made it, and nothing of a console is in the path. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "native_game.h"
#pragma warning(push)
#pragma warning(disable : 4201)
#include <d3d11_1.h>
#pragma warning(pop)
#include "game_log.h"
#include "scene_dump.h"
#include "play_stats.h"
#include "work_count.h"
#include "host.h"
#include "draw_split.h"

#define MAX_SHOTS 16u

static int g_fps = -1; /* -1: not given (a check run): no cap */
static u32 g_ticks, g_max_frames, g_shot_count;
static u64 g_due;
static LARGE_INTEGER g_tick_at;
static ID3D11Texture2D *g_stage;
static int g_bad_src;
static struct {
    u32 frame;
    char path[MAX_PATH];
} g_shots[MAX_SHOTS];

int pg_native_option(const char *arg, const char *value)
{
    if (value && !strcmp(arg, "--fps")) return g_fps = atoi(value) < 0 ? 0 : atoi(value), 2;
    if (value && (!strcmp(arg, "--present") || !strcmp(arg, "--interp-frames"))) return 2;
    if (value && !strcmp(arg, "--frames")) return g_max_frames = (u32)strtoul(value, NULL, 10), 2;
    if (value && !strcmp(arg, "--shot")) {
        char *end;
        u32 f = (u32)strtoul(value, &end, 10);
        if (*end == ',' && end[1] && g_shot_count < MAX_SHOTS) {
            g_shots[g_shot_count].frame = f;
            strncpy_s(g_shots[g_shot_count].path, sizeof g_shots[0].path, end + 1, _TRUNCATE);
            g_shot_count++;
        } else {
            port_log("native: --shot %s: not FRAME,FILE (at most %u)\n", value, MAX_SHOTS);
        }
        return 2;
    }
    if (port_menu_pick_option(arg, value)) return 2;
    return gfx_option(arg, value);
}

int pg_native_warp(void) { return g_gfx.warp; }

/* The base's present sets its own shaders, targets and states on the game's context. A renderer that caches what it set
 * (an SDL_Renderer) would then draw with the base's: so the base works in a context state of its own (ID3D11DeviceContext1
 * SwapDeviceContextState), and the game's comes back whole after. Without 11.1 the context is cleared after instead. */
static ID3D11DeviceContext1 *g_ctx1;
static ID3DDeviceContextState *g_base_state, *g_game_state;
static void native_state_swap(int to_base)
{
    if (!g_gfx.ctx) return;
    if (!g_base_state && !g_ctx1) {
        static const D3D_FEATURE_LEVEL levels[] = {D3D_FEATURE_LEVEL_11_1, D3D_FEATURE_LEVEL_11_0, D3D_FEATURE_LEVEL_10_1,
                                                   D3D_FEATURE_LEVEL_10_0};
        ID3D11Device1 *dev1 = NULL;
        if (SUCCEEDED(ID3D11DeviceContext_QueryInterface(g_gfx.ctx, &IID_ID3D11DeviceContext1, (void **)&g_ctx1)) &&
            SUCCEEDED(ID3D11Device_QueryInterface(g_gfx.dev, &IID_ID3D11Device1, (void **)&dev1)) &&
            SUCCEEDED(ID3D11Device1_CreateDeviceContextState(dev1, 0, levels, 4, D3D11_SDK_VERSION, &IID_ID3D11Device, NULL,
                                                              &g_base_state))) {
            port_log("native: the base presents in a device context state of its own\n");
        } else {
            port_log("native: no device context state of the base's own: the game's state is cleared after each present\n");
        }
        if (dev1) ID3D11Device1_Release(dev1);
    }
    if (g_base_state && g_ctx1) {
        if (to_base) {
            ID3D11DeviceContext1_SwapDeviceContextState(g_ctx1, g_base_state, &g_game_state);
        } else if (g_game_state) {
            ID3D11DeviceContext1_SwapDeviceContextState(g_ctx1, g_game_state, NULL);
            ID3DDeviceContextState_Release(g_game_state);
            g_game_state = NULL;
        }
    }
}

int pg_native_attach(ID3D11Device *device)
{
    if (!gfx_init_device(device)) {
        port_log("native: the game's device could not be joined to the window\n");
        return 0;
    }
    port_log("native: the game draws with its own device; cap %d fps%s\n", g_fps < 0 ? 0 : g_fps,
             host_fixed_clock() ? " (fixed clock: no wait)" : g_gfx.headless ? " (headless: no wait)" : "");
    return 1;
}

int pg_native_frame(PgNativeFrame *f)
{
    memset(f, 0, sizeof *f);
    if (!g_gfx.frame) return 0;
    play_stats_native(1);
    if (g_draw_split && draw_split_on()) draw_split_frame();
    f->rtv = g_gfx.frame_rtv;
    f->tex = g_gfx.frame;
    f->w = g_gfx.frame_w;
    f->h = g_gfx.frame_h;
    f->wide_k = g_gfx.wide_k > 1.0f ? g_gfx.wide_k : 1.0f;
    f->tall_k = g_gfx.tall_k > 1.0f ? g_gfx.tall_k : 1.0f;
    gfx_screen_rect(f->w, f->h, f->wide_k, f->tall_k, f->screen);
    return 1;
}

int pg_native_show_texture(ID3D11Texture2D *src)
{
    D3D11_TEXTURE2D_DESC d;
    if (!g_gfx.frame || !src) return 0;
    ID3D11Texture2D_GetDesc(src, &d);
    if (d.Width != g_gfx.frame_w || d.Height != g_gfx.frame_h || d.SampleDesc.Count != 1 ||
        (d.Format != DXGI_FORMAT_R8G8B8A8_UNORM && d.Format != DXGI_FORMAT_R8G8B8A8_TYPELESS &&
         d.Format != DXGI_FORMAT_R8G8B8A8_UNORM_SRGB)) {
        if (!g_bad_src++)
            port_log("native: a %ux%u texture (format %u) is not the frame's %ux%u R8G8B8A8: not shown\n", d.Width, d.Height,
                     (u32)d.Format, g_gfx.frame_w, g_gfx.frame_h);
        return 0;
    }
    if (g_draw_split > 0) {
        u64 t = draw_split_now();
        ID3D11DeviceContext_CopyResource(g_gfx.ctx, (ID3D11Resource *)g_gfx.frame, (ID3D11Resource *)src);
        draw_split_copied(t);
        return 1;
    }
    ID3D11DeviceContext_CopyResource(g_gfx.ctx, (ID3D11Resource *)g_gfx.frame, (ID3D11Resource *)src);
    return 1;
}

static void cap_wait(void)
{
    u64 period, now;
    if (host_fixed_clock()) {
        host_virt_to_tick(); /* the virtual clock moves one tick a frame */
        return;
    }
    if (g_gfx.headless || g_fps <= 0) return;
    period = 1000000u / (u32)g_fps;
    now = host_ticks(1000000u);
    if (!g_due || now > g_due + 4u * period) g_due = now; /* a load or a stall: the lost time is dropped */
    g_due += period;
    while (host_ticks(1000000u) < g_due) pace_nap_until(g_due, 0);
}

void pg_native_present(void)
{
    u32 frame = g_gfx.frames;
    u64 t = 0;
    if (!g_gfx.dev) return;
    play_stats_native(0);
    if (g_draw_split > 0) draw_split_game_done();
    for (u32 i = 0; i < g_shot_count; i++)
        if (g_shots[i].frame == frame)
            port_log("native: frame %u (tick %u) %s %s\n", frame, g_ticks,
                     pg_native_capture_bmp(g_shots[i].path) ? "written to" : "NOT written to", g_shots[i].path);
    port_menu_pick_poll(frame);
    native_state_swap(1); /* the game's device state kept aside while the base draws (an SDL_Renderer caches its own) */
    if (g_draw_split > 0) t = draw_split_now();
    present_show();
    if (g_draw_split > 0) draw_split_presented(t);
    native_state_swap(0);
    game_log_poll(g_ticks);
    scene_dump_poll(g_ticks);
    g_gfx.frames++;
    play_stats_shown();
    if (g_max_frames && g_gfx.frames >= g_max_frames) port_exit("frame limit", 0);
    cap_wait();
    if (g_draw_split > 0) draw_split_waited();
}

void pg_native_tick_begin(void) { QueryPerformanceCounter(&g_tick_at); }

void pg_native_tick_end(void)
{
    LARGE_INTEGER now, f;
    QueryPerformanceCounter(&now);
    QueryPerformanceFrequency(&f);
    g_ticks++;
    work_count_tick(g_ticks);
    if (g_draw_split > 0 && g_tick_at.QuadPart) draw_split_tick((u64)(now.QuadPart - g_tick_at.QuadPart));
    if (g_tick_at.QuadPart) play_stats_tick((double)(now.QuadPart - g_tick_at.QuadPart) * 1000.0 / (double)f.QuadPart);
}

u32 pg_native_ticks(void) { return g_ticks; }

u64 pg_native_now_us(void)
{
    if (host_fixed_clock()) /* rounded up: a step of exactly 1/tick_hz s takes one tick each frame */
        return ((u64)g_gfx.frames * 1000000u + port_console.tick_hz - 1u) / port_console.tick_hz;
    return host_ticks(1000000u);
}

unsigned pg_native_fps(void) { return g_fps < 0 ? 0u : (unsigned)g_fps; }

void pg_native_set_fps(unsigned fps)
{
    g_fps = (int)fps;
    g_due = 0;
    port_log("native: frame-rate cap %u%s\n", fps, fps ? " fps" : " (none)");
}

int pg_native_capture_bmp(const char *path)
{
    D3D11_TEXTURE2D_DESC d;
    D3D11_MAPPED_SUBRESOURCE m;
    u8 head[54] = {'B', 'M'}, pad[3] = {0, 0, 0}, *line;
    FILE *f = NULL;
    int ok;
    if (!g_gfx.frame) return 0;
    ID3D11Texture2D_GetDesc(g_gfx.frame, &d);
    if (g_stage) {
        D3D11_TEXTURE2D_DESC s;
        ID3D11Texture2D_GetDesc(g_stage, &s);
        if (s.Width != d.Width || s.Height != d.Height) ID3D11Texture2D_Release(g_stage), g_stage = NULL;
    }
    if (!g_stage) {
        d.Usage = D3D11_USAGE_STAGING;
        d.BindFlags = 0;
        d.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
        d.MiscFlags = 0;
        if (FAILED(ID3D11Device_CreateTexture2D(g_gfx.dev, &d, NULL, &g_stage))) return 0;
    }
    ID3D11DeviceContext_CopyResource(g_gfx.ctx, (ID3D11Resource *)g_stage, (ID3D11Resource *)g_gfx.frame);
    if (FAILED(ID3D11DeviceContext_Map(g_gfx.ctx, (ID3D11Resource *)g_stage, 0, D3D11_MAP_READ, 0, &m))) return 0;
    u32 w = d.Width, h = d.Height, row = (w * 3u + 3u) & ~3u, size = 54u + row * h, off = 54u, hdr = 40u;
    memcpy(head + 2, &size, 4);
    memcpy(head + 10, &off, 4);
    memcpy(head + 14, &hdr, 4);
    memcpy(head + 18, &w, 4);
    memcpy(head + 22, &h, 4);
    head[26] = 1;
    head[28] = 24;
    line = (u8 *)malloc(w * 3u);
    ok = line && fopen_s(&f, path, "wb") == 0 && f && fwrite(head, sizeof head, 1, f) == 1;
    for (u32 y = h; ok && y-- > 0;) {
        const u8 *src = (const u8 *)m.pData + (size_t)y * m.RowPitch;
        for (u32 x = 0; x < w; x++) line[x * 3u] = src[x * 4u + 2u], line[x * 3u + 1u] = src[x * 4u + 1u], line[x * 3u + 2u] = src[x * 4u];
        ok = fwrite(line, w * 3u, 1, f) == 1 && (row == w * 3u || fwrite(pad, row - w * 3u, 1, f) == 1);
    }
    ID3D11DeviceContext_Unmap(g_gfx.ctx, (ID3D11Resource *)g_stage, 0);
    if (f) ok = fclose(f) == 0 && ok;
    free(line);
    return ok;
}

void pg_native_release(void)
{
    draw_split_release();
    if (g_base_state) ID3DDeviceContextState_Release(g_base_state);
    if (g_ctx1) ID3D11DeviceContext1_Release(g_ctx1);
    g_base_state = NULL, g_ctx1 = NULL;
    if (g_stage) ID3D11Texture2D_Release(g_stage);
    g_stage = NULL;
    gfx_shutdown();
}
