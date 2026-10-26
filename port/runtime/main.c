/* Dreamcast port kit: the guest side of start-up (the host's main, host/host_main.cpp, comes first). Maps guest
 * RAM, loads the boot file from the user's disc image into it, installs the guest-call dispatcher and runs the game's
 * entry function (game.json "entry", called by the generated port_entry.c) until it stops, faults or the watchdog
 * fires. Functions with no readable C and no platform-layer replacement are logging stubs (tools/gen_port.py).
 *
 *   port.exe --disc <image.cue|image.gdi> [--seconds N] [--log FILE] [--check-fsca FILE] [--dump-boot FILE]
 *            [--must-work FILE]   (stop at the first call of a listed stub)
 *            [--fixed-clock 1]    (virtual time: the TMU, the vertical blank and the RTC repeat exactly)
 *            [--fast-until N]     (the fixed clock until vertical blank N, then real time: watch from there)
 *            [--headless] [--warp] [--window WxH] [--size WxH]   (runtime/gfx: no window, the WARP rasterizer, a
 *                                  window instead of full screen, the headless output size; default 640x480)
 *            [--dump-frames N,N,..] [--dump-every N] [--dump-dir DIR]   (frame PNGs, gfx_capture.c)
 *            [--input START@300,A@600+30,..]   (scripted presses at vertical blanks, gfx/gfx_input.c)
 *            [--save-dir DIR] [--vmu A1,B1,..|none]   (save files, default data\saves beside port.exe and none
 *                                  kept by a --fixed-clock run; the memory card sockets, sdk/bu_vmu.c, default A1)
 *            [--settings FILE|none]   (the saved options, host/host_settings.cpp; default data\settings.txt)
 *            [--audio on|off] [--audio-dump FILE.wav]   (the sound device, off by default in --headless and
 *                                  --fixed-clock runs; the mix as a WAV file, gfx/gfx_audio.c)
 *            [--present tick|interp] [--interp-frames N] [--interp-verify 1] [--fps N]   (in-between frames,
 *                                  frame_extra.c)
 *            [--thunks on|off]    (calls through guest pointers jump straight to the host function, dispatch.c)
 *            [--replay FILE.ptrec] [--record off]   (the session recording from boot, session.c)
 *            [--scene-dump-at V] [--scene-dir DIR] [--stop-at V]   (Copy agent prompt, scene_dump.c)
 *            [--lockstep-spec FILE] [--lockstep-trace FILE] [--lockstep FILE] [--lockstep-end exit]
 *            [--lockstep-watch W@N]
 *                                  (the lockstep check, lockstep.c)
 *            [--hw-out FILE] [--hw-tex FILE] [--hw-strip L:S:V@F] [--hw-watch-ram A@F] [--hw-watch-vram A:N@F]
 *                                  (the hardware check, with --lockstep; hw_check.c)
 *            [--ssaa 2|4] [--aniso N]   (look-review prototypes, off by default: supersampling, anisotropic
 *                                  filtering; runtime/gfx/gfx_present.c, pvr/pvr_states.c)
 */
#include <stdlib.h>
#include <string.h>
#include <windows.h>
#include "port.h"
#include "platform.h"
#include "gfx/gfx.h"
#include "host.h"
#include "scene_dump.h"
#include "session.h"
#include "lockstep.h"

#ifdef PORT_CHECKED
#include <rtcapi.h>
#include <stdarg.h>
#include <wchar.h>
#endif

static HANDLE g_game_thread;
static DWORD g_seconds = 20; /* --seconds: the watchdog; 0 none (the host passes 0 for a launcher's play run) */
static int g_thunks = 1; /* --thunks on|off: dispatch.c's jump thunks */
static volatile LONG g_exiting;

