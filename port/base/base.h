/* The port base: what every console kit shares, console-free (README.md "The port base"). Nothing in base/ includes
 * a console's own headers (port.h, platform.h, sat.h, rd_rt.h, tc_rt.h) or names a console, a game or a guest address;
 * what a base file needs from the machine it runs is declared in console.h, and the kit defines it. tests/test_port_base.py
 * fails when that stops being true. */
#ifndef PORT_BASE_H
#define PORT_BASE_H

#include <stdint.h>
#include <stdio.h>
#include "exit_codes.h" /* PORT_EXIT_*: the exit codes every console ends a run with */

/* The kits' judged headers (rd_rt.h, tc_rt.h) declare these same names; an identical typedef is allowed twice in C11,
 * and every kit compiles at /std:c11. */
typedef uint8_t u8;
typedef uint16_t u16;
typedef uint32_t u32;
typedef int8_t s8;
typedef int16_t s16;
typedef int32_t s32;
typedef uint64_t u64;
typedef int64_t s64;
typedef float f32;

#ifdef __cplusplus
extern "C" {
#endif

/* port_log.c: one log, stdout and the log file */
extern FILE *g_port_log;
void port_log_open(const char *path);
void port_log(const char *fmt, ...);
void port_log_early(const char *fmt, ...); /* port_log, and again at the top of every log opened later: for lines
                                              written before the game's log is open (settings.txt's) */
void port_log_flush(void);
const char *port_log_path(void); /* the log file, "" if none */
void port_exit(const char *why, int code); /* the console's (it stops its own threads), calls base_exit_report first */

/* clock.c: the clock, the command line and the save files (Windows API), the same for every console. */
#define VIRT_HZ 46875000u /* --fixed-clock's virtual units a second: 60 Hz, 50 Hz and the SH-4's TMU divide it */
typedef struct HostTime {
    u16 year, month, day, hour, minute, second, day_of_week; /* day_of_week: 0 = Sunday */
} HostTime;
u64 host_ticks(u32 hz);       /* ticks at hz since the first call (the performance counter) */
void host_local_time(HostTime *t); /* the game's clock: recorded, or the replay's (session.c) */
void host_sleep_ms(u32 ms);
void host_set_fixed_clock(int on); /* --fixed-clock: virtual time, the same run every time */
int host_fixed_clock(void);
void host_set_fast_until(u32 tick); /* --fast-until N: --fixed-clock until tick N, then real time */
u32 host_fast_until(void);
void host_fast_begin(void); /* frames the player does not see run on the virtual clock, without waiting ... */
void host_fast_end(void);   /* ... then real time again from the time reached (both nothing under --fixed-clock) */
/* A console's pacing loop (its wait for the next tick) moves the virtual clock through these. */
u64 *host_virt_ptr(void);        /* the virtual time (port_extra_state saves it around an in-between frame) */
void host_virt_to_tick(void);    /* --fixed-clock: to the next tick boundary */
void host_tick_real(void);       /* real time: the tick boundary just reached is now */
void host_clear_fixed(void);     /* --fast-until reached: real time from here */
typedef int (*PaceIdle)(u64 deadline_us); /* something to do in the wait; 0: nothing now */
void pace_nap_until(u64 deadline_us, int extra); /* pace.c: a short sleep toward the deadline (host_ticks at 1 MHz); extra: 1 ms at most */
int pace_option(const char *arg, const char *value); /* pace.c: --catch-up on|off; returns the arguments used */
void pace_next_tick(u32 hz, PaceIdle idle);      /* to the next 1/hz s boundary of the real clock; then host_tick_real */
void pace_wait_us(u64 us);                       /* this long on the real clock */
void pace_sleep_us(u64 us);                      /* a high-resolution sleep of about this long, no spin */
u32 host_tick_phase(int look);   /* how far into the tick the clock is, 0..65535 (65536: late) */
void host_set_args(int argc, char **argv); /* the command line, for host_arg */
char *const *host_args(int *argc);         /* that list (the host's: defaults, settings, command line) */
const char *host_arg(const char *name);    /* the value after the option name ("--vmu"), the last one wins; NULL if none */
void host_set_save_dir(const char *dir);   /* --save-dir */
int host_save_read(const char *name, void *buf, u32 size);        /* 1: read, exactly size bytes; 0: missing or not size */
int host_save_write(const char *name, const void *buf, u32 size); /* 1: written (replacing it whole); 0: failed */

/* The pad every kit hands its game: the Dreamcast controller's layout, which the other consoles' pads fit (their
 * shoulder buttons are the digital L and R, bits 11 and 12 of base_pad_buttons, set beside the analog triggers).
 * The base reads every button the host controller has (input.c); a console says which of them its game sees. */
#define HOST_PADS 8      /* the host controllers the base reads: players 1..8 (base/input.c) */
#define HOST_GAME_PADS 4 /* a game's own pad layer: players 1..4 (a Dreamcast's four ports); 5..8: port_pad_player */
#define HOST_PAD_C 0x00001u
#define HOST_PAD_B 0x00002u
#define HOST_PAD_A 0x00004u
#define HOST_PAD_START 0x00008u
#define HOST_PAD_UP 0x00010u
#define HOST_PAD_DOWN 0x00020u
#define HOST_PAD_LEFT 0x00040u
#define HOST_PAD_RIGHT 0x00080u
#define HOST_PAD_Z 0x00100u
#define HOST_PAD_Y 0x00200u
#define HOST_PAD_X 0x00400u
/* The buttons older consoles had no place for (input.c reads them all; a console's game sees them only through an
 * action, console.h console_actions). High bits, so a recording and the Dreamcast's pad (which masks them) are unchanged. */
#define HOST_PAD_LB 0x00010000u
#define HOST_PAD_RB 0x00020000u
#define HOST_PAD_L3 0x00040000u
#define HOST_PAD_R3 0x00080000u
#define HOST_PAD_BACK 0x00100000u
#define HOST_PAD_GUIDE 0x00200000u
#define HOST_PAD_RS_UP 0x00400000u
#define HOST_PAD_RS_DOWN 0x00800000u
#define HOST_PAD_RS_LEFT 0x01000000u
#define HOST_PAD_RS_RIGHT 0x02000000u
#define HOST_PAD_SHARE 0x04000000u /* SDL's MISC1: Share (Xbox Series), the microphone button (DualSense), Capture */
#define HOST_PAD_MODERN 0xffff0000u
/* The right stick is the mouse's (input_kbm.c: the middle button held while the mouse moves; kept until a middle click).
 * A bit no device or script has (Share is 0x04000000, the scripted left stick host.h SCRIPT_LS_* its own), so a recording
 * made before it has it clear. Set on player 1's pad before it is recorded. A kit line before 2.10.0 had it at
 * 0x04000000: there a replay reads it as Share, which presses nothing unless the player gave Share an action. */
#define HOST_PAD_MOUSE_LOOK 0x40000000u
typedef struct HostPad {
    u32 buttons;
    u8 l, r;   /* triggers, 0..255 */
    s8 x, y;   /* stick, -128..127; y is negative up */
} HostPad;
u32 host_pads_read(HostPad pad[HOST_PADS]); /* samples the host now, once per game input poll; bit p of the result:
                                               player p + 1 has a controller (player 1 always), up to port_pads_max */
/* After host_pads_read: player's right stick, -128..127 each way (y negative up), 0 at rest; recorded per player, so a
 * replay gives it back. A scripted RS direction holds it all the way. The game reads it through its own hook. */
void host_pad_right_stick(u32 player, s8 *x, s8 *y);
/* input.c: players 5..8 for a game whose own pad layer has players 1..4. After the frame's host_pads_read: player
 * (0..7)'s pad as that read left it (recorded, or the replay's) and 1 while that player has a controller; 0 and a zeroed
 * pad otherwise. port_pads_max: the players the run reads, --pads N, else game.json "pads", else 4 (then players 5..8
 * are never connected and a run reads, records and logs what it did before there were eight). */
int port_pad_player(int player, HostPad *pad);
int port_pads_max(void);
/* seat.c: a game's player slots as seats. Slot s (0..7) is player s + 1's; a seat nobody holds is the game's computer
 * player's. claim: at the point the game sets a match up, which of `slots` (bit s: slot s) a player holds, by
 * port_pad_player then (a device is a player from its first use, a script's players from the start); returned and kept
 * for those slots until the next claim that asks about them. claimed: 1 for a slot so held. read: each tick after the
 * game's pad read, 1 and a claimed seat's pad; 0 and nothing held for a computer's slot or a pad unplugged since. All
 * from the frame's host_pads_read, so a replay gets its recording's seats; not inside an in-between frame. */
#define PORT_SEAT_TRIGGER_DOWN 128u /* a trigger is down as a button from half its travel (as sdk/maple_pad.c's ports) */
typedef struct PortSeat {
    HostPad pad;       /* the frame's pad: the buttons, the stick and the analog triggers */
    u8 l_down, r_down; /* the triggers as buttons */
} PortSeat;
u32 port_seats_claim(u32 slots);
int port_seat_claimed(int slot);
int port_seat_read(int slot, PortSeat *seat);
/* Mouse steer (input_kbm_steer.c). On, the mouse moving with the middle button up turns player 1's left stick (moved
 * right: x positive; pushed forward: up, y negative, as a stick pushed up; Input > Keyboard and mouse > Invert mouse Y
 * flips it), added to the stick keys' and set before the pad is recorded, while Mouse steering is on (on at first), the
 * window has the focus and the kit's menu is shut; the window keeps the pointer meanwhile. Off at start. A game calls it
 * every frame (cheap; the same value again changes nothing): on while the mouse should steer (flying, say), else off. */
void input_kbm_mouse_steer(int on);
/* Menu pointer (input_kbm_pointer.c, contract 2.12.0). A game calls input_kbm_menu_pointer every frame a menu of entries
 * is up (NULL, or count 0, when none is), with each entry's rectangle in its 640x480 screen and the entry selected now.
 * Then the mouse picks entries by player 1's pad: the pointer moved onto an entry steps the selection there (UP/DOWN, or
 * LEFT/RIGHT with horizontal; the shorter way round with wrap), a left click on an entry does the same and then presses
 * its press, else confirm (0: A), once the selection is that entry, the wheel steps one entry a notch instead of its layout targets; the
 * right button and the keys stay the layout's. Set before the pad is recorded, so a replay needs nothing. While it is up
 * mouse steer waits and the pointer is shown. Off at start, and when the game stops giving it: a game that never calls
 * it is unchanged. An entry with w or h 0 is not picked (one the game skips or has locked). */
#define PORT_MENU_POINTER_MAX 32
typedef struct PortMenuPointer {
    int count;      /* entries, up to PORT_MENU_POINTER_MAX */
    int selected;   /* the entry selected now, 0..count-1; -1: none (the pointer then moves nothing) */
    int horizontal; /* 1: LEFT/RIGHT step the selection; 0: UP/DOWN */
    int wrap;       /* 1: a step past the last entry comes to the first (the game's menu wraps) */
    u32 confirm;    /* the host pad's button a click presses on its entry (HOST_PAD_*); 0: A */
    u32 press[PORT_MENU_POINTER_MAX]; /* per entry, instead of confirm (an option row's next value, say); 0: confirm */
    s16 rect[PORT_MENU_POINTER_MAX][4]; /* x, y, w, h in the 640x480 screen; w or h 0: not picked */
} PortMenuPointer;
void input_kbm_menu_pointer(const PortMenuPointer *menu);
/* The pad as the console's actions (console.h console_actions): bit i is set while the host button assigned to action i
 * is held (the assignment is the player's, Input > Controls, kept in the settings; a scripted run uses the defaults). */
u32 host_actions(const HostPad *pad);
/* pad_style.c: --pad-style auto|dreamcast|xbox|playstation; the style of controller held, for a texture pack's
 * variants (pvr/pvr_pack.c). pad_style_serial changes with it. pad_style_poll: from host_pads_read, game thread. */
int pad_style_option(const char *arg, const char *value);
int pad_style(void); /* 0 dreamcast (the game's own art), 1 xbox, 2 playstation */
u32 pad_style_serial(void);
void pad_style_poll(void);
void pad_style_tick(void); /* every read, live or not: a test's --pad-style swaps, and the game's log */
void pad_style_used(u32 device); /* a press on this SDL device (0: the keyboard) */
int pad_art_on(void);          /* --pad-art, the Input menu's Controller pictures */
void pad_art_set(int on);
int pvr_pack_has_style(int style); /* pvr/pvr_pack.c: a pack in use has pictures for this style */
int pad_art_style(void);       /* the style a texture pack shows: pad_style, or 0 with the pictures off */
int input_live(void); /* live.c: --live-input: the real pads are read (a replay or a check reads none) */
int input_option(const char *arg, const char *value);      /* --input, --bind-NAME; 0 if not ours */
int input_live_option(const char *arg, const char *value); /* --live-input on|off; 0 if not ours */
/* input_ex.c, input.c: the modern buttons for a game's own screens (its Key Configuration), player 0..7 (0..3: the game's ports).
 * held / pressed: HOST_PAD_MODERN bits, merged keyboard (E, R) and gamepads and carried by --input, recordings and replays
 * (zero in a run that scripts none). pressed: new since the last call for that player. present: the bits the controller
 * in hand has (zero without a live run). name: its maker's label for one bit, by pad_style(). */
u32 port_pad_ex_held(int player);
u32 port_pad_ex_pressed(int player);
u32 port_pad_ex_present(int player);
const char *port_pad_ex_name(u32 bit, int player);
void input_ex_note(const HostPad pad[HOST_PADS]); /* host_pads_read only */
/* input_ex_assign.c: a game's own key screen gives these buttons the game's actions (numbered from 1 by the game; 0 is
 * unassigned, as every row starts). Rows: LB, RB, the stick clicks, Back, Guide, the right stick's four directions, Share.
 * load: once at start, from the settings or the recording being replayed. set: stays saved at once. codes: the action
 * codes (codes[i] is action i + 1's) of the rows this player holds now, ORed; 0 when none is held or assigned. */
#define PORT_PAD_EX_ROWS 11u
#define PORT_PAD_EX_SHARE_ROW 10u /* offered while port_pad_ex_share_on: --pad-share, a port_switch */
#define PORT_PAD_EX_STICK_ROW PORT_PAD_EX_ROWS /* _get and _set: 1, the left stick moves (pad_stick.c) */
u32 port_pad_ex_row_bit(unsigned row);
const char *port_pad_ex_row_name(unsigned row, int player); /* for a key screen; good until the next call */
void port_pad_ex_assign_load(unsigned actions);
int port_pad_ex_assign_get(int player, unsigned row);
int port_pad_ex_assign_loaded(void);           /* 1 once the game loaded them (menu_data.c writes only settings before) */
const char *port_pad_ex_row_key(unsigned row); /* the row's word in its setting game.padex.p<N>.<word> */
void port_pad_ex_assign_set(int player, unsigned row, int action);
u32 port_pad_ex_assign_codes(int player, const u32 *codes, unsigned actions);
int port_pad_ex_share_on(void);
/* port_switch.c: --NAME on|off (or settings NAME=off) when asked; otherwise on in play, off in a check run (--lockstep,
 * --fixed-clock), and a replay as its recording was made (the session mark NAME). For a change a recording would show. */
int port_switch(const char *name, const char *label);
/* pad_stick.c: the left stick as a second d-pad, by the row above. */
u32 port_pad_stick_dirs(int x, int y, u32 last);
void port_pad_stick_apply(HostPad pad[HOST_PADS]); /* host_pads_read only */
/* pad_twin.c: in a game's fights the right stick turns and the left stick sidesteps (Input > Controls > Twin-stick). The
 * game's fight function and its turn and sidestep buttons (console action names, left then right): game.json twin_stick. */
typedef struct PortTwinStick {
    u32 fn;
    const char *turn[2], *strafe[2];
} PortTwinStick;
extern u32 port_twin_fn;        /* the console calls port_twin_hit when the game enters it (0: none) */
void port_twin_start(const PortTwinStick *t); /* at the start; NULL: the game has none */
void port_twin_hit(void);
void port_twin_apply(HostPad pad[HOST_PADS]); /* input_binds_apply only */
int port_twin_option(const char *arg, const char *value); /* --twin-stick on|off; 0 if not ours */
/* pad_virtual.c: --virtual-pad NAME, a test driver's SDL virtual gamepad pressed through shared memory (tests only). */
int pad_virtual_option(const char *arg, const char *value); /* 0 if not ours */
void pad_virtual_tick(void); /* host_pads_read only, in a live run */
int port_twin_available(void);  /* the game names a fight (the menu entry) */
int port_twin_get(void);        /* the setting, for the menu */
void port_twin_set(int on);
/* pad_ex_page.c: the page of a game's key screen holding those rows. */
#define PORT_PAD_PAGE_UP 1u
#define PORT_PAD_PAGE_DOWN 2u
#define PORT_PAD_PAGE_LEFT 4u
#define PORT_PAD_PAGE_RIGHT 8u
#define PORT_PAD_PAGE_OK 16u
#define PORT_PAD_PAGE_BACK 32u
#define PORT_PAD_PAGE_OUT_UP 2   /* port_pad_page_press in a list: UP at the first row (or BACK) left it, */
#define PORT_PAD_PAGE_OUT_DOWN 3 /* DOWN at the last row left it */
void port_pad_page_init(const char *const *choices, unsigned actions); /* loads what is kept */
void port_pad_page_enter(void);                                        /* the screen opens */
int port_pad_page_showing(int player);
void port_pad_page_open(int player);
void port_pad_page_open_at(int player, unsigned row);
void port_pad_page_list(int on); /* the rows follow the screen's own in one list: no wrap, see PORT_PAD_PAGE_OUT_* */
int port_pad_page_is_stick(unsigned row);
unsigned port_pad_list_top(unsigned top, unsigned at, unsigned shown); /* the first row shown so row `at` is in view */
int port_pad_page_press(int player, u32 press);
unsigned port_pad_page_rows(void);
unsigned port_pad_page_cursor(int player);
const char *port_pad_page_row_name(int player, unsigned row);
const char *port_pad_page_row_value(int player, unsigned row, int *set);
void port_pad_page_reset(int player);  /* the screen's RESET, CANCEL and leaving with OK */
void port_pad_page_cancel(int player);
void port_pad_page_keep(void);
/* coop_join.c: a co-op session's join and pick screen and who plays (game.json coop). press: PORT_PAD_PAGE_* bits and
 * PORT_COOP_START; returns 1 when the session starts, -1 when the opener closes the screen. line: what to draw for a pad,
 * returns its state (0 out, 1 choosing, 2 ready). players: the session's count (0: none); port/pick: player i's (0 is
 * the one who opened the screen). */
#define PORT_COOP_START 64u
int port_coop_max(void);
int port_coop_enabled(void);
int port_coop_join_open(int opener, const char *const *names, unsigned choices, int shared);
int port_coop_join_showing(void);
int port_coop_join_press(int player, u32 press);
int port_coop_join_line(int player, char *out, unsigned cap);
int port_coop_players(void);
int port_coop_port(int i);
int port_coop_pick(int i);
void port_coop_end(void);
/* host_settings.cpp: a small per-game store in the port's settings file, apart from the game's save. Keys start with "game."
 * (others are refused); unset, or in a run that reads no settings, you get `def`. */
int port_setting_get_int(const char *key, int def);
void port_setting_set_int(const char *key, int value);
void port_setting_set(const char *key, const char *value); /* a menu-style choice kept for the next start (host_settings.cpp) */
int port_setting_get_str(const char *key, char *out, int cap); /* a game's short text ("game.NAME"): 1 when there is one */
void port_setting_set_str(const char *key, const char *value);
unsigned port_settings_changes(void); /* times the settings file changed this run: re-read a kept game.NAME when it moves */
/* The settings file this play run reads and writes ("" when none: scripted runs, --settings none). */
const char *port_settings_file(void);
/* A --settings file in an older build's JSON form (a launcher's userdata\settings.json from before the move onto the Core):
 * never written, so that build still loads it; the Core keeps its own in data\settings.txt. A game reads it once to carry
 * its choices over. "" when there is none. */
const char *port_settings_legacy(void);
/* session.c: a value a game reads at a fixed point of play that changes how it plays (co-op on or off, the right
 * stick's mode, read as a level begins): the recording keeps it there, and a replay gets the recorded value at the
 * same point (live, for a recording made before the game read it). name: up to 23 characters. Inside an in-between
 * frame nothing is kept: read it from the tick. */
int port_play_option(const char *name, int live);
void host_pad_rumble(u32 player, u32 strength, u32 ms); /* player 0..7's motors, 0..65535 for ms; 0 stops */
/* host_menu.cpp: Display > Full screen and Display > Frame-rate counter, for a game whose own options screen shows them
 * (a native game's): the same as choosing the menu entry, kept the same way. */
int port_fullscreen(void);
void port_set_fullscreen(int on);
int port_fps_counter(void);
void port_set_fps_counter(int on);
/* speed.c: fast forward (Game > Fast forward speed and toggle, the base's own options: game_options.c; Tab, or Input >
 * Controls > Fast forward, R3 until the player chooses). A console
 * with PORT_CONSOLE_SPEED says once at start whether this run may have it (port_speed_allow: a play run on the real clock;
 * the base adds no check, replay or fixed clock), then reads port_speed() as it reads its clock: 1 at normal speed, else
 * how many times as fast, PORT_SPEED_FREE for as fast as it goes. It runs its machine's clock through port_speed_clock, so
 * the game sees the same time between two of its ticks as at normal speed. Its sound it drops cleanly while fast. */
#define PORT_SPEED_FREE 0xffffu
#define PORT_SPEED_CHOICES 4u /* 2x, 3x, 4x, max */
typedef struct PortSpeedClock {
    s64 last, ahead; /* the real clock's last reading, and how far the machine's clock is ahead of it */
} PortSpeedClock;
int port_speed_option(const char *arg, const char *value); /* --fast-forward-test VB+N (the speed and toggle: game_options.c) */
void port_speed_allow(int on);
u32 port_speed(void);
s64 port_speed_clock(PortSpeedClock *c, s64 now, u32 speed); /* now + c->ahead, ahead grown by (speed - 1) x the time since the last read */
void port_speed_key(int down);                     /* host_window.cpp: Tab */
void port_speed_pads(const HostPad *pad, u32 n);   /* input.c: the pads as the devices gave them, before the assignments */
int port_speed_offered(void);                      /* the console has it (the menu greys it otherwise) */
const char *port_speed_button(void);               /* the controller's button(s) for it, as the settings write them */
const char *port_speed_label(void);                /* "Fast forward 3x" while on (the title bar), else "" */
long port_speed_gen(void);                         /* changes when that label may have */

#ifdef __cplusplus
}
#endif

#endif
