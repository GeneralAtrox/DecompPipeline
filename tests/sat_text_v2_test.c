/* Clearer text for VDP2's lettering (sat_text_v2.c through the composer sat_vdp2.c, with sat_text.c and sat_text_a3.c):
 * NBG0 (8x8 cells, 16 colours, 1-word names) holds a staircase cell twice side by side, the same staircase stored mirrored
 * and shown through the name's flip bit, and a solid cell the game does not name; NBG1 under it is blue. With Clearer text
 * off the picture is the original and nothing is posted; on, the lettering's dots (and only those) show what is under them,
 * the colour with the lettering is kept, and two images are posted (the two cells side by side as one block, the flipped
 * cell), made by A3. VDP2's memory is only read. With "async" (a play run's maker thread) the first picture makes no image
 * and shows the lettering's own dots. Then NBG0 is made a 16-colour bitmap with the staircase drawn into it twice side by
 * side and a solid tile: the same, one image. Run by tests/test_sat_text.py. */
#define _CRT_SECURE_NO_WARNINGS
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "sat.h"
#include "sat_sys.h"
#include "sat_vdp.h"
#include "work_count.h"
#include "present_cpu.h"

u8 sat_v1_vram[0x80000], sat_v1_fb[2][0x40000], sat_v2_vram[0x80000], sat_v2_cram[0x1000], sat_v1_reg[0x20], sat_v2_reg[0x200];
u32 sat_vwatch_lo, sat_vwatch_hi, sat_vwatch_vb;
int g_work_on;
u64 g_work_now[WC_N];
static int g_fail, g_opt, g_threaded;
static unsigned g_ov_n;
static PresentOverlay g_ov[16];
static u8 g_stair[32];

/* the game's hook: a cell whose texels are the staircase is lettering (tag 5); a block keeps its cells' tag */
static int g_screen = 1;
static int cells(const SatTextCells *c)
{
    if (!c) return g_screen;
    if (c->tag) return c->tag;
    return c->cc == 0 && c->cs == 8 && !memcmp(c->tex, g_stair, sizeof g_stair) ? 5 : 0;
}
const SatGameHooks sat_game_table = {.text_cells = cells};

/* a stand-in for each kit function the files call */
void port_log(const char *fmt, ...) { (void)fmt; }
u32 sat_vblank_count(void) { return 1; }
void sat_v2_dump_regs(void) {}
const u8 *sat_v1_display_fb(void) { return sat_v1_fb[0]; }
int sat_wide_now;
u32 sat_wide_sw;
u32 sat_wide_width(u32 w) { sat_wide_sw = w; return w; }
const u8 *sat_wide_spr(const u8 *guest, u32 *shift, u32 *ox, u32 *mask) { *shift = 9u, *ox = 0u, *mask = 0x3ffffu; return guest; }
int sat_wide_fb_ox(const u8 *fb) { (void)fb; return 0; }
int sat_panel_frame(void) { return 0; }
u32 sat_panel_dot(u32 (*word)(u32 x, u32 y), u32 x, u32 y, u32 rgb, int spr_pr, int pn, u32 under, u32 left) { (void)word, (void)x, (void)y, (void)spr_pr, (void)pn, (void)under, (void)left; return rgb; }
void port_exit(const char *why, int code) { printf("FAIL: %s\n", why); exit(code ? code : 1); }
int port_option(const char *id) { return strcmp(id, "clear-text") ? -1 : g_opt; }
int sat_video_headless(void) { return 0; }
int host_fixed_clock(void) { return 0; }
void cpu_present_view(PortView *v) { memset(v, 0, sizeof *v); }
int cpu_present_threaded(void) { return g_threaded; }
void cpu_present_prepare(PresentImage *im) { (void)im; }
void cpu_present_unprepare(PresentImage *im) { (void)im; }
void cpu_present_overlay(const PresentOverlay *o, unsigned n) { g_ov_n = n; memcpy(g_ov, o, (n < 16u ? n : 16u) * sizeof *o); }
int sat_png_write(const char *path, const u32 *rgb, u32 w, u32 h) { (void)path, (void)rgb, (void)w, (void)h; return 1; }

