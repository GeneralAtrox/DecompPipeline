// The menu's extra entries (console.h PortMenuItem): the base's File > Restart, the console's (console_menu_extra), then the game's
// (game_menu_extra, game.json game_menu or a native game's own), then the options' (game_options.c: the base's, the
// console's and the game's PortOption tables), applied the same from the menu bar (host_menu.cpp) and
// from --menu-pick FRAME:Menu>Label[>Choice...], a scripted run's pick at a frame (native_frame.c polls it). Also the
// display settings a native game's own options page shows (port_fullscreen, port_fps_counter: base.h).
// A tagged entry (the game's own command: game.json menu_commands, or a native game's) is not applied here: its pick is
// queued as (tag, index) and the game takes it at its next tick's pad read (port_menu_drain, input.c), which records it
// (session_events.c). Its labels are static, or a runtime list the game gives (pg_native_menu_list); a '>' in one is a
// path of nested popups, and more than 16 are split into "1-16", "17-32" (the last as far as it goes: "17-20"), ... (menu_tree; host_menu_tagged.cpp builds
// the popups). --menu-pick takes the whole path, the "17-32" part optional.
#include <cctype>
#include <cstdlib>
#include <cstring>
#include <deque>
#include <iterator>
#include <map>
#include <mutex>
#include <string>
#include <utility>
#include <vector>
#include "console.h"
#include "game_log.h"
#include "host.h"
#include "host_internal.hpp"
#include "save_state.h"
#if __has_include("port_config.h")
#include "port_config.h" /* game.json game_menu */
#endif
#ifndef PORT_GAME_MENU
#define PORT_GAME_MENU 0
#endif
extern "C" int session_replaying(void); // session.c (session.h is C only)

