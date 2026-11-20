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
    int clip_opens_frame; /* pvr_set_clip_opens_frame */
    u32 stride;  /* stride textures' row width in texels */
    s32 shadow;  /* cheap shadow intensity 0..255; negative: off (two-parameter polygons) */
} PvrState;

extern PvrState g_pvr;

/* --pvr-debug nocull,nofog,nodepth: switch a stage off to see what it hides */
#define PVR_DBG_NOCULL 1u
#define PVR_DBG_NOFOG 2u
#define PVR_DBG_NODEPTH 4u
#define PVR_DBG_SHADOWFLAG 8u /* shade every polygon with the shadow bit */
#define PVR_DBG_SHADOWVOL 16u /* shade every pixel a volume encloses */
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
#define PVR_UVO_U 1u /* PvrBatch.uv_out: a u outside 0..1 (the texture repeats or mirrors there) */
#define PVR_UVO_V 2u
#define PVR_BF_SHADOW 8u  /* PCW shadow bit: modifier volumes act on it (it marks stencil bit 0x80) */

/* A run of triangles with one render state. */
typedef struct PvrBatch {
    u32 list;          /* PVRX_LIST_* */
    u32 first, count;  /* vertices, three per triangle */
    u32 isp, tsp, tcw; /* the polygon header's ISP/TSP instruction, TSP instruction and texture control words */
    u32 flags;         /* PVR_BF_* */
    u32 uv_out;        /* PVR_UVO_*: an axis on which a vertex's texture coordinate leaves 0..1 */
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
    int beyond; /* a triangle reaches past the 640x480 screen: the world is drawn there, so a wider frame shows it */
    /* the strip being assembled */
    PvrVtx strip[2];
    u32 strip_n;
    int open; /* a batch is open (the last one), so polygons with the same state join it */
    u32 *seq, seq_cap; /* --interp-parts: each vertex's polygon vertex in pvr_parts.c (1-based, 0 none) */
    f32 (*from)[3];    /* --interp-parts: where each vertex was a tick before (x, y, 1/w), when has_from */
    u32 from_cap;
    int has_from;
} PvrScene;

/* The depth buffer's scale: 1/w divided by the largest drawn, so it fits 0..1. A game can draw sprites at any 1/w
 * (a game's HUD: 1.5e37), and scaling by that underflows every normal depth to 0, so the world fails the depth test.
 * Above PVR_DEPTH_TOP the scale stays put and such a sprite clamps to the front (the rasterizer's depth clip is off).
 * A float keeps its precision at any scale, so scenes below the cap draw as they always did. */
#define PVR_DEPTH_TOP 1e18f
static __inline f32 pvr_depth_scale(f32 max_z, f32 bg_z)
{
    f32 top = max_z > bg_z ? max_z : bg_z;
    if (top > PVR_DEPTH_TOP) top = PVR_DEPTH_TOP;
    return top > 0.0f ? 1.0f / top : 1.0f;
}

/* ta_batch.c */
void tb_reset(PvrScene *s);
void tb_poly(PvrScene *s, u32 list, u32 isp, u32 tsp, u32 tcw, u32 flags, const u16 clip[4]);
void tb_vertex(PvrScene *s, const PvrVtx *v, int end_of_strip);
void tb_quad(PvrScene *s, const PvrVtx v[4]); /* a sprite: a, b, c, d around the quad */
void ta_sprite_field_copies(PvrScene *s, u32 tsp, const PvrVtx v[4]); /* ta_sprite_field.c */
void tb_tri(PvrScene *s, const PvrVtx v[3]);  /* a modifier volume triangle */

