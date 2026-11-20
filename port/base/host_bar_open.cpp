// Opening the Phangrade title bar's menus (host_bar.cpp is the bar itself): a click, F10, Alt+letter, Esc or a
// controller's Guide asks for one, and the next pump opens it (bar_poll, outside the lock, where the native menu's own loop
// ran). Guide and Esc open them only while the player has not given them a game action. The game waits while a menu is
// open, as it does under the native menu; what the menu took (keys, buttons) is then dropped, not given to the game.
// Full screen: no bar. The pointer at the screen's top edge, Guide or Esc shows it as a popup over the picture, until it
// is used or the pointer leaves it.
// Nearest existing: host_menu.cpp's native menu loop (Windows' own) - it cannot be opened from a controller.
#include "host_bar.hpp"
#include <windowsx.h>
#include <SDL3/SDL.h>
#include <string>
#include "console.h"
#include "game_log.h"
#include "host.h"
#include "host_internal.hpp"

namespace bar {
namespace {

std::string narrow(const std::wstring &w)
{
    std::string s;
    for (wchar_t c : w) s += c < 0x80 ? static_cast<char>(c) : '?';
    return s;
}

LRESULT CALLBACK pop_proc(HWND h, UINT msg, WPARAM wp, LPARAM lp)
{
    int x = GET_X_LPARAM(lp), y = GET_Y_LPARAM(lp);
    switch (msg) {
    case WM_PAINT: {
        PAINTSTRUCT ps;
        HDC dc = BeginPaint(h, &ps);
        RECT r{};
        GetClientRect(h, &r);
        paint_into(dc, 0, 0, r.right);
        EndPaint(h, &ps);
        return 0;
    }
    case WM_MOUSEACTIVATE: return MA_NOACTIVATE;
    case WM_SETCURSOR: SetCursor(LoadCursorW(nullptr, MAKEINTRESOURCEW(32512))); return TRUE;
    case WM_MOUSEMOVE: {
        int p = part_at(x, y);
        set_hot(p, g.s.pressed == p ? p : int(kNone));
        tip(p, origin());
        return 0;
    }
    case WM_LBUTTONDOWN: {
        int p = part_at(x, y);
        if (p >= 0) act(p);
        else if (p != kNone) set_hot(p, p);
        return 0;
    }
    case WM_LBUTTONUP: {
        int p = part_at(x, y);
        if (p < 0 && p != kNone && p == g.s.pressed) act(p);
        set_hot(p, kNone);
        return 0;
    }
    case WM_TIMER: { // hidden once the pointer has left it
        POINT c;
        RECT r{};
        GetCursorPos(&c);
        GetWindowRect(h, &r);
        if (!g.tracking && !shot_on() && !PtInRect(&r, c)) {
            pop_show(false);
            tip(kNone, POINT{});
        }
        return 0;
    }
    default: return DefWindowProcW(h, msg, wp, lp);
    }
}

bool guide_free() // no game action on Guide: the player's key screen (input_ex_assign.c) or Input > Controls
{
    for (unsigned row = 0; row < PORT_PAD_EX_ROWS; row++)
        if (port_pad_ex_row_bit(row) == HOST_PAD_GUIDE)
            for (int p = 0; p < HOST_PADS; p++)
                if (port_pad_ex_assign_get(p, row)) return false;
    unsigned na = 0;
    console_actions(&na);
    for (unsigned s = 1; s < input_source_count(); s++)
        if (!_stricmp(input_source_name(s), "GUIDE"))
            for (unsigned i = 0; i < na && i < 32; i++)
                if (input_bind_get(i) >> s & 1u) return false;
    return true;
}

bool escape_free() // no keyboard layout entry on Esc (input_kbm.c), and the game's text field shut (Esc is its)
{
    if (host_text_full_on()) return false;
    for (unsigned i = 0; i < input_kbm_count(); i++)
        if (!_stricmp(input_kbm_name(i), "ESCAPE") && input_kbm_get(i)) return false;
    return true;
}

void open_from_keys()
{
    if (!g.slim || !input_live() || g.tracking || g.request >= 0) return;
    if (g.full) pop_show(true);
    lay();
    request(0, true);
}

void hot_title(int t)
{
    g.s.open = t;
    redraw();
}

void run(int first, bool keys)
{
    if (g.full) pop_show(true);
    g.tracking = true;
    // as the native menu: its marks set, the pointer let go (host_menu.cpp's subclass)
    SendMessageW(g.hwnd, WM_ENTERMENULOOP, FALSE, 0);
    SendMessageW(g.hwnd, WM_INITMENUPOPUP, reinterpret_cast<WPARAM>(g.menu), 0);
    Track t{g.hwnd, g.menu, {}, g.l.index, dpi(), first, keys, hot_title};
    POINT o = origin();
    for (RECT r : g.l.titles) {
        OffsetRect(&r, o.x, o.y);
        t.titles.push_back(r);
    }
    game_log_line("bar: %s menu opened (%s)", narrow(g.l.label[first]).c_str(), keys ? "keys or controller" : "pointer");
    g.s.hot = kNone;
    tip(kNone, POINT{});
    unsigned id = track(t);
    g.s.open = kNone;
    g.tracking = false;
    redraw();
    SendMessageW(g.hwnd, WM_EXITMENULOOP, FALSE, 0);
    // what the menu took is not the game's: keys and buttons read while it was open
    SDL_FlushEvents(SDL_EVENT_KEY_DOWN, SDL_EVENT_KEY_UP);
    SDL_FlushEvents(SDL_EVENT_JOYSTICK_AXIS_MOTION, SDL_EVENT_JOYSTICK_BUTTON_UP);
    SDL_FlushEvents(SDL_EVENT_GAMEPAD_AXIS_MOTION, SDL_EVENT_GAMEPAD_BUTTON_UP);
    if (id) SendMessageW(g.hwnd, WM_COMMAND, MAKEWPARAM(id, 0), 0); // as the native menu's choice (host_menu.cpp)
}

void shots() // DP_BAR_SHOT (host_bar_shot.cpp): the bar closed, a menu, full screen with the bar, then the window closes
{
    static int n;
    RECT r{};
    MONITORINFO mi{};
    mi.cbSize = sizeof mi;
    switch (++n) {
    case 600: GetWindowRect(g.hwnd, &r); capture(r, L"bar_closed.bmp"); break;
    case 610: shot_menu(L"bar_menu.bmp"); lay(); request(0, true); break;
    case 640: PostMessageW(g.hwnd, WM_COMMAND, host::kMenuFullScreen, 0); break;
    case 760: pop_show(true); break;
    case 780:
        GetMonitorInfoW(MonitorFromWindow(g.hwnd, MONITOR_DEFAULTTONEAREST), &mi);
        capture(mi.rcMonitor, L"bar_full.bmp");
        break;
    case 790: shot_menu(L"bar_full_menu.bmp"); request(1, true); break;
    case 830: PostMessageW(g.hwnd, WM_SYSCOMMAND, SC_CLOSE, 0); break;
    default: break;
    }
}

} // namespace

void pop_make()
{
    WNDCLASSEXW wc{};
    wc.cbSize = sizeof wc;
    wc.lpfnWndProc = pop_proc;
    wc.hInstance = GetModuleHandleW(nullptr);
    wc.hCursor = LoadCursorW(nullptr, MAKEINTRESOURCEW(32512));
    wc.lpszClassName = L"PhangradeTitleBar";
    RegisterClassExW(&wc);
    g.pop = CreateWindowExW(WS_EX_TOOLWINDOW | WS_EX_TOPMOST | WS_EX_NOACTIVATE, wc.lpszClassName, L"", WS_POPUP, 0, 0, 1, 1,
                            g.hwnd, nullptr, wc.hInstance, nullptr);
}

void pop_show(bool on)
{
    if (!g.pop || on == g.pop_shown) return;
    g.pop_shown = on;
    if (on) {
        MONITORINFO mi{};
        mi.cbSize = sizeof mi;
        GetMonitorInfoW(MonitorFromWindow(g.hwnd, MONITOR_DEFAULTTONEAREST), &mi);
        SetWindowPos(g.pop, HWND_TOPMOST, mi.rcMonitor.left, mi.rcMonitor.top, mi.rcMonitor.right - mi.rcMonitor.left,
                     px(dpi(), kHeight), SWP_NOACTIVATE | SWP_SHOWWINDOW);
        SetTimer(g.pop, 1, 250, nullptr);
    } else {
        KillTimer(g.pop, 1);
        ShowWindow(g.pop, SW_HIDE);
        g.s.hot = g.s.pressed = kNone;
    }
}

} // namespace bar

namespace host {

using namespace bar;

void bar_poll()
{
    if (g.slim && shot_on()) shots();
    if (g.request < 0) return;
    int first = g.request;
    g.request = -1;
    if (g.slim && first < static_cast<int>(g.l.titles.size())) run(first, g.request_keys);
}

void bar_event(const void *sdl_event)
{
    const SDL_Event &e = *static_cast<const SDL_Event *>(sdl_event);
    if (!g.slim) return;
    if (e.type == SDL_EVENT_KEY_DOWN && !e.key.repeat && e.key.key == SDLK_ESCAPE && escape_free()) open_from_keys();
    if (e.type == SDL_EVENT_GAMEPAD_BUTTON_DOWN && e.gbutton.button == SDL_GAMEPAD_BUTTON_GUIDE && guide_free()) open_from_keys();
    if (e.type == SDL_EVENT_MOUSE_MOTION && g.full && !g.pop_shown && e.motion.y <= 1.0f &&
        !SDL_GetWindowRelativeMouseMode(SDL_GetWindowFromID(e.motion.windowID)))
        pop_show(true); // the pointer is the player's in relative mode: no bar
}

} // namespace host
