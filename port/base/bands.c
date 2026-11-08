/* Work split across cores in bands (bands.h): one helper thread for each band after the first, idle on its own event
 * until a bands_run, and a countdown the last one to finish signals. Helpers start when a count first asks for them.
 * One thread runs the bands (the console's).
 *
 * Nearest existing: runtime/frame_copy.c, the Dreamcast's helpers that copy guest RAM in slices: the same helpers and
 * countdown, but they run only that copy; these run any band function, for any console. */
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <windows.h>
#include "base.h"
#include "bands.h"

#define BANDS_DEFAULT 4u /* the game, the showing thread and audio keep cores of their own */
static unsigned g_want, g_bands, g_started = 1u; /* --bands; the count of this run; threads that can take a band */
static HANDLE g_go[BANDS_MAX], g_done;
static volatile LONG g_left;
static BandFn g_fn;
static void *g_ctx;

static DWORD WINAPI helper(void *arg)
{
    unsigned b = (unsigned)(uintptr_t)arg;
    for (;;) {
        WaitForSingleObject(g_go[b], INFINITE); /* idle until the next run */
        g_fn(g_ctx, b, g_bands);
        if (InterlockedDecrement(&g_left) == 0) SetEvent(g_done);
    }
}

int bands_option(const char *arg, const char *value)
{
    if (!value || strcmp(arg, "--bands")) return 0;
    g_want = (unsigned)atoi(value);
    return 2;
}

void bands_set(unsigned n) { g_want = n; }

/* the count asked for, less any helper that could not be started */
unsigned bands_count(void)
{
    static unsigned cores;
    unsigned n = g_want;
    if (!cores) {
        SYSTEM_INFO si;
        GetSystemInfo(&si);
        cores = si.dwNumberOfProcessors ? si.dwNumberOfProcessors : 1u;
    }
    if (!n) n = cores < BANDS_DEFAULT ? cores : BANDS_DEFAULT;
    if (n > BANDS_MAX) n = BANDS_MAX;
    if (!g_done && n > 1u) g_done = CreateEventW(NULL, FALSE, FALSE, NULL);
    while (g_started < n && g_done) {
        HANDLE t;
        g_go[g_started] = CreateEventW(NULL, FALSE, FALSE, NULL);
        t = g_go[g_started] ? CreateThread(NULL, 0, helper, (void *)(uintptr_t)g_started, 0, NULL) : NULL;
        if (!t) break;
        SetThreadPriority(t, THREAD_PRIORITY_ABOVE_NORMAL);
        CloseHandle(t);
        g_started++;
    }
    return n < g_started ? n : g_started;
}

void bands_run(BandFn fn, void *ctx)
{
    unsigned n = g_bands = bands_count();
    g_fn = fn;
    g_ctx = ctx;
    g_left = (LONG)(n - 1u);
    for (unsigned b = 1; b < n; b++) SetEvent(g_go[b]);
    fn(ctx, 0, n);
    if (n > 1u && WaitForSingleObject(g_done, 10000) != WAIT_OBJECT_0) port_exit("bands: a helper did not finish its band in 10 s", 1);
}
