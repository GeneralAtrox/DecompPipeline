/* The BIOS' backup-RAM library (BUP): the game's saves. The game calls BUP_Init through the word at 0x06000358 and the
 * other services through the table that the word at 0x06000354 points to (SelPart, Format, Stat, Write, Read, Delete,
 * Dir, Verify, GetDate, SetDate, in that order after one slot for Init). Device 0 (unit id 1 in BUP_Init's table) is the console's internal memory (32 KB,
 * 64-byte blocks); the other units are absent. The files live in one host file (--save, default saturn_backup.bin),
 * rewritten after every change; its layout is the kit's own, the game only sees the BUP calls.
 *
 * Nearest existing: sat_bios.c (the same native-service form for the SCU and clock services) - this is the library
 * behind another pair of those pointers, with state, so it has its own file. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include "sat.h"
#include "sat_sys.h"

enum { OK = 0, NON = 1, UNFORMAT = 2, NOT_ENOUGH = 4, NOT_FOUND = 5, FOUND = 6, NO_MATCH = 7 };
enum { UNIT_INT = 1, DEV_INT = 0, TOTAL_BLOCKS = 512, BLOCK = 64, HEADER_BLOCKS = 2, MAX_FILES = 128, DIR_SIZE = 34 };
enum { SLOT_INIT = 0x06000358u, SLOT_TABLE = 0x06000354u, TABLE = 0x06000380u, NSVC = 11 };

typedef struct {
    u8 dir[DIR_SIZE]; /* BupDir as the guest lays it out: name[12] comment[11] language date[4] datasize[4] blocksize[2] */
    u8 *data;
    u32 size;
} File;
static File g_file[MAX_FILES];
static int g_n;
static const char *g_path = "saturn_backup.bin";
static int g_loaded;

static u32 be32(const u8 *p) { return (u32)p[0] << 24 | (u32)p[1] << 16 | (u32)p[2] << 8 | p[3]; }
static void put32(u8 *p, u32 v) { p[0] = (u8)(v >> 24), p[1] = (u8)(v >> 16), p[2] = (u8)(v >> 8), p[3] = (u8)v; }

static u32 blocks_of(u32 size) { return 1u + (size + BLOCK - 1u) / BLOCK; }
static u32 blocks_used(void)
{
    u32 n = HEADER_BLOCKS;
    for (int i = 0; i < g_n; i++) n += blocks_of(g_file[i].size);
    return n;
}

void sat_bup_path(const char *path) { g_path = path; }

static void save_all(void)
{
    FILE *f = fopen(g_path, "wb");
    u8 hdr[8] = {'S', 'B', 'U', 'P', 0, 0, 0, 0};
    if (!f) return;
    put32(hdr + 4, (u32)g_n);
    fwrite(hdr, 1, 8, f);
    for (int i = 0; i < g_n; i++) {
        u8 len[4];
        put32(len, g_file[i].size);
        fwrite(g_file[i].dir, 1, DIR_SIZE, f);
        fwrite(len, 1, 4, f);
        fwrite(g_file[i].data, 1, g_file[i].size, f);
    }
    fclose(f);
}

static void load_all(void)
{
    FILE *f;
    u8 hdr[8];
    if (g_loaded) return;
    g_loaded = 1;
    f = fopen(g_path, "rb");
    if (!f) return;
    if (fread(hdr, 1, 8, f) == 8 && !memcmp(hdr, "SBUP", 4)) {
        u32 n = be32(hdr + 4);
        for (u32 i = 0; i < n && i < MAX_FILES; i++) {
            u8 len[4];
            File *e = &g_file[g_n];
            if (fread(e->dir, 1, DIR_SIZE, f) != DIR_SIZE || fread(len, 1, 4, f) != 4) break;
            e->size = be32(len);
            e->data = (u8 *)malloc(e->size ? e->size : 1u);
            if (!e->data || fread(e->data, 1, e->size, f) != e->size) break;
            g_n++;
        }
    }
    fclose(f);
    port_log("bup: %d file(s) in %s\n", g_n, g_path);
}

static int find(u32 name) /* the guest's 11-character file name at name */
{
    for (int i = 0; i < g_n; i++) {
        int same = 1;
        for (int k = 0; k < 11 && same; k++) same = g_file[i].dir[k] == rd8(name + (u32)k);
        if (same) return i;
    }
    return -1;
}

