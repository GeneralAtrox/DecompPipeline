/* The PowerVR renderer's own declarations, shared by the pvr/ files (not by SDK modules: this pulls in D3D11).
 *
 * A scene goes: ta_parse.c walks each list's TA parameters, ta_batch.c turns polygons and strips into triangles
 * grouped in batches (one per run of polygons with the same render state), pvr_draw.c draws the batches list by
 * list. Textures come from pvr_tex.c (cache) and pvr_twiddle.c (decode). */
#ifndef PVRX_INT_H
#define PVRX_INT_H

#include "gfx/gfx.h"
#include "pvr.h"

typedef struct PvrState {
    u16 fog_table[128];
    int fog_dirty;
    u32 fog_color, fog_density;
    f32 bg_xyz[3][3];
    u32 bg_argb[3];
    u32 clip[4]; /* x0, y0, x1, y1 in 640x480 pixels, inclusive */
    u32 stride;  /* stride textures' row width in texels */
    s32 shadow;  /* cheap shadow intensity 0..255; negative: off (two-parameter polygons) */
} PvrState;

extern PvrState g_pvr;

/* --pvr-debug nocull,nofog,nodepth: switch a stage off to see what it hides */
#define PVR_DBG_NOCULL 1u
#define PVR_DBG_NOFOG 2u
#define PVR_DBG_NODEPTH 4u
extern u32 g_pvr_debug;

/* One vertex as drawn: x, y in 640x480 screen pixels, z = 1/w (the PowerVR's depth), u, v; the base and offset
 * colours as packed ARGB; the index of its batch (the pixel shader's per-batch state). 32 bytes. */
typedef struct PvrVtx {
    f32 x, y, z, u, v;
    u32 base, offset, batch;
} PvrVtx;

#define PVR_BF_TEXTURE 1u /* textured */
#define PVR_BF_OFFSET 2u  /* the offset colour is added */
#define PVR_BF_PUNCH 4u   /* punch-through: alpha test */
#define PVR_BF_SHADOW 8u  /* PCW shadow bit: modifier volumes act on it (it marks stencil bit 0x80) */

/* A run of triangles with one render state. */
typedef struct PvrBatch {
    u32 list;          /* PVRX_LIST_* */
    u32 first, count;  /* vertices, three per triangle */
    u32 isp, tsp, tcw; /* the polygon header's ISP/TSP instruction, TSP instruction and texture control words */
    u32 flags;         /* PVR_BF_* */
    u16 clip[4];       /* scissor in 640x480 pixels: x0, y0, x1, y1 (exclusive) */
} PvrBatch;

/* The parsed scene: every pass's lists as ranges of batches. */
typedef struct PvrScene {
    PvrVtx *vtx;
    u32 nvtx, vcap;
    PvrBatch *batch;
    u32 nbatch, bcap;
    u32 range[PVRX_MAX_PASSES][PVRX_LISTS][2]; /* [first, end) batches of each pass's list */
    u32 passes;
    f32 max_z; /* the largest 1/w drawn: the depth buffer's scale */
    /* the strip being assembled */
    PvrVtx strip[2];
    u32 strip_n;
    int open; /* a batch is open (the last one), so polygons with the same state join it */
} PvrScene;

/* ta_batch.c */
void tb_reset(PvrScene *s);
void tb_poly(PvrScene *s, u32 list, u32 isp, u32 tsp, u32 tcw, u32 flags, const u16 clip[4]);
void tb_vertex(PvrScene *s, const PvrVtx *v, int end_of_strip);
void tb_quad(PvrScene *s, const PvrVtx v[4]); /* a sprite: a, b, c, d around the quad */
void tb_tri(PvrScene *s, const PvrVtx v[3]);  /* a modifier volume triangle */

/* ta_parse.c: the scene's passes into s; per-frame statistics and first-seen classes go to the log. */
void ta_parse(PvrScene *s, const PvrPass *passes, u32 count);

/* pvr_state.c: each distinct text once per run, as "ta: ..." (what the stream uses; not a to-do) */
void pvr_seen(const char *fmt, ...);

