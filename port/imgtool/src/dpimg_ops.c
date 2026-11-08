/* dpimg pixel operations, each as Pillow does it (src/libImaging: Crop, Geometry's transposes, Paste.c,
 * AlphaComposite.c, Chops.c difference, Convert.c's premultiply and L24 grey), so the bytes come out the same. */
#define DPIMG_BUILD
#include "dpimg.h"

#include <stdlib.h>
#include <string.h>

#define SHIFTFORDIV255(a) ((((a) >> 8) + (a)) >> 8)
#define DIV255(a, tmp) (tmp = (a) + 128, SHIFTFORDIV255(tmp))
#define BLEND(mask, in1, in2, tmp) DIV255((in1) * (255 - (mask)) + (in2) * (mask), tmp)
#define MULDIV255(a, b, tmp) (tmp = (a) * (b) + 128, SHIFTFORDIV255(tmp))
#define CLIP8(v) ((v) <= 0 ? 0 : (v) < 256 ? (v) : 255)

static uint8_t *at(const dpimg *im, int x, int y) { return im->px + ((size_t)y * im->w + x) * 4; }

dpimg *dpimg_crop(const dpimg *im, int x0, int y0, int x1, int y1)
{
    dpimg *out;
    int y, sx0, sx1;
    if (x1 < x0) x1 = x0;
    if (y1 < y0) y1 = y0;
    out = dpimg_new(x1 - x0, y1 - y0, 0);
    if (!out) return NULL;
    sx0 = x0 < 0 ? 0 : x0;
    sx1 = x1 > im->w ? im->w : x1;
    for (y = y0 < 0 ? 0 : y0; y < y1 && y < im->h; y++)
        if (sx1 > sx0) memcpy(at(out, sx0 - x0, y - y0), at(im, sx0, y), (size_t)(sx1 - sx0) * 4);
    return out;
}

dpimg *dpimg_transpose(const dpimg *im, int op)
{
    int W = im->w, H = im->h, swap = op == DPIMG_ROT90 || op == DPIMG_ROT270 || op == DPIMG_TRANSPOSE ||
                                      op == DPIMG_TRANSVERSE;
    dpimg *out = dpimg_new(swap ? H : W, swap ? W : H, 0);
    int x, y, sx = 0, sy = 0;
    if (!out) return NULL;
    for (y = 0; y < out->h; y++)
        for (x = 0; x < out->w; x++) {
            switch (op) {
            case DPIMG_FLIP_LR: sx = W - 1 - x, sy = y; break;
            case DPIMG_FLIP_TB: sx = x, sy = H - 1 - y; break;
            case DPIMG_ROT90: sx = W - 1 - y, sy = x; break;
            case DPIMG_ROT180: sx = W - 1 - x, sy = H - 1 - y; break;
            case DPIMG_ROT270: sx = y, sy = H - 1 - x; break;
            case DPIMG_TRANSPOSE: sx = y, sy = x; break;
            case DPIMG_TRANSVERSE: sx = W - 1 - y, sy = H - 1 - x; break;
            default: dpimg_free(out); return NULL;
            }
            memcpy(at(out, x, y), at(im, sx, sy), 4);
        }
    return out;
}

/* The part of a w x h source placed at (x, y) that lies in dst: source origin (sx, sy) and size; 0 when none. */
static int clip(const dpimg *dst, int w, int h, int *x, int *y, int *sx, int *sy, int *cw, int *ch)
{
    *sx = *x < 0 ? -*x : 0;
    *sy = *y < 0 ? -*y : 0;
    *cw = w - *sx;
    *ch = h - *sy;
    if (*x + *sx + *cw > dst->w) *cw = dst->w - (*x + *sx);
    if (*y + *sy + *ch > dst->h) *ch = dst->h - (*y + *sy);
    *x += *sx;
    *y += *sy;
    return *cw > 0 && *ch > 0;
}

void dpimg_paste(dpimg *dst, const dpimg *src, int x, int y, const dpimg *mask, int channel)
{
    int sx, sy, cw, ch, i, j, c;
    unsigned tmp;
    if (!clip(dst, src->w, src->h, &x, &y, &sx, &sy, &cw, &ch)) return;
    for (j = 0; j < ch; j++) {
        uint8_t *o = at(dst, x, y + j);
        const uint8_t *s = at(src, sx, sy + j), *m = mask ? at(mask, sx, sy + j) + channel : NULL;
        if (!m) {
            memmove(o, s, (size_t)cw * 4);
            continue;
        }
        for (i = 0; i < cw; i++, o += 4, s += 4, m += 4) /* Paste.c paste_mask_L / paste_mask_RGBA */
            for (c = 0; c < 4; c++) o[c] = (uint8_t)BLEND(*m, o[c], s[c], tmp);
    }
}

