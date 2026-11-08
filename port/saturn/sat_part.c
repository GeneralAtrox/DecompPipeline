/* A model part the player picks (Display > the game's title for it; game.json part_choice, which gen_saturn.py turns into
 * sat_part_data.h). The game names one OBJECT of its model data and the original PDATA it points at, and offers other
 * versions of that part, each an edit of the original made by the game's own tools (Robo Pit's animal heads for the
 * bunny: tools/animal_heads/export_choices.py in its repository). An edit holds only what the game's tools add, never
 * the disc's data, so the kit puts the version together from the original in guest memory:
 *   1. the original's points from drop_points on, and its polygons that use only those, renumbered from 0;
 *   2. each kept polygon takes the edit's colour and a normal worked out again from its points in double precision,
 *      (b - a) x (c - a) over its length, pointing the way the original's stored normals point overall;
 *   3. the edit's points and polygons (normals ready-made) follow;
 *   4. every polygon's attributes are the original's first, with the colour and the flag byte changed.
 * An edit, big-endian: u16 kept polygons, points, polygons, 0; u16 colour per kept polygon; s32 x, y, z per point; per
 * polygon s32 normal x, y, z, u16 vertices[4], u16 colour, u8 flag, u8 0.
 * The version goes in RAM the kit maps where a Saturn RAM cartridge sits (PART_LO; nothing in Robo Pit reads there),
 * with its pointers rebased there, and the OBJECT is pointed at it. The game then draws, paints and lights it as its
 * own. This happens at each vertical blank where the OBJECT points at the original, so a reload of the models puts it
 * back in again. The log line gives the version's FNV-1a sum, to check against the game tools' own build of it.
 * The choice is made at start: a replay takes the recording's (the original when the recording has none), otherwise
 * --part-choice NAME. It goes in the recording only when it is not the original; with the original nothing is mapped,
 * read or written, so the run is as before. A change in the menu applies from the next start.
 *
 * Nearest existing: session.h's session notes, used as they are for the start-time choice; console_guest_ptr in
 * console_sat.c reads guest memory the same way, byte-reversed for the scene dump, which this cannot use. */
#include <math.h>
#include <stdlib.h>
#include <string.h>
#include "sat.h"
#include "sat_sys.h"
#include "sat_part.h"
#include "sat_part_data.h"
#include "session.h"

#if SAT_PART_N

#define PART_LO 0x02400000u /* a RAM cartridge's place on the A-bus */
#define PART_SIZE 0x10000u
#define PART_NOTE "*part-choice"

static int g_choice;  /* asked for (index; 0 the original) */
static int g_pick;    /* in force since the start */
static u8 *g_ram;     /* the mapped RAM */

static u32 be32(const u8 *p) { return (u32)p[0] << 24 | (u32)p[1] << 16 | (u32)p[2] << 8 | p[3]; }
static u32 be16(const u8 *p) { return (u32)p[0] << 8 | p[1]; }
static void put32(u8 *p, u32 v) { p[0] = (u8)(v >> 24), p[1] = (u8)(v >> 16), p[2] = (u8)(v >> 8), p[3] = (u8)v; }
static void put16(u8 *p, u32 v) { p[0] = (u8)(v >> 8), p[1] = (u8)v; }

/* n bytes of guest work RAM at a, as they lie (big-endian), or NULL */
static u8 *guest(u32 a, u32 n)
{
    a &= 0x1fffffffu;
    if (a >= SAT_WRAM_L_BASE && a - SAT_WRAM_L_BASE <= SAT_WRAM_SIZE - n) return sat_ram_l() + (a - SAT_WRAM_L_BASE);
    if ((a >> 24 == 6u || a >> 24 == 7u) && (a & (SAT_WRAM_SIZE - 1u)) <= SAT_WRAM_SIZE - n) return sat_ram_h() + (a & (SAT_WRAM_SIZE - 1u));
    return NULL;
}

