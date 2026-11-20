// DP_BAR_SHOT=<folder>: the title bar's test pictures, for review without anyone at the machine. Set the variable on a play
// run on a real desktop (a window run, not --headless) and it pictures the window with the bar closed, a menu opened as from
// a controller, full screen with the bar shown, and a menu over it, as .bmp files in the folder (host_bar.cpp's shots),
// then closes the window. Nothing happens without the variable.
// Nearest existing: the scene dump's .bmp (scene_dump.c) - it holds the game's frame, not the window around it.
#include "host_bar.hpp"
#include <cstdio>
#include "game_log.h"

namespace bar {
namespace {

std::wstring g_dir, g_menu_name;
bool g_read;

} // namespace

bool shot_on()
{
    if (!g_read) {
        g_read = true;
        wchar_t b[MAX_PATH];
        DWORD n = GetEnvironmentVariableW(L"DP_BAR_SHOT", b, MAX_PATH);
        if (n && n < MAX_PATH) g_dir = b;
    }
    return !g_dir.empty();
}

void shot_menu(const wchar_t *name) { g_menu_name = name; }

bool shot_in_menu(const RECT &area)
{
    if (g_menu_name.empty()) return false;
    capture(area, g_menu_name.c_str());
    g_menu_name.clear();
    return true;
}

void capture(const RECT &r, const wchar_t *name)
{
    int w = r.right - r.left, h = r.bottom - r.top;
    if (w <= 0 || h <= 0 || !shot_on()) return;
    GdiFlush();
    HDC screen = GetDC(nullptr), mem = CreateCompatibleDC(screen);
    BITMAPINFOHEADER bh{};
    bh.biSize = sizeof bh;
    bh.biWidth = w;
    bh.biHeight = h; // bottom-up, as a .bmp stores it
    bh.biPlanes = 1;
    bh.biBitCount = 24;
    bh.biCompression = BI_RGB;
    void *bits = nullptr;
    HBITMAP bmp = CreateDIBSection(screen, reinterpret_cast<BITMAPINFO *>(&bh), DIB_RGB_COLORS, &bits, nullptr, 0);
    HGDIOBJ old = SelectObject(mem, bmp);
    BitBlt(mem, 0, 0, w, h, screen, r.left, r.top, SRCCOPY | CAPTUREBLT);
    GdiFlush();
    DWORD stride = static_cast<DWORD>((w * 3 + 3) & ~3), size = stride * static_cast<DWORD>(h);
    BITMAPFILEHEADER fh{};
    fh.bfType = 0x4D42;
    fh.bfOffBits = sizeof fh + sizeof bh;
    fh.bfSize = fh.bfOffBits + size;
    std::wstring path = g_dir + L"\\" + name;
    FILE *f = nullptr;
    if (_wfopen_s(&f, path.c_str(), L"wb") != 0) f = nullptr;
    bool ok = f && bits && fwrite(&fh, sizeof fh, 1, f) == 1 && fwrite(&bh, sizeof bh, 1, f) == 1 && fwrite(bits, size, 1, f) == 1;
    if (f) fclose(f);
    SelectObject(mem, old);
    DeleteObject(bmp);
    DeleteDC(mem);
    ReleaseDC(nullptr, screen);
    char n8[64] = "";
    WideCharToMultiByte(CP_UTF8, 0, name, -1, n8, sizeof n8, nullptr, nullptr);
    game_log_line("bar: picture %s %dx%d %s", n8, w, h, ok ? "written" : "NOT written");
}

} // namespace bar
