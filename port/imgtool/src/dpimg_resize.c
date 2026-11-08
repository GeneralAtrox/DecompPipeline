/* dpimg resize: Pillow's Image.resize for an RGBA image, step for step, so the output is the same bytes.
 *
 * Pillow (src/libImaging/Resample.c, Image.py resize): an RGBA image is premultiplied (RGBa) for every filter but
 * NEAREST, resampled horizontally into an 8-bit image, then vertically, and un-premultiplied. Each pass weights the
 * source pixels under the filter, scaled by the reduction factor; the weights are normalised in double precision and
 * then rounded to 22-bit fixed point, which is where the exactness comes from. */
#define DPIMG_BUILD
#include "dpimg.h"

#include <math.h>
#include <stdlib.h>
#include <string.h>

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

#define PRECISION_BITS (32 - 8 - 2)

typedef double (*filter_fn)(double);

static double box_f(double x) { return (x > -0.5 && x <= 0.5) ? 1.0 : 0.0; }
static double bilinear_f(double x)
{
    if (x < 0.0) x = -x;
    return x < 1.0 ? 1.0 - x : 0.0;
}
static double hamming_f(double x)
{
    if (x < 0.0) x = -x;
    if (x == 0.0) return 1.0;
    if (x >= 1.0) return 0.0;
    x = x * M_PI;
    return sin(x) / x * (0.54f + 0.46f * cos(x)); /* Pillow's float constants */
}
static double bicubic_f(double x)
{
    const double a = -0.5;
    if (x < 0.0) x = -x;
    if (x < 1.0) return ((a + 2.0) * x - (a + 3.0)) * x * x + 1;
    if (x < 2.0) return (((x - 5) * x + 8) * x - 4) * a;
    return 0.0;
}
static double sinc_f(double x)
{
    if (x == 0.0) return 1.0;
    x = x * M_PI;
    return sin(x) / x;
}
static double lanczos_f(double x) { return (-3.0 <= x && x < 3.0) ? sinc_f(x) * sinc_f(x / 3) : 0.0; }

/* Pillow's precompute_coeffs then normalize_coeffs_8bpc: per output pixel, the first source pixel, the count, and
 * ksize fixed-point weights. Returns ksize, or 0 when out of memory. */
static int coeffs(int in_size, float in0, float in1, int out_size, filter_fn f, double fsupport, int **bounds_out,
                  int **kk_out)
{
    double scale = (double)(in1 - in0) / out_size, filterscale = scale < 1.0 ? 1.0 : scale;
    double support = fsupport * filterscale, center, ww, ss;
    int ksize = (int)ceil(support) * 2 + 1, xx, x, xmin, xmax;
    double *pre = (double *)malloc((size_t)out_size * ksize * sizeof(double));
    int *kk = (int *)malloc((size_t)out_size * ksize * sizeof(int));
    int *bounds = (int *)malloc((size_t)out_size * 2 * sizeof(int));
    if (!pre || !kk || !bounds) {
        free(pre), free(kk), free(bounds);
        return 0;
    }
    for (xx = 0; xx < out_size; xx++) {
        double *k = pre + (size_t)xx * ksize;
        center = in0 + (xx + 0.5) * scale;
        ww = 0.0;
        ss = 1.0 / filterscale;
        xmin = (int)(center - support + 0.5);
        if (xmin < 0) xmin = 0;
        xmax = (int)(center + support + 0.5);
        if (xmax > in_size) xmax = in_size;
        xmax -= xmin;
        for (x = 0; x < xmax; x++) {
            double w = f((x + xmin - center + 0.5) * ss);
            k[x] = w;
            ww += w;
        }
        for (x = 0; x < xmax; x++)
            if (ww != 0.0) k[x] /= ww;
        for (; x < ksize; x++) k[x] = 0;
        bounds[xx * 2] = xmin;
        bounds[xx * 2 + 1] = xmax;
    }
    for (x = 0; x < out_size * ksize; x++)
        kk[x] = pre[x] < 0 ? (int)(-0.5 + pre[x] * (1 << PRECISION_BITS)) : (int)(0.5 + pre[x] * (1 << PRECISION_BITS));
    free(pre);
    *bounds_out = bounds;
    *kk_out = kk;
    return ksize;
}

