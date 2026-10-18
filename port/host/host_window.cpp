// The window, after Lodoss's desktop_host, desktop_fullscreen, desktop_window_fit and desktop_window_status (MIT,
// the user's own): one SDL3 window for the whole run. Full screen is borderless at the desktop mode (the default);
// --window picks a window instead, sized WxH, Nx (N times 640x480) or fit (the largest whole multiple of 640x480 the
// desktop's usable area holds). Alt+Enter and F11 switch between the two, and the choice is kept in the settings.
// The output's pixel size goes to the renderer (gfx_resize), which keeps the 4:3 picture pillarboxed whatever the
// window's shape. The window can also pause the game or mute it while it is not focused, and count frames in its
// title.
#include <SDL3/SDL.h>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include "host.h"
#include "host_internal.hpp"

namespace {

constexpr int kBaseW = 640, kBaseH = 480;

SDL_Window *g_window;
std::string g_title;
bool g_windowed, g_fit, g_paused;
int g_scale;          // --window Nx (0: not used)
int g_win_w, g_win_h; // the window's size when not full screen
host::Prefs g_prefs;
unsigned g_frames;
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

// The window size for --window Nx or fit, from the usable area of the display the window is on.
void fit_size()
{
    SDL_DisplayID display = g_window ? SDL_GetDisplayForWindow(g_window) : SDL_GetPrimaryDisplay();
    SDL_Rect area{0, 0, 1280, 960};
    if (!SDL_GetDisplayUsableBounds(display, &area)) port_log("host: no usable display area: %s\n", SDL_GetError());
    int most = (area.w / kBaseW < area.h / kBaseH) ? area.w / kBaseW : area.h / kBaseH;
    if (most < 1) most = 1;
    int n = g_fit || g_scale > most ? most : g_scale;
    g_win_w = kBaseW * n;
    g_win_h = kBaseH * n;
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
    case SDL_EVENT_WINDOW_CLOSE_REQUESTED: port_exit("the window was closed", 0); break;
    case SDL_EVENT_WINDOW_PIXEL_SIZE_CHANGED:
        if (e.window.data1 > 0 && e.window.data2 > 0) {
            port_log("host: output %dx%d\n", e.window.data1, e.window.data2);
            gfx_resize(static_cast<unsigned>(e.window.data1), static_cast<unsigned>(e.window.data2));
        }
        break;
    case SDL_EVENT_KEY_DOWN:
        if (!e.key.repeat && (e.key.key == SDLK_F11 || ((e.key.key == SDLK_RETURN || e.key.key == SDLK_KP_ENTER) &&
                                                         (e.key.mod & SDL_KMOD_ALT))))
            host::window_set_fullscreen(g_windowed);
        if (!e.key.repeat && e.key.key == SDLK_F9) scene_dump_request(); // the copy icon's key (full screen too)
        break;
    case SDL_EVENT_WINDOW_FOCUS_LOST:
        port_log("host: window lost the focus (the keyboard is not read)\n");
        if (g_prefs.mute_unfocused) audio_set_volume(audio_volume(), 1);
        if (g_prefs.pause_unfocused) set_paused(true);
        break;
    case SDL_EVENT_WINDOW_FOCUS_GAINED:
        port_log("host: window focused\n");
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
    if (!on) {
        SDL_SetWindowSize(g_window, g_win_w, g_win_h);
        SDL_SetWindowPosition(g_window, SDL_WINDOWPOS_CENTERED, SDL_WINDOWPOS_CENTERED);
    }
    port_log("host: %s\n", on ? "full screen" : "windowed");
    settings_set("window", window_setting());
}

void window_set_scale(int n)
{
    g_fit = n == 0;
    g_scale = n;
    fit_size();
    if (g_windowed) {
        SDL_SetWindowSize(g_window, g_win_w, g_win_h);
        SDL_SetWindowPosition(g_window, SDL_WINDOWPOS_CENTERED, SDL_WINDOWPOS_CENTERED);
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
    SDL_SetHint(SDL_HINT_JOYSTICK_ALLOW_BACKGROUND_EVENTS, "1");
    if (!SDL_Init(SDL_INIT_VIDEO)) {
        port_log("host: SDL_Init failed: %s\n", SDL_GetError());
        return 0;
    }
    const SDL_DisplayMode *mode = SDL_GetDesktopDisplayMode(SDL_GetPrimaryDisplay());
    if (!g_win_w && !g_scale) g_fit = true; // full screen leaves for the fit window
    if (g_fit || g_scale) fit_size();
    g_title = title;
    g_window = SDL_CreateWindow(title, g_win_w, g_win_h, SDL_WINDOW_HIGH_PIXEL_DENSITY | SDL_WINDOW_RESIZABLE |
                                                             (g_windowed ? 0 : SDL_WINDOW_FULLSCREEN));
    if (!g_window) {
        port_log("host: SDL_CreateWindow failed: %s\n", SDL_GetError());
        return 0;
    }
    host::menu_attach(host_window_hwnd());
    if (g_windowed) {
        host::menu_show(true);
        SDL_SetWindowSize(g_window, g_win_w, g_win_h); // the client area, now under the menu bar
        SDL_SetWindowPosition(g_window, SDL_WINDOWPOS_CENTERED, SDL_WINDOWPOS_CENTERED);
        SDL_SyncWindow(g_window);
    }
    // to the front with the focus, for the keyboard: a launcher that started the game may otherwise keep it
    if (!SDL_RaiseWindow(g_window)) port_log("host: could not raise the window: %s\n", SDL_GetError());
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

extern "C" void host_window_frame(void)
{
    Uint64 now = SDL_GetTicks();
    g_frames++;
    if (now - g_count_start < 1000) return;
    if (g_prefs.fps_counter && !g_paused) {
        char text[32];
        std::snprintf(text, sizeof text, "%.0f fps", g_frames * 1000.0 / static_cast<double>(now - g_count_start));
        set_title(text);
    }
    g_frames = 0;
    g_count_start = now;
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
extern "C" void gfx_pump(void)
{
    SDL_Event e;
    if (!g_window) return;
    while (SDL_PollEvent(&e)) handle(e);
    while (g_paused)
        if (SDL_WaitEventTimeout(&e, 100)) handle(e);
}