/* pvr_blend.c: in-between frames from the scene before this one (--interp-scene) */
/* The registers the drawing reads: g_pvr, or for an in-between frame the ones its scene was drawn with. */
extern const PvrState *g_pvr_drawn;
void pvr_blend_keep(const PvrScene *s);
void pvr_blend_new_scene(void);
int pvr_blend_have(void);
const PvrVtx *pvr_blend_vertices(const PvrScene *cur, f32 t, u32 *blended);
/* pvr_wide.c: a pack's wide= images, on a scene whose textures are decoded and whose vertices are not uploaded yet */
void pvr_wide_scene(PvrScene *s);
u32 pvr_blend_scene(void); /* changes with the scene: the blend at t = 0 changes only with it */
/* pvr_blend_gpu.c: --interp-gpu, in-between frames blended by the GPU */
int pvr_blend_gpu_option(const char *value);
int pvr_blend_gpu_ready(const PvrScene *cur, f32 t); /* 1: this frame blends on the GPU; 0: on the CPU */
void pvr_blend_gpu_bind(ID3D11DeviceContext *cx, ID3D11Buffer *scene_vb);
/* pvr_blend_early.c: --interp-early, the next scene parsed and paired before the renderer's lock (pvr_blend.c pairs) */
int pvr_blend_early_option(const char *value);
int pvr_blend_option(const char *arg, const char *value); /* pvr_blend_opt.c: the --interp-* options of scene blending */
/* pvr_parts.c (game.json part_draw): --interp-parts-log; for ta_parse.c, the part (1-based, 0 none) that wrote a vertex */
int pvr_parts_option(const char *value);
u32 pvr_parts_vertex(u32 list, u32 addr);
extern int g_parts_blend; /* --interp-parts on */
int pvr_parts_blend_option(const char *value);
u32 pvr_parts_seq(void);           /* ta_parse.c vertex: the polygon vertex just tagged (1-based, 0 none) */
void pvr_parts_from(PvrScene *s);  /* ta_parse.c, a scene parsed: where its vertices were a tick before */
const PvrVtx *pvr_parts_blend(const PvrScene *cur, f32 t, u32 *blended); /* pvr_blend_vertices, has_from */
/* pvr_parts_fit.c: a part's 3x4 map from one tick to another, fitted from vertex pairs (index arrays, NULL in order) */
typedef struct PartMap {
    double mean[3], scale, c[3][4];
    int wz;  /* w = z (else 1/z) */
    f32 lim; /* pvr_parts_map_places: how far (pixels) a vertex it maps may move; under 0, no map */
} PartMap;
int pvr_parts_fit(const f32 (*a)[3], const f32 (*b)[3], const u32 *ia, const u32 *ib, u32 n, int wz, PartMap *m);
int pvr_parts_map(const PartMap *m, const f32 v[3], f32 out[3]);
double pvr_parts_fit_worst(const PartMap *m, const f32 (*a)[3], const f32 (*b)[3], const u32 *ia, const u32 *ib, u32 n,
                           u32 *off);
u32 pvr_parts_match(const u32 *ka, u32 na, const u32 *kb, u32 nb, u32 *ia, u32 *ib, u32 cap);
u32 pvr_parts_map_places(const f32 (*a)[3], const f32 (*b)[3], const u32 *ka, u32 na, const u32 *kb, u32 nb, f32 clamp,
                         u32 min, u32 *ia, u32 *ib, u32 cap, PartMap *m, u32 *oa, u32 *ob);
f32 pvr_parts_near(const PvrVtx *v, u32 n); /* the game's near clamp (a 1/w), 0 until seen */
int pvr_parts_map_part(const f32 (*a)[3], const f32 (*b)[3], const u32 *ka, u32 na, const u32 *kb, u32 nb, f32 clamp,
                       u32 min, u32 *ia, u32 *ib, u32 cap, PartMap *m, u32 *oa, u32 *ob, u32 *nown);
int pvr_parts_in_order(const f32 (*a)[3], const f32 (*b)[3], u32 n, f32 clamp, u32 *ia, u32 *ib);
int pvr_parts_hold_frame(void); /* the scene just parsed gets no in-between frames; _holds: some parts are held */
int pvr_parts_holds(void);
int pvr_blend_truth_skip(void);
f32 pvr_blend_truth_t(f32 t);
extern int g_blend_persp; /* --interp-persp on */
int pvr_blend_early_parse(const PvrScene *cur, const PvrPass *passes, u32 count);
void pvr_blend_early_take(PvrScene *cur);
void pvr_blend_pair_next(const PvrScene *cur, const PvrScene *next);
void pvr_blend_take(const PvrScene *old);
/* pvr_screen.c: a screen coordinate of the 640x480 screen in the draw target (full: the clip spans that axis) */
LONG pvr_draw_x(u32 x, int full);
LONG pvr_draw_y(u32 y, int full);
void pvr_argb_float(u32 argb, f32 out[4]); /* a packed colour as 0..1 floats: r, g, b, a */
f32 pvr_fog_density(u32 reg);              /* FOG_DENSITY's mantissa and exponent as a number */

/* pvr_blend_hold.c: the frame's slots, one per render (a split screen's views, pvr_render_part), and what an in-between
 * frame keeps of each scene drawn last (its textures, registers and passes, the target outside the clip) */
