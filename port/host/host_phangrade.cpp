// Phangrade's game volume (its README "Shared settings"; Phangrade/integration is the source of truth for
// phangrade_audio.hpp, an identical copy). Inert unless PHANGRADE_SETTINGS names an absolute INI file. Its
// [audio] volume (a percentage, 25 by default) sets this process's Windows audio session on every output, seeded
// before the sound device opens; then a thread polls four times a second: a new value from the launcher is applied,
// and a change made in the Windows Volume Mixer is written back. The game's own --volume mix stays as it is.
// Window placement and Phangrade's window menu are not taken over: the kit's own menu and full-screen handling stay.
#include <filesystem>
#include <string>
#include <windows.h>
#include "host.h"
#include "phangrade_audio.hpp"

namespace {

std::wstring g_ini;
HANDLE g_ready;

int read_volume(int fallback)
{
    return std::clamp(static_cast<int>(GetPrivateProfileIntW(L"audio", L"volume", fallback, g_ini.c_str())), 0, 100);
}

DWORD WINAPI poll(void *)
{
    phangrade::Audio audio; // COM on this thread: opened, used and released here
    int volume = read_volume(25);
    audio.open(volume);
    SetEvent(g_ready);
    for (;;) {
        Sleep(250);
        int requested = read_volume(volume), changed = audio.changed_volume();
        if (requested != volume) {
            audio.set(volume = requested);
        } else if (changed >= 0 && changed != volume) {
            audio.set(volume = changed);
            WritePrivateProfileStringW(L"audio", L"volume", std::to_wstring(volume).c_str(), g_ini.c_str());
        }
    }
}

} // namespace

extern "C" void host_phangrade_start(void)
{
    static wchar_t path[32768];
    DWORD n = GetEnvironmentVariableW(L"PHANGRADE_SETTINGS", path, 32768);
    if (n == 0 || n >= 32768 || !std::filesystem::path(path).is_absolute()) return;
    g_ini = path;
    g_ready = CreateEventW(nullptr, TRUE, FALSE, nullptr);
    HANDLE thread = g_ready ? CreateThread(nullptr, 0, poll, nullptr, 0, nullptr) : nullptr;
    if (!thread) return;
    WaitForSingleObject(g_ready, 5000); // the sessions are seeded before any stream
    CloseHandle(thread);
    port_log("host: Phangrade settings %ls, volume %d%%\n", path, read_volume(25));
}
