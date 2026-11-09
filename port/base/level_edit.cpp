// The port base's level editor (level_edit.h): Unreal Engine's level-editor conventions on a console's level.
//   Camera:  right mouse held + mouse looks, with W A S D to fly and Q E down and up (the wheel while held sets the
//            speed); Alt + left drag orbits, Alt + right drag or the wheel zooms, middle drag pans, F frames the selection.
//   Select:  Q selects only: click (in the viewport or the Outliner), Ctrl+click adds or removes, Esc selects none.
//   Move:    W: drag an arrow or a plane of the gizmo; Snap (toolbar) rounds the position to the grid size; Details takes
//            typed positions. Ctrl+Z / Ctrl+Y undo and redo, Ctrl+S saves. Rotate (E), scale (R) and delete are not in
//            this version. Add blocker places a copy of the console's template (LevelScene.place_tmpl) where the middle
//            of the view meets the floor; it then moves like any other object, and undo takes it away.
//   Layout:  as the user's approved preview: a menu bar (the level's tab, File, Edit, Window, Help), the toolbar, the
//            viewport (pills Perspective, Lit, Collision, Blockers, Grid; the controls along its bottom), the Outliner on
//            the right (search; Lights, Meshes, Game objects, Player starts, Blocking volumes) with Details below it, a
//            status bar.
// Split at 400 lines, sharing level_edit_priv.hpp: here the editor's state, one frame, opening, closing and the entry
// points; level_edit_view.cpp the camera and the viewport's input, level_edit_move.cpp moving, undo, save and Add
// blocker, level_edit_gizmo.cpp picking and the gizmo, level_edit_overlay.cpp the viewport's lines and markers,
// level_edit_gpu.cpp the mesh pipeline and the picture, level_edit_bars.cpp the bars, level_edit_panels.cpp the panels.
// Nearest existing: none in DecompPipeline (no editor, no UI toolkit); the menu bar (host_menu.cpp) is Win32. The UI is
// Dear ImGui (base/imgui, MIT), drawn with the base's Direct3D 11 device on the base's window; the viewport's meshes
// have their own small pipeline here, the lines and markers go through ImGui's draw lists.
#include <SDL3/SDL.h> // the editor runs on SDL3 (also through level_edit_priv.hpp): built by the game build
#include "level_edit_priv.hpp"

extern "C" void host_window_title_extra(const char *extra); // host_window.cpp: the title bar's " - extra"

