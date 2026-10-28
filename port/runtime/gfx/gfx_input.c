/* Host input for the controllers on Maple ports A..D (sdk/maple_pad.c), players 1..4: every SDL3 gamepad and
 * joystick and the keyboard, plus scripted presses for automated runs.
 *   Players: a device becomes the next free player the first time it is used (a button, the stick or a trigger),
 *   so an idle virtual pad (Parsec, Steam Input, ViGEm) never takes a player's place. The keyboard is always player 1.
 *   A device unplugged frees its player; the next device used takes the lowest free one. --pads N (1..4, default 4)
 *   caps the players: --pads 1 merges every device into player 1 (buttons together, the stick and triggers from
 *   whichever device moves them most). A player 2..4 with no device and no script has no controller connected.
 *   Gamepad (Xbox layout): A/B/X/Y are the Dreamcast's (the same places), Start, the d-pad, the left stick, LT/RT
 *   as the analog triggers. Keyboard: Z/X/A/S = A/B/X/Y, Enter = Start, the arrows = the d-pad and also the stick
 *   at full deflection (a keyboard has no stick), I/K/J/L = the d-pad alone, W or Space = the R trigger and Q or
 *   Left Shift = the L trigger, fully pressed (driving games usually accelerate on R and brake on L). A game whose
 *   d-pad does something else than steering sets game.json "arrows": "stick" (PORT_ARROWS_STICK): the arrows
 *   then move only the stick.
 *   --input [Pn.]NAME@VBLANK[+N],..   NAME: A B X Y START UP DOWN LEFT RIGHT L R, held from that vertical blank
 *                                for N (default 6); scripted directions move the stick like the arrows. L=V and R=V
 *                                press a trigger part of the way (V 1..255; plain L and R are 255). Pn. (P1..P4)
 *                                is the player (default P1); a player a script names is connected from the start.
 *   A joystick SDL has no gamepad mapping for is read with a generic layout: buttons 0-3 = A/B/X/Y, 4/5 = L/R
 *   fully pressed, 9 (7 on pads with fewer than 10 buttons) = Start, the first hat = the d-pad, axes 0/1 = the stick.
 * Devices are looked for about once a second, so one plugged in later is picked up; each opened is logged.
 * Headless runs (no SDL video) read only the script. Only player 1 is recorded and replayed (session.c).
 * Everything runs on the game thread, which pumps the events. */
#include <stdlib.h>
#include <string.h>
#include <SDL3/SDL.h>
#include "port.h"
#include "platform.h"
#include "host.h"
#include "session.h"

#define MAX_SCRIPT 4096 /* scripted presses kept; later ones are dropped with one log line */
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
#define TRIGGER_USED 64 /* of 255: a trigger pressed this far claims a player */
#define FIND_EVERY 60u /* reads between looks for newly connected devices */
#define MAX_DEVICES 8
#define NO_PLAYER (-1)

typedef struct ScriptPress {
    u32 buttons, at, until, level; /* level: how far a scripted trigger is pressed, 1..255 */
    u32 player;
    int logged;
} ScriptPress;

static ScriptPress g_script[MAX_SCRIPT];
static u32 g_script_n, g_script_dropped;
static u32 g_script_players = 1u; /* bit p: the script names player p + 1 (player 1 is always connected) */
static int g_sdl = -1; /* -1 not tried, 0 no host devices, 1 gamepads and keyboard */
static u32 g_reads;
static int g_pads_max = HOST_PADS; /* --pads */