static uint8_t clip8(int in)
{
    if (in >= (1 << PRECISION_BITS << 8)) return 255;
    if (in <= 0) return 0;
    return (uint8_t)(in >> PRECISION_BITS);
}

/* out (out_w x rows) from in's rows [row0, row0 + rows) */
static void pass_h(uint8_t *out, int out_w, const dpimg *in, int row0, int rows, int ksize, const int *bounds,
                   const int *kk)
{
    int y, xx, x, c;
    for (y = 0; y < rows; y++) {
        const uint8_t *src = in->px + (size_t)(row0 + y) * in->w * 4;
        for (xx = 0; xx < out_w; xx++) {
            int xmin = bounds[xx * 2], xmax = bounds[xx * 2 + 1];
            const int *k = kk + (size_t)xx * ksize;
            for (c = 0; c < 4; c++) {
                int ss = 1 << (PRECISION_BITS - 1);
                for (x = 0; x < xmax; x++) ss += src[(x + xmin) * 4 + c] * k[x];
                out[((size_t)y * out_w + xx) * 4 + c] = clip8(ss);
            }
        }
    }
}

static void pass_v(uint8_t *out, int out_h, const uint8_t *in, int w, int ksize, const int *bounds, const int *kk)
{
    int yy, x, y, c;
    for (yy = 0; yy < out_h; yy++) {
        int ymin = bounds[yy * 2], ymax = bounds[yy * 2 + 1];
        const int *k = kk + (size_t)yy * ksize;
        for (x = 0; x < w; x++)
            for (c = 0; c < 4; c++) {
                int ss = 1 << (PRECISION_BITS - 1);
                for (y = 0; y < ymax; y++) ss += in[((size_t)(y + ymin) * w + x) * 4 + c] * k[y];
                out[((size_t)yy * w + x) * 4 + c] = clip8(ss);
            }
    }
}

dpimg *dpimg_resize(const dpimg *im, int w, int h, int filter)
{
    filter_fn f;
    double support;
    int *bh = NULL, *kh = NULL, *bv = NULL, *kv = NULL, ksh, ksv, first, last, i;
    dpimg *src, *out;
    uint8_t *tmp = NULL;
    if (!im || w <= 0 || h <= 0) return NULL;
    if (w == im->w && h == im->h) return dpimg_copy(im);
    switch (filter) {
    case DPIMG_NEAREST: { /* _imaging.c _resize: an affine scale, nearest neighbour */
        double m[6] = {(double)im->w / w, 0, 0, 0, (double)im->h / h, 0};
        return dpimg_transform(im, w, h, m, DPIMG_NEAREST, 0);
    }
    case DPIMG_BOX: f = box_f, support = 0.5; break;
    case DPIMG_BILINEAR: f = bilinear_f, support = 1.0; break;
    case DPIMG_HAMMING: f = hamming_f, support = 1.0; break;
    case DPIMG_BICUBIC: f = bicubic_f, support = 2.0; break;
    case DPIMG_LANCZOS: f = lanczos_f, support = 3.0; break;
    default: return NULL;
    }
    src = dpimg_copy(im);
    out = dpimg_new(w, h, 0);
    ksh = coeffs(im->w, 0, im->w, w, f, support, &bh, &kh);
    ksv = coeffs(im->h, 0, im->h, h, f, support, &bv, &kv);
    if (!src || !out || !ksh || !ksv) goto fail;
    dpimg_premultiply(src);
    first = bv[0];
    last = bv[h * 2 - 2] + bv[h * 2 - 1];
    if (w != im->w) { /* horizontal pass over only the rows the vertical pass reads */
        for (i = 0; i < h; i++) bv[i * 2] -= first;
        tmp = (uint8_t *)malloc((size_t)w * (last - first) * 4 + 1);
        if (!tmp) goto fail;
        pass_h(tmp, w, src, first, last - first, ksh, bh, kh);
        if (h != im->h) pass_v(out->px, h, tmp, w, ksv, bv, kv);
        else memcpy(out->px, tmp, (size_t)w * h * 4);
    } else {
        pass_v(out->px, h, src->px, w, ksv, bv, kv);
    }
    dpimg_unpremultiply(out);
    free(tmp), free(bh), free(kh), free(bv), free(kv);
    dpimg_free(src);
    return out;
fail:
    free(tmp), free(bh), free(kh), free(bv), free(kv);
    dpimg_free(src);
    dpimg_free(out);
    return NULL;
}
