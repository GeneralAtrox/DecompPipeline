// What the level editor's files share (level_edit.cpp and level_edit_*.cpp): the includes, the small vector maths, the
// Outliner's kinds, the gizmo's axes, the editor's state (level_edit.cpp defines it) and the functions one file calls in
// another. Private to the editor: a console sees only level_edit.h.
// Nearest existing: host_internal.hpp - the host's own C++ interfaces, not the editor's; level_edit.h is the C API.
#pragma once

#include <SDL3/SDL.h>
#include <algorithm>
#include <array>
#include <cctype>
#include <cmath>
#include <cstdarg>
#include <cstdint>
#include <cstdlib>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>
#include <windows.h>
#include "third_party/imgui/imgui.h"
#include "third_party/imgui/imgui_impl_dx11.h"
#include "third_party/imgui/imgui_impl_sdl3.h"
#include "level_edit.h"
#include "present.h"
#include "host.h"
#include "game_log.h"
#pragma warning(push)
#pragma warning(disable : 4201)
#include <d3dcompiler.h>
#pragma warning(pop)

namespace level_ed {

// ---- small vector maths (the editor's world: the game's axes with +Y up, in editor units) ----
struct V3 {
    float x, y, z;
};
inline V3 operator+(V3 a, V3 b) { return {a.x + b.x, a.y + b.y, a.z + b.z}; }
inline V3 operator-(V3 a, V3 b) { return {a.x - b.x, a.y - b.y, a.z - b.z}; }
inline V3 operator*(V3 a, float k) { return {a.x * k, a.y * k, a.z * k}; }
inline float dot(V3 a, V3 b) { return a.x * b.x + a.y * b.y + a.z * b.z; }
inline V3 cross(V3 a, V3 b) { return {a.y * b.z - a.z * b.y, a.z * b.x - a.x * b.z, a.x * b.y - a.y * b.x}; }
inline float len(V3 a) { return std::sqrt(dot(a, a)); }
inline V3 norm(V3 a)
{
    float l = len(a);
    return l > 1e-12f ? a * (1.0f / l) : V3{0, 0, 0};
}

struct M4 {
    float m[4][4];
};
inline M4 mul(const M4 &a, const M4 &b)
{
    M4 r{};
    for (int i = 0; i < 4; i++)
        for (int j = 0; j < 4; j++)
            for (int k = 0; k < 4; k++) r.m[i][j] += a.m[i][k] * b.m[k][j];
    return r;
}

const char *const kKindFolder[LEVEL_KINDS] = {"Player starts", "Meshes", "Game objects", "Blocking volumes", "Lights"};
const ImU32 kKindColour[LEVEL_KINDS] = {IM_COL32(80, 220, 120, 255), IM_COL32(200, 200, 200, 255), IM_COL32(240, 200, 60, 255),
                                        IM_COL32(230, 120, 255, 255), IM_COL32(255, 230, 120, 255)};
const ImU32 kSelect = IM_COL32(255, 160, 30, 255);
const char *const kKindLabel[LEVEL_KINDS] = {"Player start", "Static mesh", "Game object", "Blocking volume", "Light"};
const u32 kKindOrder[LEVEL_KINDS] = {LEVEL_LIGHT, LEVEL_MESH, LEVEL_OBJECT, LEVEL_SPAWN, LEVEL_BLOCKER}; // the Outliner's groups

// the camera's near plane, the gizmo's axes and planes, the panels' window flags
const float kNear = 0.05f;
const V3 kAxis[3] = {{1, 0, 0}, {0, 1, 0}, {0, 0, 1}};
const ImU32 kAxisCol[3] = {IM_COL32(230, 60, 60, 255), IM_COL32(90, 210, 70, 255), IM_COL32(70, 120, 240, 255)};
const int kPlane[3][2] = {{0, 1}, {0, 2}, {1, 2}}; // XY XZ YZ
const ImGuiWindowFlags kPanel = ImGuiWindowFlags_NoMove | ImGuiWindowFlags_NoResize | ImGuiWindowFlags_NoCollapse |
                                ImGuiWindowFlags_NoBringToFrontOnFocus | ImGuiWindowFlags_NoSavedSettings;

// ---- the editor's state (level_edit.cpp defines it) ----
extern const LevelEdProvider *g_prov;
extern volatile LONG g_want;
extern LevelScene g_sc;
extern SDL_Window *g_win;
extern void (*g_host_event)(const void *);
extern bool g_open;

struct Cam {
    V3 pos;
    float yaw, pitch, speed;
};
extern Cam g_cam;
extern V3 g_pivot;
extern float g_scene_r; // the level's radius, editor units: the camera's speeds follow it

extern std::vector<unsigned char> g_sel;
extern int g_primary;
extern int g_hover_ent;

struct Move {
    std::vector<u32> ids;
    std::vector<std::array<s32, 3>> from, to;
    int added = -1; // an entity placed (its index, the last): undo takes it away
};
extern std::vector<Move> g_undo, g_redo;
// g_undo's size when the level was last saved or opened; SIZE_MAX: undo cannot reach it any more
extern size_t g_saved_depth;
extern bool g_snap, g_show_col, g_show_blockers, g_show_grid;
enum Tool { TOOL_SELECT, TOOL_MOVE };
extern Tool g_tool;
extern float g_ui; // the display's scale
extern int g_snap_i;
// tests (--level-edit-*): open at a vertical blank, select one entity, write the picture and close
extern u32 g_at;
extern std::string g_select_name, g_shot_path;
extern int g_frames, g_play_at, g_title_dirty;
extern bool g_test_place;
extern std::string g_status;
extern double g_status_t;
extern bool g_ask_close, g_after_save_close;
extern bool g_play_now;

// the viewport's rectangle in pixels and its camera
extern float g_vx, g_vy, g_vw, g_vh;
extern M4 g_view, g_proj, g_vp;
extern float g_xs, g_ys;
extern V3 g_r, g_u, g_f;

// mouse modes
enum Drag { DRAG_NONE, DRAG_FLY, DRAG_ORBIT, DRAG_DOLLY, DRAG_PAN, DRAG_GIZMO, DRAG_CLICK };
extern Drag g_drag;
extern float g_click_x, g_click_y;
extern bool g_click_ctrl;
extern int g_gizmo_hover, g_gizmo_part; // 0..2 axes X Y Z, 3..5 planes XY XZ YZ
extern V3 g_drag_start_hit;
extern std::vector<std::array<s32, 3>> g_drag_from;
extern std::vector<u32> g_drag_ids;
extern std::vector<std::vector<V3>> g_tri; // each mesh's triangles in editor axes, for picking (level_edit_gpu.cpp)

// level_edit_view.cpp: the game's numbers in the editor's world, the status line, the camera, the light, viewport input
V3 to_world(const s32 p[3]);
V3 to_world_f(float x, float y, float z);
void to_game_delta(V3 d, s32 out[3]);
void status(const char *fmt, ...);
void cam_basis();
void cam_matrices();
V3 to_view(V3 w);
ImVec2 view_to_screen(V3 v);
bool project(V3 w, ImVec2 *s);
void line3(ImDrawList *dl, V3 a, V3 b, ImU32 col, float th = 1.0f);
void screen_ray(float sx, float sy, V3 *o, V3 *d);
void shown(const LevelEnt &e, s32 out[3]);
V3 ent_world(const LevelEnt &e);
void ent_box(const LevelEnt &e, V3 *lo, V3 *hi);
void frame_on(V3 lo, V3 hi);
void frame_selection();
void light_dir_of(const s32 handle[3], float dir[3]);
V3 light_world_dir();
bool in_viewport(float x, float y);
void viewport_event(const SDL_Event &e, bool ui_mouse, bool ui_keys);
void fly_keys(float dt);
// level_edit_move.cpp: moving, undo and redo, saving, the selection, Add blocker
s32 snap_step();
s32 quantize(const LevelEnt &e, s32 v);
void apply_pos(u32 id, const s32 p[3]);
void commit(Move m);
void undo();
void redo();
bool dirty();
void save();
std::vector<u32> movable_selection();
V3 selection_pivot();
void select_only(int id);
void select_toggle(int id);
void place_blocker();
// level_edit_gizmo.cpp: picking and the gizmo
bool ray_tri(V3 o, V3 d, V3 a, V3 b, V3 c, float *t);
bool ray_box(V3 o, V3 d, V3 lo, V3 hi, float *t);
int pick(float sx, float sy);
float gizmo_len(V3 at);
float seg_dist(ImVec2 p, ImVec2 a, ImVec2 b);
int gizmo_hit(float mx, float my, V3 at, u32 axes);
bool gizmo_point(int part, V3 at, float mx, float my, V3 *hit);
void gizmo_begin(int part, float mx, float my);
void gizmo_drag(float mx, float my);
void gizmo_end();
// level_edit_overlay.cpp: the wires and the viewport's overlay
float world_y(s32 gy);
float wire_dist(const LevelEnt &e, float sx, float sy);
void box_lines(ImDrawList *dl, V3 lo, V3 hi, ImU32 col, float th);
void draw_overlay(ImDrawList *dl);
// level_edit_gpu.cpp: the viewport's meshes and the picture
bool gpu_open();
void gpu_close();
bool gpu_depth(u32 w, u32 h);
void gpu_draw(ID3D11RenderTargetView *rtv);
void shot(ID3D11RenderTargetView *rtv, const char *path);
// level_edit_bars.cpp: the style, the menu bar, the toolbar, the pills, the status bar, the close prompt
void style_unreal();
bool tool_button(const char *label, bool on, const char *tip);
void hover_tip(const char *t);
void bar_sep();
void menubar();
void toolbar(float y, float w, float h);
void pills();
void status_bar(float y, float w, float h);
void close_prompt();
// level_edit_panels.cpp: the Outliner and Details
void icon(ImDrawList *dl, ImVec2 c, u32 kind, ImU32 col);
bool has_text(const char *s, const char *f);
void outliner(float x, float y, float w, float h);
const char *collision_text(const LevelEnt &e);
void kv_row(const char *k, const char *v);
void details(float x, float y, float w, float h);
// level_edit.cpp: one frame, opening and closing
void frame(float dt);
bool begin();
void end();

} // namespace level_ed
