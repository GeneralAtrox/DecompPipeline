/* The mouse given straight to a game (native_game.h pg_native_mouse_mode): PG_MOUSE_POINTER (the game draws a pointer)
 * or PG_MOUSE_LOOK (moves only; the window keeps the pointer hidden, relative mode, while captured). In either, the
 * window's mouse comes here instead of the layout (input_kbm.c): its mouse sources, the middle-drag look, mouse steer and
 * the menu pointer press nothing. PG_MOUSE_LAYOUT, the default, leaves all of that as it was.
 *   Captured: the game has the mouse. The focus lost, the kit's menu or the title bar open (a modal takes the focus) and
 *   Alt held (host_mouse.cpp) let it go: buttons up, no moves. The next click in the picture takes it back, and that
 *   click is swallowed (never the game's) until its button is let go.
 *   Sensitivity is the game's own: the moves are the window's pixels, the Core scales nothing.
 * Taken once per pad read (input.c events_read) and recorded there (session_events.c). No SDL here (tests/kbm_test.c).
 * Nearest existing: input_kbm_steer.c - the mouse turned into the pad's left stick; this hands the game the mouse itself. */
#include <string.h>
#include "base.h"
#include "host.h"
#include "input_kbm_priv.h"

static int g_mode;
static PgNativeMouse g_m; /* since the last read: moves, presses and notches add up; buttons, the pointer and captured now */
static u32 g_held;        /* the buttons down, as the window says */
static u32 g_swallow;     /* a button whose press took the mouse back: not the game's until let go */

void pg_native_mouse_mode(int mode)
{
    mode = mode == PG_MOUSE_POINTER || mode == PG_MOUSE_LOOK ? mode : PG_MOUSE_LAYOUT;
    if (mode == g_mode) return;
    if (g_mode == PG_MOUSE_LAYOUT) { /* the layout let go of the mouse: the game has it if the window is the player's */
        memset(&g_m, 0, sizeof g_m);
        g_m.captured = (u8)kbm_window_ok();
        g_held = g_swallow = 0;
    }
    g_mode = mode;
    if (mode == PG_MOUSE_LAYOUT) memset(&g_m, 0, sizeof g_m), g_held = g_swallow = 0;
    port_log("input: the mouse is %s\n", mode == PG_MOUSE_LOOK ? "the game's (look)" : mode ? "the game's (pointer)" : "the layout's");
}

int kbm_game_mouse(void) { return g_mode != PG_MOUSE_LAYOUT; }
int input_kbm_game_look(void) { return g_mode == PG_MOUSE_LOOK && g_m.captured; }
int input_kbm_game_captured(void) { return g_mode != PG_MOUSE_LAYOUT && g_m.captured; }

void input_kbm_game_release(void)
{
    if (g_mode == PG_MOUSE_LAYOUT || !g_m.captured) return;
    g_m.captured = 0, g_m.buttons = 0, g_swallow = 0;
    port_log("input: the game's mouse let go (vertical blank %u)\n", port_vblank_count());
}

void kbm_game_button(int which, int down)
{
    u32 bit = which == 0 ? 1u : which == 2 ? 2u : 4u;
    if (((g_held & bit) != 0) == (down != 0)) return; /* no change */
    g_held = down ? g_held | bit : g_held & ~bit;
    if (!down) {
        g_swallow &= ~bit, g_m.buttons &= ~bit;
    } else if (!g_m.captured) {
        if (!kbm_window_ok()) return;
        g_m.captured = 1, g_swallow |= bit; /* taken back; the click itself is not the game's */
        port_log("input: the game's mouse taken back by a click (vertical blank %u)\n", port_vblank_count());
    } else if (!(g_swallow & bit)) {
        g_m.buttons |= bit, g_m.pressed |= bit;
    }
}

void kbm_game_motion(float dx, float dy)
{
    if (g_m.captured) g_m.dx += dx, g_m.dy += dy;
}

void kbm_game_wheel(int notches)
{
    if (g_m.captured) g_m.wheel += notches;
}

void kbm_game_position(float x, float y)
{
    if (g_m.captured) g_m.x = x, g_m.y = y;
}

int input_kbm_game_mouse(PgNativeMouse *out)
{
    if (g_mode == PG_MOUSE_LAYOUT) {
        memset(out, 0, sizeof *out);
        return 0;
    }
    if (g_m.captured && !kbm_window_ok()) input_kbm_game_release();
    *out = g_m;
    g_m.dx = g_m.dy = 0.0f, g_m.pressed = 0, g_m.wheel = 0;
    return 1;
}
