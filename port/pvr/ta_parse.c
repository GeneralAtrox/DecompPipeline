/* The TA parameter stream: each list of each pass, from its start to the end the SDK recorded, read the way the
 * Tile Accelerator reads it. A parameter control word (PCW) says what the 32- or 64-byte parameter is; vertices
 * take their layout from the last polygon header. Only the words a parameter type defines are read: games leave
 * stale words in the unused ones.
 *
 * The log gets each class the stream uses once ("ta: ..."), what the renderer does not handle as a to-do, and a
 * per-scene count line at the first scene and every STATS_EVERY scenes. --ta-dump SCENE writes that scene's lists as
 * words to ta_scene_SCENE.txt, for reading the stream by hand; --dump-vram SCENE writes the texture memory at that
 * scene to ta_vram_SCENE.bin (pvr_state.c). */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "pvr_int.h"
#include "platform.h"

#define PARA_END 0u
#define PARA_CLIP 1u
#define PARA_OBJ_LIST_SET 2u
#define PARA_POLY 4u
#define PARA_SPRITE 5u
#define PARA_VERTEX 7u
#define PCW_END_OF_STRIP (1u << 28)
#define PCW_CLIP_SHIFT 16
#define PCW_SHADOW (1u << 7)
#define PCW_VOLUME (1u << 6)
#define PCW_COL_SHIFT 4
#define PCW_TEXTURE (1u << 3)
#define PCW_OFFSET (1u << 2)
#define PCW_UV16 1u
#define CLIP_INSIDE 2u
#define CLIP_OUTSIDE 3u
#define TILE 32u
#define SLOT_WORDS 8u
#define SPRITE_TYPE 15u
#define MODVOL_TYPE 17u
#define STATS_EVERY 600u

typedef struct Poly {
    u32 pcw, isp, tsp, tcw, vtype, flags;
    f32 face[2][4]; /* intensity polygons: the face base and offset colours, a r g b */
    u32 sprite_col[2];
} Poly;

typedef struct Parse {
    PvrScene *s;
    u32 list;
    Poly poly;
    int have_poly;
    u16 tile_clip[4], clip[4];
    u32 headers, vertices;
    int odd; /* a stray vertex or an unknown parameter: --ta-dump odd writes the scene */
} Parse;

/* A union, not memcpy: at the judge's /O1 (no /Oi) a memcpy is a CRT call, once per vertex word. */
static f32 wf(u32 w)
{
    union {
        u32 u;
        f32 f;
    } b;
    b.u = w;
    return b.f;
}

static u32 to_byte(f32 v) { return v <= 0.0f ? 0u : v >= 1.0f ? 255u : (u32)(v * 255.0f + 0.5f); }

static u32 pack(f32 a, f32 r, f32 g, f32 b) { return to_byte(a) << 24 | to_byte(r) << 16 | to_byte(g) << 8 | to_byte(b); }

static u32 intensity(const f32 face[4], f32 i) { return pack(face[0], face[1] * i, face[2] * i, face[3] * i); }

/* The vertex parameter type a polygon header selects (the TA's table: colour type, texture, 16-bit UV, two volumes). */
static u32 vertex_type(u32 pcw)
{
    u32 col = (pcw >> PCW_COL_SHIFT) & 3u, tex = (pcw & PCW_TEXTURE) != 0, uv16 = pcw & PCW_UV16;
    if (pcw & PCW_VOLUME) {
        if (!tex) return col == 0 ? 9u : 10u;
        return (col == 0 ? 11u : 13u) + uv16;
    }
    if (!tex) return col == 0 ? 0u : col == 1 ? 1u : 2u;
    return (col == 0 ? 3u : col == 1 ? 5u : 7u) + uv16;
}

static u32 vertex_words(u32 type) { return type == 5u || type == 6u || type >= 11u ? 16u : 8u; }

/* Header words: 8, or 16 for intensity colours with an offset face colour (and their two-volume form). */
static u32 header_words(u32 pcw)
{
    u32 col = (pcw >> PCW_COL_SHIFT) & 3u;
    return col == 2u && ((pcw & PCW_VOLUME) || (pcw & PCW_OFFSET)) ? 16u : 8u;
}

