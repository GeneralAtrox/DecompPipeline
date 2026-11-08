/* VDP2's rotation plane RBG0 (sat_vdp2_rot.c through the composer sat_vdp2.c): programs the registers, a map, cells and a
 * rotation parameter table as Robo Pit's fight does (256 colours, 16x16 cells, 1-word names, 2x2-page planes, over mode
 * 2) and reads dots of the composed picture, each composed in one band and in four (base/bands.c). Run by
 * tests/test_sat_vdp2_rot.py. */
#define _CRT_SECURE_NO_WARNINGS
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "sat.h"
#include "sat_sys.h"
#include "sat_vdp.h"
#include "work_count.h"
#include "bands.h"

u8 sat_v1_vram[0x80000], sat_v1_fb[2][0x40000], sat_v2_vram[0x80000], sat_v2_cram[0x1000], sat_v1_reg[0x20], sat_v2_reg[0x200];
u32 sat_vwatch_lo, sat_vwatch_hi, sat_vwatch_vb;
int g_work_on;
u64 g_work_now[WC_N];
const u8 *sat_v1_display_fb(void) { return sat_v1_fb[0]; }
u32 sat_vblank_count(void) { return 0; }
void port_log(const char *fmt, ...) { (void)fmt; }
void sat_v2_dump_regs(void) {}
/* sat_wide.c's side of the composer: g_wide dots more each side when set */
static u32 g_wide;
int sat_wide_now;
u32 sat_wide_sw;
u32 sat_wide_width(u32 w) { sat_wide_now = (int)g_wide, sat_wide_sw = w; return w + 2u * g_wide; }
const u8 *sat_wide_spr(const u8 *guest, u32 *shift, u32 *ox, u32 *mask) { *shift = 9u, *ox = 0u, *mask = 0x3ffffu; return guest; }
void port_exit(const char *why, int code)
{
    printf("FAIL: %s\n", why);
    exit(code ? code : 1);
}

enum { RED = 0xff0000, GREEN = 0x00ff00, YELLOW = 0xffff00, BLUE = 0x0000ff, TABLE = 0x3fe00, COEF = 0x20000 };
static int g_fail;

static void reg(u32 o, u32 v) { sat_v2_reg[o] = (u8)(v >> 8); sat_v2_reg[o + 1] = (u8)v; }
static void w16(u32 a, u32 v) { sat_v2_vram[a] = (u8)(v >> 8); sat_v2_vram[a + 1] = (u8)v; }
static void w32(u32 a, u32 v) { w16(a, v >> 16); w16(a + 2, v & 0xffffu); }
static void cram(u32 i, u32 c555) { sat_v2_cram[i * 2] = (u8)(c555 >> 8); sat_v2_cram[i * 2 + 1] = (u8)c555; }
static u32 dot(u32 x, u32 y) { return sat_frame[y * sat_frame_w + x]; }
static u32 g_one[SAT_MAX_W * SAT_MAX_H];
static void compose(void);
static void check(int ok, const char *what);
/* composed again W dots wider each side: the screen's dots the same in its middle; dot (x, y) left of the screen is
 * wide_dot(W - n) for x = -n */
static u32 g_narrow[SAT_MAX_W * SAT_MAX_H], g_nw;
static void widen(u32 w, const char *what)
{
    int same = 1;
    compose();
    memcpy(g_narrow, sat_frame, sizeof g_narrow), g_nw = sat_frame_w;
    g_wide = w;
    compose();
    g_wide = 0;
    for (u32 y = 0; y < sat_frame_h; y++)
        for (u32 x = 0; x < g_nw; x++) same &= g_narrow[y * g_nw + x] == dot(x + w, y);
    check(sat_frame_w == g_nw + 2u * w && same, what);
}

static void check(int ok, const char *what)
{
    printf("%s: %s\n", ok ? "ok  " : "FAIL", what);
    if (!ok) g_fail = 1;
}

/* each picture composed in one band and again in four (base/bands.c), over a filled frame: the bands must give it whole and
 * the same */
static void compose(void)
{
    bands_set(1);
    sat_compose();
    memcpy(g_one, sat_frame, sizeof g_one);
    memset(sat_frame, 0x5a, sizeof sat_frame);
    bands_set(4);
    sat_compose();
    if (memcmp(g_one, sat_frame, sat_frame_w * sat_frame_h * 4u)) check(0, "four bands give the picture one band gives");
}

