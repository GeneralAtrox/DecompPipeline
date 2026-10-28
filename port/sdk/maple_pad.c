/* Platform layer: the Maple bus peripherals (Shinobi's pd* calls) on the host. Ports A0, B0, C0 and D0 are standard
 * controllers for players 1..4, fed by runtime/gfx/gfx_input.c (SDL3 gamepads and the keyboard, or a --input script)
 * and sampled once per pdExecPeripheralServer as the console's peripheral server samples the bus once per frame.
 * Player 1's controller is always connected; another is while gfx_input.c gives that player one. Each controller has
 * a vibration pack in its second socket (A2, B2, ..), which drives that player's rumble; A1 is left to the memory
 * card (sdk/bu_vmu.c; --vmu picks the sockets that hold one). Every other port is empty.
 *
 * Each memory card has the VMU's 48x32 LCD, driven as the SDK drives it (Shinobi 1.4's pdVmsLcd*, read from Toy
 * Commander's copy): ready while a standard VMU is in the socket; a write converts the game's bitmap (a byte per dot,
 * dark where bit 3 is set) into the LCD's 192-byte block (a bit per dot, row by row, the first dot in the top bit) in
 * one of four orientations, queues the block write and returns 0 at once. The block reaches the card at the next
 * peripheral server pass, as the console's next Maple transfer carries it; it is logged then and handed to the host's
 * VMU screen (runtime/gfx/gfx_vmu.c) as the dots are seen: a VMU sits in the controller upside down, so the block's
 * first dot is the seen screen's bottom-right one.
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
#define PORT_CARD 1u       /* A1: the memory card */
#define DEV_CONTROLLER 0x001u
#define DEV_MEMORY_CARD 0x00eu /* storage, LCD, clock */
#define DEV_VIBRATION 0x100u
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
#define CARD_SOCKETS 8u         /* A1, A2, B1, .. D2: bit c * 2 + s is connector c's socket s + 1 */
#define LCD_W 48u
#define LCD_H 32u
#define LCD_BYTES (LCD_W * LCD_H / 8u) /* the block one write sends: 192 bytes */
#define LCD_DOT_DARK 0x08u      /* the bit of a bitmap byte the SDK tests */
#define LCD_NOT_READY (-2)
/* The connection direction bytes the devices report (Flycast's controller and VMU send 0) and the SDK's table of
 * the LCD's direction from them (controller's * 4 + card's): 1 is the usual way round, which turns the image (3). */
#define CONNECTOR_DIR 0u
static const u8 g_lcd_direction[16] = {1, 0, 2, 3, 0, 1, 3, 2, 3, 2, 1, 0, 2, 3, 0, 1};

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
static int g_pads_ready;     /* the controllers' records are set up (pdInitPeripheral or the first read) */

typedef struct Lcd {
    u8 block[LCD_BYTES]; /* the last block written */
    int queued;          /* written since the last server pass: not on the card yet */
    u32 writes;
} Lcd;
static Lcd g_lcd[CARD_SOCKETS];

/* The player whose controller has this port as its socket `sub` (PORT_PAD, PORT_VIB), if connected; -1 otherwise. */
static s32 player_at(u32 port, u32 sub)
{
    u32 p = port / PORTS_PER_PAD;
    return port % PORTS_PER_PAD == sub && p < HOST_PADS && (g_connected >> p & 1u) ? (s32)p : -1;
}

/* --vmu: socket names (A1..D2) to a mask of CARD_SOCKETS bits; "none": no card; default A1. */
u32 maple_card_sockets(void)
{
    const char *s = host_arg("--vmu");
    u32 mask = 0;
    if (!s) return 1u;
    for (; s[0] && s[1]; s++)
        if (s[0] >= 'A' && s[0] <= 'D' && (s[1] == '1' || s[1] == '2'))
            mask |= 1u << ((u32)(s[0] - 'A') * 2u + (u32)(s[1] - '1'));
    return mask;
}

/* The card socket (0..CARD_SOCKETS-1) of a port holding a memory card; -1 otherwise. */
static s32 card_at(u32 port)
{
    u32 sub = port % PORTS_PER_PAD, c = port / PORTS_PER_PAD;
    if (sub < 1u || sub > 2u || c >= CARD_SOCKETS / 2u) return -1;
    return (maple_card_sockets() >> (c * 2u + sub - 1u) & 1u) ? (s32)(c * 2u + sub - 1u) : -1;
}

/* A queued LCD block reaches its card: logged (each write, with the block) and shown on the host's VMU screen. */
static void lcd_deliver(u32 card)
{
    Lcd *l = &g_lcd[card];
    u8 seen[LCD_W * LCD_H];
    char hex[LCD_BYTES * 2u + 1u];
    l->queued = 0;
    l->writes++;
    for (u32 i = 0; i < LCD_BYTES; i++) {
        hex[i * 2u] = "0123456789abcdef"[l->block[i] >> 4];
        hex[i * 2u + 1u] = "0123456789abcdef"[l->block[i] & 15u];
        for (u32 b = 0; b < 8u; b++) /* turned: the block's first dot is the last one seen */
            seen[LCD_W * LCD_H - 1u - (i * 8u + b)] = (u8)(l->block[i] >> (7u - b) & 1u);
    }
    hex[LCD_BYTES * 2u] = '\0';
    port_log("maple: %c%u LCD write %u: %s\n", 'A' + card / 2u, 1u + card % 2u, l->writes, hex);
    host_vmu_lcd(card, seen);
}

