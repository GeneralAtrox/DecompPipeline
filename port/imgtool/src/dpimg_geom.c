/* dpimg ellipses and affine transforms, ported from Pillow 13.0.0 (src/libImaging/Draw.c's quarter and ellipse
 * scanners and ellipseNew; Geometry.c's affine_transform, ImagingScaleAffine, ImagingGenericTransform and the nearest,
 * bilinear and bicubic filters; Image.transform's premultiplied RGBA) so the pixels match. Pillow is under the
 * MIT-CMU licence (HPND): Copyright (c) 1997-2011 Secret Labs AB, Copyright (c) 1995-2011 Fredrik Lundh and
 * contributors, Copyright (c) 2010 Jeffrey A. Clark and contributors. */
#define DPIMG_BUILD
#include "dpimg_int.h"

#include <math.h>
#include <stdlib.h>
#include <string.h>

typedef struct {
    int32_t a, b, cx, cy, ex, ey;
    int64_t a2, b2, a2b2;
    int8_t finished;
} quarter_state;

static void quarter_init(quarter_state *s, int32_t a, int32_t b)
{
    if (a < 0 || b < 0) {
        s->finished = 1;
        return;
    }
    s->a = a, s->b = b, s->cx = a, s->cy = b % 2, s->ex = a % 2, s->ey = b;
    s->a2 = (int64_t)a * a, s->b2 = (int64_t)b * b, s->a2b2 = s->a2 * s->b2;
    s->finished = 0;
}

static int64_t quarter_delta(quarter_state *s, int64_t x, int64_t y)
{
    return llabs(s->a2 * y * y + s->b2 * x * x - s->a2b2);
}

static int quarter_next(quarter_state *s, int32_t *rx, int32_t *ry)
{
    if (s->finished) return -1;
    *rx = s->cx, *ry = s->cy;
    if (s->cx == s->ex && s->cy == s->ey) {
        s->finished = 1;
    } else {
        int32_t nx = s->cx, ny = s->cy + 2;
        int64_t nd = quarter_delta(s, nx, ny), d;
        if (nx > 1) {
            d = quarter_delta(s, s->cx - 2, s->cy + 2);
            if (nd > d) nx = s->cx - 2, ny = s->cy + 2, nd = d;
            d = quarter_delta(s, s->cx - 2, s->cy);
            if (nd > d) nx = s->cx - 2, ny = s->cy;
        }
        s->cx = nx, s->cy = ny;
    }
    return 0;
}

typedef struct {
    quarter_state o, i;
    int32_t py, pl, pr, cy[4], cl[4], cr[4];
    int8_t bufcnt, finished, leftmost;
} ellipse_state;

static void ellipse_init(ellipse_state *s, int32_t a, int32_t b, int32_t w)
{
    s->bufcnt = 0;
    s->leftmost = (int8_t)(a % 2);
    quarter_init(&s->o, a, b);
    if (w < 1 || quarter_next(&s->o, &s->pr, &s->py) == -1) {
        s->finished = 1;
    } else {
        s->finished = 0;
        quarter_init(&s->i, a - 2 * (w - 1), b - 2 * (w - 1));
        s->pl = s->leftmost;
    }
}

static int ellipse_next(ellipse_state *s, int32_t *x0, int32_t *y, int32_t *x1)
{
    if (s->bufcnt == 0) {
        int32_t py = s->py, l = s->pl, r = s->pr, cx = 0, cy = 0;
        int nr;
        if (s->finished) return -1;
        while ((nr = quarter_next(&s->o, &cx, &cy)) != -1 && cy <= py) {
        }
        if (nr == -1) s->finished = 1;
        else s->pr = cx, s->py = cy;
        while ((nr = quarter_next(&s->i, &cx, &cy)) != -1 && cy <= py) l = cx;
        s->pl = nr == -1 ? s->leftmost : cx;
        if ((l > 0 || l < r) && py > 0) {
            s->cl[s->bufcnt] = l == 0 ? 2 : l, s->cy[s->bufcnt] = py, s->cr[s->bufcnt] = r;
            ++s->bufcnt;
        }
        if (py > 0) {
            s->cl[s->bufcnt] = -r, s->cy[s->bufcnt] = py, s->cr[s->bufcnt] = -l;
            ++s->bufcnt;
        }
        if (l > 0 || l < r) {
            s->cl[s->bufcnt] = l == 0 ? 2 : l, s->cy[s->bufcnt] = -py, s->cr[s->bufcnt] = r;
            ++s->bufcnt;
        }
        s->cl[s->bufcnt] = -r, s->cy[s->bufcnt] = -py, s->cr[s->bufcnt] = -l;
        ++s->bufcnt;
    }
    --s->bufcnt;
    *x0 = s->cl[s->bufcnt], *y = s->cy[s->bufcnt], *x1 = s->cr[s->bufcnt];
    return 0;
}

