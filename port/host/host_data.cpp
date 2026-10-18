// The port's data folder, kept beside the executable as Lodoss keeps its own (portable, not AppData).
#include <cstdlib>
#include <system_error>
#include <windows.h>
#include "host.h"
#include "host_internal.hpp"

namespace host {

namespace fs = std::filesystem;

static fs::path exe_dir()
{
    wchar_t path[MAX_PATH];
    DWORD n = GetModuleFileNameW(nullptr, path, MAX_PATH);
    if (n == 0 || n == MAX_PATH) return fs::current_path();
    return fs::path(path).parent_path();
}

static fs::path g_given; // --data-directory

void set_data_dir(const fs::path &dir) { g_given = fs::absolute(dir); }

fs::path data_dir()
{
    if (!g_given.empty()) return g_given;
    char *env = nullptr;
    size_t len = 0;
    fs::path dir;
    if (_dupenv_s(&env, &len, "PORT_DATA_DIR") == 0 && env && *env) dir = env;
    std::free(env);
    return dir.empty() ? exe_dir() / "data" : dir;
}

fs::path logs_dir()
{
    std::error_code ec;
    fs::path dir = data_dir() / "logs";
    fs::create_directories(dir, ec);
    return dir;
}

void prepare_data_dir()
{
    std::error_code ec;
    fs::path data = data_dir(), old_saves = exe_dir() / "saves", saves = data / "saves";
    fs::create_directories(data, ec);
    if (g_given.empty() && fs::is_directory(old_saves, ec) && !fs::exists(saves, ec)) {
        fs::rename(old_saves, saves, ec);
        if (!ec) port_log("host: moved the saves folder into %s\n", saves.string().c_str()); // stdout: no log yet
    }
}

} // namespace host

extern "C" const char *host_data_folder(void)
{
    static std::string path;
    path = host::data_dir().string();
    return path.c_str();
}
