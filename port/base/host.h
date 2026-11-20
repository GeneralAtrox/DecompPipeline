/* The desktop host (C++20, base/host_*.cpp): the program's entry, the window and menu bar, its data folder, the saved
 * settings and the crash reporter, laid out after Lodoss's port (MIT, the user's own). What it needs of the console
 * is console.h; this header is what the base's C side may call of it. */
#ifndef PORT_HOST_H
#define PORT_HOST_H

#include <stdbool.h> /* input_kbm_read: SDL's keyboard state */
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
void host_window_title_extra(const char *extra);             /* the title bar's " - extra" (a game's screen, the level editor); any thread */
const char *host_window_state(void); /* "full screen", "focused", "not focused", "covered" or "minimized" (for the log) */

/* host_online.cpp: a console with online play (PORT_CONSOLE_ONLINE) says a match is on (hosting 1: this copy hosts) and what
 * the Online menu's first line shows; until the run ends the base's entries that would change the game for both players
 * are locked (the console's own through their available()). */
void host_online_match(int hosting, const char *status);
/* a console whose online play is in the game's own menus: the game again with --net value and the extra arguments
 * (space-separated), at once; 1 when it started (the console then ends this run). The saved --net choice back to off. */
int host_online_restart(const char *value, const char *extra);
void host_online_forget(void);

/* host_online.cpp: this program again with args (space-separated "--option value" pairs) in place of its own, the new
 * run waiting for this one to end; this one closes. 0 if it could not start (logged). */
int port_start_again(const char *args);

/* host_data.cpp: the data folder (settings, saves, logs, scene dumps) */
const char *host_data_folder(void);

/* input.c: the player's assignment of host buttons to the console's actions (console.h console_actions): the Input > Controls
 * menu reads and changes it, and the settings keep it as bind-<name>. */
unsigned input_source_count(void);              /* the host buttons an action can take; 0 is "none" */
const char *input_source_name(unsigned source);
int input_bind_option(const char *arg, const char *value); /* --bind-NAME SOURCE (input_bind.c); 0 if not ours */
#define SCRIPT_L GAME_LOG_TRIGGER_L /* scripted triggers: not Dreamcast button bits (game_log.h) */
#define SCRIPT_R GAME_LOG_TRIGGER_R
/* scripted left stick alone, at full deflection: no button, bits a HostPad never has (input.c make_pad clears them; the
 * last is the Dreamcast pad's unused bit 15, since HOST_PAD_SHARE took 0x04000000) */
#define SCRIPT_LS_UP 0x08000000u
#define SCRIPT_LS_DOWN 0x10000000u
#define SCRIPT_LS_LEFT 0x20000000u
#define SCRIPT_LS_RIGHT 0x00008000u
#define SCRIPT_LS (SCRIPT_LS_UP | SCRIPT_LS_DOWN | SCRIPT_LS_LEFT | SCRIPT_LS_RIGHT)
int input_script_option(const char *arg, const char *value); /* --input (input_script.c); 0 if not ours */
u32 input_script_buttons(u32 player, u32 trig[2]);          /* the scripted presses held now; trig: L and R levels */
u32 input_script_players(void);                             /* bit p: the script names player p + 1 */
/* input_kbm.c: the keyboard and mouse layout (player 1). input_key_code (input.c): a key's code by name, 0 if none. */
typedef struct KbmPad {
    u32 b, dirs;      /* buttons; the stick's directions held */
    int l, r;         /* triggers, 0 or 255 */
    int x, y;         /* the stick while dirs holds its direction (ramped, game.json key_ramp) */
    int rx, ry, look; /* the mouse look as the right stick; look: it is off the middle */
    int any;          /* something is held */
} KbmPad;
int input_key_code(const char *name);
int input_kbm_option(const char *arg, const char *value); /* --kbm-SOURCE TARGETS, --kbm-inject; 0 if not ours */
int input_kbm_injected(void);
void input_kbm_read(const bool *keys, KbmPad *out); /* keys: SDL's keyboard state, or NULL for --kbm-inject */
void input_kbm_mouse_button(int which, int down);   /* 0 left, 1 middle, 2 right */
void input_kbm_mouse_motion(float dx, float dy);
void input_kbm_mouse_wheel(int notches);            /* up positive */
void input_kbm_mouse_position(float x, float y);    /* the pointer in the game's 640x480 screen (the menu pointer) */
void input_kbm_focus_lost(void);
void input_kbm_focus_gained(void);
void input_kbm_menu_open(int open);                 /* the kit's menu is open: no mouse steer, the pointer let go */
int input_kbm_menu_is_open(void);                   /* a titlebar menu is open (native or the slim bar's) */
int input_kbm_capture_wanted(void);                 /* the window keeps the pointer (relative mode): a middle drag, or
                                                       mouse steer on (base.h input_kbm_mouse_steer), focused, menu shut */
