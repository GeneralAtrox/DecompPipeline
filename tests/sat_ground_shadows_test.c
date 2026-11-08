/* VDP1's floor shadows (sat_vdp1_list.c with sat_vdp1.c): a list as Robo Pit draws a robot's foot, then a shadow quad
 * reached through a jump that the game sorted after it, then a mesh sprite that is not a shadow (the radar), under a
 * local-coordinate offset. In the game's order the shadow's mesh darkens the foot; with the floor shadows first it does
 * not, and the radar still draws last. Run by tests/test_sat_ground_shadows.py. */
#define _CRT_SECURE_NO_WARNINGS
#include <stdio.h>
#include <string.h>
#include "sat.h"
#include "sat_vdp.h"

u8 sat_v1_vram[0x80000], sat_v1_fb[2][0x40000];
int g_work_on;
u64 g_work_now[64];
static int g_fail;

/* a stand-in for each kit function the two files call */
void port_log(const char *fmt, ...) { (void)fmt; }
u32 sat_vblank_count(void) { return 1; }
void sat_perf_phase(int phase) { (void)phase; }
void sat_perf_v1(long long ticks) { (void)ticks; }
u8 *sat_v1_draw_fb(void) { return sat_v1_fb[0]; }
u32 sat_wide_sw = 320;
u8 *sat_wide_fb(const u8 *guest) { (void)guest; return NULL; }
int sat_wide_fb_ox(const u8 *fb) { (void)fb; return 0; }
void sat_wide_back(u8 *guest) { (void)guest; }

static void w16(u32 a, u32 v) { sat_v1_vram[a] = (u8)(v >> 8), sat_v1_vram[a + 1] = (u8)v; }

/* a command at a: ctrl, jump target, draw mode, colour, texture, size and four corners */
static void cmd(u32 a, u32 ctrl, u32 to, u32 pmod, u32 colr, u32 srca, u32 size, int x0, int y0, int x1, int y1)
{
    int xy[8] = {x0, y0, x1, y0, x1, y1, x0, y1};
    w16(a, ctrl), w16(a + 2u, to / 8u), w16(a + 4u, pmod), w16(a + 6u, colr), w16(a + 8u, srca), w16(a + 0xau, size);
    for (int i = 0; i < 8; i++) w16(a + 0xcu + 2u * (u32)i, (u32)xy[i] & 0xffffu);
}

/* an 8x8 texture of one RGB colour at srca */
static void tex(u32 srca, u32 c)
{
    for (u32 i = 0; i < 64u; i++) w16(srca * 8u + 2u * i, c);
}

static u32 px(int x, int y)
{
    u32 o = ((u32)y * 512u + (u32)x) * 2u;
    return (u32)sat_v1_fb[0][o] << 8 | sat_v1_fb[0][o + 1];
}

static void check(int ok, const char *what)
{
    printf("%s: %s\n", ok ? "ok  " : "FAIL", what);
    if (!ok) g_fail = 1;
}

static void draw(int under)
{
    memset(sat_v1_fb, 0, sizeof sat_v1_fb);
    sat_v1_ground_shadows = under;
    (void)sat_v1_run(sat_v1_fb[0]);
}

int main(void)
{
    enum { RED = 0x801f, DARK = 0x8421, GREEN = 0x83e0 };
    w16(0x000, 0x0009), w16(0x014, 319), w16(0x016, 223);                  /* system clip */
    w16(0x020, 0x000a), w16(0x02c, 8), w16(0x02e, 8);                      /* local coordinates 8,8 */
    cmd(0x040, 0x1004, 0x400, 0x00c0, RED, 0, 0, 10, 10, 20, 20);          /* the foot, then a jump to 0x400 */
    cmd(0x400, 0x1002, 0x060, 0x0128, 0, 0xa4ec, 0x0108, 5, 5, 25, 25);    /* the floor shadow, back to 0x060 */
    cmd(0x060, 0x0002, 0, 0x0128, 0, 0x2dd4, 0x0108, 11, 11, 13, 13);      /* a mesh sprite of another texture */
    w16(0x080, 0x8000);                                                    /* end */
    tex(0xa4ec, DARK);
    tex(0x2dd4, GREEN);

    draw(0);
    check(px(24, 24) == DARK, "the game's order: the shadow's mesh darkens the foot");
    check(px(14, 14) == DARK && px(30, 30) == DARK, "the game's order: the shadow on the floor");
    draw(1);
    check(px(24, 24) == RED && px(25, 24) == RED, "shadows under: the foot is whole");
    check(px(14, 14) == DARK && px(30, 30) == DARK, "shadows under: the same shadow on the floor, at the list's local coordinates");
    check(px(20, 20) == GREEN, "shadows under: a mesh sprite of another texture keeps its place, over the foot");

    puts(g_fail ? "FAILED" : "all ok");
    return g_fail;
}
