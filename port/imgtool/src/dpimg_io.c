/* dpimg images and files: allocation, and loading and saving through the vendored stb_image / stb_image_write. */
#define DPIMG_BUILD
#include "dpimg.h"

#include <stdlib.h>
#include <string.h>

#define STB_IMAGE_IMPLEMENTATION
#define STBI_NO_HDR
#define STBI_NO_LINEAR
#define STBI_FAILURE_USERMSG
#include "stb_image.h"
#define STB_IMAGE_WRITE_IMPLEMENTATION
#include "stb_image_write.h"

static const char *last_error = "";

const char *dpimg_error(void) { return last_error; }

static dpimg *alloc_image(int w, int h)
{
    dpimg *im;
    if (w < 0 || h < 0 || (w && h > (1 << 30) / 4 / w)) {
        last_error = "image too large";
        return NULL;
    }
    im = (dpimg *)calloc(1, sizeof *im);
    if (!im) return NULL;
    im->w = w;
    im->h = h;
    im->px = (uint8_t *)malloc((size_t)w * h * 4 + 1);
    if (!im->px) {
        free(im);
        last_error = "out of memory";
        return NULL;
    }
    return im;
}

dpimg *dpimg_new(int w, int h, uint32_t rgba)
{
    dpimg *im = alloc_image(w, h);
    size_t i, n;
    uint8_t c[4] = {(uint8_t)(rgba >> 24), (uint8_t)(rgba >> 16), (uint8_t)(rgba >> 8), (uint8_t)rgba};
    if (!im) return NULL;
    n = (size_t)w * h;
    for (i = 0; i < n; i++) memcpy(im->px + i * 4, c, 4);
    return im;
}

dpimg *dpimg_from_rgba(int w, int h, const void *px)
{
    dpimg *im = alloc_image(w, h);
    if (im) memcpy(im->px, px, (size_t)w * h * 4);
    return im;
}

dpimg *dpimg_copy(const dpimg *im) { return dpimg_from_rgba(im->w, im->h, im->px); }

void dpimg_free(dpimg *im)
{
    if (!im) return;
    free(im->px);
    free(im);
}

static dpimg *wrap_loaded(stbi_uc *data, int w, int h)
{
    dpimg *im;
    if (!data) {
        last_error = stbi_failure_reason();
        return NULL;
    }
    im = (dpimg *)calloc(1, sizeof *im);
    if (!im) {
        stbi_image_free(data);
        return NULL;
    }
    im->w = w;
    im->h = h;
    im->px = data; /* stb allocates with malloc (STBI_MALLOC), so dpimg_free's free() releases it */
    return im;
}

dpimg *dpimg_load(const char *path)
{
    int w, h, n;
    stbi_uc *data = stbi_load(path, &w, &h, &n, 4);
    return wrap_loaded(data, w, h);
}

dpimg *dpimg_load_mem(const void *bytes, int size)
{
    int w, h, n;
    stbi_uc *data = stbi_load_from_memory((const stbi_uc *)bytes, size, &w, &h, &n, 4);
    return wrap_loaded(data, w, h);
}

static int ends_with(const char *s, const char *tail)
{
    size_t a = strlen(s), b = strlen(tail), i;
    if (a < b) return 0;
    for (i = 0; i < b; i++) {
        char c = s[a - b + i];
        if (c >= 'A' && c <= 'Z') c = (char)(c - 'A' + 'a');
        if (c != tail[i]) return 0;
    }
    return 1;
}

int dpimg_save(const dpimg *im, const char *path) { return dpimg_save_as(im, path, 4); }

/* channels 1 (grey: the R channel), 3 (RGB) or 4, as Pillow writes an L, RGB or RGBA image */
int dpimg_save_as(const dpimg *im, const char *path, int channels)
{
    int ok, c = channels;
    uint8_t *px;
    size_t i, n;
    if (!im || im->w <= 0 || im->h <= 0) {
        last_error = "empty image";
        return 1;
    }
    if (c != 1 && c != 3 && c != 4) {
        last_error = "channels must be 1, 3 or 4";
        return 1;
    }
    n = (size_t)im->w * im->h;
    px = c == 4 ? im->px : (uint8_t *)malloc(n * c + 1);
    if (!px) {
        last_error = "out of memory";
        return 1;
    }
    for (i = 0; c != 4 && i < n; i++) memcpy(px + i * c, im->px + i * 4, c);
    if (ends_with(path, ".png")) {
        stbi_write_png_compression_level = 9;
        ok = stbi_write_png(path, im->w, im->h, c, px, im->w * c);
    } else if (ends_with(path, ".bmp")) {
        ok = stbi_write_bmp(path, im->w, im->h, c, px);
    } else if (ends_with(path, ".tga")) {
        ok = stbi_write_tga(path, im->w, im->h, c, px);
    } else {
        last_error = "unknown file type (use .png, .bmp or .tga)";
        ok = -1;
    }
    if (px != im->px) free(px);
    if (ok < 0) return 1;
    if (!ok) last_error = "could not write the file";
    return ok ? 0 : 1;
}
