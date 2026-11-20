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
#include "frame_capture.h"   /* fcap_arm: XB_FCAP_AT */
#include "session.h"
#include "play_stats.h"
#include "xv_simd.h"   /* xb_vmx_mode: the vector helpers' route (SSE, the scalar answer key, or both checked) */
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

/* XB_VMX_CHECK=1: per vector helper, the calls and those whose SSE result differs from the scalar one in any of its 16
 * bytes (the scalar helpers record no VSCR[SAT], so there is no SAT bit to compare) */
#define VMX_IDS 128
static volatile LONG64 vmx_calls[VMX_IDS], vmx_bad[VMX_IDS];
static const char *volatile vmx_names[VMX_IDS];
void xb_vmx_check(int id, const char *name, const u32 *f, const u32 *s)
{
    if (id < 0 || id >= VMX_IDS) return;
    vmx_names[id] = name;
    InterlockedIncrement64(&vmx_calls[id]);
    if (f[0] != s[0] || f[1] != s[1] || f[2] != s[2] || f[3] != s[3]) InterlockedIncrement64(&vmx_bad[id]);
}
static void vmx_check_report(const char *when)
{
    long long calls = 0, bad = 0;
    int i;
    for (i = 0; i < VMX_IDS; i++) {
        if (!vmx_names[i]) continue;
        calls += vmx_calls[i];
        bad += vmx_bad[i];
        port_log("vmx check: %s %s calls %lld mismatches %lld\n", when, vmx_names[i], (long long)vmx_calls[i], (long long)vmx_bad[i]);
    }
    port_log("vmx check: %s all calls %lld mismatches %lld\n", when, calls, bad);
    port_log_flush();
}
static DWORD WINAPI vmx_check_logger(LPVOID p)
{
    (void)p;
    for (;;) {
        Sleep(5000);
        vmx_check_report("at");
    }
}

void port_exit(const char *why, int code)
{
    static LONG exiting;
    if (InterlockedExchange(&exiting, 1)) return;
    port_log("exit: %s (%d) after %u frames\n", why, code, xb_frame_number());
    if (xb_vmx_mode == 2) vmx_check_report("exit");
    xb_report();
    xb_prof_write();
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
    xb_threads_dump();
    xb_cs_waits_dump();
    port_exit("watchdog", 4);
    return 0;
}

/* XB_SAMPLE=N: every N seconds, each guest thread's stack and the file reads since the last sample, so a screen that never moves on says whether
 * the game is loading or waiting (Too Human's quote card stayed 400 s and more after its logos) */
static DWORD WINAPI sampler(LPVOID p)
{
    ULONGLONG t0 = GetTickCount64();
    for (;;) {
        Sleep((DWORD)(UINT_PTR)p * 1000u);   /* not the tick: a sampler */
        port_log("sample at %.0f s, frame %u\n", (GetTickCount64() - t0) / 1000.0, port_vblank_count());
        xb_file_reads_since();
        xb_d3d_times_since();
        xb_import_counts_since();
        xb_peek_dump();
        xb_threads_dump();
        xb_cs_waits_dump();
    }
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
    {   /* XB_FCAP_AT=f[,f...] with XB_FCAP_DIR (bring-up): the frame capture of the frame after each, into XB_FCAP_DIR\fcap_<f>, several in one run (the
         * scene dump's --scene-dump-at takes one); scene_dump_poll has just ended the one before */
        static const char *at = (const char *)1;
        if (at == (const char *)1) at = getenv("XB_FCAP_DIR") ? getenv("XB_FCAP_AT") : 0;
        for (const char *p = at; p && *p;) {
            char *e;
            unsigned long f = strtoul(p, &e, 10);
            if (e == p) break;
            if (f == frame) {
                char dir[400];
                sprintf_s(dir, sizeof dir, "%s\\fcap_%05lu", getenv("XB_FCAP_DIR"), f);
                CreateDirectoryA(getenv("XB_FCAP_DIR"), NULL), CreateDirectoryA(dir, NULL);
                fcap_arm(dir);
                port_log("frame capture (XB_FCAP_AT) armed at frame %u into %s\n", frame, dir);
            }
            p = *e ? e + 1 : e;
        }
    }
    {   /* XB_SHOT_DIR with XB_SHOT_AT=from:to:step (a diagnostic, never on in play): the shown picture of every step-th frame from..to as
         * shot_<frame>.bmp, for picture series against a reference (the intro against Xenia's, 2026-11-15) without a scene dump each */
        static int set = -1;
        static unsigned long from, to, step;
        if (set < 0) set = getenv("XB_SHOT_DIR") && getenv("XB_SHOT_AT") && sscanf_s(getenv("XB_SHOT_AT"), "%lu:%lu:%lu", &from, &to, &step) == 3 && step;
        if (set > 0 && frame >= from && frame <= to && !((frame - from) % step)) {
            char path[400];
            CreateDirectoryA(getenv("XB_SHOT_DIR"), NULL);
            sprintf_s(path, sizeof path, "%s\\shot_%05u.bmp", getenv("XB_SHOT_DIR"), frame);
            console_capture_bmp(path);
        }
    }
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
    { /* the vector helpers (runtime/xv_simd.h): SSE, unless XB_VMX_SCALAR=1 or the host lacks SSSE3 / SSE4.1 (cpuid 1: ecx 9,
       * 19); XB_VMX_CHECK=1 runs both and counts differing results (xb_vmx_check) */
        int c[4];
        __cpuid(c, 1);
        if ((getenv("XB_VMX_SCALAR") && atoi(getenv("XB_VMX_SCALAR")) > 0) || !(c[2] & (1 << 9)) || !(c[2] & (1 << 19)))
            xb_vmx_mode = 1;
        else if (getenv("XB_VMX_CHECK") && atoi(getenv("XB_VMX_CHECK")) > 0) {
            xb_vmx_mode = 2;
            CreateThread(NULL, 0, vmx_check_logger, NULL, 0, NULL);
        }
        port_log("vector helpers: %s; float multiply-adds: %s\n", xb_vmx_mode == 1 ? "scalar (XB_VMX_SCALAR=1 or no SSSE3 / SSE4.1)"
                                         : xb_vmx_mode == 2 ? "check (XB_VMX_CHECK=1: SSE and scalar, scalar used)" : "SSE",
                 xs_fma_ok() ? "FMA3" : "scalar (no FMA3)");
    }
    if (getenv("XB_SAMPLE") && atoi(getenv("XB_SAMPLE")) > 0) {
        xb_import_counts_start();
        CreateThread(NULL, 0, sampler, (LPVOID)(UINT_PTR)atoi(getenv("XB_SAMPLE")), 0, NULL);
    }
    xb_prof_start();
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
