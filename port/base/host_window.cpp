// The window, after Lodoss's desktop_host, desktop_fullscreen, desktop_window_fit and desktop_window_status (MIT,
// the user's own): one SDL3 window for the whole run. Full screen is borderless at the desktop mode (the default);
// --window picks a window instead, sized WxH, Nx (N times the console's picture) or fit (the largest whole multiple of it whose
// whole window, title bar and menu bar included, the desktop's usable area holds). Alt+Enter and F11 switch between the two, and the choice is kept in the settings.
// The output's pixel size goes to the renderer (gfx_resize), which keeps the 4:3 picture pillarboxed whatever the
// window's shape. The window can also pause the game or mute it while it is not focused, and count frames (on the
// menu bar: host_counter.cpp).
// A windowed window is sized and placed by its outer rectangle (title bar, borders and menu bar included) inside the
// work area of the display it is on (host_place.h), at any display scaling.
#ifndef NOMINMAX
#define NOMINMAX
#endif
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#include <SDL3/SDL.h>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include "host.h"
#include "present.h"
#include "game_log.h"
#include "host_internal.hpp"
#include "host_place.h"

namespace {

// The console's own picture size (port_console): the window opens as a whole multiple of it.
int base_w() { return static_cast<int>(port_console.native_w); }
int base_h() { return static_cast<int>(port_console.native_h); }

SDL_Window *g_window;
std::string g_title;
bool g_windowed, g_fit, g_paused;
int g_scale;          // --window Nx (0: not used)
int g_win_w, g_win_h; // --window WxH: the client size asked for (0: fit or Nx)
host::Prefs g_prefs;
unsigned g_frames, g_count_blanks; // frames shown and port_vblank_count() since g_count_start (frame_counter)
Uint64 g_count_start;

bool parse_size(const char *v, int *w, int *h)
{
    char *end;
    unsigned long x = std::strtoul(v, &end, 10);
    if (*end != 'x' && *end != 'X') return false;
    unsigned long y = std::strtoul(end + 1, &end, 10);
    if (*end || x < 64 || y < 48 || x > 16384 || y > 16384) return false;
    *w = static_cast<int>(x);
    *h = static_cast<int>(y);
    return true;
}

// The usable area of the display the window is on: Windows' work area of the monitor it is really on, else SDL's.
PlaceRect work_area(HWND hwnd)
{
    MONITORINFO mi{};
    mi.cbSize = sizeof mi;
    if (hwnd && GetMonitorInfo(MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST), &mi))
        return {mi.rcWork.left, mi.rcWork.top, mi.rcWork.right - mi.rcWork.left, mi.rcWork.bottom - mi.rcWork.top};
    SDL_DisplayID display = g_window ? SDL_GetDisplayForWindow(g_window) : SDL_GetPrimaryDisplay();
    SDL_Rect r{0, 0, 1280, 960};
    if (!SDL_GetDisplayUsableBounds(display, &r)) port_log("host: no usable display area: %s\n", SDL_GetError());
    return {r.x, r.y, r.w, r.h};
}

// The chrome: the outer window minus its client area (0 when it cannot be read).
void chrome_size(HWND hwnd, int *cw, int *ch)
{
    RECT o{}, c{};
    *cw = *ch = 0;
    if (!hwnd || !GetWindowRect(hwnd, &o) || !GetClientRect(hwnd, &c)) return;
    *cw = (o.right - o.left) - (c.right - c.left);
    *ch = (o.bottom - o.top) - (c.bottom - c.top);
}

// The client size for the windowed window: --window WxH as given, else Nx or fit (whole multiples of the console's picture; fit the
// largest the work area holds with the chrome around it), shrunk with its aspect kept when the outer window would not
// fit the area.
void client_size(PlaceRect area, int cw, int ch, int *w, int *h)
{
    if (g_win_w) {
        *w = g_win_w;
        *h = g_win_h;
    } else {
        int most = place_fit_scale(area, cw, ch, base_w(), base_h());
        int n = g_fit || g_scale > most ? most : g_scale;
        if (n < 1) n = 1;
        *w = base_w() * n;
        *h = base_h() * n;
    }
    place_client(area, cw, ch, w, h);
}

