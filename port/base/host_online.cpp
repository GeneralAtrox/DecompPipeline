// The Online menu, for a console with online play (port_console.flags PORT_CONSOLE_ONLINE; the Saturn kit's sat_net.c):
//   Now: ...       (greyed) what this run is: playing alone, or what the console said through host_online_match
//   Play alone / Host a game / Join a game...   (the host's address, asked for in a small dialog)
// The choice is saved as net=off|on|ADDRESS (the console's --net), so the next start plays that way, and the game offers to
// start again at once: this run's command line without its own --net choices, then --net and the choice, the new run
// waiting for this one to end first (PORT_RESUME_WAIT, host_resume.cpp's way). While a match is on, the base's entries that
// would change the game for both players are locked (host_menu.cpp); the console's own say so through available().
//
// Nearest existing: base/host_resume.cpp (the same start-again of this program on its own command line, asked first).
#include <windows.h>
#include <shellapi.h>
#include <algorithm>
#include <fstream>
#include <string>
#include <vector>
#include "game_log.h"
#include "host.h"
#include "host_internal.hpp"

namespace {

enum : UINT { kNow, kAlone, kHost, kJoin, kCount };
enum : int { kPrompt = 100, kEdit = 101 };

UINT g_first;
std::string g_now = "Now: playing alone", g_lock, g_run = "off", g_saved;
bool g_read;

std::wstring widen(const std::string &s) { return std::wstring(s.begin(), s.end()); } // the texts here are ASCII
std::string narrow(const std::wstring &s)
{
    std::string n;
    for (wchar_t c : s) n += c < 0x80 ? static_cast<char>(c) : '?';
    return n;
}

// The saved choice: the settings file's net line, else what this run is.
const std::string &saved()
{
    if (!g_read && !host::g_file.empty()) {
        std::ifstream in(host::g_file);
        std::string line;
        while (std::getline(in, line)) {
            std::string text = host::trim(line.substr(0, line.find('#')));
            size_t eq = text.find('=');
            if (eq != std::string::npos && host::trim(text.substr(0, eq)) == "net") g_saved = host::trim(text.substr(eq + 1));
        }
    }
    g_read = true;
    return g_saved.empty() ? g_run : g_saved;
}

// The game again, with --net value (and the extra arguments): this one closes once the new one has started. This run's
// own --net... and --online-note choices are left out.
bool restart(HWND hwnd, const std::string &value, const std::string &extra = "")
{
    std::vector<std::wstring> add{L"--net", widen(value)};
    for (size_t at = 0; at < extra.size();) { // the extra arguments, separated by spaces
        size_t end = extra.find(' ', at);
        if (end == std::string::npos) end = extra.size();
        if (end > at) add.push_back(widen(extra.substr(at, end - at)));
        at = end + 1;
    }
    unsigned long pid = host::start_again(hwnd, {L"--net*", L"--online-note"}, add, "online");
    if (pid) game_log_line("online: started process %lu with --net %s %s; this one ends", pid, value.c_str(), extra.c_str());
    return pid != 0;
}

void choose(HWND hwnd, const std::string &value, const std::wstring &what)
{
    g_saved = value;
    host::settings_set("net", value);
    game_log_line("online: from the next start: --net %s", value.c_str());
    std::wstring ask = L"Start the game again now, " + what + L"?\n\nThis game ends here. No: from the next start.";
    if (MessageBoxW(hwnd, ask.c_str(), L"Online", MB_YESNO | MB_ICONQUESTION) == IDYES) restart(hwnd, value);
}

// ---- a line of text from the player: a dialog built in memory (prompt, edit box, OK, Cancel)
struct Ask {
    const wchar_t *prompt;
    std::wstring *text;
};

INT_PTR CALLBACK ask_proc(HWND d, UINT msg, WPARAM wp, LPARAM lp)
{
    if (msg == WM_INITDIALOG) {
        const Ask *a = reinterpret_cast<const Ask *>(lp);
        SetWindowLongPtrW(d, DWLP_USER, lp);
        SetDlgItemTextW(d, kPrompt, a->prompt);
        SetDlgItemTextW(d, kEdit, a->text->c_str());
        SendDlgItemMessageW(d, kEdit, EM_SETSEL, 0, -1);
        SetFocus(GetDlgItem(d, kEdit));
        return FALSE; // the focus is set
    }
    if (msg == WM_COMMAND && (LOWORD(wp) == IDOK || LOWORD(wp) == IDCANCEL)) {
        if (LOWORD(wp) == IDOK) {
            wchar_t buf[256] = {};
            GetDlgItemTextW(d, kEdit, buf, 256);
            *reinterpret_cast<Ask *>(GetWindowLongPtrW(d, DWLP_USER))->text = buf;
        }
        EndDialog(d, LOWORD(wp));
        return TRUE;
    }
    return FALSE;
}

bool ask_text(HWND owner, const wchar_t *title, const wchar_t *prompt, std::wstring *text)
{
    std::vector<WORD> t;
    auto words = [&t](const void *p, size_t bytes) {
        const WORD *w = static_cast<const WORD *>(p);
        t.insert(t.end(), w, w + bytes / 2);
    };
    auto str = [&t](const wchar_t *s) {
        for (; *s; s++) t.push_back(*s);
        t.push_back(0);
    };
    auto item = [&](DWORD style, short x, short y, short cx, short cy, WORD id, WORD atom, const wchar_t *label) {
        if (t.size() % 2) t.push_back(0); // each item starts on a DWORD
        DLGITEMTEMPLATE it{WS_CHILD | WS_VISIBLE | style, 0, x, y, cx, cy, id};
        words(&it, sizeof it);
        t.push_back(0xffff), t.push_back(atom); // 0x80 button, 0x81 edit, 0x82 static
        str(label);
        t.push_back(0); // no creation data
    };
    DLGTEMPLATE head{DS_MODALFRAME | DS_SETFONT | DS_CENTER | WS_POPUP | WS_CAPTION | WS_SYSMENU, 0, 4, 0, 0, 236, 76};
    words(&head, sizeof head);
    t.push_back(0), t.push_back(0); // no menu, the default class
    str(title);
    t.push_back(9);
    str(L"Segoe UI");
    item(0, 7, 7, 222, 26, kPrompt, 0x82, L"");
    item(WS_BORDER | WS_TABSTOP | ES_AUTOHSCROLL, 7, 36, 222, 13, kEdit, 0x81, L"");
    item(BS_DEFPUSHBUTTON | WS_TABSTOP, 125, 56, 50, 14, IDOK, 0x80, L"OK");
    item(WS_TABSTOP, 179, 56, 50, 14, IDCANCEL, 0x80, L"Cancel");
    Ask a{prompt, text};
    return DialogBoxIndirectParamW(GetModuleHandleW(nullptr), reinterpret_cast<LPCDLGTEMPLATEW>(t.data()), owner, ask_proc,
                                   reinterpret_cast<LPARAM>(&a)) == IDOK;
}

} // namespace