namespace {

std::vector<PortMenuItem> g_extra_all;
const PortMenuItem *g_extra;
unsigned g_extra_count;
std::mutex g_tm;                                       // the picks queued and the runtime lists (any thread)
std::deque<std::pair<int, int>> g_queue;               // tagged picks for the game's next tick
std::map<int, std::pair<unsigned, std::vector<std::string>>> g_lists; // runtime lists: tag -> (changes, labels)
constexpr unsigned kGroup = 16, kListMax = 435;        // a popup's entries before splitting; a list's most (host_menu_tagged.cpp)

const char *on_off(bool on) { return on ? "on" : "off"; }

// File > Restart: the game again on this run's own command line (host_online.cpp's start_again), less a scripted
// --menu-pick (it would pick again); this run then ends, by its window's close or, with none, port_exit, so its recording
// and game log close cleanly before the new run (which waits for this one: PORT_RESUME_WAIT) opens its own. Offered in a
// play run, greyed in a replay or a check run (the fixed clock). Nothing is asked: the base cannot know whether the game
// holds unsaved progress. No shortcut: any free key may be one of the game's (Input > Keyboard and mouse).
int restart_offered() { return !session_replaying() && !host_fixed_clock(); }
int restart_get() { return 0; }
void restart_set(int)
{
    void *hwnd = host_window_hwnd();
    if (host::start_again(hwnd, {L"--menu-pick"}, {}, "File > Restart") && !hwnd) port_exit("File > Restart: the game started again", 0);
}
const PortMenuItem k_file[] = {{"File", "&Restart", PORT_MENU_COMMAND, nullptr, restart_get, restart_set, nullptr, nullptr, 0u,
                                restart_offered, 0}};

// The base's File entries (Restart, then Save state, Load state and their note: save_state.c), the console's, then the
// game's (once: the menu bar and --menu-pick share them).
void extras_build()
{
    static bool built;
    if (built) return;
    built = true;
    unsigned n = 0;
    const PortMenuItem *items = console_menu_extra(&n);
    g_extra_all.assign(std::begin(k_file), std::end(k_file));
    unsigned ns = 0;
    const PortMenuItem *states = save_state_menu(&ns);
    g_extra_all.insert(g_extra_all.end(), states, states + ns);
    if (items) g_extra_all.insert(g_extra_all.end(), items, items + n);
#if PORT_GAME_MENU
    n = 0;
    items = game_menu_extra(&n);
    if (items) g_extra_all.insert(g_extra_all.end(), items, items + n);
#endif
    n = 0;
    items = port_options_menu(&n);
    if (items) g_extra_all.insert(g_extra_all.end(), items, items + n);
    g_extra = g_extra_all.data();
    g_extra_count = static_cast<unsigned>(g_extra_all.size());
}

// Entry i of the extras chosen (a choice: its n-th value; a submenu: its n-th command): applied; a check or a choice kept.
void extra_apply(unsigned i, unsigned n)
{
    const PortMenuItem &e = g_extra[i];
    if (e.available && !e.available()) {
        game_log_line("menu: not applied: %s", host::online_locked() ? host::online_locked() : "the game cannot apply it now");
        return;
    }
    if (e.tag) { // the game's own: taken at its next tick (input.c), recorded there
        std::lock_guard<std::mutex> l(g_tm);
        if (g_queue.size() < 64) g_queue.emplace_back(e.tag, static_cast<int>(n));
        game_log_line("menu: tag %d entry %u for the game's next tick", e.tag, n);
    } else if (e.kind == PORT_MENU_CHOICE) {
        e.set(static_cast<int>(n));
        host::settings_set(e.setting, e.values[e.get()]);
    } else if (e.kind == PORT_MENU_SUBMENU) {
        if (n < e.n_choices) e.set(static_cast<int>(n));
    } else if (e.kind == PORT_MENU_COMMAND) {
        e.set(0);
    } else {
        e.set(!e.get());
        host::settings_set(e.setting, on_off(e.get() != 0));
    }
}

// --menu-pick: a label as the menu shows it, without its & ("Level &select" is "Level select"), in any case.
bool same_label(const char *label, const std::string &want)
{
    std::string plain, w;
    for (const char *c = label; c && *c; c++)
        if (*c != '&') plain += static_cast<char>(std::tolower(static_cast<unsigned char>(*c)));
    for (char c : want) w += static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
    return plain == w;
}

std::vector<std::string> split(const std::string &s) // "a > b>c" -> a, b, c
{
    std::vector<std::string> out;
    for (size_t at = 0;;) {
        size_t gt = s.find('>', at);
        std::string part = s.substr(at, gt == std::string::npos ? std::string::npos : gt - at);
        size_t a = part.find_first_not_of(' '), b = part.find_last_not_of(' ');
        out.push_back(a == std::string::npos ? std::string() : part.substr(a, b - a + 1));
        if (gt == std::string::npos) return out;
        at = gt + 1;
    }
}

void group(host::MenuNode &n) // more than kGroup kids: "1-16", "17-32", ...
{
    for (host::MenuNode &k : n.kids) group(k);
    if (n.kids.size() <= kGroup) return;
    std::vector<host::MenuNode> parts;
    for (size_t at = 0; at < n.kids.size(); at += kGroup) {
        size_t end = at + kGroup < n.kids.size() ? at + kGroup : n.kids.size();
        host::MenuNode g;
        g.label = std::to_string(at + 1) + "-" + std::to_string(end), g.group = true;
        g.kids.assign(n.kids.begin() + static_cast<std::ptrdiff_t>(at), n.kids.begin() + static_cast<std::ptrdiff_t>(end));
        parts.push_back(std::move(g));
    }
    n.kids = std::move(parts);
}

// The leaf the path tail[at..] names under n, or -1; a group's label may be left out of the path.
int tree_pick(const host::MenuNode &n, const std::vector<std::string> &tail, size_t at)
{
    if (at == tail.size()) return n.kids.empty() ? n.index : -1;
    for (const host::MenuNode &k : n.kids) {
        int r = same_label(k.label.c_str(), tail[at]) ? tree_pick(k, tail, at + 1) : -1;
        if (r < 0 && k.group) r = tree_pick(k, tail, at);
        if (r >= 0) return r;
    }
    return -1;
}

struct Pick {
    u32 frame;
    std::string menu, label;
    std::vector<std::string> tail; // the choice, or a tagged entry's path
};
std::vector<Pick> g_picks;

std::string joined(const std::vector<std::string> &tail)
{
    std::string s;
    for (const std::string &t : tail) s += (s.empty() ? "" : " > ") + t;
    return s;
}

} // namespace

