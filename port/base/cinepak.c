/* Cinepak ("cvid") into a 0x00RRGGBB frame (cinepak.h). The format: a 10-byte frame header (flags, size, width, height,
 * strip count), then strips of 4x4 blocks; each strip keeps two codebooks of 256 entries of 2x2 pixels (V4: four
 * entries a block, V1: one entry stretched over the block), updated in whole or by a bit mask, and a block list that is
 * all V1, V4/V1 by a bit each, or (inter frames) skip/V1/V4 by bits. Strip codebooks carry over between frames, and
 * from the strip above unless the frame's flag bit 0 says each strip brings its own. A Sega FILM file's frames have 2
 * extra bytes after the frame header (6 when they read FE 00 00 06 00 00), told by the header's size not matching the
 * chunk's.
 * Nearest existing: none in the kit (the Robo Pit audio check's Python, .work/fe/audio/au.py, decodes luma only, one
 * value per block); this follows the published description of the format. */
#include <stdlib.h>
#include <string.h>
#include "cinepak.h"

enum { MAX_STRIPS = 32 };

typedef struct {
    u32 v4[256][4], v1[256][4]; /* each entry's 2x2 pixels: top left, top right, bottom left, bottom right */
} Strip;

struct Cinepak {
    u32 w, h;
    int skip; /* the Sega FILM extra bytes: -1 not yet known */
    u32 *px;
    Strip strip[MAX_STRIPS];
};

static u32 be16(const u8 *p) { return (u32)p[0] << 8 | p[1]; }
static u32 be24(const u8 *p) { return (u32)p[0] << 16 | (u32)p[1] << 8 | p[2]; }
static u32 be32(const u8 *p) { return (u32)p[0] << 24 | be24(p + 1); }
static u32 clip(int v) { return v < 0 ? 0u : v > 255 ? 255u : (u32)v; }

Cinepak *cinepak_open(u32 w, u32 h)
{
    Cinepak *c = (Cinepak *)calloc(1, sizeof *c);
    if (!c) return NULL;
    c->px = (u32 *)calloc((size_t)w * h, 4);
    if (!c->px) {
        free(c);
        return NULL;
    }
    c->w = w, c->h = h, c->skip = -1;
    return c;
}

void cinepak_close(Cinepak *c)
{
    if (!c) return;
    free(c->px);
    free(c);
}

const u32 *cinepak_pixels(const Cinepak *c) { return c->px; }

/* a codebook chunk: 0x20..0x27, bit 2 four luma bytes only (else four luma and U, V), bit 0 a mask says which entries change */
static void codebook(u32 (*cb)[4], u32 id, const u8 *p, const u8 *end)
{
    u32 flag = 0, mask = 0, n = (id & 4u) ? 4u : 6u;
    for (u32 i = 0; i < 256; i++) {
        if ((id & 1u) && !(mask >>= 1)) {
            if (p + 4 > end) return;
            flag = be32(p), p += 4, mask = 0x80000000u;
        }
        if ((id & 1u) && !(flag & mask)) continue;
        if (p + n > end) return;
        int u = n == 6 ? (s8)p[4] : 0, v = n == 6 ? (s8)p[5] : 0;
        for (u32 k = 0; k < 4; k++) {
            int y = p[k];
            cb[i][k] = clip(y + 2 * v) << 16 | clip(y - u / 2 - v) << 8 | clip(y + 2 * u);
        }
        p += n;
    }
}

static void put2(Cinepak *c, u32 x, u32 y, u32 a, u32 b, u32 d, u32 e) /* a 2x2 square: a b over d e */
{
    u32 *r = c->px + (size_t)y * c->w + x;
    if (y >= c->h) return;
    if (x < c->w) r[0] = a;
    if (x + 1 < c->w) r[1] = b;
    if (y + 1 >= c->h) return;
    r += c->w;
    if (x < c->w) r[0] = d;
    if (x + 1 < c->w) r[1] = e;
}

