/* The game-owned renderer mode's kit (base/native_game.h): a native C or C++ game with no console emulated under it. Every
 * function of base/console.h, with nothing of a machine in it: the identity comes from the game's port_config.h, the
 * pictures, the pad buttons and the scene from the game's pg_native_game, the frame from base/native_frame.c. The window,
 * the menu bar, the settings (frame-rate cap, aspect, resolution), Copy agent prompt, the game log, the crash reporter,
 * the pads, recording and replay and the launcher's path are the base's, as for every port. A game is in this mode by
 * linking this folder (its build), never by a setting.
 *
 *   port.exe [--log FILE] [--save-dir DIR] [--fixed-clock 1] [--seconds N] [--headless] [--warp] [--frames N]
 *            [--shot FRAME,FILE] [--fps N] [--aspect ...] [--window ...] [--replay F.ptrec] [--input ...]
 *            [--scene-dump-at V] [--fault-at V] [--game-log on|off] (the base's) and the game's own options
 * An option nobody knows is skipped, so the saved settings of a newer base never stop an older game.
 *
 * port_config.h (the game's, generated from its game.json by tools/play_keys.py, or written by hand) may give:
 *   PG_NATIVE_ID "mygame"  PG_NATIVE_NAME "Xbox"  PG_NATIVE_GAME "My Game"  PG_NATIVE_VERSION "..."
 *   PG_NATIVE_W 640  PG_NATIVE_H 480  PG_NATIVE_HZ 60    (the original picture and logic rate) */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <windows.h>
#include "native_game.h"
#include "game_log.h"
#include "scene_dump.h"
#include "scene_json.h"
#include "session.h"
#include "play_stats.h"
#include "work_count.h"
#include "host.h"
#include "exit_codes.h"
#if __has_include("port_config.h")
#include "port_config.h"
#endif
#ifndef PG_NATIVE_ID
#define PG_NATIVE_ID "native"
#endif
#ifndef PG_NATIVE_NAME
#define PG_NATIVE_NAME "Windows"
#endif
#ifndef PG_NATIVE_GAME
#define PG_NATIVE_GAME "Native game"
#endif
#ifndef PG_NATIVE_VERSION
#define PG_NATIVE_VERSION PG_NATIVE_ID " native 1"
#endif
#ifndef PG_NATIVE_W
#define PG_NATIVE_W 640u
#endif
#ifndef PG_NATIVE_H
#define PG_NATIVE_H 480u
#endif
#ifndef PG_NATIVE_HZ
#define PG_NATIVE_HZ 60u
#endif

const PortConsole port_console = {PG_NATIVE_ID, PG_NATIVE_NAME, PG_NATIVE_GAME, PG_NATIVE_VERSION,
                                  PG_NATIVE_W, PG_NATIVE_H, PG_NATIVE_HZ, PORT_CONSOLE_FPSCAP};

/* No scene map: a native game has no guest memory to read fields from; it names its state through pg_native_game.scene. */
const SceneField scene_fields[1] = {{0}};
const u32 scene_field_count = 0;
const char *const scene_task_keys[1] = {0};
const u32 scene_task_count = 0;
const SceneRule scene_rules[1] = {{0}};
const u32 scene_rule_count = 0;
const SceneCount scene_counts[1] = {{0}};
const u32 scene_count_count = 0;
const u32 scene_log_every = 0;
const char scene_root[] = "";

/* ---- run state ---- */
u32 port_vblank_count(void) { return pg_native_ticks(); }
int port_extra_drawing(void) { return 0; }

/* ---- the scene dump ---- */
const u8 *console_guest_ptr(u32 addr, u32 size)
{
    (void)addr;
    (void)size;
    return NULL;
}
void console_scene_extra(struct Json *j)
{
    if (pg_native_game.scene) pg_native_game.scene(j);
}
void console_replay_args(struct Json *j, int after) /* no disc: the base's options (--warp among them) are enough */
{
    (void)j;
    (void)after;
}
int console_capture_bmp(const char *path) { return pg_native_capture_bmp(path); }
void console_view(PortView *v)
{
    memset(v, 0, sizeof *v);
    v->out_w = g_gfx.out_w, v->out_h = g_gfx.out_h;
    v->frame_w = g_gfx.frame_w, v->frame_h = g_gfx.frame_h;
    v->headless = g_gfx.headless;
    v->warp = g_gfx.warp;
    v->presented_fps = (float)gfx_presented_fps();
}
void console_watch(u32 word, u32 tick)
{
    (void)word;
    (void)tick;
}
int console_poke(u32 addr, u8 value)
{
    (void)addr;
    (void)value;
    return 0;
}

/* ---- no answer-key emulator ---- */
int console_lockstep_pad(HostPad *pad)
{
    (void)pad;
    return 0;
}
s32 console_lockstep_timer(s32 live) { return live; }
int console_lockstep_time(HostTime *t)
{
    (void)t;
    return 0;
}

