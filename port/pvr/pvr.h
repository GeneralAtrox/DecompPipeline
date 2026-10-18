/* The PowerVR (CLX2) as the SDK layer sees it: texture memory, the render state registers the SDK sets (fog,
 * background plane, clip) and "render this scene", whose display lists are TA parameter streams in guest RAM.
 * Game-free and SDK-free: Kamui 2 (sdk/kamui2_*.c) and later Kamui 1 feed it. No Windows or D3D types here, so SDK
 * modules that include game.h can include it. */
#ifndef PVRX_H
#define PVRX_H

#include "rd_rt.h"

/* Display lists, in the TA's list type order. */
enum { PVRX_LIST_OPAQUE, PVRX_LIST_OPAQUE_MOD, PVRX_LIST_TRANSLUCENT, PVRX_LIST_TRANS_MOD, PVRX_LIST_PUNCH, PVRX_LISTS };

#define PVRX_VRAM_SIZE 0x800000u /* 8 MB texture memory */
#define PVRX_MAX_PASSES 8u

/* One pass of a scene: each list's parameter words, [start, end) as guest cached addresses (start == end: empty). */
typedef struct PvrPass {
    u32 start[PVRX_LISTS], end[PVRX_LISTS];
    int autosort; /* translucent lists are sorted per pixel (else drawn in submission order) */
    int z_clear;  /* the depth buffer starts cleared (else it keeps the previous pass's) */
} PvrPass;

/* pvr_state.c: the texture memory, as the 64-bit-access ("linear") view. */
u8 *pvr_vram(void);
void pvr_vram_written(u32 offset, u32 size); /* the decoded-texture cache drops what overlaps */

/* pvr_state.c: what the SDK sets between scenes */
void pvr_set_fog_table(const u16 entries[128]); /* FOG_TABLE: each entry two 8-bit densities */
void pvr_set_fog_color(u32 argb);               /* FOG_COL_RAM */
void pvr_set_fog_density(u32 reg);              /* FOG_DENSITY: mantissa << 8 | exponent */
void pvr_set_background(const f32 xyz[3][3], const u32 argb[3]); /* the background plane: 3 vertices, colours */
void pvr_set_clip(u32 x0, u32 y0, u32 x1, u32 y1); /* global tile clip, in pixels (inclusive) */
void pvr_set_stride(u32 width);                    /* TEXT_CONTROL: the row width of stride textures, texels */
void pvr_set_border(u32 argb);                     /* the border colour, around the picture */
/* FPU_SHAD_SCALE: cheap shadow, where modifier volumes scale the shadow polygons' colours by intensity / 256
 * (0..255); a negative intensity selects the two-parameter polygons instead. */
void pvr_set_cheap_shadow(s32 intensity);

/* pvr_draw.c: draws a scene's passes into the frame target. Returns 0 when nothing could be drawn. */
int pvr_render(const PvrPass *passes, u32 count);
int pvr_read_frame(void *rgb565, u32 width, u32 height); /* the last frame, scaled to width x height; 0 if none */

/* The SDK's to-do log: calls reached with arguments the module does not handle yet, each text once per run. */
void pvr_todo(const char *fmt, ...);

#endif
