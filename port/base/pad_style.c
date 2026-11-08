/* The style of controller the player holds, for a texture pack's variants (pvr/pvr_pack.c): a pack's index.txt line
 * "KEY FILE xbox" or "KEY FILE playstation" replaces the texture only while that style is in use, so a game's
 * controller pictures can follow the pad on the desk. The style is 0 dreamcast (the game's own art, the default), 1 xbox, 2
 * playstation.
 *   --pad-style auto|dreamcast|xbox|playstation   auto (the default): the type SDL reports for the device last used, or
 *                        before any is, the first connected gamepad; looked at about once a second and when another
 *                        device is used. Only a run that reads real input does (gfx_live.c), so a check run, a replay
 *                        or a headless run never has a style; the others fix it.
 *   --pad-style NAME@VBLANK,..   a test's swaps: fixed at each of those vertical blanks (dreamcast until the first).
 *   --pad-art on|off     the Input menu's Controller pictures, saved as pad-art: off shows the game's own art whatever
 *                        is held (pad_art_style is 0), and the names of the buttons still follow the pad (pad_style).
 * The keyboard, a raw joystick (no gamepad mapping: what a USB adapter for a console's pad usually is) and any gamepad SDL
 * doesn't call Xbox or PlayStation are style 0. So is one whose name says it is a Dreamcast pad, whatever type it reports.
 * One style for every player: a pack's variant replaces a texture for all its draws.
 * Polled from host_pads_read (the game thread, which owns SDL's devices). */
#include <stdlib.h>
#include <string.h>
#include <SDL3/SDL.h>
#include "base.h"
#include "game_log.h"

#define POLL_EVERY 60u /* pad reads: about a second */
#define MAX_SWAPS 16u

static const char *const kNames[] = {"dreamcast", "xbox", "playstation"};
static const char *const kDreamcastNames[] = {"dreamcast", "dreamconn", "dreampicoport"};
static int g_forced = -1;     /* --pad-style other than auto */
static volatile int g_style;  /* auto's answer */
static volatile int g_art = 1; /* --pad-art */
static volatile u32 g_serial; /* changes with the style: the renderer decodes its textures again */
static u32 g_polls;
static int g_used_known;      /* a device was used, or the keyboard */
static u32 g_used;            /* which: its SDL id, 0 the keyboard */
static struct {
    int style;
    u32 at;
} g_swap[MAX_SWAPS];
static u32 g_swaps, g_swap_next;
static int g_logged = -1;

static int style_named(const char *s, size_t n)
{
    for (int i = 0; i < 3; i++)
        if (strlen(kNames[i]) == n && !strncmp(s, kNames[i], n)) return i;
    return -1;
}

int pad_style_option(const char *arg, const char *value)
{
    if (strcmp(arg, "--pad-style") || !value) return 0;
    g_forced = -1;
    g_swaps = g_swap_next = 0;
    if (strchr(value, '@')) {
        g_forced = 0;
        for (const char *p = value; *p && g_swaps < MAX_SWAPS;) {
            size_t n = strcspn(p, "@,");
            int style = style_named(p, n);
            if (style >= 0 && p[n] == '@') {
                g_swap[g_swaps].style = style;
                g_swap[g_swaps++].at = (u32)strtoul(p + n + 1, NULL, 10);
            } else {
                port_log("input: cannot read the --pad-style entry \"%s\"\n", p);
            }
            p += strcspn(p, ",");
            if (*p) p++;
        }
    } else {
        g_forced = style_named(value, strlen(value));
    }
    g_serial++;
    return 2;
}

int pad_style(void) { return g_forced >= 0 ? g_forced : g_style; }
int pad_art_on(void) { return g_art; }
int pad_art_style(void) { return g_art ? pad_style() : 0; }
u32 pad_style_serial(void) { return g_serial; }

void pad_art_set(int on)
{
    if (on == g_art) return;
    g_art = on;
    g_serial++;
}

static int style_of(SDL_JoystickID id)
{
    const char *name = SDL_GetGamepadNameForID(id);
    for (size_t i = 0; name && i < sizeof kDreamcastNames / sizeof kDreamcastNames[0]; i++)
        if (SDL_strcasestr(name, kDreamcastNames[i])) return 0;
    switch (SDL_GetGamepadTypeForID(id)) {
    case SDL_GAMEPAD_TYPE_XBOX360:
    case SDL_GAMEPAD_TYPE_XBOXONE: return 1;
    case SDL_GAMEPAD_TYPE_PS3:
    case SDL_GAMEPAD_TYPE_PS4:
    case SDL_GAMEPAD_TYPE_PS5: return 2;
    default: return 0;
    }
}

/* The device a button was just pressed on: 0 the keyboard. Looked at on the next read, not in a second's time. */
void pad_style_used(u32 device)
{
    if (g_used_known && g_used == device) return;
    g_used_known = 1;
    g_used = device;
    g_polls = 0;
}

static void set_style(int style, const char *what, u32 id)
{
    if (style == g_style) return;
    port_log("input: pad style %s (%s, %04x:%04x)\n", kNames[style], what, id ? SDL_GetJoystickVendorForID(id) : 0u,
             id ? SDL_GetJoystickProductForID(id) : 0u);
    g_style = style;
    g_serial++;
}

void pad_style_poll(void)
{
    int n = 0;
    SDL_JoystickID *ids;
    if (g_forced >= 0 || g_polls++ % POLL_EVERY) return;
    if (g_used_known && g_used && !SDL_GetJoystickNameForID(g_used)) g_used_known = 0; /* unplugged */
    if (g_used_known) {
        const char *name = g_used ? SDL_GetJoystickNameForID(g_used) : "the keyboard";
        set_style(g_used && SDL_IsGamepad(g_used) ? style_of(g_used) : 0, name ? name : "?", g_used);
        return;
    }
    ids = SDL_GetGamepads(&n);
    if (ids && n > 0) set_style(style_of(ids[0]), SDL_GetGamepadNameForID(ids[0]), ids[0]);
    else set_style(0, "no gamepad", 0u);
    SDL_free(ids);
}

/* Every read, live or not: a test's swaps, and the game's log says what shows when it changes. */
void pad_style_tick(void)
{
    int now;
    while (g_swap_next < g_swaps && port_vblank_count() >= g_swap[g_swap_next].at) {
        g_forced = g_swap[g_swap_next++].style;
        g_serial++;
    }
    now = pad_style() * 2 + (g_art != 0);
    if (now != g_logged) {
        g_logged = now;
        game_log_line("pad: controller %s, pictures %s, showing %s", kNames[pad_style()], g_art ? "on" : "off", kNames[pad_art_style()]);
    }
}
