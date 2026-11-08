/* dpimg drawing: ImageDraw on an image (no blending: the ink replaces the pixels, as ImageDraw.Draw(im) does).
 * Ported from Pillow 13.0.0 src/libImaging/Draw.c (line, polygon_generic, add_edge, ImagingDrawWideLine,
 * ImagingDrawRectangle, ImagingDrawPolygon, the quarter/ellipse scanners) and the ImageDraw.py glue that calls them,
 * so shapes cover the same pixels. Pillow is under the MIT-CMU licence (HPND): Copyright (c) 1997-2011 Secret Labs
 * AB, Copyright (c) 1995-2011 Fredrik Lundh and contributors, Copyright (c) 2010 Jeffrey A. Clark and contributors. */
#define DPIMG_BUILD
#include "dpimg.h"

#include <math.h>
#include <stdlib.h>
#include <string.h>

#include "dpimg_int.h"

#define ROUND_UP(f) ((int)((f) >= 0.0 ? floor((f) + 0.5F) : -floor(fabs(f) + 0.5F)))
#define ROUND_DOWN(f) ((int)((f) >= 0.0 ? ceil((f) - 0.5F) : -ceil(fabs(f) - 0.5F)))

typedef struct {
    int d, x0, y0, xmin, ymin, xmax, ymax;
    float dx;
} Edge;

static void point(Canvas *c, int x, int y)
{
    if (x >= 0 && x < c->w && y >= 0 && y < c->h) memcpy(c->px + ((size_t)y * c->w + x) * c->bpp, c->ink, c->bpp);
}

void dpimg__hline(Canvas *c, int x0, int y0, int x1)
{
    if (y0 < 0 || y0 >= c->h) return;
    if (x0 < 0) x0 = 0;
    else if (x0 >= c->w) return;
    if (x1 < 0) return;
    else if (x1 >= c->w) x1 = c->w - 1;
    for (; x0 <= x1; x0++)
        if (!c->gate || c->gate[(size_t)y0 * c->w + x0]) memcpy(c->px + ((size_t)y0 * c->w + x0) * c->bpp, c->ink, c->bpp);
}

static void line(Canvas *c, int x0, int y0, int x1, int y1)
{
    int i, n, e, dx, dy, xs, ys;
    dy = y1 - y0;
    if (dy < 0) dy = -dy, ys = -1;
    else if (dy == 0) {
        if (x1 > x0) dpimg__hline(c, x0, y0, x1 - 1);
        else if (x0 > x1) dpimg__hline(c, x1 + 1, y0, x0);
        return;
    } else ys = 1;
    dx = x1 - x0;
    if (dx < 0) dx = -dx, xs = -1;
    else xs = 1;
    if (dx == 0) {
        for (i = 0; i < dy; i++) point(c, x0, y0), y0 += ys;
    } else if (dx > dy) {
        n = dx, dy += dy, e = dy - dx, dx += dx;
        for (i = 0; i < n; i++) {
            point(c, x0, y0);
            if (e >= 0) y0 += ys, e -= dx;
            e += dy, x0 += xs;
        }
    } else {
        n = dy, dx += dx, e = dx - dy, dy += dy;
        for (i = 0; i < n; i++) {
            point(c, x0, y0);
            if (e >= 0) x0 += xs, e -= dy;
            e += dx, y0 += ys;
        }
    }
}

static int x_cmp(const void *a, const void *b)
{
    float d = *(const float *)a - *(const float *)b;
    return d < 0 ? -1 : d > 0 ? 1 : 0;
}

