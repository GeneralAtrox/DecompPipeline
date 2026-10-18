/* Read-only access to a Dreamcast GD-ROM image: a .gdi or a Redump .cue with raw 2352-byte Mode 1 tracks.
 *
 * The game's files are in the high-density area, whose first data track starts at LBA 45000. IP.BIN is its first
 * 16 sectors (the boot file name is at offset 0x60); the ISO 9660 primary volume descriptor is at LBA 45016 and its
 * directory records hold absolute LBAs. A raw sector is a 16-byte header, then 2048 bytes of data. Redump images
 * keep a 225-sector pregap at the start of every HD track after the first (the INDEX 01 offset of the .cue). */
#include <ctype.h>
#include <stdlib.h>
#include <string.h>
#include "port.h"
#include "platform.h"

#define HD_LBA 45000u
#define RAW 2352u
#define DATA 2048u
#define HEAD 16u
#define MAX_TRACKS 32

typedef struct {
    u32 lba;   /* the track's first data sector */
    long long offset; /* byte offset of that sector in its file */
    char path[512];
    FILE *f; /* opened on first read, kept open */
    int audio;   /* a CD audio track (host_disc_read_audio) */
    u32 sectors; /* its length */
} Track;

typedef struct {
    u32 num, lba, data; /* every high-density track, audio too, for the TOC */
} TocTrack;

struct PortDisc {
    Track t[MAX_TRACKS];
    int n;
    TocTrack toc[MAX_TRACKS];
    int ntoc;
};

static void add_toc(PortDisc *d, u32 num, u32 lba, u32 data)
{
    if (d->ntoc >= MAX_TRACKS) return;
    d->toc[d->ntoc].num = num;
    d->toc[d->ntoc].lba = lba;
    d->toc[d->ntoc++].data = data;
}

static void dir_of(const char *path, char *out, size_t cap)
{
    strncpy_s(out, cap, path, _TRUNCATE);
    char *s = strrchr(out, '/'), *b = strrchr(out, '\\');
    if (b > s) s = b;
    if (s) s[1] = 0;
    else out[0] = 0;
}

static long long file_size(const char *path)
{
    FILE *f = NULL;
    if (fopen_s(&f, path, "rb") != 0 || !f) return -1;
    _fseeki64(f, 0, SEEK_END);
    long long n = _ftelli64(f);
    fclose(f);
    return n;
}

static Track *add_track(PortDisc *d, const char *dir, const char *name, u32 lba, long long offset)
{
    if (d->n >= MAX_TRACKS) return NULL;
    Track *t = &d->t[d->n++];
    t->lba = lba;
    t->offset = offset;
    if (strchr(name, ':') || name[0] == '/' || name[0] == '\\') strncpy_s(t->path, sizeof t->path, name, _TRUNCATE);
    else _snprintf_s(t->path, sizeof t->path, _TRUNCATE, "%s%s", dir, name);
    return t;
}

/* An audio track runs from its first sector to the end of its file. */
static void add_audio(PortDisc *d, const char *dir, const char *name, u32 lba, long long offset)
{
    Track *t = add_track(d, dir, name, lba, offset);
    if (!t) return;
    long long size = file_size(t->path);
    t->audio = 1;
    t->sectors = size > offset ? (u32)((size - offset) / RAW) : 0;
}

