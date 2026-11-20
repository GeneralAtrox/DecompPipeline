// The game's own commands on the menu bar (console.h PortMenuItem with a tag: game.json menu_commands, or a native game's
// own). A tagged command is the extras' ordinary entry (host_menu.cpp, kExtra); a tagged submenu is built here: its labels
// (static, or the runtime list the game gives, pg_native_menu_list) made a tree (host_menu_extra.cpp menu_tree: '>' paths
// nest, more than 16 split into "1-16", "17-32", ...), each leaf an id from kTaggedId. A runtime list that changed is built
// again as its top menu opens (WM_INITMENUPOPUP for the bar or one of its menus), never while one of its own popups is
// open. A pick goes to host_menu_extra.cpp's extra_apply, which queues (tag, entry) for the game's next tick.
// At most kTaggedIds leaves in all: ids 5200..5634, clear of the Controls (4000..5023) and the keyboard and mouse
// (7960..) ranges.
// Nearest existing: host_menu.cpp append_extras - one popup of at most 16 fixed choices per entry, ids by position;
// these lists are longer, nested and change while the game runs.
#include <string>
#include <vector>
#include <windows.h>
#include "console.h"
#include "game_log.h"
#include "host_internal.hpp"

namespace {

constexpr UINT kTaggedId = 5200, kTaggedIds = 435;

struct Slot {
    unsigned item;
    int index;
};
struct Built {
    unsigned item;
    HMENU popup;
    unsigned changes;
};
std::vector<Slot> g_slots;
std::vector<Built> g_built;

std::wstring wide(const std::string &s)
{
    int n = MultiByteToWideChar(CP_UTF8, 0, s.c_str(), -1, nullptr, 0);
    std::wstring w(n > 0 ? static_cast<size_t>(n - 1) : 0u, L'\0');
    if (n > 1) MultiByteToWideChar(CP_UTF8, 0, s.c_str(), -1, w.data(), n);
    return w;
}

void fill(HMENU m, const host::MenuNode &node, unsigned item)
{
    for (const host::MenuNode &k : node.kids) {
        if (!k.kids.empty()) {
            HMENU sub = CreatePopupMenu();
            fill(sub, k, item);
            AppendMenuW(m, MF_POPUP, reinterpret_cast<UINT_PTR>(sub), wide(k.label).c_str());
        } else if (g_slots.size() < kTaggedIds) {
            AppendMenuW(m, MF_STRING, kTaggedId + static_cast<UINT>(g_slots.size()), wide(k.label).c_str());
            g_slots.push_back(Slot{item, k.index});
        }
    }
}

// Built's popup filled from the entry's labels now; its change count kept.
void build(Built &b)
{
    unsigned count = 0;
    const PortMenuItem *extras = host::menu_extras(&count);
    std::vector<std::string> labels;
    b.changes = host::menu_entries(extras[b.item], &labels);
    while (GetMenuItemCount(b.popup) > 0) DeleteMenu(b.popup, 0, MF_BYPOSITION); // its own popups go with it
    if (labels.empty()) AppendMenuW(b.popup, MF_STRING | MF_GRAYED, 0, L"(none yet)");
    else fill(b.popup, host::menu_tree(labels), b.item);
}

} // namespace

namespace host {

void *menu_tagged_popup(unsigned item)
{
    g_built.push_back(Built{item, CreatePopupMenu(), 0});
    build(g_built.back());
    return g_built.back().popup;
}

void menu_tagged_sync(void *bar_menu, void *opening)
{
    HMENU bar = static_cast<HMENU>(bar_menu), open = static_cast<HMENU>(opening);
    bool top = open == bar;
    for (int i = 0, n = bar ? GetMenuItemCount(bar) : 0; i < n && !top; i++) top = GetSubMenu(bar, i) == open;
    if (!top || g_built.empty()) return;
    unsigned count = 0;
    const PortMenuItem *extras = menu_extras(&count);
    bool changed = false;
    std::vector<std::string> labels;
    for (const Built &b : g_built) changed = changed || menu_entries(extras[b.item], &labels) != b.changes;
    if (!changed) return;
    g_slots.clear(); // all of them again, so the ids stay in one run
    for (Built &b : g_built) build(b);
    if (g_slots.size() >= kTaggedIds) port_log("menu: the game's lists fill the menu's %u entries; the rest are left off\n", kTaggedIds);
}

bool menu_tagged_command(unsigned id)
{
    if (id < kTaggedId || id - kTaggedId >= g_slots.size()) return false;
    const Slot &s = g_slots[id - kTaggedId];
    menu_extra_apply(s.item, static_cast<unsigned>(s.index));
    return true;
}

} // namespace host
