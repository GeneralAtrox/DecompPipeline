/* The host's gamepads and joysticks for input.c (split from it at the 400-line cap, no change in behaviour): SDL's
 * gamepad side, the open devices (looked for about once a second, so one plugged in later is picked up; each opened
 * is logged, one that will not open is logged once), rumble, and which modern buttons a player's controllers have.
 * Which player a device is, and reading it, stay in input.c. */
#include <stdio.h>
#include "console.h"
#include "host.h"
#include "input_priv.h"
#if __has_include("port_config.h")
#include "port_config.h" /* game.json pads */
#endif
#ifndef PORT_PADS
#define PORT_PADS HOST_GAME_PADS
#endif

#define FIND_EVERY 60u /* reads between looks for newly connected devices */

HostDevice g_dev[MAX_DEVICES];
int g_dev_n;
int g_pads_max = PORT_PADS; /* --pads (input.c) overrides it */
static SDL_JoystickID g_failed[MAX_DEVICES]; /* devices that would not open, logged once */
static int g_failed_n;
static int g_sdl = -1; /* -1 not tried, 0 no host devices, 1 gamepads and keyboard */
static u32 g_reads;
static int g_rumble = 1; /* --rumble on|off, and the host's menu */

void input_set_rumble(int on) { g_rumble = on; }

int input_rumble(void) { return g_rumble; }

int input_sdl_ready(void)
{
    if (g_sdl < 0) {
        g_sdl = SDL_WasInit(SDL_INIT_VIDEO) != 0 && SDL_InitSubSystem(SDL_INIT_GAMEPAD);
        if (!g_sdl) port_log("input: no host devices (headless or no SDL gamepad support): scripted input only\n");
    }
    return g_sdl;
}

static int device_connected(const HostDevice *d)
{
    return d->pad ? SDL_GamepadConnected(d->pad) : SDL_JoystickConnected(d->joy);
}

static void open_device(SDL_JoystickID id, int pads_max)
{
    HostDevice *d = &g_dev[g_dev_n];
    const char *name = SDL_GetJoystickNameForID(id);
    d->id = id;
    d->joy = NULL;
    d->player = pads_max == 1 ? 0 : NO_PLAYER;
    d->seen = d->live = 0;
    if ((d->pad = SDL_IsGamepad(id) ? SDL_OpenGamepad(id) : NULL) == NULL) d->joy = SDL_OpenJoystick(id);
    if (!d->pad && !d->joy) {
        if (g_failed_n < MAX_DEVICES) {
            g_failed[g_failed_n++] = id;
            port_log("input: cannot open \"%s\": %s\n", name ? name : "?", SDL_GetError());
        }
        return;
    }
    snprintf(d->desc, sizeof d->desc, "%s \"%s\" (%04x:%04x%s)",
             d->pad ? "gamepad" : "joystick with no gamepad mapping (generic layout)", name ? name : "?",
             SDL_GetJoystickVendorForID(id), SDL_GetJoystickProductForID(id),
             SDL_IsJoystickVirtual(id) ? ", virtual" : "");
    port_log("input: %s, device %u\n", d->desc, (unsigned)id);
    g_dev_n++;
}

/* Drops disconnected devices; every FIND_EVERY reads opens any gamepad or joystick not open yet. */
void input_scan_devices(int pads_max)
{
    for (int i = 0; i < g_dev_n;) {
        HostDevice *d = &g_dev[i];
        if (device_connected(d)) {
            i++;
            continue;
        }
        port_log("input: device %u disconnected\n", (unsigned)d->id);
        if (d->pad) SDL_CloseGamepad(d->pad);
        else SDL_CloseJoystick(d->joy);
        *d = g_dev[--g_dev_n];
    }
    if (g_reads++ % FIND_EVERY) return;
    int n = 0;
    SDL_JoystickID *ids = SDL_GetJoysticks(&n);
    if (g_reads == 1) port_log("input: %d gamepad or joystick device(s) connected\n", ids ? n : 0);
    for (int i = 0; ids && i < n && g_dev_n < MAX_DEVICES; i++) {
        int known = 0;
        for (int k = 0; k < g_dev_n; k++) known |= g_dev[k].id == ids[i];
        for (int k = 0; k < g_failed_n; k++) known |= g_failed[k] == ids[i];
        if (!known) open_device(ids[i], pads_max);
    }
    SDL_free(ids);
}

int input_device_count(void) { return g_dev_n; }

const char *input_device_desc(int i) { return i >= 0 && i < g_dev_n ? g_dev[i].desc : ""; }

void host_pad_rumble(u32 player, u32 strength, u32 ms)
{
    if (!g_rumble) strength = 0;
    if (strength > 0xffffu) strength = 0xffffu;
    for (int i = 0; i < g_dev_n; i++) {
        if (g_dev[i].player != (int)player) continue;
        if (g_dev[i].pad) SDL_RumbleGamepad(g_dev[i].pad, (Uint16)strength, (Uint16)strength, strength ? ms : 0u);
        else SDL_RumbleJoystick(g_dev[i].joy, (Uint16)strength, (Uint16)strength, strength ? ms : 0u);
    }
}

/* Which of the modern buttons (HOST_PAD_MODERN bits) the controllers of player `player` (0..7) have: a gamepad's by what SDL
 * reports (the right stick as its two axes), the keyboard's E and R (player 1, in a live run). A raw joystick without a
 * gamepad mapping has none. */
u32 port_pad_ex_present(int player)
{
    static const struct {
        SDL_GamepadButton button;
        u32 bit;
    } m[] = {{SDL_GAMEPAD_BUTTON_LEFT_SHOULDER, HOST_PAD_LB}, {SDL_GAMEPAD_BUTTON_RIGHT_SHOULDER, HOST_PAD_RB},
             {SDL_GAMEPAD_BUTTON_LEFT_STICK, HOST_PAD_L3},    {SDL_GAMEPAD_BUTTON_RIGHT_STICK, HOST_PAD_R3},
             {SDL_GAMEPAD_BUTTON_BACK, HOST_PAD_BACK},        {SDL_GAMEPAD_BUTTON_GUIDE, HOST_PAD_GUIDE}, {SDL_GAMEPAD_BUTTON_MISC1, HOST_PAD_SHARE}};
    u32 have = 0;
    if (player < 0 || player >= HOST_PADS || !input_live() || !input_sdl_ready()) return 0u;
    if (player == 0) have |= HOST_PAD_LB | HOST_PAD_RB;
    for (int i = 0; i < g_dev_n; i++) {
        if (g_dev[i].player != player || !g_dev[i].pad) continue;
        for (size_t k = 0; k < sizeof m / sizeof m[0]; k++)
            if (SDL_GamepadHasButton(g_dev[i].pad, m[k].button)) have |= m[k].bit;
        if (SDL_GamepadHasAxis(g_dev[i].pad, SDL_GAMEPAD_AXIS_RIGHTX) && SDL_GamepadHasAxis(g_dev[i].pad, SDL_GAMEPAD_AXIS_RIGHTY))
            have |= HOST_PAD_RS_UP | HOST_PAD_RS_DOWN | HOST_PAD_RS_LEFT | HOST_PAD_RS_RIGHT;
    }
    return have;
}
