// The level editor's bars (level_edit.cpp; level_edit_priv.hpp shares the state): the dark style after Unreal Engine's,
// the menu bar (the level's tab, File, Edit, Window, Help), the toolbar, the viewport's pills, the status bar and the
// Save changes? prompt on closing.
// Nearest existing: level_edit.cpp - split from it at 400 lines.
#include <SDL3/SDL.h> // the editor runs on SDL3 (also through level_edit_priv.hpp): built by the game build
#include "level_edit_priv.hpp"

namespace level_ed {

// ---- the panels ----
void style_unreal()
{
    ImGuiStyle &s = ImGui::GetStyle();
    ImGui::StyleColorsDark(&s);
    s.WindowRounding = 0, s.FrameRounding = 3, s.GrabRounding = 3, s.TabRounding = 3;
    s.WindowBorderSize = 1, s.FrameBorderSize = 0, s.WindowPadding = ImVec2(8, 6), s.ItemSpacing = ImVec2(6, 5);
    ImVec4 *c = s.Colors;
    c[ImGuiCol_WindowBg] = ImVec4(0.14f, 0.14f, 0.14f, 1);
    c[ImGuiCol_ChildBg] = ImVec4(0.10f, 0.10f, 0.10f, 1);
    c[ImGuiCol_Border] = ImVec4(0.05f, 0.05f, 0.05f, 1);
    c[ImGuiCol_FrameBg] = ImVec4(0.08f, 0.08f, 0.08f, 1);
    c[ImGuiCol_FrameBgHovered] = ImVec4(0.20f, 0.20f, 0.20f, 1);
    c[ImGuiCol_FrameBgActive] = ImVec4(0.24f, 0.24f, 0.24f, 1);
    c[ImGuiCol_TitleBg] = c[ImGuiCol_TitleBgActive] = ImVec4(0.09f, 0.09f, 0.09f, 1);
    c[ImGuiCol_Button] = ImVec4(0.21f, 0.21f, 0.21f, 1);
    c[ImGuiCol_ButtonHovered] = ImVec4(0.30f, 0.30f, 0.30f, 1);
    c[ImGuiCol_ButtonActive] = ImVec4(0.0f, 0.44f, 0.88f, 1);
    c[ImGuiCol_Header] = ImVec4(0.0f, 0.44f, 0.88f, 0.55f);
    c[ImGuiCol_HeaderHovered] = ImVec4(0.26f, 0.26f, 0.26f, 1);
    c[ImGuiCol_HeaderActive] = ImVec4(0.0f, 0.44f, 0.88f, 1);
    c[ImGuiCol_CheckMark] = ImVec4(0.0f, 0.6f, 1.0f, 1);
    c[ImGuiCol_Text] = ImVec4(0.86f, 0.86f, 0.86f, 1);
}

bool tool_button(const char *label, bool on, const char *tip)
{
    if (on) ImGui::PushStyleColor(ImGuiCol_Button, ImVec4(0.0f, 0.44f, 0.88f, 1));
    bool hit = ImGui::Button(label);
    if (on) ImGui::PopStyleColor();
    if (tip && ImGui::IsItemHovered(ImGuiHoveredFlags_AllowWhenDisabled | ImGuiHoveredFlags_DelayShort)) ImGui::SetTooltip("%s", tip);
    return hit;
}
void hover_tip(const char *t)
{
    if (ImGui::IsItemHovered(ImGuiHoveredFlags_AllowWhenDisabled | ImGuiHoveredFlags_DelayShort)) ImGui::SetTooltip("%s", t);
}
void bar_sep()
{
    ImGui::SameLine();
    ImGui::TextDisabled("|");
    ImGui::SameLine();
}

// the menu bar: the editor's name, the level's tab (* when changed), File, Edit, Window, Help
void menubar()
{
    if (!ImGui::BeginMainMenuBar()) return;
    ImGui::TextColored(ImVec4(0.25f, 0.62f, 1.0f, 1), "Level Editor");
    char tab[96];
    snprintf(tab, sizeof tab, "%s%s", g_sc.title, dirty() ? "*" : "");
    ImGui::PushStyleColor(ImGuiCol_Button, ImVec4(0.22f, 0.22f, 0.22f, 1));
    ImGui::SmallButton(tab);
    ImGui::PopStyleColor();
    hover_tip(dirty() ? "This level has unsaved changes" : "This level, as saved");
    if (ImGui::BeginMenu("File")) {
        if (ImGui::MenuItem("Save", "Ctrl+S")) save();
        if (ImGui::MenuItem("Play")) g_play_now = true;
        ImGui::Separator();
        if (ImGui::MenuItem("Close editor")) g_ask_close = true;
        ImGui::EndMenu();
    }
    if (ImGui::BeginMenu("Edit")) {
        if (ImGui::MenuItem("Undo", "Ctrl+Z", false, !g_undo.empty())) undo();
        if (ImGui::MenuItem("Redo", "Ctrl+Y", false, !g_redo.empty())) redo();
        ImGui::Separator();
        if (ImGui::MenuItem("Add blocker", nullptr, false, g_sc.can_place != 0)) place_blocker();
        ImGui::EndMenu();
    }
    if (ImGui::BeginMenu("Window")) {
        ImGui::MenuItem("Collision", nullptr, &g_show_col);
        ImGui::MenuItem("Blockers", nullptr, &g_show_blockers);
        ImGui::MenuItem("Grid", nullptr, &g_show_grid);
        ImGui::Separator();
        if (ImGui::MenuItem("Frame selection", "F")) frame_selection();
        ImGui::EndMenu();
    }
    if (ImGui::BeginMenu("Help")) {
        ImGui::TextDisabled("Right mouse + WASD/QE: fly (wheel while held: speed)");
        ImGui::TextDisabled("Alt + left drag: orbit    Wheel: zoom    Middle drag: pan");
        ImGui::TextDisabled("F: frame the selection    Q: select    W: move");
        ImGui::TextDisabled("Click: select    Ctrl+click: add to the selection    Esc: none");
        ImGui::TextDisabled("Ctrl+Z / Ctrl+Y: undo, redo    Ctrl+S: save");
        ImGui::TextDisabled("Rotate, scale and delete are not in this version: move only");
        ImGui::EndMenu();
    }
    ImGui::EndMainMenuBar();
}

void toolbar(float y, float w, float h)
{
    ImGui::SetNextWindowPos(ImVec2(0, y));
    ImGui::SetNextWindowSize(ImVec2(w, h));
    ImGui::Begin("##toolbar", nullptr, kPanel | ImGuiWindowFlags_NoTitleBar | ImGuiWindowFlags_NoScrollbar);
    if (ImGui::Button("Save")) save();
    hover_tip("Save the level (Ctrl+S): kept apart from the game's data and save");
    ImGui::SameLine();
    ImGui::BeginDisabled(g_undo.empty());
    if (ImGui::Button("Undo")) undo();
    ImGui::EndDisabled();
    hover_tip("Undo (Ctrl+Z)");
    ImGui::SameLine();
    ImGui::BeginDisabled(g_redo.empty());
    if (ImGui::Button("Redo")) redo();
    ImGui::EndDisabled();
    hover_tip("Redo (Ctrl+Y)");
    bar_sep();
    if (tool_button("Select (Q)", g_tool == TOOL_SELECT, "Select: click, Ctrl+click adds")) g_tool = TOOL_SELECT;
    ImGui::SameLine();
    if (tool_button("Move (W)", g_tool == TOOL_MOVE, "Move: drag an arrow or a plane of the gizmo")) g_tool = TOOL_MOVE;
    ImGui::SameLine();
    ImGui::BeginDisabled();
    ImGui::Button("Rotate (E)");
    ImGui::EndDisabled();
    hover_tip("Not in this version: move only");
    ImGui::SameLine();
    ImGui::BeginDisabled();
    ImGui::Button("Scale (R)");
    ImGui::EndDisabled();
    hover_tip("Not in this version: move only");
    bar_sep();
    if (tool_button("Snap", g_snap, "Snap moves to the grid size")) g_snap = !g_snap;
    ImGui::SameLine();
    char gl[32];
    snprintf(gl, sizeof gl, "Grid %g", static_cast<double>(snap_step()) / g_sc.unit);
    if (ImGui::Button(gl))
        for (int k = 1; k <= 4; k++)
            if (g_sc.grid[(g_snap_i + k) % 4] > 0) {
                g_snap_i = (g_snap_i + k) % 4;
                break;
            }
    hover_tip("Grid size, game units: click for the next");
    bar_sep();
    ImGui::BeginDisabled(!g_sc.can_place || g_sc.nent >= g_sc.cap);
    if (ImGui::Button("Add blocker")) place_blocker();
    ImGui::EndDisabled();
    hover_tip("Place an invisible box the robots cannot pass, where the view meets the floor; then move it like any other object");
    bar_sep();
    ImGui::PushStyleColor(ImGuiCol_Button, ImVec4(0.15f, 0.50f, 0.15f, 1));
    ImGui::PushStyleColor(ImGuiCol_ButtonHovered, ImVec4(0.20f, 0.62f, 0.20f, 1));
    if (ImGui::Button("  Play  ")) g_play_now = true;
    ImGui::PopStyleColor(2);
    hover_tip("Save, then fight in this arena with the edits");
    ImGui::End();
}

// the viewport's pills, top left: the view (Perspective, Lit) and what is shown (Collision, Blockers, Grid)
void pills()
{
    ImGui::SetNextWindowPos(ImVec2(g_vx + 8.0f * g_ui, g_vy + 8.0f * g_ui));
    ImGui::SetNextWindowBgAlpha(0.0f);
    ImGui::PushStyleVar(ImGuiStyleVar_WindowBorderSize, 0.0f);
    ImGui::PushStyleVar(ImGuiStyleVar_WindowPadding, ImVec2(0, 0));
    ImGui::PushStyleVar(ImGuiStyleVar_FrameRounding, 10.0f * g_ui);
    ImGui::Begin("##pills", nullptr, ImGuiWindowFlags_NoDecoration | ImGuiWindowFlags_AlwaysAutoResize | ImGuiWindowFlags_NoMove |
                                         ImGuiWindowFlags_NoSavedSettings | ImGuiWindowFlags_NoFocusOnAppearing | ImGuiWindowFlags_NoNav);
    ImGui::PushStyleColor(ImGuiCol_Button, ImVec4(0.10f, 0.10f, 0.10f, 0.85f));
    ImGui::Button("Perspective");
    hover_tip("The only view in this version");
    ImGui::SameLine();
    ImGui::Button("Lit");
    hover_tip("Lit by the level's key light");
    ImGui::PopStyleColor();
    ImGui::SameLine();
    if (tool_button("Collision", g_show_col, "Each model's collision: it moves with the model")) g_show_col = !g_show_col;
    ImGui::SameLine();
    if (tool_button("Blockers", g_show_blockers, "The blocking volumes, invisible in the game")) g_show_blockers = !g_show_blockers;
    ImGui::SameLine();
    if (tool_button("Grid", g_show_grid, "The grid on the ground")) g_show_grid = !g_show_grid;
    ImGui::End();
    ImGui::PopStyleVar(3);
}

void status_bar(float y, float w, float h)
{
    ImGui::SetNextWindowPos(ImVec2(0, y));
    ImGui::SetNextWindowSize(ImVec2(w, h));
    ImGui::Begin("##status", nullptr, kPanel | ImGuiWindowFlags_NoTitleBar | ImGuiWindowFlags_NoScrollbar);
    bool d = dirty();
    ImGui::TextColored(d ? ImVec4(0.95f, 0.72f, 0.30f, 1) : ImVec4(0.45f, 0.80f, 0.45f, 1), "%s", d ? "Unsaved changes" : "Saved");
    bar_sep();
    const char *f = std::strrchr(g_sc.saved_to, '\\');
    ImGui::TextUnformatted(f ? f + 1 : g_sc.saved_to);
    if (ImGui::IsItemHovered()) ImGui::SetTooltip("%s", g_sc.saved_to);
    bar_sep();
    int n = 0;
    for (u32 i = 0; i < g_sc.nent; i++) n += g_sel[i];
    ImGui::Text("%d selected", n);
    const char *u = "Units: game units, exact 16.16 fixed point kept on save";
    ImGui::SameLine(std::fmax(w - ImGui::CalcTextSize(u).x - 16.0f, ImGui::GetCursorPosX() + 20.0f));
    ImGui::TextDisabled("%s", u);
    ImGui::End();
}

void close_prompt()
{
    if (g_ask_close) {
        if (!dirty()) {
            g_open = false;
            g_ask_close = false;
            return;
        }
        ImGui::OpenPopup("Save changes?");
        g_ask_close = false;
    }
    if (ImGui::BeginPopupModal("Save changes?", nullptr, ImGuiWindowFlags_AlwaysAutoResize)) {
        ImGui::TextUnformatted("The level has unsaved changes.");
        if (ImGui::Button("Save and close")) {
            save();
            if (!dirty()) g_open = false;
            ImGui::CloseCurrentPopup();
        }
        ImGui::SameLine();
        if (ImGui::Button("Close without saving")) g_open = false, ImGui::CloseCurrentPopup();
        ImGui::SameLine();
        if (ImGui::Button("Cancel")) ImGui::CloseCurrentPopup();
        ImGui::EndPopup();
    }
}

} // namespace level_ed
