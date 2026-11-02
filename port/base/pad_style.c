/* The style of controller the player holds, for a texture pack's variants (pvr/pvr_pack.c): a pack's index.txt line
 * "KEY FILE xbox" or "KEY FILE playstation" replaces the texture only while that style is in use, so a game's
 * controller pictures can follow the pad on the desk. The style is 0 dreamcast (the game's own art, the default), 1 xbox, 2
 * playstation.
 *   --pad-style auto|dreamcast|xbox|playstation   auto (the default): the type SDL reports for the first connected
 *                        gamepad, looked at about once a second; only a run that reads real input does (gfx_live.c),
 *                        so a check run, a replay or a headless run never has a style; the others fix it.
 * Polled from host_pads_read (the game thread, which owns SDL's devices). */
#include <string.h>
#include <SDL3/SDL.h>
#include "base.h"

#define POLL_EVERY 60u /* pad reads: about a second */

static const char *const kNames[] = {"dreamcast", "xbox", "playstation"};
static int g_forced = -1;     /* --pad-style other than auto */
static volatile int g_style;  /* auto's answer */
static volatile u32 g_serial; /* changes with the style: the renderer decodes its textures again */
static u32 g_polls;

int pad_style_option(const char *arg, const char *value)
{
    if (strcmp(arg, "--pad-style") || !value) return 0;
    g_forced = -1;
    for (int i = 0; i < 3; i++)
        if (!strcmp(value, kNames[i])) g_forced = i;
    g_serial++;
    return 2;
}

int pad_style(void) { return g_forced >= 0 ? g_forced : g_style; }
u32 pad_style_serial(void) { return g_serial; }

static int style_of(SDL_GamepadType t)
{
    switch (t) {
    case SDL_GAMEPAD_TYPE_XBOX360:
    case SDL_GAMEPAD_TYPE_XBOXONE: return 1;
    case SDL_GAMEPAD_TYPE_PS3:
    case SDL_GAMEPAD_TYPE_PS4:
    case SDL_GAMEPAD_TYPE_PS5: return 2;
    default: return 0;
    }
}

void pad_style_poll(void)
{
    int n = 0, style = 0;
    SDL_JoystickID *ids;
    if (g_forced >= 0 || g_polls++ % POLL_EVERY) return;
    ids = SDL_GetGamepads(&n);
    if (ids && n > 0) style = style_of(SDL_GetGamepadTypeForID(ids[0]));
    if (style != g_style) {
        port_log("input: pad style %s (%s)\n", kNames[style], n > 0 ? SDL_GetGamepadNameForID(ids[0]) : "no gamepad");
        g_style = style;
        g_serial++;
    }
    SDL_free(ids);
}