namespace host {

void *online_menu(unsigned first_id)
{
    HMENU m = CreatePopupMenu();
    g_first = first_id;
    AppendMenuW(m, MF_STRING | MF_GRAYED, g_first + kNow, widen(g_now).c_str());
    AppendMenuW(m, MF_SEPARATOR, 0, nullptr);
    AppendMenuW(m, MF_STRING, g_first + kAlone, L"Play &alone");
    AppendMenuW(m, MF_STRING, g_first + kHost, L"&Host a game");
    AppendMenuW(m, MF_STRING, g_first + kJoin, L"&Join a game...");
    return m;
}

void online_sync(void *bar)
{
    HMENU m = static_cast<HMENU>(bar);
    const std::string &s = saved();
    if (!g_first) return;
    ModifyMenuW(m, g_first + kNow, MF_BYCOMMAND | MF_STRING | MF_GRAYED, g_first + kNow, widen(g_now).c_str());
    CheckMenuItem(m, g_first + kAlone, MF_BYCOMMAND | (s == "off" ? MF_CHECKED : MF_UNCHECKED));
    CheckMenuItem(m, g_first + kHost, MF_BYCOMMAND | (s == "on" ? MF_CHECKED : MF_UNCHECKED));
    CheckMenuItem(m, g_first + kJoin, MF_BYCOMMAND | (s != "off" && s != "on" ? MF_CHECKED : MF_UNCHECKED));
}

bool online_command(void *hwnd, unsigned id)
{
    HWND h = static_cast<HWND>(hwnd);
    if (!g_first || id < g_first || id >= g_first + kCount) return false;
    if (id == g_first + kAlone) choose(h, "off", L"playing alone");
    if (id == g_first + kHost) choose(h, "on", L"as the host (player 2 joins this computer)");
    if (id == g_first + kJoin) {
        const std::string &s = saved();
        std::wstring text = s != "off" && s != "on" && s != "join" ? widen(s) : L"";
        if (ask_text(h, L"Join a game", L"The host's address, as the host's Online menu shows it (for example 192.0.2.20, "
                                        L"or 192.0.2.20:29720 for another port):", &text) && !text.empty())
            choose(h, narrow(text), L"as player 2, joining " + text);
    }
    return true;
}

const char *online_locked() { return g_lock.empty() ? nullptr : g_lock.c_str(); }

// This program again on its own command line, less the options in drop (each with its value; "--net*": every option
// starting --net) and the one-start-only --level-play, then add; the new run waits for this one to end (PORT_RESUME_WAIT, host_resume.cpp's way) and this one
// closes. The new process's id, 0 if it could not start (logged).
unsigned long start_again(void *hwnd, const std::vector<std::wstring> &drop, const std::vector<std::wstring> &add, const char *who)
{
    int n = 0;
    wchar_t **argv = CommandLineToArgvW(GetCommandLineW(), &n), exe[MAX_PATH];
    GetModuleFileNameW(nullptr, exe, MAX_PATH);
    std::wstring cmd = arg_text(exe), next;
    bool has_next = false; // a test's --input-next A|B|...: the new copy plays --input A (and --input-next B|...)
    for (int i = 1; argv && i + 1 < n; i++)
        if (std::wstring(argv[i]) == L"--input-next") next = argv[i + 1], has_next = true;
    for (int i = 1; argv && i < n; i++) {
        std::wstring a = argv[i];
        bool dropped = std::any_of(drop.begin(), drop.end(), [&a](const std::wstring &d) {
            return !d.empty() && d.back() == L'*' ? a.rfind(d.substr(0, d.size() - 1), 0) == 0 : a == d;
        });
        if (has_next && (a == L"--input" || a == L"--input-next")) dropped = true;
        if (a == L"--level-play" || a.rfind(L"--level-edit-", 0) == 0 || dropped) {
            i++; // and its value (the editor's tests open it once, in this process only)
            continue;
        }
        cmd += L" " + arg_text(a);
    }
    LocalFree(argv);
    if (has_next) {
        size_t bar = next.find(L'|');
        cmd += L" --input " + arg_text(next.substr(0, bar));
        if (bar != std::wstring::npos) cmd += L" --input-next " + arg_text(next.substr(bar + 1));
    }
    for (const std::wstring &a : add) cmd += L" " + arg_text(a);
    std::wstring pid = std::to_wstring(GetCurrentProcessId());
    SetEnvironmentVariableW(L"PORT_RESUME_WAIT", pid.c_str()); // the child inherits it
    STARTUPINFOW si{};
    si.cb = sizeof si;
    PROCESS_INFORMATION pi{};
    BOOL ok = CreateProcessW(nullptr, cmd.data(), nullptr, nullptr, FALSE, 0, nullptr, nullptr, &si, &pi);
    DWORD err = GetLastError();
    SetEnvironmentVariableW(L"PORT_RESUME_WAIT", nullptr);
    if (!ok) {
        game_log_line("%s: could not start the game again (error %lu)", who, err);
        return 0;
    }
    CloseHandle(pi.hThread);
    CloseHandle(pi.hProcess);
    if (hwnd) PostMessageW(static_cast<HWND>(hwnd), WM_CLOSE, 0, 0); // none: the console ends this run itself
    return pi.dwProcessId;
}

} // namespace host

