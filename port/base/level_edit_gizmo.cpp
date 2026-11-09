// The level editor's picking and move gizmo (level_edit.cpp; level_edit_priv.hpp shares the state): the entity under
// the mouse (markers, then mesh triangles, then blocker wires), and the gizmo's arrows and planes: hit, drag, snap, end.
// Nearest existing: level_edit.cpp - split from it at 400 lines.
#include <SDL3/SDL.h> // the editor runs on SDL3 (also through level_edit_priv.hpp): built by the game build
#include "level_edit_priv.hpp"

namespace level_ed {

// ---- picking ----
bool ray_tri(V3 o, V3 d, V3 a, V3 b, V3 c, float *t)
{
    V3 e1 = b - a, e2 = c - a, p = cross(d, e2);
    float det = dot(e1, p);
    if (std::fabs(det) < 1e-12f) return false;
    float inv = 1.0f / det;
    V3 s = o - a;
    float u = dot(s, p) * inv;
    if (u < 0 || u > 1) return false;
    V3 q = cross(s, e1);
    float v = dot(d, q) * inv;
    if (v < 0 || u + v > 1) return false;
    *t = dot(e2, q) * inv;
    return *t > 0;
}
bool ray_box(V3 o, V3 d, V3 lo, V3 hi, float *t)
{
    float t0 = 0, t1 = 1e30f;
    const float *po = &o.x, *pd = &d.x, *pl = &lo.x, *ph = &hi.x;
    for (int i = 0; i < 3; i++) {
        if (std::fabs(pd[i]) < 1e-12f) {
            if (po[i] < pl[i] || po[i] > ph[i]) return false;
            continue;
        }
        float a = (pl[i] - po[i]) / pd[i], b = (ph[i] - po[i]) / pd[i];
        if (a > b) std::swap(a, b);
        t0 = std::fmax(t0, a), t1 = std::fmin(t1, b);
        if (t0 > t1) return false;
    }
    *t = t0;
    return true;
}

// the entity under a screen point: markers first (small, drawn on top), then meshes by their triangles, then blockers
int pick(float sx, float sy)
{
    V3 o, d;
    screen_ray(sx, sy, &o, &d);
    int best = -1;
    float bt = 1e30f;
    for (u32 i = 0; i < g_sc.nent; i++) {
        const LevelEnt &e = g_sc.ent[i];
        if (e.kind == LEVEL_MESH || e.kind == LEVEL_BLOCKER) continue;
        ImVec2 s;
        if (project(ent_world(e), &s) && std::hypot(s.x - sx, s.y - sy) < 14.0f) {
            float t = len(ent_world(e) - o) * 0.5f; // markers win over what is behind them
            if (t < bt) bt = t, best = static_cast<int>(i);
        }
        V3 lo, hi;
        float t;
        ent_box(e, &lo, &hi);
        if (ray_box(o, d, lo, hi, &t) && t < bt) bt = t, best = static_cast<int>(i);
    }
    if (best >= 0) return best;
    for (u32 i = 0; i < g_sc.nent; i++) {
        const LevelEnt &e = g_sc.ent[i];
        if (e.kind != LEVEL_MESH || e.mesh < 0 || static_cast<u32>(e.mesh) >= g_tri.size()) continue;
        V3 off = ent_world(e), ol = o - off;
        const std::vector<V3> &tr = g_tri[static_cast<u32>(e.mesh)];
        for (size_t k = 0; k + 2 < tr.size(); k += 3) {
            float t;
            if (ray_tri(ol, d, tr[k], tr[k + 1], tr[k + 2], &t) && t < bt) bt = t, best = static_cast<int>(i);
        }
    }
    // a blocking volume is picked by its wire, as in Unreal's wireframe view, when nothing solid is under the mouse
    float bd = 6.0f;
    for (u32 i = 0; best < 0 && i < g_sc.nent; i++) {
        const LevelEnt &e = g_sc.ent[i];
        float w = e.kind == LEVEL_BLOCKER && g_show_blockers ? wire_dist(e, sx, sy) : 1e30f;
        if (w < bd) bd = w, best = static_cast<int>(i);
    }
    return best;
}

// ---- the gizmo ----

float gizmo_len(V3 at) { return std::fmax(to_view(at).z, kNear * 4.0f) * 0.18f; }

float seg_dist(ImVec2 p, ImVec2 a, ImVec2 b)
{
    float dx = b.x - a.x, dy = b.y - a.y, l2 = dx * dx + dy * dy;
    float t = l2 > 0 ? ((p.x - a.x) * dx + (p.y - a.y) * dy) / l2 : 0;
    t = t < 0 ? 0 : t > 1 ? 1 : t;
    return std::hypot(p.x - (a.x + dx * t), p.y - (a.y + dy * t));
}

// which part of the gizmo is under the mouse, -1 none
int gizmo_hit(float mx, float my, V3 at, u32 axes)
{
    float l = gizmo_len(at);
    ImVec2 c, s;
    if (!project(at, &c)) return -1;
    ImVec2 m(mx, my);
    for (int p = 0; p < 3; p++) { // planes: a small square between two axes
        if ((axes >> kPlane[p][0] & 1) == 0 || (axes >> kPlane[p][1] & 1) == 0) continue;
        V3 a = kAxis[kPlane[p][0]] * (l * 0.3f), b = kAxis[kPlane[p][1]] * (l * 0.3f);
        ImVec2 q[4];
        if (!project(at + a * 0.35f + b * 0.35f, &q[0]) || !project(at + a + b * 0.35f, &q[1]) || !project(at + a + b, &q[2]) ||
            !project(at + a * 0.35f + b, &q[3]))
            continue;
        bool in = true;
        float sign = 0;
        for (int k = 0; k < 4; k++) {
            ImVec2 e1 = q[(k + 1) & 3], e0 = q[k];
            float cr = (e1.x - e0.x) * (m.y - e0.y) - (e1.y - e0.y) * (m.x - e0.x);
            if (sign == 0) sign = cr;
            else if (cr * sign < 0) in = false;
        }
        if (in) return 3 + p;
    }
    int best = -1;
    float bd = 9.0f;
    for (int a = 0; a < 3; a++) {
        if (!(axes >> a & 1) || !project(at + kAxis[a] * l, &s)) continue;
        float d = seg_dist(m, c, s);
        if (d < bd) bd = d, best = a;
    }
    return best;
}

// where the mouse ray meets the dragged axis or plane (world)
bool gizmo_point(int part, V3 at, float mx, float my, V3 *hit)
{
    V3 o, d;
    screen_ray(mx, my, &o, &d);
    if (part >= 3) {
        V3 n = cross(kAxis[kPlane[part - 3][0]], kAxis[kPlane[part - 3][1]]);
        float dn = dot(d, n);
        if (std::fabs(dn) < 1e-6f) return false;
        float t = dot(at - o, n) / dn;
        if (t <= 0) return false;
        *hit = o + d * t;
        return true;
    }
    // the point on the axis closest to the ray
    V3 a = kAxis[part], w = o - at;
    float b = dot(a, d), dd = dot(a, w), e = dot(d, w), den = 1 - b * b;
    if (den < 1e-6f) return false;
    float s = (dd - b * e) / den;
    *hit = at + a * s;
    return true;
}

void gizmo_begin(int part, float mx, float my)
{
    V3 at = selection_pivot();
    if (!gizmo_point(part, at, mx, my, &g_drag_start_hit)) return;
    g_drag = DRAG_GIZMO;
    g_gizmo_part = part;
    g_drag_ids = movable_selection();
    g_drag_from.clear();
    for (u32 id : g_drag_ids) g_drag_from.push_back({g_sc.ent[id].pos[0], g_sc.ent[id].pos[1], g_sc.ent[id].pos[2]});
}

void gizmo_drag(float mx, float my)
{
    V3 hit;
    if (g_drag_ids.empty() || !gizmo_point(g_gizmo_part, g_drag_start_hit, mx, my, &hit)) return;
    s32 dg[3];
    to_game_delta(hit - g_drag_start_hit, dg);
    bool use[3] = {false, false, false};
    if (g_gizmo_part < 3) use[g_gizmo_part] = true;
    else use[kPlane[g_gizmo_part - 3][0]] = use[kPlane[g_gizmo_part - 3][1]] = true;
    // the primary's new position is snapped to the grid on the dragged axes (Unreal snaps the location); the rest keep
    // their distance from it
    size_t lead = 0;
    for (size_t i = 0; i < g_drag_ids.size(); i++)
        if (static_cast<int>(g_drag_ids[i]) == g_primary) lead = i;
    for (int k = 0; k < 3; k++) {
        if (!use[k]) {
            dg[k] = 0;
            continue;
        }
        if (g_snap) {
            s32 step = snap_step(), want = g_drag_from[lead][k] + dg[k];
            s32 snapped = static_cast<s32>(std::llround(static_cast<double>(want) / step) * step);
            dg[k] = snapped - g_drag_from[lead][k];
        }
    }
    for (size_t i = 0; i < g_drag_ids.size(); i++) {
        const LevelEnt &e = g_sc.ent[g_drag_ids[i]];
        s32 p[3];
        for (int k = 0; k < 3; k++) p[k] = (e.axes >> k & 1) && dg[k] ? quantize(e, g_drag_from[i][k] + dg[k]) : g_drag_from[i][k];
        apply_pos(g_drag_ids[i], p);
    }
}

void gizmo_end()
{
    Move m;
    m.ids = g_drag_ids;
    m.from = g_drag_from;
    for (u32 id : g_drag_ids) m.to.push_back({g_sc.ent[id].pos[0], g_sc.ent[id].pos[1], g_sc.ent[id].pos[2]});
    commit(std::move(m));
    g_drag = DRAG_NONE;
    g_gizmo_part = -1;
}

} // namespace level_ed
