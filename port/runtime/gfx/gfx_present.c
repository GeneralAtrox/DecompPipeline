/* The Dreamcast's side of presenting (the frame target and the swap chain are the base's, base/present.c): a rendered
 * frame's movie, capture and per-frame services, the screen hold, and the base's hooks for the VMU panels. */
#include <string.h>
#include "gfx.h"
#include "platform.h"
#include "pvr.h"
#include "scene_dump.h"
#include "game_log.h"
#include "prof.h"
#include "play_stats.h"
#include <intrin.h>

static int g_edges = PORT_BORDER_EDGES;
int gfx_edges_on(void) { return g_edges; }

/* The bars beside the screen, after a movie's picture is laid (gfx.h) */
static void frame_edges(void)
{
    if (gfx_movie_on()) gfx_black_bars(); /* everything outside a movie's screen is black (the user, 2026-11-13) */
    else if (g_edges && g_pvr_framed) gfx_carry_edges();
}

/* platform.h, the renderer's end under --present-thread, holding gfx_lock: with a movie on, its current picture (and the
 * bars) on the frame target now. The in-between thread may show this frame again before gfx_frame_done counts it; it
 * showed it without the movie before: a near-black frame between the movie's frames (seen on 11-11). */
void gfx_movie_lay_frame(void)
{
    if (!gfx_movie_on()) return;
    gfx_movie_lay();
    frame_edges();
}

void gfx_frame_done(void)
{
    u64 pt;
    if (!g_gfx.dev) return;
    pt = prof_begin(PROF_DONE);
    gfx_lock();
    gfx_movie_draw();
    frame_edges();
    gfx_capture_frame(g_gfx.frames);
    gfx_unlock();
    port_extra_thread_counted(); /* the game's thread is done with the device for this frame */
    game_log_poll(g_gfx.frames);
    scene_dump_poll(g_gfx.frames);
    g_gfx.frames++;
    prof_end(pt);
}

static int g_hold;
void gfx_hold(int on) { g_hold = on; }
int gfx_held(void) { return g_hold; }

void gfx_present(void)
{
    if (g_hold) {
        gfx_pump();
        return;
    }
    g_play_by[1] = _ReturnAddress(); /* play_stats: who presented it */
    gfx_lock();
    port_extra_thread_hold(); /* the frame target holds this frame until it is shown */
    gfx_frame_done();
    gfx_show();
    port_extra_shown();
    gfx_unlock();
}

__declspec(noinline) void gfx_show(void) /* not inlined: its return address is its caller, gfx_present's is apart */
{
    u64 pt = prof_begin(PROF_PRESENT);
    g_play_by[0] = _ReturnAddress();
    gfx_lock();
    gfx_movie_check_shown(); /* --movie-check on */
    present_show();
    port_extra_thread_shown(); /* --present-thread with the game's own draw function: it may be shown again */
    play_stats_shown();        /* under the lock: that thread shows frames too */
    gfx_unlock();
    prof_end(pt);
}

/* ---- the base's hooks (base/console.h "the presenter") ---- */
int console_present_option(const char *arg, const char *value)
{
    int n = gfx_vmu_option(arg, value);
    if (n) return n;
    if (pvr_pack_option(arg, value)) return 2;
    if (!strcmp(arg, "--border-edges") && value) {
        g_edges = strcmp(value, "off") != 0;
        return 2;
    }
    return gfx_capture_option(arg, value);
}

void console_present_over(void *back_buffer) { gfx_vmu_draw(back_buffer); }

void console_present_release(void)
{
    gfx_vmu_release();
    gfx_movie_release();
}

int console_widens(void) { return PORT_WIDESCREEN; }
