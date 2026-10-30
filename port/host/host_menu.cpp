// The menu bar, after Lodoss's desktop_stage_menu (MIT, the user's own): a native Win32 menu on the SDL window,
// reached through a window subclass. Every choice applies at once and goes into the settings file (as the option it
// stands for), so the next run starts the same way. The menu shows while windowed; full screen hides it.
//   Display: full screen, window size, aspect (pillarbox; wide after the look review), VSync, in-between frames,
//            frame-rate cap, frame-rate counter, VMU screen, texture pack (when the game has one)
//   Audio:   volume, mute when unfocused
//   Input:   rumble, pause when unfocused
//   Help:    copy agent prompt (F9; the bar's copy icon is the same command: host_copy_button.cpp)
#include <string>
#include <windows.h>
#include <commctrl.h>
#include "host.h"
#include "host_internal.hpp"

namespace {

enum : UINT {
    kFullScreen = 100,
    kSizeFit = 110, // kSizeFit + N: a window N x 640x480
    kSize4 = 114,
    kFpsCounter = 120,
    kVsync,
    kAspectPillarbox,
    kAspectWide,
    kInterp = 130,
    kFpsCap = 140, // kFpsCap + N: kFpsCaps[N]
    kVmuScreen = 150, // kVmuScreen + N: --vmu-screen mode N (off, side, over)
    kTexturePack = 160,
    kVolume0 = 200, // kVolume0 + N: N x 25 %
    kVolume100 = 204,
    kMuteUnfocused = 210,
    kRumble = 300,
    kPauseUnfocused,
};

HWND g_hwnd;
HMENU g_menu;

const char *on_off(bool on) { return on ? "on" : "off"; }

// --fps N: what the Frame-rate cap menu offers (0: none, as many as the display takes)
constexpr unsigned kFpsCaps[] = {0, 60, 120, 144, 240};
constexpr UINT kFpsCapCount = sizeof kFpsCaps / sizeof kFpsCaps[0];

HMENU display_menu()
{
    HMENU m = CreatePopupMenu(), size = CreatePopupMenu(), aspect = CreatePopupMenu(), cap = CreatePopupMenu(),
          vmu = CreatePopupMenu();
    AppendMenuW(vmu, MF_STRING, kVmuScreen, L"&Off");
    AppendMenuW(vmu, MF_STRING, kVmuScreen + 1, L"&Beside the picture");
    AppendMenuW(vmu, MF_STRING, kVmuScreen + 2, L"O&ver the picture (top left)");
    AppendMenuW(cap, MF_STRING, kFpsCap, L"&None (as many as the display takes)");
    for (UINT n = 1; n < kFpsCapCount; n++) {
        std::wstring label = L"&" + std::to_wstring(kFpsCaps[n]) + L" frames a second";
        AppendMenuW(cap, MF_STRING, kFpsCap + n, label.c_str());
    }
    AppendMenuW(size, MF_STRING, kSizeFit, L"&Fit to the desktop");
    AppendMenuW(size, MF_SEPARATOR, 0, nullptr);
    for (UINT n = 1; n <= 4; n++) {
        std::wstring label = L"&" + std::to_wstring(n) + L"x  (" + std::to_wstring(640 * n) + L"x" +
                             std::to_wstring(480 * n) + L")";
        AppendMenuW(size, MF_STRING, kSizeFit + n, label.c_str());
    }
    AppendMenuW(aspect, MF_STRING, kAspectPillarbox, L"&4:3, pillarboxed");
    AppendMenuW(aspect, MF_STRING | MF_GRAYED, kAspectWide, L"&Wide (after the look review)");
    AppendMenuW(m, MF_STRING, kFullScreen, L"&Full screen\tF11");
    AppendMenuW(m, MF_POPUP, reinterpret_cast<UINT_PTR>(size), L"&Window size");
    AppendMenuW(m, MF_POPUP, reinterpret_cast<UINT_PTR>(aspect), L"&Aspect");
    AppendMenuW(m, MF_SEPARATOR, 0, nullptr);
    AppendMenuW(m, MF_STRING, kVsync, L"&VSync");
    AppendMenuW(m, MF_STRING, kInterp, L"&In-between frames (the game keeps its own rate)");
    AppendMenuW(m, MF_POPUP, reinterpret_cast<UINT_PTR>(cap), L"Frame-rate c&ap");
    AppendMenuW(m, MF_STRING, kFpsCounter, L"Frame-rate &counter");
    AppendMenuW(m, MF_SEPARATOR, 0, nullptr);
    AppendMenuW(m, MF_POPUP, reinterpret_cast<UINT_PTR>(vmu), L"V&MU screen");
    AppendMenuW(m, MF_STRING | (pvr_pack_available() ? 0u : MF_GRAYED), kTexturePack, L"&Texture pack");
    return m;
}

HMENU audio_menu()
{
    HMENU m = CreatePopupMenu(), volume = CreatePopupMenu();
    for (UINT n = 0; n <= 4; n++) {
        std::wstring label = n ? L"&" + std::to_wstring(n * 25) + L" %" : std::wstring(L"&Mute");
        AppendMenuW(volume, MF_STRING, kVolume0 + n, label.c_str());
    }
    AppendMenuW(m, MF_POPUP, reinterpret_cast<UINT_PTR>(volume), L"&Volume");
    AppendMenuW(m, MF_STRING, kMuteUnfocused, L"Mute when &unfocused");
    return m;
}

HMENU input_menu()
{
    HMENU m = CreatePopupMenu();
    AppendMenuW(m, MF_STRING, kRumble, L"&Rumble");
    AppendMenuW(m, MF_STRING, kPauseUnfocused, L"&Pause when unfocused");
    return m;
}

HMENU help_menu()
{
    HMENU m = CreatePopupMenu();
    AppendMenuW(m, MF_STRING, host::kCopyAgentPrompt, L"&Copy agent prompt\tF9");
    return m;
}

void check(UINT id, bool on) { CheckMenuItem(g_menu, id, MF_BYCOMMAND | (on ? MF_CHECKED : MF_UNCHECKED)); }

// The marks follow the current state each time a menu opens.
void sync()
{
    host::Prefs &p = host::prefs();
    int scale = host::window_scale(), volume = audio_volume();
    check(kFullScreen, host::window_fullscreen());
    for (UINT n = 0; n <= 4; n++) check(kSizeFit + n, scale == static_cast<int>(n));
    check(kAspectPillarbox, true);
    check(kVsync, gfx_vsync() != 0);
    check(kInterp, extra_interp() != 0);
    for (UINT n = 0; n < kFpsCapCount; n++) check(kFpsCap + n, extra_fps() == kFpsCaps[n]);
    check(kFpsCounter, p.fps_counter);
    for (UINT n = 0; n <= 2; n++) check(kVmuScreen + n, gfx_vmu_mode() == static_cast<int>(n));
    check(kTexturePack, pvr_pack_enabled() != 0);
    for (UINT n = 0; n <= 4; n++) check(kVolume0 + n, volume == static_cast<int>(n) * 25);
    check(kMuteUnfocused, p.mute_unfocused);
    check(kRumble, input_rumble() != 0);
    check(kPauseUnfocused, p.pause_unfocused);
}

void command(UINT id)
{
    host::Prefs &p = host::prefs();
    if (id == kFullScreen) {
        host::window_set_fullscreen(!host::window_fullscreen());
    } else if (id >= kSizeFit && id <= kSize4) {
        host::window_set_scale(static_cast<int>(id - kSizeFit));
    } else if (id == kVsync) {
        gfx_set_vsync(!gfx_vsync());
        host::settings_set("vsync", on_off(gfx_vsync() != 0));
    } else if (id == kInterp) {
        extra_set_interp(!extra_interp());
        host::settings_set("present", extra_interp() ? "interp" : "tick");
    } else if (id >= kFpsCap && id < kFpsCap + kFpsCapCount) {
        extra_set_fps(kFpsCaps[id - kFpsCap]);
        host::settings_set("fps", std::to_string(extra_fps()));
    } else if (id == kFpsCounter) {
        p.fps_counter = !p.fps_counter;
        host::settings_set("fps-counter", on_off(p.fps_counter));
    } else if (id >= kVmuScreen && id <= kVmuScreen + 2) {
        gfx_vmu_set_mode(static_cast<int>(id - kVmuScreen));
        host::settings_set("vmu-screen", gfx_vmu_mode_name(gfx_vmu_mode()));
    } else if (id == kTexturePack && pvr_pack_available()) {
        pvr_pack_set_enabled(!pvr_pack_enabled());
        host::settings_set("texture-pack", on_off(pvr_pack_enabled() != 0));
    } else if (id >= kVolume0 && id <= kVolume100) {
        audio_set_volume(static_cast<int>(id - kVolume0) * 25, 0);
        host::settings_set("volume", std::to_string(audio_volume()));
    } else if (id == kMuteUnfocused) {
        p.mute_unfocused = !p.mute_unfocused;
        host::settings_set("mute-unfocused", on_off(p.mute_unfocused));
    } else if (id == kRumble) {
        input_set_rumble(!input_rumble());
        host::settings_set("rumble", on_off(input_rumble() != 0));
    } else if (id == kPauseUnfocused) {
        p.pause_unfocused = !p.pause_unfocused;
        host::settings_set("pause-unfocused", on_off(p.pause_unfocused));
    }
}

LRESULT CALLBACK subclass(HWND hwnd, UINT msg, WPARAM wp, LPARAM lp, UINT_PTR, DWORD_PTR)
{
    intptr_t handled = 0;
    if (host::copy_button_message(hwnd, msg, wp, lp, &handled)) return handled;
    if (msg == WM_INITMENUPOPUP) {
        sync();
    } else if (msg == WM_SYSKEYDOWN && wp >= 'A' && wp <= 'Z' && !(lp & (1 << 30)) && GetMenu(hwnd)) {
        // Alt+letter opens that menu. SDL translates no keys outside text input, so no WM_SYSCHAR comes to do it.
        DefWindowProcW(hwnd, WM_SYSCOMMAND, SC_KEYMENU, static_cast<LPARAM>(wp | 0x20));
        return 0;
    } else if (msg == WM_COMMAND && HIWORD(wp) == 0 && lp == 0) {
        command(LOWORD(wp));
        return 0;
    }
    return DefSubclassProc(hwnd, msg, wp, lp);
}

} // namespace

namespace host {

void menu_attach(void *hwnd)
{
    g_hwnd = static_cast<HWND>(hwnd);
    if (!g_hwnd) return;
    g_menu = CreateMenu();
    AppendMenuW(g_menu, MF_POPUP, reinterpret_cast<UINT_PTR>(display_menu()), L"&Display");
    AppendMenuW(g_menu, MF_POPUP, reinterpret_cast<UINT_PTR>(audio_menu()), L"&Audio");
    AppendMenuW(g_menu, MF_POPUP, reinterpret_cast<UINT_PTR>(input_menu()), L"&Input");
    AppendMenuW(g_menu, MF_POPUP, reinterpret_cast<UINT_PTR>(help_menu()), L"&Help");
    host::copy_button_attach(g_hwnd, g_menu);
    if (!SetWindowSubclass(g_hwnd, subclass, 1, 0)) port_log("host: no menu (the window subclass failed)\n");
}

void menu_show(bool on)
{
    if (!g_hwnd || !g_menu) return;
    SetMenu(g_hwnd, on ? g_menu : nullptr);
    DrawMenuBar(g_hwnd);
}

} // namespace host
