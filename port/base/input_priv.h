/* Between input.c (players, script, keyboard, the pads the game reads) and input_device.c (the host's gamepads and
 * joysticks: opening, dropping, rumble, which modern buttons they have). Base-internal. */
#ifndef PORT_INPUT_PRIV_H
#define PORT_INPUT_PRIV_H
#include <SDL3/SDL.h>
#include "base.h"

#define MAX_DEVICES 16 /* open at once: a device for each of 8 players, and idle virtual pads beside them */
#define NO_PLAYER (-1)

typedef struct HostDevice {
    SDL_JoystickID id;
    SDL_Gamepad *pad;  /* a device SDL has a gamepad mapping for, or */
    SDL_Joystick *joy; /* a raw joystick, read with the generic layout */
    int player;        /* 0..7, or NO_PLAYER until it is first used */
    char desc[128];    /* for the scene dump */
    int seen, live;    /* a device reporting buttons held from its first read is not in use until its state changes */
    struct { u32 b; int x, y, l, r; } first;
} HostDevice;

extern HostDevice g_dev[MAX_DEVICES];
extern int g_dev_n;
extern int g_pads_max; /* input_device.c: the players read, --pads N, else game.json pads, else 4 */

int input_sdl_ready(void);              /* SDL's gamepad side is up (0 headless: scripted input only) */
void input_scan_devices(int pads_max);  /* drops unplugged devices; about once a second opens new ones */

#endif
