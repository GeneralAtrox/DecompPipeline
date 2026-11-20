/* A3: pixel lettering redrawn sharp at k times its size, with only the staircases cut (clearer text, sat_text.c). The
 * outline of the lit dots is traced as the pixel boundary (8-connected: two dots touching at a corner stay one shape);
 * a run of two or more 1-dot stair steps becomes a line through the steps' midpoints, so the weight is kept; every other
 * edge (flat tops and bottoms, right angles, spurs) stays on the dot grid. The loops are filled even-odd, each output
 * row from 8 sub-rows with exact coverage along the row.
 *
 * A shaded picture (sat_a3_picture): the dots darker than 0.35 of the brightest are its shadow or outline; when they are
 * more than a tenth of it, the whole silhouette is A3 filled with their colours, and over it the lettering (the dots at
 * least half way from the shadow's brightness to the ink's) is A3 filled with the ink's colours. Each part's colours are
 * carried out from its own dots to every dot and scaled up bilinearly, so a gradient stays a gradient.
 *
 * The reference these follow line for line: Dragon Force .work/font-design/scripts/fdlib.py (pixel_loops, crisp_glyph
 * 'A3', draw_loops, a3_picture with edge 'a3'), the version the user approved (2026-11-18).
 *
 * Nearest existing: none in the kit (sat_vdp1.c draws dots; nothing traces them). */
#include <stdlib.h>
#include <string.h>
#include "sat.h"
#include "sat_text.h"

enum { MAXD = 64 * 256, SUB = 8 };

typedef struct { int x, y, dx, dy; } Edge;
typedef struct { float x, y; } Fp;

/* the loops of mask m (w x h): points into pts, each loop's count into lens; returns the number of loops */
static int trace(const u8 *m, int w, int h, Fp *pts, int *npts, int *lens, int maxpts)
{
    int W = w + 1, ne = 0, nl = 0, np = 0;
    Edge *e = (Edge *)malloc(sizeof(Edge) * (size_t)(4 * w * h + 4));
    int *first = (int *)malloc(sizeof(int) * (size_t)(W * (h + 1)) * 2), *used = (int *)calloc((size_t)(4 * w * h + 4), sizeof(int));
    int *seg_x = (int *)malloc(sizeof(int) * (size_t)(4 * w * h + 4) * 5);
    if (!e || !first || !used || !seg_x) goto done;
    for (int i = 0; i < W * (h + 1) * 2; i++) first[i] = -1;
#define ON(xx, yy) ((xx) >= 0 && (yy) >= 0 && (xx) < w && (yy) < h && m[(yy) * w + (xx)])
#define ADD(px, py, ddx, ddy) do { int v_ = ((py) * W + (px)) * 2; e[ne].x = px, e[ne].y = py, e[ne].dx = ddx, e[ne].dy = ddy; first[v_ + (first[v_] >= 0)] = ne++; } while (0)
    for (int y = 0; y < h; y++)
        for (int x = 0; x < w; x++) {
            if (!m[y * w + x]) continue;
            if (!ON(x, y - 1)) ADD(x + 1, y, -1, 0);
            if (!ON(x, y + 1)) ADD(x, y + 1, 1, 0);
            if (!ON(x - 1, y)) ADD(x, y, 0, 1);
            if (!ON(x + 1, y)) ADD(x + 1, y + 1, 0, -1);
        }
    for (int s = 0; s < ne; s++) {
        int cur = s, n = 0, *sg = seg_x; /* segments as (x, y, dx, dy, len) */
        if (used[s]) continue;
        while (!used[cur]) {
            int v = ((e[cur].y + e[cur].dy) * W + e[cur].x + e[cur].dx) * 2, a = first[v], b = first[v + 1], wdx = -e[cur].dy, wdy = e[cur].dx;
            used[cur] = 1;
            if (n && sg[(n - 1) * 5 + 2] == e[cur].dx && sg[(n - 1) * 5 + 3] == e[cur].dy) sg[(n - 1) * 5 + 4]++;
            else sg[n * 5] = e[cur].x, sg[n * 5 + 1] = e[cur].y, sg[n * 5 + 2] = e[cur].dx, sg[n * 5 + 3] = e[cur].dy, sg[n * 5 + 4] = 1, n++;
            if (a >= 0 && used[a]) a = -1;
            if (b >= 0 && used[b]) b = -1;
            if (a < 0) a = b, b = -1;
            if (a < 0) break;
            if (b >= 0 && !(e[a].dx == wdx && e[a].dy == wdy)) a = b; /* a saddle: turn so the corner-touching dots stay one shape */
            cur = a;
        }
        if (n > 1 && sg[2] == sg[(n - 1) * 5 + 2] && sg[3] == sg[(n - 1) * 5 + 3]) { /* the first run continues the last */
            sg[(n - 1) * 5 + 4] += sg[4];
            memmove(sg, sg + 5, sizeof(int) * 5 * (size_t)(n - 1));
            n--;
        }
        if (n < 4) continue;
        {
            int zig[1024], cut[1024], turn[1024], z2[1024], start = np;
            if (n > 1024 || nl >= 4096 || np + 2 * n > maxpts) continue;
            for (int i = 0; i < n; i++) {
                int j = (i + n - 1) % n;
                turn[i] = sg[j * 5 + 2] * sg[i * 5 + 3] - sg[j * 5 + 3] * sg[i * 5 + 2];
            }
            for (int i = 0; i < n; i++) zig[i] = sg[i * 5 + 4] == 1 && turn[i] != turn[(i + 1) % n];
            for (int i = 0; i < n; i++) z2[i] = zig[i] && (zig[(i + n - 2) % n] || zig[(i + 2) % n]); /* A3: runs of 2 steps or more */
            memset(cut, 0, sizeof(int) * (size_t)n);
            for (int i = 0; i < n; i++)
                if (z2[i]) cut[i] = cut[(i + 1) % n] = 1;
            for (int i = 0; i < n; i++) {
                int a = cut[i], b = cut[(i + 1) % n];
                float px = (float)sg[i * 5], py = (float)sg[i * 5 + 1], dx = (float)sg[i * 5 + 2], dy = (float)sg[i * 5 + 3], ln = (float)sg[i * 5 + 4], hh;
                if (!a) pts[np].x = px, pts[np].y = py, np++;
                if (a && b) pts[np].x = px + dx * ln / 2, pts[np].y = py + dy * ln / 2, np++;
                else if (a) hh = ln / 2 < 0.5f ? ln / 2 : 0.5f, pts[np].x = px + dx * hh, pts[np].y = py + dy * hh, np++;
                else if (b) hh = ln - (ln / 2 < 0.5f ? ln / 2 : 0.5f), pts[np].x = px + dx * hh, pts[np].y = py + dy * hh, np++;
            }
            lens[nl++] = np - start;
        }
    }
done:
    free(e), free(first), free(used), free(seg_x);
    *npts = np;
    return nl;
}

