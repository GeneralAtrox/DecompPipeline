/* Where the 640x480 screen is in the draw target. A frame wider or taller than 4:3 (--aspect, gfx_aspect.c) keeps the
 * screen in its middle and shows more of the world around it, so a coordinate of the screen (a scissor, a clear) maps
 * into the middle of the target, and a clip that spans the screen's width or height spans the target's: the hardware
 * has nothing beyond the screen, and the extra space is the renderer's. */
#include "pvr_int.h"
#include "platform.h"

/* v in 0..span of the screen, to pixels of an axis size pixels long when the target is k times the screen's length. */
static LONG axis(u32 v, int full, u32 size, u32 span, f32 k)
{
    if (k <= 1.0f) return (LONG)(v * size / span);
    if (full) return v ? (LONG)size : 0;
    return (LONG)((0.5f + ((f32)v - (f32)span * 0.5f) / ((f32)span * k)) * (f32)size + 0.5f);
}

/* A screen x (0..640) in the draw target; full: the clip spans the screen's width. */
LONG pvr_draw_x(u32 x, int full) { return axis(x, full, g_gfx.draw_w, 640u, port_widescreen()); }

/* A screen y (0..480) in the draw target; full: the clip spans the screen's height. */
LONG pvr_draw_y(u32 y, int full) { return axis(y, full, g_gfx.draw_h, 480u, port_tallscreen()); }
