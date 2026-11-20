/* The game's own mouse (port/base/input_kbm_mouse.c, native_game.h pg_native_mouse_mode) and text field (input_kbm.c
 * input_kbm_text_mode; input_kbm_inject.c TEXT= and FOCUSLOST) against kbm_test.c's stubs: kbm_test.c's main calls
 * kbm_game_checks last, with its vertical blank. Built with it by tests/test_kbm.py, in both layouts.
 * Kept apart from kbm_test.c, which is over the 400-line cap. */
#include <stdio.h>
#include <string.h>
#include "base.h"
#include "host.h"

void pg_native_mouse_mode(int mode); /* native_game.h; defined in input_kbm_mouse.c */
int input_key_code(const char *name); /* kbm_test.c's stub */

static char g_typed[64];
static int g_typed_n, g_bad;

/* host_text.cpp's: what --kbm-inject TEXT= types */
void host_text_put(const char *text, int n)
{
    if (n > 0 && g_typed_n + n < (int)sizeof g_typed) memcpy(g_typed + g_typed_n, text, (size_t)n), g_typed_n += n;
}

static void check(int ok, const char *what)
{
    printf("%s: %s\n", ok ? "ok  " : "FAIL", what);
    if (!ok) g_bad = 1;
}

static void text_checks(u32 *vb)
{
    static bool keys[512];
    KbmPad k;
#ifdef GAME
    const char *bound = "Q"; /* the game's layout: Q is X */
    const u32 bit = HOST_PAD_X;
#else
    const char *bound = "Z"; /* the kit's: Z is A */
    const u32 bit = HOST_PAD_A;
#endif
    keys[input_key_code(bound)] = 1;
    input_kbm_read(keys, &k);
    check((k.b & bit) != 0, "text: shut, the layout's key presses its button");
    input_kbm_text_mode(1);
    input_kbm_read(keys, &k);
    check(!(k.b & bit), "text: the game's field open, the layout's key presses nothing");
    input_kbm_text_mode(0);
    input_kbm_read(keys, &k);
    check((k.b & bit) != 0, "text: shut again, the key presses it again");
    keys[input_key_code(bound)] = 0;
    input_kbm_read(keys, &k);
    input_kbm_option("--kbm-inject", "TEXT=h\\xc3\\xa9llo\\b\\n@1000");
    for (*vb = 998; *vb < 1003; (*vb)++) input_kbm_read(NULL, &k);
    check(g_typed_n == 8 && !memcmp(g_typed, "h\xc3\xa9llo\b\n", 8), "text: TEXT= typed once, its escapes undone");
}

static void look_checks(u32 *vb)
{
    KbmPad k;
    PgNativeMouse m = {0};
    float dx = 0.0f, dy = 0.0f, later = 0.0f;
    int a = 0, presses = 0;
    pg_native_mouse_mode(PG_MOUSE_LOOK);
    check(input_kbm_game_captured() && input_kbm_capture_wanted(), "look: the game has the mouse, the window keeps the pointer");
    input_kbm_option("--kbm-inject", "MOUSEMOVE=10:-4@1010+3,MOUSELEFT@1020+2,WHEELUP@1024");
    for (*vb = 1005; *vb < 1030; (*vb)++) {
        input_kbm_read(NULL, &k);
        check(input_kbm_game_mouse(&m) == 1, "look: taken each read") ;
        dx += m.dx, dy += m.dy;
        if (k.b & HOST_PAD_A) a++;
        if (m.pressed & 1u) presses++;
        if (*vb == 1024) check(m.wheel == 1 && !(k.b & HOST_PAD_Y), "look: a notch is the game's, not the layout's");
    }
    check(dx == 30.0f && dy == -12.0f, "look: MOUSEMOVE=10:-4 over three reads is 30, -12");
    check(!a, "look: the layout's left button presses nothing");
    check(presses == 1, "look: the game's left button pressed once");
    input_kbm_option("--kbm-inject", "FOCUSLOST@1040,MOUSEMOVE=5:5@1042+2,MOUSELEFT@1046+2");
    for (*vb = 1035; *vb < 1050; (*vb)++) {
        input_kbm_read(NULL, &k);
        input_kbm_game_mouse(&m);
        if (*vb >= 1040) later += m.dx + m.dy + (float)m.pressed;
    }
    check(!m.captured && later == 0.0f, "focus lost: captured 0, no moves, no presses");
    check(!input_kbm_capture_wanted(), "focus lost: the window lets the pointer go");
    input_kbm_focus_gained();
    input_kbm_mouse_button(0, 1);
    input_kbm_game_mouse(&m);
    check(m.captured && !m.pressed && !m.buttons, "a click takes it back and is swallowed");
    input_kbm_mouse_button(0, 0);
    input_kbm_mouse_button(0, 1);
    input_kbm_game_mouse(&m);
    check(m.pressed == 1u && m.buttons == 1u, "the next click is the game's");
    input_kbm_mouse_button(0, 0);
    input_kbm_menu_open(1);
    input_kbm_game_mouse(&m);
    check(!m.captured && !input_kbm_capture_wanted(), "the menu open lets it go");
    input_kbm_menu_open(0);
    pg_native_mouse_mode(PG_MOUSE_POINTER);
    input_kbm_mouse_button(0, 1);
    input_kbm_mouse_button(0, 0);
    input_kbm_mouse_motion(3.0f, 4.0f);
    input_kbm_mouse_motion(3.0f, 4.0f); /* two moves before a read add up */
    input_kbm_game_mouse(&m);
    check(m.captured && m.dx == 6.0f && m.dy == 8.0f && !input_kbm_capture_wanted(), "pointer: taken by a click, moves add up, the pointer stays shown");
    pg_native_mouse_mode(PG_MOUSE_LAYOUT);
    check(!input_kbm_game_mouse(&m) && !input_kbm_game_captured(), "layout: the mouse the layout's again");
}

int kbm_game_checks(u32 *vb)
{
    text_checks(vb);
    look_checks(vb);
    return g_bad;
}