#define PVR_SLOTS 8u /* a 4-view split screen is 5 renders, and its first frames draw a spare view */
void pvr_slot_begin(void);  /* pvr_render, holding gfx_lock: this render's slot */
int pvr_slot_alone(void);   /* the next render is a frame on its own, as the last frame was */
u32 pvr_slot(void);         /* the render's slot, 0 the frame's first */
u32 pvr_slots(void);        /* the renders of the last whole frame (over PVR_SLOTS: no in-between frames) */
int pvr_slot_partial(void); /* the frame is not whole: its last render is to come */
int pvr_slot_keeps(u32 slot);
void pvr_retain(ID3D11ShaderResourceView *const *srv, u32 n, const PvrPass *passes, u32 count);
const PvrState *pvr_retained(u32 slot, ID3D11ShaderResourceView ***srv, u32 *n, const PvrPass **passes, int *keep);
void pvr_hold_reset(void);
void pvr_hold_begin(void);

/* pvr_pack_bundled.c: packs/ beside port.exe. Paths are UTF-8. pack_read_choices: the Texture pack entry's choices (off,
 * on if has_game, then the folders but game_folder), read once; their number. */
#define PVR_PACK_PATH 1024
#define PVR_PACK_NAME 64
int pack_widen(const char *path, WCHAR *out, int cap);
u32 *pvr_pack_read_png(const char *path, u32 *w, u32 *h);
u32 pvr_pack_mips(u32 *px, u32 w, u32 h);
u32 pvr_pack_chain_texels(u32 w, u32 h); /* the texels pvr_pack_mips writes after the top level */
/* The levels below an image's top one, read from FILE.mips beside it when that matches the PNG, else built; how, for
 * the log. pvr_pack_write_mips: port.exe --pack-mips[-check] DIR. pvr_pack_entry (pvr_pack.c): the packs' images. */
const char *pvr_pack_chain(const char *path, u32 *px, u32 w, u32 h, int over, u32 *levels, f32 *split);
/* pvr_pack_load.c: pack images loaded on a thread of their own (pvr_pack_hint kicks one early; pvr_pack_take, the draw's,
 * waits for it); pvr_pack_image (pvr_pack.c): the image file for a texture, for the hint. */
const char *pvr_pack_image(u32 tcw, u32 w, u32 h, u32 stride, char *path, size_t cap, u64 *over_key);
/* over_key: an over line's texture key, 0 for any other line; tcw, tw x th and stride name its texture, whose texels a
 * new kick decodes on the caller's thread (pvr_pack_put_over merges them on the loader's). */
u32 pvr_pack_take(const char *path, u64 over_key, u32 tcw, u32 tw, u32 th, u32 stride, const u32 **texels,
                  ID3D11Texture2D **tex, u32 *w, u32 *h);
void pvr_pack_load_reset(void);
void pvr_pack_load_report(void);
void pvr_pack_unfilter(u8 *p, u32 w, u32 h);
f32 pvr_pack_ms(LARGE_INTEGER *t); /* the ms since *t, which becomes now */
int pvr_pack_write_mips(const char *dir, int check);
const char *pvr_pack_entry(u32 i, char *path, size_t cap, u32 *over);
int pvr_pack_option(const char *arg, const char *value);
int pvr_pack_put_over(u32 *px, u32 iw, u32 ih, const char *file, const u32 *game, u32 w, u32 h);
void pack_bundled_dir(const char *name, char *out, size_t cap);
int pack_has_index(const char *dir);
u32 pack_read_choices(int has_game, const char *game_folder, const char *const **labels, const char *const **values);
/* runtime/console_dc.c: a game.NAME setting as the Game menu shows it (its menu_checks default when never chosen, off in
 * a check run), for an index line's if= word. pvr_pack_refresh: read the packs again (runtime/port.h). */
int port_menu_check_on(const char *setting);
void pvr_pack_refresh(void);
/* pvr_pack_line.c: an index.txt line's parts (1 an image line, 0 blank, -1 unreadable, -2 off by its if= word) */
#define PVR_PACK_RIM_SET 0x1000000u /* in rim: the line has a rim= word */
typedef struct PvrPackLine {
    unsigned long long key;
    char *file;
    int variant;         /* 0 every pad style, 1 xbox, 2 playstation; -1 an unknown word */
    u32 rim, over, wide; /* PVR_PACK_RIM_SET | RRGGBB or 0; the over word; wide=W or 0 */
} PvrPackLine;
int pvr_pack_line(char *line, PvrPackLine *e);
extern int pvr_pack_every_line; /* pvr_pack_write_mips: every line read, whatever its if= setting */

/* ta_parse.c: the scene's passes into s; per-frame statistics and first-seen classes go to the log. */
void ta_parse(PvrScene *s, const PvrPass *passes, u32 count);

/* pvr_state.c: each distinct text once per run, as "ta: ..." (what the stream uses; not a to-do) */
void pvr_seen(const char *fmt, ...);

/* pvr_twiddle.c: decodes the texture a TCW and the TSP size bits describe, top level first, mip levels after (each
 * level w x h RGBA8 rows). Returns the number of levels, 0 for a format it cannot decode (logged). */
