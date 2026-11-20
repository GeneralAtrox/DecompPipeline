/* A game's scrolling backdrop can be a field of sprites that wraps just past the 640x480 screen: on a wider or taller
 * frame the wrap is in plain view. A field named in game.json sprite_fields repeats by its period to cover the frame.
 * At 4:3 there are no copies, and nothing here touches the game's lists or memory. */
#include <string.h>
#include "pvr_int.h"
#include "platform.h"

#ifdef PORT_SPRITE_FIELDS
typedef struct SpriteField {
    u32 tsp;
    int w, h, period_x, period_y;
} SpriteField;

/* How many periods beyond the field the frame reaches on one side. The field wraps inside the 4:3 screen's reach or just
 * past it, so a frame's overhang is the most it can need; the count hangs only on the shape, so that a scene and the one
 * before it have the same vertices (pvr_blend.c). */
static int field_reach(f32 overhang, int period) { return overhang > 0.0f ? (int)(overhang / (f32)period) + 1 : 0; }

static int same_size(f32 a, int size)
{
    f32 d = (a < 0.0f ? -a : a) - (f32)size;
    return d > -0.5f && d < 0.5f;
}

/* This sprite's copies, if it belongs to a repeating field. */
void ta_sprite_field_copies(PvrScene *s, u32 tsp, const PvrVtx v[4])
{
    static const SpriteField fields[] = {PORT_SPRITE_FIELDS};
    for (u32 i = 0; i < sizeof fields / sizeof fields[0]; i++) {
        const SpriteField *f = &fields[i];
        int nx, ny;
        if (tsp != f->tsp || !same_size(v[2].x - v[0].x, f->w) || !same_size(v[2].y - v[0].y, f->h)) continue;
        nx = field_reach(320.0f * (port_widescreen() - 1.0f), f->period_x);
        ny = field_reach(240.0f * (port_tallscreen() - 1.0f), f->period_y);
        for (int y = -ny; y <= ny; y++)
            for (int x = -nx; x <= nx; x++) {
                PvrVtx c[4];
                if (!x && !y) continue;
                memcpy(c, v, sizeof c);
                for (u32 k = 0; k < 4u; k++) {
                    c[k].x += (f32)(x * f->period_x);
                    c[k].y += (f32)(y * f->period_y);
                }
                tb_quad(s, c);
            }
        return;
    }
}
#else
void ta_sprite_field_copies(PvrScene *s, u32 tsp, const PvrVtx v[4])
{
    (void)s;
    (void)tsp;
    (void)v;
}
#endif
