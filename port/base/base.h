/* The port base: what every console kit shares, console-free (README.md "The port base"). Nothing in base/ includes
 * a console's own headers (port.h, platform.h, sat.h, rd_rt.h, tc_rt.h) or names a console, a game or a guest address;
 * what a base file needs from the machine it runs is declared in console.h, and the kit defines it. tests/test_port_base.py
 * fails when that stops being true. */
#ifndef PORT_BASE_H
#define PORT_BASE_H

#include <stdint.h>
#include <stdio.h>

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
#define HOST_PADS 4
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
#define HOST_PAD_MODERN 0xffff0000u
typedef struct HostPad {
    u32 buttons;
    u8 l, r;   /* triggers, 0..255 */
    s8 x, y;   /* stick, -128..127; y is negative up */
} HostPad;
u32 host_pads_read(HostPad pad[HOST_PADS]); /* samples the host now, once per game input poll; bit p of the result:
                                               player p + 1 has a controller (player 1 always) */
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
/* input_ex.c, input.c: the modern buttons for a game's own screens (its Key Configuration), player 0..3 = the game's port.
 * held / pressed: HOST_PAD_MODERN bits, merged keyboard (E, R) and gamepads and carried by --input, recordings and replays
 * (zero in a run that scripts none). pressed: new since the last call for that player. present: the bits the controller
 * in hand has (zero without a live run). name: its maker's label for one bit, by pad_style(). */
u32 port_pad_ex_held(int player);
u32 port_pad_ex_pressed(int player);
u32 port_pad_ex_present(int player);
const char *port_pad_ex_name(u32 bit, int player);
void input_ex_note(const HostPad pad[HOST_PADS]); /* host_pads_read only */
/* input_ex_assign.c: a game's own key screen gives these buttons the game's actions (numbered from 1 by the game; 0 is
 * unassigned, as every row starts). Rows: LB, RB, the stick clicks, Back, Guide, the right stick's four directions.
 * load: once at start, from the settings or the recording being replayed. set: stays saved at once. codes: the action
 * codes (codes[i] is action i + 1's) of the rows this player holds now, ORed; 0 when none is held or assigned. */
#define PORT_PAD_EX_ROWS 10u
#define PORT_PAD_EX_STICK_ROW PORT_PAD_EX_ROWS /* _get and _set: 1, the left stick moves (pad_stick.c) */
u32 port_pad_ex_row_bit(unsigned row);
const char *port_pad_ex_row_name(unsigned row, int player); /* for a key screen; good until the next call */
void port_pad_ex_assign_load(unsigned actions);
int port_pad_ex_assign_get(int player, unsigned row);
void port_pad_ex_assign_set(int player, unsigned row, int action);
u32 port_pad_ex_assign_codes(int player, const u32 *codes, unsigned actions);
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
void port_pad_page_init(const char *const *choices, unsigned actions); /* loads what is kept */
void port_pad_page_enter(void);                                        /* the screen opens */
int port_pad_page_showing(int player);
void port_pad_page_open(int player);
int port_pad_page_press(int player, u32 press);
unsigned port_pad_page_rows(void);
unsigned port_pad_page_cursor(int player);
const char *port_pad_page_row_name(int player, unsigned row);
const char *port_pad_page_row_value(int player, unsigned row, int *set);
void port_pad_page_reset(int player);  /* the screen's RESET, CANCEL and leaving with OK */
void port_pad_page_cancel(int player);
void port_pad_page_keep(void);
/* host_settings.cpp: a small per-game store in the port's settings file, apart from the game's save. Keys start with "game."
 * (others are refused); unset, or in a run that reads no settings, you get `def`. */
int port_setting_get_int(const char *key, int def);
void port_setting_set_int(const char *key, int value);
void port_setting_set(const char *key, const char *value); /* a menu-style choice kept for the next start (host_settings.cpp) */
int port_setting_get_str(const char *key, char *out, int cap); /* a game's short text ("game.NAME"): 1 when there is one */
void port_setting_set_str(const char *key, const char *value);
void host_pad_rumble(u32 player, u32 strength, u32 ms); /* player 0..3's motors, 0..65535 for ms; 0 stops */

#ifdef __cplusplus
}
#endif

#endif
