/* game_log.c: the game log. What happened in a play run, in words, in port.log beside the kit's own lines, so that
 * whoever reads a bug report starts from the log and does not have to ask the player what they did.
 *   --game-log on|off        the host's play runs turn it on; scripted and headless check runs have it off
 *   --game-log-every N       a full snapshot of the game's fields every N vertical blanks (default 600, the map may set it)
 *   --game-log-save N        the session recording is saved to autosave.ptrec beside the log every N vertical blanks
 *                            (default 3600; 0: never), so even a run killed from outside leaves one
 * Each line: [wall clock +seconds since start vVERTICAL_BLANK] text. The lines:
 *   P1 press A, P2 stick left   the game pads (all four), keyboard: press Z (P1 A)   the keys that act as a pad
 *   menu: Display > Full screen  window: lost the focus   the host's menu and window
 *   game_mode: DEMO (1) -> BATTLE (2)   a field of the game's scene map (scene_dump.json, "log": true) changed;
 *                                a field that changes too fast is summarised instead of listed
 *   snapshot: ...               every field of the map and its counts, at the interval
 *   END: ...                    the last line of a run that ended in a way the process could see: the window closed by
 *                               the user, the game's own exit, the build's stop message, a FAULT with its registers, an
 *                               unhandled exception, a CRT abort. A log without it ended abruptly (the process was killed
 *                               from outside, or died in a way nothing can catch): the next launch says so in the log it
 *                               moved aside (host/host_data.cpp) and in the new one.
 * After a fault or any exit with a non-zero code the session recording is saved as crash-<time>.ptrec and the log
 * prints the replay commands: to the last presented frame before the end, and on through it. The game only reads: the
 * guest values by the scene map's read-only access, nothing written into the game, the frames or the recording. */
#ifndef GAME_LOG_H
#define GAME_LOG_H

#ifdef __cplusplus
extern "C" {
#endif

int game_log_option(const char *arg, const char *value); /* 0 if not ours */
void game_log_start(void);                               /* after port_log_open; the first lines */
int game_log_on(void);
void game_log_line(const char *fmt, ...);                /* a stamped line (an action or an event); off: nothing */
void game_log_poll(unsigned frame);                      /* the console's frame, after each presented frame */
void game_log_previous(const char *note, const char *recording); /* host: how the previous run ended, if abruptly */
/* The end: how is the text after "END: ". code 0 is a clean end (the autosave is deleted); anything else keeps
 * a recording: save 1 writes crash-<time>.ptrec and prints the replay commands (from the game's own thread, with its
 * memory in a usable state), save 0 leaves the autosave. Only the first call writes. */
void game_log_end(const char *how, int code, int save);

#ifndef __cplusplus
#include "base.h"
#include "console.h"
void game_log_pads(const HostPad *pad, unsigned connected); /* gfx_input.c, each pad read: the changes, per player */
#define GAME_LOG_TRIGGER_L 0x40000000u /* gfx_input.c's keyboard and script trigger bits: not HOST_PAD_* bits */
#define GAME_LOG_TRIGGER_R 0x80000000u
void game_log_key(unsigned slot, int down, const char *name, unsigned bit); /* gfx_input.c, each keyboard read: key changes */
const char *game_log_button(unsigned bit);                  /* "A", "START"...: a HOST_PAD_* bit */
#endif

#ifdef __cplusplus
}
#endif

#endif