void dpimg_fill(dpimg *dst, uint32_t rgba, const dpimg *mask, int channel, int x, int y)
{
    uint8_t ink[4] = {(uint8_t)(rgba >> 24), (uint8_t)(rgba >> 16), (uint8_t)(rgba >> 8), (uint8_t)rgba};
    int sx, sy, cw, ch, i, j, c;
    unsigned tmp;
    if (!mask) {
        mask = NULL;
        sx = sy = 0, cw = dst->w, ch = dst->h, x = y = 0;
    } else if (!clip(dst, mask->w, mask->h, &x, &y, &sx, &sy, &cw, &ch)) {
        return;
    }
    for (j = 0; j < ch; j++) {
        uint8_t *o = at(dst, x, y + j);
        const uint8_t *m = mask ? at(mask, sx, sy + j) + channel : NULL;
        for (i = 0; i < cw; i++, o += 4) {
            if (!m) {
                memcpy(o, ink, 4);
                continue;
            }
            /* Paste.c fill_mask_L on an RGBA image: where the image is fully transparent a colour channel takes
             * the whole ink; the alpha channel always blends by the mask */
            for (c = 0; c < 4; c++) {
                unsigned cm = *m;
                if (c != 3 && cm != 0) cm = 255 - (255 - cm) * (1 - (255 - o[3]) / 255);
                o[c] = (uint8_t)BLEND(cm, o[c], ink[c], tmp);
            }
            m += 4;
        }
    }
}

void dpimg_alpha_composite(dpimg *dst, const dpimg *src, int x, int y)
{
    int sx, sy, cw, ch, i, j;
    if (!clip(dst, src->w, src->h, &x, &y, &sx, &sy, &cw, &ch)) return;
    for (j = 0; j < ch; j++) {
        uint8_t *d = at(dst, x, y + j);
        const uint8_t *s = at(src, sx, sy + j);
        for (i = 0; i < cw; i++, d += 4, s += 4) { /* AlphaComposite.c, 7 bits of extra precision */
            uint32_t blend, outa255, coef1, coef2;
            if (s[3] == 0) continue;
            blend = (uint32_t)d[3] * (255 - s[3]);
            outa255 = (uint32_t)s[3] * 255 + blend;
            coef1 = (uint32_t)s[3] * 255 * 255 * (1 << 7) / outa255;
            coef2 = 255 * (1 << 7) - coef1;
            d[0] = (uint8_t)(SHIFTFORDIV255(s[0] * coef1 + d[0] * coef2 + (0x80 << 7)) >> 7);
            d[1] = (uint8_t)(SHIFTFORDIV255(s[1] * coef1 + d[1] * coef2 + (0x80 << 7)) >> 7);
            d[2] = (uint8_t)(SHIFTFORDIV255(s[2] * coef1 + d[2] * coef2 + (0x80 << 7)) >> 7);
            d[3] = (uint8_t)SHIFTFORDIV255(outa255 + 0x80);
        }
    }
}

dpimg *dpimg_difference(const dpimg *a, const dpimg *b)
{
    int w = a->w < b->w ? a->w : b->w, h = a->h < b->h ? a->h : b->h, x, y, c;
    dpimg *out = dpimg_new(w, h, 0);
    if (!out) return NULL;
    for (y = 0; y < h; y++)
        for (x = 0; x < w; x++)
            for (c = 0; c < 4; c++) {
                int d = at(a, x, y)[c] - at(b, x, y)[c];
                at(out, x, y)[c] = (uint8_t)(d < 0 ? -d : d);
            }
    return out;
}

long long dpimg_compare(const dpimg *a, const dpimg *b, int tolerance, dpimg_cmp *out)
{
    dpimg_cmp r;
    int x, y, c;
    memset(&r, 0, sizeof r);
    if (a->w != b->w || a->h != b->h) {
        long long na = (long long)a->w * a->h, nb = (long long)b->w * b->h;
        r.differing = na > nb ? na : nb;
        r.max_delta = 255;
        r.bbox[2] = a->w > b->w ? a->w : b->w, r.bbox[3] = a->h > b->h ? a->h : b->h;
        if (out) *out = r;
        return r.differing;
    }
    r.bbox[0] = a->w, r.bbox[1] = a->h;
    for (y = 0; y < a->h; y++)
        for (x = 0; x < a->w; x++) {
            int worst = 0;
            for (c = 0; c < 4; c++) {
                int d = at(a, x, y)[c] - at(b, x, y)[c];
                if (d < 0) d = -d;
                if (d > worst) worst = d;
            }
            if (worst > r.max_delta) r.max_delta = worst;
            if (worst <= tolerance) continue;
            r.differing++;
            if (x < r.bbox[0]) r.bbox[0] = x;
            if (y < r.bbox[1]) r.bbox[1] = y;
            if (x + 1 > r.bbox[2]) r.bbox[2] = x + 1;
            if (y + 1 > r.bbox[3]) r.bbox[3] = y + 1;
        }
    if (!r.differing) r.bbox[0] = r.bbox[1] = 0;
    if (out) *out = r;
    return r.differing;
}

