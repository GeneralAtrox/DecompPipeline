// The Phangrade title bar (option A, the user's choice of 2026-11-12): one slim dark bar in place of both the Windows
// title bar and the native menu bar. Left to right: the Phangrade mark, the game's name, the menus (host_menu.cpp's one
// table, with the console's and the game's entries), the frame-rate reading, the Copy agent prompt icon, and minimise,
// maximise and close. This file is the window's frame (host_bar_open.cpp opens the menus and shows the bar in full screen).
//   Windowed, the bar is the window's non-client strip, where the native menu bar was. The client area (the swap chain,
//   the game's picture) is untouched: the window grows by the bar, and the picture keeps its size and its pixels. The
//   Windows frame drawing is off for the window (DWMNCRP_DISABLED), and the bar is drawn with GDI only when it changes,
//   so a frame costs nothing more. Drag, double-click, snap, resizing from the edges, dpi and monitors are the Windows
//   ones, reached through the hit test. Square corners: a rounded window would cut the picture's.
// menu-bar=native (--menu-bar native, or Display > Phangrade title bar off) brings back the Windows title bar and native
// menu at once.
// Nearest existing: host_menu.cpp's native bar - kept as the native choice; this bar reads the HMENU that it builds.
#include "host_bar.hpp"
#include <windowsx.h>
#include <commctrl.h>
#include <cstring>
#include "game_log.h"
#include "host.h"
#include "host_internal.hpp"

