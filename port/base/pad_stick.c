/* The left stick as a second d-pad, a choice per player (game.padex.p<player>.lsmove, on the game's key screen; base.h
 * port_pad_stick_*): for a game made for a pad whose stick it never reads. The stick's raw values still go to the game.
 *
 * The directions are worked out after the recording and the replay have had their say, from the stick they hold and the
 * choice they carry (input_ex_assign.c), so a recording keeps the pad as it was and a replay does what the run did.
 *
 * A direction needs ON of 128 from the centre and stays until the stick is back under OFF, so a worn stick's drift never
 * presses anything and a stick resting near the edge doesn't chatter. There are eight sectors: the smaller axis counts when
 * it is DIAG_ON percent of the larger (tan 22.5 degrees), and once it counts, until it is under DIAG_OFF. On each axis the
 * d-pad wins over the stick, so the two never press opposite directions: a d-pad tap against the held stick is that tap.
 * Nearest existing: input.c's RS_ON (the right stick's four directions, a button each and no diagonal rule). */
#include <stdlib.h>
#include "base.h"

#define ON 48
#define OFF 32
#define DIAG_ON 41
#define DIAG_OFF 33
#define HORIZONTAL (HOST_PAD_LEFT | HOST_PAD_RIGHT)
#define VERTICAL (HOST_PAD_UP | HOST_PAD_DOWN)

/* The directions of a stick at x, y (-128..127, y down), given those of the last read. */
u32 port_pad_stick_dirs(int x, int y, u32 last)
{
    int ax = abs(x), ay = abs(y), major = ax > ay ? ax : ay;
    u32 near_x = x < 0 ? HOST_PAD_LEFT : HOST_PAD_RIGHT, near_y = y < 0 ? HOST_PAD_UP : HOST_PAD_DOWN, out = 0;
    if (major < (last ? OFF : ON)) return 0;
    if (ax * 100 >= major * (last & near_x ? DIAG_OFF : DIAG_ON)) out |= near_x;
    if (ay * 100 >= major * (last & near_y ? DIAG_OFF : DIAG_ON)) out |= near_y;
    return out;
}

/* From host_pads_read, on what the replay left: adds each choosing player's stick to its d-pad. */
void port_pad_stick_apply(HostPad pad[HOST_PADS])
{
    static u32 last[HOST_PADS];
    for (int p = 0; p < HOST_PADS; p++) {
        u32 dirs = 0, dpad = pad[p].buttons;
        if (port_pad_ex_assign_get(p, PORT_PAD_EX_STICK_ROW)) dirs = port_pad_stick_dirs(pad[p].x, pad[p].y, last[p]);
        last[p] = dirs;
        if (dpad & HORIZONTAL) dirs &= ~HORIZONTAL;
        if (dpad & VERTICAL) dirs &= ~VERTICAL;
        pad[p].buttons |= dirs;
    }
}
