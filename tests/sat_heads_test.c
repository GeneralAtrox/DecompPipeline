/* New head types (sat_heads.c) on a small game: four robots' records, original types 1..6 (6 the last) with 5 the host,
 * and three new types (TYPE G cat, TYPE H dog, TYPE A1 owl). The game's steps right and left are played here as Robo Pit's
 * are (the next or previous offered type, round at the ends). Checks the order (A..F, G, H, A1, A and back), the host
 * part swapped only while a robot with a new type is built, the panel's name and its exact restore, the kit's save
 * (written, read, a renamed robot not taken, a write before the game's read leaving it), ROBOT MAKING dropping a kept
 * type, and the start switch. Run by tests/test_sat_heads.py with a sat_game.h from gen_saturn_heads.py. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "sat.h"
#include "sat_part.h"

#define REC 0x060a0000u
#define NAMES 0x060b0000u
#define AVAIL 0x060c0000u
#define FIELD(i) (REC + (i) * 90u + 2u)
#define OBJ 0x00220000u
#define HLO 0x02410000u
enum { SETUP = 0x06001000, RING = 0x06001100, NODE = 0x06001200, NEXT = 0x06001300, PREV = 0x06001400, PANEL = 0x06001500, MAKING = 0x06001600,
       RNODE = 0x06001700, COUNT = 0x06001800 };

static u8 g_wl[SAT_WRAM_SIZE], g_wh[SAT_WRAM_SIZE];
static SatRead g_rd;
static int g_replay, g_note_in = -1, g_notes, g_composed, g_fail;
static const char *g_save = "t_heads_save.bin";

static u8 *at(u32 a) { return a >> 24 == 6u ? g_wh + (a & (SAT_WRAM_SIZE - 1u)) : g_wl + (a - SAT_WRAM_L_BASE); }
u32 sat_peek32(u32 a) { u8 *p = at(a); return (u32)p[0] << 24 | (u32)p[1] << 16 | (u32)p[2] << 8 | p[3]; }
u8 rd8(u32 a) { printf("FAIL: rd8 used (it moves the busy-wait detector)\n"); g_fail = 1; return *at(a); }
u16 rd16(u32 a) { printf("FAIL: rd16 used\n"); g_fail = 1; return (u16)(at(a)[0] << 8 | at(a)[1]); }
void wr8(u32 a, u8 v) { *at(a) = v; }
void wr16(u32 a, u16 v) { at(a)[0] = (u8)(v >> 8), at(a)[1] = (u8)v; }
void port_log(const char *fmt, ...) { printf("log: %s", fmt); }
void sat_map(u32 lo, u32 hi, const char *name, SatRead rd, SatWrite wr) { (void)hi, (void)name, (void)wr; if (lo == HLO) g_rd = rd; }
int sat_cpu_id(void) { return 0; }
int sat_net_on(void) { return 0; }
int input_scripted(void) { return 0; }
const char *sat_bup_where(void) { return g_save; }
int session_replaying(void) { return g_replay; }
int session_note_replayed(const char *name, void *buf, u32 size)
{
    if (g_note_in < 0 || strcmp(name, "*new-heads") || size != 1) return 0;
    *(u8 *)buf = (u8)g_note_in, g_note_in = -1;
    return 1;
}
void session_save_read(const char *name, const void *buf, u32 size, int got) { (void)buf, (void)size, (void)got; g_notes += !strcmp(name, "*new-heads"); }
static const u8 k_edit[3] = {1, 2, 3};
const u8 *sat_part_edit(const char *value) { return !strcmp(value, "cat") ? k_edit : !strcmp(value, "dog") ? k_edit + 1 : !strcmp(value, "owl") ? k_edit + 2 : NULL; }
u32 sat_part_compose(const u8 *edit, u8 *ram, u32 lo, u32 room)
{
    g_composed++;
    if (lo != HLO + 0x2000u * (u32)(*edit - 1) + 48u || room != 0x2000u - 48u) printf("FAIL: version %u put at %08x (%x)\n", *edit, lo, room), g_fail = 1;
    ram[0] = 0xa0, ram[1] = *edit;
    return 100;
}

static void check(int ok, const char *what) { if (!ok) printf("FAIL: %s\n", what), g_fail = 1; }
static u32 field(u32 i) { return (u32)(at(FIELD(i))[0] << 8 | at(FIELD(i))[1]); }
static const TcArgs *enter(u32 fn, u32 r4, u32 r5, u32 r6, TcArgs *tmp)
{
    static TcArgs a;
    memset(&a, 0, sizeof a), a.r[0] = r4, a.r[1] = r5, a.r[2] = r6;
    return sat_heads_enter(fn, &a, tmp);
}
static u32 leave_r(u32 fn, u32 r4, u32 r5, u32 r6, u32 r)
{
    TcArgs a;
    memset(&a, 0, sizeof a), a.r[0] = r4, a.r[1] = r5, a.r[2] = r6;
    return sat_heads_leave(fn, &a, r);
}
static void leave(u32 fn, u32 r4, u32 r5, u32 r6) { (void)leave_r(fn, r4, r5, r6, 0); }
/* the game's step: the next (or previous) offered type, round at the ends; between the kit's enter and leave */
static void step(u32 i, int next)
{
    TcArgs tmp;
    u32 f = FIELD(i), cur = field(i), n = 7;
    enter(next ? NEXT : PREV, f, AVAIL, n, &tmp);
    for (u32 k = 1; k <= n; k++) {
        u32 c = next ? (cur + k) % n : (cur + n - k) % n;
        if (at(AVAIL)[c]) { wr16(f, (u16)c); break; }
    }
    leave(next ? NEXT : PREV, f, AVAIL, n);
}
/* the new type robot i shows: the host part's OBJECT the game is handed while i is built (0: the original; the ring's copy the same) */
static int shown(u32 i)
{
    TcArgs tmp;
    const TcArgs *a;
    int k = 0;
    enter(SETUP, 0, i, 0, &tmp);
    a = enter(NODE, 0x06050000, OBJ, 0, &tmp);
    u32 got = a->r[1];
    check(enter(RNODE, 0x06050000, OBJ, 0, &tmp)->r[1] == got, "the ring's own copy handed what the robot's is");
    a = enter(NODE, 0x06050000, OBJ, 0, &tmp);
    if (a->r[1] != OBJ) {
        k = (int)((a->r[1] - HLO) / 0x2000u) + 1;
        check(a->r[0] == 0x06050000 && g_rd && g_rd(a->r[1], 4) == a->r[1] + 48u && g_rd(a->r[1] + 4, 4) == 0x11223344u, "the swapped OBJECT is the host's with the version's PDATA");
    }
    check(enter(NODE, 0x06050000, OBJ + 0x40, 0, &tmp)->r[1] == OBJ + 0x40, "another part left alone");
    leave(NODE, 0, 0, 0), leave(SETUP, 0, i, 0);
    check(enter(NODE, 0x06050000, OBJ, 0, &tmp)->r[1] == OBJ, "the host part left alone outside a robot's build");
    return k;
}
static void robot(u32 i, const char *name, u32 type)
{
    wr16(FIELD(i), (u16)type), wr8(REC + i * 90u + 76u, 1);
    for (u32 c = 0; c < 10; c++) wr8(REC + i * 90u + 65u + c, (u8)(c < strlen(name) ? name[c] : 0));
}

