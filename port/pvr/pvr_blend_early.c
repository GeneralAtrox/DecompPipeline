/* --interp-early on: a scene-blended game's next scene is parsed and paired with the current one (pvr_blend.c) before
 * the renderer takes gfx_lock to draw it, on the game's thread, so the thread of in-between frames never waits for the
 * pairing: it goes on blending the scenes it has meanwhile. Three scenes take turns: the old one and the current one
 * the in-between frames read, and the next one, parsed into the third. The pairs are the same as the drawing would
 * have found; without the option the renderer keeps a copy of the scene and the drawing pairs, as before. */
#include <string.h>
#include "pvr_int.h"
#include "platform.h"

static int g_on;
static PvrScene g_next, g_old; /* the next scene, parsed here; the scene before the current one */

int pvr_blend_early_option(const char *value)
{
    g_on = !strcmp(value, "on");
    return 2;
}

/* pvr_render (pvr_draw.c), before it takes gfx_lock: 1 if the next scene is parsed into the third and paired with
 * cur (only this thread changes cur's arrays; the drawing may lend it other vertices, so its copy is taken under the
 * lock). Blending off, the pairs are dropped: the scenes parsed meanwhile are not the ones they were for. */
int pvr_blend_early_parse(const PvrScene *cur, const PvrPass *passes, u32 count)
{
    PvrScene now;
    if (!g_on) return 0;
    gfx_lock();
    if (!extra_scene()) {
        pvr_blend_new_scene();
        gfx_unlock();
        return 0;
    }
    ta_parse(&g_next, passes, count);
    now = *cur;
    gfx_unlock();
    pvr_blend_pair_next(&now, &g_next);
    return 1;
}

/* Holding gfx_lock: the next scene becomes cur, cur the old one, and the old one's arrays are the next one to fill. */
void pvr_blend_early_take(PvrScene *cur)
{
    PvrScene spare = g_old;
    g_old = *cur;
    *cur = g_next;
    g_next = spare;
    pvr_blend_take(&g_old);
}
