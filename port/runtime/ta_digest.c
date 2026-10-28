/* The display-list digest's port half (tools/hw_emu.lua is the emulator's; tools/ta_digest.py compares and has the
 * fields each mode uses, with their sources). hw_check.c's walk hands each strip's header and vertex words here; each
 * strip's fields are decoded as Flycast's TA decoder does (core/hw/pvr/ta_vtx.cpp: float colours to bytes by its table
 * of the float's top 16 bits, intensity as the face colour's bytes times the saturated intensity over 256, 16-bit UVs
 * as the float's top half), masked to what the strip's mode uses, and hashed; per list the first strip's hash and the
 * strips' hashes chained from strip 0, 1 and 2 (the D line), then each strip's first lane (the H line). --hw-dump
 * F:FILE writes the masked fields of the scene of emulator frame F ("S list strip kind count pcw tsp", "V" and each
 * field), as LS_HW_DUMP does on the emulator's side. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "port.h"
#include "platform.h"
#include "lockstep.h"

#define P1 4294967291ull
#define M1 65599ull
#define P2 4294967279ull
#define M2 31337ull
#define PCW_TEXTURE (1u << 3)
#define PCW_OFFSET (1u << 2)
#define PCW_GOURAUD (1u << 1)
#define PCW_UV16 1u
#define PCW_VOLUME (1u << 6)
#define TSP_USE_ALPHA (1u << 20)
#define FOG_PER_VERTEX 1u
#define KIND_STRIP 2u
#define KIND_SPRITE 3u
#define MAX_STRIPS 65536u /* a list's strip hashes kept for its H line */
#define MAX_FIELDS 4096u /* a strip's fields kept for --hw-dump (the hash takes them all) */

typedef struct Lanes {
    u64 a, b;
} Lanes;

static struct {
    u32 pcw, tsp, sprite, colour_words, n, nf, strips, on, negx, negy; /* neg: vertices left of / above the screen */
    u32 sprite_col[2], face[2]; /* a r g b words: the sprite's colours, the intensity face colours as Flycast's bytes */
    u32 f[MAX_FIELDS];
    u8 fpos[MAX_FIELDS]; /* f's x y z words */
    Lanes h, hx, h0, acc[3]; /* h: the strip's so far; hx: its x y z only (a strip that draws no pixel) */
} g;
static u32 g_hs[MAX_STRIPS];
static u8 g_off[MAX_STRIPS]; /* the strip draws no pixel: its H hash marked "o" */
static FILE *g_dump;
static u32 g_dump_frame, g_dump_list;

static void mix(Lanes *h, u32 x)
{
    h->a = (h->a * M1 + x) % P1;
    h->b = (h->b * M2 + x) % P2;
}

static f32 wf(u32 w)
{
    union {
        u32 u;
        f32 f;
    } b;
    b.u = w;
    return b.f;
}

/* Flycast's float_to_satu8: its table indexed by the float's top 16 bits, each entry (u8)(clamp(v, 0, 1) * 255). */
static u32 sat8(u32 w)
{
    f32 v = wf(w & 0xffff0000u);
    if (v != v) return 255u;
    v = v < 0.0f ? 0.0f : v > 1.0f ? 1.0f : v;
    return (u32)(u8)(v * 255.0f);
}

static u32 float_colour(const u32 *argb)
{
    return sat8(argb[0]) << 24 | sat8(argb[1]) << 16 | sat8(argb[2]) << 8 | sat8(argb[3]);
}

static u32 intensity(u32 face, u32 w)
{
    u32 s = sat8(w), out = face & 0xff000000u;
    for (u32 sh = 0; sh < 24u; sh += 8u) out |= (((face >> sh) & 0xffu) * s / 256u) << sh;
    return out;
}

int ta_digest_option(const char *arg, const char *value)
{
    char path[512];
    if (!value || strcmp(arg, "--hw-dump")) return 0;
    if (sscanf_s(value, "%u:%511s", &g_dump_frame, path, (unsigned)sizeof path) != 2 || fopen_s(&g_dump, path, "w")
        || !g_dump)
        exit(2);
    return 2;
}

void ta_digest_list(u32 list)
{
    u32 face[2] = {g.face[0], g.face[1]}; /* Flycast's face colours last across lists */
    memset(&g, 0, sizeof g);
    memcpy(g.face, face, sizeof face);
    g.h.a = g.h.b = g.hx.a = g.hx.b = g.h0.a = g.h0.b = 1u;
    for (u32 k = 0; k < 3u; k++) g.acc[k].a = g.acc[k].b = 1u;
    g_dump_list = list;
}

/* A polygon or sprite header: its mode, and the colours later vertices take from it (ta_parse.c's poly_header). */
void ta_digest_header(const u32 *w, u32 words, int sprite)
{
    u32 col = (w[0] >> 4) & 3u;
    g.pcw = w[0];
    g.tsp = w[2];
    g.sprite = (u32)sprite;
    if (sprite) {
        g.sprite_col[0] = w[4];
        g.sprite_col[1] = w[5];
    } else if (col == 2u) { /* intensity mode 1 (mode 2 keeps the last ones) */
        g.face[0] = float_colour(w + (words == 16u ? 8u : 4u));
        if (words == 16u) g.face[1] = float_colour(w + 12u);
    }
}

static void put(u32 x)
{
    mix(&g.h, x);
    if (g.nf < MAX_FIELDS) g.fpos[g.nf] = 0, g.f[g.nf++] = x;
}

