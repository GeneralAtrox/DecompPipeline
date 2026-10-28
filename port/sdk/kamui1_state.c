/* Platform layer: Kamui 1's render state and strip calls.
 *
 * kmProcessVertexRenderState folds the vertex context's fields into the four hardware words the context keeps at
 * 0x90 (the parameter control word, the ISP/TSP instruction word, the TSP word and the texture control word): only
 * the fields whose bit is set in render_state, the others keep what the words hold. kmSetVertexRenderState lays the
 * global parameter for those words out in the vertex buffer description's global parameter buffer (32 or 64 bytes,
 * by the polygon type the control word gives) and notes its list and size; kmSetModifierRenderState adds a
 * two-volume polygon's second volume. kmStartVertexStrip copies the global parameter to its list's write pointer and
 * kmSetVertex copies one vertex after it; the vertex's own control word says where the strip ends. The words are
 * the hardware's (the PowerVR's TA parameter formats), so pvr/ draws the lists as they are.
 *
 * Needs from the game's headers: KMVERTEXCONTEXT and KMVERTEXBUFFDESC (read through kamui1.h's layouts) and the
 * function names below as the game's registry has them. */
#include "game.h"
#include "platform.h"
#include "port.h"
#include "kamui1.h"

/* render_state bits */
#define RS_DEPTH 0x00000001u
#define RS_CULLING 0x00000002u
#define RS_SHADING 0x00000008u
#define RS_MODIFIER 0x00000010u
#define RS_Z_WRITE 0x00000020u
#define RS_SRC_BLEND 0x00000040u
#define RS_DST_BLEND 0x00000080u
#define RS_FOG 0x00000100u
#define RS_SPECULAR 0x00000200u
#define RS_ALPHA 0x00000400u
#define RS_IGNORE_TEX_ALPHA 0x00000800u
#define RS_CLAMP_UV 0x00001000u
#define RS_FLIP_UV 0x00002000u
#define RS_FILTER 0x00004000u
#define RS_SUPER_SAMPLE 0x00008000u
#define RS_MIPMAP_ADJUST 0x00010000u
#define RS_TEX_SHADING 0x00020000u
#define RS_COLOR_CLAMP 0x00040000u
#define RS_PARAM_TYPE 0x00100000u
#define RS_LIST_TYPE 0x00200000u
#define RS_COLOR_TYPE 0x00400000u
#define RS_UV_FORMAT 0x00800000u
#define RS_SRC_SELECT 0x01000000u
#define RS_DST_SELECT 0x02000000u
#define RS_DCALC_EXACT 0x04000000u
#define RS_STRIP_LENGTH 0x08000000u
#define RS_USER_CLIP 0x10000000u

/* the parameter control word */
#define PCW_TYPE_SHIFT 29
#define PCW_POLYGON 4u
#define PCW_SPRITE 5u
#define PCW_LIST_SHIFT 24
#define PCW_GROUP 0x00800000u
#define PCW_STRIP_SHIFT 18
#define PCW_CLIP_SHIFT 16
#define PCW_TWO_VOLUMES 0x80u
#define PCW_VOLUME 0x40u
#define PCW_COLOR_SHIFT 4
#define PCW_TEXTURE 0x08u
#define PCW_OFFSET 0x04u
#define PCW_GOURAUD 0x02u
#define PCW_UV16 0x01u
#define PCW_KEEP 0xe78f00ffu
/* the ISP/TSP instruction word */
#define ISP_DEPTH_SHIFT 29
#define ISP_CULL_SHIFT 27
#define ISP_Z_WRITE_OFF 0x04000000u
#define ISP_TEXTURE 0x02000000u
#define ISP_OFFSET 0x01000000u
#define ISP_GOURAUD 0x00800000u
#define ISP_UV16 0x00400000u
#define ISP_DCALC_EXACT 0x00100000u
#define ISP_KEEP 0xffd00000u
/* the TSP word */
#define TSP_SRC_SHIFT 29
#define TSP_DST_SHIFT 26
#define TSP_SRC_SELECT 0x02000000u
#define TSP_DST_SELECT 0x01000000u
#define TSP_FOG_SHIFT 22
#define TSP_COLOR_CLAMP 0x00200000u
#define TSP_USE_ALPHA 0x00100000u
#define TSP_IGNORE_TEX_ALPHA 0x00080000u
#define TSP_FLIP_SHIFT 17
#define TSP_CLAMP_SHIFT 15
#define TSP_FILTER_SHIFT 13
#define TSP_SUPER_SAMPLE 0x00001000u
#define TSP_MIPMAP_SHIFT 8
#define TSP_SHADING_SHIFT 6
#define TSP_U_SHIFT 3
#define TSP_OPAQUE_BLEND 0x20000000u /* the opaque list draws ONE, ZERO whatever the context asks */
/* the texture control word */
#define TCW_MIPMAP 0x80000000u
#define TCW_VQ 0x40000000u
#define TCW_FORMAT 0x38000000u
#define TCW_PAL4 0x28000000u
#define TCW_PAL8 0x30000000u
#define TCW_PAL_SHIFT 21
#define TCW_SCAN_ORDER 0x04000000u /* set: not twiddled */
#define TCW_STRIDE 0x02000000u
#define TCW_ADDR 0x001fffffu
/* K1Surface.flags */
#define SURF_MIPMAP 0x01u
#define SURF_TWIDDLED 0x04u
#define SURF_VQ 0x08u
#define SURF_STRIDE 0x10u

