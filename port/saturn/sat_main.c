/* Saturn port kit: the machine's start-up (the base's main, base/host_main.cpp, comes first and hands this the
 * argument list: the defaults, the saved settings, then the command line). Loads the game's code image into work RAM,
 * brings the machine up, and runs the game's entry function (translated C) on a thread with a deep stack. The entry
 * values (code load address, entry function, initial stack and GBR, frame rate) come from the game's sat_game.h.
 *
 *   port.exe --disc <image.cue> [--image FILE] [--seconds N] [--log FILE] [--save FILE] [--cdrate N]
 *            [--fixed-clock 1]  (virtual time: the vertical blank comes by the machine's own count, so a run repeats exactly)
 *            [--headless] [--fast] [--frames N] [--wav FILE | --audio-dump FILE] [--shot VB FILE] [--shot-every N] [--shot-max N]
 *            [--input NAME@V[+N],..] [--replay FILE.ptrec] [--record off] [--scene-dump-at V] [--stop-at V]
 *            [--ground-shadows under|original] [--cd-fast-loads on|off] [--in-between on|off]   (play runs: under, on, on; checks: original, off, none)
 *            [--twin-stick on|off]   (game.json twin_stick: in fights the right stick turns; on in unscripted runs)
 *            [--auto-load on|off]   (the game's load path at boot when its save is there: on in unscripted play runs)
 *            [--part-choice NAME]   (a model part the game offers, sat_part.c; default the original)
 *            [--net on|off|HOST[:PORT]]   (the Online menu's saved choice: host, play alone, join)
 *            [--net-host PORT|on | --net-join HOST[:PORT]] [--net-delay N] [--net-wait on|off] [--net-hash-every N]
 *            [--net-pace on|off] [--net-test-*]   (online play for two, game.json netplay: sat_net.h)
 *            [--aspect fit|4:3|16:9|21:9] [--volume 0..100] [--window ...]   (the base's: window, input, session, log)
 *            [--bands N]   (VDP2 composed in N bands at once, base/bands.c; 0 as the cores, at most 4; 1 one thread)
 *            [--v1-bands on|off]   (VDP1 drawn in those bands too, each band its own rows; off: one thread)
 *            [--layers HEX] [--peek A,..] [--v1dump N] [--profile] [--sample] [--trace ...]   (bring-up aids)
 * An option nobody knows is skipped, so the saved settings of a newer base never stop an older kit. */
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <windows.h>
#include "sat.h"
#include "sat_sys.h"
#include "sat_cd.h"
#include "sat_part.h"
#include "sat_game.h"
#include "sat_scsp.h"
#include "sat_vdp.h"
#include "console.h"
#include "game_log.h"
#include "scene_dump.h"
#include "session.h"
#include "play_stats.h"
#include "work_count.h"
#include "present_cpu.h"
#include "bands.h"
#include "host.h"
#include "autoload.h"
#include "sat_net.h"

static u32 g_max_frames, g_watch_s;
static const char *g_image = SAT_IMAGE_DEFAULT, *g_cue = "";
static volatile LONG g_exiting;
static int g_audio_off; /* --audio off */

/* How a port_exit reads as the game log's last line: who ended the run. */
static const char *end_words(const char *why, int code)
{
    static char text[300];
    if (!strcmp(why, "the window was closed")) return "the window was closed by the user";
    if (!strcmp(why, "--stop-at reached")) return "stopped at the vertical blank asked for (--stop-at)";
    sprintf_s(text, sizeof text, "%s: %s", code ? "stopped by the build" : "stopped", why);
    return text;
}

void port_exit(const char *why, int code)
{
    if (InterlockedExchange(&g_exiting, 1)) return;
    port_log("exit: %s (%d) after %u vertical blanks\n", why, code, sat_vblank_count());
    sat_net_end();
    play_stats_report();
    work_count_report();
    game_log_end(end_words(why, code), code, code != 0);
    sat_profile_report();
    sat_perf_report();
    sat_perf_sample_report();
    sat_video_pacing_report();
    sat_audio_close();
    sat_mem_report();
    sat_stub_report();
    port_log_flush();
    ExitProcess((UINT)code);
}

static DWORD WINAPI watchdog(LPVOID unused)
{
    (void)unused;
    Sleep(g_watch_s * 1000u); /* not the tick: a watchdog or sampler thread */
    port_log("watchdog: %us passed, %u vertical blanks\n", g_watch_s, sat_vblank_count());
    sat_where();
    port_exit("watchdog", 4);
    return 0;
}

void sat_frame_limit_check(void)
{
    if (g_max_frames && sat_vblank_count() >= g_max_frames) port_exit("frame limit", 0);
}

static DWORD WINAPI game_thread(LPVOID unused)
{
    (void)unused;
    sat_video_init(); /* the window belongs to the thread that pumps its events: this one, which also reads the pads */
    sh4_ldc_gbr(SAT_ENTRY_GBR);
    sat_call(SAT_ENTRY_SP, SAT_ENTRY_FN, 0, 0, 0, 0);
    port_exit("the entry function returned", 2);
    return 0;
}

