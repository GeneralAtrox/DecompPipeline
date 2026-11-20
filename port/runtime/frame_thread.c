/* --present-thread (frame_extra.c): a scene-blended game's in-between frames are drawn and shown by this thread, so a tick
 * of any length takes no time from them. No game code runs for one. The device context is shared under gfx_lock; this
 * thread gives way whenever the game's thread wants it. The renderer and gfx_present hold it back while the frame target
 * has a frame not yet counted or shown, and a vertical blank's wait lets it go on; under --present-thread on only a frame
 * with nothing to blend holds it, and a render's end lets it go on. A game with its own draw function (port_extra_thread_own)
 * draws and shows its in-between frames on its own thread; this one only shows the last shown frame again when the tick
 * has shown none for a slot (a long game step, the logos, a menu), so the screen keeps its rate. A render holds it until
 * its frame is shown (gfx_show), so no frame is shown early and none out of order. No game code runs here. */
#include <windows.h>
#include <timeapi.h>
#include "port.h"
#include "platform.h"
#include "frame_cost.h"

static int g_up;
static volatile LONG g_go;
static HANDLE g_wake;
static u64 g_start_us; /* when the frame was rendered */
static u64 g_period_us; /* --present-thread on: from the render before to that one */
static int g_early;   /* --present-thread on: nothing holds this thread; a render's end restarts its clock */
static u64 g_slot_us, g_nap_us; /* --fps N: when the next in-between frame is due to be shown; how long until it is */
static int g_own;               /* the game's own draw function: this thread only shows the last frame again */
static u64 g_own_shown_us, g_own_nap; /* when a frame was last shown; how long until showing it again is due */
#define OWN_GRACE_US 1500u      /* past its slot: the tick's own frame comes first unless it is this late */

static u64 now_us(void)
{
    LARGE_INTEGER t, f;
    QueryPerformanceCounter(&t);
    QueryPerformanceFrequency(&f);
    return (u64)t.QuadPart * 1000000u / (u64)f.QuadPart;
}

/* --fps N (frame_extra.c): 0 if an in-between frame that costs about cost_us, started now, would be shown in its slot or
 * after it; otherwise how long before it should start. The slots are 1/N s apart, not 1/N s after the last frame: Sleep(1)
 * and the drawing's own jitter were added to every gap, so a cap of 150 on a fight gave a p99 of 9.32 ms against 4.91
 * uncapped. A frame shown late is followed by one shown that much sooner, so the rate stays N; one late by a whole slot
 * (a load) starts the slots again from it, so none come in a burst. */
/* With no window on the real clock (--no-window) nothing takes a frame, so gfx_can_show never holds the thread back:
 * uncapped it queued draw after draw on the graphics card while holding the device context, and one game's
 * thread waited 224 ms for it. It keeps to 144 then. */
static u32 cap(u32 fps)
{
    static int headless = -1;
    if (headless < 0) {
        PortView v;
        console_view(&v);
        headless = v.headless;
    }
    return fps || !headless || host_fixed_clock() ? fps : 144u;
}

u64 port_extra_cap_wait(u64 now, u32 fps, u64 cost_us)
{
    fps = cap(fps);
    g_nap_us = fps && now + cost_us < g_slot_us ? g_slot_us - now - cost_us : 0u;
    return g_nap_us;
}

void port_extra_cap_shown(u64 at_us, u32 fps)
{
    u64 period;
    fps = cap(fps);
    period = fps ? 1000000u / fps : 0u;
    if (fps) g_slot_us = at_us > g_slot_us + period ? at_us + period : g_slot_us + period;
}

/* This thread's own wait for its slot: a high-resolution sleep (base/pace.c) to about half a millisecond early, and
 * the rest spins. It reads the counter directly: pace_nap_until is the tick's, on the game's clock. */
static void nap_to_slot(u64 nap)
{
    u64 until = now_us() + nap;
    if (nap > 600u) pace_sleep_us(nap - 500u);
    while (now_us() < until && !gfx_lock_wanted()) YieldProcessor();
}