// Sizes the windowed window and puts it, title bar and all, inside the work area of the display it is on. The chrome
// is read from the window itself (the menu bar may wrap at another width), so the size is settled in two passes.
void place_window()
{
    HWND hwnd = static_cast<HWND>(host_window_hwnd());
    int cw, ch, w = 0, h = 0;
    for (int pass = 0; pass < 2; pass++) {
        chrome_size(hwnd, &cw, &ch);
        client_size(work_area(hwnd), cw, ch, &w, &h);
        SDL_SetWindowSize(g_window, w, h);
        SDL_SyncWindow(g_window);
    }
    chrome_size(hwnd, &cw, &ch);
    int x, y;
    place_origin(work_area(hwnd), w + cw, h + ch, &x, &y);
    if (hwnd) SetWindowPos(hwnd, nullptr, x, y, 0, 0, SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE);
    else SDL_SetWindowPosition(g_window, x, y);
    SDL_SyncWindow(g_window);
}

std::string window_setting()
{
    if (!g_windowed) return "full";
    if (g_fit) return "fit";
    if (g_scale) return std::to_string(g_scale) + "x";
    return std::to_string(g_win_w) + "x" + std::to_string(g_win_h);
}

void set_title(const char *extra)
{
    std::string t = g_title;
    if (extra && *extra) t += std::string(" - ") + extra;
    SDL_SetWindowTitle(g_window, t.c_str());
}

void set_paused(bool on)
{
    g_paused = on;
    set_title(on ? "paused" : nullptr);
    port_log("host: %s\n", on ? "paused (window not focused)" : "resumed");
}

void handle(const SDL_Event &e)
{
    switch (e.type) {
    case SDL_EVENT_QUIT:
    case SDL_EVENT_WINDOW_CLOSE_REQUESTED:
        game_log_line("window: close requested (the user closed the window)");
        port_exit("the window was closed", 0);
        break;
    case SDL_EVENT_WINDOW_MINIMIZED: game_log_line("window: minimized"); break;
    case SDL_EVENT_WINDOW_RESTORED: game_log_line("window: restored"); break;
    case SDL_EVENT_WINDOW_PIXEL_SIZE_CHANGED:
        if (e.window.data1 > 0 && e.window.data2 > 0) {
            port_log("host: output %dx%d\n", e.window.data1, e.window.data2);
            game_log_line("window: resized, output %dx%d", e.window.data1, e.window.data2);
            gfx_resize(static_cast<unsigned>(e.window.data1), static_cast<unsigned>(e.window.data2));
        }
        break;
    case SDL_EVENT_KEY_DOWN:
        if (!e.key.repeat && (e.key.key == SDLK_F11 || ((e.key.key == SDLK_RETURN || e.key.key == SDLK_KP_ENTER) &&
                                                         (e.key.mod & SDL_KMOD_ALT)))) {
            game_log_line("keyboard: %s (full screen %s)", e.key.key == SDLK_F11 ? "F11" : "Alt+Enter",
                          g_windowed ? "on" : "off");
            host::window_set_fullscreen(g_windowed);
        }
        if (!e.key.repeat && e.key.key == SDLK_F9) game_log_line("keyboard: F9 (Copy agent prompt)");
        if (!e.key.repeat && e.key.key == SDLK_F9) scene_dump_request(); // the copy icon's key (full screen too)
        break;
    case SDL_EVENT_WINDOW_FOCUS_LOST:
        port_log("host: window lost the focus (the keyboard is not read)\n");
        game_log_line("window: lost the focus (the keyboard is not read)");
        if (g_prefs.mute_unfocused) audio_set_volume(audio_volume(), 1);
        if (g_prefs.pause_unfocused) set_paused(true);
        break;
    case SDL_EVENT_WINDOW_FOCUS_GAINED:
        port_log("host: window focused\n");
        game_log_line("window: focused");
        audio_set_volume(audio_volume(), 0);
        if (g_paused) set_paused(false);
        break;
    default: break;
    }
}

bool on_off(const char *value) { return std::strcmp(value, "off") != 0 && std::strcmp(value, "0") != 0; }

} // namespace

namespace host {

Prefs &prefs() { return g_prefs; }

bool window_fullscreen() { return g_window && !g_windowed; }

int window_scale() { return g_fit ? 0 : g_scale ? g_scale : -1; }

void window_set_fullscreen(bool on)
{
    if (!SDL_SetWindowFullscreen(g_window, on)) {
        port_log("host: full screen %s failed: %s\n", on ? "on" : "off", SDL_GetError());
        return;
    }
    g_windowed = !on;
    menu_show(!on);
    if (!on) place_window();
    port_log("host: %s\n", on ? "full screen" : "windowed");
    game_log_line("window: %s", on ? "full screen" : "windowed");
    settings_set("window", window_setting());
}

void window_set_scale(int n)
{
    g_fit = n == 0;
    g_scale = n;
    g_win_w = g_win_h = 0;
    if (g_windowed) {
        place_window();
        settings_set("window", window_setting());
    } else {
        window_set_fullscreen(false);
    }
}

} // namespace host

