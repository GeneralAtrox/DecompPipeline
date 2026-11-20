/* A dummy native game for the game-owned renderer mode (port/base/native_game.h): no game, no assets. It makes its own
 * Direct3D 11 device, joins it to the base's window, steps a 60 Hz logic tick on the base's clock and draws a known
 * picture: four quadrants (top left red, top right green, bottom left blue, bottom right white) and a 32x32 square on
 * the middle row whose left edge is 16 + (tick * 4) % (w - 64), yellow (magenta with Game > Dummy grid on). Even frames
 * are drawn straight into the frame target, odd frames into a texture of the game's own handed over with
 * pg_native_show_texture (an SDL_Renderer's way). FIRE (A) presses are counted, logged and named in Copy agent prompt.
 * Game > Dummy reset (a command) puts the square back at the left; Game > Dummy jump (a submenu) moves it to a third.
 * Its sound is one float stereo output the device pulls (silence at the game's own half level). Its install (the
 * launcher's --setup-only) writes assets\dummy\data.txt from the image's first line; --verify-assets checks it.
 *   --dummy-ticks N   the game ends by itself after N ticks (its main returns 0)
 *   --dummy-draw-us N  spins N us between pg_native_frame and pg_native_present (a known draw time, for the make time)
 *   --dummy-tick-us N  spins N us in each tick
 *   --dummy-tick-inside 1   ticks after pg_native_frame, inside the frame's span (as Phantom Crash's loop does)
 *   --dummy-text 1     its own text field open (pg_native_text): Enter logs what was typed and moves the square
 *   --dummy-mouse look|pointer   the mouse is the game's (pg_native_mouse_mode); its moves and presses are added up
 * Game > Dummy level is a runtime list of 20 (pg_native_menu_list, tag 7): a pick moves the square to 8 x its entry.
 * tests/test_native_game.py builds it with port/tools/build_native.py and reads its frames and log. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <windows.h>
#include "native_game.h"
#pragma warning(push)
#pragma warning(disable : 4201)
#include <d3d11_1.h>
#pragma warning(pop)
#include "game_log.h"
#include "scene_json.h"
#include "audio_out.h"

static ID3D11Device *g_dev;
static ID3D11DeviceContext1 *g_ctx1;
static ID3D11Texture2D *g_own;
static ID3D11RenderTargetView *g_own_rtv;
static u32 g_own_w, g_own_h, g_x, g_presses, g_quit_at, g_held;
static int g_grid, g_tick_inside;
static u32 g_draw_us, g_tick_us;
static int g_text, g_mouse;
static char g_field[64];
static float g_mdx, g_mdy;
static u32 g_mpresses, g_mcaptured;

static void spin_us(u32 us)
{
    LARGE_INTEGER f, a, b;
    if (!us) return;
    QueryPerformanceFrequency(&f);
    QueryPerformanceCounter(&a);
    do QueryPerformanceCounter(&b);
    while ((u64)(b.QuadPart - a.QuadPart) * 1000000u < (u64)us * (u64)f.QuadPart);
}

static int grid_get(void) { return g_grid; }
static void grid_set(int on)
{
    g_grid = on != 0;
    game_log_line("dummy: grid %s", g_grid ? "on" : "off");
}
static void reset(int unused)
{
    (void)unused;
    g_x = 0;
    port_setting_set_int("game.dummy-resets", port_setting_get_int("game.dummy-resets", 0) + 1);
    game_log_line("dummy: reset");
}
static const char *const k_jumps[] = {"First", "Second", "Third"};
static void jump(int n)
{
    g_x = (u32)n * 160u;
    game_log_line("dummy: jump to %s", k_jumps[n]);
}
static const PortMenuItem k_menu[] = {
    {"Game", "Dummy &grid", PORT_MENU_CHECK, "game.grid", grid_get, grid_set, NULL, NULL, 0, NULL},
    {"Game", "Dummy &reset", PORT_MENU_COMMAND, NULL, NULL, reset, NULL, NULL, 0, NULL},
    {"Game", "Dummy &jump", PORT_MENU_SUBMENU, NULL, NULL, jump, k_jumps, NULL, 3, NULL},
    {"Game", "Dummy &level", PORT_MENU_SUBMENU, NULL, NULL, NULL, NULL, NULL, 0, NULL, 7},
};
const PortMenuItem *game_menu_extra(unsigned *count)
{
    *count = 4;
    return k_menu;
}

static AudioOut *g_sound;
static void sound_need(void *user, AudioOut *a, u32 bytes)
{
    static const float silence[256] = {0};
    (void)user;
    for (u32 n; bytes; bytes -= n) {
        n = bytes < sizeof silence ? bytes & ~7u : (u32)sizeof silence;
        if (!n) break;
        audio_out_put(a, silence, n);
    }
}

static void fill(ID3D11RenderTargetView *v, u32 x, u32 y, u32 w, u32 h, float r, float g, float b)
{
    const float c[4] = {r, g, b, 1.0f};
    D3D11_RECT rc = {(LONG)x, (LONG)y, (LONG)(x + w), (LONG)(y + h)};
    ID3D11DeviceContext1_ClearView(g_ctx1, (ID3D11View *)v, c, &rc, 1);
}

static void draw(ID3D11RenderTargetView *v, u32 w, u32 h)
{
    fill(v, 0, 0, w / 2, h / 2, 1, 0, 0);
    fill(v, w / 2, 0, w - w / 2, h / 2, 0, 1, 0);
    fill(v, 0, h / 2, w / 2, h - h / 2, 0, 0, 1);
    fill(v, w / 2, h / 2, w - w / 2, h - h / 2, 1, 1, 1);
    fill(v, 16 + g_x % (w > 64 ? w - 64 : 1), h / 2 - 16, 32, 32, 1, g_grid ? 0.0f : 1.0f, g_grid ? 1.0f : 0.0f);
}

static int own_target(u32 w, u32 h)
{
    D3D11_TEXTURE2D_DESC d;
    if (g_own && g_own_w == w && g_own_h == h) return 1;
    if (g_own_rtv) ID3D11RenderTargetView_Release(g_own_rtv), g_own_rtv = NULL;
    if (g_own) ID3D11Texture2D_Release(g_own), g_own = NULL;
    memset(&d, 0, sizeof d);
    d.Width = w, d.Height = h, d.MipLevels = 1, d.ArraySize = 1, d.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
    d.SampleDesc.Count = 1, d.Usage = D3D11_USAGE_DEFAULT, d.BindFlags = D3D11_BIND_RENDER_TARGET | D3D11_BIND_SHADER_RESOURCE;
    if (FAILED(ID3D11Device_CreateTexture2D(g_dev, &d, NULL, &g_own)) ||
        FAILED(ID3D11Device_CreateRenderTargetView(g_dev, (ID3D11Resource *)g_own, NULL, &g_own_rtv)))
        return 0;
    g_own_w = w, g_own_h = h;
    return 1;
}

/* The text, mouse and menu picks this tick's pad read took (native_game.h). */
static void take_events(void)
{
    char buf[64];
    int n, tag, index;
    PgNativeMouse m;
    while ((n = pg_native_text_take(buf, (int)sizeof buf)) > 0)
        for (int i = 0; i < n; i++) {
            size_t len = strlen(g_field);
            if (buf[i] == 8) { /* Backspace: the last whole character */
                while (len && ((u8)g_field[len - 1] & 0xc0u) == 0x80u) len--;
                g_field[len ? len - 1 : 0] = 0;
            } else if (buf[i] == 10) { /* Enter */
                game_log_line("dummy: text submitted \"%s\"", g_field);
                g_x = (u32)strlen(g_field) * 40u;
                g_field[0] = 0;
            } else if (len + 1 < sizeof g_field) {
                g_field[len] = buf[i], g_field[len + 1] = 0;
            }
        }
    if (pg_native_mouse(&m)) {
        g_mdx += m.dx, g_mdy += m.dy, g_mcaptured = m.captured;
        if (m.pressed & 1u) game_log_line("dummy: mouse left pressed (%u)", ++g_mpresses);
    }
    while (pg_native_menu_take(&tag, &index)) {
        game_log_line("dummy: menu tag %d entry %d", tag, index);
        g_x = (u32)index * 8u;
    }
}

