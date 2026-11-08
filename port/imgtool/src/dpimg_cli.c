/* dpimg-cli: dpimg's command-line front end (docs/IMGTOOL.md). Each command loads, does one thing, saves.
 *   dpimg-cli info IN
 *   dpimg-cli convert IN OUT                         (any readable file to .png .bmp .tga)
 *   dpimg-cli crop IN OUT X0 Y0 X1 Y1
 *   dpimg-cli resize IN OUT W H [nearest|bilinear|bicubic|lanczos|box|hamming]   (bicubic, Pillow's default)
 *   dpimg-cli transpose IN OUT flip_lr|flip_tb|rot90|rot180|rot270|transpose|transverse
 *   dpimg-cli compare A B [TOLERANCE] [DIFF_OUT]     exit 0 equal, 1 different, 2 error
 *   dpimg-cli sheet OUT COLS CELL_W CELL_H IN...
 *   dpimg-cli pvr RAW OUT TCW_HEX W H [OFFSET]       a Dreamcast texture from a file of texture bytes
 *   dpimg-cli text OUT FONT SIZE TEXT [STROKE]       white text on transparent, cut to its ink */
#include "dpimg.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static const char *const FILTERS[] = {"nearest", "lanczos", "bilinear", "bicubic", "box", "hamming"};
static const char *const OPS[] = {"flip_lr", "flip_tb", "rot90", "rot180", "rot270", "transpose", "transverse"};

static int pick(const char *name, const char *const *names, int n)
{
    int i;
    for (i = 0; i < n; i++)
        if (!strcmp(name, names[i])) return i;
    return -1;
}

static dpimg *load(const char *path)
{
    dpimg *im = dpimg_load(path);
    if (!im) fprintf(stderr, "dpimg-cli: %s: %s\n", path, dpimg_error());
    return im;
}

static int save(dpimg *im, const char *path)
{
    int rc = im ? dpimg_save(im, path) : 1;
    if (rc) fprintf(stderr, "dpimg-cli: %s: %s\n", path, im ? dpimg_error() : "no image");
    dpimg_free(im);
    return rc ? 2 : 0;
}

static unsigned char *read_file(const char *path, long *size)
{
    FILE *f = fopen(path, "rb");
    unsigned char *d;
    if (!f) return NULL;
    fseek(f, 0, SEEK_END);
    *size = ftell(f);
    fseek(f, 0, SEEK_SET);
    d = (unsigned char *)malloc(*size > 0 ? (size_t)*size : 1);
    if (d && fread(d, 1, (size_t)*size, f) != (size_t)*size) free(d), d = NULL;
    fclose(f);
    return d;
}

int main(int argc, char **argv)
{
    const char *cmd = argc > 1 ? argv[1] : "";
    dpimg *a, *b;
    if (!strcmp(cmd, "info") && argc == 3) {
        if (!(a = load(argv[2]))) return 2;
        printf("%d x %d RGBA\n", a->w, a->h);
        dpimg_free(a);
        return 0;
    }
    if (!strcmp(cmd, "convert") && argc == 4) return (a = load(argv[2])) ? save(a, argv[3]) : 2;
    if (!strcmp(cmd, "crop") && argc == 8) {
        if (!(a = load(argv[2]))) return 2;
        b = dpimg_crop(a, atoi(argv[4]), atoi(argv[5]), atoi(argv[6]), atoi(argv[7]));
        dpimg_free(a);
        return save(b, argv[3]);
    }
    if (!strcmp(cmd, "resize") && (argc == 6 || argc == 7)) {
        int f = argc == 7 ? pick(argv[6], FILTERS, 6) : 3;
        if (f < 0 || !(a = load(argv[2]))) return 2;
        b = dpimg_resize(a, atoi(argv[4]), atoi(argv[5]), f);
        dpimg_free(a);
        return save(b, argv[3]);
    }
    if (!strcmp(cmd, "transpose") && argc == 5) {
        int op = pick(argv[4], OPS, 7);
        if (op < 0 || !(a = load(argv[2]))) return 2;
        b = dpimg_transpose(a, op);
        dpimg_free(a);
        return save(b, argv[3]);
    }
    if (!strcmp(cmd, "compare") && argc >= 4 && argc <= 6) {
        dpimg_cmp r;
        long long n;
        if (!(a = load(argv[2])) || !(b = load(argv[3]))) return 2;
        n = dpimg_compare(a, b, argc > 4 ? atoi(argv[4]) : 0, &r);
        printf("%lld pixels differ (largest channel difference %d) in %d,%d-%d,%d\n", n, r.max_delta, r.bbox[0],
               r.bbox[1], r.bbox[2], r.bbox[3]);
        if (argc == 6) save(dpimg_difference(a, b), argv[5]);
        dpimg_free(a), dpimg_free(b);
        return n ? 1 : 0;
    }
    if (!strcmp(cmd, "sheet") && argc >= 7) {
        int n = argc - 6, i, rc = 0;
        dpimg **ims = (dpimg **)calloc(n, sizeof *ims);
        for (i = 0; ims && i < n; i++)
            if (!(ims[i] = load(argv[6 + i]))) rc = 2;
        if (ims && !rc)
            rc = save(dpimg_contact_sheet((const dpimg *const *)ims, n, atoi(argv[3]), atoi(argv[4]), atoi(argv[5]),
                                          4, 0x202020ffu, 1),
                      argv[2]);
        for (i = 0; ims && i < n; i++) dpimg_free(ims[i]);
        free(ims);
        return rc;
    }
    if (!strcmp(cmd, "pvr") && (argc == 7 || argc == 8)) {
        long size = 0, off = argc == 8 ? strtol(argv[7], NULL, 0) : 0;
        unsigned char *d = read_file(argv[2], &size);
        uint32_t tcw = (uint32_t)strtoul(argv[4], NULL, 16);
        if (!d || off < 0 || off > size) return 2;
        b = dpimg_decode_pvr(tcw, atoi(argv[5]), atoi(argv[6]), 0, d + off, (size_t)(size - off), NULL, 0, 0);
        free(d);
        return save(b, argv[3]);
    }
    if (!strcmp(cmd, "text") && (argc == 6 || argc == 7)) {
        dpimg_font *f = dpimg_font_load(argv[3], (float)atof(argv[4]), 0);
        int ox, oy;
        if (!f) {
            fprintf(stderr, "dpimg-cli: %s: not a font this reads\n", argv[3]);
            return 2;
        }
        a = dpimg_text_mask(f, argv[5], argc == 7 ? atoi(argv[6]) : 0, &ox, &oy);
        dpimg_font_free(f);
        if (a) {
            size_t i;
            for (i = 0; i < (size_t)a->w * a->h; i++) a->px[i * 4] = a->px[i * 4 + 1] = a->px[i * 4 + 2] = 255;
        }
        return save(a, argv[2]);
    }
    fprintf(stderr, "usage: see the comment at the top of port/imgtool/src/dpimg_cli.c, or docs/IMGTOOL.md\n");
    return 2;
}
