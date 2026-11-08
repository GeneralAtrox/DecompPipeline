/* The Dreamcast's side of presenting (the frame target and the swap chain are the base's, base/present.c): a rendered
 * frame's movie, capture and per-frame services, the screen hold, and the base's hooks for the VMU panels. */
#include "gfx.h"
#include "platform.h"
#include "pvr.h"
#include "scene_dump.h"
#include "game_log.h"
#include "prof.h"
#include "play_stats.h"

void gfx_frame_done(void)
{
    u64 pt;
    if (!g_gfx.dev) return;
    pt = prof_begin(PROF_DONE);
    gfx_lock();
    gfx_movie_draw();
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
    gfx_lock();
    port_extra_thread_hold(); /* the frame target holds this frame until it is shown */
    gfx_frame_done();
    if (port_extra_thread_shows()) port_extra_thread_go(); /* that thread shows it: a slow Present costs the tick nothing */
    else gfx_show();
    port_extra_shown();
    gfx_unlock();
}

void gfx_show(void)
{
    u64 pt = prof_begin(PROF_PRESENT);
    gfx_lock();
    present_show();
    gfx_unlock();
    prof_end(pt);
    play_stats_shown();
}

/* ---- the base's hooks (base/console.h "the presenter") ---- */
int console_present_option(const char *arg, const char *value)
{
    int n = gfx_vmu_option(arg, value);
    if (n) return n;
    if (pvr_pack_option(arg, value)) return 2;
    return gfx_capture_option(arg, value);
}

void console_present_over(void *back_buffer) { gfx_vmu_draw(back_buffer); }

void console_present_release(void)
{
    gfx_vmu_release();
    gfx_movie_release();
}

int console_widens(void) { return PORT_WIDESCREEN; }