#define BLEND_MODES 12u
#define BLEND_KEEP 0xffu
#define SHADING_TEXTURED 1u /* shading_mode >> 1: 2 and 3 are the textured modes */
#define COLOR_MAX 255.0f
#define GLOBAL_WORDS 16u

/* The colour words a global parameter carries after its first four, by the polygon's colour type and volumes. */
enum { G_PLAIN, G_FACE, G_FACE_OFFSET, G_TWO_VOLUMES, G_TWO_VOLUMES_FACE, G_SPRITE = 16, G_MODIFIER = 32 };

/* Each blend mode's source and destination instructions; BLEND_KEEP where the mode is not one for that side.
 * Modes 0 and 1 (both inverse source alpha, both source alpha) set both sides at once. */
static const u8 g_src_blend[BLEND_MODES] = {5, 4, 6, 2, 7, 3, 5, BLEND_KEEP, 4, BLEND_KEEP, 1, 0};
static const u8 g_dst_blend[BLEND_MODES] = {4, 5, 6, BLEND_KEEP, 7, BLEND_KEEP, 5, 3, 4, 2, 1, 0};
static const u8 g_filter[4] = {0, 2, 1, 3};

static u32 g_strip_length;       /* the strip length the control word gets without RS_STRIP_LENGTH */
static u32 g_modifier_volume = 1; /* the volume bit a context with RS_MODIFIER and an odd select_modifier gets */
/* The last kmSetVertexRenderState's words and colours, which kmSetModifierRenderState lays out again. */
static struct {
    u32 pcw, isp, tsp, tcw, modifier_isp;
    u32 tsp1, tcw1;
    f32 face[4], offset[4], face1[4];
} g_staged;

static void *host(u32 addr) { return (void *)(uintptr_t)guest_fold(addr); }

static u32 tex_size(u32 edge)
{
    u32 n = 0;
    while (n < 7u && (8u << n) < edge) n++;
    return n;
}

static u32 field(u32 word, u32 mask, u32 value) { return (word & ~mask) | (value & mask); }