/* No pacing here: the thread takes the device context whenever the game's thread does not want it. Its Present never
 * waits for the display holding gfx_lock (--present-nowait, base/present.c), so the game's thread waits at most for an
 * in-between frame's draw and copy. The pacing it replaced (--present-pace, game.json present_pace: stay off the device
 * around renders, the tick's own frames shown by this thread) cost 60% of the in-between frames for 40 ms of waits in 5 s
 * (build box A, a battle replay with a 7 ms Present, 2026-11-14), and was one game's switch; removed in contract 2.14.0. */
/* Every 5 s of real time, beside gfx's own line: how long this thread held gfx_lock, and on what: the copy into the back
 * buffer and the Present (base/present.c, gfx_lock_split; the Present holds --present-stall-us too), and the draw, which
 * is the rest. A retry (gfx_present_retry) holds it only for its Present. A measurement only. */
static u64 g_held_us, g_retry_ticks, g_split_from;
static void lock_split_line(u64 now)
{
    static u64 copy0, present0, retry0;
    u64 copy, present, retry, f;
    LARGE_INTEGER q;
    if (!g_split_from) g_split_from = now;
    if (now - g_split_from < 5000000u) return;
    QueryPerformanceFrequency(&q);
    f = (u64)q.QuadPart;
    gfx_lock_split(&copy, &present);
    retry = g_retry_ticks;
    copy -= copy0, present -= present0, retry -= retry0, copy0 += copy, present0 += present, retry0 += retry;
    copy = copy * 1000000u / f, present = present * 1000000u / f, retry = retry * 1000000u / f;
    port_log("frame_extra: this thread held the device context %.1f ms in those %.1f s: draw %.1f, copy %.1f, Present %.1f "
             "(%.1f of it in retries)\n", (g_held_us + retry) / 1000.0, (now - g_split_from) / 1e6,
             (g_held_us > copy + present - retry ? g_held_us - copy - present + retry : 0u) / 1000.0, copy / 1000.0,
             present / 1000.0, retry / 1000.0);
    g_held_us = 0, g_split_from = now;
}

static DWORD WINAPI present_thread(void *arg)
{
    u64 done = 0; /* the frame last drawn with nothing left to blend: drawing it again would show nothing new */
    (void)arg;
    gfx_lock_present_thread();
    for (;;) {
        int what = 0, finished = 0;
        u64 held;
        u64 t0 = g_xcost ? xcost_now() : 0u; /* --present-cost */
        if (gfx_present_pending()) { /* a Present that would have waited (base/present.c): again, no lock held between */
            u64 c, p0, p1;
            gfx_lock_split(&c, &p0);
            if (gfx_lock_wanted() || gfx_present_retry()) pace_sleep_us(250u);
            gfx_lock_split(&c, &p1);
            g_retry_ticks += p1 - p0, lock_split_line(now_us());
            if (t0) xcost_add(XC_SWAP, xcost_now() - t0);
            continue;
        }
        if (!g_go) {
            WaitForSingleObject(g_wake, 50);
            if (t0) xcost_add(XC_IDLE, xcost_now() - t0);
            continue;
        }
        if (gfx_lock_wanted()) {
            SwitchToThread(); /* not the tick: the thread of in-between frames gives way */
            if (t0) xcost_add(XC_LOCK, xcost_now() - t0);
            continue;
        }
        gfx_lock();
        held = now_us();
        if (t0) xcost_add(XC_LOCK, xcost_now() - t0);
        if (g_go && (done != g_start_us || (g_early && !pvr_blend_ready())))
            what = port_extra_thread_show(now_us(), g_start_us, &finished);
        if (finished) done = g_start_us;
        gfx_unlock();
        g_held_us += now_us() - held, lock_split_line(now_us());
        t0 = g_xcost ? xcost_now() : 0u;
        if (what == 2) gfx_show_wait();
        else if (what == 3) nap_to_slot(g_own == 1 ? g_own_nap : g_nap_us); /* --fps: not yet */
        else if (!what) WaitForSingleObject(g_wake, 1); /* nothing to draw: a render's end (g_wake) goes on at once, where
                                                           Sleep(1) noticed it 1-2 ms late, a late frame every tick */
        if (t0) xcost_add(what == 2 ? XC_SWAP : XC_IDLE, xcost_now() - t0);
    }
}

