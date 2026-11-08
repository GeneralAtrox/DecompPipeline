/* dpimg text: TrueType / OpenType fonts through the vendored stb_truetype, laid out and placed as ImageFont.truetype
 * and ImageDraw.text do (Pillow's _imagingft.c): size is the em in pixels, pen positions are 26.6 fixed point with
 * whole-pixel (hinted) advances plus Pillow's basic-layout kerning, the box is Pillow's (it always holds the pen's start and end and the
 * baseline), anchors are Pillow's two letters (l m r; a m s d t b), and the ink goes on through the coverage mask as
 * ImageDraw's draw_bitmap does (dpimg_fill). FreeType hints and rasterises differently from stb_truetype, so text is
 * close to Pillow's, not equal: the proof (docs/proofs/2026-11-07/dpimg_vs_pillow.py) states the tolerance. A stroke
 * (stroke_width) is the glyph grown by that many pixels with rounded joins, as Pillow's filled FreeType stroke is,
 * from each pixel's distance to the outline (stb_truetype's own distance field skips CFF's cubic curves). */
#define DPIMG_BUILD
#include "dpimg_int.h"

#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define STB_TRUETYPE_IMPLEMENTATION
#include "stb_truetype.h"

#define PIXEL(v) ((((v) + 32) & -64) >> 6) /* 26.6 to whole pixels, as _imagingft.c rounds */

struct dpimg_font {
    stbtt_fontinfo info;
    unsigned char *data;
    float scale, ppem;
    int asc26, desc26; /* FreeType's scaled ascender and descender, 26.6 */
};

dpimg_font *dpimg_font_load(const char *path, float px_size, int index)
{
    FILE *f = fopen(path, "rb");
    dpimg_font *font;
    long n;
    int off, asc, desc, gap;
    if (!f) return NULL;
    fseek(f, 0, SEEK_END);
    n = ftell(f);
    fseek(f, 0, SEEK_SET);
    font = (dpimg_font *)calloc(1, sizeof *font);
    if (font) font->data = (unsigned char *)malloc(n > 0 ? (size_t)n : 1);
    if (!font || !font->data || fread(font->data, 1, (size_t)n, f) != (size_t)n) {
        fclose(f);
        dpimg_font_free(font);
        return NULL;
    }
    fclose(f);
    off = stbtt_GetFontOffsetForIndex(font->data, index);
    if (off < 0 || !stbtt_InitFont(&font->info, font->data, off)) {
        dpimg_font_free(font);
        return NULL;
    }
    font->scale = stbtt_ScaleForMappingEmToPixels(&font->info, px_size);
    stbtt_GetFontVMetrics(&font->info, &asc, &desc, &gap);
    font->ppem = px_size;
    font->asc26 = (int)ceil(asc * font->scale) * 64; /* FreeType's scaled metrics: ascender up, descender down */
    font->desc26 = (int)floor(desc * font->scale) * 64;
    return font;
}

void dpimg_font_free(dpimg_font *f)
{
    if (!f) return;
    free(f->data);
    free(f);
}

void dpimg_font_metrics(const dpimg_font *f, int *ascender, int *descender)
{
    *ascender = PIXEL(f->asc26);
    *descender = PIXEL(f->desc26);
}

static int next_cp(const unsigned char **s)
{
    const unsigned char *p = *s;
    int c = *p++, n = 0;
    if (c >= 0xf0) c &= 7, n = 3;
    else if (c >= 0xe0) c &= 15, n = 2;
    else if (c >= 0xc0) c &= 31, n = 1;
    while (n-- && (*p & 0xc0) == 0x80) c = c << 6 | (*p++ & 63);
    *s = p;
    return c;
}

/* Kerning as Pillow's basic layout adds it (Pillow's Windows wheels have no libraqm): FreeType's 'kern' table pair
 * (not GPOS), grid-fitted (and scaled down below 25 ppem), then added to the pen as PIXEL(delta) 26.6 units. */