KMSTATUS kmProcessVertexRenderState(KMVERTEXCONTEXT *context)
{
    K1VertexContext *c = (K1VertexContext *)context;
    u32 rs = c->render_state, pcw = c->pcw, isp = c->isp, tsp = c->tsp, tcw = c->tcw;
    const K1Surface *tex = c->texture ? host(c->texture) : NULL;
    int textured = (s32)c->shading_mode >> 1 == (s32)SHADING_TEXTURED && tex;

    if (rs & RS_DEPTH) isp = field(isp, 7u << ISP_DEPTH_SHIFT, c->depth_mode << ISP_DEPTH_SHIFT);
    if (rs & RS_CULLING) isp = field(isp, 3u << ISP_CULL_SHIFT, c->culling_mode << ISP_CULL_SHIFT);
    if (rs & RS_Z_WRITE) isp = field(isp, ISP_Z_WRITE_OFF, c->z_write_disable ? ISP_Z_WRITE_OFF : 0u);
    if (rs & RS_SHADING) {
        isp = field(isp, ISP_TEXTURE, (c->shading_mode >> 1) ? ISP_TEXTURE : 0u);
        isp = field(isp, ISP_GOURAUD, (c->shading_mode & 1u) ? ISP_GOURAUD : 0u);
    }
    if (rs & RS_SPECULAR) isp = field(isp, ISP_OFFSET, c->use_specular ? ISP_OFFSET : 0u);
    if (rs & RS_UV_FORMAT) isp = field(isp, ISP_UV16, c->uv_format ? ISP_UV16 : 0u);
    if (rs & RS_DCALC_EXACT) isp = field(isp, ISP_DCALC_EXACT, c->dcalc_exact ? ISP_DCALC_EXACT : 0u);

    if ((rs & RS_SRC_BLEND) && c->src_blend < BLEND_MODES) {
        u32 m = c->src_blend;
        if (g_src_blend[m] != BLEND_KEEP) tsp = field(tsp, 7u << TSP_SRC_SHIFT, (u32)g_src_blend[m] << TSP_SRC_SHIFT);
        if (m < 2u) tsp = field(tsp, 7u << TSP_DST_SHIFT, (u32)g_dst_blend[m] << TSP_DST_SHIFT);
    }
    if ((rs & RS_DST_BLEND) && c->dst_blend < BLEND_MODES) {
        u32 m = c->dst_blend;
        if (g_dst_blend[m] != BLEND_KEEP) tsp = field(tsp, 7u << TSP_DST_SHIFT, (u32)g_dst_blend[m] << TSP_DST_SHIFT);
        if (m < 2u) tsp = field(tsp, 7u << TSP_SRC_SHIFT, (u32)g_src_blend[m] << TSP_SRC_SHIFT);
    }
    if (rs & RS_SRC_SELECT) tsp = field(tsp, TSP_SRC_SELECT, c->src_select ? TSP_SRC_SELECT : 0u);
    if (rs & RS_DST_SELECT) tsp = field(tsp, TSP_DST_SELECT, c->dst_select ? TSP_DST_SELECT : 0u);
    if (rs & RS_FOG) tsp = field(tsp, 3u << TSP_FOG_SHIFT, c->fog_mode << TSP_FOG_SHIFT);
    if (rs & RS_COLOR_CLAMP) tsp = field(tsp, TSP_COLOR_CLAMP, c->color_clamp ? TSP_COLOR_CLAMP : 0u);
    if (rs & RS_ALPHA) tsp = field(tsp, TSP_USE_ALPHA, c->use_alpha ? TSP_USE_ALPHA : 0u);
    if (rs & RS_IGNORE_TEX_ALPHA)
        tsp = field(tsp, TSP_IGNORE_TEX_ALPHA, c->ignore_texture_alpha ? TSP_IGNORE_TEX_ALPHA : 0u);
    if (rs & RS_CLAMP_UV) tsp = field(tsp, 3u << TSP_CLAMP_SHIFT, c->clamp_uv << TSP_CLAMP_SHIFT);
    if (rs & RS_FLIP_UV) tsp = field(tsp, 3u << TSP_FLIP_SHIFT, c->flip_uv << TSP_FLIP_SHIFT);
    if (rs & RS_FILTER) tsp = field(tsp, 3u << TSP_FILTER_SHIFT, (u32)g_filter[c->filter_mode & 3u] << TSP_FILTER_SHIFT);
    if (rs & RS_SUPER_SAMPLE) tsp = field(tsp, TSP_SUPER_SAMPLE, c->super_sample ? TSP_SUPER_SAMPLE : 0u);
    if (rs & RS_MIPMAP_ADJUST) {
        u32 d = c->mipmap_adjust & 0xfu;
        tsp = field(tsp, 0xfu << TSP_MIPMAP_SHIFT, (d ? d : 4u) << TSP_MIPMAP_SHIFT); /* 0 means the default, 1.0 */
    }
    if (rs & RS_TEX_SHADING) tsp = field(tsp, 3u << TSP_SHADING_SHIFT, c->texture_shading << TSP_SHADING_SHIFT);

    /* A textured context takes its texture's sizes and control word from the surface, whatever render_state says. */
    if (textured) {
        u32 fmt;
        tsp = field(tsp, 7u << TSP_U_SHIFT, tex_size(tex->width) << TSP_U_SHIFT);
        tsp = field(tsp, 7u, tex_size(tex->height));
        tcw = field(tcw, TCW_MIPMAP, (tex->flags & SURF_MIPMAP) ? TCW_MIPMAP : 0u);
        tcw = field(tcw, TCW_VQ, (tex->flags & SURF_VQ) ? TCW_VQ : 0u);
        tcw = field(tcw, TCW_FORMAT, tex->pixel_format);
        fmt = tcw & TCW_FORMAT;
        if (fmt == TCW_PAL4 || fmt == TCW_PAL8) {
            tcw = field(tcw, 0x3fu << TCW_PAL_SHIFT, c->palette_bank << TCW_PAL_SHIFT);
        } else {
            tcw = field(tcw, TCW_SCAN_ORDER, (tex->flags & SURF_TWIDDLED) ? 0u : TCW_SCAN_ORDER);
            tcw = field(tcw, TCW_STRIDE, (tex->flags & SURF_STRIDE) ? TCW_STRIDE : 0u);
        }
        tcw = field(tcw, TCW_ADDR, tex->surface >> 3);
    }

    if ((rs & RS_PARAM_TYPE) && c->param_type <= 2u) /* 0, 1 polygon, 2 sprite; others keep the type */
        pcw = field(pcw, 7u << PCW_TYPE_SHIFT, (c->param_type == 2u ? PCW_SPRITE : PCW_POLYGON) << PCW_TYPE_SHIFT);
    if (rs & RS_LIST_TYPE) pcw = field(pcw, 7u << PCW_LIST_SHIFT, c->list_type << PCW_LIST_SHIFT);
    pcw |= PCW_GROUP;
    pcw = field(pcw, 3u << PCW_STRIP_SHIFT, ((rs & RS_STRIP_LENGTH) ? c->strip_length : g_strip_length) << PCW_STRIP_SHIFT);
    if (rs & RS_USER_CLIP) pcw = field(pcw, 3u << PCW_CLIP_SHIFT, c->user_clip << PCW_CLIP_SHIFT);
    /* A modifier volume closed by its last polygon (inside or outside) marks the volume. */
    pcw = field(pcw, PCW_TWO_VOLUMES | PCW_VOLUME,
                (c->modifier_isp & 0x60000000u) && (c->list_type & 1u) ? PCW_VOLUME : 0u);
    if ((rs & RS_MODIFIER) && (c->select_modifier & 1u)) {
        pcw = field(pcw, PCW_TWO_VOLUMES, c->select_modifier << 7);
        pcw = field(pcw, PCW_VOLUME, g_modifier_volume << 6);
    }
    if (rs & RS_COLOR_TYPE) pcw = field(pcw, 3u << PCW_COLOR_SHIFT, c->color_type << PCW_COLOR_SHIFT);
    if (rs & RS_SHADING) {
        pcw = field(pcw, PCW_TEXTURE, (c->shading_mode >> 1) ? PCW_TEXTURE : 0u);
        pcw = field(pcw, PCW_GOURAUD, (c->shading_mode & 1u) ? PCW_GOURAUD : 0u);
    }
    if (rs & RS_SPECULAR) pcw = field(pcw, PCW_OFFSET, c->use_specular ? PCW_OFFSET : 0u);
    if (rs & RS_UV_FORMAT) pcw = (pcw & ~PCW_UV16) | c->uv_format;

    c->pcw = pcw & PCW_KEEP;
    c->isp = isp & ISP_KEEP;
    c->tsp = tsp;
    c->tcw = tcw;
    return K1_OK;
}