/* .gdi: a count line, then "number lba type sector_size file offset" (file may be quoted). */
static void parse_gdi(PortDisc *d, FILE *f, const char *dir)
{
    char line[1024], name[512];
    unsigned num, lba, type, size;
    long long off;
    fgets(line, sizeof line, f);
    while (fgets(line, sizeof line, f)) {
        char *p = line;
        if (sscanf_s(p, "%u %u %u %u", &num, &lba, &type, &size) != 4) continue;
        for (int k = 0; k < 4; k++) {
            while (isspace((unsigned char)*p)) p++;
            while (*p && !isspace((unsigned char)*p)) p++;
        }
        while (isspace((unsigned char)*p)) p++;
        char *end = *p == '"' ? strchr(++p, '"') : p + strcspn(p, " \t\r\n");
        if (!end) continue;
        size_t len = (size_t)(end - p) < sizeof name - 1 ? (size_t)(end - p) : sizeof name - 1;
        memcpy(name, p, len);
        name[len] = 0;
        off = _atoi64(*end == '"' ? end + 1 : end);
        if (lba >= HD_LBA) add_toc(d, num, lba, type == 4);
        if (type == 4 && lba >= HD_LBA && size == RAW) add_track(d, dir, name, lba, off);
        if (type == 0 && lba >= HD_LBA && size == RAW) add_audio(d, dir, name, lba, off);
    }
}

/* Redump .cue: one FILE per track; the HD area's first file starts at LBA 45000 and later files follow it. */
static void parse_cue(PortDisc *d, FILE *f, const char *dir)
{
    char line[1024], file[512] = "", path[1024];
    unsigned track = 0, mm, ss, ff;
    int data = 0;
    u32 file_lba = HD_LBA;
    long long prev_size = -1;
    while (fgets(line, sizeof line, f)) {
        char *p = line;
        while (isspace((unsigned char)*p)) p++;
        if (!strncmp(p, "FILE \"", 6)) {
            char *q = strchr(p + 6, '"');
            if (!q) continue;
            if (track >= 3 && prev_size >= 0) file_lba += (u32)(prev_size / RAW);
            *q = 0;
            strncpy_s(file, sizeof file, p + 6, _TRUNCATE);
        } else if (sscanf_s(p, "TRACK %u", &track) == 1) {
            data = strstr(p, "MODE1") != NULL;
        } else if (sscanf_s(p, "INDEX 01 %u:%u:%u", &mm, &ss, &ff) == 3 && track >= 3) {
            u32 idx = (mm * 60 + ss) * 75 + ff;
            _snprintf_s(path, sizeof path, _TRUNCATE, "%s%s", dir, file);
            prev_size = file_size(path);
            add_toc(d, track, file_lba + idx, (u32)data);
            if (data) add_track(d, dir, file, file_lba + idx, (long long)idx * RAW);
            else add_audio(d, dir, file, file_lba + idx, (long long)idx * RAW);
        }
    }
}

PortDisc *disc_open(const char *path)
{
    PortDisc *d = (PortDisc *)calloc(1, sizeof *d);
    FILE *f = NULL;
    char dir[512];
    if (!d || fopen_s(&f, path, "r") != 0 || !f) {
        free(d);
        return NULL;
    }
    dir_of(path, dir, sizeof dir);
    size_t n = strlen(path);
    if (n > 4 && !_stricmp(path + n - 4, ".gdi")) parse_gdi(d, f, dir);
    else parse_cue(d, f, dir);
    fclose(f);
    if (!d->n) {
        port_log("disc: no high-density data track in %s\n", path);
        free(d);
        return NULL;
    }
    return d;
}

void disc_close(PortDisc *d)
{
    if (!d) return;
    for (int i = 0; i < d->n; i++)
        if (d->t[i].f) fclose(d->t[i].f);
    free(d);
}

static int read_sectors(PortDisc *d, u32 lba, u32 count, u8 *out)
{
    for (u32 k = 0; k < count; k++, lba++) {
        Track *t = NULL;
        for (int i = d->n - 1; i >= 0 && !t; i--)
            if (lba >= d->t[i].lba) t = &d->t[i];
        if (!t || t->audio) return 0;
        u8 raw[RAW];
        if (!t->f && (fopen_s(&t->f, t->path, "rb") != 0 || !t->f)) {
            t->f = NULL;
            return 0;
        }
        _fseeki64(t->f, t->offset + (long long)(lba - t->lba) * RAW, SEEK_SET);
        size_t got = fread(raw, 1, RAW, t->f);
        if (got != RAW || raw[0] != 0 || raw[1] != 0xff || raw[11] != 0 || raw[15] != 1) return 0;
        memcpy(out + (size_t)k * DATA, raw + HEAD, DATA);
    }
    return 1;
}