typedef struct HostDevice {
    SDL_JoystickID id;
    SDL_Gamepad *pad;  /* a device SDL has a gamepad mapping for, or */
    SDL_Joystick *joy; /* a raw joystick, read with the generic layout */
    int player;        /* 0..3, or NO_PLAYER until it is first used */
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

/* One --input entry's optional player prefix "Pn.": returns the player (0..3) and moves *p past it. */
static u32 script_player(const char **p)
{
    const char *s = *p;
    if ((s[0] == 'P' || s[0] == 'p') && s[1] >= '1' && s[1] <= '0' + HOST_PADS && s[2] == '.') {
        *p = s + 3;
        return (u32)(s[1] - '1');
    }
    return 0;
}

int input_option(const char *arg, const char *value)
{
    if (!strcmp(arg, "--rumble") && value) {
        g_rumble = strcmp(value, "off") != 0;
        return 2;
    }
    if (!strcmp(arg, "--pads") && value) {
        int n = atoi(value);
        if (n >= 1 && n <= HOST_PADS) g_pads_max = n;
        else port_log("input: --pads takes 1..%d, not \"%s\"\n", HOST_PADS, value);
        return 2;
    }
    if (strcmp(arg, "--input") || !value) return 0;
    for (const char *p = value; *p;) {
        const char *entry = p;
        u32 player = script_player(&p);
        size_t n = strcspn(p, "@,="), name = n;
        u32 bit = 0, level = 255;
        char *end = (char *)p + n;
        for (size_t i = 0; i < sizeof g_names / sizeof g_names[0]; i++)
            if (strlen(g_names[i].name) == name && !_strnicmp(p, g_names[i].name, name)) bit = g_names[i].bit;
        if (*end == '=' && (bit & (SCRIPT_L | SCRIPT_R))) {
            level = (u32)strtoul(end + 1, &end, 10);
            if (!level || level > 255) bit = 0;
        }
        if (bit && *end == '@' && g_script_n == MAX_SCRIPT) {
            if (!g_script_dropped++)
                port_log("input: --input holds at most %d presses: \"%.*s\" and the ones after it are dropped\n",
                         MAX_SCRIPT, (int)strcspn(entry, ","), entry);
        } else if (bit && *end == '@') {
            u32 at = (u32)strtoul(end + 1, &end, 10), hold = SCRIPT_HOLD;
            if (*end == '+') hold = (u32)strtoul(end + 1, &end, 10);
            g_script[g_script_n].buttons = bit;
            g_script[g_script_n].level = level;
            g_script[g_script_n].at = at;
            g_script[g_script_n].player = player;
            g_script[g_script_n++].until = at + hold;
            g_script_players |= 1u << player;
        } else {
            port_log("input: cannot read the --input entry \"%s\"\n", entry);
        }
        end += strcspn(end, ",");
        p = *end == ',' ? end + 1 : end;
    }
    return 2;
}

/* Player p's scripted presses held now; trig[0] and trig[1]: how far its scripted L and R triggers are pressed. */
static u32 script_buttons(u32 p, u32 trig[2])
{
    u32 now = port_vblank_count(), b = 0;
    trig[0] = trig[1] = 0;
    for (u32 i = 0; i < g_script_n; i++) {
        ScriptPress *s = &g_script[i];
        if (s->player != p || now < s->at || now >= s->until) continue;
        b |= s->buttons;
        if ((s->buttons & SCRIPT_L) && s->level > trig[0]) trig[0] = s->level;
        if ((s->buttons & SCRIPT_R) && s->level > trig[1]) trig[1] = s->level;
        if (!s->logged) port_log("input: scripted press %08x for player %u at vertical blank %u\n", s->buttons, p + 1,
                                 now);
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
    d->player = g_pads_max == 1 ? 0 : NO_PLAYER;
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

/* One device's state now. */
static PadState read_device(const HostDevice *d)
{
    PadState s = {0};
    if (d->pad) {
        for (size_t i = 0; i < sizeof g_pad_map / sizeof g_pad_map[0]; i++)
            if (SDL_GetGamepadButton(d->pad, g_pad_map[i].button)) s.b |= g_pad_map[i].bit;
        s.x = stick(SDL_GetGamepadAxis(d->pad, SDL_GAMEPAD_AXIS_LEFTX));
        s.y = stick(SDL_GetGamepadAxis(d->pad, SDL_GAMEPAD_AXIS_LEFTY));
        s.l = trigger(SDL_GetGamepadAxis(d->pad, SDL_GAMEPAD_AXIS_LEFT_TRIGGER));
        s.r = trigger(SDL_GetGamepadAxis(d->pad, SDL_GAMEPAD_AXIS_RIGHT_TRIGGER));
    } else {
        static const u32 face[] = {HOST_PAD_A, HOST_PAD_B, HOST_PAD_X, HOST_PAD_Y};
        SDL_Joystick *j = d->joy;
        int nb = SDL_GetNumJoystickButtons(j), axes = SDL_GetNumJoystickAxes(j);
        for (int i = 0; i < 4; i++)
            if (joy_button(j, nb, i)) s.b |= face[i];
        if (joy_button(j, nb, nb >= 10 ? 9 : 7)) s.b |= HOST_PAD_START;
        if (SDL_GetNumJoystickHats(j) > 0) {
            Uint8 h = SDL_GetJoystickHat(j, 0);
            s.b |= (h & SDL_HAT_UP ? HOST_PAD_UP : 0u) | (h & SDL_HAT_DOWN ? HOST_PAD_DOWN : 0u) |
                   (h & SDL_HAT_LEFT ? HOST_PAD_LEFT : 0u) | (h & SDL_HAT_RIGHT ? HOST_PAD_RIGHT : 0u);
        }
        s.x = axes >= 2 ? stick(SDL_GetJoystickAxis(j, 0)) : 0;
        s.y = axes >= 2 ? stick(SDL_GetJoystickAxis(j, 1)) : 0;
        s.l = joy_button(j, nb, 4) ? 255 : 0;
        s.r = joy_button(j, nb, 5) ? 255 : 0;
    }
    return s;
}

/* Adds one device's state to a player's. */
static void merge(PadState *into, const PadState *s)
{
    into->b |= s->b;
    into->x = further(into->x, s->x);
    into->y = further(into->y, s->y);
    if (s->l > into->l) into->l = s->l;
    if (s->r > into->r) into->r = s->r;
}

/* Gives a device that is used for the first time the lowest free player (none when all are taken). */
static void claim_player(HostDevice *d, const PadState *s)
{
    u32 taken = 0;
    if (d->player != NO_PLAYER || !(s->b || s->x || s->y || s->l >= TRIGGER_USED || s->r >= TRIGGER_USED)) return;
    for (int i = 0; i < g_dev_n; i++)
        if (g_dev[i].player != NO_PLAYER) taken |= 1u << g_dev[i].player;
    for (int p = 0; p < g_pads_max; p++)
        if (!(taken & (1u << p))) {
            d->player = p;
            port_log("input: device %u is player %d\n", (unsigned)d->id, p + 1);
            return;
        }
}

/* The keyboard's state (player 1). */
static PadState read_keyboard(u32 *keys)
{
    PadState s = {0};
    const bool *k = SDL_GetKeyboardState(NULL);
    if (SDL_GetModState() & SDL_KMOD_ALT) return s; /* Alt+Enter is the host's full-screen switch */
    for (size_t i = 0; i < sizeof g_key_map / sizeof g_key_map[0]; i++)
        if (k[g_key_map[i].key]) {
            if (g_key_map[i].how & KEY_PAD) s.b |= g_key_map[i].bit;
            if (g_key_map[i].how & KEY_STICK) *keys |= g_key_map[i].bit & DIRS;
            if (g_key_map[i].bit & SCRIPT_L) s.l = 255; /* a key presses its trigger fully */
            if (g_key_map[i].bit & SCRIPT_R) s.r = 255;
        }
    s.b &= ~(SCRIPT_L | SCRIPT_R);
    return s;
}

/* One player's pad from its devices' merged state, its script and (player 1) the keyboard's directions. */
static void make_pad(u32 p, const PadState *s, u32 keys, HostPad *pad)
{
    u32 trig[2], b = script_buttons(p, trig) | s->b;
    int x = s->x, y = s->y;
    keys |= b & DIRS & ~s->b; /* scripted directions move the stick */
    pad->l = (u8)s->l;
    pad->r = (u8)s->r;
    if (b & SCRIPT_L) pad->l = (u8)(trig[0] > pad->l ? trig[0] : pad->l);
    if (b & SCRIPT_R) pad->r = (u8)(trig[1] > pad->r ? trig[1] : pad->r);
    if (keys & HOST_PAD_LEFT) x = -128;
    if (keys & HOST_PAD_RIGHT) x = 127;
    if (keys & HOST_PAD_UP) y = -128;
    if (keys & HOST_PAD_DOWN) y = 127;
    pad->x = (s8)x;
    pad->y = (s8)y;
    pad->buttons = b & ~(SCRIPT_L | SCRIPT_R);
}

u32 host_pads_read(HostPad pad[HOST_PADS])
{
    PadState s[HOST_PADS] = {0};
    u32 keys = 0, connected = g_script_players;
    if (sdl_ready()) {
        s[0] = read_keyboard(&keys);
        scan_devices();
        for (int i = 0; i < g_dev_n; i++) {
            PadState one = read_device(&g_dev[i]);
            claim_player(&g_dev[i], &one);
            if (g_dev[i].player == NO_PLAYER) continue;
            merge(&s[g_dev[i].player], &one);
            connected |= 1u << g_dev[i].player;
        }
    }
    for (u32 p = 0; p < HOST_PADS; p++) make_pad(p, &s[p], p == 0 ? keys : 0u, &pad[p]);
    session_pad(&pad[0]); /* recorded, or the replay's */
    return connected & ((1u << g_pads_max) - 1u);
}

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
