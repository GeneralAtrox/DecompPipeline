// Dear ImGui (base/third_party/imgui, MIT, vendored unchanged) for the level editor (base/level_edit*.cpp), built as
// one unit beside it. Vendored code is outside the kit's game-free, size and /W4 /WX checks: its warnings are off
// here and only here.
// Nearest existing: none (the base had no UI toolkit; the menu bar is Win32, host_menu.cpp).
#pragma warning(push, 0)
#pragma warning(disable : 4127 4189 4244 4267 4456 4457 4505 4701 4702 4996)
#include "imgui/imgui.cpp"
#include "imgui/imgui_draw.cpp"
#include "imgui/imgui_tables.cpp"
#include "imgui/imgui_widgets.cpp"
#include "imgui/imgui_demo.cpp"
#include "imgui/imgui_impl_sdl3.cpp"
#include "imgui/imgui_impl_dx11.cpp"
#pragma warning(pop)