static void reg(u32 o, u32 v) { sat_v2_reg[o] = (u8)(v >> 8), sat_v2_reg[o + 1] = (u8)v; }
static void w16(u32 a, u32 v) { sat_v2_vram[a] = (u8)(v >> 8), sat_v2_vram[a + 1] = (u8)v; }
static void cram(u32 i, u32 c555) { sat_v2_cram[i * 2] = (u8)(c555 >> 8), sat_v2_cram[i * 2 + 1] = (u8)c555; }
static u32 D(u32 x, u32 y) { return sat_frame[y * sat_frame_w + x]; }
static void compose(void) { sat_compose(), sat_text_post(sat_frame); }
static void check(int ok, const char *what)
{
    printf("%s: %s\n", ok ? "ok  " : "FAIL", what);
    if (!ok) g_fail = 1;
}
/* a 4-bit cell at character number ch: dot (u, v) is 1 where on(u, v) */
static void cell(u32 ch, int (*on)(int u, int v))
{
    for (int v = 0; v < 8; v++)
        for (int u = 0; u < 8; u++)
            if (on(u, v)) sat_v2_vram[ch * 0x20u + (u32)v * 4u + (u32)u / 2u] |= (u8)(u & 1 ? 1 : 0x10);
}
static int stair(int u, int v) { return u >= v; }
static int mirror(int u, int v) { return 7 - u >= v; }
static int solid(int u, int v) { (void)u, (void)v; return 1; }
static int lettering2(u32 x, u32 y) { return y >= 64 && y < 72 && x >= 16 && x < 32 && (x & 7u) >= (y & 7u); }
static int lettering(u32 x, u32 y) { return ((y >= 16 && y < 24 && x >= 16 && x < 32) || (y >= 40 && y < 48 && x >= 16 && x < 24)) && (x & 7u) >= (y & 7u); }