static void drop(int i)
{
    free(g_file[i].data);
    memmove(&g_file[i], &g_file[i + 1], (size_t)(g_n - i - 1) * sizeof g_file[0]);
    g_n--;
}

static u32 b_init(u32 lib, u32 work, u32 config, u32 d)
{
    (void)lib, (void)work, (void)d;
    load_all();
    for (u32 i = 0; i < 3; i++) {
        wr16(config + 4u * i, (u16)(i == 0 ? UNIT_INT : 0));
        wr16(config + 4u * i + 2u, (u16)(i == 0 ? 1 : 0));
    }
    return 0;
}
static u32 b_selpart(u32 dev, u32 part, u32 c, u32 d)
{
    (void)part, (void)c, (void)d;
    return dev == DEV_INT ? OK : NON;
}
static u32 b_format(u32 dev, u32 b, u32 c, u32 d)
{
    (void)b, (void)c, (void)d;
    if (dev != DEV_INT) return NON;
    while (g_n) drop(0);
    save_all();
    return OK;
}
static u32 b_stat(u32 dev, u32 tbsize, u32 out, u32 d)
{
    u32 free_blocks = TOTAL_BLOCKS - blocks_used();
    (void)tbsize, (void)d;
    if (dev != DEV_INT) return NON;
    wr32(out, TOTAL_BLOCKS * BLOCK);
    wr32(out + 4u, TOTAL_BLOCKS);
    wr32(out + 8u, BLOCK);
    wr32(out + 12u, free_blocks * BLOCK);
    wr32(out + 16u, free_blocks);
    /* datanum is how many more files the memory takes (Robo Pit's save check writes straight away when it is not 0 and
     * only looks for its existing file when it is 0: a full memory) */
    wr32(out + 20u, (u32)(MAX_FILES - g_n) < free_blocks / 2u ? (u32)(MAX_FILES - g_n) : free_blocks / 2u);
    return OK;
}
static u32 b_write(u32 dev, u32 dir, u32 data, u32 mode)
{
    u32 size = rd32(dir + 28u), have;
    int at;
    if (dev != DEV_INT) return NON;
    at = find(dir);
    if (at >= 0 && mode) return FOUND; /* mode 0 overwrites, as Robo Pit's boot check needs (it writes, then verifies, with a file already there) */
    have = at >= 0 ? blocks_of(g_file[at].size) : 0;
    if (blocks_used() - have + blocks_of(size) > TOTAL_BLOCKS || (at < 0 && g_n >= MAX_FILES)) return NOT_ENOUGH;
    if (at >= 0) drop(at);
    {
        File *e = &g_file[g_n++];
        for (u32 i = 0; i < DIR_SIZE; i++) e->dir[i] = rd8(dir + i);
        put32(e->dir + 28, size);
        e->dir[32] = (u8)(blocks_of(size) >> 8), e->dir[33] = (u8)blocks_of(size);
        e->size = size;
        e->data = (u8 *)malloc(size ? size : 1u);
        for (u32 i = 0; i < size; i++) e->data[i] = rd8(data + i);
    }
    save_all();
    return OK;
}
static u32 b_read(u32 dev, u32 name, u32 data, u32 d)
{
    int at;
    (void)d;
    if (dev != DEV_INT) return NON;
    at = find(name);
    if (at < 0) return NOT_FOUND;
    for (u32 i = 0; i < g_file[at].size; i++) wr8(data + i, g_file[at].data[i]);
    return OK;
}
static u32 delete_file(u32 dev, u32 name, u32 c, u32 d)
{
    int at;
    (void)c, (void)d;
    if (dev != DEV_INT) return NON;
    at = find(name);
    if (at < 0) return NOT_FOUND;
    drop(at);
    save_all();
    return OK;
}
static u32 b_dir(u32 dev, u32 name, u32 tbsize, u32 out)
{
    u32 n = 0;
    if (dev != DEV_INT) return NON;
    for (int i = 0; i < g_n && n < tbsize; i++) {
        int match = 1; /* an empty name lists everything, else the files that start with the name */
        for (int k = 0; match && k < 11; k++) {
            u8 want = rd8(name + (u32)k);
            if (want == 0) break;
            match = want == g_file[i].dir[k];
        }
        if (!match) continue;
        for (u32 k = 0; k < DIR_SIZE; k++) wr8(out + 36u * n + k, g_file[i].dir[k]);
        n++;
    }
    return n;
}
static u32 b_verify(u32 dev, u32 name, u32 data, u32 d)
{
    int at;
    (void)d;
    if (dev != DEV_INT) return NON;
    at = find(name);
    if (at < 0) return NOT_FOUND;
    {
        u32 first = 0, bad = 0;
        for (u32 i = 0; i < g_file[at].size; i++)
            if (rd8(data + i) != g_file[at].data[i] && !bad++) first = i;
        if (bad) port_log("bup: verify differs in %u of %u bytes, the first at %u\n", bad, g_file[at].size, first);
        return bad ? NO_MATCH : OK;
    }
}

