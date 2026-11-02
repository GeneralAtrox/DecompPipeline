/* The kit's start-up, called by the base's entry (base/host_main.cpp): --image IMAGE.bin --entry HEX, with the log (--log) and
 * the base's options. It loads the image, calls the entry point and logs what ends the run. --seconds N ends the run after N
 * seconds whatever the game is doing (0 or none: never, a play run). Bring-up aids stay in the XB_* variables (README.md). */
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "xb.h"
#include "console.h"
#include "game_log.h"
#include "scene_dump.h"
#include "session.h"
#include "play_stats.h"
#include "present.h"
#include "present_cpu.h"
#include "host.h"

void xb_report(void)
{
    FILE *f = NULL;
    char p[540];
    unsigned i;
    port_log("touched %u of %u translated functions\n", xb_touched(), xb_fn_count);
    sprintf_s(p, sizeof p, "%s.touched", port_log_path());
    if (!fopen_s(&f, p, "w") && f) {
        for (i = 0; i < xb_fn_count; i++)
            if (xb_touched_addr(i)) fprintf(f, "%08x\n", xb_touched_addr(i));
        fclose(f);
    }
}

void port_exit(const char *why, int code)
{
    static LONG exiting;
    if (InterlockedExchange(&exiting, 1)) return;
    port_log("exit: %s (%d) after %u frames\n", why, code, xb_frame_number());
    xb_report();
    xb_sound_close();
    play_stats_report();
    game_log_end(!strcmp(why, "the window was closed") ? "the window was closed by the user" : why, code, code != 0);
    port_log_flush();
    ExitProcess((UINT)code);
}

void xb_stop(const char *kind, const char *detail, u32 value)
{
    static LONG stopping;
    FILE *f = NULL;
    char p[540];
    if (InterlockedExchange(&stopping, 1)) Sleep(INFINITE); /* not the tick: another guest thread is reporting its stop, and the first one names the run */
    port_log("STOP %s %08x %s\n", kind, value, detail);
    xb_trace_dump();
    sprintf_s(p, sizeof p, "%s.stop", port_log_path());
    if (!fopen_s(&f, p, "w") && f) {
        fprintf(f, "%s %08x\n", kind, value);
        fclose(f);
    }
    port_exit(kind, 3);
}

static DWORD WINAPI watchdog(LPVOID p)
{
    Sleep((DWORD)(UINT_PTR)p * 1000u); /* not the tick: a watchdog */
    port_log("STOP timeout - the run was still going\n");
    port_exit("watchdog", 4);
    return 0;
}

/* The kernel's KeTimeStampBundle, which the game's GetTickCount (821b8b48) reads straight from memory, so no call asks the kit: the game's 3-second
 * wait after it creates its first save never ended while this stayed 0. Each frame, from the run's clock: +0 and +8 the times in 100 ns, +0x10 the milliseconds. */
static void time_stamp_update(void)
{
    static u32 at;
    u64 ms = tc_timebase() / 49875u;
    for (unsigned i = 0; !at && i < xb_var_count; i++)
        if (xb_var_table[i].ord == 173 && !strcmp(xb_lib_name[xb_var_table[i].lib], "xboxkrnl.exe")) at = xb_var_table[i].value;
    if (!at) return;
    wr64(at, ms * 10000u);
    wr64(at + 8u, ms * 10000u);
    wr32(at + 0x10u, (u32)ms);
    xb_net_game_members_heard((u32)ms);   /* after: 821b58c8 subtracts unsigned, so a member's time ahead of the clock reads as an age of weeks */
}

/* Each shown frame ends here, on the thread that pumps the window: the base's per-frame work, then the wait for the blank (the
 * original's Swap shows the frame on one). */
void xb_tick_end(void)
{
    xb_net_check_tick();
    xb_net_test_stall();
    u32 frame = xb_frame_number();
    game_log_poll(frame);
    scene_dump_poll(frame);
    xb_sound_tick();
    play_stats_shown();
    play_stats_wait(1);
    if (host_fixed_clock()) host_virt_to_tick();
    else pace_next_tick(port_console.tick_hz, NULL);
    play_stats_wait(0);
    xb_pads_poll();
    time_stamp_update();
}

typedef struct KitRun {
    const char *image, *log;
    u32 entry;
    unsigned seconds;
} KitRun;

/* This kit's own options; the arguments taken, 0 if not ours. --game-dir and --save-dir are read where they are used (host_arg). */
static int kit_option(const char *a, const char *v, KitRun *r)
{
    if (!v) return 0;
    if (!strcmp(a, "--image")) r->image = v;
    else if (!strcmp(a, "--entry")) r->entry = (u32)strtoul(v, NULL, 16);
    else if (!strcmp(a, "--log")) r->log = v;
    else if (!strcmp(a, "--seconds")) r->seconds = (unsigned)atoi(v);
    else if (!strcmp(a, "--volume")) audio_set_volume(atoi(v), 0);
    else if (!strcmp(a, "--fixed-clock")) host_set_fixed_clock(atoi(v) != 0);
    else if (!strcmp(a, "--game-dir") || !strcmp(a, "--save-dir")) (void)0;
    else return 0;
    return 2;
}

int port_main(int argc, char **argv)
{
    KitRun run = {NULL, "xbox360_port.log", 0, 0};
    TcArgs a;
    host_set_args(argc, argv);
    for (int i = 1, used; i < argc; i += used) {
        const char *o = argv[i], *v = i + 1 < argc ? argv[i + 1] : NULL;
        if ((used = input_live_option(o, v)) != 0) continue; /* offered first: it owns --fast-until */
        if ((used = kit_option(o, v, &run)) == 0 && (used = input_option(o, v)) == 0 && (used = session_option(o, v)) == 0 &&
            (used = scene_dump_option(o, v)) == 0 && (used = game_log_option(o, v)) == 0 && (used = play_stats_option(o, v)) == 0 &&
            (used = host_window_option(o, v)) == 0 && (used = pace_option(o, v)) == 0 && (used = cpu_present_option(o, v)) == 0)
            used = 1; /* unknown: skipped */
    }
    port_log_open(run.log);
    game_log_start();
    host_phangrade_start();
    if (!run.image || !run.entry) {
        port_log("usage: xbox_run --image IMAGE.bin --entry HEX [--log LOG] [--seconds N]\n");
        return 2;
    }
    if ((g_gfx.headless || host_fixed_clock()) && !g_gfx.aspect_set) { /* checks and dumps stay at the game's own shape */
        g_gfx.aspect_set = 1;
        g_gfx.aspect = 16.0f / 9.0f;
    }
    if (!xb_mem_init(run.image, 0x82000000u)) {
        port_log("cannot set up guest memory from %s\n", run.image);
        return 2;
    }
    if (run.seconds) CreateThread(NULL, 0, watchdog, (LPVOID)(UINT_PTR)run.seconds, 0, NULL);
    /* No pad read here: the base decides once whether the host's devices can be read, at the first read, and before the window
     * opens the answer would be no for the whole run. xb_tick_end reads them. */
    time_stamp_update();
    memset(&a, 0, sizeof a);
    a.sp[0] = 0x700FFF00u;
    port_log("start %08x\n", run.entry);
    (void)tc_call(run.entry, &a);
    port_exit("the entry point returned", 0);
    return 0;
}