/* the table: identity rotation, one map dot per screen dot; then the fields a case changes */
static void table(void)
{
    memset(sat_v2_vram + TABLE, 0, 0x60);
    w32(TABLE + 0x10, 0x10000u); /* dYst 1.0: the next line is the next map row */
    w32(TABLE + 0x14, 0x10000u); /* dX 1.0 */
    w32(TABLE + 0x1c, 0x10000u); /* A */
    w32(TABLE + 0x2c, 0x10000u); /* E */
    w32(TABLE + 0x4c, 0x10000u); /* kx */
    w32(TABLE + 0x50, 0x10000u); /* ky */
}

static void setup(void)
{
    memset(sat_v2_reg, 0, sizeof sat_v2_reg);
    memset(sat_v2_vram, 0, sizeof sat_v2_vram);
    reg(0x00, 0x8000);         /* TVMD: display on, 320x224 */
    reg(0x20, 0x0010);         /* BGON: RBG0 only */
    reg(0x2a, 0x1100);         /* CHCTLB: RBG0 256 colours, 16x16 cells */
    reg(0x38, 0x8000);         /* PNCR: 1-word names */
    reg(0x3a, 0x0b00);         /* PLSZ: 2x2-page planes, over mode 2 (transparent outside the map) */
    reg(0x3e, 0x0002);         /* MPOFR: maps from 0x40000 */
    reg(0x52, 0x0004);         /* plane C is the all-green one at 0x42000; the rest are plane A */
    reg(0xfc, 0x0003);         /* PRIR */
    reg(0xbc, 0x0001);         /* RPTA */
    reg(0xbe, 0xff00);
    reg(0xac, 0x0003);         /* the back screen's colour at 0x7fffe */
    reg(0xae, 0xffff);
    w16(0x7fffe, 0x7c00u);
    cram(5, 0x001f), cram(6, 0x03e0), cram(7, 0x03ff);
    for (u32 i = 0; i < 256; i++) sat_v2_vram[0x400 + i] = 5, sat_v2_vram[0x800 + i] = 6, sat_v2_vram[0xc00 + i] = 7;
    for (u32 cy = 0; cy < 32; cy++)
        for (u32 cx = 0; cx < 32; cx++) {
            for (u32 pg = 0; pg < 4; pg++) /* plane A, each page: red | green over yellow */
                w16(0x40000 + pg * 0x800 + (cy * 32 + cx) * 2, cy >= 8 ? 24 : cx >= 16 ? 16 : 8);
            w16(0x42000 + (cy * 32 + cx) * 2, 16);
        }
    table();
}