extern "C" int host_window_option(const char *arg, const char *value)
{
    if (!value) return 0;
    bool *pref = !std::strcmp(arg, "--fps-counter")       ? &g_prefs.fps_counter
                 : !std::strcmp(arg, "--pause-unfocused") ? &g_prefs.pause_unfocused
                 : !std::strcmp(arg, "--mute-unfocused")  ? &g_prefs.mute_unfocused
                                                          : nullptr;
    if (pref) {
        *pref = on_off(value);
        return 2;
    }
    if (std::strcmp(arg, "--window")) return 0;
    if (!std::strcmp(value, "full")) {
        g_windowed = false;
        return 2;
    }
    char *end;
    long n = std::strtol(value, &end, 10);
    bool fit = !std::strcmp(value, "fit"), scale = (*end == 'x' || *end == 'X') && !end[1] && n >= 1 && n <= 16;
    int w = 0, h = 0;
    if (!fit && !scale && !parse_size(value, &w, &h)) return 0;
    g_fit = fit;
    g_scale = scale ? static_cast<int>(n) : 0;
    if (w) {
        g_win_w = w;
        g_win_h = h;
    }
    g_windowed = true;
    return 2;
}

extern "C" int host_window_open(const char *title, unsigned *w, unsigned *h, int *fullscreen)
{
    // the gamepad keeps working while another window has the focus (a launcher that started the game, a second
    // screen); SDL otherwise reads it as released
    SDL_SetHint(SDL_HINT_JOYSTICK_ALLOW_BACKGROUND_EVENTS, input_live() ? "1" : "0");
    // a run that plays by itself (runtime/gfx/gfx_live.c) never takes the focus from what the user is doing
    const bool take_focus = input_live() != 0;
    SDL_SetHint(SDL_HINT_WINDOW_ACTIVATE_WHEN_SHOWN, take_focus ? "1" : "0");
    if (!SDL_Init(SDL_INIT_VIDEO)) {
        port_log("host: SDL_Init failed: %s\n", SDL_GetError());
        return 0;
    }
    const SDL_DisplayMode *mode = SDL_GetDesktopDisplayMode(SDL_GetPrimaryDisplay());
    if (!g_win_w && !g_scale) g_fit = true; // full screen leaves for the fit window
    // the size it is created at is a first guess (the chrome is not known yet); place_window settles it
    int first_w = 0, first_h = 0;
    client_size(work_area(nullptr), 0, 0, &first_w, &first_h);
    g_title = title;
    g_window = SDL_CreateWindow(title, first_w, first_h, SDL_WINDOW_HIGH_PIXEL_DENSITY | SDL_WINDOW_RESIZABLE |
                                                             (g_windowed ? 0 : SDL_WINDOW_FULLSCREEN) |
                                                             (take_focus ? 0 : SDL_WINDOW_NOT_FOCUSABLE));
    if (!g_window) {
        port_log("host: SDL_CreateWindow failed: %s\n", SDL_GetError());
        return 0;
    }
    host::menu_attach(host_window_hwnd());
    if (g_windowed) {
        host::menu_show(true);
        place_window(); // the client area under the menu bar, the whole window inside the work area
    }
    // to the front with the focus, for the keyboard: a launcher that started the game may otherwise keep it (not for a
    // run that plays by itself: raising takes the foreground even from a window that cannot be focused)
    if (take_focus && !SDL_RaiseWindow(g_window)) port_log("host: could not raise the window: %s\n", SDL_GetError());
    int pw = 0, ph = 0;
    SDL_GetWindowSizeInPixels(g_window, &pw, &ph);
    *w = static_cast<unsigned>(pw);
    *h = static_cast<unsigned>(ph);
    *fullscreen = !g_windowed;
    g_count_start = SDL_GetTicks();
    if (mode)
        port_log("gfx: desktop %dx%d @ %.2f Hz, %s %dx%d\n", mode->w, mode->h, static_cast<double>(mode->refresh_rate),
                 g_windowed ? "window" : "full screen", pw, ph);
    return 1;
}

extern "C" void *host_window_hwnd(void)
{
    if (!g_window) return nullptr;
    return SDL_GetPointerProperty(SDL_GetWindowProperties(g_window), SDL_PROP_WINDOW_WIN32_HWND_POINTER, nullptr);
}

