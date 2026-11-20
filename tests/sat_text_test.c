/* Clearer text (sat_text.c with sat_vdp1.c, sat_vdp1_list.c and sat_text_a3.c): a list draws a background, a half-
 * transparent polygon, a lettering sprite (a staircase of white dots) and a sprite the game does not name, drawn over the
 * lettering's right half. The game's framebuffer must be the same with Clearer text on and off; the copy the composer
 * shows has the lettering's own dots (and only those) back to what was under them, marked; the posted image is the
 * lettering's dots at k times, its staircase cut into a slope (A3). With "async" (a play run's maker thread) the frame
 * that first shows the lettering makes no image: it shows the lettering's own dots, and the image comes once made.
 * Run by tests/test_sat_text.py. */
#define _CRT_SECURE_NO_WARNINGS
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "sat.h"
#include "sat_vdp.h"
#include "present_cpu.h"

u8 sat_v1_vram[0x80000], sat_v1_fb[2][0x40000], sat_v2_vram[0x80000], sat_v2_reg[0x200];
u32 sat_frame[SAT_MAX_W * SAT_MAX_H], sat_frame_w = 320, sat_frame_h = 224;
int g_work_on;
u64 g_work_now[64];
static int g_fail, g_tag = 1, g_opt = 1, g_threaded;
static unsigned g_over_n;
static PresentOverlay g_over;

static int hook(const SatTextSprite *s) { return g_tag && s->srca == 0x200u ? 7 : 0; }
const SatGameHooks sat_game_table = {.text = hook};

/* a stand-in for each kit function the files call */
void port_log(const char *fmt, ...) { (void)fmt; }
u32 sat_vblank_count(void) { return 1; }
void sat_perf_phase(int phase) { (void)phase; }
void sat_perf_v1(long long ticks) { (void)ticks; }
u8 *sat_v1_draw_fb(void) { return sat_v1_fb[0]; }
const u8 *sat_v1_display_fb(void) { return sat_v1_fb[0]; }
u32 sat_wide_sw = 320;
u8 *sat_wide_fb(const u8 *guest) { (void)guest; return NULL; }
int sat_wide_fb_ox(const u8 *fb) { (void)fb; return 0; }
void sat_wide_back(u8 *guest) { (void)guest; }
void sat_panel_mark(u32 pmod, u32 colr) { (void)pmod, (void)colr; }
void port_exit(const char *why, int code) { (void)why, (void)code; }
int port_option(const char *id) { return strcmp(id, "clear-text") ? -1 : g_opt; }
int sat_video_headless(void) { return 0; }
int host_fixed_clock(void) { return 0; }
void cpu_present_view(PortView *v) { memset(v, 0, sizeof *v); }
int cpu_present_threaded(void) { return g_threaded; }
void cpu_present_prepare(PresentImage *im) { (void)im; }
void cpu_present_unprepare(PresentImage *im) { (void)im; }
void cpu_present_overlay(const PresentOverlay *o, unsigned n) { g_over_n = n, g_over = n ? o[0] : g_over; }
int sat_png_write(const char *path, const u32 *rgb, u32 w, u32 h) { (void)path, (void)rgb, (void)w, (void)h; return 1; }

static void w16(u32 a, u32 v) { sat_v1_vram[a] = (u8)(v >> 8), sat_v1_vram[a + 1] = (u8)v; }
static void cmd(u32 a, u32 ctrl, u32 pmod, u32 colr, u32 srca, u32 size, int x0, int y0, int x1, int y1)
{
    int xy[8] = {x0, y0, x1, y0, x1, y1, x0, y1};
    w16(a, ctrl), w16(a + 4u, pmod), w16(a + 6u, colr), w16(a + 8u, srca), w16(a + 0xau, size);
    for (int i = 0; i < 8; i++) w16(a + 0xcu + 2u * (u32)i, (u32)xy[i] & 0xffffu);
}
/* the erase colour has its MSB set, so a half-transparent polygon drawn twice (by the scan, then the draw) is seen */
static void erase(void) { for (u32 i = 0; i < sizeof sat_v1_fb[0]; i += 2) sat_v1_fb[0][i] = 0x84, sat_v1_fb[0][i + 1] = 0x21; }
static u32 word(const u8 *fb, int x, int y) { u32 o = ((u32)y * 512u + (u32)x) * 2u; return (u32)fb[o] << 8 | fb[o + 1]; }
static void check(int ok, const char *what)
{
    printf("%s: %s\n", ok ? "ok  " : "FAIL", what);
    if (!ok) g_fail = 1;
}

