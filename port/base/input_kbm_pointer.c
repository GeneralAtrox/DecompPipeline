/* Menu pointer (base.h input_kbm_menu_pointer). The user, 2026-11-13, about a game's pause menu: "Pause menu does not
 * support mouse control". While a game says a menu is up and gives its entries' rectangles (every frame, with the entry selected), the
 * mouse picks entries the way the pad does, so the game's own menu code runs unchanged:
 *   - the pointer moved onto an entry: the d-pad steps the selection there (UP/DOWN, or LEFT/RIGHT for a row; the
 *     shorter way round when the menu wraps), one press at a time, each let go before the next;
 *   - a left click on an entry: the same, then the confirm button (A unless the game names another) once the selection
 *     is that entry (or the entry's own press: an option row's next value, say). A click off every entry does nothing. The button, held on after the menu shuts, presses nothing
 *     through the layout until it is let go;
 *   - the wheel: one step up or down per notch, instead of the layout's targets (outside a menu the layout's again);
 *   - the right button and every key: the layout's, as before. A key pressed drops a hover not yet reached, so the
 *     pointer resting on an entry never fights the keyboard; only moving it to another entry steers again.
 * It all becomes presses of player 1's pad before the pad is recorded (input_kbm.c input_kbm_read), so a replay needs
 * nothing and reads no pointer. The window turns the pointer into the game's 640x480 screen through the picture's place
 * at any shape (host_mouse.cpp, present_aspect.c gfx_screen_point). Mouse steer waits while a menu is up, so the pointer
 * stays free and shown. A selection the steps never reach (an entry the game skips) is given up after one lap.
 * Off until a game asks: a game that never calls it is unchanged. --kbm-inject MOUSEPOS=X:Y@V puts the pointer there
 * for a test (input_kbm_inject.c). */
#include <string.h>
#include "base.h"
#include "host.h"
#include "input_kbm_priv.h"

#define STEP_ON 2     /* reads a step or the confirm is held (as a wheel notch, input_kbm.c) */
#define STEP_OFF 2    /* reads let go before the next */
#define FRESH_READS 8 /* reads a menu stays up without the game giving it again */
#define WHEEL_MAX 8

static PortMenuPointer g_menu;
static int g_fresh;
static float g_pos[2];
static int g_have_pos, g_moved;
static int g_hover = -1, g_target = -1, g_click = -1, g_steps, g_wheel;
static int g_pulse_left, g_was_left, g_left_eaten;
static u32 g_pulse_bit;

int kbm_pointer_on(void) { return g_fresh > 0 && g_menu.count > 0; }

static void forget(void)
{
    g_hover = g_target = g_click = -1;
    g_steps = g_wheel = 0;
}

void input_kbm_menu_pointer(const PortMenuPointer *menu)
{
    int was = kbm_pointer_on();
    if (menu && menu->count > 0) {
        g_menu = *menu;
        if (g_menu.count > PORT_MENU_POINTER_MAX) g_menu.count = PORT_MENU_POINTER_MAX;
        g_fresh = FRESH_READS;
    } else {
        g_menu.count = 0, g_fresh = 0;
    }
    if (was == kbm_pointer_on()) return;
    forget();
    g_moved = 0; /* a pointer resting where the menu opens moves nothing */
    if (!was) g_pulse_left = 0;
    port_log("input: menu pointer %s (%d entries)\n", was ? "off" : "on", g_menu.count);
}

void input_kbm_mouse_position(float x, float y)
{
    if (kbm_game_mouse()) { /* the game's own pointer (input_kbm_mouse.c): no menu pointer */
        kbm_game_position(x, y);
        return;
    }
    g_pos[0] = x, g_pos[1] = y;
    g_have_pos = g_moved = 1;
}

static int entry_at(void)
{
    for (int i = 0; g_have_pos && i < g_menu.count; i++) {
        const s16 *r = g_menu.rect[i];
        if (g_pos[0] >= r[0] && g_pos[0] < r[0] + r[2] && g_pos[1] >= r[1] && g_pos[1] < r[1] + r[3]) /* w or h 0: never */
            return i;
    }
    return -1;
}

int kbm_pointer_wheel(int notches)
{
    if (!kbm_pointer_on()) return 0;
    g_wheel -= notches; /* up is the previous entry */
    g_wheel = g_wheel > WHEEL_MAX ? WHEEL_MAX : g_wheel < -WHEEL_MAX ? -WHEEL_MAX : g_wheel;
    return 1;
}

void kbm_pointer_forget(void)
{
    forget();
    g_pulse_left = 0;
}

int kbm_pointer_left(int down)
{
    if (down && !g_was_left && kbm_pointer_on() && kbm_window_ok()) {
        int at = entry_at();
        g_left_eaten = 1; /* the menu's, until let go */
        if (at >= 0) g_click = at, g_target = -1, g_steps = 0;
        port_log("input: menu pointer: click at %d,%d on entry %d (selected %d)\n", (int)g_pos[0], (int)g_pos[1], at,
                 g_menu.selected);
    }
    if (!down) g_left_eaten = 0;
    g_was_left = down;
    return down && !g_left_eaten;
}

static u32 dir_bit(int dir)
{
    if (g_menu.horizontal) return input_name_bit(dir > 0 ? "RIGHT" : "LEFT");
    return input_name_bit(dir > 0 ? "DOWN" : "UP");
}

/* The way from entry `from` to `to`: 1 next, -1 previous; round the end when that is shorter and the menu wraps. */
static int way(int from, int to)
{
    int d = to - from, n = g_menu.count;
    if (g_menu.wrap) {
        if (d > n / 2) d -= n;
        else if (d < -(n / 2)) d += n;
    }
    return d > 0 ? 1 : -1;
}

static u32 pulse(void)
{
    if (!g_pulse_left) return 0;
    return g_pulse_left-- > STEP_OFF ? g_pulse_bit : 0u;
}

static u32 start(u32 bit)
{
    g_pulse_bit = bit, g_pulse_left = STEP_ON + STEP_OFF;
    return pulse();
}

u32 kbm_pointer_read(int keys_pressed)
{
    int goal, sel;
    if (g_fresh > 0 && --g_fresh == 0) port_log("input: menu pointer off (not given again)\n");
    if (!kbm_pointer_on() || !kbm_window_ok()) {
        kbm_pointer_forget();
        return 0;
    }
    if (g_moved) {
        int at = entry_at();
        g_moved = 0;
        if (at >= 0 && at != g_hover) g_target = at, g_steps = 0;
        g_hover = at;
    }
    if (keys_pressed) g_target = -1; /* the keyboard took over */
    if (g_pulse_left) return pulse();
    if (g_wheel) {
        int dir = g_wheel > 0 ? 1 : -1;
        g_wheel -= dir;
        g_target = -1;
        return start(dir_bit(dir));
    }
    goal = g_click >= 0 ? g_click : g_target;
    sel = g_menu.selected;
    if (goal < 0 || goal >= g_menu.count) {
        g_click = g_target = -1;
        return 0;
    }
    if (sel == goal) {
        int click = g_click >= 0;
        g_click = g_target = -1;
        if (!click) return 0u;
        return start(g_menu.press[goal] ? g_menu.press[goal] : g_menu.confirm ? g_menu.confirm : input_name_bit("A"));
    }
    if (sel < 0 || ++g_steps > g_menu.count + 1) {
        port_log("input: menu pointer: entry %d not reached (selected %d), given up\n", goal, sel);
        g_click = g_target = -1;
        return 0;
    }
    return start(dir_bit(way(sel, goal)));
}