// Once a second: frames shown and the game's own ticks (vertical blanks run) a second, to the menu bar (host_counter.cpp).
static void frame_counter(void)
{
    Uint64 now = SDL_GetTicks();
    u32 blanks = port_vblank_count();
    if (now - g_count_start < 1000) return;
    double secs = static_cast<double>(now - g_count_start) / 1000.0;
    if (!g_paused) host::counter_show(g_prefs.fps_counter, g_frames / secs, (blanks - g_count_blanks) / secs, port_console.tick_hz);
    g_frames = 0;
    g_count_start = now;
    g_count_blanks = blanks;
}

extern "C" void host_window_frame(void)
{
    g_frames++;
    if (!gfx_on_present_thread()) frame_counter();
}

// The window as the display sees it: a window that is covered or not focused may be given few presents (DWM).
extern "C" const char *host_window_state(void)
{
    if (!g_window) return "no window";
    SDL_WindowFlags f = SDL_GetWindowFlags(g_window);
    if (f & SDL_WINDOW_MINIMIZED) return "minimized";
    if (f & SDL_WINDOW_OCCLUDED) return "covered";
    if (!(f & SDL_WINDOW_INPUT_FOCUS)) return "not focused";
    return (f & SDL_WINDOW_FULLSCREEN) ? "full screen" : "focused";
}

// Window events: closing ends the run, a new pixel size resizes the output, Alt+Enter and F11 switch full screen,
// and while paused the game thread waits here (still handling events) until the window is focused again.
// --present-thread: the lock around the device context (host.h). The game's thread is the window's: while it waits it answers
// messages sent to the window, since a present on the other thread can wait for those. It says it is waiting, so the other
// thread, which draws frame after frame, cannot keep it out.
static CRITICAL_SECTION g_gfx_cs;
static volatile LONG g_gfx_mt, g_gfx_wanted, g_gfx_waits; static LONGLONG g_gfx_wait_us; /* the game's thread's waits (gfx_lock_waits) */
static __declspec(thread) int g_gfx_present_thread;

extern "C" void gfx_lock_on(void)
{
    InitializeCriticalSection(&g_gfx_cs);
    g_gfx_mt = 1;
}

extern "C" void gfx_lock(void)
{
    if (!g_gfx_mt) return;
    if (g_gfx_present_thread) {
        EnterCriticalSection(&g_gfx_cs);
        return;
    }
    InterlockedIncrement(&g_gfx_wanted);
    if (!TryEnterCriticalSection(&g_gfx_cs)) {
        LARGE_INTEGER a, b, f;
        QueryPerformanceCounter(&a);
        do {
            MSG m;
            PeekMessageW(&m, NULL, 0, 0, PM_NOREMOVE | PM_QS_SENDMESSAGE);
            SwitchToThread();
        } while (!TryEnterCriticalSection(&g_gfx_cs));
        QueryPerformanceCounter(&b);
        QueryPerformanceFrequency(&f);
        g_gfx_waits++, g_gfx_wait_us += (b.QuadPart - a.QuadPart) * 1000000 / f.QuadPart;
        if ((b.QuadPart - a.QuadPart) * 10 >= f.QuadPart)
            port_log("gfx: the game's thread waited %lld ms for the device context\n", (b.QuadPart - a.QuadPart) * 1000 / f.QuadPart);
    }
    InterlockedDecrement(&g_gfx_wanted);
}

extern "C" void gfx_unlock(void)
{
    if (g_gfx_mt) LeaveCriticalSection(&g_gfx_cs);
}

extern "C" void gfx_lock_present_thread(void) { g_gfx_present_thread = 1; }
extern "C" int gfx_on_present_thread(void) { return g_gfx_present_thread; }
extern "C" int gfx_lock_wanted(void) { return g_gfx_wanted != 0; }
extern "C" void gfx_lock_waits(unsigned *n, unsigned long long *us) { *n = (unsigned)g_gfx_waits, *us = (unsigned long long)g_gfx_wait_us; }

extern "C" void gfx_pump(void)
{
    SDL_Event e;
    if (!g_window) return;
    frame_counter(); /* frames shown by the other thread (--present-thread) count here */
    while (SDL_PollEvent(&e)) {
        gfx_lock();
        handle(e);
        gfx_unlock();
    }
    if (g_paused) {
        gfx_lock();
        while (g_paused)
            if (SDL_WaitEventTimeout(&e, 100)) handle(e);
        gfx_unlock();
    }
}
