/* Seats: which of a game's player slots a person plays. Slot s (0..7) is host player s + 1's seat; a seat nobody holds
 * is the game's to give to its computer player (the user's rule, 2026-11-03: a slot is the computer's unless a pad is
 * there for it). For a game whose matches have more slots than its own pad layer has players (players 5..8, which no
 * pad port carries), and just as well for its slots 0..3.
 *
 * The game's part: at the point it sets a match up it asks which of its slots are held (port_seats_claim) and makes the
 * others computer players; then, every tick after its own pad read, it takes each claimed seat's pad (port_seat_read)
 * where it has no pad port to read. Nothing here knows a game, a console or what a match is.
 *
 * Who holds a seat is input.c's players, unchanged: a device becomes the next free player the first time it is used (a
 * button, the stick or a trigger), and a player a script names is connected from the start. So a pad holds its seat
 * once it has been pressed at any time before the claim (on the game's menus, say), and a pad plugged in and never
 * touched leaves its seat to the computer. Claiming a seat for every device plugged in was not done: an idle virtual
 * pad (Parsec, Steam Input, ViGEm) would take a slot and stand still in it all match, which is what input.c's
 * first-use rule exists to stop, and devices are scanned about once a second, at times no recording holds.
 *
 * Seats are claimed, not followed. A pad first used after the claim takes no slot from the computer until the next
 * claim; a claimed pad unplugged during the match keeps its seat with nothing held, and plays again when it is back.
 *
 * Replays: every answer comes from the frame's host_pads_read through port_pad_player, whose pads and connected bits
 * are the recording's in a replay (session_other.c records players 2..8), never from the devices plugged in now. So a
 * claim made at the same point of play gives a replay the recorded run's seats, and nothing is added to the recording.
 * Call both on the game's tick, after its pad read; never inside an in-between frame. */
#include "base.h"
#include "game_log.h"

static u32 g_claimed; /* bit s: slot s was held at the claim that last asked about it */

u32 port_seats_claim(u32 slots)
{
    u32 held = 0;
    slots &= (1u << HOST_PADS) - 1u;
    for (int s = 0; s < HOST_PADS; s++)
        if ((slots >> s & 1u) && port_pad_player(s, NULL)) held |= 1u << s;
    g_claimed = (g_claimed & ~slots) | held;
    game_log_line("seats: of slots %#x, %#x are held by a player (bit 0: slot 1); the rest are the computer's",
                  (unsigned)slots, (unsigned)held);
    return held;
}

int port_seat_claimed(int slot) { return slot >= 0 && slot < HOST_PADS && (g_claimed >> slot & 1u); }

int port_seat_read(int slot, PortSeat *seat)
{
    const PortSeat none = {0};
    HostPad pad = {0};
    int on = port_seat_claimed(slot) && port_pad_player(slot, &pad);
    if (!seat) return on;
    *seat = none;
    if (on) {
        seat->pad = pad;
        seat->l_down = (u8)(pad.l >= PORT_SEAT_TRIGGER_DOWN);
        seat->r_down = (u8)(pad.r >= PORT_SEAT_TRIGGER_DOWN);
    }
    return on;
}