/* ---- the menu bar: no console entries (the game's come through game_menu_extra, game.json game_menu) ---- */
const PortMenuItem *console_menu_extra(unsigned *count)
{
    *count = 0;
    return NULL;
}

/* ---- input ---- */
void console_pads_changed(void) {}
const PortAction *console_actions(unsigned *count)
{
    *count = pg_native_game.actions ? pg_native_game.n_actions : 0u;
    return pg_native_game.actions;
}

/* ---- the launcher contract: the base's install.c, or the game's own (an extracted-data game); no console's disc ---- */
int console_install(const char *image, const char *work, const char *assets)
{
    return pg_native_game.install ? pg_native_game.install(image, work, assets) : -1;
}
int console_check_image(const char *image)
{
    (void)image;
    return 0;
}

/* ---- the presenter's hooks ---- */
int console_present_option(const char *arg, const char *value)
{
    (void)arg;
    (void)value;
    return 0;
}
void console_present_over(void *back_buffer) { (void)back_buffer; }
void console_present_release(void) {}
int console_widens(void) { return 1; } /* the game fills the window at any shape unless the player chose one */
int extra_interp(void) { return 0; }   /* every frame is the game's own */
void extra_set_fps(unsigned fps) { pg_native_set_fps(fps); }
unsigned extra_fps(void) { return pg_native_fps(); }

/* ---- the end ---- */
static volatile LONG g_exiting;
static u32 g_watch_s;

static const char *end_words(const char *why, int code)
{
    static char text[300];
    if (!strcmp(why, "the window was closed")) return "the window was closed by the user";
    sprintf_s(text, sizeof text, "%s: %s", code ? "stopped by the build" : "stopped", why);
    return text;
}

void port_exit(const char *why, int code)
{
    if (InterlockedExchange(&g_exiting, 1)) return;
    port_log("exit: %s (%d) after %u ticks, %u frames presented\n", why, code, pg_native_ticks(), g_gfx.frames);
    if (pg_native_game.end) pg_native_game.end(why, code);
    play_stats_report();
    pg_native_split_report();
    work_count_report();
    game_log_end(end_words(why, code), code, code != 0);
    port_log_flush();
    ExitProcess((UINT)code);
}

static DWORD WINAPI watchdog(LPVOID unused)
{
    (void)unused;
    Sleep(g_watch_s * 1000u); /* not the tick: a watchdog thread */
    port_log("watchdog: %us passed, %u ticks\n", g_watch_s, pg_native_ticks());
    port_exit("watchdog", PORT_EXIT_WATCHDOG);
    return 0;
}

/* ---- start-up: base/host_main.cpp builds the argument list and calls this ---- */
static int kit_option(const char *a, const char *v, const char **log)
{
    if (!v) return 0;
    if (!strcmp(a, "--log")) *log = v;
    else if (!strcmp(a, "--save-dir")) host_set_save_dir(v);
    else if (!strcmp(a, "--fixed-clock")) host_set_fixed_clock(atoi(v) != 0);
    else if (!strcmp(a, "--seconds")) g_watch_s = (u32)atoi(v);
    else if (!strcmp(a, "--clipboard-test")) host_text_test(v);
    else return 0;
    return 2;
}

int port_main(int argc, char **argv)
{
    const char *log = PG_NATIVE_ID "_port.log";
    int code;
    host_set_args(argc, argv);
    for (int i = 1, used; i < argc; i += used) {
        const char *a = argv[i], *v = i + 1 < argc ? argv[i + 1] : NULL;
        if ((used = input_live_option(a, v)) == 0 && (used = kit_option(a, v, &log)) == 0 &&
            (used = pg_native_game.option ? pg_native_game.option(a, v) : 0) == 0 && (used = input_option(a, v)) == 0 &&
            (used = session_option(a, v)) == 0 && (used = scene_dump_option(a, v)) == 0 &&
            (used = game_log_option(a, v)) == 0 && (used = play_stats_option(a, v)) == 0 &&
            (used = work_count_option(a, v)) == 0 && (used = pace_option(a, v)) == 0 &&
            (used = pg_native_option(a, v)) == 0)
            used = 1; /* unknown: skipped */
    }
    port_log_open(log);
    game_log_start();
    host_phangrade_start();
    port_twin_start(NULL);
    port_log("native: %s (%s), %ux%u at %u Hz, the game's own renderer\n", port_console.game, port_console.version,
             port_console.native_w, port_console.native_h, port_console.tick_hz);
    port_log_flush();
    if (g_watch_s) CreateThread(NULL, 0, watchdog, NULL, 0, NULL);
    code = pg_native_game.main();
    port_exit(code ? "the game ended with an error" : "the game ended", code);
    return code;
}
