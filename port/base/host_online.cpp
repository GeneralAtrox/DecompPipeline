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

// The game again, with --net value: this one closes once the new one has started.
void restart(HWND hwnd, const std::string &value)
{
    int n = 0;
    wchar_t **argv = CommandLineToArgvW(GetCommandLineW(), &n), exe[MAX_PATH];
    GetModuleFileNameW(nullptr, exe, MAX_PATH);
    std::wstring cmd = host::arg_text(exe);
    for (int i = 1; argv && i < n; i++) {
        std::wstring a = argv[i];
        if (a == L"--net" || a == L"--net-host" || a == L"--net-join") {
            i++; // and its value
            continue;
        }
        cmd += L" " + host::arg_text(a);
    }
    LocalFree(argv);
    cmd += L" --net " + host::arg_text(widen(value));
    std::wstring pid = std::to_wstring(GetCurrentProcessId());
    SetEnvironmentVariableW(L"PORT_RESUME_WAIT", pid.c_str()); // the child inherits it
    STARTUPINFOW si{};
    si.cb = sizeof si;
    PROCESS_INFORMATION pi{};
    BOOL ok = CreateProcessW(nullptr, cmd.data(), nullptr, nullptr, FALSE, 0, nullptr, nullptr, &si, &pi);
    SetEnvironmentVariableW(L"PORT_RESUME_WAIT", nullptr);
    if (!ok) {
        game_log_line("online: could not start the game again (error %lu)", GetLastError());
        return;
    }
    CloseHandle(pi.hThread);
    CloseHandle(pi.hProcess);
    game_log_line("online: started process %lu with --net %s; this one ends", pi.dwProcessId, value.c_str());
    PostMessageW(hwnd, WM_CLOSE, 0, 0);
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
        if (ask_text(h, L"Join a game", L"The host's address, as the host's Online menu shows it (for example 192.168.1.20, "
                                        L"or 192.168.1.20:29720 for another port):", &text) && !text.empty())
            choose(h, narrow(text), L"as player 2, joining " + text);
    }
    return true;
}

const char *online_locked() { return g_lock.empty() ? nullptr : g_lock.c_str(); }

} // namespace host

extern "C" void host_online_match(int hosting, const char *status)
{
    g_now = std::string("Now: ") + (status ? status : "");
    g_run = hosting ? "on" : "join";
    g_lock = "locked during online play: it would change the game for both players";
    game_log_line("online: %s; the menu's entries that would change the game are locked until this run ends", g_now.c_str());
}
