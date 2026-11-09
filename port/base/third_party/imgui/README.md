# Dear ImGui, vendored

Dear ImGui v1.91.9b (https://github.com/ocornut/imgui, tag v1.91.9b), MIT (LICENSE.txt), the files as
released: the core, the demo (imgui.cpp's tools reach it) and the SDL3 and Direct3D 11 backends (from `backends/`, placed
here so their `#include "imgui.h"` finds the core). Nothing is changed. `../imgui_build.cpp` compiles them as one unit
with their warnings off; the level editor (`../level_edit*.cpp`) is the only user.
