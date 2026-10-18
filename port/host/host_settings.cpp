// The saved options, data\settings.txt: one "key=value" per line, '#' starts a comment. A line is the command-line
// option "--key value" (any port.exe option that takes a value: window, audio, vmu, disc, seconds, ...). They go
// before the real command line, whose options are parsed later and so win.
#include <fstream>
#include "host.h"
#include "host_internal.hpp"

namespace host {

static std::string trim(const std::string &s)
{
    const char *space = " \t\r\n";
    size_t b = s.find_first_not_of(space);
    if (b == std::string::npos) return {};
    return s.substr(b, s.find_last_not_of(space) - b + 1);
}

std::vector<std::string> settings_args(const std::filesystem::path &file)
{
    std::vector<std::string> args;
    std::ifstream in(file);
    std::string line;
    for (int n = 1; std::getline(in, line); n++) {
        std::string text = trim(line.substr(0, line.find('#')));
        size_t eq = text.find('=');
        if (text.empty()) continue;
        std::string key = eq == std::string::npos ? std::string() : trim(text.substr(0, eq));
        if (key.empty() || key.rfind("--", 0) == 0) {
            port_log("settings: %s line %d: expected key=value\n", file.string().c_str(), n);
            continue;
        }
        args.push_back("--" + key);
        args.push_back(trim(text.substr(eq + 1)));
    }
    return args;
}

static std::filesystem::path g_file; // the file the menu writes; empty: none (scripted runs, --settings none)

void settings_use(const std::filesystem::path &file) { g_file = file; }

// The key's line is replaced where it stands (later lines for it are dropped) or appended; comments and other keys
// stay. Written to FILE.new and moved over FILE.
void settings_set(const std::string &key, const std::string &value)
{
    if (g_file.empty()) return;
    std::vector<std::string> lines;
    std::string line;
    bool done = false;
    {
        std::ifstream in(g_file);
        while (std::getline(in, line)) {
            std::string text = trim(line.substr(0, line.find('#')));
            size_t eq = text.find('=');
            if (eq != std::string::npos && trim(text.substr(0, eq)) == key) {
                if (done) continue;
                line = key + "=" + value;
                done = true;
            }
            lines.push_back(line);
        }
    }
    if (!done) lines.push_back(key + "=" + value);
    std::filesystem::path tmp = g_file;
    tmp += ".new";
    {
        std::ofstream out(tmp, std::ios::trunc);
        for (const std::string &l : lines) out << l << '\n';
        if (!out) {
            port_log("settings: cannot write %s\n", tmp.string().c_str());
            return;
        }
    }
    std::error_code ec;
    std::filesystem::rename(tmp, g_file, ec);
    if (ec) port_log("settings: cannot replace %s\n", g_file.string().c_str());
}

} // namespace host