/* The controllers' records as the bus reports them once the devices are found. */
static void pads_init(void)
{
    for (u32 p = 0; p < HOST_PADS; p++)
        g_maple_pad[p] = (MapleDevice){.support = PAD_BUTTONS | PAD_ANALOG, .off = 0xffffffffu,
                                       .name = "Dreamcast Controller", .info = &g_maple_info_pad};
    g_pads_ready = 1;
}

void pdInitPeripheral(s32 logic, void *recvBuffer, void *sendBuffer)
{
    (void)logic;
    (void)recvBuffer;
    (void)sendBuffer;
    pads_init();
    port_log("maple: controllers on ports A..D for players 1..%u (A always, each with its vibration pack in socket 2),"
             " the memory card in A1\n", HOST_PADS);
}

/* A game can start the peripheral system through an SDK entry the port does not bind (a newer library's variant of
 * pdInitPeripheral with more arguments): the console's bus finds the controllers all the same, so the first server
 * pass or read sets the records up as pdInitPeripheral would. Without this every pad reads as unplugged. */
static void pads_ready(void)
{
    if (g_pads_ready) return;
    pads_init();
    port_log("maple: the game reads the bus without pdInitPeripheral: controllers on ports A..D set up at the first"
             " peripheral read\n");
}

void pdExitPeripheral(void)
{
    for (u32 p = 0; p < HOST_PADS; p++) host_pad_rumble(p, 0, 0);
}

void pdExecPeripheralServer(void)
{
    HostPad h[HOST_PADS];
    u32 was_connected = g_connected;
    pads_ready();
    for (u32 c = 0; c < CARD_SOCKETS; c++)
        if (g_lcd[c].queued) lcd_deliver(c);
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
    pads_ready();
    return (LibPadDevice *)(p >= 0 ? &g_maple_pad[p] : &g_maple_none);
}

/* The device record of a port (the SDK's PDS_PERIPHERALINFO, laid out as MapleInfo): its function bits and product
 * name, blank-padded to 30 characters as the devices report them. A1 holds the memory card sdk/bu_vmu.c serves,
 * each controller's socket 2 its vibration pack; an empty port has type 0. */
void *pdGetPeripheralInfo(u32 port)
{
    static const MapleInfo vmu = {DEV_MEMORY_CARD, {0, 0, 0}, 0, 0, {0, 0}, "Visual Memory                 "};
    static const MapleInfo vib = {DEV_VIBRATION, {0, 0, 0}, 0, 0, {0, 0}, "Puru Puru Pack                "};
    const MapleInfo *info = &g_maple_info_none;
    if (player_at(port, PORT_PAD) >= 0) info = &g_maple_info_pad;
    else if (port == PORT_CARD) info = &vmu;
    else if (player_at(port, PORT_VIB) >= 0) info = &vib;
    return (void *)(uintptr_t)info;
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

/* The SDK's: ready while the port holds a standard VMU (type 0x0e, a 48x32 one-colour LCD). */
s32 pdVmsLcdIsReady(u32 port) { return card_at(port) >= 0; }

/* The SDK's: its table at the controller's direction (of the socket the card is in) and the card's. */
s32 pdVmsLcdGetDirection(u32 port)
{
    (void)port;
    return g_lcd_direction[CONNECTOR_DIR * 4u + CONNECTOR_DIR];
}

/* The SDK's: the bitmap (LCD_W x LCD_H bytes, row by row) into the block, by flag: 0 as it is, 1 mirrored left to
 * right, 2 top to bottom, 3 both (turned). 0 once queued; LCD_NOT_READY without an LCD. */
s32 pdVmsLcdWrite(u32 port, const void *bitmap, u32 flag)
{
    const u8 *dot = (const u8 *)bitmap;
    s32 card = card_at(port);
    if (card < 0) return LCD_NOT_READY;
    Lcd *l = &g_lcd[card];
    for (u32 i = 0; i < LCD_W * LCD_H; i++) {
        u32 x = i % LCD_W, y = i / LCD_W, at;
        if (flag == 1u || flag == 3u) x = LCD_W - 1u - x;
        if (flag == 2u || flag == 3u) y = LCD_H - 1u - y;
        at = y * LCD_W + x;
        if (dot[i] & LCD_DOT_DARK) l->block[at / 8u] |= (u8)(0x80u >> (at % 8u));
        else l->block[at / 8u] &= (u8)~(0x80u >> (at % 8u));
    }
    l->queued = 1;
    return 0;
}