static void polygon_generic(Canvas *c, int n, Edge *e)
{
    Edge **table;
    float *xx, adj, adj_other;
    int count = 0, ymin = c->h - 1, ymax = 0, i, j, k;
    if (n <= 0) return;
    table = (Edge **)calloc(n, sizeof(Edge *));
    if (!table) return;
    for (i = 0; i < n; i++) {
        if (ymin > e[i].ymin) ymin = e[i].ymin;
        if (ymax < e[i].ymax) ymax = e[i].ymax;
        if (e[i].ymin == e[i].ymax) {
            dpimg__hline(c, e[i].xmin, e[i].ymin, e[i].xmax);
            continue;
        }
        table[count++] = e + i;
    }
    if (ymin < 0) ymin = 0;
    if (ymax > c->h) ymax = c->h;
    xx = (float *)calloc((size_t)count * 2 + 1, sizeof(float));
    if (!xx) {
        free(table);
        return;
    }
    for (; ymin <= ymax; ymin++) {
        j = 0;
        for (i = 0; i < count; i++) {
            Edge *cur = table[i];
            if (ymin < cur->ymin || ymin > cur->ymax) continue;
            xx[j++] = (ymin - cur->y0) * cur->dx + cur->x0;
            if (ymin == cur->ymax && ymin < ymax) {
                xx[j] = xx[j - 1];
                j++;
            } else if ((ymin == cur->ymin || ymin == cur->ymax) && cur->dx != 0) {
                for (k = 0; k < i; k++) { /* connect discontiguous corners */
                    Edge *o = table[k];
                    int off;
                    if ((ymin != o->ymin && ymin != o->ymax) || o->dx == 0) continue;
                    if (roundf(xx[j - 1]) != roundf((ymin - o->y0) * o->dx + o->x0)) continue;
                    off = ymin == cur->ymax ? -1 : 1;
                    adj = (ymin + off - cur->y0) * cur->dx + cur->x0;
                    if (ymin + off >= o->ymin && ymin + off <= o->ymax) {
                        adj_other = (ymin + off - o->y0) * o->dx + o->x0;
                        if (xx[j - 1] > adj + 1 && xx[j - 1] > adj_other + 1)
                            xx[j - 1] = roundf((float)fmax(adj, adj_other)) + 1;
                        else if (xx[j - 1] < adj - 1 && xx[j - 1] < adj_other - 1)
                            xx[j - 1] = roundf((float)fmin(adj, adj_other)) - 1;
                        break;
                    }
                }
            }
        }
        qsort(xx, j, sizeof(float), x_cmp);
        for (i = 1; i < j; i += 2) dpimg__hline(c, ROUND_UP(xx[i - 1]), ymin, ROUND_DOWN(xx[i]));
    }
    free(xx);
    free(table);
}

static void add_edge(Edge *e, int x0, int y0, int x1, int y1)
{
    if (x0 <= x1) e->xmin = x0, e->xmax = x1;
    else e->xmin = x1, e->xmax = x0;
    if (y0 <= y1) e->ymin = y0, e->ymax = y1;
    else e->ymin = y1, e->ymax = y0;
    if (y0 == y1) {
        e->d = 0;
        e->dx = 0.0;
    } else {
        e->dx = ((float)(x1 - x0)) / (y1 - y0);
        e->d = y0 == e->ymin ? 1 : -1;
    }
    e->x0 = x0;
    e->y0 = y0;
}

static void wide_line(Canvas *c, int x0, int y0, int x1, int y1, int width)
{
    int dx = x1 - x0, dy = y1 - y0, dxmin, dxmax, dymin, dymax;
    double big, small, rmax, rmin;
    Edge e[4];
    if (dx == 0 && dy == 0) {
        point(c, x0, y0);
        return;
    }
    big = hypot(dx, dy);
    small = (width - 1) / 2.0;
    rmax = ROUND_UP(small) / big;
    rmin = ROUND_DOWN(small) / big;
    dxmin = ROUND_DOWN(rmin * dy), dxmax = ROUND_DOWN(rmax * dy);
    dymin = ROUND_DOWN(rmin * dx), dymax = ROUND_DOWN(rmax * dx);
    {
        int v[4][2] = {{x0 - dxmin, y0 + dymax}, {x1 - dxmin, y1 + dymax}, {x1 + dxmax, y1 - dymin},
                       {x0 + dxmax, y0 - dymin}};
        add_edge(e + 0, v[0][0], v[0][1], v[1][0], v[1][1]);
        add_edge(e + 1, v[1][0], v[1][1], v[2][0], v[2][1]);
        add_edge(e + 2, v[2][0], v[2][1], v[3][0], v[3][1]);
        add_edge(e + 3, v[3][0], v[3][1], v[0][0], v[0][1]);
        polygon_generic(c, 4, e);
    }
}

Canvas dpimg__canvas(dpimg *im, uint32_t rgba)
{
    Canvas c;
    c.px = im->px, c.w = im->w, c.h = im->h, c.bpp = 4, c.gate = NULL;
    c.ink[0] = (uint8_t)(rgba >> 24), c.ink[1] = (uint8_t)(rgba >> 16), c.ink[2] = (uint8_t)(rgba >> 8);
    c.ink[3] = (uint8_t)rgba;
    return c;
}

/* ImageDraw.line: width 1 draws each segment without its end, then the last point; wider ones are polygons. */
void dpimg_line(dpimg *im, const int *xy, int n, uint32_t rgba, int width)
{
    Canvas c = dpimg__canvas(im, rgba);
    int i;
    if (width <= 0) return;
    for (i = 0; i < n - 1; i++) {
        const int *p = xy + i * 2;
        if (width == 1) line(&c, p[0], p[1], p[2], p[3]);
        else wide_line(&c, p[0], p[1], p[2], p[3], width);
    }
    if (width == 1 && n > 1) point(&c, xy[(n - 1) * 2], xy[(n - 1) * 2 + 1]);
}