/* A position word (x, y or z); a vertex's x and y count it left of or above the screen when negative (not -0). */
static void pos(u32 x)
{
    put(x);
    mix(&g.hx, x);
    if (g.nf) g.fpos[g.nf - 1u] = 1;
}

static void vtx(u32 x, u32 y)
{
    g.negx += x > 0x80000000u;
    g.negy += y > 0x80000000u;
}

static void colours(u32 base, u32 offset)
{
    u32 tex = g.pcw & PCW_TEXTURE, shad = (g.tsp >> 6) & 3u, bm = 0xffffffffu, om = 0;
    if (!(g.tsp & TSP_USE_ALPHA)) bm = 0x00ffffffu;
    if (tex && shad == 0u) bm = 0;
    if (tex && shad == 1u) bm &= 0x00ffffffu;
    if (tex && (g.pcw & PCW_OFFSET)) om = ((g.tsp >> 22) & 3u) == FOG_PER_VERTEX ? 0xffffffffu : 0x00ffffffu;
    put(base & bm);
    put(offset & om);
}

static void uv(const u32 *w, u32 at)
{
    if (!(g.pcw & PCW_TEXTURE)) return;
    if (g.pcw & PCW_UV16) {
        put(w[at] & 0xffff0000u);
        put(w[at] << 16);
    } else {
        put(w[at]);
        put(w[at + 1u]);
    }
}

/* One vertex parameter (a sprite's whole parameter): its fields in the emulator half's order. */
void ta_digest_vertex(const u32 *w)
{
    u32 col = (g.pcw >> 4) & 3u, base = 0, offset = 0;
    if (g.sprite) {
        for (u32 i = 0; i < 3u; i++) {
            pos(w[1 + i * 3u]);
            pos(w[2 + i * 3u]);
            pos(w[3 + i * 3u]);
            vtx(w[1 + i * 3u], w[2 + i * 3u]);
            if (g.pcw & PCW_TEXTURE) {
                put(w[13u + i] & 0xffff0000u);
                put(w[13u + i] << 16);
            }
        }
        pos(w[10]);
        pos(w[11]);
        vtx(w[10], w[11]);
        colours(g.sprite_col[0], g.sprite_col[1]);
        g.n = 4u;
        return;
    }
    pos(w[1]);
    pos(w[2]);
    pos(w[3]);
    vtx(w[1], w[2]);
    if (g.pcw & PCW_VOLUME) { /* two volumes: x y z only (not modelled) */
        g.n++;
        return;
    }
    uv(w, 4u);
    if (!(g.pcw & PCW_TEXTURE)) {
        base = col == 0u ? w[6] : col == 1u ? float_colour(w + 4u) : intensity(g.face[0], w[6]);
    } else if (col == 0u) {
        base = w[6], offset = w[7];
    } else if (col == 1u) {
        base = float_colour(w + 8u), offset = float_colour(w + 12u);
    } else {
        base = intensity(g.face[0], w[6]), offset = intensity(g.face[1], w[7]);
    }
    if ((g.pcw & PCW_GOURAUD) || g.n >= 2u) colours(base, offset);
    g.n++;
}

/* A strip ends (hw_check.c's end_strip): its fields hashed into the list's digests, and dumped if asked. One wholly
 * left of or above the screen (every vertex's x, or every y, negative) draws no pixel: its x y z only. */
void ta_digest_strip(u32 frame)
{
    int off = g.n && (g.negx == g.n || g.negy == g.n);
    Lanes h = off ? g.hx : g.h;
    if (!g.n) return;
    if (g.strips == 0u) g.h0 = h;
    if (g.strips < MAX_STRIPS) g_hs[g.strips] = (u32)h.a, g_off[g.strips] = (u8)off;
    for (u32 k = 0; k < 3u && k <= g.strips; k++) {
        mix(&g.acc[k], (u32)h.a);
        mix(&g.acc[k], (u32)h.b);
    }
    if (g_dump && frame == g_dump_frame) {
        fprintf(g_dump, "S %u %u %u %u %x %x\nV", g_dump_list, g.strips, g.sprite ? KIND_SPRITE : KIND_STRIP,
                g.n, g.pcw & ~(1u << 28), g.tsp);
        for (u32 i = 0; i < g.nf; i++)
            if (!off || g.fpos[i]) fprintf(g_dump, " %x", g.f[i]);
        fputc('\n', g_dump);
    }
    g.strips++;
    g.n = g.nf = g.negx = g.negy = 0;
    g.h.a = g.h.b = g.hx.a = g.hx.b = 1u;
    g.on = 1;
}

/* The list's "D" line (hw_emu.lua's), if it had strips. */
void ta_digest_write(FILE *out, u32 list)
{
    if (!g.on) return;
    fprintf(out, "D %u %08x%08x %08x%08x %08x%08x %08x%08x\n", list, (u32)g.h0.a, (u32)g.h0.b, (u32)g.acc[0].a,
            (u32)g.acc[0].b, (u32)g.acc[1].a, (u32)g.acc[1].b, (u32)g.acc[2].a, (u32)g.acc[2].b);
    fprintf(out, "H %u", list);
    for (u32 i = 0; i < g.strips && i < MAX_STRIPS; i++) fprintf(out, " %08x%s", g_hs[i], g_off[i] ? "o" : "");
    fputc('\n', out);
}

void ta_digest_scene_done(u32 frame)
{
    if (g_dump && frame == g_dump_frame) {
        fputs("END\n", g_dump);
        fclose(g_dump);
        g_dump = NULL;
    }
}

int ta_digest_dumping(void) { return g_dump != NULL; }