u32 pvr_decode(u32 tcw, u32 w, u32 h, u32 stride, u32 *out, u32 out_texels);
void pvr_yuv422_rgba(const u8 *p, u32 w, u32 h, u32 stride, u32 *out); /* host YUV422 rows (stride bytes) to RGBA */
u32 pvr_decode_texels(u32 tcw, u32 w, u32 h); /* out_texels pvr_decode needs */
u32 pvr_texture_bytes(u32 tcw, u32 w, u32 h, u32 stride); /* the bytes of texture memory it reads */
u32 pvr_tcw_layout(u32 tcw); /* the TCW without a palettized texture's selector bits (they are no layout bits) */
int pvr_palette_range(u32 tcw, u32 *first, u32 *count); /* the palette entries it reads; 0: not palettized */

/* pvr_pal.c: palette RAM and PAL_RAM_CTRL. pvr_palette_stamp changes when any of the entries (or the format) does. */
u32 pvr_palette_entry(u32 index);
u32 pvr_palette_format(void);
u32 pvr_palette_stamp(u32 first, u32 count);

/* pvr_pack.c: a texture pack's image for the texture (key: format bits, size, stride and the texture memory bytes
 * read); its level count (every image has its whole chain, mip-mapped guest or not), 0 for none. The texels stay
 * the pack's. pvr_pack_generation changes when the pack is switched on or off. */
u32 pvr_pack_levels(u32 tcw, u32 w, u32 h, u32 stride, const u32 **texels, ID3D11Texture2D **tex, u32 *pw, u32 *ph);
u64 pvr_pack_key(const u8 *vram, u32 tcw, u32 w, u32 h, u32 stride);
u32 pvr_pack_generation(void);
/* The colour a pack gives the untextured polygon after this texture (0x1000000 | RRGGBB), 0 for none. */
u32 pvr_pack_rim(u32 tcw, u32 tsp);
f32 pvr_pack_wide(u32 tcw, u32 tsp); /* the image's width over the columns that are the texture; 0: no wide= image */
/* pvr_keys.c: --texture-keys DIR lists the textures a run decodes (a PNG each, and the pack key); 0 if not ours */
int pvr_keys_option(const char *arg, const char *value);
void pvr_keys_note(u32 tcw, u32 w, u32 h, u32 stride, const u32 *texels);
/* base/pad_style.c: the pad style (0 dreamcast, 1 xbox, 2 playstation) a pack entry may be for */
int pad_style_option(const char *arg, const char *value);
int pad_style(void);
u32 pad_style_serial(void);

/* pvr_state.c: --dump-vram SCENE writes the texture memory as it is when that scene is parsed */
void pvr_vram_dump(u32 scene);

/* pvr_tex.c */
int pvr_yuv_option(const char *value); /* --pvr-yuv simd|table|check (pvr_twiddle.c) */
void pvr_tex_invalidate(u32 offset, u32 size);
ID3D11ShaderResourceView *pvr_tex_get(u32 tcw, u32 tsp); /* NULL: draw untextured (logged) */
/* How a texture pack's image is sampled (pvr_states.c): 0 not a pack image, PVR_PACK_MIPPED in place of a mip-mapped
 * guest texture, PVR_PACK_FLAT of one that is not. Kept on the view, so a retained scene samples as it did. */
#define PVR_PACK_MIPPED 1u
#define PVR_PACK_FLAT 2u
u32 pvr_tex_pack_mode(ID3D11ShaderResourceView *srv);
int pvr_tex_option(const char *arg, const char *value); /* --tex-recheck on|off */

/* pvr_states.c: D3D11 state objects, created once per distinct state */
ID3D11BlendState *pvr_blend_state(u32 list, u32 tsp);
ID3D11DepthStencilState *pvr_depth_state(u32 list, u32 isp);
ID3D11SamplerState *pvr_sampler_state(u32 tsp, u32 uv_out, u32 pack); /* PvrBatch.uv_out, pvr_tex_pack_mode */
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
int pvr_frame_share_clip(D3D11_RECT *r); /* pvr_clip.c: 0 unless a wipe has the clip open to the frame */

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

/* pvr_state.c: port_render_keep_frame for the render starting now (pvr_render); g_pvr_keep: it keeps the frame */
void pvr_take_keep(void);
void pvr_frame_bars(void); /* after a render (pvr_render): one that keeps the frame keeps the bars its first render set */
extern int g_pvr_keep;

/* pvr_read.c: after each render, keeps a game frame (not an in-between one) for pvr_read_frame */
void pvr_keep_frame(void);

#endif