int disc_boot_name(PortDisc *d, char *out, int cap)
{
    u8 ip[DATA];
    if (cap < 2 || !read_sectors(d, HD_LBA, 1, ip)) return 0;
    int n = 0;
    for (int i = 0x60; i < 0x70 && ip[i] > ' ' && n < cap - 1; i++) out[n++] = (char)ip[i];
    out[n] = 0;
    return n > 0;
}

static u32 le32(const u8 *p) { return p[0] | (p[1] << 8) | (p[2] << 16) | ((u32)p[3] << 24); }

static int root_dir(PortDisc *d, u32 *lba, u32 *size)
{
    u8 pvd[DATA];
    if (!read_sectors(d, HD_LBA + 16, 1, pvd) || memcmp(pvd + 1, "CD001", 5)) return 0;
    *lba = le32(pvd + 156 + 2);
    *size = le32(pvd + 156 + 10);
    return 1;
}

/* Reads the directory at lba/size and calls visit for each record (its name without ";1"; "." and ".." for the
 * self and parent records) until visit returns nonzero. Returns -1 if it cannot be read, else visit's last result. */
typedef int (*DirVisit)(void *ctx, const HostDirEnt *e);
static int walk_dir(PortDisc *d, u32 lba, u32 size, DirVisit visit, void *ctx)
{
    u32 sectors = (size + DATA - 1) / DATA;
    u8 *buf = (u8 *)malloc((size_t)sectors * DATA);
    int stop = 0;
    if (!buf || !read_sectors(d, lba, sectors, buf)) {
        free(buf);
        return -1;
    }
    for (u32 i = 0; i < size && !stop;) {
        u32 n = buf[i];
        if (!n) {
            i = (i / DATA + 1) * DATA;
            continue;
        }
        const u8 *r = buf + i;
        u32 nl = r[32], len = 0;
        HostDirEnt e;
        e.lba = le32(r + 2);
        e.size = le32(r + 10);
        e.is_dir = (r[25] & 2) != 0;
        if (nl == 1 && r[33] <= 1) {
            strcpy_s(e.name, sizeof e.name, r[33] ? ".." : ".");
        } else {
            while (len < nl && len < sizeof e.name - 1 && r[33 + len] != ';') len++;
            memcpy(e.name, r + 33, len);
            e.name[len] = 0;
        }
        stop = visit(ctx, &e);
        i += n;
    }
    free(buf);
    return stop;
}

typedef struct {
    const char *name;
    size_t len;
    HostDirEnt *out;
} FindCtx;

static int find_visit(void *ctx, const HostDirEnt *e)
{
    FindCtx *f = (FindCtx *)ctx;
    if (strlen(e->name) != f->len || _strnicmp(e->name, f->name, f->len)) return 0;
    *f->out = *e;
    return 1;
}

/* Finds name (one path component, case-insensitive) in the directory at lba/size. */
static int find_entry(PortDisc *d, u32 lba, u32 size, const char *name, size_t len, HostDirEnt *out)
{
    FindCtx f = {name, len, out};
    return walk_dir(d, lba, size, find_visit, &f) == 1;
}

u8 *disc_read_file(PortDisc *d, const char *path, u32 *size_out)
{
    HostDirEnt e;
    if (!root_dir(d, &e.lba, &e.size)) return NULL;
    for (const char *p = path; *p;) {
        size_t len = strcspn(p, "/");
        if (!find_entry(d, e.lba, e.size, p, len, &e)) return NULL;
        p += len;
        if (*p == '/') p++;
    }
    u32 sectors = (e.size + DATA - 1) / DATA;
    u8 *buf = (u8 *)malloc((size_t)sectors * DATA + 1);
    if (!buf || !read_sectors(d, e.lba, sectors, buf)) {
        free(buf);
        return NULL;
    }
    *size_out = e.size;
    return buf;
}

