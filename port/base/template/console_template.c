/* A new console's side of the port base, every function of base/console.h stubbed so it links and runs: window, menu,
 * settings, Copy agent prompt (F9), session recording and replay, game log, pads and the launcher's path are the base's.
 * tools/game_setup.py console copies this to port/<kit>/console_@ID@.c with @ID@, @NAME@ and @GAME@ filled in; what is left
 * is the machine: fill each TODO. This template draws a CPU frame (an XRGB buffer the console composes) through
 * base/present_cpu.c; a console with a GPU renderer draws into g_gfx's frame target (base/present.h, runtime/gfx/gfx_present.c).
 *
 * Nearest existing: saturn/console_sat.c (a finished one of the same kind) and runtime/console_dc.c. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "console.h"
#include "scene_json.h"
#include "scene_dump.h"
#include "present_cpu.h"

/* TODO: the picture the console composes each tick (0x00RRGGBB) and its size. */
#define FRAME_MAX_W 640u
#define FRAME_MAX_H 480u
u32 g_@ID@_frame[FRAME_MAX_W * FRAME_MAX_H];
u32 g_@ID@_frame_w = 320u, g_@ID@_frame_h = 240u;
static u32 g_ticks; /* TODO: the console's own count of ticks (vertical blanks) the game has waited for */
static int g_headless;

const PortConsole port_console = {"@ID@", "@NAME@", "@GAME@", "@ID@ kit 1", 320u, 240u, 60u, 0u};

/* ---- run state ---- */
u32 port_vblank_count(void) { return g_ticks; }
int port_extra_drawing(void) { return 0; }

/* ---- the scene dump ---- */
/* TODO: the host address of size bytes at the game's address addr, or NULL if they are not all in the game's memory
 * (hand a scalar over in host byte order: a big-endian console reverses 2, 4 and 8 byte values). */
const u8 *console_guest_ptr(u32 addr, u32 size)
{
    (void)addr;
    (void)size;
    return NULL;
}

void console_scene_extra(struct Json *j) { (void)j; } /* TODO: members for the machine's state, each ",\n    \"name\": ..." */

void console_replay_args(struct Json *j, int after)
{
    if (!after) scene_carry(j, "--disc", 1); /* TODO: the options a replay needs besides the base's */
}

int console_capture_bmp(const char *path)
{
    (void)path;
    return 0; /* TODO: write g_@ID@_frame as a 24-bit BMP */
}

void console_view(PortView *v)
{
    memset(v, 0, sizeof *v);
    v->headless = g_headless;
    v->frame_w = g_@ID@_frame_w;
    v->frame_h = g_@ID@_frame_h;
    if (!g_headless) cpu_present_view(v);
}

void console_watch(u32 word, u32 tick)
{
    (void)word;
    (void)tick;
}

/* ---- the answer-key emulator: none ---- */
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

/* ---- the menu bar: no console-only entries ---- */
const PortMenuItem *console_menu_extra(unsigned *count)
{
    *count = 0;
    return NULL;
}

/* ---- input ---- */
void console_pads_changed(void) {}

/* TODO: the buttons of the console's pad, in the order host_actions' bits come (name, default host button). */
static const PortAction k_actions[] = {{"A", "A"}, {"B", "B"}, {"START", "START"}};
const PortAction *console_actions(unsigned *count)
{
    *count = (unsigned)(sizeof k_actions / sizeof k_actions[0]);
    return k_actions;
}

/* ---- the launcher contract ---- */
int console_check_image(const char *image)
{
    (void)image;
    return 0; /* TODO: 0 if the image is this console's game; port_install_fail(5, ...) if it is another */
}

/* ---- the presenter's hooks (base/present.h owns the rest): a CPU frame goes through base/present_cpu.c ---- */
int console_present_option(const char *arg, const char *value)
{
    (void)arg;
    (void)value;
    return 0;
}
void console_present_over(void *back_buffer) { (void)back_buffer; }
void console_present_release(void) {}
int console_widens(void) { return 0; }
int extra_interp(void) { return 0; }
void extra_set_fps(unsigned fps) { (void)fps; }
unsigned extra_fps(void) { return 0; }

/* ---- audio: TODO scale what the console sends to its device by the volume (the Audio menu and --volume) ---- */
static int g_volume = 100, g_muted;
void audio_set_volume(int percent, int muted)
{
    g_volume = percent < 0 ? 0 : percent > 100 ? 100 : percent;
    g_muted = muted;
}
int audio_volume(void) { return g_volume; }

/* ---- start-up: the base's entry (base/host_main.cpp) builds the argument list and calls this ---- */
int port_main(int argc, char **argv)
{
    host_set_args(argc, argv);
    /* TODO: take the console's own options, then the base's in a chain as saturn/sat_main.c does (input_option,
     * session_option, scene_dump_option, game_log_option, play_stats_option, work_count_option, host_window_option, cpu_present_option),
     * port_log_open, game_log_start, host_phangrade_start; then boot the machine and run it. Each blank calls
     * work_count_tick(blank) and the exit work_count_report(). Each tick ends by
     * presenting: cpu_present(frame, w, h) unless headless, game_log_poll(port_vblank_count()), scene_dump_poll(...),
     * play_stats_shown(). */
    (void)g_headless;
    (void)g_ticks;
    return 0;
}
