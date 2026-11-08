/* Twin-stick: in a game's fights the right stick turns and the left stick's sideways direction sidesteps, for a game made
 * for a pad that turns on the d-pad (Input > Controls > Twin-stick; --twin-stick on|off; base.h port_twin_*). The game
 * names, in game.json twin_stick, a function it runs only in a fight and its turn and sidestep buttons (the console's
 * action names); the console calls port_twin_hit when that function is entered.
 *
 * While the function ran within the last NEAR pad reads: the right stick left or right (input.c's RS_ON from the centre,
 * a digital press) presses the turn buttons, and the left stick held sideways past ON presses the sidestep buttons
 * instead of the d-pad's left or right; its up and down still walk. A d-pad direction held wins over the stick on that
 * axis, so the d-pad and the keyboard's arrows still turn as the game made them. When the player has put the right
 * stick's left or right on an action (Input > Controls), the right stick keeps that and does not turn.
 *
 * It is applied as the pad is read, before it is recorded (input_bind.c's input_binds_apply), so a recording holds the
 * buttons the game saw and a replay needs neither the setting nor the fight's timing. On in a play run; a scripted run
 * leaves it off unless --twin-stick on is given, so the scripts written before it still press what they did.
 * Nearest existing: pad_stick.c (the left stick as a second d-pad, applied after the recording, on the key screen's choice). */
#include <string.h>
#include "base.h"
#include "console.h"
#include "host.h"

#define ON 64  /* of 128: the left stick counts as sideways */
#define NEAR 8 /* pad reads since the fight's function ran that still count as in a fight */
#define SIDEWAYS (HOST_PAD_LEFT | HOST_PAD_RIGHT)

u32 port_twin_fn; /* the fight's function (0: the game has none) */
static u32 g_turn[2], g_strafe[2]; /* host buttons: left, right */
static int g_want = -1, g_on, g_fight;
static u32 g_since = NEAR + 1;

/* The default host button of the console's action called name, as a HostPad bit; 0 if none. */
static u32 action_bit(const char *name)
{
    unsigned n;
    const PortAction *a = console_actions(&n);
    for (unsigned i = 0; name && a && i < n; i++)
        if (!strcmp(a[i].name, name)) return input_name_bit(a[i].dflt);
    port_log("twin-stick: the console has no button \"%s\"\n", name ? name : "(none)");
    return 0;
}

int port_twin_option(const char *arg, const char *value)
{
    if (strcmp(arg, "--twin-stick") || !value) return 0;
    g_want = strcmp(value, "off") != 0;
    return 2;
}

/* At the start: the game's fight function and buttons (NULL: none). */
void port_twin_start(const PortTwinStick *t)
{
    port_twin_fn = 0;
    if (t && t->fn) {
        g_turn[0] = action_bit(t->turn[0]), g_turn[1] = action_bit(t->turn[1]);
        g_strafe[0] = action_bit(t->strafe[0]), g_strafe[1] = action_bit(t->strafe[1]);
        if (g_turn[0] && g_turn[1] && g_strafe[0] && g_strafe[1]) port_twin_fn = t->fn;
    }
    g_on = port_twin_fn && (g_want < 0 ? !input_scripted() : g_want);
    port_log("twin-stick: %s\n", !port_twin_fn ? "the game names no fight" : g_on ? "on (in fights)" : "off");
}

void port_twin_hit(void) { g_since = 0; }

int port_twin_available(void) { return port_twin_fn != 0; }
int port_twin_get(void) { return g_want < 0 ? 1 : g_want; } /* the setting, for the menu */
void port_twin_set(int on) { g_want = on != 0, g_on = port_twin_fn && g_want; }

/* The player has put the right stick's left or right on an action. */
static int right_stick_bound(void)
{
    unsigned n;
    console_actions(&n);
    for (unsigned s = 1; s < input_source_count(); s++) {
        const char *name = input_source_name(s);
        if (strcmp(name, "RSLEFT") && strcmp(name, "RSRIGHT")) continue;
        for (unsigned i = 0; i < n; i++)
            if (input_bind_get(i) >> s & 1u) return 1;
    }
    return 0;
}

/* From input_binds_apply, before the recording. */
void port_twin_apply(HostPad pad[HOST_PADS])
{
    int fight = g_since <= NEAR, turn;
    if (g_since <= NEAR) g_since++;
    if (fight != g_fight)
        port_log("twin-stick: %s at vertical blank %u\n", fight ? "a fight (the right stick turns)" : "out of the fight", port_vblank_count());
    g_fight = fight;
    if (!g_on || !fight) return;
    turn = !right_stick_bound();
    for (int p = 0; p < HOST_PADS; p++) {
        HostPad *h = &pad[p];
        u32 dpad = h->buttons & SIDEWAYS;
        if (turn && h->buttons & HOST_PAD_RS_LEFT) h->buttons |= g_turn[0];
        if (turn && h->buttons & HOST_PAD_RS_RIGHT) h->buttons |= g_turn[1];
        if (dpad) continue;
        if (h->x < -ON) h->buttons |= g_strafe[0];
        if (h->x > ON) h->buttons |= g_strafe[1];
        h->x = 0;
    }
}