static int kern_basic(const dpimg_font *f, int g1, int g2)
{
    double k;
    if (!f->info.kern) return 0;
    k = stbtt__GetGlyphKernInfoAdvance(&f->info, g1, g2) * f->scale * 64.0;
    if (f->ppem < 25) k = k * f->ppem / 25;
    return PIXEL(((int)floor(k + 0.5) + 32) & -64);
}

/* A laid-out line: glyph indices, pen x (26.6), the end pen x, and Pillow's box in pixels, y up from the baseline. */
typedef struct {
    int n, pos, x_min, x_max, y_min, y_max;
    int *glyph, *pen;
} Line;

static int layout(const dpimg_font *f, const char *utf8, Line *L)
{
    const unsigned char *s = (const unsigned char *)utf8;
    size_t len = strlen(utf8);
    int prev = 0, adv, lsb, x0, y0, x1, y1;
    memset(L, 0, sizeof *L);
    L->glyph = (int *)malloc((len + 1) * sizeof(int));
    L->pen = (int *)malloc((len + 1) * sizeof(int));
    if (!L->glyph || !L->pen) return 0;
    while (*s) {
        int g = stbtt_FindGlyphIndex(&f->info, next_cp(&s)), px;
        if (prev && g) L->pos += kern_basic(f, prev, g);
        px = PIXEL(L->pos);
        L->glyph[L->n] = g, L->pen[L->n++] = L->pos;
        stbtt_GetGlyphHMetrics(&f->info, g, &adv, &lsb);
        L->pos += (int)floor(adv * f->scale + 0.5) * 64;
        if (PIXEL(L->pos) > L->x_max) L->x_max = PIXEL(L->pos);
        stbtt_GetGlyphBitmapBox(&f->info, g, f->scale, f->scale, &x0, &y0, &x1, &y1); /* y down */
        if (px + x1 > L->x_max) L->x_max = px + x1;
        if (px + x0 < L->x_min) L->x_min = px + x0;
        if (-y0 > L->y_max) L->y_max = -y0;
        if (-y1 < L->y_min) L->y_min = -y1;
        prev = g;
    }
    return 1;
}

static void line_free(Line *L)
{
    free(L->glyph);
    free(L->pen);
}

/* Pillow's getbbox: left, top, right, bottom from the anchor point, y down, grown by the stroke. */
static void line_bbox(const dpimg_font *f, const Line *L, const char *anchor, int stroke, int bbox[4])
{
    char h = anchor && anchor[0] ? anchor[0] : 'l', v = anchor && anchor[0] && anchor[1] ? anchor[1] : 'a';
    int xa = 0, ya = 0;
    if (L->n) {
        xa = h == 'm' ? PIXEL(L->pos / 2) : h == 'r' ? PIXEL(L->pos) : 0;
        switch (v) {
        case 't': ya = L->y_max; break;
        case 'm': ya = PIXEL((f->asc26 + f->desc26) / 2); break;
        case 's': ya = 0; break;
        case 'b': ya = L->y_min; break;
        case 'd': ya = PIXEL(f->desc26); break;
        default: ya = PIXEL(f->asc26); break; /* 'a' */
        }
    }
    bbox[0] = -xa + L->x_min - stroke;
    bbox[1] = ya - L->y_max - stroke;
    bbox[2] = bbox[0] + L->x_max - L->x_min + 2 * stroke;
    bbox[3] = bbox[1] + L->y_max - L->y_min + 2 * stroke;
}

void dpimg_text_bbox(const dpimg_font *f, const char *utf8, const char *anchor, int stroke, int bbox[4])
{
    Line L;
    if (layout(f, utf8, &L)) line_bbox(f, &L, anchor, stroke, bbox);
    else bbox[0] = bbox[1] = bbox[2] = bbox[3] = 0;
    line_free(&L);
}

int dpimg_text_length(const dpimg_font *f, const char *utf8)
{
    Line L;
    int pos = layout(f, utf8, &L) ? L.pos : 0;
    line_free(&L);
    return pos;
}

