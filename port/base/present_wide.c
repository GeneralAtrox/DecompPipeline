/* game.json wide_cull (port/tools/wide_cull.py): a game's culling call sees the whole frame, not only its 640x480 screen.
 * Moved from present.c at its 400-line cap; nearest existing: present.c, whose port_widescreen and port_tallscreen give the
 * shares. */
#include "present.h"

/* The 640x480 screen is the frame's middle, so a view centred on it (the full screen, or a wipe opening from the
 * centre) grows by the same share: W by W * (wide - 1), H by H * (tall - 1), half to each side. A view off centre
 * (a render target's) keeps its own. A call inside a widened one (a renderer calling its twin) leaves it as it is. */
static int g_wide_cull_depth;

static int wide_centred(f32 at, f32 size, f32 screen)
{
    f32 d = at + size * 0.5f - screen * 0.5f;
    return d > -0.5f && d < 0.5f;
}

int port_wide_cull_begin(u32 viewport, f32 saved[4])
{
    f32 *v = (f32 *)(uintptr_t)viewport; /* W, H, X, Y */
    f32 k = port_widescreen() - 1.0f, ky = port_tallscreen() - 1.0f;
    f32 side, over;
    int i;
    if (g_wide_cull_depth || (k <= 0.0f && ky <= 0.0f) || !wide_centred(v[2], v[0], 640.0f)
        || !wide_centred(v[3], v[1], 480.0f)) {
        return 0;
    }
    for (i = 0; i < 4; i++) saved[i] = v[i];
    side = v[0] * 0.5f * k;
    over = v[1] * 0.5f * ky;
    v[0] += 2.0f * side;
    v[1] += 2.0f * over;
    v[2] -= side;
    v[3] -= over;
    g_wide_cull_depth = 1;
    return 1;
}

void port_wide_cull_end(u32 viewport, const f32 saved[4])
{
    f32 *v = (f32 *)(uintptr_t)viewport;
    int i;
    for (i = 0; i < 4; i++) v[i] = saved[i];
    g_wide_cull_depth = 0;
}
