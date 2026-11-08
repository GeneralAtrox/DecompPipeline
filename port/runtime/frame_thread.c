/* --present-thread (frame_extra.c): a scene-blended game's in-between frames are drawn and shown by this thread, so a tick
 * of any length takes no time from them. No game code runs for one. The device context is shared under gfx_lock; this
 * thread gives way whenever the game's thread wants it. The renderer and gfx_present hold it back while the frame target
 * has a frame not yet counted or shown, and a vertical blank's wait lets it go on; under --present-thread on only a frame
 * with nothing to blend holds it, and a render's end lets it go on. */
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
static volatile u32 g_render_gen, g_counted_gen, g_backoff_gen; /* renders begun; the last counted; stay out until it is */
static volatile int g_counts;   /* gfx_frame_done reports the count (port_extra_thread_counted) */
static u64 g_render_at, g_backoff_at, g_hold_us; /* a render's start; the back-off's; what a frame holds the lock for */
static u64 g_tick_at, g_lead_us; /* the tick's start (port_extra_thread_tick); from a tick's start to its render */

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
static void nap_to_slot(void)
{
    u64 until = now_us() + g_nap_us;
    if (g_nap_us > 600u) pace_sleep_us(g_nap_us - 500u);
    while (now_us() < until && !gfx_lock_wanted()) YieldProcessor();
}

/* The game's thread must not wait for this one. On a 300 Hz desktop (11-07) each Present took 7 ms, 144 a second,
 * all under gfx_lock: the render and the frame's count after it each waited for one, and the game ran 35-50 ticks a
 * second, its sound running dry. So (--present-pace on, the default) this thread takes the lock for a frame only
 * - once the frame rendered last is counted (gfx_frame_done), not between the render and its count;
 * - if what a frame holds the lock for, recently, ends half a millisecond before the next render is due (the last
 *   one's start plus the time between renders);
 * - after a frame the game's thread had to wait for, not until the next render is counted.
 * Each rule lapses after STAY_OUT_US, so a render that is never counted or a long tick cannot stop the frames. */
#define STAY_OUT_US 25000u
/* A slow frame (a Present of several ms: the 300 Hz desktop) would still run into a render: renders come every 2-3 ticks
 * (a game drawing at 20-30 a second), so the time since the last one does not say when. Every tick starts on its 1/60 s boundary and a render comes
 * about g_lead_us after; this thread keeps out of the lock from a frame's length before that until the render begins
 * (this tick's, if it has not rendered yet, and the next one's). A short frame (under 2.5 ms) does not use this rule. */
static int slow_frame_due(u64 now)
{
    u64 at = g_tick_at + g_lead_us, next = at + 1000000u / PORT_VBLANK_HZ;
    if (g_render_at < g_tick_at && now + g_hold_us + 500u > at && now < at + 1000u) return 1;
    return now + g_hold_us + 500u > next && now < next + 1000u;
}

static int stay_out(u64 now)
{
    u64 due = g_render_at + g_period_us;
    if (!g_early || !gfx_present_paced()) return 0;
    if (g_counts && g_counted_gen != g_render_gen && now - g_render_at < STAY_OUT_US) return 1;
    if ((int)(g_backoff_gen - g_counted_gen) > 0 && now - g_backoff_at < STAY_OUT_US) return 1;
    if (g_hold_us > 2500u && g_tick_at && slow_frame_due(now)) return 1;
    /* only a slow frame keeps out of the render's predicted time: a short one costs the render under 2.5 ms, and keeping
     * out cost in-between frames their slots (build box A: 533 late of 5818 against the live exe's 305 of 6144) */
    return g_hold_us > 2500u && g_period_us && g_period_us < 100000u && now + g_hold_us + 500u > due && now < due + 2000u;
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
        if (stay_out(now_us())) {
            WaitForSingleObject(g_wake, 1); /* a frame's count wakes it (port_extra_thread_counted) */
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
        if (what == 1) g_hold_us = g_hold_us ? (g_hold_us * 3u + now_us() - held) / 4u : now_us() - held;
        if (gfx_lock_wanted() && g_early) g_backoff_gen = g_render_gen + 1u, g_backoff_at = now_us(); /* it waited for this frame */
        t0 = g_xcost ? xcost_now() : 0u;
        if (what == 2) gfx_show_wait();
        else if (what == 3) nap_to_slot(); /* --fps: not yet */
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
    port_log("frame_extra: in-between frames on a thread of their own (--present-thread)\n");
}

int port_extra_thread_on(void) { return g_up == 1; }

void port_extra_thread_hold(void)
{
    g_render_at = now_us(); /* a render, or the tick's own show, begins: the device is the game's thread's */
    if (g_tick_at && g_render_at - g_tick_at < 1000000u / PORT_VBLANK_HZ)
        g_lead_us = g_lead_us ? (g_lead_us * 3u + g_render_at - g_tick_at) / 4u : g_render_at - g_tick_at;
    g_render_gen++;
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
    g_go = 1;
    SetEvent(g_wake);
}

u64 port_extra_thread_period(void) { return g_period_us; }

/* --present-pace on, in-between frames on with this thread running: the tick's own frame (none drawn for it, or blending
 * off for a movie) is shown by this thread too, so the tick never makes a Present (on build box A a 7 ms one cost it 8 ms a blank). */
int extra_interp(void);
int port_extra_thread_shows(void) { return g_up == 1 && g_early && gfx_present_paced() && extra_interp(); }

void port_extra_thread_counted(void)
{
    g_counts = 1;
    g_counted_gen = g_render_gen;
    if (g_up == 1) SetEvent(g_wake);
}

/* The host, as a tick starts (its vertical blank's wait is over). */
void port_extra_thread_tick(void) { if (g_up == 1) g_tick_at = now_us(); }

int port_extra_thread_go(void)
{
    if (g_up != 1) return 0;
    g_go = 1;
    SetEvent(g_wake);
    return 1;
}