namespace bar {

Bar g;

unsigned dpi() { return g.hwnd ? GetDpiForWindow(g.hwnd) : 96u; }
bool framed() { return g.slim && g.hwnd && (GetWindowLongW(g.hwnd, GWL_STYLE) & WS_CAPTION) == WS_CAPTION; }

namespace {

void dwm_policy(HWND h, bool app_draws)
{
    using Fn = HRESULT(WINAPI *)(HWND, DWORD, LPCVOID, DWORD);
    static HMODULE dll = LoadLibraryExW(L"dwmapi.dll", nullptr, LOAD_LIBRARY_SEARCH_SYSTEM32);
    static Fn set = dll ? reinterpret_cast<Fn>(GetProcAddress(dll, "DwmSetWindowAttribute")) : nullptr;
    if (!set) return;
    DWORD policy = app_draws ? 2u : 0u; // DWMNCRP_DISABLED: the bar draws the frame; DWMNCRP_USEWINDOWSTYLE
    set(h, 2 /* DWMWA_NCRENDERING_POLICY */, &policy, sizeof policy);
    DWORD corner = app_draws ? 1u : 0u; // DWMWCP_DONOTROUND
    set(h, 33 /* DWMWA_WINDOW_CORNER_PREFERENCE */, &corner, sizeof corner);
}

int inset() // a maximised window reaches past its monitor by its frame
{
    if (!IsZoomed(g.hwnd)) return 0;
    return GetSystemMetricsForDpi(SM_CXFRAME, dpi()) + GetSystemMetricsForDpi(SM_CXPADDEDBORDER, dpi());
}

RECT bar_rect() // in window coordinates
{
    RECT w{};
    GetWindowRect(g.hwnd, &w);
    int in = inset();
    return RECT{in, in, (w.right - w.left) - in, in + px(dpi(), kHeight)};
}

void paint_frame() // the strip, and the 1-pixel edge round the picture
{
    if (!framed()) return;
    HDC dc = GetWindowDC(g.hwnd);
    if (!dc) return;
    RECT w{};
    GetWindowRect(g.hwnd, &w);
    int W = w.right - w.left, H = w.bottom - w.top;
    RECT b = bar_rect();
    paint_into(dc, b.left, b.top, b.right - b.left);
    if (!IsZoomed(g.hwnd)) {
        fill(dc, RECT{0, b.bottom, 1, H}, kLine);
        fill(dc, RECT{W - 1, b.bottom, W, H}, kLine);
        fill(dc, RECT{0, H - 1, W, H}, kLine);
    }
    ReleaseDC(g.hwnd, dc);
}

LRESULT hit_test(LPARAM lp)
{
    RECT w{};
    GetWindowRect(g.hwnd, &w);
    int x = GET_X_LPARAM(lp) - w.left, y = GET_Y_LPARAM(lp) - w.top, W = w.right - w.left, H = w.bottom - w.top;
    RECT b = bar_rect();
    if (!IsZoomed(g.hwnd)) {
        int e = px(dpi(), 6), top = px(dpi(), 4);
        bool l = x < e, r = x >= W - e, t = y < top, bo = y >= H - e;
        if (t) return l ? HTTOPLEFT : r ? HTTOPRIGHT : HTTOP;
        if (y < b.bottom || !input_kbm_capture_wanted()) { // in the picture only while the pointer is not the player's
            if (bo) return l ? HTBOTTOMLEFT : r ? HTBOTTOMRIGHT : HTBOTTOM;
            if (l) return HTLEFT;
            if (r) return HTRIGHT;
        }
    }
    if (y < b.top || y >= b.bottom) return HTCLIENT;
    switch (hit(g.l, x - b.left, y - b.top)) {
    case kMin: return HTMINBUTTON;
    case kMax: return HTMAXBUTTON; // Windows 11 offers its snap layouts here
    case kClose: return HTCLOSE;
    case kName: case kMark: case kPillPart: case kNone: return HTCAPTION;
    default: return HTMENU; // a title or the copy icon
    }
}

bool ours(WPARAM ht) { return ht == HTMINBUTTON || ht == HTMAXBUTTON || ht == HTCLOSE || ht == HTMENU; }

} // namespace

void paint_into(HDC dc, int x, int y, int w)
{
    wchar_t t[160] = L"";
    GetWindowTextW(g.hwnd, t, 160);
    g.s.title = t;
    g.s.maxed = IsZoomed(g.hwnd) != 0;
    g.s.full = g.full;
    host::counter_text(g.s.shown, g.s.game, &g.s.slow);
    layout(g.menu, g.s, w, dpi(), &g.l);
    HDC mem = CreateCompatibleDC(dc);
    HBITMAP bmp = CreateCompatibleBitmap(dc, w, g.l.h);
    HGDIOBJ old = SelectObject(mem, bmp);
    paint(mem, g.l, g.s);
    BitBlt(dc, x, y, w, g.l.h, mem, 0, 0, SRCCOPY);
    SelectObject(mem, old);
    DeleteObject(bmp);
    DeleteDC(mem);
}

void redraw()
{
    if (framed()) {
        paint_frame();
    } else if (g.pop_shown) {
        HDC dc = GetDC(g.pop);
        RECT r{};
        GetClientRect(g.pop, &r);
        paint_into(dc, 0, 0, r.right);
        ReleaseDC(g.pop, dc);
    }
}

void set_hot(int hot, int pressed)
{
    if (hot == g.s.hot && pressed == g.s.pressed) return;
    g.s.hot = hot;
    g.s.pressed = pressed;
    redraw();
}

int part_at(int x, int y)
{
    int p = hit(g.l, x, y);
    return p == kName || p == kMark || p == kPillPart ? int(kNone) : p;
}

void lay()
{
    if (!g.l.titles.empty()) return;
    RECT r{};
    GetWindowRect(g.hwnd, &r);
    layout(g.menu, g.s, r.right - r.left, dpi(), &g.l);
}

void request(int title, bool keys)
{
    if (title < 0 || title >= static_cast<int>(g.l.titles.size())) return;
    g.request = title;
    g.request_keys = keys;
}

void act(int part) // a press on the bar, as the window's own commands
{
    switch (part) {
    case kMin: PostMessageW(g.hwnd, WM_SYSCOMMAND, SC_MINIMIZE, 0); break;
    case kMax:
        if (g.full) PostMessageW(g.hwnd, WM_COMMAND, host::kMenuFullScreen, 0); // leaves full screen
        else PostMessageW(g.hwnd, WM_SYSCOMMAND, IsZoomed(g.hwnd) ? SC_RESTORE : SC_MAXIMIZE, 0);
        break;
    case kClose: PostMessageW(g.hwnd, WM_SYSCOMMAND, SC_CLOSE, 0); break;
    case kCopy: PostMessageW(g.hwnd, WM_COMMAND, host::kCopyAgentPrompt, 0); break; // host_copy_button.cpp
    default: request(part, false); break;
    }
}

void tip(int part, POINT o) // the copy icon's tooltip (host_copy_button.cpp)
{
    RECT r = g.l.copy;
    OffsetRect(&r, o.x, o.y);
    host::copy_button_tip(g.hwnd, part == kCopy ? &r : nullptr);
}

POINT origin()
{
    RECT w{};
    if (!framed()) {
        GetWindowRect(g.pop, &w);
        return POINT{w.left, w.top};
    }
    GetWindowRect(g.hwnd, &w);
    RECT b = bar_rect();
    return POINT{w.left + b.left, w.top + b.top};
}

} // namespace bar