enum { KBM_STEER_ON, KBM_STEER_SENSITIVITY, KBM_STEER_INVERT }; /* Input > Keyboard and mouse: mouse steer's settings */
int input_kbm_steer_get(int which);
void input_kbm_steer_set(int which, int value);     /* sensitivity 1..10 */
const char *input_kbm_steer_key(int which);         /* its settings key: kbm-mouse-steer, -sensitivity, -invert */
unsigned input_kbm_count(void);                     /* Input > Keyboard and mouse: the sources */
const char *input_kbm_name(unsigned i);
unsigned input_kbm_target_count(void);              /* target 0 is "none" */
const char *input_kbm_target_name(unsigned t);
u32 input_kbm_get(unsigned i);                      /* bit t: target t */
void input_kbm_toggle(unsigned i, unsigned t);
void input_kbm_reset(void);                         /* back to the game's layout */
const char *input_kbm_text(unsigned i, char *buf, size_t cap); /* the settings file's words */
int input_scripted(void);                       /* input.c: a script drives the pad (a scripted run) */
u32 input_name_bit(const char *name);           /* input.c: the HostPad bit of a button name ("LB"); 0 if none */
u32 input_button_bit(const char *name);         /* input_script.c: as --input names it, the console's names first; 0 if none */
u32 autoload_buttons(u32 *own);                /* autoload.c: player 1's presses; *own 1 while it drives a load */
u32 input_bind_get(unsigned action);            /* the sources action takes now (bit s: source s) */
void input_bind_toggle(unsigned action, unsigned source); /* a source on or off it; 0 ("none") clears it */
const char *input_bind_text(unsigned action, char *buf, size_t cap); /* the settings file's words */
void input_binds_apply(HostPad pad[HOST_PADS]); /* the assignments, before the pad is recorded */

/* host_text.cpp: typing and pasting into a console's own text field (digits . : - Backspace Enter Esc; Ctrl+V, Shift+Insert) */
void host_text_capture(int on);             /* the field is open (keys are queued) or shut */
void host_text_key(int key, int mod);       /* host_window.cpp: an SDL key went down */
int host_text_take(void);                   /* the next key: a character, '\b', '\n', 27 (Esc), 22 (paste); -1 none */
int host_text_clipboard(char *out, int cap); /* the clipboard's text (or --clipboard-test's next part); 0 if none */
void host_text_test(const char *clip);      /* --clipboard-test TEXT ('|' between pastes): the real clipboard is not read */
/* host_text.cpp: the game's own text field (native_game.h pg_native_text): UTF-8 typed, pasted or injected, queued */
int host_text_full_on(void);                /* the game's field is open: Esc and the layout's keys are the field's */
void host_text_input(const char *utf8);     /* host_mouse.cpp: SDL's text input */
void host_text_put(const char *text, int n); /* --kbm-inject TEXT= (UTF-8, or the ANSI code page's) */
int host_text_drain(char *out, int cap);    /* input.c, each pad read: the queue's whole characters; the bytes */
void host_text_sync(void *sdl_window);      /* SDL's text input started or stopped as the field is open or shut */
/* input_kbm.c, input_kbm_mouse.c: the layout's keys while the game's text field is open; the mouse given to the game */
void input_kbm_text_mode(int on);           /* on: the layout's keys press nothing */
int input_kbm_game_mouse(PgNativeMouse *out); /* each pad read: taken, the moves cleared; 1 in PG_MOUSE_POINTER or LOOK */
int input_kbm_game_look(void);              /* PG_MOUSE_LOOK and captured: the window keeps the pointer */
int input_kbm_game_captured(void);
void input_kbm_game_release(void);          /* Alt held, a menu, the focus lost: the player has the mouse */

/* scene_dump.c: "Copy agent prompt" (the menu bar's icon, Help menu, F9) after the next presented frame */
void scene_dump_request(void);
/* host_menu.cpp: --menu-pick FRAME:Menu>Label[>Choice] (repeatable, up to 8) picks a console's or the game's own menu
 * entry (console_menu_extra, game_menu_extra) at presented frame FRAME, as a click on it does, with or without a window:
 * a scripted walk of the game's menu. port_menu_pick_poll is called once per presented frame. */
int port_menu_pick_option(const char *arg, const char *value);
void port_menu_pick_poll(u32 frame);
int port_menu_drain(int *tag, int *index);  /* input.c, each pad read: the next pick of a tagged entry (host_menu_extra.cpp) */

#ifdef __cplusplus
}
#endif

#endif
