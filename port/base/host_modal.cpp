// The window's modal loops (a titlebar menu held open, the title bar dragged or a border resized) without stopping the
// game. Windows runs such a loop inside the message that starts it, on the window's thread, until the menu closes or
// the mouse is let go; the window's thread is the game's (gfx_pump takes the events inside the tick), so the game used
// to stop for as long as the menu stayed open: no ticks, no frames, no sound (one game on 13 Nov: 38.9 s with
// the menu open, ended by the click that closed it).
//
// gfx_pump hands its event loop to modal_pump, which runs it on a fiber of its own (the pump fiber); the game's fiber
// (whichever one called gfx_pump: the thread itself, or a library context's host coroutine) waits for it as before.
// When a modal loop starts inside it, a timer is set; each WM_TIMER the loop hands out switches back to the game's
// fiber, which returns from its gfx_pump and runs on to its next one (a tick, paced as always), and that gfx_pump
// switches back into the loop. The events that the loop holds back reach the game when it ends, as before. Without a
// modal loop the order of everything is as before: the events are taken at the same gfx_pump, then the game goes on.
// No thread is added and nothing runs at once with anything else, so the game's state is touched only by its fiber.
// A host that cannot make fibers takes the events in place, as before.
#ifndef NOMINMAX
#define NOMINMAX
#endif
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#include <commctrl.h>
#include <SDL3/SDL.h>
#include "console.h"
#include "host.h"
#include "game_log.h"
#include "host_internal.hpp"

namespace {

constexpr UINT_PTR kTimer = 0x6d6f64; // this file's timer and subclass id on the window
constexpr ULONGLONG kSliceMs = 12;    // a WM_TIMER lends the game ticks until this long has passed (one is ~16.7 ms)

void *g_pump;                // the pump fiber, made at the first gfx_pump with a window
void *g_game;                // the fiber that called gfx_pump: the one a lent tick resumes
void (*g_poll)();            // the event loop the pump fiber runs
bool g_failed, g_polling, g_lent, g_modal;
const char *g_what = "menu"; // what holds the loop: a menu, or a move or resize (a drag)
ULONGLONG g_since;           // when the modal loop started
u32 g_blank0;                // port_vblank_count() when it started

void CALLBACK pump_main(void *)
{
    for (;;) {
        g_polling = true;
        g_poll();
        g_polling = false;
        SwitchToFiber(g_game);
    }
}

void modal_start(HWND hwnd, const char *what)
{
    if (g_modal || !g_polling || GetCurrentFiber() != g_pump) return; // a loop in the level editor or a pause: as before
    g_modal = true;
    g_what = what;
    g_since = GetTickCount64();
    g_blank0 = port_vblank_count();
    SetTimer(hwnd, kTimer, USER_TIMER_MINIMUM, nullptr);
    game_log_line("window: %s open; the game runs on under it", what);
}

void modal_end(HWND hwnd)
{
    if (!g_modal) return;
    KillTimer(hwnd, kTimer);
    g_modal = false;
    game_log_line("window: %s closed after %.1f s, %u ticks run under it", g_what, (GetTickCount64() - g_since) / 1000.0,
                  port_vblank_count() - g_blank0);
}

// One WM_TIMER from the modal loop: the game's fiber runs to its next gfx_pump, again until a slice has passed. The
// loop gets the input between slices (Windows hands out WM_TIMER only when nothing else is waiting).
void lend()
{
    if (!g_modal || !g_polling || g_lent || GetCurrentFiber() != g_pump) return;
    ULONGLONG start = GetTickCount64();
    g_lent = true;
    do {
        SwitchToFiber(g_game);
    } while (GetTickCount64() - start < kSliceMs);
    g_lent = false;
}

LRESULT CALLBACK subclass(HWND hwnd, UINT msg, WPARAM wp, LPARAM lp, UINT_PTR, DWORD_PTR)
{
    switch (msg) {
    case WM_ENTERMENULOOP: modal_start(hwnd, "menu"); break;
    case WM_ENTERSIZEMOVE: modal_start(hwnd, "move or resize"); break;
    case WM_EXITMENULOOP:
    case WM_EXITSIZEMOVE: modal_end(hwnd); break;
    case WM_TIMER:
        if (wp != kTimer) break;
        lend();
        return 0;
    default: break;
    }
    return DefSubclassProc(hwnd, msg, wp, lp);
}

bool setup()
{
    HWND hwnd = static_cast<HWND>(host_window_hwnd());
    void *self = IsThreadAFiber() ? GetCurrentFiber() : ConvertThreadToFiber(nullptr);
    if (hwnd && self) g_pump = CreateFiberEx(0x10000, 0x100000, 0, pump_main, nullptr); // 1 MB, the main thread's
    if (g_pump && !SetWindowSubclass(hwnd, subclass, kTimer, 0)) {
        DeleteFiber(g_pump);
        g_pump = nullptr;
    }
    port_log("host: window events %s\n", g_pump ? "on their own fiber (a menu or drag does not stop the game)"
                                               : "in place (no fiber: a menu or drag stops the game while held)");
    return g_pump != nullptr;
}

} // namespace

namespace host {

void modal_pump(void (*poll)())
{
    if (!g_pump) {
        if (!g_failed) g_failed = !setup();
        if (g_failed) {
            poll();
            return;
        }
    }
    if (g_lent) { // a lent tick has reached its gfx_pump: back into the modal loop (the game resumes at the next)
        g_game = GetCurrentFiber();
        SwitchToFiber(g_pump);
        return;
    }
    g_game = GetCurrentFiber();
    g_poll = poll;
    SwitchToFiber(g_pump);
}

} // namespace host
