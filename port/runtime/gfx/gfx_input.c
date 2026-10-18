/* Host input for the controller on Maple port A (sdk/maple_pad.c): every SDL3 gamepad and joystick and the
 * keyboard, merged (buttons together, the stick and triggers from whichever device moves them most), plus scripted
 * presses for automated runs. Merging keeps an idle virtual pad (Parsec, Steam Input, ViGEm) from hiding the real one.
 *   Gamepad (Xbox layout): A/B/X/Y are the Dreamcast's (the same places), Start, the d-pad, the left stick, LT/RT
 *   as the analog triggers. Keyboard: Z/X/A/S = A/B/X/Y, Enter = Start, the arrows = the d-pad and also the stick
 *   at full deflection (a keyboard has no stick), I/K/J/L = the d-pad alone, W or Space = the R trigger and Q or
 *   Left Shift = the L trigger, fully pressed (driving games usually accelerate on R and brake on L). A game whose
 *   d-pad does something else than steering sets game.json "arrows": "stick" (PORT_ARROWS_STICK): the arrows
 *   then move only the stick.
 *   --input NAME@VBLANK[+N],..   NAME: A B X Y START UP DOWN LEFT RIGHT L R, held from that vertical blank for N
 *                                (default 6); scripted directions move the stick like the arrows. L=V and R=V
 *                                press a trigger part of the way (V 1..255; plain L and R are 255).
 *   A joystick SDL has no gamepad mapping for is read with a generic layout: buttons 0-3 = A/B/X/Y, 4/5 = L/R
 *   fully pressed, 9 (7 on pads with fewer than 10 buttons) = Start, the first hat = the d-pad, axes 0/1 = the stick.
 * Devices are looked for about once a second, so one plugged in later is picked up; each opened is logged.
 * Headless runs (no SDL video) read only the script. Everything runs on the game thread, which pumps the events. */
#include <stdlib.h>
#include <string.h>
#include <SDL3/SDL.h>
#include "port.h"
#include "platform.h"
#include "host.h"
#include "session.h"

#define MAX_SCRIPT 64
#define SCRIPT_HOLD 6u
#define SCRIPT_L 0x40000000u /* scripted triggers: not Dreamcast button bits */
#define SCRIPT_R 0x80000000u
#define DIRS (HOST_PAD_UP | HOST_PAD_DOWN | HOST_PAD_LEFT | HOST_PAD_RIGHT)
#define KEY_PAD 1u   /* the key presses its button */
#define KEY_STICK 2u /* the key moves the stick (directions) */
#if PORT_ARROWS_STICK
#define ARROWS KEY_STICK
#else
#define ARROWS (KEY_PAD | KEY_STICK)
#endif
#define STICK_DEAD 12  /* of 128: a worn host stick's centring noise (the Dreamcast pad reports raw values) */
#define FIND_EVERY 60u /* reads between looks for newly connected devices */
#define MAX_DEVICES 8

typedef struct ScriptPress {
    u32 buttons, at, until, level; /* level: how far a scripted trigger is pressed, 1..255 */
    int logged;
} ScriptPress;

static ScriptPress g_script[MAX_SCRIPT];
static u32 g_script_n;
static int g_sdl = -1; /* -1 not tried, 0 no host devices, 1 gamepads and keyboard */
static u32 g_reads;

typedef struct HostDevice {
    SDL_JoystickID id;
    SDL_Gamepad *pad;  /* a device SDL has a gamepad mapping for, or */
    SDL_Joystick *joy; /* a raw joystick, read with the generic layout */
    char desc[128];    /* for the scene dump */
} HostDevice;

static HostDevice g_dev[MAX_DEVICES];
static int g_dev_n;
static SDL_JoystickID g_failed[MAX_DEVICES]; /* devices that would not open, logged once */
static int g_failed_n;

typedef struct PadState {
    u32 b;
    int x, y, l, r;
} PadState;

