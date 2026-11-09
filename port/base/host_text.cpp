// host_text.cpp: typing and pasting into a console's own text field (a kit's address entry in the game's own screens),
// from the window's keys and the clipboard. The console asks for keys while its field is open (host_text_capture); they
// are queued only then, and only when the real keyboard is read (input_live: a check or a scripted run reads none). Keys
// that type: digits (top row and keypad), '.', ':', '-', Backspace, Enter and Esc; Ctrl+V and Shift+Insert paste. Letters
// do not type: they are the pad's buttons (Z is A, X is B), so a name is pasted or picked with the pad. The clipboard is
// read only on a paste key, as text; --clipboard-test (host_text_test) stands in for it, so a test never reads the real one.
//
// Nearest existing: host_online.cpp's ask_text (a Windows dialog for the Online menu's address) - a dialog over the game,
// outside its own screens, where this feeds the game's own field.
#include <windows.h>
#include <SDL3/SDL.h>
#include <cstdio>
#include <mutex>
#include <string>
#include "game_log.h"
#include "host.h"

extern "C" int input_live(void); // live.c

namespace {
std::mutex g_m;
int g_q[64], g_head, g_n;
bool g_on, g_tested;
std::string g_test; // --clipboard-test: each paste takes the text up to the next '|'

int key_char(int key, int mod)
{
    const bool shift = (mod & SDL_KMOD_SHIFT) != 0, ctrl = (mod & SDL_KMOD_CTRL) != 0;
    if ((ctrl && key == SDLK_V) || (shift && key == SDLK_INSERT)) return 22;
    if (ctrl || (mod & SDL_KMOD_ALT)) return -1;
    if (key >= SDLK_0 && key <= SDLK_9 && !shift) return '0' + (key - SDLK_0);
    if (key >= SDLK_KP_1 && key <= SDLK_KP_9) return '1' + static_cast<int>(key - SDLK_KP_1);
    if (key == SDLK_KP_0) return '0';
    if ((key == SDLK_PERIOD && !shift) || key == SDLK_KP_PERIOD) return '.';
    if (key == SDLK_COLON || (key == SDLK_SEMICOLON && shift)) return ':';
    if ((key == SDLK_MINUS && !shift) || key == SDLK_KP_MINUS) return '-';
    if (key == SDLK_BACKSPACE) return '\b';
    if (key == SDLK_RETURN || key == SDLK_KP_ENTER) return '\n';
    if (key == SDLK_ESCAPE) return 27;
    return -1;
}
} // namespace

extern "C" void host_text_capture(int on)
{
    std::lock_guard<std::mutex> l(g_m);
    g_on = on != 0, g_n = 0;
}

extern "C" void host_text_key(int key, int mod)
{
    int c = key_char(key, mod);
    if (c < 0 || !input_live()) return;
    std::lock_guard<std::mutex> l(g_m);
    if (g_on && g_n < 64) g_q[(g_head + g_n++) % 64] = c;
}

extern "C" int host_text_take(void)
{
    std::lock_guard<std::mutex> l(g_m);
    if (!g_n) return -1;
    int c = g_q[g_head];
    g_head = (g_head + 1) % 64, g_n--;
    return c;
}

extern "C" void host_text_test(const char *clip)
{
    std::lock_guard<std::mutex> l(g_m);
    g_test = clip ? clip : "", g_tested = true;
    game_log_line("text: TEST clipboard (--clipboard-test): the real clipboard is not read");
}

extern "C" int host_text_clipboard(char *out, int cap)
{
    std::string s;
    bool test;
    {
        std::lock_guard<std::mutex> l(g_m);
        test = g_tested;
        if (test) {
            size_t bar = g_test.find('|');
            s = g_test.substr(0, bar), g_test = bar == std::string::npos ? "" : g_test.substr(bar + 1);
        }
    }
    if (!test) {
        if (!input_live() || !OpenClipboard(nullptr)) return 0;
        HANDLE h = GetClipboardData(CF_UNICODETEXT);
        const wchar_t *w = h ? static_cast<const wchar_t *>(GlobalLock(h)) : nullptr;
        for (int i = 0; w && w[i] && i < 255; i++) s += w[i] < 128 ? static_cast<char>(w[i]) : '?';
        if (w) GlobalUnlock(h);
        CloseClipboard();
    }
    if (s.empty() || cap < 2) return 0;
    snprintf(out, static_cast<size_t>(cap), "%s", s.c_str());
    return 1;
}