// A console whose online play is in the game's own menus (no Online menu): the game again at once with --net value and
// the extra arguments (space-separated), nothing asked; 1 when the new one started (the caller then ends this one).
extern "C" int host_online_restart(const char *value, const char *extra)
{
    return restart(nullptr, value ? value : "off", extra ? extra : "") ? 1 : 0; // the console ends this run (port_exit)
}

// The Online menu's saved choice back to playing alone (a console without the menu has no other way to change it).
extern "C" void host_online_forget(void)
{
    if (saved() == "off") return;
    g_saved = "off";
    host::settings_set("net", "off");
    game_log_line("online: the saved --net choice is back to off (online play is in the game's own menu)");
}

extern "C" void host_online_match(int hosting, const char *status)
{
    g_now = std::string("Now: ") + (status ? status : "");
    g_run = hosting ? "on" : "join";
    g_lock = "locked during online play: it would change the game for both players";
    game_log_line("online: %s; the menu's entries that would change the game are locked until this run ends", g_now.c_str());
}

// host.h: the game again with these options in place of its own ("--level-mod on --level-play on")
extern "C" int port_start_again(const char *args)
{
    std::vector<std::wstring> add, drop;
    std::string s = args ? args : "";
    for (size_t i = 0; i < s.size();) {
        while (i < s.size() && s[i] == ' ') i++;
        size_t j = s.find(' ', i);
        if (j == std::string::npos) j = s.size();
        if (j > i) {
            std::wstring w = widen(s.substr(i, j - i));
            add.push_back(w);
            if (w.rfind(L"--", 0) == 0) drop.push_back(w);
        }
        i = j;
    }
    unsigned long pid = host::start_again(host_window_hwnd(), drop, add, "start again");
    if (pid) game_log_line("start again: process %lu started with %s; this one ends", pid, s.c_str());
    return pid != 0;
}
