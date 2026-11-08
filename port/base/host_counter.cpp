// The menu bar's frame-rate counter (Display > Frame-rate counter): an owner-drawn item before the copy icon,
// "144 fps   game 55/60": the frames shown a second, and the game's own rate, vertical blanks it ran a second against
// the console's (port_console.tick_hz). In-between frames keep the shown rate at the cap while the game itself runs
// slow, and the shown rate alone hid that, so the game's figure is red under 98% of its target. A window title cannot
// be coloured, so it is not in the title any more. frame_counter (host_window.cpp) measures it once a second on the
// game's or the present thread; the bar is drawn on the window's thread, so the reading is posted to it.
#include <windows.h>
#include <cstdio>
#include <cwchar>
#include "host.h"
#include "host_internal.hpp"

namespace {

constexpr UINT kItem = host::kFrameCounter;
constexpr UINT kRefresh = WM_APP + 0x208; // the item's text changed: measure and draw the bar again
constexpr double kSlow = 0.98;
constexpr COLORREF kRed = RGB(220, 30, 30);

HWND g_hwnd;
HMENU g_bar;
SRWLOCK g_lock = SRWLOCK_INIT;
wchar_t g_shown[32], g_game[32]; // empty: the counter is off
bool g_slow, g_posted_off = true;

int scaled(HWND hwnd, int px) { return MulDiv(px, static_cast<int>(GetDpiForWindow(hwnd)), 96); }

HFONT menu_font(HWND hwnd)
{
    NONCLIENTMETRICSW m{};
    m.cbSize = sizeof m;
    if (!SystemParametersInfoForDpi(SPI_GETNONCLIENTMETRICS, sizeof m, &m, 0, GetDpiForWindow(hwnd))) return nullptr;
    return CreateFontIndirectW(&m.lfMenuFont);
}

// The two parts' widths in dc and the gap between them; false when the counter is off.
bool widths(HWND hwnd, HDC dc, const wchar_t *shown, const wchar_t *game, SIZE *a, SIZE *b, int *gap)
{
    if (!*shown) return false;
    GetTextExtentPoint32W(dc, shown, static_cast<int>(std::wcslen(shown)), a);
    GetTextExtentPoint32W(dc, game, static_cast<int>(std::wcslen(game)), b);
    *gap = scaled(hwnd, 10);
    return true;
}

void draw(HWND hwnd, const DRAWITEMSTRUCT &d)
{
    wchar_t shown[32], game[32];
    bool slow;
    AcquireSRWLockShared(&g_lock);
    wcscpy_s(shown, g_shown);
    wcscpy_s(game, g_game);
    slow = g_slow;
    ReleaseSRWLockShared(&g_lock);
    HBRUSH fill = CreateSolidBrush(GetSysColor(COLOR_MENUBAR));
    FillRect(d.hDC, &d.rcItem, fill);
    DeleteObject(fill);
    HFONT font = menu_font(hwnd);
    HGDIOBJ old = font ? SelectObject(d.hDC, font) : nullptr;
    SIZE a, b;
    int gap;
    if (widths(hwnd, d.hDC, shown, game, &a, &b, &gap)) {
        int x = d.rcItem.left + scaled(hwnd, 6), y = (d.rcItem.top + d.rcItem.bottom - a.cy) / 2;
        SetBkMode(d.hDC, TRANSPARENT);
        SetTextColor(d.hDC, GetSysColor(COLOR_MENUTEXT));
        TextOutW(d.hDC, x, y, shown, static_cast<int>(std::wcslen(shown)));
        SetTextColor(d.hDC, slow ? kRed : GetSysColor(COLOR_MENUTEXT));
        TextOutW(d.hDC, x + a.cx + gap, y, game, static_cast<int>(std::wcslen(game)));
    }
    if (old) SelectObject(d.hDC, old);
    if (font) DeleteObject(font);
}

UINT measure(HWND hwnd)
{
    wchar_t shown[32], game[32];
    AcquireSRWLockShared(&g_lock);
    wcscpy_s(shown, g_shown);
    wcscpy_s(game, g_game);
    ReleaseSRWLockShared(&g_lock);
    HDC dc = GetDC(hwnd);
    HFONT font = menu_font(hwnd);
    HGDIOBJ old = font ? SelectObject(dc, font) : nullptr;
    SIZE a, b;
    int gap;
    UINT w = widths(hwnd, dc, shown, game, &a, &b, &gap) ? static_cast<UINT>(a.cx + gap + b.cx + scaled(hwnd, 12)) : 0u;
    if (old) SelectObject(dc, old);
    if (font) DeleteObject(font);
    ReleaseDC(hwnd, dc);
    return w;
}

} // namespace

namespace host {

void counter_attach(void *hwnd, void *bar)
{
    g_hwnd = static_cast<HWND>(hwnd);
    g_bar = static_cast<HMENU>(bar);
    if (!AppendMenuW(g_bar, MF_OWNERDRAW | MF_DISABLED, kItem, nullptr)) port_log("host: no frame-rate counter on the menu bar\n");
}

void counter_show(bool on, double shown_fps, double game_hz, unsigned target_hz)
{
    if (!g_hwnd || (!on && g_posted_off)) return;
    AcquireSRWLockExclusive(&g_lock);
    if (on) {
        std::swprintf(g_shown, 32, L"%.0f fps", shown_fps);
        std::swprintf(g_game, 32, L"game %.0f/%u", game_hz, target_hz);
        g_slow = target_hz && game_hz < kSlow * target_hz;
    } else {
        g_shown[0] = g_game[0] = 0;
    }
    ReleaseSRWLockExclusive(&g_lock);
    g_posted_off = !on;
    PostMessageW(g_hwnd, kRefresh, 0, 0);
}

bool counter_message(void *hwnd_ptr, unsigned msg, uintptr_t wp, intptr_t lp, intptr_t *result)
{
    HWND hwnd = static_cast<HWND>(hwnd_ptr);
    switch (msg) {
    case kRefresh:
        // changing the item makes the bar measure it again (the text's width changes); then the bar is drawn again
        ModifyMenuW(g_bar, kItem, MF_BYCOMMAND | MF_OWNERDRAW | MF_DISABLED, kItem, nullptr);
        if (GetMenu(hwnd)) DrawMenuBar(hwnd);
        *result = 0;
        return true;
    case WM_MEASUREITEM: {
        auto *m = reinterpret_cast<MEASUREITEMSTRUCT *>(lp);
        if (m->CtlType != ODT_MENU || m->itemID != kItem) return false;
        m->itemWidth = measure(hwnd);
        m->itemHeight = static_cast<UINT>(GetSystemMetrics(SM_CYMENU));
        *result = TRUE;
        return true;
    }
    case WM_DRAWITEM: {
        auto *d = reinterpret_cast<DRAWITEMSTRUCT *>(lp);
        if (d->CtlType != ODT_MENU || d->itemID != kItem) return false;
        draw(hwnd, *d);
        *result = TRUE;
        return true;
    }
    default: (void)wp; return false;
    }
}

} // namespace host