static void rect(Canvas *c, int x0, int y0, int x1, int y1, int fill, int width)
{
    int i, y;
    if (fill) {
        if (y0 < 0) y0 = 0;
        else if (y0 >= c->h) return;
        if (y1 < 0) return;
        else if (y1 > c->h) y1 = c->h;
        for (y = y0; y <= y1; y++) dpimg__hline(c, x0, y, x1);
        return;
    }
    if (width == 0) width = 1;
    for (i = 0; i < width; i++) {
        dpimg__hline(c, x0, y0 + i, x1);
        dpimg__hline(c, x0, y1 - i, x1);
        line(c, x1 - i, y0 + width, x1 - i, y1 - width + 1);
        line(c, x0 + i, y0 + width, x0 + i, y1 - width + 1);
    }
}

/* ImageDraw.rectangle(xy, fill, outline, width): flags 1 = fill, 2 = outline. */
void dpimg_rect(dpimg *im, int x0, int y0, int x1, int y1, uint32_t fill, uint32_t outline, int width, int flags)
{
    Canvas c;
    if (x1 < x0 || y1 < y0) return;
    if (flags & 1) c = dpimg__canvas(im, fill), rect(&c, x0, y0, x1, y1, 1, 1);
    if ((flags & 2) && !((flags & 1) && outline == fill) && width) c = dpimg__canvas(im, outline), rect(&c, x0, y0, x1, y1, 0, width);
}

static void polygon(Canvas *c, const int *xy, int count, int fill, int width)
{
    int i, n, x0, y0, x1, y1;
    if (count <= 0) return;
    if (fill) {
        Edge *e = (Edge *)calloc(count, sizeof(Edge));
        if (!e) return;
        for (i = n = 0; i < count - 1; i++) {
            x0 = xy[i * 2], y0 = xy[i * 2 + 1], x1 = xy[i * 2 + 2], y1 = xy[i * 2 + 3];
            if (y0 == y1 && i != 0 && y0 == xy[i * 2 - 1]) { /* a horizontal line after another */
                Edge *last = &e[n - 1];
                if (x1 > x0 && x0 > xy[i * 2 - 2]) {
                    last->xmax = x1;
                    continue;
                } else if (x1 < x0 && x0 < xy[i * 2 - 2]) {
                    last->xmin = x1;
                    continue;
                }
            }
            add_edge(&e[n++], x0, y0, x1, y1);
        }
        if (xy[i * 2] != xy[0] || xy[i * 2 + 1] != xy[1]) add_edge(&e[n++], xy[i * 2], xy[i * 2 + 1], xy[0], xy[1]);
        polygon_generic(c, n, e);
        free(e);
        return;
    }
    for (i = 0; i < count - 1; i++) {
        if (width == 1) line(c, xy[i * 2], xy[i * 2 + 1], xy[i * 2 + 2], xy[i * 2 + 3]);
        else wide_line(c, xy[i * 2], xy[i * 2 + 1], xy[i * 2 + 2], xy[i * 2 + 3], width);
    }
    if (width == 1) line(c, xy[i * 2], xy[i * 2 + 1], xy[0], xy[1]);
    else wide_line(c, xy[i * 2], xy[i * 2 + 1], xy[0], xy[1], width);
}

/* ImageDraw.polygon(xy, fill, outline, width): a wider outline is drawn 2 * width - 1 wide inside the filled shape. */
void dpimg_polygon(dpimg *im, const int *xy, int n, uint32_t fill, uint32_t outline, int width, int flags)
{
    Canvas c;
    if ((flags & 1)) c = dpimg__canvas(im, fill), polygon(&c, xy, n, 1, 0);
    if (!(flags & 2) || ((flags & 1) && outline == fill) || !width) return;
    c = dpimg__canvas(im, outline);
    if (width == 1) {
        polygon(&c, xy, n, 0, 1);
    } else {
        Canvas m;
        uint8_t *gate = (uint8_t *)calloc((size_t)im->w * im->h + 1, 1);
        if (!gate) return;
        m.px = gate, m.w = im->w, m.h = im->h, m.bpp = 1, m.ink[0] = 255, m.gate = NULL;
        polygon(&m, xy, n, 1, 0);
        c.gate = gate;
        polygon(&c, xy, n, 0, width * 2 - 1);
        free(gate);
    }
}
