// The menu bar, after Lodoss's desktop_stage_menu (MIT, the user's own): a native Win32 menu on the SDL window,
// reached through a window subclass. Every choice applies at once and goes into the settings file (as the option it
// stands for), so the next run starts the same way. The menu shows while windowed; full screen hides it.
//   Display: full screen, window size, aspect (fit the window, 4:3, 16:9, 21:9), VSync,
//            frame-rate cap, frame-rate counter, then the console's own entries (console_menu_extra: the
//            Dreamcast's VMU screen and texture pack)
//   Audio:   volume, mute when unfocused, then the console's
//   Input:   rumble, pause when unfocused, controls (twin-stick, pad_twin.c; each of the console's buttons), then the
//            console's
//   Game:    auto-load save (autoload.c; greyed for a game that names no load path)
//   Online:  a console with online play (host_online.cpp): play alone, host, join; while a match is on, the entries that
//            would change the game for both players are greyed and refused (Pause when unfocused, Auto-load, Resume)
//   Help:    copy agent prompt (F9; the bar's copy icon is the same command: host_copy_button.cpp)
#include <cctype>
#include <cstring>
#include <string>
#include <windows.h>
#include <commctrl.h>
#include "console.h"
#include "present.h"
#include "game_log.h"
#include "host.h"
#include "host_internal.hpp"
#include "autoload.h"