void port_extra_thread_begin(int wanted)
{
    HANDLE t;
    if (!g_early || !g_up) g_start_us = now_us();
    if (g_up || !wanted) return;
    g_up = -1; /* tried */
    timeBeginPeriod(1); /* Sleep(1) otherwise sleeps a scheduler tick (15 ms): as long as two frames */
    g_wake = CreateEventW(NULL, FALSE, FALSE, NULL);
    gfx_lock_on();
    t = g_wake ? CreateThread(NULL, 0, present_thread, NULL, 0, NULL) : NULL;
    if (!t) {
        port_log("frame_extra: no thread for in-between frames; the tick draws them\n");
        return;
    }
    SetThreadPriority(t, THREAD_PRIORITY_ABOVE_NORMAL);
    CloseHandle(t);
    g_up = 1;
    gfx_present_nowait(gfx_present_nowait_wanted()); /* a Present never waits for the display holding gfx_lock */
    port_log("frame_extra: in-between frames on a thread of their own (--present-thread)\n");
}

int port_extra_thread_on(void) { return g_up == 1; }

void port_extra_thread_hold(void)
{
    if (!g_early || !pvr_blend_ready()) g_go = 0; /* a movie's overlay goes on the frame target when the frame is counted */
}

int port_extra_thread_is_early(void) { return g_early; }

void port_extra_thread_early(int on) { g_early = on; }

/* The renderer, holding gfx_lock: the new scene is parsed and drawn, so in-between frames can start from it now. */
void port_extra_thread_rendered(void)
{
    u64 now;
    if (g_up != 1 || !g_early) return;
    now = now_us();
    g_period_us = g_start_us && now > g_start_us ? now - g_start_us : 0u;
    g_start_us = now;
    gfx_movie_lay_frame(); /* a frame shown again before it is counted has the movie on it */
    g_go = 1;
    SetEvent(g_wake);
}

u64 port_extra_thread_period(void) { return g_period_us; }

void port_extra_thread_counted(void)
{
    if (g_up == 1) SetEvent(g_wake);
}

int port_extra_thread_go(void)
{
    if (g_up != 1 || g_own == 1) return 0; /* the game's own draw function: the tick still draws its in-between frames */
    g_go = 1;
    SetEvent(g_wake);
    return 1;
}

/* port_extra_begin, for a game with its own draw function under --present-thread on, real clock and no dump: start the
 * thread that shows the last frame again. */
void port_extra_thread_own(int wanted)
{
    if (!wanted || g_own) return;
    g_own = -1; /* tried */
    port_extra_thread_begin(1);
    if (g_up != 1) return;
    g_own = 1;
    port_log("frame_extra: the game draws its own in-between frames; the thread shows the last frame again when a tick "
             "shows none for a slot\n");
}

/* gfx_show, holding gfx_lock: a frame was shown, so it may be shown again until a render holds the thread. */
void port_extra_thread_shown(void)
{
    if (g_own != 1) return;
    g_own_shown_us = now_us();
    if (!g_go) {
        g_go = 1;
        SetEvent(g_wake);
    }
}

/* This thread, holding gfx_lock (port_extra_thread_show), with the game's own draw function: the last frame again once
 * its slot and OWN_GRACE_US have gone by with nothing shown. 1 shown, 2 the display would not take it, 3 not yet. */
int port_extra_thread_reshow(u64 now, u32 fps, u32 *count)
{
    u64 due;
    if (g_own != 1) return 0;
    fps = cap(fps);
    due = g_own_shown_us + 1000000u / (fps ? fps : 144u) + OWN_GRACE_US;
    if (now < due) return g_own_nap = due - now, 3;
    if (!gfx_can_show()) return 2;
    gfx_show();
    ++*count;
    return 1;
}
