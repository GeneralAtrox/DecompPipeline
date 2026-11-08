// Help > Resume before the last crash: the game starts again and plays the last crash's session recording
// (crash-<time>.ptrec, or killed-<time>.ptrec from a run that was killed) on the fixed clock, as fast as the machine
// goes, to a few seconds before it ended; there the replay hands over (session.c --replay-until) and the player plays
// on, on the real clock, with the real pads. Nothing is stored but the recording the game log already keeps, and the
// game itself is never written to: the replay gives it the same inputs, so it reaches the same state.
// The new run waits for this one to end first (PORT_RESUME_WAIT), so that it can move this run's log aside.
#include <windows.h>
#include <shellapi.h>
#include <cstdio>
#include <string>
#include "game_log.h"
#include "host.h"
#include "host_internal.hpp"

namespace fs = std::filesystem;

namespace host {

// An argument as CommandLineToArgvW reads it back (host_online.cpp's start-again uses it too).
std::wstring arg_text(const std::wstring &a)
{
    if (!a.empty() && a.find_first_of(L" \t\"") == std::wstring::npos) return a;
    std::wstring q = L"\"";
    unsigned slashes = 0;
    for (wchar_t c : a) {
        if (c == L'\\') {
            slashes++;
            continue;
        }
        q.append(c == L'"' ? slashes * 2 + 1 : slashes, L'\\');
        slashes = 0;
        q += c;
    }
    q.append(slashes * 2, L'\\');
    return q + L"\"";
}

} // namespace host

namespace {

constexpr unsigned kBackSeconds = 5;
using host::arg_text;

// This run's own command line without what a resume sets, then the resume's options.
std::wstring command_line(const fs::path &rec, unsigned until)
{
    int n = 0;
    wchar_t **argv = CommandLineToArgvW(GetCommandLineW(), &n);
    wchar_t exe[MAX_PATH];
    GetModuleFileNameW(nullptr, exe, MAX_PATH);
    std::wstring cmd = arg_text(exe);
    for (int i = 1; argv && i < n; i++) {
        std::wstring a = argv[i];
        if (a == L"--replay" || a == L"--replay-until" || a == L"--fast-until" || a == L"--live-input") {
            i++; // and its value
            continue;
        }
        cmd += L" " + arg_text(a);
    }
    LocalFree(argv);
    return cmd + L" --replay " + arg_text(rec.wstring()) + L" --replay-until " + std::to_wstring(until) + L" --fast-until " +
           std::to_wstring(until) + L" --live-input on";
}

} // namespace

namespace host {

bool resume_find(fs::path *rec, unsigned *end)
{
    std::error_code ec;
    fs::file_time_type newest{};
    bool found = false;
    for (const auto &e : fs::directory_iterator(logs_dir(), ec)) {
        std::wstring name = e.path().filename().wstring();
        if (e.path().extension() != L".ptrec" || (name.rfind(L"crash-", 0) && name.rfind(L"killed-", 0))) continue;
        auto t = e.last_write_time(ec);
        if (ec || (found && t <= newest)) continue;
        newest = t;
        *rec = e.path();
        found = true;
    }
    FILE *f = nullptr;
    *end = 0;
    if (!found || _wfopen_s(&f, rec->c_str(), L"rb") || !f) return false;
    if (std::fseek(f, -4, SEEK_END) || std::fread(end, 4, 1, f) != 1) *end = 0; // session.c: the last u32 is the blank reached
    std::fclose(f);
    return *end > 0;
}

void resume_start(void *hwnd)
{
    fs::path rec;
    unsigned end = 0;
    if (!resume_find(&rec, &end)) return;
    unsigned hz = port_console.tick_hz ? port_console.tick_hz : 60u, back = kBackSeconds * hz;
    unsigned until = end > 2 * back ? end - back : end / 2 + 1;
    wchar_t ask[600];
    std::swprintf(ask, 600,
                  L"Start the game again from %u seconds before the last crash?\n\nIts recording (%ls) is played to there "
                  L"as fast as the computer goes, then you play on. This game ends now.",
                  (end - until) / hz, rec.filename().c_str());
    if (MessageBoxW(static_cast<HWND>(hwnd), ask, L"Resume before the last crash", MB_OKCANCEL | MB_ICONQUESTION) != IDOK) return;
    std::wstring cmd = command_line(rec, until), pid = std::to_wstring(GetCurrentProcessId());
    SetEnvironmentVariableW(L"PORT_RESUME_WAIT", pid.c_str()); // the child inherits it
    STARTUPINFOW si{};
    si.cb = sizeof si;
    PROCESS_INFORMATION pi{};
    BOOL ok = CreateProcessW(nullptr, cmd.data(), nullptr, nullptr, FALSE, 0, nullptr, nullptr, &si, &pi);
    SetEnvironmentVariableW(L"PORT_RESUME_WAIT", nullptr);
    if (!ok) {
        game_log_line("resume: could not start the game again (error %lu)", GetLastError());
        return;
    }
    CloseHandle(pi.hThread);
    CloseHandle(pi.hProcess);
    game_log_line("resume: started process %lu, which replays %s to vertical blank %u of %u and plays on from there",
                  pi.dwProcessId, rec.string().c_str(), until, end);
    PostMessageW(static_cast<HWND>(hwnd), WM_CLOSE, 0, 0);
}

void resume_wait()
{
    wchar_t pid[16];
    if (!GetEnvironmentVariableW(L"PORT_RESUME_WAIT", pid, 16)) return;
    SetEnvironmentVariableW(L"PORT_RESUME_WAIT", nullptr);
    HANDLE h = OpenProcess(SYNCHRONIZE, FALSE, static_cast<DWORD>(_wtoi(pid)));
    if (!h) return;
    WaitForSingleObject(h, 15000); // the run that asked for it closes its window and ends; never wait for ever
    CloseHandle(h);
}

} // namespace host
