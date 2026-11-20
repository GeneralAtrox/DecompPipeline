/* xb_gpu.c, the window: the resize test and the first frame's time (one translation unit with it; here for the 400-line cap).
 *
 * The window's "Not responding" through the first load (47.6 s on the user's laptop, 2026-11-09) is not a guest wait on the window's thread: waits
 * made in slices there were never entered before the first frame (0 looks, 2026-11-11) and were taken out. The base times its own start-up
 * (present_device.c, "gfx: start-up took") and the first frame shown is timed here, to find where that time goes */
#include <time.h>

/* XB_RESIZE_AT="<vertical blank>,<w>x<h>", a test: the output changes size once at that vertical blank, as when the player maximises the window
 * (gfx_resize, which a window's size message calls). A headless run can then show what a resize does to the picture (the maximise zoom, 2026-11-09) */
static int s_resize_check;   /* draws left whose device state is read back and logged (xb_gpu_draw.h), some before the resize test and some after */
static int s_resize_trace;   /* lines left of the trace after the resize test: each distinct target and viewport bound, and each frame shown */

static void window_resize_test(void)
{
    static int at = -1, w, h;
    if (at < 0) {
        const char *e = getenv("XB_RESIZE_AT"), *c = e ? strchr(e, ',') : NULL, *x = c ? strchr(c, 'x') : NULL;
        at = 0;
        if (x) at = atoi(e), w = atoi(c + 1), h = atoi(x + 1);
    }
    {
        static int pre;
        if (at > 20 && !pre && port_vblank_count() >= (u32)at - 20u) pre = 1, s_resize_check = 16;
    }
    if (at > 0 && w > 0 && h > 0 && port_vblank_count() >= (u32)at) {
        port_log("resize test: output %ux%u to %dx%d at vertical blank %u, frame %u (XB_RESIZE_AT)\n", g_gfx.out_w, g_gfx.out_h, w, h, port_vblank_count(), s_frames);
        gfx_resize((unsigned)w, (unsigned)h);
        s_resize_trace = 80;
        s_resize_check = 24;
        at = 0;
    }
}

static void window_frame_shown(void)   /* xb_gpu_present */
{
    static int done;
    window_resize_test();
    if (!done) done = 1, port_log("window: first frame shown %.0f ms into the run, after %u game files opened\n", (double)clock() * 1000.0 / CLOCKS_PER_SEC, xb_flow_file_count());
}
