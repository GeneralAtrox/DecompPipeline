// host_text.cpp: typing and pasting into a console's own text field (a kit's address entry in the game's own screens),
// from the window's keys and the clipboard. The console asks for keys while its field is open (host_text_capture); they
// are queued only then, and only when the real keyboard is read (input_live: a check or a scripted run reads none). Keys
// that type: digits (top row and keypad), '.', ':', '-', Backspace, Enter and Esc; Ctrl+V and Shift+Insert paste. Letters
// do not type: they are the pad's buttons (Z is A, X is B), so a name is pasted or picked with the pad. The clipboard is
// read only on a paste key, as text; --clipboard-test (host_text_test) stands in for it, so a test never reads the real one.
//
// The game's own text field (native_game.h pg_native_text) is the full keyboard instead: while it is open SDL's text
// input is on (any layout, dead keys) and what it types is queued as UTF-8, with '\b', '\n', '\t' and 0x1b for
// Backspace, Enter, Tab and Esc; Ctrl+V and Shift+Insert paste the clipboard's text (its line breaks dropped) into the same
// queue. Each pad read takes the queue (host_text_drain, input.c) and the recording keeps it (session_events.c), so a
// replay never reads the clipboard. Alt+Enter, F9 and F11 stay the window's.
//
// Nearest existing: host_online.cpp's ask_text (a Windows dialog for the Online menu's address) - a dialog over the game,
// outside its own screens, where this feeds the game's own field.
#include <windows.h>
#include <SDL3/SDL.h>
#include <cstdio>
#include <cstring>
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
bool g_full, g_full_started; // the game's field is open; SDL's text input is on
std::string g_fq;            // its queue, UTF-8
constexpr size_t kFullMax = 4096;

void full_put(const std::string &s) // under g_m
{
    if (g_fq.size() + s.size() <= kFullMax) g_fq += s;
}

// The clipboard's text as UTF-8 (or --clipboard-test's next part), its line breaks dropped.
std::string clip_utf8()
{
    std::string s;
    {
        std::lock_guard<std::mutex> l(g_m);
        if (g_tested) {
            size_t bar = g_test.find('|');
            s = g_test.substr(0, bar), g_test = bar == std::string::npos ? "" : g_test.substr(bar + 1);
        }
    }
    if (!g_tested && OpenClipboard(nullptr)) {
        HANDLE h = GetClipboardData(CF_UNICODETEXT);
        const wchar_t *w = h ? static_cast<const wchar_t *>(GlobalLock(h)) : nullptr;
        int n = w ? WideCharToMultiByte(CP_UTF8, 0, w, -1, nullptr, 0, nullptr, nullptr) : 0;
        if (n > 1) s.resize(static_cast<size_t>(n - 1)), WideCharToMultiByte(CP_UTF8, 0, w, -1, s.data(), n, nullptr, nullptr);
        if (w) GlobalUnlock(h);
        CloseClipboard();
    }
    std::string out;
    for (char c : s)
        if (c != '\r' && c != '\n') out += c;
    return out;
}

void full_key(int key, int mod)
{
    const bool shift = (mod & SDL_KMOD_SHIFT) != 0, ctrl = (mod & SDL_KMOD_CTRL) != 0;
    if ((ctrl && key == SDLK_V) || (shift && key == SDLK_INSERT)) {
        std::string s = clip_utf8();
        std::lock_guard<std::mutex> l(g_m);
        full_put(s);
        return;
    }
    if (ctrl || (mod & SDL_KMOD_ALT)) return; // Alt+Enter is the window's
    char c = key == SDLK_BACKSPACE ? '\b' : key == SDLK_RETURN || key == SDLK_KP_ENTER ? '\n' : key == SDLK_TAB ? '\t'
           : key == SDLK_ESCAPE ? static_cast<char>(27) : 0;
    std::lock_guard<std::mutex> l(g_m);
    if (c) full_put(std::string(1, c));
}

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
    if (g_full) {
        if (input_live()) full_key(key, mod);
        return;
    }
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

extern "C" void pg_native_text(int active)
{
    bool on = active != 0;
    if (on == g_full) return;
    {
        std::lock_guard<std::mutex> l(g_m);
        g_full = on, g_fq.clear();
    }
    input_kbm_text_mode(on);
    game_log_line("text: the game's field %s", on ? "open (the keys type; the layout's keys press nothing)" : "shut");
}

extern "C" int host_text_full_on(void) { return g_full ? 1 : 0; }

extern "C" void host_text_input(const char *utf8)
{
    if (!g_full || !utf8 || !input_live()) return;
    std::lock_guard<std::mutex> l(g_m);
    full_put(utf8);
}

// --kbm-inject TEXT=: UTF-8 as given, or the ANSI code page's (a command line's) made UTF-8.
extern "C" void host_text_put(const char *text, int n)
{
    if (!g_full || !text || n <= 0) return;
    std::string s(text, static_cast<size_t>(n));
    if (!MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, s.data(), n, nullptr, 0)) {
        int w = MultiByteToWideChar(CP_ACP, 0, s.data(), n, nullptr, 0);
        std::wstring ws(static_cast<size_t>(w > 0 ? w : 0), L'\0');
        if (w > 0) MultiByteToWideChar(CP_ACP, 0, s.data(), n, ws.data(), w);
        int u = w > 0 ? WideCharToMultiByte(CP_UTF8, 0, ws.data(), w, nullptr, 0, nullptr, nullptr) : 0;
        s.assign(static_cast<size_t>(u > 0 ? u : 0), '\0');
        if (u > 0) WideCharToMultiByte(CP_UTF8, 0, ws.data(), w, s.data(), u, nullptr, nullptr);
    }
    std::lock_guard<std::mutex> l(g_m);
    full_put(s);
}

extern "C" int host_text_drain(char *out, int cap)
{
    std::lock_guard<std::mutex> l(g_m);
    size_t n = g_fq.size() < static_cast<size_t>(cap > 0 ? cap : 0) ? g_fq.size() : static_cast<size_t>(cap > 0 ? cap : 0);
    while (n && n < g_fq.size() && (static_cast<unsigned char>(g_fq[n]) & 0xc0) == 0x80) n--; // whole characters
    if (n) std::memcpy(out, g_fq.data(), n), g_fq.erase(0, n);
    return static_cast<int>(n);
}

extern "C" void host_text_sync(void *sdl_window)
{
    SDL_Window *w = static_cast<SDL_Window *>(sdl_window);
    bool want = g_full && input_live();
    if (!w || want == g_full_started) return;
    g_full_started = want;
    if (want) SDL_StartTextInput(w);
    else SDL_StopTextInput(w);
}