static void read_face(f32 out[4], const u32 *w)
{
    for (u32 i = 0; i < 4u; i++) out[i] = wf(w[i]);
}

static void poly_header(Parse *p, const u32 *w, int sprite)
{
    Poly *q = &p->poly;
    u32 col, clip_mode;
    q->pcw = w[0];
    q->isp = w[1];
    q->tsp = w[2];
    q->tcw = w[3];
    q->flags = 0;
    if (sprite) {
        q->vtype = SPRITE_TYPE + ((q->pcw & PCW_TEXTURE) != 0);
        q->sprite_col[0] = w[4];
        q->sprite_col[1] = w[5];
    } else if (p->list == PVRX_LIST_OPAQUE_MOD || p->list == PVRX_LIST_TRANS_MOD) {
        q->vtype = MODVOL_TYPE;
    } else {
        q->vtype = vertex_type(q->pcw);
        col = (q->pcw >> PCW_COL_SHIFT) & 3u;
        if (col == 2u && header_words(q->pcw) == 16u) {
            read_face(q->face[0], w + 8);
            read_face(q->face[1], w + 12);
        } else if (col == 2u) {
            read_face(q->face[0], w + 4);
        }
    }
    if (q->pcw & PCW_TEXTURE) q->flags |= PVR_BF_TEXTURE;
    if ((q->pcw & PCW_TEXTURE) && (q->pcw & PCW_OFFSET)) q->flags |= PVR_BF_OFFSET;
    if (p->list == PVRX_LIST_PUNCH) q->flags |= PVR_BF_PUNCH;
    if ((q->pcw & PCW_SHADOW) && q->vtype != MODVOL_TYPE) q->flags |= PVR_BF_SHADOW;
    clip_mode = (q->pcw >> PCW_CLIP_SHIFT) & 3u;
    memmove(p->clip, (u16[4]){0, 0, 640, 480}, sizeof p->clip);
    if (clip_mode == CLIP_INSIDE) memmove(p->clip, p->tile_clip, sizeof p->clip);
    if (clip_mode == CLIP_OUTSIDE) pvr_todo("ta: user clip outside (list %u)", p->list);
    p->have_poly = 1;
    p->headers++;
    pvr_seen("list %u: %s vertex type %u, isp %08x tsp %08x", p->list, sprite ? "sprite" : "polygon", q->vtype,
             q->isp & 0xfff00000u, q->tsp & 0xfff800c0u);
    if (q->vtype == MODVOL_TYPE) /* a volume's triangles: only the ISP word (volume instruction, culling) counts */
        tb_poly(p->s, p->list, q->isp, 0, 0, 0, p->clip);
    else
        tb_poly(p->s, p->list, q->isp, q->tsp, q->tcw, q->flags, p->clip);
}

/* A modifier volume triangle: a, b, c with x y z each. */
static void volume_triangle(Parse *p, const u32 *w)
{
    PvrVtx v[3];
    memset(v, 0, sizeof v);
    for (u32 i = 0; i < 3u; i++) {
        v[i].x = wf(w[1 + i * 3u]);
        v[i].y = wf(w[2 + i * 3u]);
        v[i].z = wf(w[3 + i * 3u]);
    }
    p->vertices += 3u;
    tb_tri(p->s, v);
}

static void uv16(PvrVtx *v, u32 w)
{
    v->u = wf(w & 0xffff0000u);
    v->v = wf(w << 16);
}

