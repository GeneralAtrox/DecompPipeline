/* The BIOS' backup-RAM library (BUP): the game's saves. The game calls BUP_Init through the word at 0x06000358 and the
 * other services through the table that the word at 0x06000354 points to (SelPart, Format, Stat, Write, Read, Delete,
 * Dir, Verify, GetDate, SetDate, in that order after one slot for Init). Device 0 (unit id 1 in BUP_Init's table) is the console's internal memory (32 KB,
 * 64-byte blocks). Device 1 is the plug-in backup memory cartridge: the standard 4 Mbit (512 KB, 512-byte blocks, unit id 2),
 * present only while the "memory-cartridge" option is on (console_sat.c; on in play runs, off in checks and replays, so
 * with it off BUP_Init reports no second device, as before). One host file per device (--save, else --save-dir's
 * saturn_backup.bin, else the current folder's; its sibling saturn_backup_cart.bin for the cartridge, made empty on first
 * use), rewritten after every change in the kit's own layout. A game that names its load path (game.json auto_load) has
 * it driven at boot when its file is here (sat_bup_auto_load, base autoload.c).
 * Nearest existing: sat_bios.c (the same native-service form for the SCU and clock services) - this is the library
 * behind another pair of those pointers, with state, so it has its own file. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <windows.h>
#include "sat.h"
#include "sat_sys.h"
#include "sat_game.h"
#include "autoload.h"
#include "console.h"

enum { OK = 0, NON = 1, UNFORMAT = 2, NOT_ENOUGH = 4, NOT_FOUND = 5, FOUND = 6, NO_MATCH = 7 };
enum { UNIT_INT = 1, UNIT_CART = 2, DEV_INT = 0, DEV_CART = 1, HEADER_BLOCKS = 2, MAX_FILES = 128, DIR_SIZE = 34 };
enum { SLOT_INIT = 0x06000358u, SLOT_TABLE = 0x06000354u, TABLE = 0x06000380u, NSVC = 11 };

typedef struct {
    u8 dir[DIR_SIZE]; /* BupDir as the guest lays it out: name[12] comment[11] language date[4] datasize[4] blocksize[2] */
    u8 *data;
    u32 size;
} File;
typedef struct {
    File file[MAX_FILES];
    int n, loaded;
    char path[1024];
    u32 total, block; /* the memory's blocks and their size in bytes */
} Dev;
static Dev g_dev[2];
static int g_cart; /* the cartridge is plugged in (this run's "memory-cartridge" value, read at BUP_Init) */
static int g_named, g_in_dir; /* --save named the file; the files are in --save-dir's folder */

static u32 be32(const u8 *p) { return (u32)p[0] << 24 | (u32)p[1] << 16 | (u32)p[2] << 8 | p[3]; }
static void put32(u8 *p, u32 v) { p[0] = (u8)(v >> 24), p[1] = (u8)(v >> 16), p[2] = (u8)(v >> 8), p[3] = (u8)v; }

static void dev_defaults(void)
{
    if (g_dev[0].total) return;
    g_dev[0].total = 512u, g_dev[0].block = 64u;
    g_dev[1].total = 1024u, g_dev[1].block = 512u;
    snprintf(g_dev[0].path, sizeof g_dev[0].path, "saturn_backup.bin");
    snprintf(g_dev[1].path, sizeof g_dev[1].path, "saturn_backup_cart.bin");
}
static Dev *dev_of(u32 dev)
{
    dev_defaults();
    return dev == DEV_INT ? &g_dev[0] : dev == DEV_CART && g_cart ? &g_dev[1] : NULL;
}
static u32 blocks_of(const Dev *d, u32 size) { return 1u + (size + d->block - 1u) / d->block; }
static u32 blocks_used(const Dev *d)
{
    u32 n = HEADER_BLOCKS;
    for (int i = 0; i < d->n; i++) n += blocks_of(d, d->file[i].size);
    return n;
}