/* The glyph's outline as straight segments in pixels (y down, from the pen on the baseline): quadratic (TrueType)
 * and cubic (CFF) curves cut into 16 pieces, every contour closed. Returns the count; *out is malloc'd. */
typedef struct {
    float x0, y0, x1, y1;
} Seg;

static int add_seg(Seg **s, int *n, int *cap, float x0, float y0, float x1, float y1)
{
    if (*n == *cap) {
        Seg *t = (Seg *)realloc(*s, (size_t)(*cap *= 2) * sizeof(Seg));
        if (!t) return 0;
        *s = t;
    }
    (*s)[(*n)++] = (Seg){x0, y0, x1, y1};
    return 1;
}

static int outline(const dpimg_font *f, int g, Seg **out)
{
    stbtt_vertex *v;
    int nv = stbtt_GetGlyphShape(&f->info, g, &v), i, k, n = 0, cap = 64, ok = 1;
    float k_ = f->scale, cx = 0, cy = 0, sx = 0, sy = 0;
    Seg *s = (Seg *)malloc(cap * sizeof(Seg));
    for (i = 0; s && ok && i < nv; i++) {
        float x = v[i].x * k_, y = -v[i].y * k_;
        if (v[i].type == STBTT_vmove) {
            if (i && (cx != sx || cy != sy)) ok = add_seg(&s, &n, &cap, cx, cy, sx, sy);
            sx = x, sy = y;
        } else if (v[i].type == STBTT_vline) {
            ok = add_seg(&s, &n, &cap, cx, cy, x, y);
        } else {
            float ax = v[i].cx * k_, ay = -v[i].cy * k_, bx = v[i].cx1 * k_, by = -v[i].cy1 * k_, px = cx, py = cy;
            for (k = 1; ok && k <= 16; k++) {
                float t = k / 16.0f, u = 1 - t, qx, qy;
                if (v[i].type == STBTT_vcurve)
                    qx = u * u * cx + 2 * u * t * ax + t * t * x, qy = u * u * cy + 2 * u * t * ay + t * t * y;
                else
                    qx = u * u * u * cx + 3 * u * u * t * ax + 3 * u * t * t * bx + t * t * t * x,
                    qy = u * u * u * cy + 3 * u * u * t * ay + 3 * u * t * t * by + t * t * t * y;
                ok = add_seg(&s, &n, &cap, px, py, qx, qy);
                px = qx, py = qy;
            }
        }
        cx = x, cy = y;
    }
    if (s && ok && nv && (cx != sx || cy != sy)) ok = add_seg(&s, &n, &cap, cx, cy, sx, sy);
    stbtt_FreeShape(&f->info, v);
    if (!s || !ok) {
        free(s);
        return 0;
    }
    *out = s;
    return n;
}

/* The glyph grown by `stroke` pixels with round joins (Pillow's filled FreeType stroke): each pixel's coverage from
 * its centre's distance to the outline, inside by the non-zero winding rule, into mask kept at the maximum. */
