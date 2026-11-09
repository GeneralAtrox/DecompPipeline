// The level editor's camera and viewport input (level_edit.cpp; level_edit_priv.hpp shares the state): the game's
// numbers in the editor's world and back, the status line, the camera's basis, matrices and projection, framing, the key
// light's direction, and the mouse and keys in the viewport (fly, orbit, dolly, pan, click to select, the shortcuts).
// Nearest existing: level_edit.cpp - split from it at 400 lines.
#include <SDL3/SDL.h> // the editor runs on SDL3 (also through level_edit_priv.hpp): built by the game build
#include "level_edit_priv.hpp"

namespace level_ed {

// ---- conversions between the game's numbers and the editor's world ----
V3 to_world(const s32 p[3])
{
    float k = 1.0f / g_sc.unit;
    return {static_cast<float>(p[0]) * k, static_cast<float>(p[1]) * (g_sc.y_down ? -k : k), static_cast<float>(p[2]) * k};
}
// a mesh vertex: LevelVert is already in game units (not the 16.16 of positions), so only Y's direction changes
V3 to_world_f(float x, float y, float z) { return {x, g_sc.y_down ? -y : y, z}; }
// a world delta in game units (rounded per axis)
void to_game_delta(V3 d, s32 out[3])
{
    double k = g_sc.unit;
    out[0] = static_cast<s32>(std::lround(d.x * k));
    out[1] = static_cast<s32>(std::lround(d.y * (g_sc.y_down ? -k : k)));
    out[2] = static_cast<s32>(std::lround(d.z * k));
}

void status(const char *fmt, ...)
{
    char buf[400];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(buf, sizeof buf, fmt, ap);
    va_end(ap);
    g_status = buf;
    g_status_t = ImGui::GetTime();
    game_log_line("level editor: %s", buf);
}

// ---- the camera ----
void cam_basis()
{
    float cp = std::cos(g_cam.pitch), sp = std::sin(g_cam.pitch);
    g_f = {cp * std::sin(g_cam.yaw), sp, cp * std::cos(g_cam.yaw)};
    g_r = norm(cross(V3{0, 1, 0}, g_f));
    g_u = cross(g_f, g_r);
}

void cam_matrices()
{
    cam_basis();
    V3 e = g_cam.pos;
    M4 v = {{{g_r.x, g_u.x, g_f.x, 0}, {g_r.y, g_u.y, g_f.y, 0}, {g_r.z, g_u.z, g_f.z, 0},
             {-dot(g_r, e), -dot(g_u, e), -dot(g_f, e), 1}}};
    float aspect = g_vh > 0 ? g_vw / g_vh : 1.0f;
    g_ys = 1.0f / std::tan(0.5f * 1.2f); // about 69 degrees of height (Unreal's 90 across a wide view)
    g_xs = g_ys / aspect;
    // reversed depth with no far plane (depth = near / distance, cleared to 0, nearer wins): a level thousands of units
    // across keeps its precision; the usual near-to-far depth rounded far meshes past 1 and clipped them away
    M4 p = {{{g_xs, 0, 0, 0}, {0, g_ys, 0, 0}, {0, 0, 0, 1}, {0, 0, kNear, 0}}};
    g_view = v;
    g_proj = p;
    g_vp = mul(v, p);
}

// a world point in view space (x right, y up, z ahead)
V3 to_view(V3 w)
{
    V3 d = w - g_cam.pos;
    return {dot(d, g_r), dot(d, g_u), dot(d, g_f)};
}
ImVec2 view_to_screen(V3 v)
{
    float x = v.x * g_xs / v.z, y = v.y * g_ys / v.z;
    return ImVec2(g_vx + (x + 1.0f) * 0.5f * g_vw, g_vy + (1.0f - y) * 0.5f * g_vh);
}
bool project(V3 w, ImVec2 *s)
{
    V3 v = to_view(w);
    if (v.z < kNear) return false;
    *s = view_to_screen(v);
    return true;
}
// a world line clipped at the near plane, onto the viewport's draw list
void line3(ImDrawList *dl, V3 a, V3 b, ImU32 col, float th)
{
    V3 va = to_view(a), vb = to_view(b);
    if (va.z < kNear && vb.z < kNear) return;
    if (va.z < kNear) va = va + (vb - va) * ((kNear - va.z) / (vb.z - va.z));
    if (vb.z < kNear) vb = vb + (va - vb) * ((kNear - vb.z) / (va.z - vb.z));
    dl->AddLine(view_to_screen(va), view_to_screen(vb), col, th);
}
// the ray under a screen point
void screen_ray(float sx, float sy, V3 *o, V3 *d)
{
    float x = (sx - g_vx) / g_vw * 2.0f - 1.0f, y = 1.0f - (sy - g_vy) / g_vh * 2.0f;
    *o = g_cam.pos;
    *d = norm(g_r * (x / g_xs) + g_u * (y / g_ys) + g_f);
}

void shown(const LevelEnt &e, s32 out[3])
{
    for (int k = 0; k < 3; k++) out[k] = e.pos[k] + e.add[k];
}
V3 ent_world(const LevelEnt &e)
{
    s32 p[3];
    shown(e, p);
    return to_world(p);
}
void ent_box(const LevelEnt &e, V3 *lo, V3 *hi)
{
    s32 a[3], b[3], p[3];
    shown(e, p);
    for (int i = 0; i < 3; i++) a[i] = p[i] + e.lo[i], b[i] = p[i] + e.hi[i];
    V3 pa = to_world(a), pb = to_world(b);
    *lo = {std::fmin(pa.x, pb.x), std::fmin(pa.y, pb.y), std::fmin(pa.z, pb.z)};
    *hi = {std::fmax(pa.x, pb.x), std::fmax(pa.y, pb.y), std::fmax(pa.z, pb.z)};
}

void frame_on(V3 lo, V3 hi)
{
    V3 c = (lo + hi) * 0.5f;
    float r = std::fmax(len(hi - lo) * 0.5f, g_scene_r * 0.02f);
    cam_basis();
    g_pivot = c;
    g_cam.pos = c - g_f * (r / std::tan(0.5f * 1.2f) * 1.3f);
}

void frame_selection()
{
    V3 lo{1e30f, 1e30f, 1e30f}, hi{-1e30f, -1e30f, -1e30f};
    bool any = false;
    for (u32 i = 0; i < g_sc.nent; i++) {
        if (!g_sel[i]) continue;
        V3 a, b;
        ent_box(g_sc.ent[i], &a, &b);
        lo = {std::fmin(lo.x, a.x), std::fmin(lo.y, a.y), std::fmin(lo.z, a.z)};
        hi = {std::fmax(hi.x, b.x), std::fmax(hi.y, b.y), std::fmax(hi.z, b.z)};
        any = true;
    }
    if (!any) {
        for (u32 i = 0; i < g_sc.nent; i++) {
            V3 a, b;
            ent_box(g_sc.ent[i], &a, &b);
            lo = {std::fmin(lo.x, a.x), std::fmin(lo.y, a.y), std::fmin(lo.z, a.z)};
            hi = {std::fmax(hi.x, b.x), std::fmax(hi.y, b.y), std::fmax(hi.z, b.z)};
        }
    }
    if (lo.x <= hi.x) frame_on(lo, hi);
}

// ---- the light ----
void light_dir_of(const s32 handle[3], float dir[3])
{
    V3 d = norm(to_world(g_sc.light_target) - to_world(handle)); // editor axes
    dir[0] = d.x, dir[1] = g_sc.y_down ? -d.y : d.y, dir[2] = d.z;
}
V3 light_world_dir()
{
    for (u32 i = 0; i < g_sc.nent; i++)
        if (g_sc.ent[i].kind == LEVEL_LIGHT) {
            const float *d = g_sc.ent[i].dir;
            return norm(V3{d[0], g_sc.y_down ? -d[1] : d[1], d[2]});
        }
    return norm(V3{-0.4f, -0.8f, 0.4f});
}

// ---- input in the viewport ----
bool in_viewport(float x, float y) { return x >= g_vx && y >= g_vy && x < g_vx + g_vw && y < g_vy + g_vh; }

void viewport_event(const SDL_Event &e, bool ui_mouse, bool ui_keys)
{
    ImGuiIO &io = ImGui::GetIO();
    bool alt = (SDL_GetModState() & SDL_KMOD_ALT) != 0, ctrl = (SDL_GetModState() & SDL_KMOD_CTRL) != 0;
    switch (e.type) {
    case SDL_EVENT_MOUSE_BUTTON_DOWN: {
        float x = e.button.x, y = e.button.y;
        if (ui_mouse || !in_viewport(x, y) || g_drag != DRAG_NONE) break;
        if (e.button.button == SDL_BUTTON_RIGHT) {
            g_drag = alt ? DRAG_DOLLY : DRAG_FLY;
            SDL_SetWindowRelativeMouseMode(g_win, true);
        } else if (e.button.button == SDL_BUTTON_MIDDLE) {
            g_drag = DRAG_PAN;
        } else if (e.button.button == SDL_BUTTON_LEFT) {
            if (alt) {
                g_drag = DRAG_ORBIT;
                g_pivot = movable_selection().empty() && g_primary < 0 ? g_cam.pos + g_f * (g_scene_r * 0.5f) : selection_pivot();
            } else if (g_gizmo_hover >= 0) {
                gizmo_begin(g_gizmo_hover, x, y);
            } else {
                g_drag = DRAG_CLICK;
                g_click_x = x, g_click_y = y, g_click_ctrl = ctrl;
            }
        }
        break;
    }
    case SDL_EVENT_MOUSE_BUTTON_UP:
        if (g_drag == DRAG_FLY || g_drag == DRAG_DOLLY) {
            if (e.button.button == SDL_BUTTON_RIGHT) g_drag = DRAG_NONE, SDL_SetWindowRelativeMouseMode(g_win, false);
        } else if (g_drag == DRAG_PAN && e.button.button == SDL_BUTTON_MIDDLE) {
            g_drag = DRAG_NONE;
        } else if (e.button.button == SDL_BUTTON_LEFT) {
            if (g_drag == DRAG_GIZMO) gizmo_end();
            else if (g_drag == DRAG_CLICK) {
                int id = pick(g_click_x, g_click_y);
                if (g_click_ctrl) select_toggle(id);
                else select_only(id);
            }
            if (g_drag == DRAG_ORBIT || g_drag == DRAG_CLICK) g_drag = DRAG_NONE;
        }
        break;
    case SDL_EVENT_MOUSE_MOTION: {
        float dx = e.motion.xrel, dy = e.motion.yrel, k = g_scene_r * 0.003f;
        if (g_drag == DRAG_FLY) {
            g_cam.yaw += dx * 0.004f;
            g_cam.pitch = std::fmin(std::fmax(g_cam.pitch - dy * 0.004f, -1.55f), 1.55f);
        } else if (g_drag == DRAG_DOLLY) {
            cam_basis();
            g_cam.pos = g_cam.pos + g_f * (-(dx + dy) * k);
        } else if (g_drag == DRAG_PAN) {
            cam_basis();
            g_cam.pos = g_cam.pos - g_r * (dx * k) + g_u * (dy * k);
        } else if (g_drag == DRAG_ORBIT) {
            float d = len(g_cam.pos - g_pivot);
            g_cam.yaw += dx * 0.006f;
            g_cam.pitch = std::fmin(std::fmax(g_cam.pitch - dy * 0.006f, -1.55f), 1.55f);
            cam_basis();
            g_cam.pos = g_pivot - g_f * d;
        } else if (g_drag == DRAG_GIZMO) {
            gizmo_drag(e.motion.x, e.motion.y);
        } else if (g_drag == DRAG_CLICK && std::hypot(e.motion.x - g_click_x, e.motion.y - g_click_y) > 4.0f) {
            g_drag = DRAG_NONE; // a drag on empty space does nothing (no box select yet)
        }
        break;
    }
    case SDL_EVENT_MOUSE_WHEEL:
        if (g_drag == DRAG_FLY) {
            g_cam.speed = std::fmin(std::fmax(g_cam.speed * (e.wheel.y > 0 ? 1.25f : 0.8f), g_scene_r * 0.02f), g_scene_r * 8.0f);
            status("Camera speed %.2f", static_cast<double>(g_cam.speed));
        } else if (!ui_mouse && in_viewport(io.MousePos.x, io.MousePos.y)) {
            cam_basis();
            g_cam.pos = g_cam.pos + g_f * (e.wheel.y * g_scene_r * 0.08f);
        }
        break;
    case SDL_EVENT_KEY_DOWN: {
        if (ui_keys || e.key.repeat) break;
        SDL_Keycode k = e.key.key;
        if (g_drag == DRAG_FLY) break; // W A S D Q E fly
        if (ctrl && k == SDLK_Z) undo();
        else if (ctrl && k == SDLK_Y) redo();
        else if (ctrl && k == SDLK_S) save();
        else if (k == SDLK_F) frame_selection();
        else if (k == SDLK_W) g_tool = TOOL_MOVE;
        else if (k == SDLK_Q) g_tool = TOOL_SELECT;
        else if (k == SDLK_E || k == SDLK_R) status("Rotate and scale are not in this version: move only");
        else if (k == SDLK_DELETE || k == SDLK_BACKSPACE) status("Deleting objects is not in this version: move only (Ctrl+Z takes back a blocker just placed)");
        else if (k == SDLK_ESCAPE) select_only(-1);
        break;
    }
    default: break;
    }
}

void fly_keys(float dt)
{
    if (g_drag != DRAG_FLY) return;
    const bool *ks = SDL_GetKeyboardState(nullptr);
    cam_basis();
    V3 m{0, 0, 0};
    if (ks[SDL_SCANCODE_W]) m = m + g_f;
    if (ks[SDL_SCANCODE_S]) m = m - g_f;
    if (ks[SDL_SCANCODE_D]) m = m + g_r;
    if (ks[SDL_SCANCODE_A]) m = m - g_r;
    if (ks[SDL_SCANCODE_E]) m = m + V3{0, 1, 0};
    if (ks[SDL_SCANCODE_Q]) m = m - V3{0, 1, 0};
    if (len(m) > 0) g_cam.pos = g_cam.pos + norm(m) * (g_cam.speed * dt);
}

} // namespace level_ed
