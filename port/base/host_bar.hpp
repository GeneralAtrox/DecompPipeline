// The Phangrade title bar's own pieces (host_bar*.cpp): its look, its layout, its state, and the menus that drop from it.
// Only the bar's files include this; the rest of the host reaches the bar through host_internal.hpp's bar_* functions.
//   host_bar.cpp       the window's frame: the bar as its non-client strip, the hit test, the window buttons
//   host_bar_open.cpp  opening the menus (pointer, keys, controller), the bar over a full screen, the test pictures' order
//   host_bar_draw.cpp  fonts, layout and drawing of the bar and of a dropped menu
//   host_bar_menu.cpp  the dropped menus' own loop
//   host_bar_shot.cpp  DP_BAR_SHOT's pictures
// Nearest existing: host_menu.cpp's native menu bar - kept as the "native" choice; the bar draws the same menu table.
#pragma once
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#include <string>
#include <vector>

namespace bar {

// The look: flat and dark. Sizes are at 96 dpi and scaled by px().
constexpr COLORREF kBg = RGB(22, 24, 28), kBgOff = RGB(30, 32, 36), kText = RGB(232, 234, 238), kDim = RGB(146, 152, 162),
                   kHover = RGB(44, 48, 56), kPress = RGB(56, 61, 71), kAccent = RGB(96, 142, 255), kRed = RGB(240, 84, 84),
                   kCloseRed = RGB(196, 43, 28), kLine = RGB(40, 43, 50), kMenuBg = RGB(30, 33, 38), kMenuLine = RGB(54, 58, 66),
                   kGrey = RGB(100, 106, 116), kPill = RGB(36, 39, 45);
constexpr int kHeight = 32; // the bar, at 96 dpi

int px(unsigned dpi, int v);

struct Fonts {
    HFONT text, bold, icons, mark;
};
const Fonts &fonts(unsigned dpi); // made once per dpi: Segoe UI (Variable on Windows 11), Segoe Fluent Icons or MDL2 Assets

// The icon font's glyphs (the same code points in Segoe Fluent Icons and Segoe MDL2 Assets)
constexpr wchar_t kGlyphMin = 0xE921, kGlyphMax = 0xE922, kGlyphRestore = 0xE923, kGlyphClose = 0xE8BB, kGlyphCopy = 0xE8C8,
                  kGlyphCheck = 0xE73E, kGlyphRight = 0xE76C, kGlyphUp = 0xE70E, kGlyphDown = 0xE70D, kGlyphWindow = 0xE73F;

void fill(HDC dc, const RECT &r, COLORREF c);
void round_fill(HDC dc, const RECT &r, int radius, COLORREF c);
void text(HDC dc, HFONT f, const std::wstring &s, RECT r, COLORREF c, UINT how);

// A menu label without its & marks and shortcut: "&Full screen\tF11" -> "Full screen", keys "F11", mnemonic 'F'.
std::wstring plain(const wchar_t *text, std::wstring *keys, wchar_t *mnemonic);

// The bar's parts. A menu title is its index in Layout::titles (0 and up).
enum Part : int { kNone = -1, kMark = -2, kName = -3, kPillPart = -4, kCopy = -5, kMin = -6, kMax = -7, kClose = -8 };

struct Layout {
    int w = 0, h = 0;
    unsigned dpi = 96;
    RECT mark{}, name{}, pill{}, copy{}, min{}, max{}, close{};
    std::vector<RECT> titles;        // the menu titles, left to right
    std::vector<int> index;          // each title's position in the menu bar's HMENU
    std::vector<std::wstring> label; // each title's words
    std::vector<wchar_t> mnemonic;   // each title's Alt letter
};

struct State {
    int hot = kNone, pressed = kNone, open = kNone; // pointer over, button held, menu dropped
    bool active = true, full = false, maxed = false;
    std::wstring title;
    wchar_t shown[32] = L"", game[32] = L""; // the frame-rate reading; empty: off
    bool slow = false;
};

// Lays the bar out for a width (bar coordinates: 0,0 its top left) and draws it into dc.
void layout(HMENU menu, const State &s, int w, unsigned dpi, Layout *out);
void paint(HDC dc, const Layout &l, const State &s);
int hit(const Layout &l, int x, int y); // a Part

// A dropped menu: its entries as read from the HMENU (marks and grey as host_menu.cpp's sync set them), and its place.
struct Item {
    UINT id = 0;
    HMENU sub = nullptr;
    std::wstring label, keys;
    wchar_t mnemonic = 0;
    bool sep = false, grey = false, checked = false;
    int y = 0, h = 0; // in the menu's list
};
struct Level {
    HMENU m = nullptr;
    HWND wnd = nullptr;
    std::vector<Item> items;
    int sel = -1, parent = -1, w = 0, h = 0, list = 0, scroll = 0; // h: the window's; list: all the items'
    RECT at{};                                                      // on screen
};
int menu_pad(unsigned dpi); // the space above and below a menu's entries
void read_menu(Level &L, unsigned dpi);
void draw_menu(HDC out, const Level &L, unsigned dpi);

// The menus' loop (host_bar_menu.cpp). Runs until a choice or a close; the command chosen, 0 for none. titles: each
// title's rectangle on screen. hot: told which title is open as it changes (to draw the bar again).
struct Track {
    HWND owner;
    HMENU menu;
    std::vector<RECT> titles;
    std::vector<int> index;
    unsigned dpi;
    int first;
    bool keys; // opened from the keyboard or a controller: the first entry is selected
    void (*hot)(int title);
};
unsigned track(const Track &t);

// The bar's state, shared by host_bar.cpp and host_bar_open.cpp
struct Bar {
    HWND hwnd = nullptr, pop = nullptr; // the game's window; the bar's popup over a full screen
    HMENU menu = nullptr;
    bool slim = true, full = false, tracking = false, pop_shown = false;
    State s;
    Layout l;
    int request = -1; // a menu to open at the next pump (its title)
    bool request_keys = false;
};
extern Bar g;
unsigned dpi();
bool framed(); // the bar is the window's non-client strip (slim and windowed)
void paint_into(HDC dc, int x, int y, int w);
void redraw();
void set_hot(int hot, int pressed);
int part_at(int x, int y); // what the pointer can press: a title, the icon or a window button (else kNone)
void lay();                // a layout, before the bar was ever drawn
void request(int title, bool keys);
void act(int part);
void tip(int part, POINT origin);
POINT origin(); // the bar's top left on screen
void pop_make(); // host_bar_open.cpp
void pop_show(bool on);

// host_bar_shot.cpp: DP_BAR_SHOT=<folder>, the bar's test pictures (taken in host_bar_open.cpp's order and by track).
bool shot_on();
void shot_menu(const wchar_t *name); // the next dropped menu is pictured as name, then closed
bool shot_in_menu(const RECT &area); // inside a dropped menu, once drawn: true when it took the picture
void capture(const RECT &screen, const wchar_t *name);

} // namespace bar