/* The disc gdFs reads at run time (platform.h). */
static PortDisc *g_run_disc;

void disc_set_run(PortDisc *d) { g_run_disc = d; }

int host_disc_root(u32 *lba, u32 *size) { return g_run_disc && root_dir(g_run_disc, lba, size); }

int host_disc_read(u32 lba, u32 count, void *out)
{
    return g_run_disc && read_sectors(g_run_disc, lba, count, (u8 *)out);
}

int host_disc_find(u32 lba, u32 size, const char *name, HostDirEnt *out)
{
    return g_run_disc && find_entry(g_run_disc, lba, size, name, strlen(name), out);
}

typedef struct {
    HostDirEnt *out;
    int max, n;
} ListCtx;

static int list_visit(void *ctx, const HostDirEnt *e)
{
    ListCtx *l = (ListCtx *)ctx;
    if (!strcmp(e->name, ".") || !strcmp(e->name, "..")) return 0;
    if (l->n >= l->max) return 2;
    l->out[l->n++] = *e;
    return 0;
}

int host_disc_list(u32 lba, u32 size, HostDirEnt *out, int max)
{
    ListCtx l = {out, max, 0};
    if (!g_run_disc) return -1;
    int r = walk_dir(g_run_disc, lba, size, list_visit, &l);
    return r < 0 ? -1 : r == 2 ? -2 : l.n;
}

int host_disc_audio(u32 track, u32 *lba, u32 *sectors)
{
    const PortDisc *d = g_run_disc;
    for (int i = 0; d && i < d->ntoc; i++) {
        if (d->toc[i].num != track || d->toc[i].data) continue;
        for (int k = 0; k < d->n; k++)
            if (d->t[k].audio && d->t[k].lba == d->toc[i].lba) {
                *lba = d->t[k].lba;
                *sectors = d->t[k].sectors;
                return 1;
            }
    }
    return 0;
}

int host_disc_read_audio(u32 lba, u32 count, void *out)
{
    PortDisc *d = g_run_disc;
    for (int i = 0; d && i < d->n; i++) {
        Track *t = &d->t[i];
        if (!t->audio || lba < t->lba || lba + count > t->lba + t->sectors) continue;
        if (!t->f && (fopen_s(&t->f, t->path, "rb") != 0 || !t->f)) {
            t->f = NULL;
            return 0;
        }
        _fseeki64(t->f, t->offset + (long long)(lba - t->lba) * RAW, SEEK_SET);
        return fread(out, RAW, count, t->f) == count;
    }
    return 0;
}

/* The high-density area's TOC in the GD-ROM form: toc[track - 1] = control/ADR byte (0x41 data, 0x01 audio) << 24
 * | frame address (LBA + 150), unused entries all ones; then the first track, the last track and the lead-out. */
int host_disc_toc(u32 *toc)
{
    const PortDisc *d = g_run_disc;
    if (!d || !d->ntoc) return 0;
    for (int i = 0; i < 99; i++) toc[i] = 0xffffffffu;
    for (int i = 0; i < d->ntoc; i++) {
        const TocTrack *t = &d->toc[i];
        if (t->num >= 1 && t->num <= 99) toc[t->num - 1] = (t->data ? 0x41u : 0x01u) << 24 | (t->lba + 150u);
    }
    const TocTrack *first = &d->toc[0], *last = &d->toc[d->ntoc - 1];
    toc[99] = (first->data ? 0x41u : 0x01u) << 24 | first->num << 16;
    toc[100] = (last->data ? 0x41u : 0x01u) << 24 | last->num << 16;
    toc[101] = 0x41u << 24; /* the lead-out address is not known from the image list */
    return 1;
}
