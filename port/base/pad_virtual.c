/* --virtual-pad NAME (tests only): an SDL virtual gamepad that a test driver presses through shared memory, so a scripted
 * walk reaches the game the way a real controller's presses do: SDL lists the device, input.c opens it as a gamepad,
 * claims it as a player when first used and reads its buttons and sticks through SDL's gamepad mapping, then the
 * assignments, the recording and the game's pad. Nothing is installed (no ViGEm): the pad lives in this process.
 *   NAME is a Windows file mapping the driver makes before the run (Python: mmap.mmap(-1, 64, tagname=NAME)), laid out
 *   as VPadShared below: the driver writes magic, buttons (HostPad bits) and the axes (a trigger at rest is 0); this
 *   writes vblank and reads, the
 *   vertical blank of the pad read just made, so the driver can press by the game's clock.
 * Only with --live-input on in a windowed run (input.c reads no devices otherwise); off unless asked, never in a play run.
 * Nearest existing: input_script.c's --input, which hands its presses to the game after the devices are read, so it
 * cannot show the SDL gamepad path works. */
#include <string.h>
#include <SDL3/SDL.h>
#include <windows.h>
#include "base.h"
#include "console.h"

#define VPAD_MAGIC 0x44415056u /* "VPAD" */

typedef struct VPadShared {
    volatile u32 magic, buttons;
    volatile s16 lx, ly, rx, ry; /* -32768..32767, y down */
    volatile s16 lt, rt;         /* 0..32767 */
    volatile u32 vblank, reads;
} VPadShared;

static char g_name[64];
static VPadShared *g_shm;
static SDL_Joystick *g_joy;
static int g_failed;

static const struct {
    u32 bit;
    SDL_GamepadButton button;
} g_map[] = {
    {HOST_PAD_A, SDL_GAMEPAD_BUTTON_SOUTH},       {HOST_PAD_B, SDL_GAMEPAD_BUTTON_EAST},
    {HOST_PAD_X, SDL_GAMEPAD_BUTTON_WEST},        {HOST_PAD_Y, SDL_GAMEPAD_BUTTON_NORTH},
    {HOST_PAD_START, SDL_GAMEPAD_BUTTON_START},   {HOST_PAD_UP, SDL_GAMEPAD_BUTTON_DPAD_UP},
    {HOST_PAD_DOWN, SDL_GAMEPAD_BUTTON_DPAD_DOWN}, {HOST_PAD_LEFT, SDL_GAMEPAD_BUTTON_DPAD_LEFT},
    {HOST_PAD_RIGHT, SDL_GAMEPAD_BUTTON_DPAD_RIGHT}, {HOST_PAD_LB, SDL_GAMEPAD_BUTTON_LEFT_SHOULDER},
    {HOST_PAD_RB, SDL_GAMEPAD_BUTTON_RIGHT_SHOULDER}, {HOST_PAD_L3, SDL_GAMEPAD_BUTTON_LEFT_STICK},
    {HOST_PAD_R3, SDL_GAMEPAD_BUTTON_RIGHT_STICK}, {HOST_PAD_BACK, SDL_GAMEPAD_BUTTON_BACK},
    {HOST_PAD_GUIDE, SDL_GAMEPAD_BUTTON_GUIDE},
};

int pad_virtual_option(const char *arg, const char *value)
{
    if (strcmp(arg, "--virtual-pad") || !value) return 0;
    strncpy_s(g_name, sizeof g_name, value, _TRUNCATE);
    return 2;
}

/* The pad and the shared block, once: the driver's block first, then the SDL device (SDL is up: input.c's sdl_ready). */
static int ready(void)
{
    if (g_joy) return 1;
    if (g_failed) return 0;
    if (!g_shm) {
        HANDLE h = OpenFileMappingA(FILE_MAP_ALL_ACCESS, FALSE, g_name);
        g_shm = h ? (VPadShared *)MapViewOfFile(h, FILE_MAP_ALL_ACCESS, 0, 0, sizeof(VPadShared)) : NULL;
        if (!g_shm || g_shm->magic != VPAD_MAGIC) {
            port_log("input: --virtual-pad %s: no driver block (error %lu); no virtual pad\n", g_name, GetLastError());
            g_failed = 1;
            return 0;
        }
    }
    {
        SDL_VirtualJoystickDesc d;
        SDL_JoystickID id;
        SDL_INIT_INTERFACE(&d);
        d.type = SDL_JOYSTICK_TYPE_GAMEPAD;
        d.naxes = SDL_GAMEPAD_AXIS_COUNT;
        d.nbuttons = SDL_GAMEPAD_BUTTON_COUNT;
        d.name = "Kit virtual pad (--virtual-pad)";
        id = SDL_AttachVirtualJoystick(&d);
        g_joy = id ? SDL_OpenJoystick(id) : NULL;
        port_log("input: --virtual-pad %s: SDL virtual gamepad %u %s\n", g_name, (unsigned)id, g_joy ? "attached" : SDL_GetError());
        if (!g_joy) g_failed = 1;
    }
    return g_joy != NULL;
}

/* From host_pads_read before the devices are scanned: the driver's buttons and sticks onto the SDL device, and the
 * vertical blank back to the driver. */
void pad_virtual_tick(void)
{
    u32 b;
    if (!g_name[0] || !ready()) return;
    b = g_shm->buttons;
    for (size_t i = 0; i < sizeof g_map / sizeof g_map[0]; i++)
        SDL_SetJoystickVirtualButton(g_joy, (int)g_map[i].button, (b & g_map[i].bit) != 0);
    SDL_SetJoystickVirtualAxis(g_joy, SDL_GAMEPAD_AXIS_LEFTX, g_shm->lx);
    SDL_SetJoystickVirtualAxis(g_joy, SDL_GAMEPAD_AXIS_LEFTY, g_shm->ly);
    SDL_SetJoystickVirtualAxis(g_joy, SDL_GAMEPAD_AXIS_RIGHTX, g_shm->rx);
    SDL_SetJoystickVirtualAxis(g_joy, SDL_GAMEPAD_AXIS_RIGHTY, g_shm->ry);
    /* a trigger is a whole axis on the device: -32768 at rest, so 0..32767 from the driver is spread over it */
    SDL_SetJoystickVirtualAxis(g_joy, SDL_GAMEPAD_AXIS_LEFT_TRIGGER, (Sint16)(g_shm->lt * 2 - 32768));
    SDL_SetJoystickVirtualAxis(g_joy, SDL_GAMEPAD_AXIS_RIGHT_TRIGGER, (Sint16)(g_shm->rt * 2 - 32768));
    SDL_UpdateJoysticks();
    g_shm->vblank = port_vblank_count();
    g_shm->reads++;
}
