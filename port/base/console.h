/* The console interface: everything the base needs from the machine a kit emulates or replaces, and nothing else.
 * The kit defines these (one console per executable, so they are plain link-time symbols: no table, no cost in the
 * per-frame path beyond the call itself). A new console fills in this file's declarations in its own console_*.c;
 * tools/game_setup.py scaffolds one with every function stubbed (README.md "Adding a console").
 *
 *   identity      port_console
 *   run state     port_vblank_count, port_extra_drawing
 *   scene dump    console_guest_ptr, console_scene_extra, console_replay_args, console_capture_bmp
 *   menu          console_menu_extra
 *   input         console_pads_changed, console_actions
 *   presenter     console_present_option, console_present_over, console_present_release, console_widens
 *   start-up      port_main, port_setup, port_verify_assets, port_find_image, console_check_image
 */
#ifndef PORT_CONSOLE_H
#define PORT_CONSOLE_H

#include <stddef.h>
#include "base.h"
#include "audio_out.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct PortConsole {
    const char *id;        /* "dreamcast", "saturn": the settings and log use it */
    const char *name;      /* "Dreamcast": the scene-dump prompt names it */
    const char *game;      /* the game's name (the window title, the prompt) */
    const char *version;   /* the build's identity line (--version) */
    unsigned native_w, native_h; /* the console's picture: the window opens as a whole multiple of it */
    unsigned tick_hz;      /* the game's own rate: 60 or 50 */
    unsigned flags;        /* PORT_CONSOLE_*: what its presenter does (the menu greys an entry the console cannot apply) */
    unsigned replay_fps;   /* the fewest frames a second a headless replay runs at on the slowest machine, for its time limit (--seconds); 0: 150 */
    const char *no_replay; /* why its fixed-clock replay does not reach a dump's frame yet, or NULL: its dumps then replay on the real clock and say so */
} PortConsole; /* a console that leaves out the last members gets 0 / NULL: the defaults */
enum {
    PORT_CONSOLE_FPSCAP = 2, /* a frame-rate cap (Display > Frame-rate cap) */
    PORT_CONSOLE_ONLINE = 4, /* online play: the Online menu (base/host_online.cpp) and the option --net on|off|HOST[:PORT] */
    PORT_CONSOLE_FCAP = 8,   /* a scene dump also records the next frame draw by draw (base/frame_capture.h; the kit writes the
                                records). Without it nothing is armed, allocated or written: one test a frame */
};

extern const PortConsole port_console;

/* ---- run state ---- */
u32 port_vblank_count(void);   /* ticks the game has run (vertical blanks waited for) */
int port_extra_drawing(void);  /* 1 inside an in-between frame (nothing is recorded or logged there); 0 if the console has none */

/* ---- the scene dump ---- */
/* The host address of size bytes at the game's address addr (a scene field's address), or NULL if they are not all in
 * the game's memory. Read only. */
const u8 *console_guest_ptr(u32 addr, u32 size);
/* An in-between frame or any other console state the dump should name: members appended to the "scene" object, each
 * starting with ",\n    " (see scene_dump.c). May add nothing. */
struct Json;
void console_scene_extra(struct Json *j);
/* Arguments the replay command needs beyond the base's (the disc, --vmu, ...): each starts with a space; after is 0
 * before "--replay" and 1 after "--warp" (scene_carry copies this run's option). */
void console_replay_args(struct Json *j, int after);
/* The frame as drawn, to a file (24-bit BMP); 0 on failure. */
int console_capture_bmp(const char *path);

/* How the frame is being shown (the dump's "output", "window" and "present" members, the play summary's size). */
typedef struct PortView {
    unsigned out_w, out_h;     /* the output's pixel size */
    unsigned frame_w, frame_h; /* the console's frame before scaling */
    int headless;              /* no window */
    int warp;                  /* --warp: a faster than real time run (the replay command repeats it) */
    int interp;                /* in-between frames are shown */
    float presented_fps;       /* frames shown a second, measured */
} PortView;
void console_view(PortView *v);
/* --watch ADDR: log writes to a game word from tick; word 0 stops. A console without a watch does nothing. */
void console_watch(u32 word, u32 tick);
/* --poke-ram ADDR=V: write the byte V to the game's address ADDR (a test's way to a late scene, such as a stage it would
 * take a whole playthrough to reach); 0 if ADDR is not in the game's memory or the console has no poke. */
int console_poke(u32 addr, u8 value);

/* ---- the answer-key emulator (tools/lockstep.py) ----
 * A console that checks its port against an emulator frame by frame hands the emulator's pad, timer and clock values
 * to a recording here. 1: the emulator's frame gave it; 0: use the live one. A console without one returns 0 / live. */
int console_lockstep_pad(HostPad *pad);
s32 console_lockstep_timer(s32 live);
int console_lockstep_time(HostTime *t);

/* ---- the menu bar ---- */
/* A console-only entry: a check (on/off), a choice (one of a set), a command or a submenu of commands, at the end of the named menu ("Display", "Audio",
 * "Input" or "Game"), after a separator. The base builds every other entry, so the menus, their order, names and shortcuts
 * are the same on every console. The setting is the key in the settings file (a check saves on/off, a choice its
 * values[] word); get/set give and take the value; available() greys the entry when the game cannot apply it.
 * A command (Cheats > Unlock everything) calls set(0) when chosen; a submenu (Level select > Title) lists choices[] (at
 * most PORT_MENU_CHOICES_MAX) and calls set(index) for the one chosen. Neither keeps anything in the settings: setting,
 * get and values may be NULL. */