/* a date is the minutes since 1980-01-01 00:00; BupDate is year (since 1980), month, day, hour, minute, weekday */
static u32 days_from_civil(int y, int m, int d)
{
    int era;
    u32 yoe, doy, doe;
    y -= m <= 2;
    era = (y >= 0 ? y : y - 399) / 400;
    yoe = (u32)(y - era * 400);
    doy = (u32)((153 * (m + (m > 2 ? -3 : 9)) + 2) / 5 + d - 1);
    doe = yoe * 365u + yoe / 4u - yoe / 100u + doy;
    return (u32)(era * 146097) + doe - 719468u;
}
static u32 set_date(u32 tb, u32 b, u32 c, u32 d)
{
    u32 days = days_from_civil(1980 + (int)rd8(tb), (int)rd8(tb + 1), (int)rd8(tb + 2)) - days_from_civil(1980, 1, 1);
    (void)b, (void)c, (void)d;
    return days * 1440u + (u32)rd8(tb + 3) * 60u + rd8(tb + 4);
}
static u32 get_date(u32 date, u32 tb, u32 c, u32 d)
{
    time_t t = (time_t)(315532800 + (long long)date * 60);
    struct tm *g = gmtime(&t);
    (void)c, (void)d;
    if (!g) return 0;
    wr8(tb, (u8)(g->tm_year - 80));
    wr8(tb + 1, (u8)(g->tm_mon + 1));
    wr8(tb + 2, (u8)g->tm_mday);
    wr8(tb + 3, (u8)g->tm_hour);
    wr8(tb + 4, (u8)g->tm_min);
    wr8(tb + 5, (u8)g->tm_wday);
    return 0;
}

static const SatNative k_raw[NSVC] = {b_init, b_selpart, b_format, b_stat, b_write, b_read, delete_file, b_dir, b_verify, get_date, set_date};
static const char *const k_name[NSVC] = {"Init", "SelPart", "Format", "Stat", "Write", "Read", "Delete", "Dir", "Verify", "GetDate", "SetDate"};

/* each service is logged with its arguments and result: what a game's save check asked is what the log must show */
#define TRACED(i)                                                                          \
    static u32 t##i(u32 a, u32 b, u32 c, u32 d)                                            \
    {                                                                                      \
        u32 r = k_raw[i](a, b, c, d);                                                      \
        port_log("bup: %s(%x, %x, %x, %x) = %u\n", k_name[i], a, b, c, d, r);              \
        return r;                                                                          \
    }
TRACED(0) TRACED(1) TRACED(2) TRACED(3) TRACED(4) TRACED(5) TRACED(6) TRACED(7) TRACED(8) TRACED(9) TRACED(10)
static const SatNative k_svc[NSVC] = {t0, t1, t2, t3, t4, t5, t6, t7, t8, t9, t10};
enum { ROM = 0x00000f00u }; /* the made-up addresses of these services (sat_bios.c's are at 0x0e00) */

SatNative sat_bup_native(u32 addr)
{
    u32 i = (addr - ROM) / 4u;
    return addr >= ROM && i < NSVC ? k_svc[i] : 0;
}

void sat_bup_init(void)
{
    wr32(SLOT_INIT, ROM);
    wr32(SLOT_TABLE, TABLE);
    for (u32 i = 0; i < NSVC; i++) wr32(TABLE + 4u * i, ROM + 4u * i);
}