static void stroked_glyph(const dpimg_font *f, int g, int stroke, dpimg *mask, int px, int py)
{
    Seg *s = NULL;
    int n = outline(f, g, &s), x0, y0, x1, y1, ix, iy, i;
    if (n <= 0) return;
    stbtt_GetGlyphBitmapBox(&f->info, g, f->scale, f->scale, &x0, &y0, &x1, &y1);
    for (iy = y0 - stroke - 1; iy < y1 + stroke + 1; iy++)
        for (ix = x0 - stroke - 1; ix < x1 + stroke + 1; ix++) {
            int mx = px + ix, my = py + iy, wind = 0, v;
            float cx = ix + 0.5f, cy = iy + 0.5f, d2 = 1e30f, cov;
            uint8_t *p;
            if (mx < 0 || my < 0 || mx >= mask->w || my >= mask->h) continue;
            for (i = 0; i < n; i++) {
                const Seg *e = &s[i];
                float dx = e->x1 - e->x0, dy = e->y1 - e->y0, l2 = dx * dx + dy * dy;
                float t = l2 > 0 ? ((cx - e->x0) * dx + (cy - e->y0) * dy) / l2 : 0, qx, qy, side;
                t = t < 0 ? 0 : t > 1 ? 1 : t;
                qx = e->x0 + t * dx - cx, qy = e->y0 + t * dy - cy;
                if (qx * qx + qy * qy < d2) d2 = qx * qx + qy * qy;
                side = dx * (cy - e->y0) - (cx - e->x0) * dy;
                if (e->y0 <= cy) {
                    if (e->y1 > cy && side > 0) wind++;
                } else if (e->y1 <= cy && side < 0) {
                    wind--;
                }
            }
            cov = stroke + 0.5f - (wind ? -sqrtf(d2) : sqrtf(d2));
            v = cov <= 0 ? 0 : cov >= 1 ? 255 : (int)(cov * 255 + 0.5f);
            p = mask->px + ((size_t)my * mask->w + mx) * 4 + 3;
            if (v > *p) *p = (uint8_t)v;
        }
    free(s);
}

/* The coverage of one glyph into mask (alpha channel), its pen at (px, py) in the mask, kept at the maximum. */
static void glyph(const dpimg_font *f, int g, int stroke, dpimg *mask, int px, int py)
{
    int w, h, xo, yo, x, y;
    unsigned char *bm;
    if (stroke > 0) {
        stroked_glyph(f, g, stroke, mask, px, py);
        return;
    }
    bm = stbtt_GetGlyphBitmap(&f->info, f->scale, f->scale, g, &w, &h, &xo, &yo);
    if (!bm) return;
    for (y = 0; y < h; y++)
        for (x = 0; x < w; x++) {
            int mx = px + xo + x, my = py + yo + y;
            uint8_t *p;
            if (mx < 0 || my < 0 || mx >= mask->w || my >= mask->h) continue;
            p = mask->px + ((size_t)my * mask->w + mx) * 4 + 3;
            if (bm[y * w + x] > *p) *p = bm[y * w + x];
        }
    stbtt_FreeBitmap(bm, NULL);
}

/* The coverage mask Pillow's font.render makes (its box, alpha channel), and the pen origin inside it. */
static dpimg *line_mask(const dpimg_font *f, const Line *L, int stroke, int *origin_x, int *origin_y)
{
    int w = L->x_max - L->x_min + 2 * stroke, h = L->y_max - L->y_min + 2 * stroke, i;
    dpimg *mask = dpimg_new(w > 0 ? w : 1, h > 0 ? h : 1, 0);
    if (!mask) return NULL;
    *origin_x = stroke - L->x_min, *origin_y = stroke + L->y_max;
    for (i = 0; i < L->n; i++) glyph(f, L->glyph[i], stroke, mask, *origin_x + PIXEL(L->pen[i]), *origin_y);
    return mask;
}

dpimg *dpimg_text_mask(const dpimg_font *f, const char *utf8, int stroke, int *origin_x, int *origin_y)
{
    Line L;
    dpimg *mask = layout(f, utf8, &L) ? line_mask(f, &L, stroke, origin_x, origin_y) : NULL;
    line_free(&L);
    return mask;
}

void dpimg_text(dpimg *im, const dpimg_font *f, int x, int y, const char *utf8, uint32_t rgba, const char *anchor,
                int stroke)
{
    Line L;
    int bbox[4], ox, oy;
    dpimg *mask = NULL;
    if (layout(f, utf8, &L) && L.n) {
        line_bbox(f, &L, anchor, stroke, bbox);
        mask = line_mask(f, &L, stroke, &ox, &oy);
    }
    line_free(&L);
    if (!mask) return;
    dpimg_fill(im, rgba, mask, 3, x + bbox[0], y + bbox[1]);
    dpimg_free(mask);
}
