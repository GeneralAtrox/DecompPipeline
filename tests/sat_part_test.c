/* A model part the player picks (sat_part.c): a small original in low work RAM (five points, the first two dropped; a
 * polygon on a dropped point and two kept ones) and one version ("fox") that adds three points and a polygon. Checks
 * that the original changes nothing, that the version is put together, rebased and pointed at once the models are
 * loaded, the recording's choice, and a wrong original refused. Run by tests/test_sat_part.py with sat_part_data.h. */
#define _CRT_SECURE_NO_WARNINGS
#include <stdio.h>
#include <string.h>
#include "sat.h"
#include "sat_part.h"

#define PDATA 0x00210000u
#define OBJ 0x00220000u
#define LO 0x02400000u

static u8 g_wl[SAT_WRAM_SIZE], g_wh[SAT_WRAM_SIZE];
static SatRead g_rd;
static u32 g_map_lo, g_maps;
static int g_replay, g_note_in, g_notes;
static char g_note[16];
static int g_fail;

/* a stand-in for each kit and base function sat_part.c calls */
u8 *sat_ram_l(void) { return g_wl; }
u8 *sat_ram_h(void) { return g_wh; }
u32 sat_vblank_count(void) { return 1; }
void port_log(const char *fmt, ...) { printf("log: %s", fmt); }
void sat_map(u32 lo, u32 hi, const char *name, SatRead rd, SatWrite wr)
{
    (void)hi, (void)name, (void)wr;
    g_map_lo = lo, g_rd = rd, g_maps++;
}
int session_replaying(void) { return g_replay; }
int session_note_replayed(const char *name, void *buf, u32 size)
{
    if (!g_note_in || strcmp(name, "*part-choice") || size != 16) return 0;
    memcpy(buf, g_note, 16);
    g_note_in = 0;
    return 1;
}
void session_save_read(const char *name, const void *buf, u32 size, int got)
{
    (void)got;
    if (!strcmp(name, "*part-choice") && size == 16) memcpy(g_note, buf, 16), g_notes++;
}

static void check(int ok, const char *what)
{
    if (!ok) {
        printf("FAIL: %s\n", what);
        g_fail = 1;
    }
}

static void w32(u32 a, u32 v) { u8 *p = g_wl + (a - SAT_WRAM_L_BASE); p[0] = (u8)(v >> 24), p[1] = (u8)(v >> 16), p[2] = (u8)(v >> 8), p[3] = (u8)v; }
static void w16(u32 a, u32 v) { u8 *p = g_wl + (a - SAT_WRAM_L_BASE); p[0] = (u8)(v >> 8), p[1] = (u8)v; }
static u32 r32(u32 a) { return g_rd(a, 4); }
static u32 r16(u32 a) { return g_rd(a, 2); }
static u32 r8(u32 a) { return g_rd(a, 1); }

/* the original: points (0..4), polygons [0 1 2 2] [2 3 4 4] [4 3 2 2], stored normals along z (or against: flip) */
static void load_models(int flip, u32 npt)
{
    static const s32 pts[5][3] = {{7, 7, 7}, {8, 8, 8}, {0, 0, 0}, {65536, 0, 0}, {0, 65536, 0}};
    static const u32 v[3][4] = {{0, 1, 2, 2}, {2, 3, 4, 4}, {4, 3, 2, 2}};
    const u32 pnt = PDATA + 0x100u, plt = PDATA + 0x200u, att = PDATA + 0x300u;
    w32(PDATA, pnt), w32(PDATA + 4, npt), w32(PDATA + 8, plt), w32(PDATA + 12, 3), w32(PDATA + 16, att);
    for (u32 i = 0; i < 5; i++)
        for (u32 k = 0; k < 3; k++) w32(pnt + 12 * i + 4 * k, (u32)pts[i][k]);
    for (u32 i = 0; i < 3; i++) {
        w32(plt + 20 * i, 0), w32(plt + 20 * i + 4, 0), w32(plt + 20 * i + 8, (u32)((i == 2) != flip ? -65536 : 65536));
        for (u32 j = 0; j < 4; j++) w16(plt + 20 * i + 12 + 2 * j, v[i][j]);
        w32(att + 12 * i, i ? 0x00050000u : 0x00090001u), w32(att + 12 * i + 4, i ? 0x00408000u : 0x00c08015u), w32(att + 12 * i + 8, 0x00070004u);
    }
    w32(OBJ, PDATA);
}