/* even-odd coverage of the loops at k times (an output of kw x kh, 0..255); each sub-row tests only the edges that
 * reach its source row (bucketed once, in loop order, so the crossings are the same as testing every edge) */
static void fill(const Fp *pts, const int *lens, int nl, int kw, int kh, int k, u8 *cov)
{
    int rows = (kh + k - 1) / k, ne = 0, *start = (int *)calloc((size_t)rows + 2u, sizeof(int)), *pos = (int *)calloc((size_t)rows + 2u, sizeof(int)), *at = NULL;
    float *acc = (float *)malloc(sizeof(float) * (size_t)(kw + 2)), xs[512];
    Fp *ea = NULL;
    for (int l = 0; l < nl; l++) ne += lens[l];
    if (!acc || !start || !pos || !(ea = (Fp *)malloc(sizeof(Fp) * 2u * (size_t)(ne + 1))) || !(at = (int *)malloc(sizeof(int) * (size_t)(ne * (rows + 1) + 1)))) goto done;
    for (int pass = 0; pass < 2; pass++) { /* count each source row's edges, then place them in loop order */
        int base = 0, e = 0;
        if (pass)
            for (int r = 0; r < rows; r++) start[r + 1] += start[r], pos[r] = start[r];
        for (int l = 0; l < nl; base += lens[l++])
            for (int i = 0; i < lens[l]; i++, e++) {
                Fp a = pts[base + i], b = pts[base + (i + 1) % lens[l]];
                float lo = a.y < b.y ? a.y : b.y, hi = a.y < b.y ? b.y : a.y;
                int r0 = (int)lo, r1 = (int)hi - ((float)(int)hi == hi);
                if (lo == hi) continue;
                r0 = r0 < 0 ? 0 : r0, r1 = r1 >= rows ? rows - 1 : r1;
                ea[2 * e] = a, ea[2 * e + 1] = b;
                for (int r = r0; r <= r1; r++)
                    if (pass) at[pos[r]++] = e;
                    else start[r + 1]++;
            }
    }
    for (int Y = 0; Y < kh; Y++) {
        memset(acc, 0, sizeof(float) * (size_t)(kw + 2));
        for (int s = 0; s < SUB; s++) {
            float yy = ((float)Y + ((float)s + 0.5f) / SUB) / (float)k;
            int nx = 0, r = (int)yy;
            if (r >= rows) r = rows - 1;
            for (int q = start[r]; q < start[r + 1]; q++) {
                Fp a = ea[2 * at[q]], b = ea[2 * at[q] + 1];
                if ((a.y <= yy) != (b.y <= yy) && nx < 512) xs[nx++] = (a.x + (yy - a.y) * (b.x - a.x) / (b.y - a.y)) * (float)k;
            }
            for (int i = 1; i < nx; i++) /* a few crossings a row: insertion sort */
                for (int j = i; j > 0 && xs[j - 1] > xs[j]; j--) { float t = xs[j]; xs[j] = xs[j - 1], xs[j - 1] = t; }
            for (int i = 0; i + 1 < nx; i += 2) {
                float a = xs[i] < 0 ? 0 : xs[i], b = xs[i + 1] > (float)kw ? (float)kw : xs[i + 1];
                int ia = (int)a, ib = (int)b;
                if (b <= a) continue;
                if (ia == ib) { acc[ia] += b - a; continue; }
                acc[ia] += (float)(ia + 1) - a;
                for (int x = ia + 1; x < ib; x++) acc[x] += 1.0f;
                if (ib < kw) acc[ib] += b - (float)ib;
            }
        }
        for (int X = 0; X < kw; X++) {
            float c = acc[X] / SUB;
            cov[Y * kw + X] = (u8)(c >= 1.0f ? 255 : c <= 0 ? 0 : (int)(c * 255.0f + 0.5f));
        }
    }
done:
    free(acc), free(start), free(pos), free(ea), free(at);
}

