/* Platform layer: the Maple bus peripherals (Shinobi's pd* calls) on the host. Ports A0, B0, C0 and D0 are standard
 * controllers for players 1..4, fed by runtime/gfx/gfx_input.c (SDL3 gamepads and the keyboard, or a --input script)
 * and sampled once per pdExecPeripheralServer as the console's peripheral server samples the bus once per frame.
 * Player 1's controller is always connected; another is while gfx_input.c gives that player one. Each controller has
 * a vibration pack in its second socket (A2, B2, ..), which drives that player's rumble; A1 is left to the memory
 * card (sdk/bu_vmu.c), whose LCD is not shown. Every other port is empty.
 *
 * The record pdGetPeripheral returns is laid out as the console's (MapleDevice): games read it by offset and follow
 * its info pointer to the device type word. Pointers are host pointers, which the 32-bit port shares with the game.
 *
 * Needs from the game's headers: the names below as the game's registry has them, pdGetPeripheral returning the
 * game's record type for it (LibPadDevice), and PDS_VIBPARAM {unit, flag, power (-7..7), freq, inc}. */
#include "game.h"
#include "platform.h"

#define PORTS_PER_PAD 6u   /* a connector's ports: the controller (A0), then its expansion sockets */
#define PORT_PAD 0u        /* A0 */
#define PORT_VIB 2u        /* A2: the second expansion socket (the first holds the memory card) */
#define DEV_CONTROLLER 0x001u
#define PAD_BUTTONS 0x000006feu /* B A Start, the d-pad, Y X */
#define PAD_ANALOG 0x000f0000u  /* the analog R and L triggers and the stick's two axes */
/* The triggers as buttons too: the library adds them to the button words, and games bind actions to them (Toy
 * Commander's control presets, its menu pointer). A trigger counts as down from half its travel; the SDK's own
 * threshold is not in the headers read here. */
#define PAD_DIGITAL_R 0x00010000u
#define PAD_DIGITAL_L 0x00020000u
#define TRIGGER_DOWN 128u
#define VIB_OK 0
#define VIB_NO_VIBRATOR (-2)
#define VIB_CONTINUOUS 0x01u    /* PDS_VIBPARAM.flag: keep going until pdVibMxStop */
#define VIB_POWER_MAX 7
#define VIB_PULSE_MS 250u       /* one non-continuous vibration */
#define VIB_HOLD_MS 10000u      /* a continuous one, renewed by the game's next start */

typedef struct MapleInfo {
    u32 type;              /* device type bits; 0: nothing connected */
    u32 reserved[3];
    u8 is_root, area_code, connector[2];
    char product[32];
} MapleInfo;

typedef struct MapleDevice {
    u32 id;
    u32 support;           /* 0x04: the buttons and axes the device has */
    u32 on, off;           /* 0x08, 0x0c: buttons held, buttons not held */
    u32 press, release;    /* 0x10, 0x14: since the previous server pass */
    u16 r, l;              /* 0x18, 0x1a: analog triggers, 0..255 */
    s16 x1, y1, x2, y2;    /* 0x1c..0x22: sticks, -128..127 (y negative up) */
    const char *name;      /* 0x24 */
    void *extend;          /* 0x28 */
    u32 old;               /* 0x2c */
    const MapleInfo *info; /* 0x30 */
} MapleDevice;
_Static_assert(sizeof(MapleDevice) == 0x34, "the console's peripheral record is 0x34 bytes");

static const MapleInfo g_maple_info_pad = {DEV_CONTROLLER, {0, 0, 0}, 1, 0, {0, 0}, "Dreamcast Controller"};
static const MapleInfo g_maple_info_none = {0, {0, 0, 0}, 0, 0, {0, 0}, ""};
static MapleDevice g_maple_pad[HOST_PADS];
static MapleDevice g_maple_none = {.off = 0xffffffffu, .name = "", .info = &g_maple_info_none};
static u32 g_connected = 1u; /* bit p: player p + 1 has a controller */