static const struct {
    const char *name;
    u32 bit;
} g_names[] = {
    {"A", HOST_PAD_A},       {"B", HOST_PAD_B},         {"X", HOST_PAD_X},   {"Y", HOST_PAD_Y},
    {"START", HOST_PAD_START}, {"UP", HOST_PAD_UP},     {"DOWN", HOST_PAD_DOWN}, {"LEFT", HOST_PAD_LEFT},
    {"RIGHT", HOST_PAD_RIGHT}, {"L", SCRIPT_L},         {"R", SCRIPT_R},
};

static const struct {
    SDL_GamepadButton button;
    u32 bit;
} g_pad_map[] = {
    {SDL_GAMEPAD_BUTTON_SOUTH, HOST_PAD_A},       {SDL_GAMEPAD_BUTTON_EAST, HOST_PAD_B},
    {SDL_GAMEPAD_BUTTON_WEST, HOST_PAD_X},        {SDL_GAMEPAD_BUTTON_NORTH, HOST_PAD_Y},
    {SDL_GAMEPAD_BUTTON_START, HOST_PAD_START},   {SDL_GAMEPAD_BUTTON_DPAD_UP, HOST_PAD_UP},
    {SDL_GAMEPAD_BUTTON_DPAD_DOWN, HOST_PAD_DOWN}, {SDL_GAMEPAD_BUTTON_DPAD_LEFT, HOST_PAD_LEFT},
    {SDL_GAMEPAD_BUTTON_DPAD_RIGHT, HOST_PAD_RIGHT},
};

static const struct {
    SDL_Scancode key;
    u32 bit, how;
} g_key_map[] = {
    {SDL_SCANCODE_Z, HOST_PAD_A, KEY_PAD},         {SDL_SCANCODE_X, HOST_PAD_B, KEY_PAD},
    {SDL_SCANCODE_A, HOST_PAD_X, KEY_PAD},         {SDL_SCANCODE_S, HOST_PAD_Y, KEY_PAD},
    {SDL_SCANCODE_RETURN, HOST_PAD_START, KEY_PAD}, {SDL_SCANCODE_KP_ENTER, HOST_PAD_START, KEY_PAD},
    {SDL_SCANCODE_UP, HOST_PAD_UP, ARROWS},        {SDL_SCANCODE_DOWN, HOST_PAD_DOWN, ARROWS},
    {SDL_SCANCODE_LEFT, HOST_PAD_LEFT, ARROWS},    {SDL_SCANCODE_RIGHT, HOST_PAD_RIGHT, ARROWS},
    {SDL_SCANCODE_I, HOST_PAD_UP, KEY_PAD},        {SDL_SCANCODE_K, HOST_PAD_DOWN, KEY_PAD},
    {SDL_SCANCODE_J, HOST_PAD_LEFT, KEY_PAD},      {SDL_SCANCODE_L, HOST_PAD_RIGHT, KEY_PAD},
    {SDL_SCANCODE_Q, SCRIPT_L, KEY_PAD},           {SDL_SCANCODE_W, SCRIPT_R, KEY_PAD},
    {SDL_SCANCODE_LSHIFT, SCRIPT_L, KEY_PAD},      {SDL_SCANCODE_SPACE, SCRIPT_R, KEY_PAD},
};

static int g_rumble = 1; /* --rumble on|off, and the host's menu */

void input_set_rumble(int on) { g_rumble = on; }

int input_rumble(void) { return g_rumble; }

