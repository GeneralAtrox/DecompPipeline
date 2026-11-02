// The port's data folder, kept beside the executable as Lodoss keeps its own (portable, not AppData).
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <string>
#include <system_error>
#include <windows.h>
#include "game_log.h"
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

namespace {

constexpr int kKeepLogs = 8;

std::string read_part(const fs::path &file, bool tail, size_t bytes)
{
    std::ifstream in(file, std::ios::binary);
    if (!in) return {};
    in.seekg(0, std::ios::end);
    std::streamoff size = in.tellg(), at = tail && size > static_cast<std::streamoff>(bytes) ? size - static_cast<std::streamoff>(bytes) : 0;
    in.seekg(at);
    std::string s(bytes, '\0');
    in.read(s.data(), static_cast<std::streamsize>(bytes));
    s.resize(static_cast<size_t>(in.gcount()));
    return s;
}

std::string last_line(const std::string &tail)
{
    size_t end = tail.find_last_not_of("\r\n");
    if (end == std::string::npos) return {};
    size_t start = tail.find_last_of('\n', end);
    std::string line = tail.substr(start == std::string::npos ? 0 : start + 1, end - (start == std::string::npos ? 0 : start));
    if (line.size() > 200) line.resize(200);
    return line;
}

std::string now_stamp()
{
    SYSTEMTIME t;
    GetLocalTime(&t);
    char s[32];
    std::snprintf(s, sizeof s, "%04u%02u%02u-%02u%02u%02u", t.wYear, t.wMonth, t.wDay, t.wHour, t.wMinute, t.wSecond);
    return s;
}

} // namespace

void rotate_logs()
{
    std::error_code ec;
    fs::path logs = logs_dir(), cur = logs / "port.log", first = logs / "port.1.log", autosave = logs / "autosave.ptrec";
    if (!fs::exists(cur, ec)) {
        fs::remove(autosave, ec);
        return;
    }
    auto numbered = [&](int n) { return logs / ("port." + std::to_string(n) + ".log"); };
    fs::remove(numbered(kKeepLogs), ec);
    for (int n = kKeepLogs - 1; n >= 1; n--)
        if (fs::exists(numbered(n), ec)) fs::rename(numbered(n), numbered(n + 1), ec);
    fs::rename(cur, first, ec);
    if (ec) return; // still open in another run: it is not ours to judge
    std::string head = read_part(first, false, 8192), tail = read_part(first, true, 65536);
    bool ours = head.find("gamelog: on") != std::string::npos, ended = tail.find("] END: ") != std::string::npos;
    if (!ours || ended) {
        fs::remove(autosave, ec);
        return;
    }
    std::string line = last_line(tail), rec;
    if (fs::exists(autosave, ec)) {
        fs::path kept = logs / ("killed-" + now_stamp() + ".ptrec");
        fs::rename(autosave, kept, ec);
        if (!ec) rec = kept.string();
    }
    std::string note = "the previous run (port.1.log) ended abruptly: its log has no END line, so the process was killed from "
                       "outside (Task Manager, a launcher, a power cut) or died where nothing can catch it (a stack overflow "
                       "or a fast-fail; Windows' Application log may hold the exception code). Its last line: " + line;
    std::ofstream(first, std::ios::app | std::ios::binary)
        << "[" << now_stamp() << "] END: missing. Found at the next launch: " << note << "\n";
    game_log_previous(note.c_str(), rec.c_str());
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
