// The crash reporter, after Lodoss's crash_report (MIT, the user's own): an exception nothing handled (the guest
// dispatcher's vectored handler sees every fault first) writes a minidump to data\logs and a line to the log, then
// tells an interactive user where the dump is. The watchdog (runtime/main.c) stays the bound for hangs.
#include <cstdio>
#include <string>
#include <windows.h>
#include <dbghelp.h>
#include "host.h"
#include "host_internal.hpp"

namespace host {

static bool g_interactive;
static wchar_t g_dump[MAX_PATH];

static bool write_dump(EXCEPTION_POINTERS *ep)
{
    SYSTEMTIME t;
    GetLocalTime(&t);
    std::wstring name = (logs_dir() / "crash-").wstring();
    if (swprintf_s(g_dump, MAX_PATH, L"%ls%04u%02u%02u-%02u%02u%02u.dmp", name.c_str(), t.wYear, t.wMonth, t.wDay,
                   t.wHour, t.wMinute, t.wSecond) < 0)
        return false;
    HANDLE f = CreateFileW(g_dump, GENERIC_WRITE, 0, nullptr, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (f == INVALID_HANDLE_VALUE) return false;
    MINIDUMP_EXCEPTION_INFORMATION info{GetCurrentThreadId(), ep, FALSE};
    auto type = static_cast<MINIDUMP_TYPE>(MiniDumpNormal | MiniDumpWithThreadInfo);
    BOOL ok = MiniDumpWriteDump(GetCurrentProcess(), GetCurrentProcessId(), f, type, &info, nullptr, nullptr);
    CloseHandle(f);
    return ok != FALSE;
}

static LONG WINAPI on_crash(EXCEPTION_POINTERS *ep)
{
    static volatile LONG once;
    if (InterlockedExchange(&once, 1)) return EXCEPTION_CONTINUE_SEARCH;
    const EXCEPTION_RECORD *r = ep->ExceptionRecord;
    bool dumped = write_dump(ep);
    port_log("CRASH: exception %08lx at %p%s%ls\n", r->ExceptionCode, r->ExceptionAddress,
             dumped ? ", dump " : ", no dump", dumped ? g_dump : L"");
    port_log_flush();
    if (g_interactive) {
        wchar_t text[MAX_PATH + 128];
        swprintf_s(text, sizeof text / sizeof text[0], L"The port stopped on exception %08lx.\n\n%ls%ls",
                   r->ExceptionCode, dumped ? L"A crash dump was written to:\n" : L"No crash dump could be written.",
                   dumped ? g_dump : L"");
        MessageBoxW(nullptr, text, L"Port crash", MB_OK | MB_ICONERROR | MB_TASKMODAL);
    }
    return EXCEPTION_EXECUTE_HANDLER; // ends the process with the exception's code
}

void crash_install(bool interactive)
{
    g_interactive = interactive;
    SetUnhandledExceptionFilter(on_crash);
}

} // namespace host
