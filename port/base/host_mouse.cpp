// The mouse's events from the window (host_window.cpp handle): the mouse is player 1's, by the keyboard and mouse layout
// (input_kbm.c), or the game's own (input_kbm_mouse.c, native_game.h pg_native_mouse_mode); and SDL's text input for the
// game's text field (host_text.cpp). Apart from host_window.cpp only for its 400-line cap.
#include <SDL3/SDL.h>
#include "host.h"
#include "host_internal.hpp"
#include "present.h"

namespace {

// The pointer from window coordinates to the game's 640x480 screen, through where the picture sits at any shape
// (present_aspect.c gfx_screen_point); for the menu pointer (input_kbm_pointer.c).
void pointer_at(SDL_WindowID id, float x, float y)
{
    SDL_Window *w = SDL_GetWindowFromID(id);
    int ww = 0, wh = 0;
    if (!w || !SDL_GetWindowSize(w, &ww, &wh) || ww <= 0 || wh <= 0 || !g_gfx.frame_w || !g_gfx.frame_h) return;
    GfxFrameShape s = {g_gfx.frame_w, g_gfx.frame_h, g_gfx.frame_x, g_gfx.frame_y, g_gfx.wide_k, g_gfx.tall_k};
    f32 at[2];
    gfx_screen_point(x * static_cast<f32>(g_gfx.out_w) / static_cast<f32>(ww),
                     y * static_cast<f32>(g_gfx.out_h) / static_cast<f32>(wh), &s, at);
    input_kbm_mouse_position(at[0], at[1]);
}

} // namespace

namespace host {

void mouse_event(const SDL_Event &e)
{
    switch (e.type) {
    case SDL_EVENT_MOUSE_BUTTON_DOWN:
    case SDL_EVENT_MOUSE_BUTTON_UP: { // the mouse is player 1's, by the keyboard and mouse layout (input_kbm.c)
        int which = e.button.button == SDL_BUTTON_LEFT ? 0 : e.button.button == SDL_BUTTON_MIDDLE ? 1 : e.button.button == SDL_BUTTON_RIGHT ? 2 : -1;
        input_kbm_mouse_button(which, e.type == SDL_EVENT_MOUSE_BUTTON_DOWN);
        host::mouse_capture_sync(); // a middle drag (the look) keeps the pointer
        break;
    }
    case SDL_EVENT_MOUSE_MOTION:
        input_kbm_mouse_motion(e.motion.xrel, e.motion.yrel);
        pointer_at(e.motion.windowID, e.motion.x, e.motion.y);
        break;
    case SDL_EVENT_TEXT_INPUT: host_text_input(e.text.text); break;
    case SDL_EVENT_MOUSE_WHEEL: {
        static float part; // a smooth wheel's fractions add up to notches
        part += e.wheel.direction == SDL_MOUSEWHEEL_FLIPPED ? -e.wheel.y : e.wheel.y;
        int notches = static_cast<int>(part);
        part -= static_cast<float>(notches);
        if (notches) input_kbm_mouse_wheel(notches);
        break;
    }
    default: break;
    }
}

// The window keeps the pointer (relative mode) while the layout wants it (a middle drag, mouse steer) or the game's look
// has the mouse; Alt held gives the player the game's mouse.
bool mouse_capture_want(void *sdl_window)
{
    SDL_Window *w = static_cast<SDL_Window *>(sdl_window);
    host_text_sync(w);
    if (input_kbm_game_captured() && (SDL_GetModState() & SDL_KMOD_ALT)) input_kbm_game_release();
    return input_kbm_capture_wanted() != 0 && (SDL_GetWindowFlags(w) & SDL_WINDOW_INPUT_FOCUS) != 0;
}

} // namespace host
