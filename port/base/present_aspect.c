/* The shape of the frame: the aspect ratio choice (4:3, 16:9, 21:9, any W:H, or fit the window), and from it the frame
 * target's size and where the 640x480 screen sits in it. The frame is the largest rectangle of the chosen shape the
 * output holds, centred (bars where the shapes differ, never stretched); fit takes the whole output.
 *
 * The 640x480 screen is the game's 4:3 picture. A frame wider than 4:3 shows more of the world to its sides (wide_k:
 * the frame's width over the screen's), a narrower one more above and below (tall_k). The screen is the frame's
 * middle, and 2D drawn at the screen's size stays 4:3. */
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "present.h"

#define RATIO_MIN 0.2
#define RATIO_MAX 10.0

static int number(const char **p, double *v)
{
    char *end;
    *v = strtod(*p, &end);
    if (end == *p || !(*v > 0.0)) return 0;
    *p = end;
    return 1;
}

/* "fit" (0: follow the output's shape), "4:3", "16:9", "21:9", any "W:H" or a decimal ("1.6"); 0 if it is none of
 * those or out of range. */
int gfx_aspect_parse(const char *text, f32 *ratio)
{
    const char *p = text;
    double a, b = 1.0;
    if (!text) return 0;
    if (!strcmp(text, "fit") || !strcmp(text, "window")) {
        *ratio = 0.0f;
        return 1;
    }
    if (!number(&p, &a)) return 0;
    if (*p == ':' || *p == 'x') {
        p++;
        if (!number(&p, &b)) return 0;
    }
    if (*p || a / b < RATIO_MIN || a / b > RATIO_MAX) return 0;
    *ratio = (f32)(a / b);
    return 1;
}

static int is(f32 ratio, double w, double h) { return fabs((double)ratio - w / h) < 1.0e-3; }

/* The setting's words: "fit", "4:3", "16:9", "21:9" or the decimal. */
const char *gfx_aspect_name(f32 ratio, char buf[16])
{
    if (ratio <= 0.0f) return "fit";
    if (is(ratio, 4, 3)) return "4:3";
    if (is(ratio, 16, 9)) return "16:9";
    if (is(ratio, 21, 9)) return "21:9";
    snprintf(buf, 16, "%.4g", (double)ratio);
    return buf;
}

/* ratio (0: fit) in an output of out_w x out_h. */
void gfx_frame_shape(u32 out_w, u32 out_h, f32 ratio, GfxFrameShape *s)
{
    double r = ratio > 0.0f ? (double)ratio : (double)out_w / (double)(out_h ? out_h : 1u), k;
    s->w = out_w;
    s->h = out_h;
    if (ratio > 0.0f) {
        s->w = (u32)((double)out_h * r + 0.5);
        if (s->w > out_w) {
            s->w = out_w;
            s->h = (u32)((double)out_w / r + 0.5);
        }
        if (!s->w) s->w = 1;
        if (!s->h) s->h = 1;
    }
    s->x = (out_w - s->w) / 2u;
    s->y = (out_h - s->h) / 2u;
    k = r * 0.75;
    if (fabs(k - 1.0) < 1.0e-4) k = 1.0;
    s->wide_k = k > 1.0 ? (f32)k : 1.0f;
    s->tall_k = k < 1.0 ? (f32)(1.0 / k) : 1.0f;
}

/* The 640x480 screen inside a frame of w x h with these factors: x, y, width, height in frame pixels. */
void gfx_screen_rect(u32 w, u32 h, f32 wide_k, f32 tall_k, u32 r[4])
{
    r[2] = wide_k > 1.0f ? (u32)((f32)w / wide_k + 0.5f) : w;
    r[3] = tall_k > 1.0f ? (u32)((f32)h / tall_k + 0.5f) : h;
    r[0] = (w - r[2]) / 2u;
    r[1] = (h - r[3]) / 2u;
}