int main(void)
{
    char path[64], label[9] = {0};
    FILE *f;
    TcArgs tmp;
    int expect[] = {1, 2, 3, 4, 5, 6, -1, -2, -3, 1}, back[] = {1, -3, -2, -1, 6, 5, 4, 3, 2, 1}, k;
    for (u32 c = 1; c < 7; c++) at(AVAIL)[c] = 1;
    memcpy(at(NAMES + 5u * 18u), "TYPE E\0\0", 8);
    for (u32 c = 0; c < 44; c++) at(OBJ)[c] = (u8)c;
    at(OBJ)[4] = 0x11, at(OBJ)[5] = 0x22, at(OBJ)[6] = 0x33, at(OBJ)[7] = 0x44;
    robot(0, "ROBO", 1), robot(1, "PIT", 5), robot(2, "THIRD", 2);

    sat_heads_option("off");
    sat_heads_start(1);
    check(!sat_heads_on && !g_notes, "--new-heads off");
    g_note_in = 1;
    sat_heads_option("off");
    sat_heads_start(0);
    check(sat_heads_on && g_notes == 1, "the recording's switch taken");

    /* the boot check's write, before the game read its file, leaves the kit's save */
    snprintf(path, sizeof path, "t_heads_save.heads");
    f = fopen(path, "w"), fputs("saturn new head types 1\n1 dog 5049540000000000000000\n", f), fclose(f);
    sat_heads_bup(0x060d0000, 1);
    memcpy(at(0x060d0000), "ROBO_PIT___", 11);
    sat_heads_bup(0x060d0000, 1);
    check((f = fopen(path, "r")) != NULL, "a write before the game's read keeps the kit's save");
    if (f) fclose(f);

    /* order right: A .. F, G, H, A1, A; then left back */
    for (int s = 0; s < 10; s++) {
        k = shown(0);
        if (expect[s] > 0) check(field(0) == (u32)expect[s] && !k, "an original type, right");
        else check(field(0) == 5 && k == -expect[s], "a new type on the host, right");
        printf("right %d: field %u new %d\n", s, field(0), k);
        if (s < 9) step(0, 1);
    }
    for (int s = 0; s < 10; s++) { /* left from A: A1, H, G, F, E .. A */
        k = shown(0);
        if (back[s] > 0) check(field(0) == (u32)back[s] && !k, "an original type, left");
        else check(field(0) == 5 && k == -back[s], "a new type on the host, left");
        printf("left %d: field %u new %d\n", s, field(0), k);
        if (s < 9) step(0, 0);
    }

    /* a field that is not a record's type is not touched */
    wr16(0x060c0100, 6);
    enter(NEXT, 0x060c0100, AVAIL, 7, &tmp), wr16(0x060c0100, 1), leave(NEXT, 0x060c0100, AVAIL, 7);
    check(at(0x060c0100)[1] == 1, "another choice's field left alone");

    /* robot 0 takes TYPE H; the panel names it and puts the host's name back exactly */
    for (int s = 0; s < 7; s++) step(0, 1); /* A .. F, G, H */
    check(shown(0) == 2, "robot 0 has TYPE H");
    enter(PANEL, 0, 0, 0, &tmp);
    memcpy(label, at(NAMES + 5u * 18u), 8);
    check(!memcmp(label, "TYPE H\0\0", 8), "the panel shows TYPE H");
    check(leave_r(COUNT, 7, AVAIL, 0, 6) == 9, "the panel counts the new types with the host");
    at(AVAIL)[5] = 0;
    check(leave_r(COUNT, 7, AVAIL, 0, 5) == 5, "no new types counted while the host is not offered");
    at(AVAIL)[5] = 1;
    leave(PANEL, 0, 0, 0);
    check(leave_r(COUNT, 7, AVAIL, 0, 6) == 6, "the count outside the panel unchanged");
    check(!memcmp(at(NAMES + 5u * 18u), "TYPE E\0\0", 8), "the host's name put back");
    enter(PANEL, 1, 0, 0, &tmp);
    check(!memcmp(at(NAMES + 5u * 18u), "TYPE E\0\0", 8), "an original robot's panel unchanged");
    leave(PANEL, 1, 0, 0);
    check(shown(1) == 0 && shown(2) == 0, "other robots keep the original");

    /* the game reads its save: the kit's save says robot 1 is TYPE H... (dog, name PIT) */
    sat_heads_bup(0x060d0000, 0);
    check(shown(0) == 0 && shown(1) == 2, "the kit's save read: only what it says");
    /* save robot 1 (H) and a new robot 2 (A1); read back with robot 2 renamed in the game's save */
    robot(2, "THIRD", 6), step(2, 1), step(2, 1), step(2, 1); /* F, then G, H, A1 */
    check(shown(2) == 3, "robot 2 has TYPE A1");
    sat_heads_bup(0x060d0000, 1);
    robot(2, "OTHER", 5);
    sat_heads_bup(0x060d0000, 0);
    check(shown(1) == 2 && shown(2) == 0, "saved and read back; a renamed robot not taken");
    robot(2, "THIRD", 5);
    sat_heads_bup(0x060d0000, 0);
    check(shown(2) == 3, "the same robot taken");
    robot(2, "RENAMED", 5);
    check(shown(2) == 3, "once taken, a rename keeps it");

    /* ROBOT MAKING on robot 1 drops its type; with none left the save removes the kit's file */
    enter(MAKING, 1, 0, 0, &tmp), leave(MAKING, 1, 0, 0);
    check(shown(1) == 0, "ROBOT MAKING drops a kept type");
    enter(MAKING, 2, 0, 0, &tmp), leave(MAKING, 2, 0, 0);
    sat_heads_bup(0x060d0000, 1);
    check((f = fopen(path, "r")) == NULL, "no robot with a new type: no kit's save");
    if (f) fclose(f);
    check(g_composed == 3, "each version put together once");
    remove(path);
    printf(g_fail ? "FAILED\n" : "all ok\n");
    return g_fail;
}
