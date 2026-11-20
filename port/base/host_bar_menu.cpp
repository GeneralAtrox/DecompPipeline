// The menus that drop from the Phangrade title bar (host_bar.hpp, track): popup windows drawn with GDI, read from the
// menu bar's HMENU as it stands (host_menu.cpp's one table, its marks set by its sync), in a loop of their own on the
// window's thread, as Windows' own menus run: the game waits until the menu closes. Pointer; keys (arrows, Enter, Esc,
// the underlined letters); controllers through SDL's gamepads (d-pad or left stick moves, A chooses, B backs out, Guide
// closes). A menu taller than the screen scrolls (wheel, keys, or the pointer at its top or bottom edge).
// Nearest existing: TrackPopupMenu (the native menu) - it cannot be drawn dark or driven by a controller.
#include "host_bar.hpp"
#include <SDL3/SDL.h>
#include <algorithm>

namespace bar {
namespace {

const wchar_t kClass[] = L"PhangradeMenu";
std::vector<Level> g_lv;
const Track *g_t;
unsigned g_dpi;
int g_title, g_hover_level = -1, g_hover_item = -1;
ULONGLONG g_hover_at;
bool g_done;
unsigned g_result;

int pad() { return menu_pad(g_dpi); }
int view(const Level &L) { return L.h - 2 * pad(); }

// Puts the menu at x, y, inside the work area of that point's monitor; flip: its x when it would leave on the right.
void place(Level &L, int x, int y, int flip)
{
    MONITORINFO mi{};
    mi.cbSize = sizeof mi;
    GetMonitorInfoW(MonitorFromPoint(POINT{x, y}, MONITOR_DEFAULTTONEAREST), &mi);
    RECT a = mi.rcWork;
    L.h = std::min(L.list + 2 * pad(), static_cast<int>(a.bottom - a.top));
    if (x + L.w > a.right) x = flip >= 0 ? flip - L.w : a.right - L.w;
    x = std::max(x, static_cast<int>(a.left));
    if (y + L.h > a.bottom) y = a.bottom - L.h;
    y = std::max(y, static_cast<int>(a.top));
    L.at = RECT{x, y, x + L.w, y + L.h};
}

int next(const Level &L, int from, int dir)
{
    int n = static_cast<int>(L.items.size());
    for (int k = 1; k <= n; k++) {
        int i = ((from + dir * k) % n + n) % n;
        if (from < 0 && dir > 0) i = k - 1;
        if (!L.items[i].sep) return i;
    }
    return -1;
}

void show(Level &L, int k)
{
    if (!L.wnd) {
        L.wnd = CreateWindowExW(WS_EX_TOOLWINDOW | WS_EX_TOPMOST | WS_EX_NOACTIVATE, kClass, L"", WS_POPUP, L.at.left, L.at.top,
                                L.w, L.h, g_t->owner, nullptr, GetModuleHandleW(nullptr), nullptr);
        using Fn = HRESULT(WINAPI *)(HWND, DWORD, LPCVOID, DWORD);
        static HMODULE dll = LoadLibraryExW(L"dwmapi.dll", nullptr, LOAD_LIBRARY_SEARCH_SYSTEM32);
        static Fn set = dll ? reinterpret_cast<Fn>(GetProcAddress(dll, "DwmSetWindowAttribute")) : nullptr;
        DWORD round = 3; // DWMWCP_ROUNDSMALL (Windows 11)
        if (set && L.wnd) set(L.wnd, 33, &round, sizeof round);
    }
    SetWindowLongPtrW(L.wnd, GWLP_USERDATA, k);
    SetWindowPos(L.wnd, HWND_TOPMOST, L.at.left, L.at.top, L.w, L.h, SWP_NOACTIVATE | SWP_SHOWWINDOW);
    InvalidateRect(L.wnd, nullptr, FALSE);
    UpdateWindow(L.wnd);
}

void close_from(size_t k)
{
    while (g_lv.size() > k) {
        if (g_lv.back().wnd) DestroyWindow(g_lv.back().wnd);
        g_lv.pop_back();
    }
    if (g_lv.size() > 0 && g_hover_level >= static_cast<int>(g_lv.size())) g_hover_level = -1;
}

void repaint(size_t k)
{
    if (k < g_lv.size()) InvalidateRect(g_lv[k].wnd, nullptr, FALSE);
}

void visible(Level &L) // scrolls the selection into view
{
    if (L.sel >= 0) {
        const Item &it = L.items[L.sel];
        if (it.y < L.scroll) L.scroll = it.y;
        if (it.y + it.h > L.scroll + view(L)) L.scroll = it.y + it.h - view(L);
    }
    L.scroll = std::max(0, std::min(L.scroll, L.list - view(L)));
}

void root(int t, bool keys) // a title's menu; the level 0 window is kept, so it keeps the pointer's capture
{
    int n = static_cast<int>(g_t->titles.size());
    g_title = ((t % n) + n) % n;
    close_from(1);
    if (g_lv.empty()) g_lv.emplace_back();
    Level &L = g_lv[0];
    L.m = GetSubMenu(g_t->menu, g_t->index[g_title]);
    L.sel = -1;
    L.scroll = 0;
    read_menu(L, g_dpi);
    const RECT &r = g_t->titles[g_title];
    place(L, r.left, r.bottom, -1);
    if (keys) L.sel = next(L, -1, 1);
    show(L, 0);
    if (g_t->hot) g_t->hot(g_title);
}

void open_sub(size_t k, int i, bool keys)
{
    close_from(k + 1);
    const Item it = g_lv[k].items[i];
    if (!it.sub || it.grey) return;
    RECT at = g_lv[k].at;
    Level L;
    L.m = it.sub;
    L.parent = i;
    read_menu(L, g_dpi);
    place(L, at.right - px(g_dpi, 4), at.top + it.y - g_lv[k].scroll, at.left + px(g_dpi, 4));
    if (keys) L.sel = next(L, -1, 1);
    g_lv.push_back(L);
    show(g_lv.back(), static_cast<int>(g_lv.size() - 1));
}

void choose(size_t k, int i, bool keys)
{
    const Item &it = g_lv[k].items[i];
    if (it.sep || it.grey) return;
    if (it.sub) open_sub(k, i, keys);
    else g_result = it.id, g_done = true;
}

void select(size_t k, int i)
{
    Level &L = g_lv[k];
    if (g_lv.size() > k + 1 && g_lv[k + 1].parent != i && i >= 0) close_from(k + 1);
    if (L.sel == i) return;
    L.sel = i;
    g_hover_level = static_cast<int>(k);
    g_hover_item = i;
    g_hover_at = GetTickCount64();
    repaint(k);
}

void key(WPARAM vk)
{
    size_t k = g_lv.size() - 1;
    Level &L = g_lv[k];
    switch (vk) {
    case VK_UP: case VK_DOWN:
        L.sel = next(L, L.sel, vk == VK_UP ? -1 : 1);
        visible(L);
        repaint(k);
        break;
    case VK_RIGHT:
        if (L.sel >= 0 && L.items[L.sel].sub && !L.items[L.sel].grey) open_sub(k, L.sel, true);
        else root(g_title + 1, true);
        break;
    case VK_LEFT:
        if (k > 0) close_from(k);
        else root(g_title - 1, true);
        break;
    case VK_RETURN: case VK_SPACE:
        if (L.sel >= 0) choose(k, L.sel, true);
        break;
    case VK_ESCAPE:
        if (k > 0) close_from(k);
        else g_done = true;
        break;
    case VK_MENU: case VK_F10: g_done = true; break;
    default:
        for (int i = 0; i < static_cast<int>(L.items.size()); i++)
            if (L.items[i].mnemonic && L.items[i].mnemonic == static_cast<wchar_t>(vk)) {
                L.sel = i;
                choose(k, i, true);
                break;
            }
        break;
    }
}

bool at(POINT p, int *k, int *i) // the deepest menu under p and its item there (-1: none, or a line)
{
    for (int lv = static_cast<int>(g_lv.size()) - 1; lv >= 0; lv--) {
        const Level &L = g_lv[lv];
        if (!PtInRect(&L.at, p)) continue;
        *k = lv;
        *i = -1;
        int y = p.y - L.at.top - pad() + L.scroll;
        for (int n = 0; n < static_cast<int>(L.items.size()); n++)
            if (!L.items[n].sep && y >= L.items[n].y && y < L.items[n].y + L.items[n].h) *i = n;
        return true;
    }
    return false;
}

void mouse(const MSG &m)
{
    int k = -1, i = -1, t = -1;
    bool over = at(m.pt, &k, &i);
    for (int n = 0; n < static_cast<int>(g_t->titles.size()); n++)
        if (PtInRect(&g_t->titles[n], m.pt)) t = n;
    switch (m.message) {
    case WM_MOUSEMOVE:
        if (over && i >= 0) select(static_cast<size_t>(k), i);
        else if (!over && t >= 0 && t != g_title) root(t, false);
        break;
    case WM_LBUTTONDOWN: case WM_RBUTTONDOWN: case WM_MBUTTONDOWN:
        if (over) break;
        if (t >= 0 && t != g_title) root(t, false);
        else g_done = true; // its own title again, or outside: closed
        break;
    case WM_LBUTTONUP: case WM_RBUTTONUP:
        if (over && i >= 0) choose(static_cast<size_t>(k), i, false);
        break;
    case WM_MOUSEWHEEL:
        if (over) {
            Level &L = g_lv[k];
            L.scroll -= GET_WHEEL_DELTA_WPARAM(m.wParam) / WHEEL_DELTA * px(g_dpi, 84);
            L.scroll = std::max(0, std::min(L.scroll, L.list - view(L)));
            repaint(k);
        }
        break;
    default: break;
    }
}

LRESULT CALLBACK proc(HWND h, UINT msg, WPARAM wp, LPARAM lp)
{
    switch (msg) {
    case WM_PAINT: {
        PAINTSTRUCT ps;
        HDC dc = BeginPaint(h, &ps);
        size_t k = static_cast<size_t>(GetWindowLongPtrW(h, GWLP_USERDATA));
        if (k < g_lv.size() && g_lv[k].wnd == h) draw_menu(dc, g_lv[k], g_dpi);
        EndPaint(h, &ps);
        return 0;
    }
    case WM_ERASEBKGND: return 1;
    case WM_MOUSEACTIVATE: return MA_NOACTIVATE;
    case WM_SETCURSOR: SetCursor(LoadCursorW(nullptr, MAKEINTRESOURCEW(32512))); return TRUE;
    case WM_CAPTURECHANGED: // another window took the pointer (Alt+Tab, a click elsewhere): closed
        if (!g_lv.empty() && h == g_lv[0].wnd && reinterpret_cast<HWND>(lp) != h) g_done = true;
        return 0;
    default: return DefWindowProcW(h, msg, wp, lp);
    }
}

struct Pad {
    bool dir[4], a, b, guide; // up, down, left, right
};

Pad read_pad() // every controller SDL has open (input_device.c opened them), ORed
{
    Pad p{};
    if (!SDL_WasInit(SDL_INIT_GAMEPAD)) return p;
    SDL_UpdateGamepads();
    int n = 0;
    SDL_JoystickID *ids = SDL_GetGamepads(&n);
    for (int i = 0; ids && i < n; i++) {
        SDL_Gamepad *gp = SDL_GetGamepadFromID(ids[i]);
        if (!gp) continue;
        int x = SDL_GetGamepadAxis(gp, SDL_GAMEPAD_AXIS_LEFTX), y = SDL_GetGamepadAxis(gp, SDL_GAMEPAD_AXIS_LEFTY);
        p.dir[0] |= SDL_GetGamepadButton(gp, SDL_GAMEPAD_BUTTON_DPAD_UP) || y < -20000;
        p.dir[1] |= SDL_GetGamepadButton(gp, SDL_GAMEPAD_BUTTON_DPAD_DOWN) || y > 20000;
        p.dir[2] |= SDL_GetGamepadButton(gp, SDL_GAMEPAD_BUTTON_DPAD_LEFT) || x < -20000;
        p.dir[3] |= SDL_GetGamepadButton(gp, SDL_GAMEPAD_BUTTON_DPAD_RIGHT) || x > 20000;
        p.a |= SDL_GetGamepadButton(gp, SDL_GAMEPAD_BUTTON_SOUTH);
        p.b |= SDL_GetGamepadButton(gp, SDL_GAMEPAD_BUTTON_EAST);
        p.guide |= SDL_GetGamepadButton(gp, SDL_GAMEPAD_BUTTON_GUIDE);
    }
    SDL_free(ids);
    return p;
}

} // namespace

unsigned track(const Track &t)
{
    static bool registered;
    if (!registered) {
        WNDCLASSEXW wc{};
        wc.cbSize = sizeof wc;
        wc.style = CS_DROPSHADOW;
        wc.lpfnWndProc = proc;
        wc.hInstance = GetModuleHandleW(nullptr);
        wc.hCursor = LoadCursorW(nullptr, MAKEINTRESOURCEW(32512));
        wc.lpszClassName = kClass;
        registered = RegisterClassExW(&wc) != 0;
    }
    if (t.titles.empty() || t.first < 0 || t.first >= static_cast<int>(t.titles.size())) return 0;
    g_t = &t;
    g_dpi = t.dpi;
    g_done = false;
    g_result = 0;
    g_hover_level = -1;
    g_lv.clear();
    root(t.first, t.keys);
    SetCapture(g_lv[0].wnd);
    Pad was = read_pad();
    bool used = false;
    ULONGLONG since[4] = {}, last[4] = {};
    int frames = 0;
    static const WPARAM kDir[4] = {VK_UP, VK_DOWN, VK_LEFT, VK_RIGHT};
    while (!g_done) {
        MsgWaitForMultipleObjectsEx(0, nullptr, 16, QS_ALLINPUT, MWMO_INPUTAVAILABLE);
        MSG m;
        while (!g_done && PeekMessageW(&m, nullptr, 0, 0, PM_REMOVE)) {
            if (m.message == WM_QUIT) {
                PostQuitMessage(static_cast<int>(m.wParam));
                g_done = true;
            } else if (m.message >= WM_MOUSEFIRST && m.message <= WM_MOUSELAST) {
                mouse(m);
            } else if (m.message == WM_KEYDOWN || m.message == WM_SYSKEYDOWN) {
                key(m.wParam);
            } else if (m.message != WM_CHAR && m.message != WM_SYSCHAR && (m.message < WM_NCMOUSEMOVE || m.message > WM_NCMBUTTONDBLCLK)) {
                DispatchMessageW(&m); // key releases still reach SDL, so it holds no key down
            }
        }
        if (g_done || !IsWindow(t.owner) || IsIconic(t.owner)) break;
        ULONGLONG now = GetTickCount64();
        Pad p = read_pad();
        for (int d = 0; d < 4 && !g_done; d++) { // a direction: once, then again every 90 ms after 350 ms held
            if (!p.dir[d]) continue;
            if (!was.dir[d]) since[d] = last[d] = now, key(kDir[d]), used = true;
            else if (now - since[d] >= 350 && now - last[d] >= 90) last[d] = now, key(kDir[d]);
        }
        if (!g_done && p.a && !was.a) key(VK_RETURN), used = true;
        if (!g_done && p.b && !was.b) key(VK_ESCAPE), used = true;
        if (!g_done && p.guide && !was.guide) g_done = true, used = true;
        was = p;
        if (g_done) break;
        POINT c; // the pointer held at a scrolling menu's top or bottom edge scrolls it
        GetCursorPos(&c);
        int k = -1, i = -1;
        if (at(c, &k, &i)) {
            Level &L = g_lv[k];
            int edge = pad() + px(g_dpi, 10), y = c.y - L.at.top, old = L.scroll;
            if (y < edge) L.scroll -= px(g_dpi, 8);
            else if (y >= L.h - edge) L.scroll += px(g_dpi, 8);
            L.scroll = std::max(0, std::min(L.scroll, L.list - view(L)));
            if (L.scroll != old) repaint(k);
        }
        if (g_hover_level >= 0 && g_hover_level < static_cast<int>(g_lv.size()) && now - g_hover_at >= 300) { // hover opens
            Level &L = g_lv[g_hover_level];
            if (L.sel == g_hover_item && L.sel >= 0 && L.items[L.sel].sub && g_lv.size() == static_cast<size_t>(g_hover_level) + 1)
                open_sub(g_hover_level, L.sel, false);
            g_hover_level = -1;
        }
        if (shot_on() && ++frames == 8) {
            RECT a{};
            GetWindowRect(t.owner, &a);
            for (const Level &L : g_lv) UnionRect(&a, &a, &L.at);
            if (shot_in_menu(a)) g_done = true;
        }
    }
    g_done = true;
    ReleaseCapture();
    close_from(0);
    for (int n = 0; used && n < 100; n++) { // the button that chose stays the menu's: wait (up to a second) for its release
        Pad p = read_pad();
        if (!p.a && !p.b && !p.guide) break;
        Sleep(10);
    }
    return g_result;
}

} // namespace bar