void sat_a3_cover(const u8 *m, int w, int h, int k, u8 *cov)
{
    int maxpts = 8 * w * h + 64, np, lens[4096], nl;
    Fp *pts = (Fp *)malloc(sizeof(Fp) * (size_t)maxpts);
    memset(cov, 0, (size_t)(w * k) * (size_t)(h * k));
    if (!pts) return;
    nl = trace(m, w, h, pts, &np, lens, maxpts);
    if (nl > 4096) nl = 4096;
    fill(pts, lens, nl, w * k, h * k, k, cov);
    free(pts);
}

/* every dot's colour carried from the nearest known dot (a breadth-first spread, 4-neighbour) */
static void spread(const u32 *rgb, const u8 *known, int w, int h, u32 *out)
{
    int *q = (int *)malloc(sizeof(int) * (size_t)(w * h)), qh = 0, qt = 0;
    u8 *seen = (u8 *)calloc((size_t)(w * h), 1);
    if (!q || !seen) { free(q), free(seen); return; }
    for (int i = 0; i < w * h; i++) {
        out[i] = 0;
        if (known[i]) out[i] = rgb[i], seen[i] = 1, q[qt++] = i;
    }
    while (qh < qt) {
        int i = q[qh++], x = i % w, y = i / w, nb[4] = {x > 0 ? i - 1 : -1, x < w - 1 ? i + 1 : -1, y > 0 ? i - w : -1, y < h - 1 ? i + w : -1};
        for (int j = 0; j < 4; j++)
            if (nb[j] >= 0 && !seen[nb[j]]) seen[nb[j]] = 1, out[nb[j]] = out[i], q[qt++] = nb[j];
    }
    free(q), free(seen);
}

static float lum(u32 c) { return 0.299f * (float)(c >> 16 & 255u) + 0.587f * (float)(c >> 8 & 255u) + 0.114f * (float)(c & 255u); }

/* field (w x h colours) at output dot X, Y of a k-times picture, bilinear */
static void sample(const u32 *f, int w, int h, int k, int X, int Y, float *rgb)
{
    float sx = ((float)X + 0.5f) / (float)k - 0.5f, sy = ((float)Y + 0.5f) / (float)k - 0.5f, fx, fy;
    int x0, y0, x1, y1;
    sx = sx < 0 ? 0 : sx > (float)(w - 1) ? (float)(w - 1) : sx, sy = sy < 0 ? 0 : sy > (float)(h - 1) ? (float)(h - 1) : sy;
    x0 = (int)sx, y0 = (int)sy, x1 = x0 + 1 < w ? x0 + 1 : x0, y1 = y0 + 1 < h ? y0 + 1 : y0, fx = sx - (float)x0, fy = sy - (float)y0;
    for (int c = 0; c < 3; c++) {
        int s = 16 - 8 * c;
        float a = (float)(f[y0 * w + x0] >> s & 255u), b = (float)(f[y0 * w + x1] >> s & 255u), cc = (float)(f[y1 * w + x0] >> s & 255u), d = (float)(f[y1 * w + x1] >> s & 255u);
        rgb[c] = (a * (1 - fx) + b * fx) * (1 - fy) + (cc * (1 - fx) + d * fx) * fy;
    }
}