static void tick(void)
{
    HostPad pads[HOST_PADS];
    u32 held;
    pg_native_tick_begin();
    if (g_text) pg_native_text(1);
    if (g_mouse) pg_native_mouse_mode(g_mouse);
    host_pads_read(pads);
    take_events();
    held = host_actions(&pads[0]);
    if (held & ~g_held & 1u) game_log_line("dummy: FIRE pressed (%u)", ++g_presses);
    g_held = held;
    g_x += 4;
    spin_us(g_tick_us);
    pg_native_tick_end();
}

static int dummy_main(void)
{
    static const D3D_FEATURE_LEVEL levels[] = {D3D_FEATURE_LEVEL_11_1, D3D_FEATURE_LEVEL_11_0};
    ID3D11DeviceContext *ctx = NULL;
    char grid[8] = "off";
    u64 t0, done = 0;
    HRESULT hr = D3D11CreateDevice(NULL, pg_native_warp() ? D3D_DRIVER_TYPE_WARP : D3D_DRIVER_TYPE_HARDWARE, NULL, 0,
                                   levels, 2, D3D11_SDK_VERSION, &g_dev, NULL, &ctx);
    if (FAILED(hr) || FAILED(ID3D11DeviceContext_QueryInterface(ctx, &IID_ID3D11DeviceContext1, (void **)&g_ctx1))) {
        port_log("dummy: no Direct3D 11.1 device (%08lx)\n", (unsigned long)hr);
        return 1;
    }
    ID3D11DeviceContext_Release(ctx);
    if (!pg_native_attach(g_dev)) return 1;
    port_setting_get_str("game.grid", grid, sizeof grid);
    g_grid = !strcmp(grid, "on");
    game_log_line("dummy: started, grid %s", g_grid ? "on" : "off");
    {
        static char names[20][12];
        const char *labels[20];
        for (int i = 0; i < 20; i++) _snprintf_s(names[i], sizeof names[i], _TRUNCATE, "Level %d", i + 1), labels[i] = names[i];
        pg_native_menu_list(7, labels, 20);
    }
    if ((g_sound = audio_out_open_format(48000, 2, 1)) != NULL) {
        audio_out_gain(g_sound, 0.5f);
        audio_out_pull(g_sound, sound_need, NULL);
        audio_out_start(g_sound);
        game_log_line("dummy: sound out");
    }
    t0 = pg_native_now_us();
    while (!g_quit_at || pg_native_ticks() < g_quit_at) {
        PgNativeFrame f;
        u64 due = (pg_native_now_us() - t0) * port_console.tick_hz / 1000000u;
        if (!g_tick_inside) {
            for (int n = 0; done < due && n < 6; n++, done++) tick();
            done = due; /* further behind than 6 ticks: the time is dropped */
        }
        if (!pg_native_frame(&f)) return 1;
        if (g_tick_inside) {
            for (int n = 0; done < due && n < 6; n++, done++) tick();
            done = due;
        }
        spin_us(g_draw_us);
        if (g_gfx.frames % 2u == 0u) {
            draw(f.rtv, f.w, f.h);
        } else if (own_target(f.w, f.h)) {
            draw(g_own_rtv, f.w, f.h);
            pg_native_show_texture(g_own);
        }
        pg_native_present();
    }
    return 0;
}

