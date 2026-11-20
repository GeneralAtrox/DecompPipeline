/* Mouse steer (base.h input_kbm_mouse_steer). The user, 2026-11-11: "In a aircraft i think we should use mouse movement
 * as rotation". While the game asks for it (it says so every frame), the setting is on, the window has the focus and the
 * kit's menu is shut, the mouse moving with the middle button up turns player 1's left stick: across is X (right
 * positive), forward and back is Y (forward is up, y negative, the sense of a stick pushed up or a key on LSUP; Invert
 * mouse Y flips it). The middle button held keeps the mouse for the look (input_kbm.c). The window keeps the pointer
 * meanwhile (host_window.cpp, from input_kbm_capture_wanted); a headless run has no pointer to keep but steers the same,
 * for --kbm-inject MOUSEMOVE (input_kbm_inject.c).
 *   A rate: the pixels moved since the last read, times the sensitivity, are the deflection, eased with the last read's,
 *   so a steady hand gives a steady stick and a still mouse brings it back to the middle within a few reads. It is
 *   added to the stick keys' (input_kbm.c input_kbm_read) before the pad is recorded, so a replay needs no settings.
 *   Settings (Input > Keyboard and mouse, kept apart from the game's save; they come back as --kbm-mouse-* options):
 *   kbm-mouse-steer on|off (on: it does nothing until a game asks), kbm-mouse-sensitivity 1..10 (5),
 *   kbm-mouse-invert on|off (off).
 * Off until the game asks, so a game that never does is unchanged. */
#include <stdlib.h>
#include <string.h>
#include "base.h"
#include "host.h"
#include "input_kbm_priv.h"

/* By feel: */
#define STEER_PIXELS_FULL 24.0f /* pixels moved between two reads for full deflection, at sensitivity 5 */
#define STEER_KEEP 0.4f         /* of the last read's deflection kept at each read; the rest follows the mouse */
#define STEER_SNAP 3.0f         /* deflection under this with the mouse still is the middle */
#define STEER_BOUND 4096.0f     /* pixels kept between two reads at most (a run that reads no pad) */
#define SENS_MIN 1
#define SENS_MAX 10
#define SENS_DEFAULT 5

static const char *const k_options[] = {"--kbm-mouse-steer", "--kbm-mouse-sensitivity", "--kbm-mouse-invert"};
static int g_want, g_setting = 1, g_sens = SENS_DEFAULT, g_invert;
static float g_acc[2], g_steer[2]; /* pixels since the last read; the deflection, -127..127 */

int kbm_steer_active(void) { return g_want && g_setting && kbm_window_ok() && !kbm_pointer_on(); } /* a menu up: the pointer free */

static void centre(void) { g_acc[0] = g_acc[1] = g_steer[0] = g_steer[1] = 0.0f; }

void input_kbm_mouse_steer(int on)
{
    if ((on != 0) == g_want) return;
    g_want = on != 0;
    if (!g_want) centre();
    port_log("input: mouse steer %s by the game\n", g_want ? "asked for" : "let go");
}

void kbm_steer_motion(float dx, float dy)
{
    if (!kbm_steer_active()) return;
    g_acc[0] += dx, g_acc[1] += dy;
    for (int a = 0; a < 2; a++)
        g_acc[a] = g_acc[a] > STEER_BOUND ? STEER_BOUND : g_acc[a] < -STEER_BOUND ? -STEER_BOUND : g_acc[a];
}

void kbm_steer_read(int *x, int *y)
{
    float gain = 127.0f / STEER_PIXELS_FULL * (float)g_sens / (float)SENS_DEFAULT;
    int out[2];
    if (!kbm_steer_active()) centre(); /* let go at once: the game, the setting, the focus or the menu */
    for (int a = 0; a < 2; a++) {
        float move = g_acc[a] * gain * ((a && g_invert) ? -1.0f : 1.0f), s;
        move = move > 127.0f ? 127.0f : move < -127.0f ? -127.0f : move;
        s = g_steer[a] * STEER_KEEP + move * (1.0f - STEER_KEEP);
        if (g_acc[a] == 0.0f && s < STEER_SNAP && s > -STEER_SNAP) s = 0.0f;
        g_steer[a] = s;
        g_acc[a] = 0.0f;
        out[a] = (int)(s < 0.0f ? s - 0.5f : s + 0.5f);
    }
    *x = out[0], *y = out[1];
}

/* ---- the settings: Input > Keyboard and mouse, and --kbm-mouse-steer|sensitivity|invert ---- */
int input_kbm_steer_get(int which)
{
    return which == KBM_STEER_ON ? g_setting : which == KBM_STEER_SENSITIVITY ? g_sens : which == KBM_STEER_INVERT ? g_invert : 0;
}

void input_kbm_steer_set(int which, int value)
{
    if (which == KBM_STEER_ON) g_setting = value != 0;
    else if (which == KBM_STEER_SENSITIVITY) g_sens = value < SENS_MIN ? SENS_MIN : value > SENS_MAX ? SENS_MAX : value;
    else if (which == KBM_STEER_INVERT) g_invert = value != 0;
}

const char *input_kbm_steer_key(int which) { return which >= 0 && which < 3 ? k_options[which] + 2 : ""; }

int kbm_steer_option(const char *arg, const char *value)
{
    int on = strcmp(value, "off") != 0 && strcmp(value, "0") != 0;
    for (int w = 0; w < 3; w++)
        if (kbm_same(arg, k_options[w])) {
            input_kbm_steer_set(w, w == KBM_STEER_SENSITIVITY ? atoi(value) : on);
            return 2;
        }
    return 0;
}
