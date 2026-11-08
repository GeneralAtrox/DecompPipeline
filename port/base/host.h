/* The desktop host (C++20, base/host_*.cpp): the program's entry, the window and menu bar, its data folder, the saved
 * settings and the crash reporter, laid out after Lodoss's port (MIT, the user's own). What it needs of the console
 * is console.h; this header is what the base's C side may call of it. */
#ifndef PORT_HOST_H
#define PORT_HOST_H

#include <stddef.h>
#include "console.h"

#ifdef __cplusplus
extern "C" {
#endif

/* host_phangrade.cpp: Phangrade's game volume (PHANGRADE_SETTINGS), before the sound device opens */
void host_phangrade_start(void);

/* host_window.cpp: the SDL3 window (the console's presenter opens it unless --headless) and its menu bar
 * (host_menu.cpp). gfx_pump (gfx.h) is there too. Options: --window full|fit|Nx|WxH, --fps-counter on|off,
 * --pause-unfocused on|off, --mute-unfocused on|off. */
int host_window_option(const char *arg, const char *value); /* the arguments taken, or 0 */
int host_window_open(const char *title, unsigned *w, unsigned *h, int *fullscreen); /* pixels; 0 on failure */
void gfx_pump(void);                                         /* window events: closing, resizing, full screen, F9, pausing while unfocused */
/* host_window.cpp: --present-thread (runtime/frame_extra.c). The thread that shows in-between frames and the game's thread
 * share the device context: each holds this lock around its use. It does nothing until gfx_lock_on. The lock nests. */
void gfx_lock_on(void);
void gfx_lock(void);
void gfx_unlock(void);
void gfx_lock_present_thread(void); /* called by that thread, once */
int gfx_on_present_thread(void);
void gfx_lock_waits(unsigned *n, unsigned long long *us); /* the game's thread's waits for the lock so far, in us */
int gfx_lock_wanted(void);          /* the game's thread is waiting for the lock: that thread lets it have it */
void *host_window_hwnd(void);
void host_window_frame(void);                                /* a frame was presented (the frame-rate counter) */
const char *host_window_state(void); /* "full screen", "focused", "not focused", "covered" or "minimized" (for the log) */

/* host_online.cpp: a console with online play (PORT_CONSOLE_ONLINE) says a match is on (hosting 1: this copy hosts) and what
 * the Online menu's first line shows; until the run ends the base's entries that would change the game for both players
 * are locked (the console's own through their available()). */
void host_online_match(int hosting, const char *status);

/* host_data.cpp: the data folder (settings, saves, logs, scene dumps) */
const char *host_data_folder(void);

/* input.c: the player's assignment of host buttons to the console's actions (console.h console_actions): the Input > Controls
 * menu reads and changes it, and the settings keep it as bind-<name>. */
unsigned input_source_count(void);              /* the host buttons an action can take; 0 is "none" */
const char *input_source_name(unsigned source);
int input_bind_option(const char *arg, const char *value); /* --bind-NAME SOURCE (input_bind.c); 0 if not ours */
#define SCRIPT_L GAME_LOG_TRIGGER_L /* scripted triggers: not Dreamcast button bits (game_log.h) */
#define SCRIPT_R GAME_LOG_TRIGGER_R
#define SCRIPT_LS_UP 0x04000000u /* scripted left stick alone, at full deflection: no button (HOST_PAD_MODERN's free bits) */
#define SCRIPT_LS_DOWN 0x08000000u
#define SCRIPT_LS_LEFT 0x10000000u
#define SCRIPT_LS_RIGHT 0x20000000u
#define SCRIPT_LS (SCRIPT_LS_UP | SCRIPT_LS_DOWN | SCRIPT_LS_LEFT | SCRIPT_LS_RIGHT)
int input_script_option(const char *arg, const char *value); /* --input (input_script.c); 0 if not ours */
u32 input_script_buttons(u32 player, u32 trig[2]);          /* the scripted presses held now; trig: L and R levels */
u32 input_script_players(void);                             /* bit p: the script names player p + 1 */
int input_scripted(void);                       /* input.c: a script drives the pad (a scripted run) */
u32 input_name_bit(const char *name);           /* input.c: the HostPad bit of a button name ("LB"); 0 if none */
u32 input_button_bit(const char *name);         /* input_script.c: as --input names it, the console's names first; 0 if none */
u32 autoload_buttons(u32 *own);                /* autoload.c: player 1's presses; *own 1 while it drives a load */
u32 input_bind_get(unsigned action);            /* the sources action takes now (bit s: source s) */
void input_bind_toggle(unsigned action, unsigned source); /* a source on or off it; 0 ("none") clears it */
const char *input_bind_text(unsigned action, char *buf, size_t cap); /* the settings file's words */
void input_binds_apply(HostPad pad[HOST_PADS]); /* the assignments, before the pad is recorded */

/* scene_dump.c: "Copy agent prompt" (the menu bar's icon, Help menu, F9) after the next presented frame */
void scene_dump_request(void);

#ifdef __cplusplus
}
#endif

#endif
