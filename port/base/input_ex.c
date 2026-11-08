/* The buttons older consoles had no place for, as a game's own code asks for them: held and newly pressed per player, which
 * of them the controller in hand has, and what its maker calls them. Split from input.c, which reads the devices and
 * calls input_ex_note once per read; base.h port_pad_ex_*. */
#include <string.h>
#include "base.h"
#include "host.h"

static u32 g_held[HOST_PADS], g_pressed[HOST_PADS];

/* From host_pads_read, after the script, the recording and the replay have had their say: bits 16..31 of each pad. */
void input_ex_note(const HostPad pad[HOST_PADS])
{
    for (u32 p = 0; p < HOST_PADS; p++) {
        u32 now = pad[p].buttons & HOST_PAD_MODERN;
        g_pressed[p] |= now & ~g_held[p];
        g_held[p] = now;
    }
}

u32 port_pad_ex_held(int player) { return player >= 0 && player < HOST_PADS ? g_held[player] : 0u; }

/* Pressed since the last call for this player (so a screen polled slower than the pad misses none). */
u32 port_pad_ex_pressed(int player)
{
    u32 b;
    if (player < 0 || player >= HOST_PADS) return 0u;
    b = g_pressed[player];
    g_pressed[player] = 0u;
    return b;
}

const char *port_pad_ex_name(u32 bit, int player)
{
    static const struct {
        u32 bit;
        const char *generic, *xbox, *playstation;
    } n[] = {
        {HOST_PAD_LB, "LB", "LB", "L1"},          {HOST_PAD_RB, "RB", "RB", "R1"},
        {HOST_PAD_L3, "Left stick click", "LS", "L3"}, {HOST_PAD_R3, "Right stick click", "RS", "R3"},
        {HOST_PAD_BACK, "Back", "View", "Share"}, {HOST_PAD_GUIDE, "Guide", "Xbox", "PS"},
        {HOST_PAD_RS_UP, "Right stick up", "RS up", "R-stick up"},
        {HOST_PAD_RS_DOWN, "Right stick down", "RS down", "R-stick down"},
        {HOST_PAD_RS_LEFT, "Right stick left", "RS left", "R-stick left"},
        {HOST_PAD_RS_RIGHT, "Right stick right", "RS right", "R-stick right"},
    };
    int style = pad_style();
    (void)player; /* one style for every pad: the one last used */
    for (size_t i = 0; i < sizeof n / sizeof n[0]; i++)
        if (n[i].bit == bit) return style == 1 ? n[i].xbox : style == 2 ? n[i].playstation : n[i].generic;
    return "";
}