static void vertex(Parse *p, const u32 *w)
{
    const Poly *q = &p->poly;
    PvrVtx v;
    memset(&v, 0, sizeof v);
    v.x = wf(w[1]);
    v.y = wf(w[2]);
    v.z = wf(w[3]);
    v.base = 0xffffffffu;
    switch (q->vtype) {
    case 0: v.base = w[6]; break;
    case 1: v.base = pack(wf(w[4]), wf(w[5]), wf(w[6]), wf(w[7])); break;
    case 2: v.base = intensity(q->face[0], wf(w[6])); break;
    case 3: v.u = wf(w[4]); v.v = wf(w[5]); v.base = w[6]; v.offset = w[7]; break;
    case 4: uv16(&v, w[4]); v.base = w[6]; v.offset = w[7]; break;
    case 5:
    case 6:
        if (q->vtype == 5u) {
            v.u = wf(w[4]);
            v.v = wf(w[5]);
        } else {
            uv16(&v, w[4]);
        }
        v.base = pack(wf(w[8]), wf(w[9]), wf(w[10]), wf(w[11]));
        v.offset = pack(wf(w[12]), wf(w[13]), wf(w[14]), wf(w[15]));
        break;
    case 7:
    case 8:
        if (q->vtype == 7u) {
            v.u = wf(w[4]);
            v.v = wf(w[5]);
        } else {
            uv16(&v, w[4]);
        }
        v.base = intensity(q->face[0], wf(w[6]));
        v.offset = intensity(q->face[1], wf(w[7]));
        break;
    default:
        pvr_todo("ta: vertex type %u (list %u)", q->vtype, p->list);
        return;
    }
    p->vertices++;
    tb_vertex(p->s, &v, (w[0] & PCW_END_OF_STRIP) != 0);
}

/* A sprite: a, b, c with x y z, d with x y (its z on the plane of the other three), packed 16-bit UVs for a, b, c. */
static void sprite(Parse *p, const u32 *w)
{
    PvrVtx v[4];
    f32 ux, uy, uz, vx, vy, vz, nx, ny, nz;
    memset(v, 0, sizeof v);
    for (u32 i = 0; i < 3u; i++) {
        v[i].x = wf(w[1 + i * 3u]);
        v[i].y = wf(w[2 + i * 3u]);
        v[i].z = wf(w[3 + i * 3u]);
    }
    v[3].x = wf(w[10]);
    v[3].y = wf(w[11]);
    ux = v[1].x - v[0].x, uy = v[1].y - v[0].y, uz = v[1].z - v[0].z;
    vx = v[2].x - v[0].x, vy = v[2].y - v[0].y, vz = v[2].z - v[0].z;
    nx = uy * vz - uz * vy, ny = uz * vx - ux * vz, nz = ux * vy - uy * vx;
    v[3].z = nz != 0.0f ? v[0].z - (nx * (v[3].x - v[0].x) + ny * (v[3].y - v[0].y)) / nz : v[0].z;
    for (u32 i = 0; i < 3u; i++) uv16(&v[i], w[13 + i]);
    v[3].u = v[0].u + v[2].u - v[1].u;
    v[3].v = v[0].v + v[2].v - v[1].v;
    for (u32 i = 0; i < 4u; i++) {
        v[i].base = p->poly.sprite_col[0];
        v[i].offset = p->poly.sprite_col[1];
    }
    p->vertices += 4u;
    tb_quad(p->s, v);
}

static void parse_list(Parse *p, u32 start, u32 end)
{
    const u32 *w = (const u32 *)(uintptr_t)start, *stop = (const u32 *)(uintptr_t)end;
    p->have_poly = 0;
    p->s->open = 0;
    memmove(p->tile_clip, (u16[4]){0, 0, 640, 480}, sizeof p->tile_clip);
    while (w + SLOT_WORDS <= stop) {
        u32 type = w[0] >> 29, words = SLOT_WORDS;
        switch (type) {
        case PARA_END: p->have_poly = 0; break;
        case PARA_CLIP:
            p->tile_clip[0] = (u16)((w[4] & 0x3fu) * TILE);
            p->tile_clip[1] = (u16)((w[5] & 0xfu) * TILE);
            p->tile_clip[2] = (u16)(((w[6] & 0x3fu) + 1u) * TILE);
            p->tile_clip[3] = (u16)(((w[7] & 0xfu) + 1u) * TILE);
            break;
        case PARA_OBJ_LIST_SET: pvr_todo("ta: object list set (list %u)", p->list); break;
        case PARA_POLY:
            words = p->list == PVRX_LIST_OPAQUE_MOD || p->list == PVRX_LIST_TRANS_MOD ? SLOT_WORDS : header_words(w[0]);
            if (w + words > stop) return;
            poly_header(p, w, 0);
            break;
        case PARA_SPRITE: poly_header(p, w, 1); break;
        case PARA_VERTEX:
            if (!p->have_poly) {
                pvr_seen("list %u: vertices after an end of list, ignored as the TA does", p->list);
                p->odd = 1;
                break;
            }
            words = p->poly.vtype >= SPRITE_TYPE ? 16u : vertex_words(p->poly.vtype);
            if (w + words > stop) return;
            if (p->poly.vtype == MODVOL_TYPE)
                volume_triangle(p, w);
            else if (p->poly.vtype >= SPRITE_TYPE)
                sprite(p, w);
            else
                vertex(p, w);
            break;
        default:
            p->odd = 1;
            pvr_todo("ta: parameter %08x (list %u, offset %x): the rest of the list is skipped", w[0], p->list,
                     (u32)((uintptr_t)w - start));
            return;
        }
        w += words;
    }
}

