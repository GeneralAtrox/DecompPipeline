/* The scene blending's options (ta_parse.c hands this the --interp-* ones; frame_extra.c takes the rest), and
 * --interp-truth, a check of in-between frames against the real ones.
 *
 *   --interp-persp on|off  positions blend in perspective (pvr_blend.c): x/z, y/z and 1/z, z being the PowerVR's 1/w,
 *                          are what move in a straight line when a point moves in a straight line in the view, so a
 *                          moving camera's in-between frame is its view at that time; linear screen positions bent
 *                          near ground and walls and swam their textures. Default off
 *   --interp-truth on      with a fixed clock and frame dumps (--dump-every 1 --dump-extra 1): the scene before is kept
 *                          only at even frames and in-between frames are drawn at one half, so the one after an odd
 *                          frame N blends N-2 with N, the time of frame N-1, which was rendered and dumped too.
 *                          port/tools/interp_truth.py compares them. A check only: frames shown are not the game's */
#include <string.h>
#include "pvr_int.h" /* and gfx/gfx.h: g_gfx.frames */

int g_blend_persp;
static int g_truth;

int pvr_blend_option(const char *arg, const char *value)
{
    if (!strcmp(arg, "--interp-gpu")) return pvr_blend_gpu_option(value);
    if (!strcmp(arg, "--interp-parts")) return pvr_parts_blend_option(value); /* pvr_parts.c */
    if (!strcmp(arg, "--interp-early")) return pvr_blend_early_option(value);
    if (!strcmp(arg, "--interp-persp")) return g_blend_persp = !strcmp(value, "on"), 2;
    if (!strcmp(arg, "--interp-truth")) return g_truth = !strcmp(value, "on"), 2;
    if (!strcmp(arg, "--interp-parts-log")) return pvr_parts_option(value); /* pvr_parts.c */
    return 0;
}

/* pvr_blend_keep: 1 if the scene before is not kept now (--interp-truth, an odd frame). */
int pvr_blend_truth_skip(void) { return g_truth && (g_gfx.frames & 1u); }

/* pvr_blend_vertices: the time an in-between frame blends at. */
f32 pvr_blend_truth_t(f32 t) { return g_truth ? 0.5f : t; }