static u32 ram_read(u32 phys, u32 size)
{
    u32 v = 0, o = phys - PART_LO;
    for (u32 i = 0; i < size; i++) v = v << 8 | g_ram[(o + i) & (PART_SIZE - 1u)];
    return v;
}

static void ram_write(u32 phys, u32 size, u32 value)
{
    u32 o = phys - PART_LO;
    for (u32 i = 0; i < size; i++) g_ram[(o + i) & (PART_SIZE - 1u)] = (u8)(value >> (8u * (size - 1u - i)));
}

static int find(const char *v)
{
    for (int i = 0; i < SAT_PART_N; i++)
        if (!strcmp(sat_part_values[i], v)) return i;
    return -1;
}

void sat_part_option(const char *value)
{
    g_choice = find(value);
    if (g_choice < 0) {
        port_log("part: no %s here, the original stays\n", value);
        g_choice = 0;
    }
}

void sat_part_start(void)
{
    char name[16] = {0};
    if (session_note_replayed(PART_NOTE, name, sizeof name)) {
        name[15] = 0;
        g_pick = find(name) > 0 ? find(name) : 0;
    } else if (session_replaying()) {
        g_pick = 0;
    } else {
        g_pick = g_choice;
    }
    if (g_pick) {
        strncpy_s(name, sizeof name, sat_part_values[g_pick], _TRUNCATE);
        session_save_read(PART_NOTE, name, sizeof name, 1);
    }
    port_log("part: %s\n", sat_part_values[g_pick]);
}

/* the unit normal of points a, b, c (16.16), in 16.16 rounded half to even */
static void normal(const u8 *a, const u8 *b, const u8 *c, double sg, u8 *out)
{
    double u[3], w[3], n[3], r;
    for (int k = 0; k < 3; k++) {
        u[k] = (double)(s32)be32(b + 4 * k) / 65536.0 - (double)(s32)be32(a + 4 * k) / 65536.0;
        w[k] = (double)(s32)be32(c + 4 * k) / 65536.0 - (double)(s32)be32(a + 4 * k) / 65536.0;
    }
    n[0] = u[1] * w[2] - u[2] * w[1];
    n[1] = u[2] * w[0] - u[0] * w[2];
    n[2] = u[0] * w[1] - u[1] * w[0];
    r = sqrt(n[0] * n[0] + n[1] * n[1] + n[2] * n[2]);
    if (r == 0.0) r = 1.0;
    for (int k = 0; k < 3; k++) put32(out + 4 * k, (u32)(s32)nearbyint(n[k] / r * sg * 65536.0));
}