/* the block list: 0x30 V4/V1 by a bit each, 0x31 inter (skip, then V4/V1), 0x32 all V1 */
static int vectors(Cinepak *c, const Strip *s, u32 id, u32 x1, u32 y1, u32 x2, u32 y2, const u8 *p, const u8 *end)
{
    u32 flag = 0, mask = 0;
    for (u32 y = y1; y < y2; y += 4) {
        for (u32 x = x1; x < x2; x += 4) {
            if ((id & 1u) && !(mask >>= 1)) {
                if (p + 4 > end) return 0;
                flag = be32(p), p += 4, mask = 0x80000000u;
            }
            if ((id & 1u) && !(flag & mask)) continue; /* an inter frame's unchanged block */
            if (!(id & 2u) && !(mask >>= 1)) {
                if (p + 4 > end) return 0;
                flag = be32(p), p += 4, mask = 0x80000000u;
            }
            if ((id & 2u) || !(flag & mask)) {
                const u32 *e;
                if (p >= end) return 0;
                e = s->v1[*p++];
                put2(c, x, y, e[0], e[0], e[0], e[0]);
                put2(c, x + 2, y, e[1], e[1], e[1], e[1]);
                put2(c, x, y + 2, e[2], e[2], e[2], e[2]);
                put2(c, x + 2, y + 2, e[3], e[3], e[3], e[3]);
            } else {
                const u32 *a, *b, *d, *f;
                if (p + 4 > end) return 0;
                a = s->v4[p[0]], b = s->v4[p[1]], d = s->v4[p[2]], f = s->v4[p[3]];
                p += 4;
                put2(c, x, y, a[0], a[1], a[2], a[3]);
                put2(c, x + 2, y, b[0], b[1], b[2], b[3]);
                put2(c, x, y + 2, d[0], d[1], d[2], d[3]);
                put2(c, x + 2, y + 2, f[0], f[1], f[2], f[3]);
            }
        }
    }
    return 1;
}

static int strip(Cinepak *c, Strip *s, u32 x1, u32 y1, u32 x2, u32 y2, const u8 *p, const u8 *end)
{
    while (p + 4 <= end) {
        u32 id = p[0], n = be24(p + 1);
        const u8 *body = p + 4, *next = n >= 4 && body + (n - 4) <= end ? body + (n - 4) : end;
        if (id >= 0x20 && id <= 0x27) codebook((id & 2u) ? s->v1 : s->v4, id, body, next);
        else if (id >= 0x30 && id <= 0x32) return vectors(c, s, id, x1, y1, x2, y2, body, next);
        p = next;
    }
    return 1;
}

int cinepak_frame(Cinepak *c, const u8 *data, u32 size)
{
    const u8 *p = data, *end = data + size;
    u32 flags, nstrips, y0 = 0;
    if (size < 10) return 0;
    flags = p[0], nstrips = be16(p + 8);
    if (c->skip < 0) {
        u32 coded = be24(p + 1);
        if (coded && coded != size && size % coded)
            c->skip = size >= 16 && !memcmp(p + 10, "\xfe\x00\x00\x06\x00\x00", 6) ? 6 : 2;
        else
            c->skip = 0;
    }
    p += 10 + (u32)c->skip;
    if (nstrips > MAX_STRIPS) nstrips = MAX_STRIPS;
    for (u32 i = 0; i < nstrips; i++) {
        u32 y1, y2, x1, x2, n;
        if (p + 12 > end) return 0;
        y1 = be16(p + 4), x1 = be16(p + 6), y2 = be16(p + 8), x2 = be16(p + 10);
        if (!y1) y1 = y0, y2 = y0 + be16(p + 8); /* a zero top: the strip follows the one above, the bottom is its height */
        n = be24(p + 1);
        if (n < 12) return 0;
        n -= 12;
        p += 12;
        if (p + n > end) n = (u32)(end - p);
        if (i > 0 && !(flags & 1u)) c->strip[i] = c->strip[i - 1];
        if (!strip(c, &c->strip[i], x1, y1, x2 < c->w ? x2 : c->w, y2 < c->h ? y2 : c->h, p, p + n)) return 0;
        p += n;
        y0 = y2;
    }
    return 1;
}