typedef struct KitRun {
    int fast, headless, det;
    u32 shot_vb, shot_every;
    const char *shot_path, *log;
} KitRun;

/* This kit's own options (the bring-up aids and the machine's); the arguments taken, 0 if not ours. */
static int kit_option(const char *a, const char *v, KitRun *r)
{
    if (!strcmp(a, "--fast")) {
        r->fast = 1;
        return 1;
    }
    if (!strcmp(a, "--det")) { /* the machine's own progress sets the vertical blanks and the timer: a run repeats exactly */
        r->det = 1;
        return 1;
    }
    if (!strcmp(a, "--headless")) {
        r->headless = 1;
        cpu_present_option(a, NULL); /* the counters' run opens the device with no window */
        return 1;
    }
    if (!strcmp(a, "--profile")) {
        sat_set_profile(1);
        return 1;
    }
    if (!strcmp(a, "--watch-saved")) {
        sat_set_watch_saved(1);
        return 1;
    }
    if (!strcmp(a, "--sample")) {
        sat_perf_sample(1);
        return 1;
    }
    if (!v) return 0;
    if (!strcmp(a, "--frames")) g_max_frames = (u32)atoi(v);
    else if (!strcmp(a, "--vblank-wait")) sat_set_vblank_wait(strcmp(v, "off") != 0);
    else if (!strcmp(a, "--vwatch")) {
        char *e;
        sat_vwatch_lo = (u32)strtoul(v, &e, 16);
        sat_vwatch_hi = (u32)strtoul(e + 1, &e, 16);
        if (*e == ',') sat_vwatch_vb = (u32)strtoul(e + 1, NULL, 10);
    }
    else if (!strcmp(a, "--cdrate")) sat_cd_data_rate = (u32)atoi(v);
    else if (!strcmp(a, "--v1dump")) sat_v1_dump_every = (u32)atoi(v);
    else if (!strcmp(a, "--v1-bands")) sat_v1_bands_set(strcmp(v, "off") != 0);
    else if (!strcmp(a, "--ground-shadows")) sat_v1_ground_shadows = !strcmp(v, "under");
    else if (!strcmp(a, "--in-between-shot")) sat_inb_shot_option(v);
    else if (!strcmp(a, "--in-between")) sat_inb_on = !strcmp(v, "on"); /* play runs: on (sat_inb.c) */
    else if (!strcmp(a, "--cd-fast-loads")) sat_cd_fast_loads = strcmp(v, "off") != 0;
    else if (!strcmp(a, "--part-choice")) sat_part_option(v);
    else if (!strcmp(a, "--layers")) sat_layer_mask = (u32)strtoul(v, NULL, 16);
    else if (!strcmp(a, "--peek")) {
        for (char *s = (char *)v; *s && sat_peek_n < 16; sat_peek_n++) {
            sat_peek_addr[sat_peek_n] = (u32)strtoul(s, &s, 16);
            if (*s == ',') s++;
        }
    }
    else if (!strcmp(a, "--wav") || !strcmp(a, "--audio-dump")) sat_audio_wav(v);
    else if (!strcmp(a, "--shot-every")) r->shot_every = (u32)atoi(v);
    else if (!strcmp(a, "--shot-max")) sat_shot_max = (u32)atoi(v);
    else if (!strcmp(a, "--trace")) sat_set_trace((u32)atoi(v));
    else if (!strcmp(a, "--watch") || !strcmp(a, "--seconds")) g_watch_s = (u32)atoi(v);
    else if (!strcmp(a, "--volume")) audio_set_volume(atoi(v), 0);
    else if (!strcmp(a, "--audio")) g_audio_off = !strcmp(v, "off");
    else if (!strcmp(a, "--image")) g_image = v;
    else if (!strcmp(a, "--disc")) g_cue = v;
    else if (!strcmp(a, "--log")) r->log = v;
    else if (!strcmp(a, "--save")) sat_bup_path(v);
    else if (!strcmp(a, "--save-dir")) host_set_save_dir(v);
    else if (!strcmp(a, "--fixed-clock")) host_set_fixed_clock(atoi(v) != 0);
    else return 0;
    return 2;
}

/* Fast loads (sat_cd.c) as the replay recorded them (off when it has no note, as before the choice), else as asked or,
 * if not asked, on in a play run; kept in the recording when on, so a run with it off records nothing new. */
#define FAST_LOADS_NOTE "*cd-fast-loads"
static void fast_loads_start(int play_run)
{
    u8 on = 0;
    if (session_note_replayed(FAST_LOADS_NOTE, &on, 1)) sat_cd_fast_loads = on != 0;
    else if (session_replaying()) sat_cd_fast_loads = 0;
    else if (sat_cd_fast_loads < 0) sat_cd_fast_loads = play_run;
    on = (u8)(sat_cd_fast_loads > 0);
    if (on) session_save_read(FAST_LOADS_NOTE, &on, 1, 1);
    port_log("cd: fast loads %s\n", on ? "on" : "off");
}