/* the version in g_ram from the original at SAT_PART_PDATA and the edit; its size, 0 if the original is not as named */
static u32 compose(const u8 *edit)
{
    const u8 *hd = guest(SAT_PART_PDATA, 20u), *pts, *pls, *ats, *cols, *ep, *ef;
    u32 npt = 0, npl = 0, nk, ne, nf, base, opt, opl, lo, ao, k = 0;
    double s = 0.0;
    if (!hd || (npt = be32(hd + 4)) != SAT_PART_POINTS || (npl = be32(hd + 12)) != SAT_PART_POLYS) return 0;
    pts = guest(be32(hd), npt * 12u), pls = guest(be32(hd + 8), npl * 20u), ats = guest(be32(hd + 16), npl * 12u);
    nk = be16(edit), ne = be16(edit + 2), nf = be16(edit + 4);
    cols = edit + 8, ep = cols + 2u * nk, ef = ep + 12u * ne;
    base = npt - SAT_PART_DROP, opt = base + ne, opl = nk + nf;
    lo = 20u + 12u * opt, ao = lo + 20u * opl;
    if (!pts || !pls || !ats || ao + 12u * opl > PART_SIZE) return 0;
    for (u32 i = 0; i < npl; i++) { /* which way the original's normals point: the sum of each against its points' */
        u8 n[12];
        const u8 *p = pls + 20u * i;
        if (be16(p + 12) >= npt || be16(p + 14) >= npt || be16(p + 16) >= npt) return 0;
        normal(pts + 12u * be16(p + 12), pts + 12u * be16(p + 14), pts + 12u * be16(p + 16), 1.0, n);
        for (int c = 0; c < 3; c++) s += (double)(s32)be32(n + 4 * c) / 65536.0 * ((double)(s32)be32(p + 4 * c) / 65536.0);
    }
    memset(g_ram, 0, PART_SIZE);
    put32(g_ram, PART_LO + 20u), put32(g_ram + 4, opt), put32(g_ram + 8, PART_LO + lo), put32(g_ram + 12, opl), put32(g_ram + 16, PART_LO + ao);
    memcpy(g_ram + 20, pts + 12u * SAT_PART_DROP, 12u * base);
    memcpy(g_ram + 20 + 12u * base, ep, 12u * ne);
    for (u32 i = 0; i < npl; i++) {
        const u8 *p = pls + 20u * i;
        u8 *q = g_ram + lo + 20u * k, *a = g_ram + ao + 12u * k;
        u32 v[4];
        for (int j = 0; j < 4; j++) v[j] = be16(p + 12 + 2 * j);
        if (v[0] < SAT_PART_DROP || v[1] < SAT_PART_DROP || v[2] < SAT_PART_DROP || v[3] < SAT_PART_DROP) continue;
        if (k == nk) return 0;
        for (int j = 0; j < 4; j++) put16(q + 12 + 2 * j, v[j] - SAT_PART_DROP);
        normal(g_ram + 20 + 12u * (v[0] - SAT_PART_DROP), g_ram + 20 + 12u * (v[1] - SAT_PART_DROP), g_ram + 20 + 12u * (v[2] - SAT_PART_DROP),
               s >= 0.0 ? 1.0 : -1.0, q);
        memcpy(a, ats, 12);
        put16(a + 6, be16(cols + 2u * k));
        k++;
    }
    if (k != nk) return 0;
    for (u32 i = 0; i < nf; i++, k++) {
        const u8 *f = ef + 24u * i;
        u8 *a = g_ram + ao + 12u * k;
        memcpy(g_ram + lo + 20u * k, f, 20);
        memcpy(a, ats, 12);
        a[0] = f[22];
        put16(a + 6, be16(f + 20));
    }
    return ao + 12u * opl;
}

void sat_part_frame(void)
{
    u8 *slot;
    u32 size, sum = 2166136261u;
    if (!g_pick || !(slot = guest(SAT_PART_OBJECT, 4u)) || be32(slot) != SAT_PART_PDATA) return;
    if (!g_ram) {
        g_ram = (u8 *)calloc(1, PART_SIZE);
        if (!g_ram) return;
        sat_map(PART_LO, PART_LO + PART_SIZE, "part", ram_read, ram_write);
    }
    size = compose(sat_part_edits[g_pick]);
    if (!size) {
        port_log("part: the original at %08x is not the one game.json names; %s is not put in\n", SAT_PART_PDATA, sat_part_values[g_pick]);
        g_pick = 0;
        return;
    }
    for (u32 i = 0; i < size; i++) sum = (sum ^ g_ram[i]) * 16777619u;
    put32(slot, PART_LO);
    port_log("part: %s in place of the original at vertical blank %u: %u points, %u polygons, %u bytes at %08x, sum %08x\n",
             sat_part_values[g_pick], sat_vblank_count(), be32(g_ram + 4), be32(g_ram + 12), size, PART_LO, sum);
}

static int part_get(void) { return g_choice; }
static void part_set(int choice) { g_choice = choice; }
static const PortMenuItem k_item = {"Display", SAT_PART_TITLE " (from the next start)", PORT_MENU_CHOICE, "part-choice", part_get, part_set,
                                    sat_part_labels, sat_part_values, SAT_PART_N, NULL};
const PortMenuItem *sat_part_menu(void) { return &k_item; }

#else /* the game offers no part */
void sat_part_option(const char *value) { (void)value; }
void sat_part_start(void) {}
void sat_part_frame(void) {}
const PortMenuItem *sat_part_menu(void) { return NULL; }
#endif