int input_option(const char *arg, const char *value)
{
    if (!strcmp(arg, "--rumble") && value) {
        g_rumble = strcmp(value, "off") != 0;
        return 2;
    }
    if (strcmp(arg, "--input") || !value) return 0;
    for (const char *p = value; *p && g_script_n < MAX_SCRIPT;) {
        size_t n = strcspn(p, "@,="), name = n;
        u32 bit = 0, level = 255;
        char *end = (char *)p + n;
        for (size_t i = 0; i < sizeof g_names / sizeof g_names[0]; i++)
            if (strlen(g_names[i].name) == name && !_strnicmp(p, g_names[i].name, name)) bit = g_names[i].bit;
        if (*end == '=' && (bit & (SCRIPT_L | SCRIPT_R))) {
            level = (u32)strtoul(end + 1, &end, 10);
            if (!level || level > 255) bit = 0;
        }
        if (bit && *end == '@') {
            u32 at = (u32)strtoul(end + 1, &end, 10), hold = SCRIPT_HOLD;
            if (*end == '+') hold = (u32)strtoul(end + 1, &end, 10);
            g_script[g_script_n].buttons = bit;
            g_script[g_script_n].level = level;
            g_script[g_script_n].at = at;
            g_script[g_script_n++].until = at + hold;
        } else {
            port_log("input: cannot read the --input entry \"%s\"\n", p);
        }
        end += strcspn(end, ",");
        p = *end == ',' ? end + 1 : end;
    }
    return 2;
}

/* The scripted presses held now; trig[0] and trig[1]: how far the scripted L and R triggers are pressed. */
static u32 script_buttons(u32 trig[2])
{
    u32 now = port_vblank_count(), b = 0;
    trig[0] = trig[1] = 0;
    for (u32 i = 0; i < g_script_n; i++) {
        ScriptPress *s = &g_script[i];
        if (now < s->at || now >= s->until) continue;
        b |= s->buttons;
        if ((s->buttons & SCRIPT_L) && s->level > trig[0]) trig[0] = s->level;
        if ((s->buttons & SCRIPT_R) && s->level > trig[1]) trig[1] = s->level;
        if (!s->logged) port_log("input: scripted press %08x at vertical blank %u\n", s->buttons, now);
        s->logged = 1;
    }
    return b;
}

static int sdl_ready(void)
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

static void open_device(SDL_JoystickID id)
{
    HostDevice *d = &g_dev[g_dev_n];
    const char *name = SDL_GetJoystickNameForID(id);
    d->id = id;
    d->joy = NULL;
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
static void scan_devices(void)
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
        if (!known) open_device(ids[i]);
    }
    SDL_free(ids);
}

int input_device_count(void) { return g_dev_n; }

const char *input_device_desc(int i) { return i >= 0 && i < g_dev_n ? g_dev[i].desc : ""; }

static int stick(Sint16 v)
{
    int s = v / 256; /* -128..127 */
    return abs(s) < STICK_DEAD ? 0 : s;
}

static int trigger(Sint16 v) { return v <= 0 ? 0 : (v * 255 + 16383) / 32767; }

static int further(int a, int b) { return abs(b) > abs(a) ? b : a; }

static int joy_button(SDL_Joystick *j, int n, int i) { return i < n && SDL_GetJoystickButton(j, i); }