static void ellipse(dpimg *im, int x0, int y0, int x1, int y1, uint32_t rgba, int fill, int width)
{
    Canvas c = dpimg__canvas(im, rgba);
    int a = x1 - x0, b = y1 - y0;
    ellipse_state st;
    int32_t X0, Y, X1;
    if (a < 0 || b < 0) return;
    if (fill) width = a + b;
    ellipse_init(&st, a, b, width);
    while (ellipse_next(&st, &X0, &Y, &X1) != -1)
        dpimg__hline(&c, x0 + (X0 + a) / 2, y0 + (Y + b) / 2, x0 + (X1 + a) / 2);
}

/* ImageDraw.ellipse(xy, fill, outline, width): flags 1 = fill, 2 = outline. */
void dpimg_ellipse(dpimg *im, int x0, int y0, int x1, int y1, uint32_t fill, uint32_t outline, int width, int flags)
{
    if (x1 < x0 || y1 < y0) return;
    if (flags & 1) ellipse(im, x0, y0, x1, y1, fill, 1, 0);
    if ((flags & 2) && !((flags & 1) && outline == fill) && width) ellipse(im, x0, y0, x1, y1, outline, 0, width);
}

#define COORD(v) ((v) < 0.0 ? -1 : ((int)(v)))
#define FLOOR(v) ((v) >= 0.0 ? (int)(v) : (int)floor(v))
#define XCLIP(x) (((x) < 0) ? 0 : ((x) < xsize) ? (x) : xsize - 1)
#define YCLIP(y) (((y) < 0) ? 0 : ((y) < ysize) ? (y) : ysize - 1)
#define CUBIC(v, v1, v2, v3, v4, d)                                                                                    \
    {                                                                                                                  \
        double p1 = v2, p2 = -(v1) + (v3), p3 = 2 * ((v1) - (v2)) + (v3) - (v4), p4 = -(v1) + (v2) - (v3) + (v4);     \
        v = p1 + (d) * (p2 + (d) * (p3 + (d) * p4));                                                                   \
    }

static const uint8_t *row(const dpimg *im, int y) { return im->px + (size_t)y * im->w * 4; }

/* Geometry.c's filters for a 32-bit image: 0 where (xin, yin) lies outside, the pixel written otherwise. */
static int sample(uint8_t *out, const dpimg *im, double xin, double yin, int filter)
{
    int xsize = im->w, ysize = im->h, x, y, b, x0, x1, x2, x3;
    double dx, dy, v1, v2, v3, v4;
    const uint8_t *in;
    if (filter == DPIMG_NEAREST) {
        x = COORD(xin), y = COORD(yin);
        if (x < 0 || x >= xsize || y < 0 || y >= ysize) return 0;
        memcpy(out, row(im, y) + x * 4, 4);
        return 1;
    }
    if (xin < 0.0 || xin >= xsize || yin < 0.0 || yin >= ysize) return 0;
    xin -= 0.5, yin -= 0.5;
    x = FLOOR(xin), y = FLOOR(yin);
    dx = xin - x, dy = yin - y;
    if (filter == DPIMG_BILINEAR) {
        x0 = XCLIP(x) * 4, x1 = XCLIP(x + 1) * 4;
        for (b = 0; b < 4; b++) {
            in = row(im, YCLIP(y)) + b;
            v1 = in[x0] + (in[x1] - in[x0]) * dx;
            if (y + 1 >= 0 && y + 1 < ysize) in = row(im, y + 1) + b, v2 = in[x0] + (in[x1] - in[x0]) * dx;
            else v2 = v1;
            v1 = v1 + (v2 - v1) * dy;
            out[b] = (uint8_t)v1;
        }
        return 1;
    }
    x--, y--; /* bicubic */
    x0 = XCLIP(x) * 4, x1 = XCLIP(x + 1) * 4, x2 = XCLIP(x + 2) * 4, x3 = XCLIP(x + 3) * 4;
    for (b = 0; b < 4; b++) {
        in = row(im, YCLIP(y)) + b;
        CUBIC(v1, in[x0], in[x1], in[x2], in[x3], dx);
        if (y + 1 >= 0 && y + 1 < ysize) { in = row(im, y + 1) + b; CUBIC(v2, in[x0], in[x1], in[x2], in[x3], dx); }
        else v2 = v1;
        if (y + 2 >= 0 && y + 2 < ysize) { in = row(im, y + 2) + b; CUBIC(v3, in[x0], in[x1], in[x2], in[x3], dx); }
        else v3 = v2;
        if (y + 3 >= 0 && y + 3 < ysize) { in = row(im, y + 3) + b; CUBIC(v4, in[x0], in[x1], in[x2], in[x3], dx); }
        else v4 = v3;
        CUBIC(v1, v1, v2, v3, v4, dy);
        out[b] = v1 <= 0.0 ? 0 : v1 >= 255.0 ? 255 : (uint8_t)v1;
    }
    return 1;
}