static u32 g_dump_scene = 0xffffffffu, g_vram_scene = 0xffffffffu;
static int g_dump_odd;

int pvr_option(const char *arg, const char *value)
{
    if (value && !strcmp(arg, "--pvr-debug")) {
        g_pvr_debug |= strstr(value, "nocull") ? PVR_DBG_NOCULL : 0u;
        g_pvr_debug |= strstr(value, "nofog") ? PVR_DBG_NOFOG : 0u;
        g_pvr_debug |= strstr(value, "nodepth") ? PVR_DBG_NODEPTH : 0u;
        return 2;
    }
    if (value && !strcmp(arg, "--dump-vram")) {
        g_vram_scene = (u32)strtoul(value, NULL, 10);
        return 2;
    }
    if (!value || strcmp(arg, "--ta-dump")) return 0;
    if (!strcmp(value, "odd"))
        g_dump_odd = 1;
    else
        g_dump_scene = (u32)strtoul(value, NULL, 10);
    return 2;
}

static void dump(u32 scene, const PvrPass *passes, u32 count)
{
    char name[64];
    FILE *f;
    snprintf(name, sizeof name, "ta_scene_%u.txt", scene);
    if (fopen_s(&f, name, "w") || !f) return;
    for (u32 i = 0; i < count; i++)
        for (u32 l = 0; l < PVRX_LISTS; l++) {
            const u32 *w = (const u32 *)(uintptr_t)passes[i].start[l];
            u32 n = (passes[i].end[l] - passes[i].start[l]) / 4u;
            fprintf(f, "pass %u list %u: %08x..%08x (%u bytes)\n", i, l, passes[i].start[l], passes[i].end[l], n * 4u);
            for (u32 k = 0; k < n; k++) {
                if (k % 8u == 0) fprintf(f, "  %04x:", k * 4u);
                fprintf(f, " %08x", w[k]);
                if (k % 8u == 7u || k + 1u == n) fputc('\n', f);
            }
        }
    fclose(f);
    port_log("ta: scene %u written to %s\n", scene, name);
}

void ta_parse(PvrScene *s, const PvrPass *passes, u32 count)
{
    static u32 scenes;
    Parse p;
    if (scenes == g_dump_scene) dump(scenes, passes, count);
    if (scenes == g_vram_scene) pvr_vram_dump(scenes);
    memset(&p, 0, sizeof p);
    p.s = s;
    tb_reset(s);
    s->passes = count < PVRX_MAX_PASSES ? count : PVRX_MAX_PASSES;
    for (u32 i = 0; i < s->passes; i++) {
        for (u32 l = 0; l < PVRX_LISTS; l++) {
            p.list = l;
            s->range[i][l][0] = s->nbatch;
            parse_list(&p, passes[i].start[l], passes[i].end[l]);
            s->range[i][l][1] = s->nbatch;
        }
    }
    if (p.odd && g_dump_odd) {
        dump(scenes, passes, count);
        g_dump_odd = 0;
    }
    if (scenes++ % STATS_EVERY == 0)
        port_log("ta: scene %u (vblank %u): %u pass(es), %u headers, %u vertices, %u batches, %u triangles\n",
                 scenes - 1u, port_vblank_count(), s->passes, p.headers, p.vertices, s->nbatch, s->nvtx / 3u);
}
