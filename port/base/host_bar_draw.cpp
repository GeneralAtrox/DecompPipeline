// The Phangrade title bar's look (host_bar.hpp): its fonts, the layout of its parts, and drawing it with GDI. Nothing here
// runs while the bar is unchanged: the window's thread draws it when the pointer, the title or the frame-rate reading
// changes.
// Nearest existing: host_counter.cpp and host_copy_button.cpp draw items on the native bar - this draws the whole bar.
#include "host_bar.hpp"
#include <algorithm>
#include <cwchar>
#include <cwctype>

namespace bar {

int px(unsigned dpi, int v) { return MulDiv(v, static_cast<int>(dpi), 96); }

namespace {

HFONT make(const wchar_t *const *faces, int height, int weight)
{
    HDC dc = GetDC(nullptr);
    HFONT f = nullptr;
    for (; *faces && !f; faces++) { // the first face that is installed
        HFONT t = CreateFontW(height, 0, 0, 0, weight, 0, 0, 0, DEFAULT_CHARSET, OUT_TT_PRECIS, CLIP_DEFAULT_PRECIS,
                              CLEARTYPE_QUALITY, DEFAULT_PITCH, *faces);
        wchar_t got[LF_FACESIZE] = L"";
        HGDIOBJ old = SelectObject(dc, t);
        GetTextFaceW(dc, LF_FACESIZE, got);
        SelectObject(dc, old);
        if (!_wcsicmp(got, *faces)) f = t;
        else DeleteObject(t);
    }
    if (!f)
        f = CreateFontW(height, 0, 0, 0, weight, 0, 0, 0, DEFAULT_CHARSET, OUT_TT_PRECIS, CLIP_DEFAULT_PRECIS, CLEARTYPE_QUALITY,
                        DEFAULT_PITCH, L"Segoe UI");
    ReleaseDC(nullptr, dc);
    return f;
}

int text_w(HDC dc, HFONT f, const std::wstring &s)
{
    HGDIOBJ old = SelectObject(dc, f);
    SIZE z{};
    GetTextExtentPoint32W(dc, s.c_str(), static_cast<int>(s.size()), &z);
    SelectObject(dc, old);
    return z.cx;
}

RECT rect(int l, int t, int r, int b) { return RECT{l, t, r, b}; }
bool in(const RECT &r, int x, int y) { return x >= r.left && x < r.right && y >= r.top && y < r.bottom; }

} // namespace

void fill(HDC dc, const RECT &r, COLORREF c)
{
    SetDCBrushColor(dc, c);
    FillRect(dc, &r, static_cast<HBRUSH>(GetStockObject(DC_BRUSH)));
}

void round_fill(HDC dc, const RECT &r, int radius, COLORREF c)
{
    SetDCBrushColor(dc, c);
    SetDCPenColor(dc, c);
    HGDIOBJ ob = SelectObject(dc, GetStockObject(DC_BRUSH)), op = SelectObject(dc, GetStockObject(DC_PEN));
    RoundRect(dc, r.left, r.top, r.right, r.bottom, radius, radius);
    SelectObject(dc, ob);
    SelectObject(dc, op);
}

void text(HDC dc, HFONT f, const std::wstring &s, RECT r, COLORREF c, UINT how)
{
    HGDIOBJ old = SelectObject(dc, f);
    SetTextColor(dc, c);
    SetBkMode(dc, TRANSPARENT);
    DrawTextW(dc, s.c_str(), static_cast<int>(s.size()), &r, how | DT_SINGLELINE | DT_VCENTER | DT_NOPREFIX);
    SelectObject(dc, old);
}

const Fonts &fonts(unsigned dpi)
{
    static Fonts f{};
    static unsigned made;
    if (made == dpi && f.text) return f;
    for (HFONT h : {f.text, f.bold, f.icons, f.mark})
        if (h) DeleteObject(h);
    static const wchar_t *const text_faces[] = {L"Segoe UI Variable Text", L"Segoe UI", nullptr};
    static const wchar_t *const icon_faces[] = {L"Segoe Fluent Icons", L"Segoe MDL2 Assets", nullptr};
    f.text = make(text_faces, -px(dpi, 12), FW_NORMAL);
    f.bold = make(text_faces, -px(dpi, 12), FW_SEMIBOLD);
    f.icons = make(icon_faces, -px(dpi, 10), FW_NORMAL);
    f.mark = make(text_faces, -px(dpi, 11), FW_BOLD);
    made = dpi;
    return f;
}

std::wstring plain(const wchar_t *t, std::wstring *keys, wchar_t *mnemonic)
{
    std::wstring s;
    if (mnemonic) *mnemonic = 0;
    for (; *t && *t != L'\t'; t++) {
        if (*t == L'&') {
            if (t[1] == L'&') {
                s += L'&';
                t++;
            } else if (t[1] && mnemonic && !*mnemonic) {
                *mnemonic = static_cast<wchar_t>(std::towupper(t[1]));
            }
            continue;
        }
        s += *t;
    }
    if (keys) *keys = *t == L'\t' ? std::wstring(t + 1) : std::wstring();
    return s;
}

void layout(HMENU menu, const State &s, int w, unsigned dpi, Layout *l)
{
    const Fonts &f = fonts(dpi);
    HDC dc = GetDC(nullptr);
    l->w = w;
    l->h = px(dpi, kHeight);
    l->dpi = dpi;
    int h = l->h, bw = px(dpi, 46);
    l->close = rect(w - bw, 0, w, h);
    l->max = rect(l->close.left - bw, 0, l->close.left, h);
    l->min = rect(l->max.left - bw, 0, l->max.left, h);
    l->copy = rect(l->min.left - px(dpi, 40), 0, l->min.left - px(dpi, 4), h);
    int right = l->copy.left;
    l->pill = RECT{};
    if (s.shown[0]) {
        int pw = text_w(dc, f.text, s.shown) + text_w(dc, f.text, L"   ") + text_w(dc, f.text, s.game) + px(dpi, 20);
        int ph = px(dpi, 20);
        l->pill = rect(right - px(dpi, 6) - pw, (h - ph) / 2, right - px(dpi, 6), (h - ph) / 2 + ph);
        right = l->pill.left;
    }
    l->titles.clear();
    l->index.clear();
    l->label.clear();
    l->mnemonic.clear();
    int total = 0;
    wchar_t buf[128];
    for (int i = 0, n = menu ? GetMenuItemCount(menu) : 0; i < n; i++) { // the menus; not the native bar's own items
        if (!GetSubMenu(menu, i) || GetMenuStringW(menu, static_cast<UINT>(i), buf, 128, MF_BYPOSITION) <= 0) continue;
        wchar_t m;
        std::wstring words = plain(buf, nullptr, &m);
        int tw = text_w(dc, f.text, words) + px(dpi, 20);
        l->titles.push_back(rect(0, 0, tw, h));
        l->index.push_back(i);
        l->label.push_back(words);
        l->mnemonic.push_back(m);
        total += tw;
    }
    int x = px(dpi, 10), mk = px(dpi, 16);
    l->mark = rect(x, (h - mk) / 2, x + mk, (h - mk) / 2 + mk);
    x += mk + px(dpi, 10);
    int room = right - px(dpi, 12) - x - total - px(dpi, 12); // the name gives way to the menus in a narrow window
    int nw = std::min(text_w(dc, f.bold, s.title), room > 0 ? room : 0);
    l->name = rect(x, 0, x + nw, h);
    if (nw > 0) x += nw + px(dpi, 12);
    for (RECT &t : l->titles) {
        int tw = t.right;
        t = rect(x, 0, x + tw, h);
        x += tw;
    }
    ReleaseDC(nullptr, dc);
}

int hit(const Layout &l, int x, int y)
{
    if (y < 0 || y >= l.h || x < 0 || x >= l.w) return kNone;
    for (size_t i = 0; i < l.titles.size(); i++)
        if (in(l.titles[i], x, y)) return static_cast<int>(i);
    if (in(l.close, x, y)) return kClose;
    if (in(l.max, x, y)) return kMax;
    if (in(l.min, x, y)) return kMin;
    if (in(l.copy, x, y)) return kCopy;
    if (in(l.pill, x, y)) return kPillPart;
    if (in(l.mark, x, y)) return kMark;
    return kName; // the rest of the bar: it drags the window
}

void paint(HDC dc, const Layout &l, const State &s)
{
    const Fonts &f = fonts(l.dpi);
    unsigned d = l.dpi;
    COLORREF fg = s.active ? kText : kDim;
    fill(dc, rect(0, 0, l.w, l.h), s.active ? kBg : kBgOff);
    fill(dc, rect(0, l.h - 1, l.w, l.h), kLine);
    round_fill(dc, l.mark, px(d, 5), s.active ? kAccent : kGrey); // the Phangrade mark: an accent tile with a P
    text(dc, f.mark, L"P", l.mark, RGB(255, 255, 255), DT_CENTER);
    if (l.name.right > l.name.left) text(dc, f.bold, s.title, l.name, fg, DT_LEFT | DT_END_ELLIPSIS);
    for (size_t i = 0; i < l.titles.size(); i++) {
        bool open = s.open == static_cast<int>(i), hot = s.hot == static_cast<int>(i);
        const RECT &r = l.titles[i];
        if (open || hot)
            round_fill(dc, rect(r.left + px(d, 2), px(d, 4), r.right - px(d, 2), l.h - px(d, 4)), px(d, 6), open ? kPress : kHover);
        text(dc, f.text, l.label[i], r, open || hot ? kText : fg, DT_CENTER);
    }
    if (s.shown[0]) { // "144 fps   game 60/60", the game's figure red when it runs slow (host_counter.cpp)
        round_fill(dc, l.pill, l.pill.bottom - l.pill.top, kPill);
        RECT r = l.pill;
        r.left += px(d, 10);
        HGDIOBJ old = SelectObject(dc, f.text);
        SIZE a{}, gap{};
        GetTextExtentPoint32W(dc, s.shown, static_cast<int>(wcslen(s.shown)), &a);
        GetTextExtentPoint32W(dc, L"   ", 3, &gap);
        SelectObject(dc, old);
        text(dc, f.text, s.shown, r, kText, DT_LEFT);
        r.left += a.cx + gap.cx;
        text(dc, f.text, s.game, r, s.slow ? kRed : kDim, DT_LEFT);
    }
    auto button = [&](const RECT &r, int part, wchar_t glyph) {
        bool hot = s.hot == part, down = s.pressed == part, danger = part == kClose;
        if (hot || down) {
            if (danger) fill(dc, r, down ? RGB(150, 34, 24) : kCloseRed);
            else if (part == kCopy) round_fill(dc, rect(r.left, px(d, 4), r.right, l.h - px(d, 4)), px(d, 6), down ? kPress : kHover);
            else fill(dc, r, down ? kPress : kHover);
        }
        text(dc, f.icons, std::wstring(1, glyph), r, hot && danger ? RGB(255, 255, 255) : fg, DT_CENTER);
    };
    button(l.copy, kCopy, kGlyphCopy);
    button(l.min, kMin, kGlyphMin);
    button(l.max, kMax, s.full ? kGlyphWindow : s.maxed ? kGlyphRestore : kGlyphMax);
    button(l.close, kClose, kGlyphClose);
}

// A dropped menu (host_bar_menu.cpp): its entries read from the HMENU, and drawn
int menu_pad(unsigned dpi) { return px(dpi, 5); }

void read_menu(Level &L, unsigned g_dpi)
{
    HDC dc = GetDC(nullptr);
    HGDIOBJ old = SelectObject(dc, fonts(g_dpi).text);
    int y = 0, wl = 0, wk = 0;
    L.items.clear();
    for (int i = 0, n = GetMenuItemCount(L.m); i < n; i++) {
        wchar_t buf[256] = L"";
        MENUITEMINFOW mi{};
        mi.cbSize = sizeof mi;
        mi.fMask = MIIM_FTYPE | MIIM_STATE | MIIM_ID | MIIM_SUBMENU | MIIM_STRING;
        mi.dwTypeData = buf;
        mi.cch = 255;
        if (!GetMenuItemInfoW(L.m, static_cast<UINT>(i), TRUE, &mi) || (mi.fType & MFT_OWNERDRAW)) continue;
        Item it;
        it.sep = (mi.fType & MFT_SEPARATOR) != 0;
        it.id = mi.wID;
        it.sub = mi.hSubMenu;
        it.grey = (mi.fState & MFS_DISABLED) != 0;
        it.checked = (mi.fState & MFS_CHECKED) != 0;
        if (!it.sep) {
            it.label = plain(buf, &it.keys, &it.mnemonic);
            SIZE a{}, b{};
            GetTextExtentPoint32W(dc, it.label.c_str(), static_cast<int>(it.label.size()), &a);
            if (!it.keys.empty()) GetTextExtentPoint32W(dc, it.keys.c_str(), static_cast<int>(it.keys.size()), &b);
            wl = std::max(wl, static_cast<int>(a.cx));
            wk = std::max(wk, static_cast<int>(b.cx));
        }
        it.h = px(g_dpi, it.sep ? 9 : 28);
        it.y = y;
        y += it.h;
        L.items.push_back(it);
    }
    SelectObject(dc, old);
    ReleaseDC(nullptr, dc);
    L.list = y;
    L.w = std::max(px(g_dpi, 200), px(g_dpi, 36) + wl + (wk ? px(g_dpi, 36) + wk : 0) + px(g_dpi, 32));
}

void draw_menu(HDC out, const Level &L, unsigned g_dpi)
{
    const Fonts &f = fonts(g_dpi);
    unsigned d = g_dpi;
    HDC dc = CreateCompatibleDC(out);
    HBITMAP bmp = CreateCompatibleBitmap(out, L.w, L.h);
    HGDIOBJ old = SelectObject(dc, bmp);
    fill(dc, RECT{0, 0, L.w, L.h}, kMenuLine);
    fill(dc, RECT{1, 1, L.w - 1, L.h - 1}, kMenuBg);
    IntersectClipRect(dc, 1, menu_pad(g_dpi), L.w - 1, L.h - menu_pad(g_dpi));
    for (int n = 0; n < static_cast<int>(L.items.size()); n++) {
        const Item &it = L.items[n];
        int y = menu_pad(g_dpi) + it.y - L.scroll;
        if (y + it.h < 0 || y > L.h) continue;
        if (it.sep) {
            fill(dc, RECT{px(d, 12), y + it.h / 2, L.w - px(d, 12), y + it.h / 2 + 1}, kMenuLine);
            continue;
        }
        if (n == L.sel) round_fill(dc, RECT{px(d, 4), y + 1, L.w - px(d, 4), y + it.h - 1}, px(d, 6), kHover);
        COLORREF c = it.grey ? kGrey : kText;
        if (it.checked) text(dc, f.icons, std::wstring(1, kGlyphCheck), RECT{px(d, 8), y, px(d, 34), y + it.h}, it.grey ? kGrey : kAccent, DT_CENTER);
        RECT r{px(d, 36), y, L.w - px(d, 30), y + it.h};
        text(dc, f.text, it.label, r, c, DT_LEFT | DT_END_ELLIPSIS);
        if (!it.keys.empty()) text(dc, f.text, it.keys, r, kDim, DT_RIGHT);
        if (it.sub) text(dc, f.icons, std::wstring(1, kGlyphRight), RECT{L.w - px(d, 28), y, L.w - px(d, 8), y + it.h}, kDim, DT_CENTER);
    }
    SelectClipRgn(dc, nullptr);
    int band = menu_pad(g_dpi) + px(d, 10);
    if (L.scroll > 0) {
        fill(dc, RECT{1, 1, L.w - 1, band}, kMenuBg);
        text(dc, f.icons, std::wstring(1, kGlyphUp), RECT{0, 1, L.w, band}, kDim, DT_CENTER);
    }
    if (L.scroll < L.list - (L.h - 2 * menu_pad(g_dpi))) {
        fill(dc, RECT{1, L.h - band, L.w - 1, L.h - 1}, kMenuBg);
        text(dc, f.icons, std::wstring(1, kGlyphDown), RECT{0, L.h - band, L.w, L.h - 1}, kDim, DT_CENTER);
    }
    BitBlt(out, 0, 0, L.w, L.h, dc, 0, 0, SRCCOPY);
    SelectObject(dc, old);
    DeleteObject(bmp);
    DeleteDC(dc);
}

} // namespace bar