/* The player whose controller has this port as its socket `sub` (PORT_PAD, PORT_VIB), if connected; -1 otherwise. */
static s32 player_at(u32 port, u32 sub)
{
    u32 p = port / PORTS_PER_PAD;
    return port % PORTS_PER_PAD == sub && p < HOST_PADS && (g_connected >> p & 1u) ? (s32)p : -1;
}

void pdInitPeripheral(s32 logic, void *recvBuffer, void *sendBuffer)
{
    (void)logic;
    (void)recvBuffer;
    (void)sendBuffer;
    for (u32 p = 0; p < HOST_PADS; p++)
        g_maple_pad[p] = (MapleDevice){.support = PAD_BUTTONS | PAD_ANALOG, .off = 0xffffffffu,
                                       .name = "Dreamcast Controller", .info = &g_maple_info_pad};
    port_log("maple: controllers on ports A..D for players 1..%u (A always, each with its vibration pack in socket 2),"
             " the memory card in A1\n", HOST_PADS);
}

void pdExitPeripheral(void)
{
    for (u32 p = 0; p < HOST_PADS; p++) host_pad_rumble(p, 0, 0);
}

void pdExecPeripheralServer(void)
{
    HostPad h[HOST_PADS];
    u32 was_connected = g_connected;
    g_connected = host_pads_read(h) | 1u;
    if (g_connected != was_connected) port_log("maple: controllers connected %x (bit 0: port A)\n", g_connected);
    for (u32 p = 0; p < HOST_PADS; p++) {
        MapleDevice *d = &g_maple_pad[p];
        u32 was = d->on;
        if (!(g_connected >> p & 1u)) h[p] = (HostPad){0}; /* unplugged: nothing held when it comes back */
        d->on = (h[p].buttons & PAD_BUTTONS) | (h[p].r >= TRIGGER_DOWN ? PAD_DIGITAL_R : 0u) |
                (h[p].l >= TRIGGER_DOWN ? PAD_DIGITAL_L : 0u);
        d->off = ~d->on;
        d->press = d->on & ~was;
        d->release = was & ~d->on;
        d->r = h[p].r;
        d->l = h[p].l;
        d->x1 = h[p].x;
        d->y1 = h[p].y;
    }
}

LibPadDevice *pdGetPeripheral(u32 port)
{
    s32 p = player_at(port, PORT_PAD);
    return (LibPadDevice *)(p >= 0 ? &g_maple_pad[p] : &g_maple_none);
}

s32 pdVibMxIsReady(u32 port) { return player_at(port, PORT_VIB) >= 0; }

s32 pdVibMxStart(u32 port, const PDS_VIBPARAM *param)
{
    s32 power, p = player_at(port, PORT_VIB);
    if (p < 0) return VIB_NO_VIBRATOR;
    power = param->power < 0 ? -param->power : param->power;
    if (power > VIB_POWER_MAX) power = VIB_POWER_MAX;
    host_pad_rumble((u32)p, (u32)power * 0xffffu / VIB_POWER_MAX,
                    (param->flag & VIB_CONTINUOUS) ? VIB_HOLD_MS : VIB_PULSE_MS);
    return VIB_OK;
}

s32 pdVibMxStop(u32 port)
{
    s32 p = player_at(port, PORT_VIB);
    if (p < 0) return VIB_NO_VIBRATOR;
    host_pad_rumble((u32)p, 0, 0);
    return VIB_OK;
}

/* No VMU LCD anywhere: the game never finds one ready, so it never writes or turns one. */
s32 pdVmsLcdIsReady(u32 port)
{
    (void)port;
    return 0;
}

s32 pdVmsLcdGetDirection(u32 port)
{
    (void)port;
    return 0;
}

s32 pdVmsLcdWrite(u32 port, const void *bitmap, u32 flag)
{
    (void)port;
    (void)bitmap;
    (void)flag;
    return -2; /* no LCD connected */
}