/* pvr_twiddle.c: decodes the texture a TCW and the TSP size bits describe, top level first, mip levels after (each
 * level w x h RGBA8 rows). Returns the number of levels, 0 for a format it cannot decode (logged). */
u32 pvr_decode(u32 tcw, u32 w, u32 h, u32 stride, u32 *out, u32 out_texels);
u32 pvr_decode_texels(u32 tcw, u32 w, u32 h); /* out_texels pvr_decode needs */
u32 pvr_texture_bytes(u32 tcw, u32 w, u32 h, u32 stride); /* the bytes of texture memory it reads */
u32 pvr_tcw_layout(u32 tcw); /* the TCW without a palettized texture's selector bits (they are no layout bits) */
int pvr_palette_range(u32 tcw, u32 *first, u32 *count); /* the palette entries it reads; 0: not palettized */

/* pvr_pal.c: palette RAM and PAL_RAM_CTRL. pvr_palette_stamp changes when any of the entries (or the format) does. */
u32 pvr_palette_entry(u32 index);
u32 pvr_palette_format(void);
u32 pvr_palette_stamp(u32 first, u32 count);

/* pvr_pack.c: a texture pack's image for the texture (key: format bits, size, stride and the texture memory bytes
 * read); its level count (mip-mapped guests get the image's whole box-filtered chain), 0 for none. The texels stay
 * the pack's. pvr_pack_generation changes when the pack is switched on or off. */
u32 pvr_pack_levels(u32 tcw, u32 w, u32 h, u32 stride, const u32 **texels, u32 *pw, u32 *ph);
u64 pvr_pack_key(const u8 *vram, u32 tcw, u32 w, u32 h, u32 stride);
u32 pvr_pack_generation(void);

/* pvr_state.c: --dump-vram SCENE writes the texture memory as it is when that scene is parsed */
void pvr_vram_dump(u32 scene);

/* pvr_tex.c */
void pvr_tex_invalidate(u32 offset, u32 size);
ID3D11ShaderResourceView *pvr_tex_get(u32 tcw, u32 tsp); /* NULL: draw untextured (logged) */

/* pvr_states.c: D3D11 state objects, created once per distinct state */
ID3D11BlendState *pvr_blend_state(u32 list, u32 tsp);
ID3D11DepthStencilState *pvr_depth_state(u32 list, u32 isp);
ID3D11SamplerState *pvr_sampler_state(u32 tsp);
ID3D11RasterizerState *pvr_raster_state(void);
void pvr_states_release(void);

/* pvr_oit.c: autosort. begin binds the gather pass (0: no resources, draw in submission order); resolve blends
 * the sorted fragments into the frame target and leaves the pipeline for pvr_draw.c to bind again. */
int pvr_oit_begin(ID3D11DepthStencilView *dsv);
void pvr_oit_resolve(void);
/* the full-screen triangle vertex shader (pvr_oit.hlsl vs_full), shared with pvr_modvol.c; NULL if it failed */
ID3D11VertexShader *pvr_full_vs(void);

/* pvr_draw.c: a batch's scissor (its tile clip within the global clip) */
void pvr_scissor(const PvrBatch *b);

/* pvr_modvol.c: a pass's opaque modifier volumes, after its opaque and punch-through polygons: the volumes mark the
 * pixels they enclose in the stencil, then cheap shadow scales the enclosed shadow-polygon pixels. Leaves the
 * pipeline for pvr_draw.c to bind again. */
void pvr_modvol_draw(const PvrScene *s, u32 pass, ID3D11Buffer *frame_consts);
/* whether a pass's translucent modifier volumes may change anything: a translucent polygon with the shadow bit
 * overlaps the on-screen volumes' box (Kamui starts every list with a dummy volume off screen) */
int pvr_modvol_trans_needed(const PvrScene *s, u32 pass);
/* the pass's translucent modifier volumes, between pvr_oit_begin's gather and pvr_oit_resolve: they mark the shadow
 * fragments they enclose, which the resolve scales by the cheap shadow intensity */
void pvr_modvol_trans_draw(const PvrScene *s, u32 pass);

/* pvr_read.c: after each render, keeps a game frame (not an in-between one) for pvr_read_frame */
void pvr_keep_frame(void);

#endif