static u32 packed(const f32 argb[4])
{
    u32 w = 0;
    for (u32 i = 0; i < 4u; i++) w = w << 8 | ((u32)(s32)(argb[i] * COLOR_MAX) & 0xffu);
    return w;
}

/* The polygon's global parameter layout, from its control word: sprite, list, volumes, colour type, offset. */
static u32 layout(u32 pcw)
{
    u32 color = pcw >> PCW_COLOR_SHIFT & 3u, offset = (pcw & PCW_OFFSET) != 0u;
    if (pcw >> PCW_TYPE_SHIFT & 1u) return pcw >> PCW_LIST_SHIFT & 1u ? 0xffu : G_SPRITE;
    if (pcw >> PCW_LIST_SHIFT & 1u) return G_MODIFIER;
    if (pcw & PCW_VOLUME) return color == 2u ? G_TWO_VOLUMES_FACE : G_TWO_VOLUMES;
    if (color == 2u) return offset ? G_FACE_OFFSET : G_FACE;
    return G_PLAIN;
}

/* The staged words as a global parameter in the vertex buffer's global parameter buffer, and its list and size in
 * the list state words (what the description's global_param and list_state point at). */
static KMSTATUS write_global(void)
{
    u32 *g = k1_global_param(), *state = k1_list_state(), list = g_staged.pcw >> PCW_LIST_SHIFT & 7u, n = 4u;
    if (!g || !state) return K1_ERROR;
    memset(g, 0, GLOBAL_WORDS * 4u);
    g[0] = g_staged.pcw;
    if (list & 1u) {
        g[1] = g_staged.modifier_isp;
        n = 8u;
    } else {
        g[1] = g_staged.isp;
        g[2] = list == 0u ? (g_staged.tsp & 0x03ffffffu) | TSP_OPAQUE_BLEND : g_staged.tsp;
        g[3] = g_staged.tcw;
        switch (layout(g_staged.pcw)) {
        case G_PLAIN: n = 8u; break;
        case G_FACE: memmove(&g[4], g_staged.face, 16u); n = 8u; break;
        case G_FACE_OFFSET:
            memmove(&g[8], g_staged.face, 16u);
            memmove(&g[12], g_staged.offset, 16u);
            n = 16u;
            break;
        case G_TWO_VOLUMES: g[4] = g_staged.tsp1; g[5] = g_staged.tcw1; n = 8u; break;
        case G_TWO_VOLUMES_FACE:
            g[4] = g_staged.tsp1;
            g[5] = g_staged.tcw1;
            memmove(&g[8], g_staged.face, 16u);
            memmove(&g[12], g_staged.face1, 16u);
            n = 16u;
            break;
        case G_SPRITE: g[4] = packed(g_staged.face); g[5] = packed(g_staged.offset); n = 8u; break;
        default: break;
        }
    }
    state[0] = list;
    state[1] = n * 4u;
    return K1_OK;
}