namespace host {

const PortMenuItem *menu_extras(unsigned *count)
{
    extras_build();
    *count = g_extra_count;
    return g_extra;
}

void menu_extra_apply(unsigned i, unsigned n)
{
    if (i < g_extra_count) extra_apply(i, n);
}

unsigned menu_entries(const PortMenuItem &e, std::vector<std::string> *labels)
{
    labels->clear();
    if (e.kind != PORT_MENU_SUBMENU) return 0;
    if (e.choices) {
        for (unsigned n = 0; n < e.n_choices && n < kListMax; n++) labels->push_back(e.choices[n] ? e.choices[n] : "");
        return 0;
    }
    std::lock_guard<std::mutex> l(g_tm);
    auto it = g_lists.find(e.tag);
    if (it == g_lists.end()) return 0;
    *labels = it->second.second;
    return it->second.first;
}

MenuNode menu_tree(const std::vector<std::string> &labels)
{
    MenuNode root;
    for (size_t i = 0; i < labels.size(); i++) {
        MenuNode *at = &root;
        std::vector<std::string> path = split(labels[i]);
        for (size_t p = 0; p + 1 < path.size(); p++) { // the path's popups, shared by the entries that name them
            MenuNode *found = nullptr;
            for (MenuNode &k : at->kids)
                if (!k.kids.empty() && k.label == path[p]) found = &k;
            if (!found) {
                at->kids.push_back(MenuNode{path[p], -1, false, {}});
                found = &at->kids.back();
            }
            at = found;
        }
        at->kids.push_back(MenuNode{path.back(), static_cast<int>(i), false, {}});
    }
    group(root);
    return root;
}

} // namespace host

extern "C" int port_menu_pick_option(const char *arg, const char *value)
{
    if (!value || std::strcmp(arg, "--menu-pick")) return 0;
    std::string v = value;
    size_t colon = v.find(':');
    std::vector<std::string> path = split(colon == std::string::npos ? std::string() : v.substr(colon + 1));
    if (colon == std::string::npos || path.size() < 2 || g_picks.size() >= 8) {
        port_log("menu: --menu-pick %s: not FRAME:Menu>Label[>Choice...] (at most 8)\n", value);
        return 2;
    }
    g_picks.push_back(Pick{static_cast<u32>(std::strtoul(v.c_str(), nullptr, 10)), path[0], path[1],
                           std::vector<std::string>(path.begin() + 2, path.end())});
    return 2;
}

extern "C" void port_menu_pick_poll(u32 frame)
{
    for (const Pick &p : g_picks) {
        if (p.frame != frame) continue;
        extras_build();
        bool done = false;
        std::string choice = joined(p.tail);
        for (unsigned i = 0; i < g_extra_count && !done; i++) {
            const PortMenuItem &e = g_extra[i];
            if (!same_label(e.menu, p.menu) || !same_label(e.label, p.label)) continue;
            int n = 0;
            if (e.tag && e.kind == PORT_MENU_SUBMENU) {
                std::vector<std::string> labels;
                host::menu_entries(e, &labels);
                if ((n = tree_pick(host::menu_tree(labels), p.tail, 0)) < 0) break;
            } else if (e.kind == PORT_MENU_CHOICE || e.kind == PORT_MENU_SUBMENU) {
                while (static_cast<unsigned>(n) < e.n_choices && (p.tail.size() != 1 || !same_label(e.choices[n], p.tail[0]))) n++;
                if (static_cast<unsigned>(n) == e.n_choices) break;
            } else if (!p.tail.empty()) {
                break;
            }
            game_log_line("menu: %s > %s%s%s (--menu-pick)", p.menu.c_str(), p.label.c_str(), choice.empty() ? "" : " > ",
                          choice.c_str());
            extra_apply(i, static_cast<unsigned>(n));
            done = true;
        }
        if (!done) game_log_line("menu: --menu-pick %s > %s > %s: no such entry", p.menu.c_str(), p.label.c_str(), choice.c_str());
    }
}

extern "C" int port_menu_drain(int *tag, int *index)
{
    std::lock_guard<std::mutex> l(g_tm);
    if (g_queue.empty()) return 0;
    *tag = g_queue.front().first, *index = g_queue.front().second;
    g_queue.pop_front();
    return 1;
}

// A runtime list's labels (copied), from the game; the menu builds them again before it next opens.
extern "C" void pg_native_menu_list(int tag, const char *const *labels, int n)
{
    std::vector<std::string> v;
    for (int i = 0; labels && i < n && static_cast<unsigned>(i) < kListMax; i++) v.push_back(labels[i] ? labels[i] : "");
    std::lock_guard<std::mutex> l(g_tm);
    auto &list = g_lists[tag];
    if (list.first && list.second == v) return;
    list.first++, list.second = std::move(v);
    port_log("menu: the game's list %d has %u entries\n", tag, static_cast<unsigned>(list.second.size()));
}

extern "C" int port_fullscreen(void) { return host::window_fullscreen() ? 1 : 0; }
extern "C" void port_set_fullscreen(int on)
{
    if ((on != 0) != host::window_fullscreen()) host::window_set_fullscreen(on != 0);
}
extern "C" int port_fps_counter(void) { return host::prefs().fps_counter ? 1 : 0; }
extern "C" void port_set_fps_counter(int on)
{
    host::prefs().fps_counter = on != 0;
    host::settings_set("fps-counter", on_off(on != 0));
}