int main(void)
{
    setup();
    compose();
    check(dot(10, 10) == RED && dot(300, 10) == GREEN && dot(10, 150) == YELLOW, "identity: the map as it is");
    w32(TABLE + 0x44, 100u << 16); /* Mx 100: 100 dots of the map left of the screen */
    widen(24, "widened 24 each side: the screen in the middle as it was");
    check(dot(0, 10) == RED && dot(24 + 330, 10) == GREEN, "widened: RBG0 carries on left and right of the screen");
    w32(TABLE + 0x4c, 0x8000u); /* kx 0.5: an x read unsigned lands 2^31 dots off the map (at 1.0 it wraps back) */
    widen(24, "widened at kx 0.5: the screen in the middle as it was");
    check(dot(0, 10) == RED && dot(24 + 330, 10) == GREEN, "widened at kx 0.5: RBG0's X left and right of the screen");
    w32(TABLE + 0x4c, 0x10000u), w32(TABLE + 0x28, 0x8000u); /* D 0.5: Y half a dot down per dot right */
    widen(24, "widened and sheared: the screen in the middle as it was");
    check(dot(0, 40) == RED && dot(24 + 330, 40) == YELLOW, "widened and sheared: RBG0's Y left and right of the screen");
    table();

    w32(TABLE + 0x44, (u32)-100 << 16); /* Mx -100: the left 100 dots fall off the map */
    compose();
    check(dot(50, 10) == BLUE && dot(150, 10) == RED, "over mode 2: transparent off the map, the back screen shows");

    table();
    w32(TABLE + 0x1c, 0x20000u);          /* A 2.0 and Cx -100: Xp = 2 (Px - Cx) + Cx = 100, X = 2H + 100 */
    w16(TABLE + 0x3c, (u32)-100 & 0x3fffu);
    compose();
    check(dot(10, 10) == RED && dot(100, 10) == GREEN, "the matrix and a negative Cx (14 bits, signed)");

    table();
    w32(TABLE + 0x44, 2048u << 16); /* Mx 2048: plane C of the 4x4 map */
    compose();
    check(dot(10, 10) == GREEN && dot(10, 150) == GREEN, "the map is 4x4 planes");

    table();
    w32(TABLE + 0x4c, 0x20000u); /* kx 2.0 */
    compose();
    check(dot(130, 10) == GREEN && dot(120, 10) == RED, "the scale kx");

    table();
    reg(0xb4, 0x0001);                   /* KTCTL: coefficients on, 2 words, as kx and ky */
    w32(TABLE + 0x54, 0x80000000u);      /* KAst 0x8000: coefficient 0 at 0x20000 */
    w32(TABLE + 0x58, 0x10000u);         /* dKAst 1.0: one coefficient per line */
    for (u32 v = 0; v < 224; v++) w32(COEF + v * 4, v < 50 ? 0x10000u : v < 60 ? 0x80000000u : 0x20000u);
    compose();
    check(dot(130, 10) == RED && dot(130, 55) == BLUE && dot(130, 60) == GREEN, "the coefficient table: scale, then transparent");
    w32(TABLE + 0x58, 0), w32(TABLE + 0x5c, 0x10000u); /* dKAx 1.0 instead: one coefficient per dot along the line */
    compose();
    check(dot(10, 10) == RED && dot(55, 10) == BLUE && dot(130, 10) == GREEN, "the coefficient table along a line (dKAx)");
    reg(0xb4, 0);

    table();
    reg(0xc0, 40), reg(0xc2, 10), reg(0xc4, 118), reg(0xc6, 19); /* window 0: dots 20..59 (half dots), lines 10..19 */
    reg(0xd4, 0x0003);                   /* WCTLC: RBG0 through window 0, hidden outside it (area 1) */
    compose();
    check(dot(30, 15) == RED && dot(10, 15) == BLUE && dot(30, 5) == BLUE && dot(30, 25) == BLUE, "a window: shown inside only");
    reg(0xc0, 0), reg(0xc4, 638); /* window 0 the screen's whole width: widened, it reaches the wide edges */
    w32(TABLE + 0x44, 100u << 16);
    widen(24, "widened through a window: the screen as it was");
    check(dot(0, 15) == RED && dot(367, 15) == GREEN && dot(0, 5) == BLUE && dot(23, 5) == BLUE, "widened: a window at the screen's edges reaches the wide edges");
    reg(0xc0, 40), reg(0xc4, 118);
    table();
    reg(0xd4, 0x0002);                   /* area 0: hidden inside */
    compose();
    check(dot(30, 15) == BLUE && dot(10, 15) == RED && dot(30, 25) == RED, "a window's area bit: hidden inside");
    reg(0xd8, 0x8000), reg(0xda, 0x3000); /* line window table at 0x6000: line y shows dots 0..y (half dots 0..2y) */
    for (u32 v = 0; v < 224; v++) w16(0x6000 + v * 4, 0), w16(0x6002 + v * 4, v * 2);
    reg(0xd4, 0x0003);
    compose();
    check(dot(12, 15) == RED && dot(16, 15) == BLUE && dot(3, 12) == RED && dot(14, 12) == BLUE && dot(3, 5) == BLUE, "the line window table, within the window's lines");
    reg(0xd4, 0), reg(0xd8, 0);

    reg(0x20, 0x0001);                   /* NBG0 only: a 512x256 256-colour bitmap at 0, red left of x 64, green after */
    reg(0x28, 0x0012), reg(0xf8, 0x0004), reg(0x70, 4);
    for (u32 v = 0; v < 256; v++)
        for (u32 u = 0; u < 512; u++) sat_v2_vram[v * 512 + u] = u < 64 ? 5 : 6;
    compose();
    check(dot(0, 50) == RED && dot(0, 150) == RED && dot(61, 50) == GREEN, "NBG0's bitmap scrolled by 4");
    widen(24, "NBG0 widened: the screen as it was");
    check(dot(0, 50) == GREEN && dot(24, 50) == RED, "widened: NBG0 wraps left of the screen (x -24 is the bitmap's 492)");
    reg(0x9a, 0x0012), reg(0xa0, 0x0001), reg(0xa2, 0x8000); /* line scroll X every 2 lines from 0x30000 */
    for (u32 v = 0; v < 112; v++) w32(0x30000 + v * 4, (v * 2 < 100 ? 0u : 60u) << 16);
    compose();
    check(dot(0, 50) == RED && dot(0, 150) == GREEN && dot(61, 50) == GREEN, "line scroll X adds to the scroll register");
    for (u32 v = 0; v < 112; v++) w32(0x30000 + v * 4, (v == 25 ? 60u : 0u) << 16);
    compose();
    check(dot(0, 50) == GREEN && dot(0, 51) == GREEN && dot(0, 49) == RED && dot(0, 52) == RED, "one entry per 2 lines (LSS 1)");
    reg(0x9a, 0);

    table();
    reg(0x20, 0x0000);
    compose();
    check(dot(10, 10) == BLUE, "RBG0 off: the back screen");
    printf(g_fail ? "FAIL\n" : "all ok\n");
    return g_fail;
}
