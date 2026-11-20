/* Loose files beside the disc image, for gdi.c (split from it for the kit's 400-line cap). */
#include <io.h>
#include <stdio.h>
#include <string.h>
#include "gdi_int.h"

const char *host_arg(const char *name); /* base/clock.c */

#define DATA GDI_DATA

/* Loose files (game.json loose_dir, --loose-dir DIR): files in a folder beside the disc image appear in the disc's
 * directories, at sector numbers past any GD-ROM's last (LOOSE_LBA up, gdi_int.h), and are read from the host file.
 * A file in the folder's root goes in the disc's root; one in a subfolder goes in the disc directory of the same path (a subfolder the
 * disc has no directory for is left out, and logged). A loose file hides a disc file of the same name. */
#define LOOSE_MAX 64
typedef struct {
    u32 dir_lba; /* the disc directory it appears in */
    u32 lba, size;
    char name[32];
    char path[600];
    FILE *f;
} Loose;
static Loose g_loose[LOOSE_MAX];
static int g_nloose;
static char g_loose_root[520];
static int g_loose_on;

int loose_read(u32 lba, u8 *out)
{
    for (int i = 0; i < g_nloose; i++) {
        Loose *l = &g_loose[i];
        u32 sectors = (l->size + DATA - 1) / DATA;
        if (lba < l->lba || lba >= l->lba + sectors) continue;
        if (!l->f && (fopen_s(&l->f, l->path, "rb") != 0 || !l->f)) {
            l->f = NULL;
            return 0;
        }
        u32 at = (lba - l->lba) * DATA, n = l->size - at < DATA ? l->size - at : DATA;
        _fseeki64(l->f, at, SEEK_SET);
        if (fread(out, 1, n, l->f) != n) return 0;
        memset(out + n, 0, DATA - n);
        return 1;
    }
    return 0;
}

int loose_hidden(u32 dir_lba, const char *name)
{
    for (int k = 0; k < g_nloose; k++)
        if (g_loose[k].dir_lba == dir_lba && !_stricmp(g_loose[k].name, name)) return 1;
    return 0;
}

/* gdi.c's walk_dir: the directory's loose files, first; visit's first nonzero result, else 0 */
int loose_visit(u32 dir_lba, DirVisit visit, void *ctx)
{
    int stop = 0;
    for (int k = 0; k < g_nloose && !stop; k++) {
        if (g_loose[k].dir_lba != dir_lba) continue;
        HostDirEnt e;
        e.lba = g_loose[k].lba;
        e.size = g_loose[k].size;
        e.is_dir = 0;
        strcpy_s(e.name, sizeof e.name, g_loose[k].name);
        stop = visit(ctx, &e);
    }
    return stop;
}

/* The loose folder's files, each under the disc directory (dir_lba/dir_size) of its subfolder's path. */
static void loose_scan(PortDisc *d, const char *host, u32 dir_lba, u32 dir_size, int depth)
{
    char pattern[600];
    struct __finddata64_t fd;
    intptr_t h;
    sprintf_s(pattern, sizeof pattern, "%s\\*", host);
    h = _findfirst64(pattern, &fd);
    if (h == -1) return;
    do {
        char path[600];
        if (!strcmp(fd.name, ".") || !strcmp(fd.name, "..")) continue;
        sprintf_s(path, sizeof path, "%s\\%s", host, fd.name);
        if (fd.attrib & _A_SUBDIR) {
            HostDirEnt e;
            if (depth < 8 && gdi_find_entry(d, dir_lba, dir_size, fd.name, strlen(fd.name), &e) && e.is_dir) {
                loose_scan(d, path, e.lba, e.size, depth + 1);
            } else {
                port_log("loose: %s has no directory on the disc; left out\n", path);
            }
            continue;
        }
        if (strlen(fd.name) >= sizeof g_loose[0].name || fd.size <= 0 || fd.size > 0x7fffffff) {
            port_log("loose: %s left out (name over 31 characters, empty or too big)\n", path);
            continue;
        }
        if (g_nloose >= LOOSE_MAX) {
            port_log("loose: more than %d files; %s left out\n", LOOSE_MAX, path);
            continue;
        }
        Loose *l = &g_loose[g_nloose];
        Loose *prev = g_nloose ? &g_loose[g_nloose - 1] : NULL;
        l->dir_lba = dir_lba;
        l->lba = prev ? prev->lba + (prev->size + DATA - 1) / DATA + 16u : LOOSE_LBA;
        l->size = (u32)fd.size;
        strcpy_s(l->name, sizeof l->name, fd.name);
        strcpy_s(l->path, sizeof l->path, path);
        l->f = NULL;
        g_nloose++;
        port_log("loose: %s, %u bytes, in the disc directory at LBA %u\n", path, l->size, dir_lba);
    } while (_findnext64(h, &fd) == 0);
    _findclose(h);
}

void disc_loose_start(const char *image)
{
    const char *arg = host_arg("--loose-dir");
    u32 lba, size;
    struct __finddata64_t fd;
    intptr_t h;
    if (arg && arg[0]) {
        strcpy_s(g_loose_root, sizeof g_loose_root, arg);
    } else {
#ifdef PORT_LOOSE_DIR
        char dir[512];
        gdi_dir_of(image, dir, sizeof dir);
        sprintf_s(g_loose_root, sizeof g_loose_root, "%s%s", dir, PORT_LOOSE_DIR); /* dir ends in its separator */
#else
        (void)image;
        return;
#endif
    }
    h = _findfirst64(g_loose_root, &fd);
    if (h == -1) return; /* no folder: the disc as it is */
    _findclose(h);
    if (!(fd.attrib & _A_SUBDIR) || !gdi_run_disc() || !gdi_root_dir(gdi_run_disc(), &lba, &size)) return;
    g_loose_on = 1;
    loose_scan(gdi_run_disc(), g_loose_root, lba, size, 0);
    port_log("loose: %d files from %s\n", g_nloose, g_loose_root);
}

const char *host_loose_dir(void) { return g_loose_on ? g_loose_root : NULL; }