static int dummy_option(const char *arg, const char *value)
{
    if (value && !strcmp(arg, "--dummy-ticks")) return g_quit_at = (u32)strtoul(value, NULL, 10), 2;
    if (value && !strcmp(arg, "--dummy-draw-us")) return g_draw_us = (u32)strtoul(value, NULL, 10), 2;
    if (value && !strcmp(arg, "--dummy-tick-us")) return g_tick_us = (u32)strtoul(value, NULL, 10), 2;
    if (value && !strcmp(arg, "--dummy-tick-inside")) return g_tick_inside = atoi(value) != 0, 2;
    if (value && !strcmp(arg, "--dummy-text")) return g_text = atoi(value) != 0, 2;
    if (value && !strcmp(arg, "--dummy-mouse")) return g_mouse = !strcmp(value, "look") ? PG_MOUSE_LOOK : PG_MOUSE_POINTER, 2;
    return 0;
}

static void dummy_end(const char *why, int code)
{
    port_log("dummy: end (%s, %d) at x %u, %u FIRE presses\n", why, code, g_x, g_presses);
    if (g_mouse) port_log("dummy: mouse %.0f, %.0f, %u left presses, captured %u\n", g_mdx, g_mdy, g_mpresses, g_mcaptured);
    audio_out_close(g_sound);
}

static void dummy_scene(struct Json *j) { json_f(j, ",\n    \"dummy_x\": %u,\n    \"dummy_presses\": %u", g_x, g_presses); }

/* The launcher's setup: --setup-only (image, work) writes work\assets\dummy\data.txt from the image's first line and
 * then installation.json; --verify-assets (assets) checks data.txt is there. 4: the image or the data is missing. */
static int dummy_install(const char *image, const char *work, const char *assets)
{
    char path[MAX_PATH], line[128] = "";
    FILE *f = NULL;
    if (!image) {
        _snprintf_s(path, sizeof path, _TRUNCATE, "%s\\data.txt", assets);
        if (fopen_s(&f, path, "rb") || !f) return port_install_fail(4, "no %s", path);
        fclose(f);
        return 0;
    }
    if (fopen_s(&f, image, "rb") || !f) return port_install_fail(4, "the disc image %s is missing or unreadable", image);
    if (!fgets(line, sizeof line, f)) line[0] = 0;
    fclose(f);
    _snprintf_s(path, sizeof path, _TRUNCATE, "%s\\assets", work);
    CreateDirectoryA(path, NULL);
    _snprintf_s(path, sizeof path, _TRUNCATE, "%s\\assets\\dummy", work);
    CreateDirectoryA(path, NULL);
    _snprintf_s(path, sizeof path, _TRUNCATE, "%s\\assets\\dummy\\data.txt", work);
    if (fopen_s(&f, path, "wb") || !f) return port_install_fail(1, "cannot write %s", path);
    fputs(line, f);
    fclose(f);
    return port_install_record(work, "assets/dummy");
}

static const PortAction k_actions[] = {{"FIRE", "A"}, {"PAUSE", "START"}};
const PgNativeGame pg_native_game = {dummy_option, dummy_main, dummy_end, k_actions, 2u, dummy_scene, dummy_install};
