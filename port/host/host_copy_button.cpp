// The menu bar's "Copy agent prompt" icon, after Phantom Crash's desktop_copy_button and Lodoss's
// desktop_copy_context_button (MIT, the user's own): an owner-drawn item (two sheets) at the end of the bar, with a
// tooltip ("Copy agent prompt (F9)") while the pointer is on it. A click asks for a scene dump
// (runtime/scene_dump.c) after the next frame; nothing pops up. The Help menu's "Copy agent prompt" entry
// (host_menu.cpp) is the same command, and F9 (host_window.cpp) does it in full screen too, where the bar is hidden.
#include <windows.h>
#include <commctrl.h>
#include "host.h"
#include "host_internal.hpp"

namespace {

constexpr UINT kCopy = host::kCopyAgentPrompt;
HWND g_tip;
TOOLINFOW g_tool;
bool g_tip_shown;

int scaled(HWND hwnd, int px) { return MulDiv(px, static_cast<int>(GetDpiForWindow(hwnd)), 96); }

void tip_hide()
{
    if (!g_tip || !g_tip_shown) return;
    SendMessageW(g_tip, TTM_TRACKACTIVATE, FALSE, reinterpret_cast<LPARAM>(&g_tool));
    g_tip_shown = false;
}

// The item's rectangle on screen; false when the bar is hidden or the item is not on it.
bool item_rect(HWND hwnd, RECT *r)
{
    HMENU bar = GetMenu(hwnd);
    if (!bar) return false;
    for (int i = GetMenuItemCount(bar) - 1; i >= 0; i--)
        if (GetMenuItemID(bar, i) == kCopy) return GetMenuItemRect(hwnd, bar, static_cast<UINT>(i), r) != 0;
    return false;
}

void tip_track(HWND hwnd, POINT screen)
{
    RECT r;
    if (!g_tip || !item_rect(hwnd, &r) || !PtInRect(&r, screen)) {
        tip_hide();
        return;
    }
    TRACKMOUSEEVENT t{sizeof t, TME_LEAVE | TME_NONCLIENT, hwnd, 0};
    TrackMouseEvent(&t);
    SendMessageW(g_tip, TTM_TRACKPOSITION, 0, MAKELPARAM(r.left, r.bottom + scaled(hwnd, 4)));
    if (!g_tip_shown) SendMessageW(g_tip, TTM_TRACKACTIVATE, TRUE, reinterpret_cast<LPARAM>(&g_tool));
    g_tip_shown = true;
}

// For a menu item hwndItem is the menu, not a window: the scale comes from the window the bar is on.
void draw(HWND hwnd, const DRAWITEMSTRUCT &d)
{
    bool hot = (d.itemState & (ODS_HOTLIGHT | ODS_SELECTED)) != 0;
    COLORREF bg = GetSysColor(COLOR_MENUBAR), hi = GetSysColor(COLOR_MENUHILIGHT);
    if (hot)
        bg = RGB((GetRValue(bg) * 3 + GetRValue(hi)) / 4, (GetGValue(bg) * 3 + GetGValue(hi)) / 4,
                 (GetBValue(bg) * 3 + GetBValue(hi)) / 4);
    HBRUSH fill = CreateSolidBrush(bg);
    FillRect(d.hDC, &d.rcItem, fill);
    int s = scaled(hwnd, 8), off = scaled(hwnd, 3), cx = (d.rcItem.left + d.rcItem.right) / 2,
        cy = (d.rcItem.top + d.rcItem.bottom) / 2;
    HPEN pen = CreatePen(PS_SOLID, scaled(hwnd, 1),
                         GetSysColor((d.itemState & ODS_GRAYED) ? COLOR_GRAYTEXT : COLOR_MENUTEXT));
    HGDIOBJ old_pen = SelectObject(d.hDC, pen), old_brush = SelectObject(d.hDC, fill);
    int x = cx - (s + off) / 2, y = cy - (s - off) / 2; // the front sheet; the back one up and right of it
    RoundRect(d.hDC, x + off, y - off, x + off + s, y - off + s, 3, 3);
    RoundRect(d.hDC, x, y, x + s, y + s, 3, 3); // filled: it hides the back sheet's corner
    SelectObject(d.hDC, old_pen);
    SelectObject(d.hDC, old_brush);
    DeleteObject(pen);
    DeleteObject(fill);
}

} // namespace

namespace host {

void copy_button_attach(void *hwnd_ptr, void *bar)
{
    HWND hwnd = static_cast<HWND>(hwnd_ptr);
    if (!AppendMenuW(static_cast<HMENU>(bar), MF_OWNERDRAW, kCopy, nullptr)) {
        port_log("host: no copy button on the menu bar\n");
        return;
    }
    INITCOMMONCONTROLSEX icc{sizeof icc, ICC_BAR_CLASSES};
    InitCommonControlsEx(&icc);
    g_tip = CreateWindowExW(WS_EX_TOPMOST, TOOLTIPS_CLASSW, nullptr, WS_POPUP | TTS_NOPREFIX | TTS_ALWAYSTIP, 0, 0,
                            0, 0, hwnd, nullptr, GetModuleHandleW(nullptr), nullptr);
    if (!g_tip) return;
    g_tool = TOOLINFOW{};
    g_tool.cbSize = sizeof g_tool;
    g_tool.uFlags = TTF_TRACK | TTF_ABSOLUTE;
    g_tool.hwnd = hwnd;
    g_tool.lpszText = const_cast<wchar_t *>(L"Copy agent prompt (F9)");
    SendMessageW(g_tip, TTM_ADDTOOLW, 0, reinterpret_cast<LPARAM>(&g_tool));
}

bool copy_button_message(void *hwnd_ptr, unsigned msg, uintptr_t wp, intptr_t lp, intptr_t *result)
{
    HWND hwnd = static_cast<HWND>(hwnd_ptr);
    switch (msg) {
    case WM_MEASUREITEM: {
        auto *m = reinterpret_cast<MEASUREITEMSTRUCT *>(lp);
        if (m->CtlType != ODT_MENU || m->itemID != kCopy) return false;
        m->itemWidth = static_cast<UINT>(scaled(hwnd, 28));
        m->itemHeight = static_cast<UINT>(GetSystemMetrics(SM_CYMENU));
        *result = TRUE;
        return true;
    }
    case WM_DRAWITEM: {
        auto *d = reinterpret_cast<DRAWITEMSTRUCT *>(lp);
        if (d->CtlType != ODT_MENU || d->itemID != kCopy) return false;
        draw(hwnd, *d);
        *result = TRUE;
        return true;
    }
    case WM_COMMAND:
        if (HIWORD(wp) != 0 || lp != 0 || LOWORD(wp) != kCopy) return false;
        tip_hide();
        scene_dump_request();
        *result = 0;
        return true;
    case WM_NCMOUSEMOVE:
        tip_track(hwnd, POINT{static_cast<short>(LOWORD(lp)), static_cast<short>(HIWORD(lp))});
        return false;
    case WM_NCMOUSELEAVE:
    case WM_MOUSEMOVE:
    case WM_ENTERMENULOOP:
    case WM_KILLFOCUS: tip_hide(); return false;
    default: return false;
    }
}

} // namespace host