/* Text on the stack (a message being formatted or shown): runs of 16 or more printable characters. */
static void log_stack_strings(const u32 *sp, u32 bytes)
{
    const char *p = (const char *)sp, *end = p + bytes;
    MEMORY_BASIC_INFORMATION mi;
    if (VirtualQuery(sp, &mi, sizeof mi) && (const char *)mi.BaseAddress + mi.RegionSize < end)
        end = (const char *)mi.BaseAddress + mi.RegionSize;
    for (int shown = 0; p < end && shown < 8;) {
        const char *s = p;
        while (s < end && *s >= ' ' && *s < 0x7f) s++;
        if (s - p >= 16) {
            port_log("  text at stack+%03x: %.*s\n", (u32)(p - (const char *)sp), (int)(s - p > 200 ? 200 : s - p), p);
            shown++;
        }
        p = s + 1;
    }
}

static void log_game_thread(void)
{
    CONTEXT c;
    memset(&c, 0, sizeof c);
    c.ContextFlags = CONTEXT_CONTROL | CONTEXT_INTEGER;
    if (SuspendThread(g_game_thread) == (DWORD)-1 || !GetThreadContext(g_game_thread, &c)) return;
    HMODULE m = GetModuleHandleW(NULL);
    const IMAGE_NT_HEADERS *nt = (const IMAGE_NT_HEADERS *)((const u8 *)m + ((const IMAGE_DOS_HEADER *)m)->e_lfanew);
    uintptr_t lo = (uintptr_t)m, hi = lo + nt->OptionalHeader.SizeOfImage;
    port_log("game thread: eip port.exe+%08x esp %08lx\n", (u32)(c.Eip - lo), c.Esp);
    /* Words on the stack that point into the executable: return addresses (and some function pointers). */
    const u32 *sp = (const u32 *)(uintptr_t)c.Esp;
    for (int i = 0, n = 0; i < 256 && n < 16; i++)
        if (sp[i] > lo && sp[i] < hi) {
            port_log("  stack+%03x: port.exe+%08x\n", i * 4, (u32)(sp[i] - lo));
            n++;
        }
    log_stack_strings(sp, 0x2000);
}

void port_exit(const char *why, int code)
{
    if (InterlockedExchange(&g_exiting, 1)) return;
    port_log("EXIT: %s after %u vertical blanks\n", why, port_vblank_count());
    dispatch_report();
    port_stub_report();
    port_extra_report();
    lockstep_close();
    port_log_flush();
    ExitProcess((UINT)code);
}

#ifdef PORT_CHECKED
/* A checked build's run-time check failure (tools/build.py --checked): the message names the local a callee wrote
 * past. It ends the run with exit code 3, like a must-work stub. */
static int __cdecl rtc_report(int type, const wchar_t *file, int line, const wchar_t *module, const wchar_t *fmt, ...)
{
    wchar_t msg[512];
    va_list ap;
    (void)type;
    (void)file;
    (void)line;
    (void)module;
    va_start(ap, fmt);
    _vsnwprintf_s(msg, 512, _TRUNCATE, fmt, ap);
    va_end(ap);
    port_log("RTC: %ls\n", msg);
    port_exit("run-time check failure", 3);
    return 1;
}
#endif

static DWORD WINAPI watchdog(void *arg)
{
    (void)arg;
    Sleep(g_seconds * 1000u);
    port_log("WATCHDOG: %lu s elapsed\n", g_seconds);
    log_game_thread();
    port_exit("watchdog", 2);
    return 0;
}

