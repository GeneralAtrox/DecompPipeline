// The level editor's viewport overlay (level_edit.cpp; level_edit_priv.hpp shares the state): the wires of blocking
// volumes and collision shapes, and the lines drawn over the meshes through ImGui's draw list: the grid, the volumes,
// the markers, the light's beam, the gizmo, the controls along the bottom and the last message.
// Nearest existing: level_edit.cpp - split from it at 400 lines.
#include <SDL3/SDL.h> // the editor runs on SDL3 (also through level_edit_priv.hpp): built by the game build
#include "level_edit_priv.hpp"

namespace level_ed {

// ---- wires: a blocking volume, a collision shape ----
using Seg = std::array<V3, 2>;
float world_y(s32 gy) { return static_cast<float>(gy) / g_sc.unit * (g_sc.y_down ? -1.0f : 1.0f); }
void ent_wire(const LevelEnt &e, std::vector<Seg> &out)
{
    s32 p[3];
    shown(e, p);
    V3 c = to_world(p);
    float y0 = world_y(p[1] + e.lo[1]), y1 = world_y(p[1] + e.hi[1]), r = static_cast<float>(e.radius) / g_sc.unit;
    std::vector<V3> ring;
    if (e.shape == LEVEL_SHAPE_CYLINDER) {
        for (int i = 0; i < 16; i++) {
            float a = static_cast<float>(i) * 0.3926991f;
            ring.push_back(V3{c.x + r * std::cos(a), 0, c.z + r * std::sin(a)});
        }
    } else if (e.shape == LEVEL_SHAPE_OCTAGON) {
        float k = r * 1.41421356f - r; // where |x| + |z| = r * sqrt 2 cuts the square's corners
        const float pts[8][2] = {{r, -k}, {r, k}, {k, r}, {-k, r}, {-r, k}, {-r, -k}, {-k, -r}, {k, -r}};
        for (const auto &q : pts) ring.push_back(V3{c.x + q[0], 0, c.z + q[1]});
    } else if (e.shape == LEVEL_SHAPE_BOX) {
        V3 lo, hi;
        ent_box(e, &lo, &hi);
        V3 q[8];
        for (int i = 0; i < 8; i++) q[i] = {i & 1 ? hi.x : lo.x, i & 2 ? hi.y : lo.y, i & 4 ? hi.z : lo.z};
        const int ed[12][2] = {{0, 1}, {2, 3}, {4, 5}, {6, 7}, {0, 2}, {1, 3}, {4, 6}, {5, 7}, {0, 4}, {1, 5}, {2, 6}, {3, 7}};
        for (const auto &k2 : ed) out.push_back({q[k2[0]], q[k2[1]]});
        return;
    }
    size_t n = ring.size();
    for (size_t i = 0; i < n; i++) {
        V3 a = ring[i], b = ring[(i + 1) % n];
        out.push_back({V3{a.x, y0, a.z}, V3{b.x, y0, b.z}});
        out.push_back({V3{a.x, y1, a.z}, V3{b.x, y1, b.z}});
        if (n <= 8 || i % 4 == 0) out.push_back({V3{a.x, y0, a.z}, V3{a.x, y1, a.z}});
    }
}
// how near (pixels) a screen point is to an entity's wire; 1e30 when none of it is in front of the camera
float wire_dist(const LevelEnt &e, float sx, float sy)
{
    std::vector<Seg> w;
    ent_wire(e, w);
    float best = 1e30f;
    for (const Seg &s : w) {
        ImVec2 a, b;
        if (project(s[0], &a) && project(s[1], &b)) best = std::fmin(best, seg_dist(ImVec2(sx, sy), a, b));
    }
    return best;
}

// ---- the viewport's overlay: grid, volumes, markers, light, gizmo ----
void box_lines(ImDrawList *dl, V3 lo, V3 hi, ImU32 col, float th)
{
    V3 c[8];
    for (int i = 0; i < 8; i++) c[i] = {i & 1 ? hi.x : lo.x, i & 2 ? hi.y : lo.y, i & 4 ? hi.z : lo.z};
    const int e[12][2] = {{0, 1}, {2, 3}, {4, 5}, {6, 7}, {0, 2}, {1, 3}, {4, 6}, {5, 7}, {0, 4}, {1, 5}, {2, 6}, {3, 7}};
    for (const auto &k : e) line3(dl, c[k[0]], c[k[1]], col, th);
}

void draw_overlay(ImDrawList *dl)
{
    dl->PushClipRect(ImVec2(g_vx, g_vy), ImVec2(g_vx + g_vw, g_vy + g_vh), true);
    // the grid on the floor (where a placed object stands; game Y 0 when the level places none), at the snap step,
    // around the point the view looks at on it (under the camera when it looks up)
    if (g_show_grid) {
        float step = static_cast<float>(snap_step()) / g_sc.unit, gy = 0.0f;
        while (step * 60.0f < g_scene_r) step *= 2.0f;
        if (g_sc.can_place) gy = static_cast<float>(g_sc.place_tmpl.add[1] + g_sc.place_tmpl.pos[1]) / g_sc.unit * (g_sc.y_down ? -1.0f : 1.0f);
        V3 at = g_cam.pos;
        if (g_f.y < -1e-3f) {
            float t = (gy - g_cam.pos.y) / g_f.y;
            if (t > 0 && t < g_scene_r * 4.0f) at = g_cam.pos + g_f * t;
        }
        float cx = std::round(at.x / step) * step, cz = std::round(at.z / step) * step, half = step * 40.0f;
        for (int i = -40; i <= 40; i++) {
            float o = static_cast<float>(i) * step;
            ImU32 col = i == 0 ? IM_COL32(90, 90, 90, 160) : IM_COL32(70, 70, 70, 90);
            line3(dl, V3{cx + o, gy, cz - half}, V3{cx + o, gy, cz + half}, col);
            line3(dl, V3{cx - half, gy, cz + o}, V3{cx + half, gy, cz + o}, col);
        }
    }
    for (u32 i = 0; i < g_sc.nent; i++) {
        const LevelEnt &e = g_sc.ent[i];
        bool sel = g_sel[i] != 0, hov = static_cast<int>(i) == g_hover_ent;
        ImU32 col = sel ? kSelect : kKindColour[e.kind % LEVEL_KINDS];
        V3 lo, hi, p = ent_world(e);
        ent_box(e, &lo, &hi);
        ImVec2 s;
        switch (e.kind) {
        case LEVEL_BLOCKER: {
            if (!g_show_blockers) break;
            std::vector<Seg> w;
            ent_wire(e, w);
            for (const Seg &s2 : w) line3(dl, s2[0], s2[1], col, sel || hov ? 2.5f : 1.5f);
            if (e.shape == LEVEL_SHAPE_NONE) box_lines(dl, lo, hi, col, sel || hov ? 2.5f : 1.5f);
            break;
        }
        case LEVEL_MESH:
            if (sel || hov) box_lines(dl, lo, hi, sel ? kSelect : IM_COL32(255, 255, 255, 120), 1.0f);
            break;
        case LEVEL_LIGHT: {
            V3 t = to_world(g_sc.light_target);
            for (int k = 0; k < 24; k += 2) // the beam, dashed, to the point it aims at
                line3(dl, p + (t - p) * (static_cast<float>(k) / 24.0f), p + (t - p) * (static_cast<float>(k + 1) / 24.0f),
                      IM_COL32(255, 230, 120, 200), 2.0f);
            V3 d = norm(t - p);
            float l = gizmo_len(p) * 0.6f;
            line3(dl, p, p + d * l, col, 3.0f);
            if (project(p, &s)) {
                dl->AddCircleFilled(s, 9.0f, col);
                for (int k = 0; k < 8; k++) {
                    float a = static_cast<float>(k) * 0.785398f;
                    dl->AddLine(ImVec2(s.x + std::cos(a) * 12, s.y + std::sin(a) * 12), ImVec2(s.x + std::cos(a) * 17, s.y + std::sin(a) * 17), col, 2.0f);
                }
                dl->AddText(ImVec2(s.x + 18, s.y - 8), col, e.name);
            }
            break;
        }
        default: // spawn points and game objects: their box and a marker with the name
            box_lines(dl, lo, hi, col, sel || hov ? 2.0f : 1.0f);
            if (project(p, &s)) {
                if (e.kind == LEVEL_SPAWN) {
                    dl->AddTriangleFilled(ImVec2(s.x, s.y - 10), ImVec2(s.x - 8, s.y + 6), ImVec2(s.x + 8, s.y + 6), col);
                } else {
                    dl->AddRectFilled(ImVec2(s.x - 6, s.y - 6), ImVec2(s.x + 6, s.y + 6), col);
                }
                dl->AddText(ImVec2(s.x + 10, s.y - 8), col, e.name);
            }
            break;
        }
    }
    // collision shapes that move with their models (toolbar: Collision), as Unreal's Show > Collision
    if (g_show_col)
        for (u32 i = 0; i < g_sc.nent; i++) {
            const LevelEnt &e = g_sc.ent[i];
            if (!e.collides || e.shape == LEVEL_SHAPE_NONE) continue;
            std::vector<Seg> w;
            ent_wire(e, w);
            ImU32 c = g_sel[i] ? IM_COL32(120, 230, 255, 230) : IM_COL32(90, 190, 255, 110);
            for (const Seg &s2 : w) line3(dl, s2[0], s2[1], c, 1.0f);
        }
    // the gizmo, on the selection
    std::vector<u32> mv = movable_selection();
    if (!mv.empty() && g_tool == TOOL_MOVE) {
        V3 at = selection_pivot();
        u32 axes = 0;
        for (u32 id : mv) axes |= g_sc.ent[id].axes;
        float l = gizmo_len(at);
        int lit = g_drag == DRAG_GIZMO ? g_gizmo_part : g_gizmo_hover;
        for (int p = 0; p < 3; p++) {
            if ((axes >> kPlane[p][0] & 1) == 0 || (axes >> kPlane[p][1] & 1) == 0) continue;
            V3 a = kAxis[kPlane[p][0]] * (l * 0.3f), b = kAxis[kPlane[p][1]] * (l * 0.3f);
            ImVec2 q[4];
            if (project(at + a * 0.35f + b * 0.35f, &q[0]) && project(at + a + b * 0.35f, &q[1]) && project(at + a + b, &q[2]) &&
                project(at + a * 0.35f + b, &q[3])) {
                ImU32 c = lit == 3 + p ? IM_COL32(255, 230, 60, 200) : (kAxisCol[3 - kPlane[p][0] - kPlane[p][1]] & 0x00ffffffu) | 0x70000000u;
                dl->AddQuadFilled(q[0], q[1], q[2], q[3], c);
            }
        }
        ImVec2 c0;
        if (project(at, &c0)) {
            for (int a = 0; a < 3; a++) {
                if (!(axes >> a & 1)) continue;
                ImVec2 tip;
                if (!project(at + kAxis[a] * l, &tip)) continue;
                ImU32 c = lit == a ? IM_COL32(255, 230, 60, 255) : kAxisCol[a];
                dl->AddLine(c0, tip, c, 3.0f);
                float dx = tip.x - c0.x, dy = tip.y - c0.y, dl2 = std::hypot(dx, dy);
                if (dl2 > 1) {
                    dx /= dl2, dy /= dl2;
                    dl->AddTriangleFilled(ImVec2(tip.x + dx * 12, tip.y + dy * 12), ImVec2(tip.x - dy * 6, tip.y + dx * 6),
                                          ImVec2(tip.x + dy * 6, tip.y - dx * 6), c);
                }
            }
            dl->AddCircleFilled(c0, 4.0f, IM_COL32(230, 230, 230, 255));
        }
    }
    // the controls along the viewport's bottom (as the preview), and the last message above them for a few seconds
    {
        const char *help = "Right mouse + WASD/QE fly \xc2\xb7 Alt+left drag orbit \xc2\xb7 Wheel zoom \xc2\xb7 F frame \xc2\xb7 W move \xc2\xb7 "
                           "Ctrl+click add to selection \xc2\xb7 Ctrl+Z undo";
        float th = ImGui::GetTextLineHeight();
        ImVec2 a(g_vx, g_vy + g_vh - th - 8.0f);
        dl->AddRectFilled(a, ImVec2(g_vx + g_vw, g_vy + g_vh), IM_COL32(0, 0, 0, 140));
        dl->AddText(ImVec2(a.x + 10.0f, a.y + 4.0f), IM_COL32(200, 200, 200, 255), help);
        if (!g_status.empty() && ImGui::GetTime() - g_status_t < 5.0) {
            ImVec2 sz = ImGui::CalcTextSize(g_status.c_str());
            ImVec2 b(g_vx + g_vw - sz.x - 22.0f, a.y - sz.y - 18.0f);
            dl->AddRectFilled(b, ImVec2(b.x + sz.x + 14.0f, b.y + sz.y + 10.0f), IM_COL32(30, 30, 30, 230), 4.0f);
            dl->AddText(ImVec2(b.x + 7.0f, b.y + 5.0f), IM_COL32(235, 235, 235, 255), g_status.c_str());
        }
    }
    dl->PopClipRect();
}

} // namespace level_ed