void dpimg_premultiply(dpimg *im)
{
    size_t i, n = (size_t)im->w * im->h;
    unsigned tmp;
    for (i = 0; i < n; i++) {
        uint8_t *p = im->px + i * 4;
        p[0] = (uint8_t)MULDIV255(p[0], p[3], tmp);
        p[1] = (uint8_t)MULDIV255(p[1], p[3], tmp);
        p[2] = (uint8_t)MULDIV255(p[2], p[3], tmp);
    }
}

void dpimg_unpremultiply(dpimg *im)
{
    size_t i, n = (size_t)im->w * im->h;
    for (i = 0; i < n; i++) {
        uint8_t *p = im->px + i * 4;
        int a = p[3];
        if (a == 255 || a == 0) continue;
        p[0] = (uint8_t)CLIP8((255 * p[0]) / a);
        p[1] = (uint8_t)CLIP8((255 * p[1]) / a);
        p[2] = (uint8_t)CLIP8((255 * p[2]) / a);
    }
}

void dpimg_grey(dpimg *im)
{
    size_t i, n = (size_t)im->w * im->h;
    for (i = 0; i < n; i++) { /* Convert.c rgb2l: L24, ITU-R 601-2 */
        uint8_t *p = im->px + i * 4;
        uint8_t l = (uint8_t)((p[0] * 19595 + p[1] * 38470 + p[2] * 7471 + 0x8000) >> 16);
        p[0] = p[1] = p[2] = l;
        p[3] = 255;
    }
}

int dpimg_bbox(const dpimg *im, int channel, int bbox[4])
{
    int x, y, found = 0;
    bbox[0] = im->w, bbox[1] = im->h, bbox[2] = bbox[3] = 0;
    for (y = 0; y < im->h; y++)
        for (x = 0; x < im->w; x++) {
            const uint8_t *p = at(im, x, y);
            if (channel >= 0 ? !p[channel] : !(p[0] | p[1] | p[2] | p[3])) continue;
            found = 1;
            if (x < bbox[0]) bbox[0] = x;
            if (y < bbox[1]) bbox[1] = y;
            if (x + 1 > bbox[2]) bbox[2] = x + 1;
            if (y + 1 > bbox[3]) bbox[3] = y + 1;
        }
    if (!found) bbox[0] = bbox[1] = 0;
    return found;
}

dpimg *dpimg_contact_sheet(const dpimg *const *ims, int n, int cols, int cell_w, int cell_h, int pad, uint32_t bg,
                           int filter)
{
    int rows, i;
    dpimg *sheet;
    if (n <= 0 || cols <= 0 || cell_w <= 0 || cell_h <= 0) return NULL;
    if (cols > n) cols = n;
    rows = (n + cols - 1) / cols;
    sheet = dpimg_new(cols * (cell_w + pad) + pad, rows * (cell_h + pad) + pad, bg);
    if (!sheet) return NULL;
    for (i = 0; i < n; i++) {
        const dpimg *im = ims[i];
        dpimg *fit = NULL;
        int w = im->w, h = im->h, x, y;
        if (w > cell_w || h > cell_h) { /* shrink to fit, aspect kept; never enlarged */
            double s = (double)cell_w / w < (double)cell_h / h ? (double)cell_w / w : (double)cell_h / h;
            w = (int)(w * s + 0.5), h = (int)(h * s + 0.5);
            if (w < 1) w = 1;
            if (h < 1) h = 1;
            fit = dpimg_resize(im, w, h, filter);
            if (!fit) {
                dpimg_free(sheet);
                return NULL;
            }
        }
        x = pad + (i % cols) * (cell_w + pad) + (cell_w - w) / 2;
        y = pad + (i / cols) * (cell_h + pad) + (cell_h - h) / 2;
        dpimg_alpha_composite(sheet, fit ? fit : im, x, y);
        dpimg_free(fit);
    }
    return sheet;
}
