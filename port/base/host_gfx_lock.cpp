// --present-thread's lock around the device context (host.h), split from host_window.cpp at its 400-line cap.
// Nearest existing: host_window.cpp - the window and its events; this lock is shared by both threads and needs none of
// the window's state.
#ifndef NOMINMAX
#define NOMINMAX
#endif
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#include "base.h"
#include "host.h"

// The game's thread is the window's: while it waits it answers messages sent to the window, since a present on the other
// thread can wait for those. It says it is waiting, so the other thread, which draws frame after frame, cannot keep it out.
static CRITICAL_SECTION g_gfx_cs;
static volatile LONG g_gfx_mt, g_gfx_wanted, g_gfx_waits; static LONGLONG g_gfx_wait_us; /* the game's thread's waits (gfx_lock_waits) */
static __declspec(thread) int g_gfx_present_thread;

extern "C" void gfx_lock_on(void)
{
    InitializeCriticalSection(&g_gfx_cs);
    g_gfx_mt = 1;
}

extern "C" void gfx_lock(void)
{
    if (!g_gfx_mt) return;
    if (g_gfx_present_thread) {
        EnterCriticalSection(&g_gfx_cs);
        return;
    }
    InterlockedIncrement(&g_gfx_wanted);
    if (!TryEnterCriticalSection(&g_gfx_cs)) {
        LARGE_INTEGER a, b, f;
        QueryPerformanceCounter(&a);
        do {
            MSG m;
            PeekMessageW(&m, NULL, 0, 0, PM_NOREMOVE | PM_QS_SENDMESSAGE);
            SwitchToThread();
        } while (!TryEnterCriticalSection(&g_gfx_cs));
        QueryPerformanceCounter(&b);
        QueryPerformanceFrequency(&f);
        g_gfx_waits++, g_gfx_wait_us += (b.QuadPart - a.QuadPart) * 1000000 / f.QuadPart;
        if ((b.QuadPart - a.QuadPart) * 10 >= f.QuadPart)
            port_log("gfx: the game's thread waited %lld ms for the device context\n", (b.QuadPart - a.QuadPart) * 1000 / f.QuadPart);
    }
    InterlockedDecrement(&g_gfx_wanted);
}

extern "C" void gfx_unlock(void)
{
    if (g_gfx_mt) LeaveCriticalSection(&g_gfx_cs);
}

extern "C" void gfx_lock_present_thread(void) { g_gfx_present_thread = 1; }
extern "C" int gfx_on_present_thread(void) { return g_gfx_present_thread; }
extern "C" int gfx_lock_wanted(void) { return g_gfx_wanted != 0; }
extern "C" void gfx_lock_waits(unsigned *n, unsigned long long *us) { *n = (unsigned)g_gfx_waits, *us = (unsigned long long)g_gfx_wait_us; }