namespace level_ed {

// ---- the editor's state ----
const LevelEdProvider *g_prov;
volatile LONG g_want;
LevelScene g_sc;
SDL_Window *g_win;
void (*g_host_event)(const void *);
bool g_open;

Cam g_cam;
V3 g_pivot;
float g_scene_r = 10.0f; // the level's radius, editor units: the camera's speeds follow it

std::vector<unsigned char> g_sel;
int g_primary = -1;
int g_hover_ent = -1;

std::vector<Move> g_undo, g_redo;
size_t g_saved_depth; // g_undo's size when the level was last saved or opened; SIZE_MAX: undo cannot reach it any more
bool g_snap = true, g_show_col = true, g_show_blockers = true, g_show_grid = true;
Tool g_tool = TOOL_MOVE;
float g_ui = 1.0f; // the display's scale
int g_snap_i = 1;
// tests (--level-edit-*): open at a vertical blank, select one entity, write the picture and close
u32 g_at = ~0u;
std::string g_select_name, g_shot_path;
int g_frames, g_play_at = -1, g_title_dirty = -1;
bool g_test_place;
std::string g_status;
double g_status_t;
bool g_ask_close, g_after_save_close;
bool g_play_now;

// the viewport's rectangle in pixels and its camera
float g_vx, g_vy, g_vw, g_vh;
M4 g_view, g_proj, g_vp;
float g_xs, g_ys;
V3 g_r, g_u, g_f;

// mouse modes
Drag g_drag;
float g_click_x, g_click_y;
bool g_click_ctrl;
int g_gizmo_hover = -1, g_gizmo_part = -1; // 0..2 axes X Y Z, 3..5 planes XY XZ YZ
V3 g_drag_start_hit;
std::vector<std::array<s32, 3>> g_drag_from;
std::vector<u32> g_drag_ids;

// ---- one frame ----
void frame(float dt)
{
    ImGuiIO &io = ImGui::GetIO();
    ImGui_ImplDX11_NewFrame();
    ImGui_ImplSDL3_NewFrame();
    ImGui::NewFrame();
    g_frames++;
    float W = io.DisplaySize.x, H = io.DisplaySize.y, mb = ImGui::GetFrameHeight(), bar = ImGui::GetFrameHeight() + 14.0f, top = mb + bar;
    float sb = ImGui::GetFrameHeight() + 8.0f, side = std::fmin(300.0f * g_ui, W * 0.45f);
    g_vx = 0, g_vy = top, g_vw = std::fmax(W - side, 16.0f), g_vh = std::fmax(H - top - sb, 16.0f);
    fly_keys(dt);
    cam_matrices();
    // tests: place a blocker, select by name, press Play
    if (g_test_place && g_frames == 2) place_blocker();
    if (g_frames == 3)
        for (u32 i = 0; i < g_sc.nent; i++)
            if (!g_select_name.empty() && g_select_name == g_sc.ent[i].name) select_only(static_cast<int>(i)), frame_selection();
    if (g_play_at > 0 && g_frames == g_play_at) g_play_now = true;
    if (g_title_dirty != static_cast<int>(dirty())) {
        g_title_dirty = static_cast<int>(dirty());
        char t[128];
        snprintf(t, sizeof t, "Level editor - %s%s", g_sc.title, g_title_dirty ? "*" : "");
        host_window_title_extra(t);
    }
    menubar();
    toolbar(mb, W, bar);
    outliner(g_vw, top, side, (H - top - sb) * 0.55f);
    details(g_vw, top + (H - top - sb) * 0.55f, side, (H - top - sb) * 0.45f);
    status_bar(H - sb, W, sb);
    pills();
    close_prompt();
    // hover: the gizmo first (Move), then what is under the mouse
    g_gizmo_hover = -1;
    g_hover_ent = -1;
    if (g_drag == DRAG_NONE && !io.WantCaptureMouse && in_viewport(io.MousePos.x, io.MousePos.y)) {
        std::vector<u32> mv = movable_selection();
        if (!mv.empty() && g_tool == TOOL_MOVE) {
            u32 axes = 0;
            for (u32 id : mv) axes |= g_sc.ent[id].axes;
            g_gizmo_hover = gizmo_hit(io.MousePos.x, io.MousePos.y, selection_pivot(), axes);
        }
        if (g_gizmo_hover < 0) g_hover_ent = pick(io.MousePos.x, io.MousePos.y);
    }
    draw_overlay(ImGui::GetBackgroundDrawList());
    ImGui::Render();
    ID3D11RenderTargetView *rtv = gfx_back_rtv();
    if (!rtv) return;
    float bg[4] = {0.05f, 0.05f, 0.06f, 1};
    g_gfx.ctx->ClearRenderTargetView(rtv, bg);
    gpu_draw(rtv);
    g_gfx.ctx->OMSetRenderTargets(1, &rtv, nullptr);
    ImGui_ImplDX11_RenderDrawData(ImGui::GetDrawData());
    if (!g_shot_path.empty() && g_frames == 20) {
        shot(rtv, g_shot_path.c_str());
        g_open = false;
    }
    if (FAILED(g_gfx.swap->Present(1, 0))) port_exit("the display device was lost", 4);
}

bool begin()
{
    std::memset(&g_sc, 0, sizeof g_sc);
    g_sc.unit = 65536.0f;
    if (!g_prov || !g_prov->open(&g_sc) || !g_sc.nent) {
        port_log("level editor: the console could not open its level\n");
        game_log_line("level editor: not opened (the console could not read its level)");
        return false;
    }
    if (!(g_sc.unit > 0)) g_sc.unit = 65536.0f;
    if (g_sc.grid[0] <= 0) g_sc.grid[0] = static_cast<s32>(g_sc.unit);
    g_sel.assign(std::max(g_sc.cap, g_sc.nent), 0);
    g_primary = -1;
    if (g_sc.snap >= 0 && g_sc.snap < 4 && g_sc.grid[g_sc.snap] > 0) g_snap_i = g_sc.snap;
    g_frames = 0;
    g_undo.clear(), g_redo.clear();
    g_saved_depth = 0;
    g_drag = DRAG_NONE;
    g_play_now = g_ask_close = false;
    for (u32 i = 0; i < g_sc.nent; i++)
        if (g_sc.ent[i].kind == LEVEL_LIGHT) light_dir_of(g_sc.ent[i].pos, g_sc.ent[i].dir);
    // the level's size sets the camera's speeds; the first view looks down on the whole level from the front, like a
    // new level in Unreal
    V3 lo{1e30f, 1e30f, 1e30f}, hi{-1e30f, -1e30f, -1e30f};
    for (u32 i = 0; i < g_sc.nent; i++) {
        V3 a, b;
        ent_box(g_sc.ent[i], &a, &b);
        lo = {std::fmin(lo.x, a.x), std::fmin(lo.y, a.y), std::fmin(lo.z, a.z)};
        hi = {std::fmax(hi.x, b.x), std::fmax(hi.y, b.y), std::fmax(hi.z, b.z)};
    }
    g_scene_r = std::fmax(len(hi - lo) * 0.5f, 1.0f);
    g_cam.yaw = 0.0f, g_cam.pitch = -0.6f, g_cam.speed = g_scene_r * 0.5f;
    frame_on(lo, hi);
    IMGUI_CHECKVERSION();
    ImGui::CreateContext();
    ImGuiIO &io = ImGui::GetIO();
    io.IniFilename = nullptr; // no imgui.ini: the layout is fixed, nothing is written
    io.LogFilename = nullptr;
    style_unreal();
    float scale = SDL_GetWindowDisplayScale(g_win);
    if (!(scale > 0.5f)) scale = 1.0f;
    ImGui::GetStyle().ScaleAllSizes(scale);
    g_ui = scale;
    if (!io.Fonts->AddFontFromFileTTF("C:\\Windows\\Fonts\\segoeui.ttf", 16.0f * scale)) {
        ImFontConfig cfg;
        cfg.SizePixels = 13.0f * scale;
        io.Fonts->AddFontDefault(&cfg);
    }
    if (!ImGui_ImplSDL3_InitForD3D(g_win) || !ImGui_ImplDX11_Init(g_gfx.dev, g_gfx.ctx) || !gpu_open()) {
        port_log("level editor: the UI could not start\n");
        return false;
    }
    g_title_dirty = -1, g_tool = TOOL_MOVE;
    if (!g_shot_path.empty()) // a test's picture: where each object is, its box and its drawn mesh, in editor units
        for (u32 i = 0; i < g_sc.nent; i++) {
            const LevelEnt &e = g_sc.ent[i];
            V3 o = ent_world(e), a, b, ml{0, 0, 0}, mh{0, 0, 0};
            ent_box(e, &a, &b);
            u32 nt = e.mesh >= 0 && static_cast<u32>(e.mesh) < g_tri.size() ? static_cast<u32>(g_tri[static_cast<u32>(e.mesh)].size()) : 0u;
            if (nt) {
                ml = mh = g_tri[static_cast<u32>(e.mesh)][0];
                for (const V3 &q : g_tri[static_cast<u32>(e.mesh)])
                    ml = {std::fmin(ml.x, q.x), std::fmin(ml.y, q.y), std::fmin(ml.z, q.z)}, mh = {std::fmax(mh.x, q.x), std::fmax(mh.y, q.y), std::fmax(mh.z, q.z)};
            }
            port_log("level editor: scene %s at %.0f %.0f %.0f box %.0f..%.0f %.0f..%.0f %.0f..%.0f mesh %d (%u verts) %.0f..%.0f %.0f..%.0f %.0f..%.0f\n",
                     e.name, o.x, o.y, o.z, a.x, b.x, a.y, b.y, a.z, b.z, e.mesh, nt, ml.x + o.x, mh.x + o.x, ml.y + o.y, mh.y + o.y, ml.z + o.z, mh.z + o.z);
        }
    status("Opened %s: %u objects", g_sc.title, g_sc.nent);
    return true;
}

void end()
{
    if (g_drag == DRAG_FLY || g_drag == DRAG_DOLLY) SDL_SetWindowRelativeMouseMode(g_win, false);
    gpu_close();
    if (ImGui::GetCurrentContext()) {
        ImGui_ImplDX11_Shutdown();
        ImGui_ImplSDL3_Shutdown();
        ImGui::DestroyContext();
    }
    host_window_title_extra(nullptr);
    if (g_prov) g_prov->close(&g_sc);
    g_sel.clear();
}

} // namespace level_ed

