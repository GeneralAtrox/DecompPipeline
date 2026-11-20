// The saved options, data\settings.txt: one "key=value" per line, '#' starts a comment. A line is the command-line
// option "--key value", for the keys a player's menu choice writes only (player_key); any other is ignored and logged.
// They go before the real command line, whose options are parsed later and so win.
#include <cctype>
#include <fstream>
#include "host.h"
#include "host_internal.hpp"
#if __has_include("port_config.h")
#include "port_config.h" /* game.json game_menu */
#endif
#ifndef PORT_GAME_MENU
#define PORT_GAME_MENU 0
#endif

namespace host {

std::string trim(const std::string &s)
{
    const char *space = " \t\r\n";
    size_t b = s.find_first_not_of(space);
    if (b == std::string::npos) return {};
    return s.substr(b, s.find_last_not_of(space) - b + 1);
}

// A key a player's menu choice writes: the base's menus (host_menu.cpp, host_menu_kbm.cpp's kbm-*, the bindings' bind-*,
// host_bar.cpp, host_online.cpp, host_resolution.cpp) and the console's and game's own entries. The user, 2026-11-13: "a
// settings file should not be controlling how the game behaves"; any other option (presenting, threads, waits, the swap
// chain, pacing) is the command line's only, for tests and agents.
static bool player_key(const std::string &key)
{
    static const char *const base[] = {"window", "fullscreen-mode", "aspect", "fps", "fps-counter", "vsync", "menu-bar", "volume",
                                       "mute-unfocused", "rumble", "pause-unfocused", "pad-art", "twin-stick", "auto-load", "net"};
    for (const char *k : base)
        if (key == k) return true;
    if (key.rfind("bind-", 0) == 0 || key.rfind("kbm-", 0) == 0) return true;
    auto listed = [&key](const PortMenuItem *items, unsigned n) {
        for (unsigned i = 0; items && i < n; i++)
            if (items[i].setting && key == items[i].setting) return true;
        return false;
    };
    unsigned n = 0;
    const PortMenuItem *items = console_menu_extra(&n);
    if (listed(items, n)) return true;
#if PORT_GAME_MENU
    n = 0;
    items = game_menu_extra(&n);
    if (listed(items, n)) return true;
#endif
    return false;
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
            port_log_early("settings: %s line %d: expected key=value\n", file.string().c_str(), n);
            continue;
        }
        if (key.rfind("game.", 0) == 0) continue; // a game's own value (port_setting_*), not an option
        if (key == "present") continue;           // saved by the In-between frames entry there used to be
        if (key == "interp-scene") continue;      // saved by the later In-between frames entry: game.json interp_scene decides
        if (!player_key(key)) {
            port_log_early("settings: %s line %d: %s is not a player setting, ignored (a technical option is the command line's)\n",
                     file.string().c_str(), n, key.c_str());
            continue;
        }
        args.push_back("--" + key);
        args.push_back(trim(text.substr(eq + 1)));
    }
    return args;
}

std::filesystem::path g_file; // the file the menu writes; empty: none (scripted runs, --settings none)

void settings_use(const std::filesystem::path &file) { g_file = file; }

// A launcher hands a game the settings file its catalogue names, for every version of the game: a build from before the
// move onto the Core keeps JSON there. Its first non-blank character is '{', or, before the older build has saved one,
// its name ends in .json (a fresh install: the Core writing key=value lines there would leave the older build, picked
// again to go back, unable to load it). The Core never writes that file (key=value lines appended to it left the older
// build unable to load it) and keeps its own in data\settings.txt.
bool settings_is_json(const std::filesystem::path &file)
{
    std::string ext = file.extension().string();
    for (char &c : ext) c = (char)std::tolower((unsigned char)c);
    if (ext == ".json") return true;
    std::ifstream in(file, std::ios::binary);
    char c = 0;
    while (in.get(c))
        if (c != ' ' && c != '\t' && c != '\r' && c != '\n' && (unsigned char)c < 0x80) return c == '{';
    return false;
}

static std::string g_legacy; // an older build's JSON settings file, read by the game once (port_settings_legacy)
void settings_legacy(const std::filesystem::path &file) { g_legacy = file.string(); }
volatile unsigned g_changes; // settings_set calls that reached the file: port_settings_changes

// The key's line is replaced where it stands (later lines for it are dropped) or appended; comments and other keys
// stay. Written to FILE.new and moved over FILE.
void settings_set(const std::string &key, const std::string &value)
{
    if (g_file.empty()) return;
    g_changes = g_changes + 1u;
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

// A game's own integer in the settings file ("game.NAME=value", apart from the game's save; on/off, as a Game menu check
// saves it, read as 1/0). Only a play run with a settings file
// has one: a scripted run, or --settings none, always gets the default.
extern "C" int port_setting_get_int(const char *key, int def)
{
    std::string k = key ? key : "";
    if (k.rfind("game.", 0) != 0 || host::g_file.empty()) return def;
    std::ifstream in(host::g_file);
    std::string line;
    int value = def;
    while (std::getline(in, line)) {
        std::string text = host::trim(line.substr(0, line.find('#')));
        size_t eq = text.find('=');
        if (eq == std::string::npos || host::trim(text.substr(0, eq)) != k) continue;
        std::string v = host::trim(text.substr(eq + 1));
        try {
            value = v == "on" ? 1 : v == "off" ? 0 : std::stoi(v);
        } catch (...) {
            value = def;
        }
    }
    return value;
}

// How many times the settings file has been changed in this run (any key, by a menu or the game): a game that keeps a
// game.NAME value it reads often reads the file again only when this moves. 0 for a run with no settings file.
extern "C" unsigned port_settings_changes(void) { return host::g_changes; }

extern "C" const char *port_settings_file(void)
{
    static std::string s;
    s = host::g_file.string();
    return s.c_str();
}

extern "C" const char *port_settings_legacy(void) { return host::g_legacy.c_str(); }

extern "C" void port_setting_set(const char *key, const char *value) { host::settings_set(key, value ? value : ""); }
// A game's own short text ("game.NAME=text"), as port_setting_get_int: def when there is none. 1 when one was found.
extern "C" int port_setting_get_str(const char *key, char *out, int cap)
{
    std::string k = key ? key : "", v;
    bool found = false;
    if (cap <= 0 || k.rfind("game.", 0) != 0 || host::g_file.empty()) return 0;
    std::ifstream in(host::g_file);
    std::string line;
    while (std::getline(in, line)) {
        std::string text = host::trim(line.substr(0, line.find('#')));
        size_t eq = text.find('=');
        if (eq != std::string::npos && host::trim(text.substr(0, eq)) == k) v = host::trim(text.substr(eq + 1)), found = true;
    }
    if (found) snprintf(out, (size_t)cap, "%s", v.c_str());
    return found ? 1 : 0;
}

extern "C" void port_setting_set_str(const char *key, const char *value)
{
    std::string k = key ? key : "";
    if (k.rfind("game.", 0) != 0) {
        port_log("settings: a game's key starts with \"game.\", not \"%s\"\n", k.c_str());
        return;
    }
    host::settings_set(k, value ? value : "");
}

extern "C" void port_setting_set_int(const char *key, int value)
{
    std::string k = key ? key : "";
    if (k.rfind("game.", 0) != 0) {
        port_log("settings: a game's key starts with \"game.\", not \"%s\"\n", k.c_str());
        return;
    }
    host::settings_set(k, std::to_string(value));
}