int main(int argc, char **argv)
{
    enum { WHITE = 0xffffff, BLUE = 0x0000ff, NB0 = 0x2000, NB1 = 0x4000 };
    static u32 off[320 * 224];
    static u8 vram0[0x80000];
    int ok = 1;
    g_threaded = argc > 1 && !strcmp(argv[1], "async");
    for (int v = 0; v < 8; v++)
        for (int u = 0; u < 8; u++) g_stair[v * 4 + u / 2] |= (u8)(stair(u, v) ? (u & 1 ? 1 : 0x10) : 0);
    reg(0x00, 0x8000);         /* display on, 320 x 224 */
    reg(0x20, 0x0003);         /* NBG0, NBG1 on */
    reg(0x30, 0x8000), reg(0x32, 0x8000); /* 1-word names */
    reg(0x40, 0x0101), reg(0x42, 0x0101); /* NBG0's map: page 1 (0x2000) */
    reg(0x44, 0x0202), reg(0x46, 0x0202); /* NBG1's: page 2 (0x4000) */
    reg(0xf8, 0x0307);         /* NBG0 priority 7 over NBG1's 3 */
    cram(1, 0x7fff), cram(17, 0x7c00); /* palette 0 pen 1 white, palette 1 pen 1 blue */
    cell(0x10, stair), cell(0x11, mirror), cell(0x12, solid), cell(0x13, solid);
    w16(NB0 + (2 * 64 + 2) * 2, 0x0010), w16(NB0 + (2 * 64 + 3) * 2, 0x0010); /* the staircase twice side by side */
    w16(NB0 + (5 * 64 + 2) * 2, 0x0411);                                       /* the mirrored one, shown flipped */
    w16(NB0 + (2 * 64 + 6) * 2, 0x0012);                                       /* a solid cell, not lettering */
    for (u32 i = 0; i < 64u * 64u; i++) w16(NB1 + i * 2u, 0x1013);             /* blue under it all */

    g_opt = 0, compose(), memcpy(off, sat_frame, sizeof off);
    check(D(16, 16) == WHITE && D(31, 16) == WHITE && D(16, 17) == BLUE && D(48, 16) == WHITE && D(16, 40) == WHITE && D(23, 40) == WHITE && g_ov_n == 0,
          "off: the original lettering, nothing posted");
    memcpy(vram0, sat_v2_vram, sizeof vram0);
    g_opt = 1, compose();
    if (g_threaded) {
        check(g_ov_n == 0 && D(16, 16) == WHITE && !memcmp(sat_frame, off, sizeof off), "a maker thread: the first picture makes no image, the lettering's own dots stand in");
        sat_text_wait(), compose();
    }
    check(!memcmp(vram0, sat_v2_vram, sizeof vram0), "VDP2's memory only read");
    check(D(16, 16) == BLUE && D(24, 16) == BLUE && D(16, 40) == BLUE, "the lettering cells' dots show what is under them (the flipped one too)");
    check(D(48, 16) == WHITE, "a cell the game does not name: kept");
    check(sat_text_with[16 * 320 + 16] == WHITE && sat_text_with[40 * 320 + 23] == WHITE, "the colour with the lettering kept");
    for (u32 y = 0; y < 224u; y++)
        for (u32 x = 0; x < 320u; x++) ok &= (D(x, y) != off[y * 320u + x]) == lettering(x, y) && (!lettering(x, y) || D(x, y) == BLUE);
    check(ok, "the picture differs from the original at the lettering's dots alone");
    check(g_ov_n == 2 && g_ov[0].x == 16.0f && g_ov[0].y == 16.0f && g_ov[0].w == 16.0f && g_ov[0].h == 8.0f && g_ov[1].x == 16.0f && g_ov[1].y == 40.0f && g_ov[1].w == 8.0f,
          "two images posted: the cells side by side as one block, the flipped cell");
    if (g_ov_n >= 1) {
        const PresentImage *im = g_ov[0].img;
        int a3 = 0;
        for (u32 i = 0; i < im->w * im->h; i++) a3 |= (im->px[i] >> 24) > 0u && (im->px[i] >> 24) < 255u;
        check(im->w == 96u && im->h == 48u && im->px[3 * 96 + 3] == 0xffffffffu && im->px[45 * 96 + 3] == 0u && a3, "6 times the block, A3: whole where lit, nothing where clear, a slope between");
    }
    g_opt = 0, compose();
    check(!memcmp(sat_frame, off, sizeof off) && g_ov_n == 0, "off again: the original exactly, nothing posted");

    reg(0x28, 0x0002), reg(0x3c, 0x0002); /* NBG0 a 16-colour bitmap, 512 x 256 at 0x40000 */
    for (u32 t = 0; t < 3u; t++)
        for (u32 v = 0; v < 8u; v++)
            for (u32 u = 0; u < 8u; u++) {
                u32 x = (t == 2u ? 48u : 16u + 8u * t) + u, y = 64u + v;
                if (t == 2u || stair((int)u, (int)v)) sat_v2_vram[0x40000u + (y * 512u + x) / 2u] |= (u8)(x & 1u ? 1 : 0x10);
            }
    g_opt = 0, compose(), memcpy(off, sat_frame, sizeof off);
    check(D(16, 64) == WHITE && D(16, 65) == BLUE && D(31, 64) == WHITE && D(48, 64) == WHITE && D(16, 16) == BLUE && g_ov_n == 0, "bitmap, off: the original");
    g_opt = 1, compose();
    if (g_threaded) sat_text_wait(), compose();
    ok = 1;
    for (u32 y = 0; y < 224u; y++)
        for (u32 x = 0; x < 320u; x++) ok &= (D(x, y) != off[y * 320u + x]) == lettering2(x, y) && (!lettering2(x, y) || D(x, y) == BLUE);
    check(ok && D(48, 64) == WHITE && sat_text_with[64 * 320 + 16] == WHITE, "bitmap: its lettering's dots alone show what is under them, the colour with them kept");
    check(g_ov_n == 1 && g_ov[0].x == 16.0f && g_ov[0].y == 64.0f && g_ov[0].w == 16.0f && g_ov[0].h == 8.0f, "bitmap: one image, the two tiles side by side");
    g_screen = 0, compose(), g_screen = 1;
    check(!memcmp(sat_frame, off, sizeof off) && g_ov_n == 0, "bitmap, on, the game says nothing to look at: the original exactly");
    g_opt = 0, compose();
    check(!memcmp(sat_frame, off, sizeof off) && g_ov_n == 0, "bitmap, off again: the original exactly");
    puts(g_fail ? "FAILED" : "all ok");
    return g_fail;
}