using namespace level_ed;

extern "C" {

void level_edit_provide(const LevelEdProvider *p) { g_prov = p; }
const LevelEdProvider *level_edit_provider(void) { return g_prov; }
void level_edit_request(void)
{
    if (g_prov) InterlockedExchange(&g_want, 1);
}
int level_edit_wanted(void)
{
    if (g_at != ~0u && g_prov && port_vblank_count() >= g_at) g_at = ~0u, InterlockedExchange(&g_want, 1);
    return g_want != 0;
}

int level_edit_option(const char *arg, const char *value)
{
    if (!value) return 0;
    if (!strcmp(arg, "--level-edit-at")) g_at = static_cast<u32>(strtoul(value, nullptr, 10));
    else if (!strcmp(arg, "--level-edit-select")) g_select_name = value;
    else if (!strcmp(arg, "--level-edit-shot")) g_shot_path = value;
    else if (!strcmp(arg, "--level-edit-play")) g_play_at = atoi(value);
    else if (!strcmp(arg, "--level-edit-place")) g_test_place = strcmp(value, "0") != 0;
    else return 0;
    return 2;
}


void level_edit_run(void *sdl_window, void (*host_event)(const void *))
{
    InterlockedExchange(&g_want, 0);
    if (!g_gfx.swap || g_open) return;
    g_win = static_cast<SDL_Window *>(sdl_window);
    g_host_event = host_event;
    game_log_line("level editor: opened (the game is paused)");
    g_open = begin();
    Uint64 last = SDL_GetTicksNS();
    while (g_open) {
        SDL_Event e;
        while (SDL_PollEvent(&e)) {
            ImGui_ImplSDL3_ProcessEvent(&e);
            ImGuiIO &io = ImGui::GetIO();
            bool input = e.type == SDL_EVENT_MOUSE_MOTION || e.type == SDL_EVENT_MOUSE_BUTTON_DOWN || e.type == SDL_EVENT_MOUSE_BUTTON_UP ||
                         e.type == SDL_EVENT_MOUSE_WHEEL || e.type == SDL_EVENT_KEY_DOWN || e.type == SDL_EVENT_KEY_UP || e.type == SDL_EVENT_TEXT_INPUT;
            if (input) viewport_event(e, io.WantCaptureMouse && g_drag == DRAG_NONE, io.WantTextInput);
            else g_host_event(&e); // closing, resizing, full screen
        }
        Uint64 now = SDL_GetTicksNS();
        float dt = static_cast<float>(static_cast<double>(now - last) * 1e-9);
        last = now;
        if (SDL_GetWindowFlags(g_win) & SDL_WINDOW_MINIMIZED) {
            SDL_WaitEventTimeout(nullptr, 100);
            continue;
        }
        frame(dt > 0.1f ? 0.1f : dt);
        if (g_play_now) {
            g_play_now = false;
            save();
            if (!dirty()) {
                game_log_line("level editor: Play");
                g_prov->play(&g_sc);
                g_open = false;
            }
        }
    }
    end();
    InterlockedExchange(&g_want, 0); // a menu choice made while it was open
    game_log_line("level editor: closed (the game goes on)");
}

} // extern "C"
