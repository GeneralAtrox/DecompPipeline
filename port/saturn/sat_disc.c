/* The game disc: a cue sheet of per-track .bin files (MODE1/2352 data, raw audio) and the ISO 9660 tree on track 1.
 * Nearest existing: the Dreamcast kit's GD-ROM reader (port/dc) - GDI, 2048-byte sectors, no frame addresses. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "sat_disc.h"

enum { MAXFILES = 99, PREGAP = 150 };
typedef struct {
    FILE *f;
    u32 lba, nsect; /* where the file's first sector sits on the disc */
} Part;
static Part g_part[MAXFILES];
static SatTrack g_track[MAXFILES + 1];
static u32 g_nparts, g_ntracks, g_end;

static u32 frames(const char *mm_ss_ff)
{
    unsigned m = 0, s = 0, f = 0;
    sscanf(mm_ss_ff, "%u:%u:%u", &m, &s, &f);
    return (m * 60u + s) * 75u + f;
}

int sat_disc_open(const char *cue)
{
    char line[600], dir[520], path[1100];
    const char *slash;
    FILE *c;
    u32 lba = 0, idx0 = 0;
    if (!cue || !*cue) return 1;
    c = fopen(cue, "r");
    if (!c) return 1;
    slash = strrchr(cue, '\\');
    if (!slash) slash = strrchr(cue, '/');
    snprintf(dir, sizeof dir, "%.*s", slash ? (int)(slash - cue + 1) : 0, cue);
    while (fgets(line, sizeof line, c)) {
        char *q = strchr(line, '"');
        if (strstr(line, "FILE") && q) {
            char *e = strrchr(line, '"');
            Part *p = &g_part[g_nparts];
            long long size;
            *e = 0;
            snprintf(path, sizeof path, "%s%s", dir, q + 1);
            p->f = fopen(path, "rb");
            if (!p->f) {
                fclose(c);
                return 1;
            }
            _fseeki64(p->f, 0, SEEK_END);
            size = _ftelli64(p->f);
            p->lba = lba;
            p->nsect = (u32)(size / SAT_SECTOR);
            lba += p->nsect;
            g_nparts++;
            idx0 = 0;
        } else if (strstr(line, "TRACK")) {
            unsigned n = 0;
            char mode[32] = "";
            sscanf(strstr(line, "TRACK") + 5, "%u %31s", &n, mode);
            if (n >= 1 && n <= MAXFILES) {
                g_ntracks = n;
                g_track[n].ctrl = strcmp(mode, "AUDIO") == 0 ? 0x01 : 0x41;
            }
        } else if (strstr(line, "INDEX") && g_ntracks) {
            unsigned n = 0;
            char t[32] = "";
            sscanf(strstr(line, "INDEX") + 5, "%u %31s", &n, t);
            if (n == 0) idx0 = frames(t);
            if (n == 1) g_track[g_ntracks].fad = g_part[g_nparts - 1].lba + frames(t) + PREGAP;
            (void)idx0;
        }
    }
    fclose(c);
    g_end = lba + PREGAP;
    return g_nparts ? 0 : 1;
}

int sat_disc_present(void) { return g_nparts != 0; }
u32 sat_disc_ntracks(void) { return g_ntracks; }
const SatTrack *sat_disc_track(u32 tno) { return tno >= 1 && tno <= g_ntracks ? &g_track[tno] : NULL; }
u32 sat_disc_leadout(void) { return g_end; }

int sat_disc_read(u32 fad, u8 *raw)
{
    u32 lba;
    if (fad < PREGAP) return 0;
    lba = fad - PREGAP;
    for (u32 i = 0; i < g_nparts; i++) {
        Part *p = &g_part[i];
        if (lba >= p->lba && lba < p->lba + p->nsect) {
            _fseeki64(p->f, (long long)(lba - p->lba) * SAT_SECTOR, SEEK_SET);
            return fread(raw, 1, SAT_SECTOR, p->f) == SAT_SECTOR;
        }
    }
    return 0;
}

static u32 le32(const u8 *p) { return (u32)p[0] | (u32)p[1] << 8 | (u32)p[2] << 16 | (u32)p[3] << 24; }

static void record(const u8 *r, SatFile *out)
{
    u32 n = r[32];
    memset(out, 0, sizeof *out);
    out->dir_lba = le32(r + 2);
    out->fad = out->dir_lba + PREGAP;
    out->size = le32(r + 10);
    out->attr = r[25];
    out->unit = r[26];
    out->gap = r[27];
    if (n >= sizeof out->name) n = sizeof out->name - 1u;
    if (n == 1 && r[33] <= 1) {
        strcpy(out->name, r[33] ? ".." : ".");
    } else {
        memcpy(out->name, r + 33, n);
        out->name[n] = 0;
        if (strchr(out->name, ';')) *strchr(out->name, ';') = 0;
    }
}

int sat_disc_root(SatFile *root)
{
    u8 raw[SAT_SECTOR];
    if (!sat_disc_read(16 + PREGAP, raw)) return 1;
    record(raw + 16 + 156, root);
    return 0;
}

int sat_disc_dir(u32 dir_lba, u32 size, SatFile *out, int max)
{
    int n = 0;
    for (u32 off = 0; off < size && n < max; off += 2048u) {
        u8 raw[SAT_SECTOR];
        u32 pos = 0;
        if (!sat_disc_read(dir_lba + off / 2048u + PREGAP, raw)) break;
        while (pos < 2048u && raw[16 + pos] && n < max) {
            record(raw + 16 + pos, &out[n++]);
            pos += raw[16 + pos];
        }
    }
    return n;
}