/* A3 of a picture: dots w x h (0x00RRGGBB), lit where on[] is set; out: (w k) x (h k) premultiplied 0xAARRGGBB */
int sat_a3_picture(const u32 *dots, const u8 *on, int w, int h, int k, u32 *out)
{
    int n = w * h, nm = 0, ns = 0, kw = w * k, kh = h * k, has_shade, ok = 0, flat1 = 1, flat2 = 1;
    float f1[3], f2[3];
    float lmax = 1, *L = (float *)malloc(sizeof(float) * (size_t)n);
    u8 *shade = (u8 *)calloc((size_t)n, 1), *ink = (u8 *)calloc((size_t)n, 1), *lit = (u8 *)calloc((size_t)n, 1);
    u8 *c1 = (u8 *)malloc((size_t)(kw * kh)), *c2 = (u8 *)malloc((size_t)(kw * kh));
    u32 *fs = (u32 *)malloc(sizeof(u32) * (size_t)n), *fi = (u32 *)malloc(sizeof(u32) * (size_t)n), *lb = (u32 *)malloc(sizeof(u32) * (size_t)n), *li = (u32 *)malloc(sizeof(u32) * (size_t)n);
    if (!L || !shade || !ink || !lit || !c1 || !c2 || !fs || !fi || !lb || !li || n > MAXD) goto done;
    for (int i = 0; i < n; i++) {
        L[i] = lum(dots[i]);
        if (on[i]) nm++, lmax = L[i] > lmax || nm == 1 ? L[i] : lmax;
    }
    for (int i = 0; i < n; i++) {
        shade[i] = on[i] && L[i] < 0.35f * lmax, ink[i] = on[i] && L[i] >= 0.75f * lmax;
        ns += shade[i];
    }
    has_shade = (float)ns > 0.1f * (float)nm;
    { /* the brightness under the ink (the shadow's, carried) and the ink's own, carried: coverage between them */
        u32 *lv = fs;
        for (int i = 0; i < n; i++) lv[i] = (u32)(L[i] * 16.0f);
        if (has_shade) spread(lv, shade, w, h, lb);
        else memset(lb, 0, sizeof(u32) * (size_t)n);
        spread(lv, ink, w, h, li);
        for (int i = 0; i < n; i++) {
            float bg = (float)lb[i] / 16.0f, in = (float)li[i] / 16.0f, d = in - bg, cv = (L[i] - bg) / (d > 1e-6f ? d : 1e-6f);
            lit[i] = on[i] && cv >= 0.5f;
        }
    }
    if (has_shade) sat_a3_cover(on, w, h, k, c1), spread(dots, shade, w, h, fs);
    else memset(c1, 0, (size_t)(kw * kh));
    sat_a3_cover(lit, w, h, k, c2), spread(dots, ink, w, h, fi);
    for (int i = 1; i < n; i++) flat1 &= fs[i] == fs[0], flat2 &= fi[i] == fi[0]; /* one colour each: no bilinear needed */
    for (int c = 0; c < 3; c++) f1[c] = (float)(fs[0] >> (16 - 8 * c) & 255u), f2[c] = (float)(fi[0] >> (16 - 8 * c) & 255u);
    for (int Y = 0; Y < kh; Y++)
        for (int X = 0; X < kw; X++) {
            float a1 = (float)c1[Y * kw + X] / 255.0f, a2 = (float)c2[Y * kw + X] / 255.0f, s1[3] = {0, 0, 0}, s2[3] = {0, 0, 0}, a, r[3];
            if (!c1[Y * kw + X] && !c2[Y * kw + X]) {
                out[Y * kw + X] = 0;
                continue;
            }
            if (a1 > 0) {
                if (flat1) s1[0] = f1[0], s1[1] = f1[1], s1[2] = f1[2];
                else sample(fs, w, h, k, X, Y, s1);
            }
            if (a2 > 0) {
                if (flat2) s2[0] = f2[0], s2[1] = f2[1], s2[2] = f2[2];
                else sample(fi, w, h, k, X, Y, s2);
            }
            for (int c = 0; c < 3; c++) r[c] = s1[c] * a1 * (1 - a2) + s2[c] * a2;
            a = a1 * (1 - a2) + a2;
            out[Y * kw + X] = (u32)(a * 255.0f + 0.5f) << 24 | (u32)(r[0] + 0.5f) << 16 | (u32)(r[1] + 0.5f) << 8 | (u32)(r[2] + 0.5f);
        }
    ok = 1;
done:
    free(L), free(shade), free(ink), free(lit), free(c1), free(c2), free(fs), free(fi), free(lb), free(li);
    return ok;
}