typedef struct PortMenuItem {
    const char *menu;            /* "Display", "Audio", "Input" or "Game" */
    const char *label;           /* with & for the mnemonic */
    int kind;                    /* PORT_MENU_CHECK, PORT_MENU_CHOICE, PORT_MENU_COMMAND or PORT_MENU_SUBMENU */
    const char *setting;         /* the settings.txt key */
    int (*get)(void);            /* check: 0/1; choice: the index */
    void (*set)(int value);
    const char *const *choices;  /* choice: the labels */
    const char *const *values;   /* choice: the settings file's word for each (what the --option takes) */
    unsigned n_choices;
    int (*available)(void);      /* NULL: always; else 0 greys the item */
    int tag;                     /* 0: set is called, as above. Else the game's own command (game.json menu_commands, or a
                                    native game's): a pick goes to the game as (tag, index) at its next tick
                                    (native_game.h pg_native_menu_take) and is recorded; set may be NULL. A tagged
                                    SUBMENU's choices may be more than PORT_MENU_CHOICES_MAX (shown as "1-16", "17-32",
                                    ...), a '>' in one is a path of nested popups, and choices NULL is a runtime list
                                    (pg_native_menu_list); --menu-pick takes the whole path */
} PortMenuItem;
enum { PORT_MENU_CHECK = 1, PORT_MENU_CHOICE = 2, PORT_MENU_COMMAND = 3, PORT_MENU_SUBMENU = 4 };
#define PORT_MENU_CHOICES_MAX 16u /* a choice's or a submenu's entries */
/* The console's entries (a static array), or NULL/0. */
const PortMenuItem *console_menu_extra(unsigned *count);
/* A game's own entries (game.json game_menu: the game defines it), the same kind, after the console's in each menu.
 * Its settings are the game's: a key starting "game." is not a command-line option, so the game reads it itself. */
const PortMenuItem *game_menu_extra(unsigned *count);

/* ---- input ---- */
/* The mouse given straight to a game (native_game.h pg_native_mouse_mode, pg_native_mouse). */
enum { PG_MOUSE_LAYOUT, PG_MOUSE_POINTER, PG_MOUSE_LOOK };
typedef struct PgNativeMouse {
    f32 dx, dy, x, y;
    u32 buttons, pressed;
    s32 wheel;
    u8 captured;
} PgNativeMouse;
/* The base reads the host's keyboard and controllers (input.c) into HostPad (base.h), the Dreamcast controller's
 * layout, which the other consoles' pads fit. A console whose game reads another layout maps from HostPad where it
 * hands the pad to the game. */
void console_pads_changed(void); /* the set of controllers changed (scanned again): the console may re-read what it
                                    shows of them (the Dreamcast's pad skins) */
/* The buttons of the console's own pad that the player can assign (Input > Controls), in the order of host_actions'
 * bits. name is also the --input script's name for it; dflt is the host button it takes until the player chooses:
 * A B X Y START UP DOWN LEFT RIGHT LB RB LT RT L3 R3 BACK GUIDE RSUP RSDOWN RSLEFT RSRIGHT (or "none"). The base's
 * settings keep a choice as bind-<name>. A console whose game reads the HostPad itself (the Dreamcast) returns NULL. */
typedef struct PortAction {
    const char *name;
    const char *dflt;
} PortAction;
const PortAction *console_actions(unsigned *count);

/* ---- start-up: the launcher contract (README.md), run by host_main.cpp ---- */
int port_main(int argc, char **argv); /* the console's start-up, given the argument list the host built */
int port_setup(const char *image, const char *work);         /* --setup-only: check, copy to work\assets\disc */
/* A kit whose game is made ready another way than a copy of its .cue or .gdi image: the native kit, whose game extracts
 * what it needs from its disc into data files. --setup-only (image and work) or --verify-assets (assets; image NULL):
 * the exit code, as port_setup's; -1: the kit has none, the disc copy below runs. */
int console_install(const char *image, const char *work, const char *assets);
/* install.c: work\installation.json {"version", "assets"} (assets relative to work), written last; 0, or 1 (logged). */
int port_install_record(const char *work, const char *assets);
int port_verify_assets(const char *assets);                  /* --verify-assets: check the image in assets */
int port_find_image(const char *dir, char *out, size_t cap); /* the disc image in dir (or a folder below) */
/* Is image this console's game? 0 yes; 4 missing or unreadable; 5 another game or region (say why with port_log; the
 * base has already checked that the image's listed track files exist). base/install.c does the rest of the contract. */
int console_check_image(const char *image);
int port_install_fail(int code, const char *fmt, ...); /* install.c: prints "error: ..." (stderr and the log) and returns code */

/* ---- the presenter (base/present.h owns the device, the swap chain and the frame target) ---- */
int console_present_option(const char *arg, const char *value); /* the console's own display options; 0 if not ours */
void console_present_over(void *back_buffer); /* an ID3D11Resource: what the console lays over the frame on the window (nothing, for most) */
void console_present_release(void);           /* the device is going away */
int console_widens(void);                     /* 1: a play run's world fills the window unless the player chose a shape */

/* ---- in-between frames: drawing and undoing them is the console's; they follow the frame-rate cap (PORT_CONSOLE_FPSCAP greys it) ---- */
int extra_interp(void);        /* --present */
void extra_set_fps(unsigned fps); /* --fps N, 0 uncapped */
unsigned extra_fps(void);

/* ---- the base's own, for its menu ---- */
void input_set_rumble(int on); /* --rumble */
int input_rumble(void);
int input_device_count(void);         /* input.c: the gamepads and joysticks open now */
const char *input_device_desc(int i); /* one of them: name, VID:PID, kind */

#ifdef __cplusplus
}
#endif

#endif
