// Display > Resolution (host_menu.cpp puts the group in the Display menu; this file fills it each time it opens). In a
// window it is the window's size: fit to the desktop, or 1x to 4x the console's picture. In full screen it is the
// display's modes, the desktop's own first and the default; the choice is SDL's full-screen mode for the window, kept in
// the settings as fullscreen-mode (desktop, or WxH@Hz). The game is drawn at the window's size either way (present.c
// gfx_resize makes the frame target the largest rectangle of the aspect in it), so this is also the size it is drawn at.
// Nearest existing: host_menu.cpp's Window size group - it had no full-screen modes, and host_menu.cpp is at the
// 400-line cap.
#ifndef NOMINMAX
#define NOMINMAX
#endif
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#include <SDL3/SDL.h>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>
#include "console.h"
#include "game_log.h"
#include "host.h"
#include "host_internal.hpp"

namespace {

constexpr UINT kSizeFit = 110;     // kSizeFit + N: a window N x the console's picture (0: fit to the desktop)
constexpr UINT kSize4 = 114;
constexpr UINT kModeDesktop = 700; // full screen at the desktop's own mode
constexpr UINT kMode = 701;        // kMode + N: g_modes[N]
constexpr size_t kModeMax = 63;

struct Mode {
    int w, h;
    float hz;
};
std::vector<Mode> g_modes; // the display's, as the group last listed them
Mode g_want{};             // fullscreen-mode; w 0: the desktop's
HMENU g_menu;

SDL_Window *window() // the game's window: the base makes one SDL window (host_window.cpp)
{
    int n = 0;
    SDL_Window **all = SDL_GetWindows(&n);
    SDL_Window *w = all && n > 0 ? all[0] : nullptr;
    SDL_free(all);
    return w;
}

std::string text(const Mode &m)
{
    if (!m.w) return "desktop";
    char b[48];
    std::snprintf(b, sizeof b, "%dx%d@%.2f", m.w, m.h, static_cast<double>(m.hz));
    return b;
}

void list_modes(SDL_Window *w)
{
    g_modes.clear();
    int n = 0;
    SDL_DisplayMode **all = SDL_GetFullscreenDisplayModes(w ? SDL_GetDisplayForWindow(w) : SDL_GetPrimaryDisplay(), &n);
    for (int i = 0; all && i < n && g_modes.size() < kModeMax; i++) {
        Mode m{all[i]->w, all[i]->h, all[i]->refresh_rate};
        bool seen = false; // a mode again at another pixel density
        for (const Mode &o : g_modes) seen = seen || (o.w == m.w && o.h == m.h && std::fabs(o.hz - m.hz) < 0.5f);
        if (!seen) g_modes.push_back(m);
    }
    SDL_free(all);
}

// The window's full-screen mode: the display's mode nearest the one wanted, at its size, or the desktop's.
void apply(SDL_Window *w, bool chosen)
{
    if (!w) return;
    SDL_DisplayMode found{};
    const SDL_DisplayMode *use = nullptr;
    if (g_want.w) {
        if (SDL_GetClosestFullscreenDisplayMode(SDL_GetDisplayForWindow(w), g_want.w, g_want.h, g_want.hz, true, &found) &&
            found.w == g_want.w && found.h == g_want.h)
            use = &found;
        else
            game_log_line("window: full screen mode %s is not offered by this display; the desktop's is used", text(g_want).c_str());
    }
    if (!SDL_SetWindowFullscreenMode(w, use)) {
        port_log("host: full screen mode %s: %s\n", text(g_want).c_str(), SDL_GetError());
        return;
    }
    if (chosen || use) game_log_line("window: full screen mode %s", use ? text(Mode{use->w, use->h, use->refresh_rate}).c_str() : "desktop");
}

void add(UINT id, const std::wstring &label, bool on) { AppendMenuW(g_menu, MF_STRING | (on ? MF_CHECKED : 0u), id, label.c_str()); }

// --fullscreen-mode desktop | WxH[@Hz]
int resolution_option(const char *arg, const char *value)
{
    if (!value || std::strcmp(arg, "--fullscreen-mode")) return 0;
    Mode m{};
    if (std::strcmp(value, "desktop")) {
        char *end = nullptr;
        m.w = static_cast<int>(std::strtol(value, &end, 10));
        if (!end || (*end != 'x' && *end != 'X')) return 0;
        m.h = static_cast<int>(std::strtol(end + 1, &end, 10));
        if (*end == '@') m.hz = std::strtof(end + 1, &end);
        if (m.w <= 0 || m.h <= 0 || *end) return 0;
    }
    g_want = m;
    return 2;
}

} // namespace

namespace host {

int window_options(const char *arg, const char *value)
{
    if (int n = bar_option(arg, value)) return n; // --menu-bar slim|native (host_bar.cpp)
    return resolution_option(arg, value);
}

void *resolution_menu()
{
    g_menu = CreatePopupMenu();
    apply(window(), false); // the window was just made (host_window_open): the saved mode for its full screen
    return g_menu;
}

void resolution_sync()
{
    if (!g_menu) return;
    while (GetMenuItemCount(g_menu) > 0) DeleteMenu(g_menu, 0, MF_BYPOSITION);
    if (!window_fullscreen()) {
        int scale = window_scale();
        add(kSizeFit, L"&Fit to the desktop", scale == 0);
        AppendMenuW(g_menu, MF_SEPARATOR, 0, nullptr);
        for (UINT n = 1; n <= 4; n++)
            add(kSizeFit + n,
                L"&" + std::to_wstring(n) + L"x  (" + std::to_wstring(port_console.native_w * n) + L"x" +
                    std::to_wstring(port_console.native_h * n) + L")",
                scale == static_cast<int>(n));
        return;
    }
    SDL_Window *w = window();
    const SDL_DisplayMode *desk = SDL_GetDesktopDisplayMode(w ? SDL_GetDisplayForWindow(w) : SDL_GetPrimaryDisplay());
    std::wstring label = L"&Desktop";
    if (desk) label += L" (" + std::to_wstring(desk->w) + L"x" + std::to_wstring(desk->h) + L")";
    add(kModeDesktop, label, !g_want.w);
    AppendMenuW(g_menu, MF_SEPARATOR, 0, nullptr);
    list_modes(w);
    for (size_t i = 0; i < g_modes.size(); i++) {
        const Mode &m = g_modes[i];
        bool on = g_want.w == m.w && g_want.h == m.h && std::fabs(g_want.hz - m.hz) < 0.5f;
        add(kMode + static_cast<UINT>(i),
            std::to_wstring(m.w) + L"x" + std::to_wstring(m.h) + L"  " + std::to_wstring(std::lround(m.hz)) + L" Hz", on);
    }
}

bool resolution_command(unsigned id)
{
    if (id >= kSizeFit && id <= kSize4) {
        window_set_scale(static_cast<int>(id - kSizeFit));
        return true;
    }
    if (id != kModeDesktop && (id < kMode || id - kMode >= g_modes.size())) return false;
    g_want = id == kModeDesktop ? Mode{} : g_modes[id - kMode];
    apply(window(), true);
    settings_set("fullscreen-mode", text(g_want));
    return true;
}

} // namespace host