/* the cartridge's file sits beside the internal one: its name with "_cart" before the extension */
static void cart_path(const char *internal, char *out, size_t cap)
{
    const char *base = internal, *dot;
    size_t head;
    for (const char *p = internal; *p; p++)
        if (*p == '/' || *p == '\\') base = p + 1;
    dot = strrchr(base, '.');
    head = dot ? (size_t)(dot - internal) : strlen(internal);
    snprintf(out, cap, "%.*s_cart%s", (int)head, internal, dot ? dot : "");
}
void sat_bup_path(const char *path)
{
    dev_defaults();
    snprintf(g_dev[0].path, sizeof g_dev[0].path, "%s", path);
    cart_path(path, g_dev[1].path, sizeof g_dev[1].path);
    g_named = 1, g_in_dir = 0;
}
/* --save-dir DIR (a launcher's USERDATA\saves, base/host_main.cpp): the files there, unless --save names one */
void sat_bup_save_dir(const char *dir)
{
    dev_defaults();
    if (g_named) return;
    snprintf(g_dev[0].path, sizeof g_dev[0].path, "%s\\saturn_backup.bin", dir);
    cart_path(g_dev[0].path, g_dev[1].path, sizeof g_dev[1].path);
    g_in_dir = 1;
}
static void make_folder(const char *file) /* the save's folder (the data folder's saves) */
{
    char dir[1024], *slash = (snprintf(dir, sizeof dir, "%s", file), strrchr(dir, '\\'));
    if (slash) *slash = 0, CreateDirectoryA(dir, NULL);
}
/* A save where older builds kept it (the current folder: a Start Game.bat's .work\auto) is copied once to the --save-dir
 * folder when that has none, so nobody loses one. The old file is left as it was. */
static void carry_over(const Dev *d)
{
    const char *old = d == &g_dev[0] ? "saturn_backup.bin" : "saturn_backup_cart.bin";
    if (!g_in_dir || GetFileAttributesA(d->path) != INVALID_FILE_ATTRIBUTES || GetFileAttributesA(old) == INVALID_FILE_ATTRIBUTES) return;
    make_folder(d->path);
    if (CopyFileA(old, d->path, TRUE)) port_log("bup: %s from the current folder copied to %s, the save's place now\n", old, d->path);
}
const char *sat_bup_where(void)
{
    dev_defaults();
    return g_dev[0].path;
}

static void save_all(Dev *d)
{
    FILE *f = fopen(d->path, "wb");
    u8 hdr[8] = {'S', 'B', 'U', 'P', 0, 0, 0, 0};
    if (!f && g_in_dir) make_folder(d->path), f = fopen(d->path, "wb");
    if (!f) return;
    put32(hdr + 4, (u32)d->n);
    fwrite(hdr, 1, 8, f);
    for (int i = 0; i < d->n; i++) {
        u8 len[4];
        put32(len, d->file[i].size);
        fwrite(d->file[i].dir, 1, DIR_SIZE, f);
        fwrite(len, 1, 4, f);
        fwrite(d->file[i].data, 1, d->file[i].size, f);
    }
    fclose(f);
}

static void load_all(Dev *d)
{
    FILE *f;
    u8 hdr[8];
    if (d->loaded) return;
    d->loaded = 1;
    carry_over(d);
    f = fopen(d->path, "rb");
    if (!f) {
        if (d == &g_dev[1]) save_all(d); /* a cartridge's file is made empty on first use */
        port_log("bup: %d file(s) in %s\n", d->n, d->path);
        return;
    }
    if (fread(hdr, 1, 8, f) == 8 && !memcmp(hdr, "SBUP", 4)) {
        u32 n = be32(hdr + 4);
        for (u32 i = 0; i < n && i < MAX_FILES; i++) {
            u8 len[4];
            File *e = &d->file[d->n];
            if (fread(e->dir, 1, DIR_SIZE, f) != DIR_SIZE || fread(len, 1, 4, f) != 4) break;
            e->size = be32(len);
            e->data = (u8 *)malloc(e->size ? e->size : 1u);
            if (!e->data || fread(e->data, 1, e->size, f) != e->size) break;
            d->n++;
        }
    }
    fclose(f);
    port_log("bup: %d file(s) in %s\n", d->n, d->path);
}