KMSTATUS kmSetVertexRenderState(KMVERTEXCONTEXT *context)
{
    const K1VertexContext *c = (const K1VertexContext *)context;
    g_staged.pcw = c->pcw;
    g_staged.isp = c->isp;
    g_staged.tsp = c->tsp;
    g_staged.tcw = c->tcw;
    g_staged.modifier_isp = c->modifier_isp;
    memmove(g_staged.face, c->face_color, sizeof g_staged.face);
    memmove(g_staged.offset, c->offset_color, sizeof g_staged.offset);
    return write_global();
}

/* A two-volume polygon's second volume: its TSP and texture words and face colour, beside the first volume's. */
KMSTATUS kmSetModifierRenderState(KMVERTEXCONTEXT *context)
{
    const K1VertexContext *c = (const K1VertexContext *)context;
    g_staged.tsp1 = c->tsp;
    g_staged.tcw1 = c->tcw;
    memmove(g_staged.face1, c->face_color, sizeof g_staged.face1);
    return write_global();
}

/* Appends bytes to the list's buffer at its write pointer and advances the pointer (whole words, as Kamui does). */
static KMSTATUS append(const K1VertexBuffDesc *d, const void *src, u32 bytes)
{
    u32 list = d->list_state[0];
    if (list >= K1_LISTS || !d->current[list]) return K1_ERROR;
    memmove(host(d->current[list]), src, bytes);
    d->current[list] += bytes & ~3u;
    return K1_OK;
}

KMSTATUS kmStartVertexStrip(KMVERTEXBUFFDESC *bufferDesc)
{
    const K1VertexBuffDesc *d = (const K1VertexBuffDesc *)bufferDesc;
    return append(d, d->global_param, d->list_state[1]);
}

KMSTATUS kmSetVertex(KMVERTEXBUFFDESC *bufferDesc, void *vertex, s32 vertexType, s32 structSize)
{
    (void)vertexType;
    return append((const K1VertexBuffDesc *)bufferDesc, host((u32)(uintptr_t)vertex), (u32)structSize);
}
