// The level editor's edits (level_edit.cpp; level_edit_priv.hpp shares the state): moving to a position, undo and redo,
// saving through the console's provider, the selection, and Add blocker (the console's template placed where the
// middle of the view meets the floor).
// Nearest existing: level_edit.cpp - split from it at 400 lines.
#include <SDL3/SDL.h> // the editor runs on SDL3 (also through level_edit_priv.hpp): built by the game build
#include "level_edit_priv.hpp"

namespace level_ed {

// ---- moving ----
s32 snap_step() { return g_sc.grid[g_snap_i] > 0 ? g_sc.grid[g_snap_i] : 1; }
s32 quantize(const LevelEnt &e, s32 v)
{
    return e.quantum > 1 ? static_cast<s32>(std::llround(static_cast<double>(v) / e.quantum) * e.quantum) : v;
}

void apply_pos(u32 id, const s32 p[3])
{
    LevelEnt &e = g_sc.ent[id];
    for (int k = 0; k < 3; k++) e.pos[k] = p[k];
    if (e.kind == LEVEL_LIGHT) light_dir_of(e.pos, e.dir);
}

void commit(Move m)
{
    bool changed = m.added >= 0;
    for (size_t i = 0; i < m.ids.size(); i++) changed |= m.from[i] != m.to[i];
    if (!changed) return;
    if (g_saved_depth > g_undo.size()) g_saved_depth = SIZE_MAX; // the saved state is no longer reachable by undo
    g_undo.push_back(std::move(m));
    g_redo.clear();
}

void undo()
{
    if (g_undo.empty()) return status("Nothing to undo");
    Move m = std::move(g_undo.back());
    g_undo.pop_back();
    if (m.added >= 0) {
        g_sel[static_cast<u32>(m.added)] = 0;
        if (g_primary == m.added) g_primary = -1;
        g_sc.nent = static_cast<u32>(m.added); // the last placed: it stays in the array for redo
        status("Undo: %s taken away", g_sc.ent[m.added].name);
    } else {
        for (size_t i = 0; i < m.ids.size(); i++) apply_pos(m.ids[i], m.from[i].data());
        status("Undo: moved %u object(s) back", static_cast<unsigned>(m.ids.size()));
    }
    g_redo.push_back(std::move(m));
}

void redo()
{
    if (g_redo.empty()) return status("Nothing to redo");
    Move m = std::move(g_redo.back());
    g_redo.pop_back();
    if (m.added >= 0) {
        g_sc.nent = static_cast<u32>(m.added) + 1;
        status("Redo: %s placed", g_sc.ent[m.added].name);
    } else {
        for (size_t i = 0; i < m.ids.size(); i++) apply_pos(m.ids[i], m.to[i].data());
        status("Redo: moved %u object(s)", static_cast<unsigned>(m.ids.size()));
    }
    g_undo.push_back(std::move(m));
}

bool dirty() { return g_undo.size() != g_saved_depth; }

void save()
{
    char msg[400] = "";
    if (g_prov->save(&g_sc, msg, sizeof msg)) {
        g_saved_depth = g_undo.size();
        status("Saved: %s", msg);
    } else {
        status("Not saved: %s", msg);
    }
}

std::vector<u32> movable_selection()
{
    std::vector<u32> ids;
    for (u32 i = 0; i < g_sc.nent; i++)
        if (g_sel[i] && g_sc.ent[i].axes) ids.push_back(i);
    return ids;
}

V3 selection_pivot()
{
    if (g_primary >= 0 && g_sel[static_cast<u32>(g_primary)]) return ent_world(g_sc.ent[g_primary]);
    V3 c{0, 0, 0};
    int n = 0;
    for (u32 i = 0; i < g_sc.nent; i++)
        if (g_sel[i]) c = c + ent_world(g_sc.ent[i]), n++;
    return n ? c * (1.0f / static_cast<float>(n)) : c;
}

// ---- selection ----
void select_only(int id)
{
    std::fill(g_sel.begin(), g_sel.end(), static_cast<unsigned char>(0));
    g_primary = id;
    if (id >= 0) g_sel[static_cast<u32>(id)] = 1;
}
void select_toggle(int id)
{
    if (id < 0) return;
    g_sel[static_cast<u32>(id)] ^= 1;
    g_primary = g_sel[static_cast<u32>(id)] ? id : -1;
    if (g_primary < 0)
        for (u32 i = 0; i < g_sc.nent; i++)
            if (g_sel[i]) g_primary = static_cast<int>(i);
}

// ---- placing (Add blocker): a copy of the console's template where the middle of the view meets its floor ----
void place_blocker()
{
    if (!g_sc.can_place) return status("This level takes no placed objects");
    if (g_sc.nent >= g_sc.cap) return status("No room for another %s (%u objects at most)", g_sc.place_tmpl.name, g_sc.cap);
    LevelEnt e = g_sc.place_tmpl;
    V3 o, d;
    screen_ray(g_vx + g_vw * 0.5f, g_vy + g_vh * 0.5f, &o, &d);
    float fy = static_cast<float>(e.add[1] + e.pos[1]) / g_sc.unit * (g_sc.y_down ? -1.0f : 1.0f), t = -1.0f;
    if (std::fabs(d.y) > 1e-4f) t = (fy - o.y) / d.y;
    V3 w = t > 0 && t < g_scene_r * 4.0f ? o + d * t : g_cam.pos + g_f * (g_scene_r * 0.5f); // looking up: ahead of the camera
    s32 g[3] = {static_cast<s32>(std::lround(w.x * g_sc.unit)), e.pos[1], static_cast<s32>(std::lround(w.z * g_sc.unit))};
    for (int k = 0; k < 3; k += 2) {
        if (g_snap) g[k] = static_cast<s32>(std::llround(static_cast<double>(g[k]) / snap_step()) * snap_step());
        g[k] = quantize(e, g[k]);
    }
    char id[32];
    for (int n = 1;; n++) {
        snprintf(id, sizeof id, "%s-%02d", g_sc.place_tmpl.id, n);
        bool used = false;
        for (u32 i = 0; i < g_sc.nent; i++) used |= std::strcmp(g_sc.ent[i].id, id) == 0;
        if (!used) {
            snprintf(e.name, sizeof e.name, "%s %02d", g_sc.place_tmpl.name, n);
            break;
        }
    }
    snprintf(e.id, sizeof e.id, "%s", id);
    for (int k = 0; k < 3; k++) e.pos[k] = e.orig[k] = g[k];
    u32 at = g_sc.nent;
    g_sc.ent[g_sc.nent++] = e;
    Move m;
    m.added = static_cast<int>(at);
    commit(std::move(m));
    select_only(static_cast<int>(at));
    status("Placed %s: an invisible box the robots cannot pass; move it like any other object", e.name);
}

} // namespace level_ed