static int find(const Dev *d, u32 name) /* the guest's 11-character file name at name */
{
    for (int i = 0; i < d->n; i++) {
        int same = 1;
        for (int k = 0; k < 11 && same; k++) same = d->file[i].dir[k] == rd8(name + (u32)k);
        if (same) return i;
    }
    return -1;
}

static void drop(Dev *d, int i)
{
    free(d->file[i].data);
    memmove(&d->file[i], &d->file[i + 1], (size_t)(d->n - i - 1) * sizeof d->file[0]);
    d->n--;
}

static u32 b_init(u32 lib, u32 work, u32 config, u32 d)
{
    (void)lib, (void)work, (void)d;
    dev_defaults();
    g_cart = port_option("memory-cartridge") > 0;
    load_all(&g_dev[0]);
    if (g_cart) load_all(&g_dev[1]);
    port_log("bup: memory cartridge %s\n", g_cart ? "plugged in" : "absent");
    for (u32 i = 0; i < 3; i++) {
        u32 unit = i == 0 ? UNIT_INT : i == 1 && g_cart ? UNIT_CART : 0u;
        wr16(config + 4u * i, (u16)unit);
        wr16(config + 4u * i + 2u, (u16)(unit ? 1 : 0));
    }
    return 0;
}
static u32 b_selpart(u32 dev, u32 part, u32 c, u32 d)
{
    (void)part, (void)c, (void)d;
    return dev_of(dev) ? OK : NON;
}
static u32 b_format(u32 dev, u32 b, u32 c, u32 d)
{
    Dev *m = dev_of(dev);
    (void)b, (void)c, (void)d;
    if (!m) return NON;
    while (m->n) drop(m, 0);
    save_all(m);
    return OK;
}
static u32 b_stat(u32 dev, u32 tbsize, u32 out, u32 d)
{
    Dev *m = dev_of(dev);
    u32 free_blocks;
    (void)tbsize, (void)d;
    if (!m) return NON;
    free_blocks = m->total - blocks_used(m);
    wr32(out, m->total * m->block);
    wr32(out + 4u, m->total);
    wr32(out + 8u, m->block);
    wr32(out + 12u, free_blocks * m->block);
    wr32(out + 16u, free_blocks);
    /* datanum is how many more files the memory takes (Robo Pit's save check writes straight away when it is not 0 and
     * only looks for its existing file when it is 0: a full memory) */
    wr32(out + 20u, (u32)(MAX_FILES - m->n) < free_blocks / 2u ? (u32)(MAX_FILES - m->n) : free_blocks / 2u);
    return OK;
}
static u32 b_write(u32 dev, u32 dir, u32 data, u32 mode)
{
    Dev *m = dev_of(dev);
    u32 size = rd32(dir + 28u), have;
    int at;
    if (!m) return NON;
    at = find(m, dir);
    if (at >= 0 && mode) return FOUND; /* mode 0 overwrites, as Robo Pit's boot check needs (it writes, then verifies, with a file already there) */
    have = at >= 0 ? blocks_of(m, m->file[at].size) : 0;
    if (blocks_used(m) - have + blocks_of(m, size) > m->total || (at < 0 && m->n >= MAX_FILES)) return NOT_ENOUGH;
    if (at >= 0) drop(m, at);
    {
        File *e = &m->file[m->n++];
        for (u32 i = 0; i < DIR_SIZE; i++) e->dir[i] = rd8(dir + i);
        put32(e->dir + 28, size);
        e->dir[32] = (u8)(blocks_of(m, size) >> 8), e->dir[33] = (u8)blocks_of(m, size);
        e->size = size;
        e->data = (u8 *)malloc(size ? size : 1u);
        for (u32 i = 0; i < size; i++) e->data[i] = rd8(data + i);
    }
    save_all(m);
    if (sat_heads_on && dev == DEV_INT) sat_game_heads_bup(dir, 1); /* the game's own save beside the game's (the game's hook) */
    return OK;
}
static u32 b_read(u32 dev, u32 name, u32 data, u32 d)
{
    Dev *m = dev_of(dev);
    int at;
    (void)d;
    if (!m) return NON;
    at = find(m, name);
    if (at < 0) return NOT_FOUND;
    for (u32 i = 0; i < m->file[at].size; i++) wr8(data + i, m->file[at].data[i]);
    if (sat_heads_on && dev == DEV_INT) sat_game_heads_bup(name, 0);
    return OK;
}
static u32 delete_file(u32 dev, u32 name, u32 c, u32 d)
{
    Dev *m = dev_of(dev);
    int at;
    (void)c, (void)d;
    if (!m) return NON;
    at = find(m, name);
    if (at < 0) return NOT_FOUND;
    drop(m, at);
    save_all(m);
    return OK;
}
static u32 b_dir(u32 dev, u32 name, u32 tbsize, u32 out)
{
    Dev *m = dev_of(dev);
    u32 n = 0;
    if (!m) return NON;
    for (int i = 0; i < m->n && n < tbsize; i++) {
        int match = 1; /* an empty name lists everything, else the files that start with the name */
        for (int k = 0; match && k < 11; k++) {
            u8 want = rd8(name + (u32)k);
            if (want == 0) break;
            match = want == m->file[i].dir[k];
        }
        if (!match) continue;
        for (u32 k = 0; k < DIR_SIZE; k++) wr8(out + 36u * n + k, m->file[i].dir[k]);
        n++;
    }
    return n;
}
static u32 b_verify(u32 dev, u32 name, u32 data, u32 d)
{
    Dev *m = dev_of(dev);
    int at;
    (void)d;
    if (!m) return NON;
    at = find(m, name);
    if (at < 0) return NOT_FOUND;
    {
        u32 first = 0, bad = 0;
        for (u32 i = 0; i < m->file[at].size; i++)
            if (rd8(data + i) != m->file[at].data[i] && !bad++) first = i;
        if (bad) port_log("bup: verify differs in %u of %u bytes, the first at %u\n", bad, m->file[at].size, first);
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
    dev_defaults();
    carry_over(&g_dev[0]), carry_over(&g_dev[1]); /* at the start, whether or not the game reads its save */
    wr32(SLOT_INIT, ROM);
    wr32(SLOT_TABLE, TABLE);
    for (u32 i = 0; i < NSVC; i++) wr32(TABLE + 4u * i, ROM + 4u * i);
}

/* Auto-load (game.json auto_load, which gen_saturn.py turns into SAT_AUTO_LOAD_FILE and its steps): at the start, after
 * the save's notes. The file is looked for only when the setting wants it, so a run without it reads the save as before. */
#ifdef SAT_AUTO_LOAD_FILE
static const AutoLoadStep k_auto[] = SAT_AUTO_LOAD_STEPS;
static int has_file(void)
{
    dev_defaults();
    load_all(&g_dev[0]);
    for (int i = 0; i < g_dev[0].n; i++)
        if (!memcmp(g_dev[0].file[i].dir, SAT_AUTO_LOAD_FILE, 11)) return 1;
    return 0;
}
static void poke16(u32 addr, u32 value) { wr16(addr, (u16)value); }
static u32 peek16(u32 addr) { return rd16(addr); }
static u32 call1(u32 fn, u32 arg)
{
    TcArgs a;
    memset(&a, 0, sizeof a);
    a.r[0] = arg;
    return tc_call(fn, &a);
}
static const AutoLoadConsole k_con = {has_file, poke16, peek16, call1};
#endif
void sat_bup_auto_load(int play_run)
{
#ifdef SAT_AUTO_LOAD_FILE
    autoload_start(k_auto, sizeof k_auto / sizeof k_auto[0], play_run, &k_con);
#else
    (void)play_run;
#endif
}