int port_main(int argc, char **argv)
{
    KitRun run = {0, 0, 0, 0, 0, NULL, "saturn_port.log"};
    u8 *buf;
    FILE *f;
    long n;
    HANDLE th;
    host_set_args(argc, argv);
    for (int i = 1, used; i < argc; i += used) {
        const char *a = argv[i], *v = i + 1 < argc ? argv[i + 1] : NULL;
        if ((used = input_live_option(a, v)) != 0) continue; /* offered first: it owns --fast-until */
        if (!strcmp(a, "--shot") && i + 2 < argc) { /* VB FILE */
            run.shot_vb = (u32)atoi(argv[i + 1]);
            run.shot_path = argv[i + 2];
            used = 3;
        } else if (!strcmp(a, "--trace-from") && i + 2 < argc) { /* ADDR N */
            sat_set_trace_from((u32)strtoul(argv[i + 1], NULL, 16), (u32)atoi(argv[i + 2]));
            used = 3;
        } else if (!strcmp(a, "--trace-vb") && i + 2 < argc) { /* VB N */
            sat_set_trace_vb((u32)atoi(argv[i + 1]), (u32)atoi(argv[i + 2]));
            used = 3;
        } else if ((used = kit_option(a, v, &run)) == 0 && (used = input_option(a, v)) == 0 &&
                   (used = session_option(a, v)) == 0 && (used = scene_dump_option(a, v)) == 0 &&
                   (used = game_log_option(a, v)) == 0 && (used = play_stats_option(a, v)) == 0 && (used = work_count_option(a, v)) == 0 &&
                   (used = host_window_option(a, v)) == 0 && (used = sat_video_dump_option(a, v)) == 0 && (used = cpu_present_option(a, v)) == 0 &&
                   (used = autoload_option(a, v)) == 0 && (used = sat_movie_option(a, v)) == 0 && (used = sat_net_option(a, v)) == 0 &&
                   (used = bands_option(a, v)) == 0) {
            used = 1; /* unknown: skipped */
        }
    }
    port_log_open(run.log);
    game_log_start();
    sat_net_start(g_image); /* online play: a joiner takes the host's settings and save here */
    host_phangrade_start();
    sat_mem_init();
    sat_sys_init();
    sat_sys_set(SAT_FRAME_HZ, run.fast);
    sat_sys_set_det(run.det || sat_net_on()); /* online play runs on the --det timeline */
    sat_call_init();
    sat_scu_init();
    sat_smpc_init();
    sat_vdp_init();
    sat_scsp_init();
    sat_cd_init(g_cue);
    sat_cdda_enable(!run.headless && !run.fast && !host_fixed_clock() && !g_audio_off);
    sat_audio_enable(!run.headless && !run.fast && !host_fixed_clock() && !g_audio_off);
    if (sat_v1_ground_shadows < 0) sat_v1_ground_shadows = !run.headless && !run.fast && !host_fixed_clock(); /* a play run */
    fast_loads_start(!run.headless && !run.fast && !host_fixed_clock());
    sat_smpc_clock_start(!run.headless && !run.fast && !host_fixed_clock() && !run.det && !sat_net_on());
    sat_movie_start(!run.headless && !run.fast && !host_fixed_clock(), !g_audio_off);
    sat_part_start();
#ifdef SAT_TWIN_STICK
    {
        static const PortTwinStick twin = SAT_TWIN_STICK;
        port_twin_start(&twin);
    }
#else
    port_twin_start(NULL);
#endif
    sat_bup_auto_load(!run.headless && !run.fast && !host_fixed_clock());
    sat_net_started();
    sat_video_options(run.headless, run.shot_vb, run.shot_path, run.shot_every);
    f = fopen(g_image, "rb");
    if (!f) {
        port_log("cannot open the code image %s\n", g_image);
        return 1;
    }
    fseek(f, 0, SEEK_END);
    n = ftell(f);
    fseek(f, 0, SEEK_SET);
    buf = (u8 *)malloc((size_t)n);
    if (fread(buf, 1, (size_t)n, f) != (size_t)n) {
        port_log("short read of %s\n", g_image);
        return 1;
    }
    fclose(f);
    sat_load(SAT_IMAGE_BASE, buf, (u32)n);
    free(buf);
    sat_bios_init();
    port_log("port: image %s (%ld bytes) at %08x, entry %08x, %u functions\n", g_image, n, SAT_IMAGE_BASE, SAT_ENTRY_FN,
             (u32)sat_nfns);
    port_log_flush();
    if (g_watch_s) CreateThread(NULL, 0, watchdog, NULL, 0, NULL);
    th = CreateThread(NULL, 256u << 20, game_thread, NULL, STACK_SIZE_PARAM_IS_A_RESERVATION, NULL);
    WaitForSingleObject(th, INFINITE);
    return 0;
}