namespace host {

using namespace bar;

bool bar_on() { return g.slim; }

int bar_option(const char *arg, const char *value)
{
    if (std::strcmp(arg, "--menu-bar") || !value) return 0;
    if (!std::strcmp(value, "native")) g.slim = false;
    else if (!std::strcmp(value, "slim")) g.slim = true;
    else return 0;
    return 2;
}

void bar_attach(void *hwnd, void *menu)
{
    g.hwnd = static_cast<HWND>(hwnd);
    g.menu = static_cast<HMENU>(menu);
    pop_make();
    if (int n = GetMenuItemCount(g.menu); n > 0) // Help, the last title: how to open the menus without a pointer
        if (HMENU help = GetSubMenu(g.menu, n - 1)) {
            AppendMenuW(help, MF_SEPARATOR, 0, nullptr);
            AppendMenuW(help, MF_STRING | MF_GRAYED, 0, L"Open the title bar's menus\tF10, Esc or Guide");
        }
    if (g.slim) dwm_policy(g.hwnd, true);
    game_log_line("window: %s", g.slim ? "Phangrade title bar" : "native title and menu bar (menu-bar=native)");
}

void bar_show(bool windowed)
{
    g.full = !windowed;
    if (!g.slim || !g.hwnd) return;
    if (windowed) pop_show(false);
    SetWindowPos(g.hwnd, nullptr, 0, 0, 0, 0, SWP_FRAMECHANGED | SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE);
}

bool bar_size(void *hwnd, int outer_w, int outer_h) // SDL would size the window by the Windows frame, not the bar's
{
    if (!g.slim || !hwnd) return false;
    SetWindowPos(static_cast<HWND>(hwnd), nullptr, 0, 0, outer_w, outer_h, SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE);
    return true;
}

void bar_set(bool slim)
{
    if (slim == g.slim || !g.hwnd) return;
    g.slim = slim;
    game_log_line("menu: Display > Phangrade title bar %s", slim ? "on" : "off (the native title and menu bar)");
    pop_show(false);
    dwm_policy(g.hwnd, slim);
    if (!slim) SetWindowPos(g.hwnd, nullptr, 0, 0, 0, 0, SWP_FRAMECHANGED | SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE);
    menu_show(!g.full);
    window_replace();
    settings_set("menu-bar", slim ? "slim" : "native");
}

void bar_redraw()
{
    if (g.slim) redraw();
}

bool bar_message(void *hwnd_ptr, unsigned msg, uintptr_t wp, intptr_t lp, intptr_t *result)
{
    HWND hwnd = static_cast<HWND>(hwnd_ptr);
    if (msg == WM_INITMENUPOPUP) CheckMenuItem(g.menu, kMenuSlimBar, MF_BYCOMMAND | (g.slim ? MF_CHECKED : MF_UNCHECKED));
    if (msg == WM_COMMAND && HIWORD(wp) == 0 && lp == 0 && LOWORD(wp) == kMenuSlimBar) { // Display > Phangrade title bar
        bar_set(!g.slim);
        *result = 0;
        return true;
    }
    if (!g.slim) return false;
    if (msg == WM_SYSKEYDOWN) { // F10 and Alt+letter open the menus, windowed or full screen
        int found = -1;
        lay();
        if (wp == VK_F10) found = 0;
        else if (wp >= 'A' && wp <= 'Z' && !(lp & (1 << 30)))
            for (size_t i = 0; i < g.l.mnemonic.size(); i++)
                if (g.l.mnemonic[i] == static_cast<wchar_t>(wp)) found = static_cast<int>(i);
        if (found < 0) return false;
        if (g.full) pop_show(true);
        request(found, true);
        *result = 0;
        return true;
    }
    if (msg == WM_SYSCOMMAND && (wp & 0xFFF0) == SC_KEYMENU) {
        *result = 0;
        return true;
    }
    if (!framed()) return false;
    auto bx = [&] { return GET_X_LPARAM(lp) - origin().x; };
    auto by = [&] { return GET_Y_LPARAM(lp) - origin().y; };
    switch (msg) {
    case WM_NCCALCSIZE: {
        RECT *r = wp ? &reinterpret_cast<NCCALCSIZE_PARAMS *>(lp)->rgrc[0] : reinterpret_cast<RECT *>(lp);
        int f = IsZoomed(hwnd) ? inset() : 1;
        r->left += f;
        r->right -= f;
        r->bottom -= f;
        r->top += (IsZoomed(hwnd) ? f : 0) + px(dpi(), kHeight);
        *result = 0;
        return true;
    }
    case WM_NCHITTEST: *result = hit_test(lp); return true;
    case WM_NCPAINT: paint_frame(); *result = 0; return true;
    case WM_NCACTIVATE:
        g.s.active = wp != 0;
        *result = DefSubclassProc(hwnd, msg, wp, -1); // -1: Windows draws no caption
        paint_frame();
        return true;
    case WM_SETTEXT: case WM_SETICON: { // Windows draws its caption as the text changes: not while it is hidden from it
        LONG_PTR style = GetWindowLongPtrW(hwnd, GWL_STYLE);
        SetWindowLongPtrW(hwnd, GWL_STYLE, style & ~static_cast<LONG_PTR>(WS_VISIBLE));
        *result = DefSubclassProc(hwnd, msg, wp, lp);
        SetWindowLongPtrW(hwnd, GWL_STYLE, style);
        paint_frame();
        return true;
    }
    case 0x00AE: case 0x00AF: *result = 0; return true; // WM_NCUAHDRAWCAPTION, WM_NCUAHDRAWFRAME: the theme's drawing
    case WM_NCMOUSEMOVE: {
        TRACKMOUSEEVENT t{sizeof t, TME_LEAVE | TME_NONCLIENT, hwnd, 0};
        TrackMouseEvent(&t);
        int p = ours(wp) ? part_at(bx(), by()) : int(kNone);
        set_hot(p, g.s.pressed == p ? p : int(kNone));
        tip(p, origin());
        *result = 0;
        return true;
    }
    case WM_NCMOUSELEAVE:
        set_hot(kNone, kNone);
        tip(kNone, POINT{});
        return false;
    case WM_NCLBUTTONDOWN: case WM_NCLBUTTONDBLCLK: {
        if (!ours(wp)) return false; // the caption drags (double-click maximises); the edges resize
        int p = part_at(bx(), by());
        if (p >= 0 && msg == WM_NCLBUTTONDOWN) act(p);
        else if (p < 0) set_hot(p, p);
        *result = 0;
        return true;
    }
    case WM_NCLBUTTONUP: {
        if (!ours(wp)) return false;
        int p = part_at(bx(), by());
        if (p < 0 && p != kNone && p == g.s.pressed) act(p);
        set_hot(p, kNone);
        *result = 0;
        return true;
    }
    default: return false;
    }
}

} // namespace host