int main(int argc, char **argv)
{
    enum { BLUE = 0xfc00, WHITE = 0xffff, GREEN = 0x83e0, LET = 0x200, OTH = 0x220 };
    static u8 off[0x40000];
    const u16 *mask;
    const u8 *shown;
    int a3 = 0, ok;
    g_threaded = argc > 1 && !strcmp(argv[1], "async");
    w16(0x000, 0x0009), w16(0x014, 319), w16(0x016, 223);                    /* system clip */
    cmd(0x020, 0x0004, 0x00c0, BLUE, 0, 0, 0, 0, 40, 40);                     /* the background */
    cmd(0x040, 0x0004, 0x00c3, 0x801f, 0, 0, 50, 30, 58, 38);                 /* a half-transparent polygon on the erase colour */
    cmd(0x060, 0x0000, 0x00a8, 0, LET, 0x0108, 10, 10, 0, 0);                 /* the lettering: 8x8 RGB */
    cmd(0x080, 0x0000, 0x00a8, 0, OTH, 0x0108, 14, 10, 0, 0);                 /* a sprite over its right half */
    w16(0x0a0, 0x8000);
    for (u32 v = 0; v < 8u; v++)
        for (u32 u = 0; u < 8u; u++) w16(LET * 8u + (v * 8u + u) * 2u, u >= v ? WHITE : 0u), w16(OTH * 8u + (v * 8u + u) * 2u, GREEN);

    g_opt = 0, erase(), sat_v1_draw(), memcpy(off, sat_v1_fb[0], sizeof off); /* Clearer text off: the original */
    g_opt = 1, g_tag = 0, erase(), sat_v1_draw();
    check(!memcmp(off, sat_v1_fb[0], sizeof off), "on, no lettering named: the game's framebuffer is the original");
    shown = sat_text_spr(sat_v1_fb[0], &mask, 320);
    check(shown == sat_v1_fb[0] && !mask, "no lettering named: the framebuffer itself is shown");
    g_tag = 1;
    for (int bands = 0; bands < 2; bands++) {
        sat_v1_bands_set(bands);
        erase(), sat_v1_draw();
        check(!memcmp(off, sat_v1_fb[0], sizeof off), bands ? "in bands: the game's framebuffer is as with Clearer text off" : "the game's framebuffer is as with Clearer text off");
        shown = sat_text_spr(sat_v1_fb[0], &mask, 320);
        check(shown != sat_v1_fb[0] && mask, "lettering named: a copy is shown");
        if (shown == sat_v1_fb[0] || !mask) break;
        check(word(sat_v1_fb[0], 10, 10) == WHITE && word(shown, 10, 10) == BLUE && mask[10 * 512 + 10] == 1, "a lettering dot: back to what was under it, marked");
        check(word(shown, 15, 12) == GREEN && !mask[12 * 512 + 15], "a dot the next sprite drew over: kept");
        check(word(shown, 10, 15) == BLUE && !mask[15 * 512 + 10], "a clear texel: not marked");
        ok = 1;
        for (int y = 0; y < 256; y++)
            for (int x = 0; x < 512; x++) ok &= (word(shown, x, y) == word(off, x, y)) == !mask[y * 512 + x];
        check(ok, "the copy differs from the framebuffer at the marked dots alone");
    }

    /* a compose: the picture blue, white where the lettering was (sat_vdp2.c's colour with it), then posted */
    for (u32 i = 0; i < 320u * 224u; i++) sat_frame[i] = 0x0000ffu, sat_text_with[i] = mask && mask[(i / 320u) * 512u + i % 320u] ? 0xffffffu : 0x0000ffu;
    sat_text_frame(sat_frame, 320, 224, 0), sat_text_post(sat_frame);
    if (g_threaded) {
        check(g_over_n == 0 && sat_frame[10 * 320 + 10] == 0xffffffu && sat_frame[15 * 320 + 10] == 0x0000ffu,
              "a maker thread: the first frame makes no image, the lettering's own dots stand in");
        sat_text_wait();
        for (u32 i = 0; i < 320u * 224u; i++) sat_frame[i] = 0x0000ffu;
        sat_text_frame(sat_frame, 320, 224, 0), sat_text_post(sat_frame);
        check(sat_frame[10 * 320 + 10] == 0x0000ffu, "made: the next frame shows the image, not the dots");
    }
    check(g_over_n == 1 && g_over.x == 10.0f && g_over.y == 10.0f && g_over.w == 9.0f && g_over.h == 9.0f, "one image posted, at the lettering's box");
    if (g_over_n == 1) {
        const PresentImage *im = g_over.img;
        check(im->w == 54u && im->h == 54u && im->px[3 * 54 + 3] == 0xffffffffu, "6 times the box, a lit dot's middle white and whole");
        for (u32 i = 0; i < im->w * im->h; i++) a3 |= (im->px[i] >> 24) > 0u && (im->px[i] >> 24) < 255u;
        check(a3, "the staircase's edge cut into a slope (part-covered pixels)");
        check(im->px[45 * 54 + 3] == 0u, "a clear texel's place: nothing");
    }
    g_opt = 0;
    shown = sat_text_spr(sat_v1_fb[0], &mask, 320);
    sat_text_frame(sat_frame, 320, 224, 0), sat_text_post(sat_frame);
    check(shown == sat_v1_fb[0] && !mask && g_over_n == 0, "the option off: the framebuffer shown and nothing posted");

    puts(g_fail ? "FAILED" : "all ok");
    return g_fail;
}
