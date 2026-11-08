/* dpimg's internal declarations, shared by its source files only. */
#ifndef DPIMG_INT_H
#define DPIMG_INT_H

#include "dpimg.h"

/* A drawing target (dpimg_draw.c): an image's RGBA pixels, or a mask of one byte a pixel; gate, when set, is a mask
 * of one byte a pixel and dpimg__hline writes only where it is non-zero. */
typedef struct {
    uint8_t *px;
    int w, h, bpp;
    uint8_t ink[4];
    const uint8_t *gate;
} Canvas;

Canvas dpimg__canvas(dpimg *im, uint32_t rgba);
void dpimg__hline(Canvas *c, int x0, int y0, int x1); /* x0..x1 inclusive, clipped */

#endif
