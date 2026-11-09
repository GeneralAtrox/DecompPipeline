// The level editor's panels (level_edit.cpp; level_edit_priv.hpp shares the state): the Outliner (search, the kinds'
// folders, click and Ctrl+click, double-click frames) and Details (typed positions, the exact 16.16 numbers, notes,
// Reset to original).
// Nearest existing: level_edit.cpp - split from it at 400 lines.
#include <SDL3/SDL.h> // the editor runs on SDL3 (also through level_edit_priv.hpp): built by the game build
#include "level_edit_priv.hpp"

namespace level_ed {

void icon(ImDrawList *dl, ImVec2 c, u32 kind, ImU32 col)
{
    switch (kind) {
    case LEVEL_SPAWN: dl->AddTriangleFilled(ImVec2(c.x, c.y - 5), ImVec2(c.x - 5, c.y + 4), ImVec2(c.x + 5, c.y + 4), col); break;
    case LEVEL_LIGHT: dl->AddCircleFilled(c, 4.5f, col); break;
    case LEVEL_BLOCKER: dl->AddRect(ImVec2(c.x - 5, c.y - 5), ImVec2(c.x + 5, c.y + 5), col, 0.0f, 0, 1.5f); break;
    case LEVEL_OBJECT: dl->AddRectFilled(ImVec2(c.x - 4, c.y - 4), ImVec2(c.x + 4, c.y + 4), col, 2.0f); break;
    default: dl->AddQuadFilled(ImVec2(c.x, c.y - 5), ImVec2(c.x + 5, c.y), ImVec2(c.x, c.y + 5), ImVec2(c.x - 5, c.y), col); break;
    }
}

bool has_text(const char *s, const char *f)
{
    if (!*f) return true;
    for (; *s; s++) {
        size_t k = 0;
        while (f[k] && s[k] && std::tolower(static_cast<unsigned char>(s[k])) == std::tolower(static_cast<unsigned char>(f[k]))) k++;
        if (!f[k]) return true;
    }
    return false;
}

void outliner(float x, float y, float w, float h)
{
    static char filter[64];
    ImGui::SetNextWindowPos(ImVec2(x, y));
    ImGui::SetNextWindowSize(ImVec2(w, h));
    ImGui::Begin("Outliner", nullptr, kPanel);
    ImGui::SetNextItemWidth(-1);
    ImGui::InputTextWithHint("##filter", "Search", filter, sizeof filter);
    ImGui::TextDisabled("%u objects", g_sc.nent);
    ImGui::BeginChild("##list");
    ImDrawList *dl = ImGui::GetWindowDrawList();
    for (u32 g = 0; g < LEVEL_KINDS; g++) {
        u32 k = kKindOrder[g], n = 0;
        for (u32 i = 0; i < g_sc.nent; i++) n += g_sc.ent[i].kind == k;
        if (!n) continue;
        char head[64];
        snprintf(head, sizeof head, "%s (%u)", kKindFolder[k], n);
        if (!ImGui::TreeNodeEx(head, ImGuiTreeNodeFlags_DefaultOpen | ImGuiTreeNodeFlags_SpanAvailWidth)) continue;
        for (u32 i = 0; i < g_sc.nent; i++) {
            const LevelEnt &e = g_sc.ent[i];
            if (e.kind != k) continue;
            if (filter[0] && !has_text(e.name, filter) && !has_text(e.type, filter) && !has_text(kKindLabel[k], filter)) continue;
            bool moved = std::memcmp(e.pos, e.orig, sizeof e.pos) != 0;
            ImGui::PushID(static_cast<int>(i));
            if (ImGui::Selectable("##row", g_sel[i] != 0, ImGuiSelectableFlags_None, ImVec2(0, ImGui::GetTextLineHeight()))) {
                if (ImGui::GetIO().KeyCtrl) select_toggle(static_cast<int>(i));
                else select_only(static_cast<int>(i));
            }
            if (ImGui::IsItemHovered() && ImGui::IsMouseDoubleClicked(0)) frame_selection();
            if (ImGui::IsItemHovered(ImGuiHoveredFlags_DelayNormal)) ImGui::SetTooltip("%s", e.type);
            ImVec2 a = ImGui::GetItemRectMin(), b = ImGui::GetItemRectMax();
            float th = ImGui::GetTextLineHeight();
            icon(dl, ImVec2(a.x + th * 0.5f, (a.y + b.y) * 0.5f), k, kKindColour[k]);
            char nm[80];
            snprintf(nm, sizeof nm, "%s%s", e.name, moved ? " *" : "");
            dl->AddText(ImVec2(a.x + th * 1.3f, a.y), ImGui::GetColorU32(ImGuiCol_Text), nm);
            float lw = ImGui::CalcTextSize(kKindLabel[k]).x;
            dl->AddText(ImVec2(b.x - lw - 6.0f, a.y), ImGui::GetColorU32(ImGuiCol_TextDisabled), kKindLabel[k]);
            ImGui::PopID();
        }
        ImGui::TreePop();
    }
    ImGui::EndChild();
    ImGui::End();
}

const char *collision_text(const LevelEnt &e)
{
    if (e.kind == LEVEL_BLOCKER) return e.axes ? "Box, invisible in game" : "The game's own shape, in code (locked)";
    if ((e.kind == LEVEL_MESH || e.kind == LEVEL_OBJECT) && e.collides) return "From the model, moves with it";
    if (e.kind == LEVEL_SPAWN) return "None (robot uses its own)";
    return "None";
}

void kv_row(const char *k, const char *v)
{
    ImGui::TableNextRow();
    ImGui::TableNextColumn();
    ImGui::TextDisabled("%s", k);
    ImGui::TableNextColumn();
    ImGui::TextWrapped("%s", v);
}

void details(float x, float y, float w, float h)
{
    ImGui::SetNextWindowPos(ImVec2(x, y));
    ImGui::SetNextWindowSize(ImVec2(w, h));
    ImGui::Begin("Details", nullptr, kPanel);
    int n = 0;
    for (u32 i = 0; i < g_sc.nent; i++) n += g_sel[i];
    if (g_primary < 0 || !n) {
        ImGui::TextDisabled("Select an object in the viewport or Outliner.");
        ImGui::End();
        return;
    }
    if (n > 1) {
        ImGui::Text("%d objects", n);
        ImGui::TextDisabled("Move moves them all by the same amount.");
        ImGui::End();
        return;
    }
    LevelEnt &e = g_sc.ent[g_primary];
    ImDrawList *dl = ImGui::GetWindowDrawList();
    ImGui::TextUnformatted(e.name);
    ImGui::SameLine();
    {
        const char *b = kKindLabel[e.kind % LEVEL_KINDS];
        ImVec2 p = ImGui::GetCursorScreenPos(), s = ImGui::CalcTextSize(b);
        ImU32 c = kKindColour[e.kind % LEVEL_KINDS];
        dl->AddRectFilled(p, ImVec2(p.x + s.x + 12.0f, p.y + s.y + 2.0f), (c & 0x00ffffffu) | 0x40000000u, 4.0f);
        dl->AddText(ImVec2(p.x + 6.0f, p.y + 1.0f), c, b);
        ImGui::Dummy(ImVec2(s.x + 12.0f, s.y + 2.0f));
    }
    ImGui::Separator();
    ImGui::TextDisabled("Location (game units)");
    float fw = std::fmax((ImGui::GetContentRegionAvail().x - 3.0f * 14.0f * g_ui) / 3.0f, 30.0f);
    for (int k = 0; k < 3; k++) {
        ImGui::PushID(k);
        ImVec2 p = ImGui::GetCursorScreenPos();
        dl->AddRectFilled(p, ImVec2(p.x + 4.0f * g_ui, p.y + ImGui::GetFrameHeight()), kAxisCol[k], 2.0f);
        ImGui::Dummy(ImVec2(6.0f * g_ui, ImGui::GetFrameHeight()));
        ImGui::SameLine(0, 0);
        double v = static_cast<double>(e.pos[k]) / g_sc.unit;
        ImGui::BeginDisabled(!(e.axes >> k & 1));
        ImGui::SetNextItemWidth(fw);
        /* the typed number is taken when the field is left or Enter is pressed (InputScalar has no EnterReturnsTrue) */
        static double pending[3];
        static bool typed[3];
        if (ImGui::InputDouble("##v", &v, 0.0, 0.0, "%.1f")) pending[k] = v, typed[k] = true;
        if (ImGui::IsItemDeactivatedAfterEdit() && typed[k]) {
            typed[k] = false;
            s32 nv = quantize(e, static_cast<s32>(std::llround(pending[k] * g_sc.unit)));
            if (nv != e.pos[k]) {
                Move m;
                std::array<s32, 3> f{e.pos[0], e.pos[1], e.pos[2]}, t = f;
                t[k] = nv;
                m.ids.push_back(static_cast<u32>(g_primary)), m.from.push_back(f), m.to.push_back(t);
                apply_pos(static_cast<u32>(g_primary), t.data());
                commit(std::move(m));
            }
        }
        ImGui::EndDisabled();
        hover_tip(k == 0 ? "X" : k == 2 ? "Z" : g_sc.y_down ? "Y, as the game counts it: down is plus" : "Y");
        ImGui::PopID();
        if (k < 2) ImGui::SameLine();
    }
    ImGui::PushTextWrapPos(0.0f);
    ImGui::TextDisabled("Saved exactly: X 0x%08X \xc2\xb7 Y 0x%08X \xc2\xb7 Z 0x%08X", static_cast<u32>(e.pos[0]), static_cast<u32>(e.pos[1]),
                        static_cast<u32>(e.pos[2]));
    ImGui::PopTextWrapPos();
    ImGui::Spacing();
    if (ImGui::BeginTable("##kv", 2)) {
        char t[96];
        ImGui::TableSetupColumn("k", ImGuiTableColumnFlags_WidthFixed, 80.0f * g_ui);
        ImGui::TableSetupColumn("v", ImGuiTableColumnFlags_WidthStretch);
        kv_row("Collision", collision_text(e));
        if (!e.axes) kv_row("Moves", "Locked in this version");
        else if (e.quantum > 1) snprintf(t, sizeof t, "In steps of %g", static_cast<double>(e.quantum) / g_sc.unit), kv_row("Moves", t);
        kv_row("Type", e.type);
        if (e.kind == LEVEL_LIGHT) {
            snprintf(t, sizeof t, "%.3f, %.3f, %.3f", static_cast<double>(e.dir[0]), static_cast<double>(e.dir[1]), static_cast<double>(e.dir[2]));
            kv_row("Shines", t);
        }
        if (e.info[0]) kv_row("Notes", e.info);
        ImGui::EndTable();
    }
    if (e.axes && std::memcmp(e.pos, e.orig, sizeof e.pos) != 0 && ImGui::SmallButton("Reset to original")) {
        Move m;
        m.ids.push_back(static_cast<u32>(g_primary));
        m.from.push_back({e.pos[0], e.pos[1], e.pos[2]});
        m.to.push_back({e.orig[0], e.orig[1], e.orig[2]});
        apply_pos(static_cast<u32>(g_primary), e.orig);
        commit(std::move(m));
    }
    ImGui::End();
}

} // namespace level_ed
