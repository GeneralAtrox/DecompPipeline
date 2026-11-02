/* Where and how big the windowed game window is, as plain arithmetic: no SDL, no Windows, so a test can build it alone
 * (tests/test_window_place.py). host_window.cpp gives it the numbers it measures: the usable area of the display the
 * window is on (the work area: the screen without the taskbar) and the chrome, the outer window's size minus its
 * client area (title bar, borders and menu bar). The whole outer window then sits inside the area. */
#ifndef HOST_PLACE_H
#define HOST_PLACE_H

typedef struct {
    int x, y, w, h;
} PlaceRect;

/* The largest whole multiple n of base_w x base_h whose client area, with the chrome around it, fits the area; 0 when
 * even 1x does not. */
static inline int place_fit_scale(PlaceRect area, int chrome_w, int chrome_h, int base_w, int base_h)
{
    int nw = (area.w - chrome_w) / base_w, nh = (area.h - chrome_h) / base_h;
    int n = nw < nh ? nw : nh;
    return n < 0 ? 0 : n;
}

/* The client size to ask for: w x h as wanted when its outer window fits the area, otherwise the largest size of the
 * same shape that does (the picture shrinks, its aspect stays). Never below 1x1. */
static inline void place_client(PlaceRect area, int chrome_w, int chrome_h, int *w, int *h)
{
    long aw = area.w - chrome_w, ah = area.h - chrome_h;
    if (aw < 1) aw = 1;
    if (ah < 1) ah = 1;
    if (*w <= aw && *h <= ah) return;
    if (aw * *h <= ah * *w) { /* the width is what runs out first */
        *h = (int)(aw * *h / *w);
        *w = (int)aw;
    } else {
        *w = (int)(ah * *w / *h);
        *h = (int)ah;
    }
    if (*w < 1) *w = 1;
    if (*h < 1) *h = 1;
}

/* The outer window's top left: centred in the area, and never outside it (an outer window larger than the area sits at
 * the area's top left, so the title bar shows). */
static inline void place_origin(PlaceRect area, int outer_w, int outer_h, int *x, int *y)
{
    int dx = area.w - outer_w, dy = area.h - outer_h;
    *x = area.x + (dx > 0 ? dx / 2 : 0);
    *y = area.y + (dy > 0 ? dy / 2 : 0);
}

#endif