/* Adds one device to the merged state. */
static void read_device(const HostDevice *d, PadState *s)
{
    int x, y, l, r;
    if (d->pad) {
        for (size_t i = 0; i < sizeof g_pad_map / sizeof g_pad_map[0]; i++)
            if (SDL_GetGamepadButton(d->pad, g_pad_map[i].button)) s->b |= g_pad_map[i].bit;
        x = stick(SDL_GetGamepadAxis(d->pad, SDL_GAMEPAD_AXIS_LEFTX));
        y = stick(SDL_GetGamepadAxis(d->pad, SDL_GAMEPAD_AXIS_LEFTY));
        l = trigger(SDL_GetGamepadAxis(d->pad, SDL_GAMEPAD_AXIS_LEFT_TRIGGER));
        r = trigger(SDL_GetGamepadAxis(d->pad, SDL_GAMEPAD_AXIS_RIGHT_TRIGGER));
    } else {
        static const u32 face[] = {HOST_PAD_A, HOST_PAD_B, HOST_PAD_X, HOST_PAD_Y};
        SDL_Joystick *j = d->joy;
        int nb = SDL_GetNumJoystickButtons(j), axes = SDL_GetNumJoystickAxes(j);
        for (int i = 0; i < 4; i++)
            if (joy_button(j, nb, i)) s->b |= face[i];
        if (joy_button(j, nb, nb >= 10 ? 9 : 7)) s->b |= HOST_PAD_START;
        if (SDL_GetNumJoystickHats(j) > 0) {
            Uint8 h = SDL_GetJoystickHat(j, 0);
            s->b |= (h & SDL_HAT_UP ? HOST_PAD_UP : 0u) | (h & SDL_HAT_DOWN ? HOST_PAD_DOWN : 0u) |
                    (h & SDL_HAT_LEFT ? HOST_PAD_LEFT : 0u) | (h & SDL_HAT_RIGHT ? HOST_PAD_RIGHT : 0u);
        }
        x = axes >= 2 ? stick(SDL_GetJoystickAxis(j, 0)) : 0;
        y = axes >= 2 ? stick(SDL_GetJoystickAxis(j, 1)) : 0;
        l = joy_button(j, nb, 4) ? 255 : 0;
        r = joy_button(j, nb, 5) ? 255 : 0;
    }
    s->x = further(s->x, x);
    s->y = further(s->y, y);
    if (l > s->l) s->l = l;
    if (r > s->r) s->r = r;
}

void host_pad_read(HostPad *pad)
{
    u32 trig[2], b = script_buttons(trig), keys = b & DIRS;
    PadState s = {0};
    if (sdl_ready()) {
        const bool *k = SDL_GetKeyboardState(NULL);
        int alt = (SDL_GetModState() & SDL_KMOD_ALT) != 0; /* Alt+Enter is the host's full-screen switch */
        for (size_t i = 0; i < sizeof g_key_map / sizeof g_key_map[0] && !alt; i++)
            if (k[g_key_map[i].key]) {
                if (g_key_map[i].how & KEY_PAD) b |= g_key_map[i].bit;
                if (g_key_map[i].how & KEY_STICK) keys |= g_key_map[i].bit & DIRS;
                if (g_key_map[i].bit & SCRIPT_L) trig[0] = 255; /* a key presses its trigger fully */
                if (g_key_map[i].bit & SCRIPT_R) trig[1] = 255;
            }
        scan_devices();
        for (int i = 0; i < g_dev_n; i++) read_device(&g_dev[i], &s);
    }
    int x = s.x, y = s.y;
    b |= s.b;
    pad->l = (u8)s.l;
    pad->r = (u8)s.r;
    if (b & SCRIPT_L) pad->l = (u8)(trig[0] > pad->l ? trig[0] : pad->l);
    if (b & SCRIPT_R) pad->r = (u8)(trig[1] > pad->r ? trig[1] : pad->r);
    if (keys & HOST_PAD_LEFT) x = -128;
    if (keys & HOST_PAD_RIGHT) x = 127;
    if (keys & HOST_PAD_UP) y = -128;
    if (keys & HOST_PAD_DOWN) y = 127;
    pad->x = (s8)x;
    pad->y = (s8)y;
    pad->buttons = b & ~(SCRIPT_L | SCRIPT_R);
    session_pad(pad); /* recorded, or the replay's */
}

void host_pad_rumble(u32 strength, u32 ms)
{
    if (!g_rumble) strength = 0;
    if (strength > 0xffffu) strength = 0xffffu;
    for (int i = 0; i < g_dev_n; i++) {
        if (g_dev[i].pad) SDL_RumbleGamepad(g_dev[i].pad, (Uint16)strength, (Uint16)strength, strength ? ms : 0u);
        else SDL_RumbleJoystick(g_dev[i].joy, (Uint16)strength, (Uint16)strength, strength ? ms : 0u);
    }
}