static void the_version(s32 nz)
{
    static const s32 add[3][3] = {{10, 11, 12}, {20, 21, 22}, {30, 31, 32}};
    const u32 lo = LO + 20 + 12 * 6, ao = lo + 20 * 3;
    check(r32(LO) == LO + 20 && r32(LO + 4) == 6 && r32(LO + 8) == lo && r32(LO + 12) == 3 && r32(LO + 16) == ao, "the header rebased");
    check(r32(LO + 20) == 0 && r32(LO + 20 + 12 + 0) == 65536 && r32(LO + 20 + 24 + 4) == 65536, "the original's points from the third on");
    for (u32 i = 0; i < 3; i++)
        for (u32 k = 0; k < 3; k++) check(r32(LO + 20 + 36 + 12 * i + 4 * k) == (u32)add[i][k], "the version's points after them");
    check(r32(lo) == 0 && r32(lo + 4) == 0 && r32(lo + 8) == (u32)nz, "the first kept polygon's normal worked out");
    check(r32(lo + 28) == (u32)-nz, "the second kept polygon's normal worked out");
    check(r16(lo + 12) == 0 && r16(lo + 14) == 1 && r16(lo + 16) == 2 && r16(lo + 18) == 2, "the kept polygon renumbered");
    check(r16(lo + 32) == 2 && r16(lo + 34) == 1 && r16(lo + 36) == 0, "the second renumbered");
    check(r32(lo + 40) == 1 && r32(lo + 44) == 2 && r32(lo + 48) == 3 && r16(lo + 52) == 3 && r16(lo + 54) == 4 && r16(lo + 56) == 5,
          "the version's polygon as made");
    for (u32 i = 0; i < 3; i++) {
        u32 a = ao + 12 * i;
        check(r8(a + 1) == 9 && r16(a + 2) == 1 && r16(a + 4) == 0xc0 && r16(a + 8) == 7 && r16(a + 10) == 4, "the attributes the original's first");
        check(r8(a) == (i == 2 ? 1u : 0u), "the flag: the version's on its polygon, the original's on the kept");
        check(r16(a + 6) == (i == 0 ? 0x1111u : i == 1 ? 0x2222u : 0x3333u), "the colours from the version");
    }
}

int main(void)
{
    /* the original: nothing mapped, nothing written, nothing recorded */
    load_models(0, 5);
    sat_part_start();
    sat_part_frame();
    check(g_maps == 0 && g_wl[OBJ - SAT_WRAM_L_BASE + 3] == 0 && g_notes == 0, "the original changes nothing");
    sat_part_option("nope");
    sat_part_start();
    sat_part_frame();
    check(g_maps == 0 && g_notes == 0, "an unknown name keeps the original");

    /* the version: not before the models are loaded, then once */
    memset(g_wl, 0, sizeof g_wl);
    sat_part_option("fox");
    sat_part_start();
    check(g_notes == 1 && !strcmp(g_note, "fox"), "the choice recorded");
    sat_part_frame();
    check(g_maps == 0, "nothing before the models are loaded");
    load_models(0, 5);
    sat_part_frame();
    check(g_maps == 1 && g_map_lo == LO, "the RAM mapped where a cartridge's sits");
    check(g_wl[OBJ - SAT_WRAM_L_BASE] == 0x02 && g_wl[OBJ - SAT_WRAM_L_BASE + 1] == 0x40, "the OBJECT pointed at the version");
    if (g_rd) the_version(65536);
    sat_part_frame();
    check(g_maps == 1, "mapped once");

    /* the original's normals pointing the other way: so do the version's */
    load_models(1, 5);
    sat_part_frame();
    if (g_rd) the_version(-65536);

    /* a replay: the recording's choice over the asked one; none recorded is the original */
    sat_part_option("original");
    g_replay = 1, g_note_in = 1, g_notes = 0;
    strcpy(g_note, "fox");
    sat_part_start();
    load_models(0, 5);
    sat_part_frame();
    check(g_wl[OBJ - SAT_WRAM_L_BASE] == 0x02, "a replay takes the recording's choice");
    sat_part_option("fox");
    sat_part_start();
    load_models(0, 5);
    sat_part_frame();
    check(g_wl[OBJ - SAT_WRAM_L_BASE] == 0x00, "a replay with no choice recorded has the original");
    g_replay = 0;

    /* not the original game.json names: left alone */
    sat_part_start();
    load_models(0, 6);
    sat_part_frame();
    check(g_wl[OBJ - SAT_WRAM_L_BASE] == 0x00, "a different original refused");
    printf(g_fail ? "failed\n" : "all ok\n");
    return g_fail;
}