#define FIX(v) FLOOR((v) * 65536.0 + 0.5)
static int fits_fixed(const double a[6], int x, int y)
{
    return fabs(x * a[0] + y * a[1] + a[2]) < 32768.0 && fabs(x * a[3] + y * a[4] + a[5]) < 32768.0;
}

/* Image.transform(size, AFFINE, a, filter, fillcolor): a maps an output pixel's centre to the input. */
dpimg *dpimg_transform(const dpimg *im, int w, int h, const double a[6], int filter, uint32_t fill)
{
    dpimg *src = NULL, *out;
    int x, y;
    uint8_t fillpx[4] = {(uint8_t)(fill >> 24), (uint8_t)(fill >> 16), (uint8_t)(fill >> 8), (uint8_t)fill};
    if (w <= 0 || h <= 0 || filter < 0 || filter > DPIMG_BICUBIC || filter == DPIMG_LANCZOS) return NULL;
    out = dpimg_new(w, h, fill);
    if (!out) return NULL;
    if (filter == DPIMG_NEAREST && a[1] == 0 && a[3] == 0) { /* ImagingScaleAffine */
        int *xintab = (int *)calloc(w, sizeof(int)), xmin = w, xmax = 0;
        double xo = a[2] + a[0] * 0.5, yo = a[5] + a[4] * 0.5;
        if (!xintab) {
            dpimg_free(out);
            return NULL;
        }
        for (x = 0; x < w; x++, xo += a[0]) {
            int xin = COORD(xo);
            if (xin >= 0 && xin < im->w) {
                xmax = x + 1;
                if (x < xmin) xmin = x;
                xintab[x] = xin;
            }
        }
        for (y = 0; y < h; y++, yo += a[4]) {
            int yi = COORD(yo);
            uint8_t *o = out->px + (size_t)y * w * 4;
            for (x = 0; x < w; x++) memcpy(o + x * 4, fillpx, 4);
            if (yi >= 0 && yi < im->h)
                for (x = xmin; x < xmax; x++) memcpy(o + x * 4, row(im, yi) + xintab[x] * 4, 4);
        }
        free(xintab);
        return out;
    }
    if (filter == DPIMG_NEAREST) { /* Pillow's affine_fixed (16.16 fixed point), or its float loop when too big */
        if (fits_fixed(a, 0, 0) && fits_fixed(a, w, h) && fits_fixed(a, 0, h) && fits_fixed(a, w, 0)) {
            int a0 = FIX(a[0]), a1 = FIX(a[1]), a3 = FIX(a[3]), a4 = FIX(a[4]);
            int xo = FIX(a[2] + a[0] * 0.5 + a[1] * 0.5), yo = FIX(a[5] + a[3] * 0.5 + a[4] * 0.5);
            for (y = 0; y < h; y++, xo += a1, yo += a4) {
                int xx = xo, yy = yo;
                uint8_t *o = out->px + (size_t)y * w * 4;
                for (x = 0; x < w; x++, xx += a0, yy += a3) {
                    int xin = xx >> 16, yin = yy >> 16;
                    if (xin >= 0 && xin < im->w && yin >= 0 && yin < im->h) memcpy(o + x * 4, row(im, yin) + xin * 4, 4);
                }
            }
        } else {
            double xo = a[2] + a[1] * 0.5 + a[0] * 0.5, yo = a[5] + a[4] * 0.5 + a[3] * 0.5;
            for (y = 0; y < h; y++, xo += a[1], yo += a[4]) {
                double xx = xo, yy = yo;
                uint8_t *o = out->px + (size_t)y * w * 4;
                for (x = 0; x < w; x++, xx += a[0], yy += a[3]) {
                    int xin = COORD(xx), yin = COORD(yy);
                    if (xin >= 0 && xin < im->w && yin >= 0 && yin < im->h) memcpy(o + x * 4, row(im, yin) + xin * 4, 4);
                }
            }
        }
        return out;
    }
    { /* Image.transform premultiplies RGBA for every filter but NEAREST */
        src = dpimg_copy(im);
        if (!src) {
            dpimg_free(out);
            return NULL;
        }
        dpimg_premultiply(src);
        im = src;
    }
    for (y = 0; y < h; y++)
        for (x = 0; x < w; x++) {
            double xin = x + 0.5, yin = y + 0.5;
            uint8_t *o = out->px + ((size_t)y * w + x) * 4;
            if (!sample(o, im, a[0] * xin + a[1] * yin + a[2], a[3] * xin + a[4] * yin + a[5], filter))
                memcpy(o, fillpx, 4);
        }
    if (src) {
        dpimg_unpremultiply(out);
        dpimg_free(src);
    }
    return out;
}