/* 0 when the boot file is loaded, else the exit code: 4 the disc is missing or unreadable, 5 it has no boot file. */
static int load_boot(const char *image, const char *dump)
{
    PortDisc *d = disc_open(image);
    char boot[32];
    u32 size = 0;
    if (!d) {
        port_log("disc: cannot open %s\n", image);
        return 4;
    }
    if (PORT_BOOT_FILE[0]) { /* game.json "boot_file" */
        strncpy_s(boot, sizeof boot, PORT_BOOT_FILE, _TRUNCATE);
    } else if (!disc_boot_name(d, boot, sizeof boot)) {
        port_log("disc: no IP.BIN boot file name\n");
        disc_close(d);
        return 5;
    }
    u8 *data = disc_read_file(d, boot, &size);
    if (!data || !guest_load(PORT_IMAGE_BASE, data, size)) {
        port_log("disc: cannot load %s\n", boot);
        free(data);
        disc_close(d);
        return 5;
    }
    disc_set_run(d); /* kept open for the game's gdFs reads */
    if (g_thunks) dispatch_thunks(PORT_IMAGE_BASE, PORT_IMAGE_BASE + size);
    port_log("disc: %s, %u bytes loaded at %08x\n", boot, size, PORT_IMAGE_BASE);
    if (dump) {
        FILE *f = NULL;
        if (fopen_s(&f, dump, "wb") == 0 && f) {
            fwrite(data, 1, size, f);
            fclose(f);
        }
    }
    free(data);
    return 0;
}

/* Called by the host's main (host/host_main.cpp) with the saved settings and the defaults before the command line. */
int port_main(int argc, char **argv)
{
    const char *image = NULL, *log = "port.log", *fsca = NULL, *dump = NULL, *must = NULL;
    host_set_args(argc, argv);
    for (int i = 1, used; i < argc; i += used) {
        const char *a = argv[i], *v = i + 1 < argc ? argv[i + 1] : NULL;
        used = v ? 2 : 1;
        if (!strcmp(a, "--disc") && v) image = v;
        else if (!strcmp(a, "--seconds") && v) g_seconds = (DWORD)atoi(v);
        else if (!strcmp(a, "--log") && v) log = v;
        else if (!strcmp(a, "--check-fsca") && v) fsca = v;
        else if (!strcmp(a, "--dump-boot") && v) dump = v;
        else if (!strcmp(a, "--must-work") && v) must = v;
        else if (!strcmp(a, "--fixed-clock") && v) host_set_fixed_clock(atoi(v) != 0);
        else if (!strcmp(a, "--fast-until") && v) {
            host_set_fixed_clock(1);
            host_set_fast_until((u32)strtoul(v, NULL, 10));
        }
        else if (!strcmp(a, "--save-dir") && v) host_set_save_dir(v);
        else if (!strcmp(a, "--thunks") && v) g_thunks = strcmp(v, "off") != 0;
        else if ((used = input_option(a, v)) == 0 && (used = audio_option(a, v)) == 0 && (used = gfx_option(a, v)) == 0
                 && (used = extra_option(a, v)) == 0 && (used = session_option(a, v)) == 0 &&
                 (used = scene_dump_option(a, v)) == 0 &&
                 (used = lockstep_option(a, v)) == 0)
            used = 1; /* unknown: skipped */
    }
    port_log_open(log);
    host_phangrade_start();
#ifdef PORT_CHECKED
    _RTC_SetErrorFuncW(rtc_report);
#endif
    if (!image) {
        port_log("usage: port --disc <image.cue|image.gdi> [--seconds N] [--log FILE] [--check-fsca FILE]\n");
        return 1;
    }
    if (must && !port_stub_must_work(must)) {
        port_log("must-work list: cannot read %s\n", must);
        return 1;
    }
    if (!guest_mem_init() || !sh4_rt_init(fsca)) return 1;
    if (!gfx_init()) return 1;
    if (!dispatch_install()) {
        port_log("dispatch: cannot install the handler\n");
        return 1;
    }
    port_log("dispatch: %d guest functions\n", dispatch_count());
    int boot = load_boot(image, dump);
    if (boot) return boot;
    DuplicateHandle(GetCurrentProcess(), GetCurrentThread(), GetCurrentProcess(), &g_game_thread, 0, FALSE,
                    DUPLICATE_SAME_ACCESS);
    if (g_seconds) CreateThread(NULL, 0, watchdog, NULL, 0, NULL);
    port_log("start: %s (%08x)\n", PORT_ENTRY_NAME, PORT_ENTRY_ADDR);
    port_log_flush();
    sh4_rt_set_fpu_mode();
    port_game_entry();
    port_exit("the entry function returned", 0);
    return 0;
}
