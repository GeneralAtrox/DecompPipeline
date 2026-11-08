/* dpimg: DecompPipeline's small image library (docs/IMGTOOL.md), so nothing we ship or author needs Pillow. Every
 * image is 8-bit RGBA, rows top to bottom, no padding: px[(y * w + x) * 4 + c], c = R, G, B, A. A grey ("L") image is
 * RGBA with R = G = B and A = 255, which every operation below treats as Pillow treats an L image. Each operation
 * gives Pillow 13's bytes on the same input, text excepted (close, not equal: see dpimg_text.c), as
 * docs/proofs/2026-11-07/dpimg_vs_pillow.py shows. Colours are 0xRRGGBBAA. */
#ifndef DPIMG_H
#define DPIMG_H

#include <stddef.h>
#include <stdint.h>

#if defined(_WIN32) && defined(DPIMG_BUILD)
#define DPIMG_API __declspec(dllexport)
#elif defined(_WIN32)
#define DPIMG_API __declspec(dllimport)
#else
#define DPIMG_API
#endif

#ifdef __cplusplus
extern "C" {
#endif

typedef struct dpimg {
    int w, h;
    uint8_t *px; /* w * h * 4 bytes, owned by the image */
} dpimg;

/* ---- images and files (dpimg_io.c) ---- */
DPIMG_API dpimg *dpimg_new(int w, int h, uint32_t rgba);        /* NULL when out of memory */
DPIMG_API dpimg *dpimg_from_rgba(int w, int h, const void *px); /* a copy of w * h RGBA pixels */
DPIMG_API dpimg *dpimg_copy(const dpimg *im);
DPIMG_API void dpimg_free(dpimg *im);
DPIMG_API dpimg *dpimg_load(const char *path); /* PNG (8-bit), BMP, TGA, JPEG, GIF first frame, PSD, PNM */
DPIMG_API dpimg *dpimg_load_mem(const void *data, int size);
DPIMG_API int dpimg_save(const dpimg *im, const char *path); /* by extension .png .bmp .tga; 0 on success */
DPIMG_API int dpimg_save_as(const dpimg *im, const char *path, int channels); /* 1 (grey, from R), 3 or 4 */
DPIMG_API const char *dpimg_error(void);                     /* why the last load or save failed */

/* ---- pixels (dpimg_ops.c) ---- */
DPIMG_API dpimg *dpimg_crop(const dpimg *im, int x0, int y0, int x1, int y1); /* outside: transparent black */
enum { DPIMG_FLIP_LR, DPIMG_FLIP_TB, DPIMG_ROT90, DPIMG_ROT180, DPIMG_ROT270, DPIMG_TRANSPOSE, DPIMG_TRANSVERSE };
DPIMG_API dpimg *dpimg_transpose(const dpimg *im, int op); /* Image.transpose (Pillow's numbering) */
/* Image.paste(src, (x, y), mask): mask NULL copies; else mask's channel (3: its alpha, 0: an L mask) blends. */
DPIMG_API void dpimg_paste(dpimg *dst, const dpimg *src, int x, int y, const dpimg *mask, int channel);
/* Image.paste(colour, (x, y), mask), and ImageDraw's text ink: mask NULL fills the whole image. */
DPIMG_API void dpimg_fill(dpimg *dst, uint32_t rgba, const dpimg *mask, int channel, int x, int y);
DPIMG_API void dpimg_alpha_composite(dpimg *dst, const dpimg *src, int x, int y); /* Image.alpha_composite */
DPIMG_API dpimg *dpimg_difference(const dpimg *a, const dpimg *b); /* ImageChops.difference, alpha too */
typedef struct dpimg_cmp {
    long long differing; /* pixels with a channel more than the tolerance apart (all of them when sizes differ) */
    int max_delta;       /* the largest difference of any channel */
    int bbox[4];         /* x0, y0, x1, y1 (exclusive) round the differing pixels; zeros when none */
} dpimg_cmp;
DPIMG_API long long dpimg_compare(const dpimg *a, const dpimg *b, int tolerance, dpimg_cmp *out);
DPIMG_API void dpimg_premultiply(dpimg *im);   /* Convert.c RGBA -> RGBa */
DPIMG_API void dpimg_unpremultiply(dpimg *im); /* RGBa -> RGBA */
DPIMG_API void dpimg_grey(dpimg *im);          /* convert("L"): ITU-R 601-2, as Pillow's L24 */
/* Image.getbbox(): channel -1 any non-zero channel, else that channel non-zero (3 = alpha). 0 when empty. */
DPIMG_API int dpimg_bbox(const dpimg *im, int channel, int bbox[4]);
/* n images in rows of cols, each shrunk to fit cell_w x cell_h (aspect kept, never enlarged) with dpimg_resize's
 * filter, centred, composited over bg, pad pixels between cells and round the edge. */
DPIMG_API dpimg *dpimg_contact_sheet(const dpimg *const *ims, int n, int cols, int cell_w, int cell_h, int pad,
                                     uint32_t bg, int filter);

/* ---- resize and transform (dpimg_resize.c, dpimg_geom.c) ---- */
enum { DPIMG_NEAREST, DPIMG_LANCZOS, DPIMG_BILINEAR, DPIMG_BICUBIC, DPIMG_BOX, DPIMG_HAMMING }; /* Pillow's numbers */
DPIMG_API dpimg *dpimg_resize(const dpimg *im, int w, int h, int filter); /* Image.resize (Pillow's default BICUBIC) */
/* Image.transform(size, AFFINE, a, filter, fillcolor): (a0 x + a1 y + a2, a3 x + a4 y + a5) of an output pixel's
 * centre is where it samples the input; NEAREST, BILINEAR or BICUBIC. */
DPIMG_API dpimg *dpimg_transform(const dpimg *im, int w, int h, const double a[6], int filter, uint32_t fill);

/* ---- drawing (dpimg_draw.c, dpimg_geom.c): ImageDraw; flags 1 = fill, 2 = outline ---- */
DPIMG_API void dpimg_rect(dpimg *im, int x0, int y0, int x1, int y1, uint32_t fill, uint32_t outline, int width,
                          int flags); /* corners inclusive */
DPIMG_API void dpimg_ellipse(dpimg *im, int x0, int y0, int x1, int y1, uint32_t fill, uint32_t outline, int width,
                             int flags);
DPIMG_API void dpimg_polygon(dpimg *im, const int *xy, int n, uint32_t fill, uint32_t outline, int width, int flags);
DPIMG_API void dpimg_line(dpimg *im, const int *xy, int n, uint32_t rgba, int width); /* n points, x y pairs */

/* ---- text (dpimg_text.c): ImageFont.truetype and ImageDraw.text, through stb_truetype ---- */
typedef struct dpimg_font dpimg_font;
DPIMG_API dpimg_font *dpimg_font_load(const char *path, float px_size, int index); /* NULL when unreadable */
DPIMG_API void dpimg_font_free(dpimg_font *f);
DPIMG_API void dpimg_font_metrics(const dpimg_font *f, int *ascender, int *descender); /* pixels, y up */
/* font.getbbox(text, anchor, stroke_width): the ink box relative to the anchor point; anchor NULL is "la". */
DPIMG_API void dpimg_text_bbox(const dpimg_font *f, const char *utf8, const char *anchor, int stroke, int bbox[4]);
/* The pen's travel along the line in 26.6 fixed point (Pillow's getlength times 64). */
DPIMG_API int dpimg_text_length(const dpimg_font *f, const char *utf8);
/* The line's coverage as an image's alpha (font.getmask), and where its pen origin falls in it. */
DPIMG_API dpimg *dpimg_text_mask(const dpimg_font *f, const char *utf8, int stroke, int *origin_x, int *origin_y);
DPIMG_API void dpimg_text(dpimg *im, const dpimg_font *f, int x, int y, const char *utf8, uint32_t rgba,
                          const char *anchor, int stroke);

/* ---- console textures (dpimg_tex.c) ---- */
/* Dreamcast PVR texture bytes, as a TCW (address bits ignored) and size describe them, to RGBA; palette: PAL_RAM's
 * entries (palettized formats), palette_format PAL_RAM_CTRL's (0 1555, 1 565, 2 4444, 3 8888). The top level. */
DPIMG_API dpimg *dpimg_decode_pvr(uint32_t tcw, int w, int h, int stride, const void *data, size_t size,
                                  const uint32_t *palette, int palette_count, int palette_format);
DPIMG_API uint32_t dpimg_pvr_bytes(uint32_t tcw, int w, int h, int stride); /* the bytes such a texture takes */

#ifdef __cplusplus
}
#endif
#endif