namespace {

enum : UINT {
    kFullScreen = 100,
    kSizeFit = 110, // kSizeFit + N: a window N x 640x480
    kSize4 = 114,
    kFpsCounter = 120,
    kVsync,
    kAspect = 170, // kAspect + N: kAspects[N]
    kBind = 4000,  // kBind + 32 * action + source: Input > Controls
    kFpsCap = 140, // kFpsCap + N: kFpsCaps[N]
    kExtra = 1000, // kExtra + 16 * item + choice: console_menu_extra's entries
    kVolume0 = 200, // kVolume0 + N: N x 25 %
    kVolume100 = 204,
    kMuteUnfocused = 210,
    kRumble = 300, kPadArt = 301,
    kPauseUnfocused, kTwinStick,
    kResume = 310,
    kAutoLoad = 311,
    kOnline = 320, // kOnline + N: host_online.cpp's entries
};

HWND g_hwnd;
HMENU g_menu;
const PortMenuItem *g_extra;
unsigned g_extra_count;

const char *on_off(bool on) { return on ? "on" : "off"; }

// --aspect: what the Aspect menu offers; the first (fit the window) is the default
struct AspectChoice {
    const wchar_t *label;
    const char *setting;
};
constexpr AspectChoice kAspects[] = {
    {L"&Fit window", "fit"}, {L"&4:3", "4:3"}, {L"&16:9", "16:9"}, {L"&21:9", "21:9"},
};
constexpr UINT kAspectCount = sizeof kAspects / sizeof kAspects[0];

// --fps N: what the Frame-rate cap menu offers (0: none, as many as the display takes)
constexpr unsigned kFpsCaps[] = {0, 60, 120, 144, 240};
constexpr UINT kFpsCapCount = sizeof kFpsCaps / sizeof kFpsCaps[0];

// A label: UTF-8, since a texture pack's is its folder name.
std::wstring wide(const char *s)
{
    int n = MultiByteToWideChar(CP_UTF8, 0, s, -1, nullptr, 0);
    std::wstring w(n > 0 ? static_cast<size_t>(n - 1) : 0u, L'\0');
    if (n > 1) MultiByteToWideChar(CP_UTF8, 0, s, -1, w.data(), n);
    return w;
}

// The console's entries for a menu, after a separator; the ones it cannot apply are greyed.
void append_extras(HMENU m, const char *menu)
{
    bool first = true;
    for (unsigned i = 0; i < g_extra_count; i++) {
        const PortMenuItem &e = g_extra[i];
        if (std::strcmp(e.menu, menu)) continue;
        if (first) AppendMenuW(m, MF_SEPARATOR, 0, nullptr);
        first = false;
        UINT grey = e.available && !e.available() ? MF_GRAYED : 0u;
        if (e.kind == PORT_MENU_CHOICE) {
            HMENU sub = CreatePopupMenu();
            for (unsigned n = 0; n < e.n_choices; n++)
                AppendMenuW(sub, MF_STRING, kExtra + 16 * i + n, wide(e.choices[n]).c_str());
            AppendMenuW(m, MF_POPUP | grey, reinterpret_cast<UINT_PTR>(sub), wide(e.label).c_str());
        } else {
            AppendMenuW(m, MF_STRING | grey, kExtra + 16 * i, wide(e.label).c_str());
        }
    }
}

HMENU display_menu()
{
    HMENU m = CreatePopupMenu(), size = CreatePopupMenu(), aspect = CreatePopupMenu(), cap = CreatePopupMenu();
    AppendMenuW(cap, MF_STRING, kFpsCap, L"&None (as many as the display takes)");
    for (UINT n = 1; n < kFpsCapCount; n++) {
        std::wstring label = L"&" + std::to_wstring(kFpsCaps[n]) + L" frames a second";
        AppendMenuW(cap, MF_STRING, kFpsCap + n, label.c_str());
    }
    AppendMenuW(size, MF_STRING, kSizeFit, L"&Fit to the desktop");
    AppendMenuW(size, MF_SEPARATOR, 0, nullptr);
    for (UINT n = 1; n <= 4; n++) {
        std::wstring label = L"&" + std::to_wstring(n) + L"x  (" + std::to_wstring(port_console.native_w * n) + L"x" +
                             std::to_wstring(port_console.native_h * n) + L")";
        AppendMenuW(size, MF_STRING, kSizeFit + n, label.c_str());
    }
    for (UINT n = 0; n < kAspectCount; n++) AppendMenuW(aspect, MF_STRING, kAspect + n, kAspects[n].label);
    AppendMenuW(m, MF_STRING, kFullScreen, L"&Full screen\tF11");
    AppendMenuW(m, MF_POPUP, reinterpret_cast<UINT_PTR>(size), L"&Window size");
    AppendMenuW(m, MF_POPUP, reinterpret_cast<UINT_PTR>(aspect), L"&Aspect");
    AppendMenuW(m, MF_SEPARATOR, 0, nullptr);
    AppendMenuW(m, MF_STRING, kVsync, L"&VSync");
    AppendMenuW(m, MF_POPUP | (port_console.flags & PORT_CONSOLE_FPSCAP ? 0u : MF_GRAYED), reinterpret_cast<UINT_PTR>(cap),
                L"Frame-rate c&ap");
    AppendMenuW(m, MF_STRING, kFpsCounter, L"Frame-rate &counter");
    append_extras(m, "Display");
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
    append_extras(m, "Audio");
    return m;
}

HMENU input_menu()
{
    HMENU m = CreatePopupMenu();
    AppendMenuW(m, MF_STRING, kRumble, L"&Rumble");
    AppendMenuW(m, MF_STRING, kPauseUnfocused, L"&Pause when unfocused");
    AppendMenuW(m, MF_STRING, kPadArt, L"Controller p&ictures");
    // Controls: each button of the console's own pad (console_actions) takes any host button
    unsigned n = 0;
    const PortAction *actions = console_actions(&n);
    HMENU controls = CreatePopupMenu();
    AppendMenuW(controls, MF_STRING, kTwinStick, L"&Twin-stick (in fights: right stick turns, left stick sidesteps)");
    AppendMenuW(controls, MF_SEPARATOR, 0, nullptr);
    for (unsigned i = 0; actions && i < n && i < 32; i++) {
        HMENU one = CreatePopupMenu();
        for (unsigned s = 0; s < input_source_count(); s++)
            AppendMenuW(one, MF_STRING, kBind + 32 * i + s, s ? wide(input_source_name(s)).c_str() : L"(none)");
        AppendMenuW(controls, MF_POPUP, reinterpret_cast<UINT_PTR>(one), wide(actions[i].name).c_str());
    }
    AppendMenuW(m, MF_POPUP | (n ? 0u : MF_GRAYED), reinterpret_cast<UINT_PTR>(controls), L"&Controls");
    append_extras(m, "Input");
    return m;
}

HMENU game_menu()
{
    HMENU m = CreatePopupMenu();
    AppendMenuW(m, MF_STRING, kAutoLoad, L"&Auto-load save (from the next start)");
    return m;
}

HMENU help_menu()
{
    HMENU m = CreatePopupMenu();
    AppendMenuW(m, MF_STRING, host::kCopyAgentPrompt, L"&Copy agent prompt\tF9");
    AppendMenuW(m, MF_STRING, kResume, L"&Resume before the last crash...");
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
    for (UINT n = 0; n < kAspectCount; n++) check(kAspect + n, gfx_aspect_text() == std::string(kAspects[n].setting));
    check(kVsync, gfx_vsync() != 0);
    for (UINT n = 0; n < kFpsCapCount; n++) check(kFpsCap + n, extra_fps() == kFpsCaps[n]);
    check(kFpsCounter, p.fps_counter);
    for (unsigned i = 0; i < g_extra_count; i++) {
        const PortMenuItem &e = g_extra[i];
        if (e.kind == PORT_MENU_CHOICE) {
            for (unsigned n = 0; n < e.n_choices; n++) check(kExtra + 16 * i + n, e.get() == static_cast<int>(n));
        } else {
            check(kExtra + 16 * i, e.get() != 0);
        }
    }
    for (UINT n = 0; n <= 4; n++) check(kVolume0 + n, volume == static_cast<int>(n) * 25);
    check(kMuteUnfocused, p.mute_unfocused);
    check(kRumble, input_rumble() != 0);
    check(kPauseUnfocused, p.pause_unfocused);
    check(kPadArt, pad_art_on() != 0);
    check(kTwinStick, port_twin_get() != 0);
    EnableMenuItem(g_menu, kTwinStick, MF_BYCOMMAND | (port_twin_available() ? MF_ENABLED : MF_GRAYED));
    std::filesystem::path rec;
    unsigned end = 0;
    EnableMenuItem(g_menu, kResume, MF_BYCOMMAND | (host::resume_find(&rec, &end) && !host::online_locked() ? MF_ENABLED : MF_GRAYED));
    EnableMenuItem(g_menu, kPauseUnfocused, MF_BYCOMMAND | (host::online_locked() ? MF_GRAYED : MF_ENABLED));
    if (port_console.flags & PORT_CONSOLE_ONLINE) host::online_sync(g_menu);
    check(kAutoLoad, autoload_get() != 0);
    EnableMenuItem(g_menu, kAutoLoad, MF_BYCOMMAND | (autoload_available() && !host::online_locked() ? MF_ENABLED : MF_GRAYED));
    unsigned na = 0;
    console_actions(&na);
    for (unsigned i = 0; i < na && i < 32; i++)
        for (unsigned s = 0; s < input_source_count(); s++) check(kBind + 32 * i + s, s ? (input_bind_get(i) >> s & 1u) != 0 : input_bind_get(i) == 0);
}

// A menu choice in words for the game log: "Display > Full screen" (the labels without their & and shortcut text).
std::string menu_words(UINT id)
{
    auto plain = [](const wchar_t *w) {
        std::string s;
        for (; *w && *w != L'\t'; w++)
            if (*w != L'&') s += *w < 0x80 ? static_cast<char>(*w) : '?';
        return s;
    };
    wchar_t text[128];
    for (int i = 0, n = GetMenuItemCount(g_menu); i < n; i++) {
        HMENU sub = GetSubMenu(g_menu, i);
        if (sub && GetMenuStringW(sub, id, text, 128, MF_BYCOMMAND) > 0) {
            std::string item = plain(text);
            if (GetMenuStringW(g_menu, static_cast<UINT>(i), text, 128, MF_BYPOSITION) > 0) item = plain(text) + " > " + item;
            return item;
        }
    }
    return "command " + std::to_string(id);
}

void command(UINT id)
{
    host::Prefs &p = host::prefs();
    game_log_line("menu: %s", menu_words(id).c_str());
    if (host::online_command(g_hwnd, id)) return;
    if (host::online_locked() && (id == kPauseUnfocused || id == kAutoLoad || id == kResume)) {
        game_log_line("menu: not applied: %s", host::online_locked());
        return;
    }
    if (id == kFullScreen) {
        host::window_set_fullscreen(!host::window_fullscreen());
    } else if (id >= kSizeFit && id <= kSize4) {
        host::window_set_scale(static_cast<int>(id - kSizeFit));
    } else if (id >= kAspect && id < kAspect + kAspectCount) {
        gfx_set_aspect(kAspects[id - kAspect].setting);
        host::settings_set("aspect", gfx_aspect_text());
    } else if (id == kVsync) {
        gfx_set_vsync(!gfx_vsync());
        host::settings_set("vsync", on_off(gfx_vsync() != 0));
    } else if (id >= kFpsCap && id < kFpsCap + kFpsCapCount) {
        extra_set_fps(kFpsCaps[id - kFpsCap]);
        host::settings_set("fps", std::to_string(extra_fps()));
    } else if (id == kFpsCounter) {
        p.fps_counter = !p.fps_counter;
        host::settings_set("fps-counter", on_off(p.fps_counter));
    } else if (id >= kExtra && (id - kExtra) / 16 < g_extra_count) {
        const PortMenuItem &e = g_extra[(id - kExtra) / 16];
        if (e.available && !e.available()) {
            game_log_line("menu: not applied: %s", host::online_locked() ? host::online_locked() : "the game cannot apply it now");
            return;
        }
        if (e.kind == PORT_MENU_CHOICE) {
            e.set(static_cast<int>((id - kExtra) % 16));
            host::settings_set(e.setting, e.values[e.get()]);
        } else {
            e.set(!e.get());
            host::settings_set(e.setting, on_off(e.get() != 0));
        }
    } else if (id >= kVolume0 && id <= kVolume100) {
        audio_set_volume(static_cast<int>(id - kVolume0) * 25, 0);
        host::settings_set("volume", std::to_string(audio_volume()));
    } else if (id == kMuteUnfocused) {
        p.mute_unfocused = !p.mute_unfocused;
        host::settings_set("mute-unfocused", on_off(p.mute_unfocused));
    } else if (id == kRumble) {
        input_set_rumble(!input_rumble());
        host::settings_set("rumble", on_off(input_rumble() != 0));
    } else if (id == kPadArt) {
        pad_art_set(!pad_art_on());
        host::settings_set("pad-art", on_off(pad_art_on() != 0));
    } else if (id == kAutoLoad) {
        if (!autoload_available()) return;
        autoload_set(!autoload_get());
        host::settings_set("auto-load", on_off(autoload_get() != 0));
    } else if (id == kPauseUnfocused) {
        p.pause_unfocused = !p.pause_unfocused;
        host::settings_set("pause-unfocused", on_off(p.pause_unfocused));
    } else if (id == kTwinStick) {
        port_twin_set(!port_twin_get());
        host::settings_set("twin-stick", on_off(port_twin_get() != 0));
    } else if (id == kResume) {
        host::resume_start(g_hwnd);
    } else if (id >= kBind) {
        unsigned na = 0, i = (id - kBind) / 32, s = (id - kBind) % 32;
        const PortAction *actions = console_actions(&na);
        if (i >= na) return;
        input_bind_toggle(i, s);
        char text[128];
        std::string key = std::string("bind-") + actions[i].name, value = input_bind_text(i, text, sizeof text);
        for (char &c : key) c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
        for (char &c : value) c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
        host::settings_set(key, value);
    }
}

LRESULT CALLBACK subclass(HWND hwnd, UINT msg, WPARAM wp, LPARAM lp, UINT_PTR, DWORD_PTR)
{
    intptr_t handled = 0;
    if (host::copy_button_message(hwnd, msg, wp, lp, &handled) || host::counter_message(hwnd, msg, wp, lp, &handled)) return handled;
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
    g_extra = console_menu_extra(&g_extra_count);
    g_menu = CreateMenu();
    AppendMenuW(g_menu, MF_POPUP, reinterpret_cast<UINT_PTR>(display_menu()), L"&Display");
    AppendMenuW(g_menu, MF_POPUP, reinterpret_cast<UINT_PTR>(audio_menu()), L"&Audio");
    AppendMenuW(g_menu, MF_POPUP, reinterpret_cast<UINT_PTR>(input_menu()), L"&Input");
    AppendMenuW(g_menu, MF_POPUP, reinterpret_cast<UINT_PTR>(game_menu()), L"&Game");
    if (port_console.flags & PORT_CONSOLE_ONLINE)
        AppendMenuW(g_menu, MF_POPUP, reinterpret_cast<UINT_PTR>(host::online_menu(kOnline)), L"&Online");
    AppendMenuW(g_menu, MF_POPUP, reinterpret_cast<UINT_PTR>(help_menu()), L"&Help");
    host::counter_attach(g_hwnd, g_menu);
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
