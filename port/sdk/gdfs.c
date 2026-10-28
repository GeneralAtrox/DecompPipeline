/* Platform layer: the gdFs file system (Shinobi's GD-ROM library) over the disc image, read at run time through
 * the host (platform.h host_disc_*). Every read is synchronous: it is done when the call returns.
 *
 * Directory records keep the layout a game reads from them: a 16-byte header, then 44-byte entries {fad, size,
 * flags, sid, name[32], pad}. The header's last two words hold the directory's own LBA and size so gdFsSetDir can
 * make it current. Listings leave out "." and "..": a game that walks its directory tree recurses into every entry
 * flagged as a directory, so a listed "." would never end.
 *
 * gdFsGetStat: games wait while it returns 1, the "transfer busy" value, so a finished transfer returns 2.
 * Streaming readers (Gd_StreamGetStat, Gd_ServerTick) see each request accepted and already done.
 *
 * Needs from the game's headers (game.h): GDFS and GDFS_DIRREC as pointers to struct GdfsHandle and struct
 * GdfsDirRec (defined here), GDD_DRVSTAT_STANDBY / _PLAY / _PAUSE, GDD_SEEK_CUR / _END and SdkProc, and the
 * function names below as the game's registry has them (the Gd_* ones are registry names for library functions
 * with no SDK name; tools/gen_port.py replaces only the names the registry declares). */
#include "game.h"
#include "platform.h"

/* The library's return codes and entry flags, by value (the game compares against these numbers). */
#define FS_ERR_OK 0
#define FS_ERR_DISC (-5)
#define FS_ERR_DIRREC (-6)
#define FS_ERR_NOTFOUND (-8)
#define FS_ERR_NOHNDL (-9)
#define FS_ERR_ILLHNDL (-10)
#define FS_ERR_NOTDIR (-11)
#define FS_ERR_DIROVER (-12)
#define FS_ERR_32ALIGN (-14)
#define FS_ERR_SIZE (-15)
#define FS_ERR_SEEK (-16)
#define FS_ERR_READ (-19)
#define FS_ERR_EOF (-22)
#define FS_ENTRY_EXISTS 0x01u
#define FS_ENTRY_DIR 0x02u
#define FS_TRANSFER_DONE 2
#define GD_SECTOR 2048u
#define GD_FAD_OFFSET 150u /* frame address = LBA + 150 */
#define GD_MAX_OPEN 16
#define GD_MAX_LIST 0x400
#define GD_NAME 32

typedef struct GdfsEntry {
    u32 fad;
    u32 size;
    u8 flags;
    u8 sid;
    char name[GD_NAME];
    u8 pad[2];
} GdfsEntry; /* 0x2c bytes */

struct GdfsDirRec {
    s32 count;
    s32 max_entries;
    u32 dir_lba; /* the SDK's dir_fad slot */
    u32 dir_size; /* the SDK's pad slot */
    GdfsEntry entries[1];
};

struct GdfsHandle {
    s32 used;
    u32 lba;
    u32 size;
    s32 sectors;
    s32 pos; /* in sectors */
};

static struct GdfsHandle g_handles[GD_MAX_OPEN];
static HostDirEnt g_list[GD_MAX_LIST];
static u32 g_root_lba, g_root_size, g_cur_lba, g_cur_size;
static s32 g_mounted;
static s32 g_drive_stat = GDD_DRVSTAT_STANDBY;

static int is_sep(char c) { return c == '\\' || c == '/'; }

static char upper(char c) { return c >= 'a' && c <= 'z' ? (char)(c - 32) : c; }

static int same_name(const char *a, const char *b)
{
    while (*a && upper(*a) == upper(*b)) a++, b++;
    return upper(*a) == upper(*b);
}

static int mount(void)
{
    if (!g_mounted && host_disc_root(&g_root_lba, &g_root_size)) {
        g_cur_lba = g_root_lba;
        g_cur_size = g_root_size;
        g_mounted = 1;
    }
    return g_mounted;
}

/* Resolves a directory path ("\\A\\B", "A/B", "X:\\A", ".", ".."; empty or NULL is the current directory). */
static s32 resolve_dir(const char *path, u32 *lba, u32 *size)
{
    const char *p = path;
    HostDirEnt e;
    char part[GD_NAME];
    *lba = g_cur_lba;
    *size = g_cur_size;
    if (!p) return FS_ERR_OK;
    for (const char *q = p; *q; q++)
        if (*q == ':') p = q + 1;
    if (is_sep(*p)) {
        *lba = g_root_lba;
        *size = g_root_size;
    }
    while (*p) {
        int n = 0;
        while (is_sep(*p)) p++;
        while (*p && !is_sep(*p))
            if (n < GD_NAME - 1) part[n++] = *p++;
            else p++;
        part[n] = 0;
        if (!n || (n == 1 && part[0] == '.')) continue;
        if (!host_disc_find(*lba, *size, part, &e)) return FS_ERR_NOTFOUND;
        if (!e.is_dir) return FS_ERR_NOTDIR;
        *lba = e.lba;
        *size = e.size;
    }
    return FS_ERR_OK;
}

s32 gdFsInit(s32 maxOpen, void *work, s32 maxDirent, void *dirBuffer)
{
    (void)maxOpen, (void)work, (void)maxDirent, (void)dirBuffer;
    if (!mount()) {
        port_log("gdfs: no ISO 9660 volume on the disc\n");
        return FS_ERR_DIRREC;
    }
    port_log("gdfs: root directory at LBA %u, %u bytes\n", g_root_lba, g_root_size);
    return FS_ERR_OK;
}

void gdFsFinish(void)
{
    for (int i = 0; i < GD_MAX_OPEN; i++) g_handles[i].used = 0;
    g_mounted = 0;
}

GDFS_DIRREC gdFsCreateDirhn(void *dirBuffer, s32 maxDirent)
{
    GDFS_DIRREC rec = (GDFS_DIRREC)dirBuffer;
    if (!rec || maxDirent <= 0) return NULL;
    rec->count = 0;
    rec->max_entries = maxDirent;
    rec->dir_lba = 0;
    rec->dir_size = 0;
    return rec;
}

s32 gdFsLoadDir(const char *dirName, GDFS_DIRREC dirRec)
{
    u32 lba, size;
    s32 max, n, err;
    if (!dirRec) return FS_ERR_DIRREC;
    if (!mount()) return FS_ERR_DIRREC;
    err = resolve_dir(dirName, &lba, &size);
    if (err != FS_ERR_OK) return err;
    max = dirRec->max_entries < GD_MAX_LIST ? dirRec->max_entries : GD_MAX_LIST;
    n = host_disc_list(lba, size, g_list, max);
    if (n == -1) return FS_ERR_READ;
    if (n == -2) {
        port_log("gdfs: %s has more than %d entries\n", dirName ? dirName : "(current)", max);
        return FS_ERR_DIROVER;
    }
    for (s32 i = 0; i < n; i++) {
        GdfsEntry *d = &dirRec->entries[i];
        int k = 0;
        d->fad = g_list[i].lba + GD_FAD_OFFSET;
        d->size = g_list[i].size;
        d->flags = (u8)(FS_ENTRY_EXISTS | (g_list[i].is_dir ? FS_ENTRY_DIR : 0u));
        d->sid = 0;
        for (; k < GD_NAME - 1 && g_list[i].name[k]; k++) d->name[k] = g_list[i].name[k];
        for (; k < GD_NAME; k++) d->name[k] = 0;
        d->pad[0] = d->pad[1] = 0;
    }
    dirRec->count = n;
    dirRec->dir_lba = lba;
    dirRec->dir_size = size;
    return FS_ERR_OK;
}

s32 gdFsSetDir(GDFS_DIRREC dirRec)
{
    if (!dirRec || !dirRec->dir_size) return FS_ERR_DIRREC;
    g_cur_lba = dirRec->dir_lba;
    g_cur_size = dirRec->dir_size;
    return FS_ERR_OK;
}

s32 gdFsChangeDir(const char *dirName)
{
    u32 lba, size;
    s32 err;
    if (!mount()) return FS_ERR_DIRREC;
    err = resolve_dir(dirName, &lba, &size);
    if (err == FS_ERR_OK) {
        g_cur_lba = lba;
        g_cur_size = size;
    }
    return err;
}

/* Finds a file: in dirRec's entries when the name has no directory part, else through the path. */
static s32 find_file(const char *fileName, GDFS_DIRREC dirRec, u32 *lba, u32 *size)
{
    const char *base = fileName;
    HostDirEnt e;
    char dir[256];
    u32 dlba, dsize;
    int n = 0;
    s32 err;
    for (const char *q = fileName; *q; q++)
        if (is_sep(*q) || *q == ':') base = q + 1;
    if (base == fileName && dirRec) {
        for (s32 i = 0; i < dirRec->count; i++) {
            const GdfsEntry *d = &dirRec->entries[i];
            if ((d->flags & FS_ENTRY_DIR) || !same_name(d->name, fileName)) continue;
            *lba = d->fad - GD_FAD_OFFSET;
            *size = d->size;
            return FS_ERR_OK;
        }
        return FS_ERR_NOTFOUND;
    }
    for (const char *q = fileName; q < base && n < (int)sizeof dir - 1; q++) dir[n++] = *q;
    dir[n] = 0;
    err = resolve_dir(n ? dir : NULL, &dlba, &dsize);
    if (err != FS_ERR_OK) return err;
    if (!host_disc_find(dlba, dsize, base, &e) || e.is_dir) return FS_ERR_NOTFOUND;
    *lba = e.lba;
    *size = e.size;
    return FS_ERR_OK;
}

GDFS gdFsOpen(const char *fileName, GDFS_DIRREC dirRec)
{
    u32 lba, size;
    if (!fileName || !mount()) return NULL;
    if (find_file(fileName, dirRec, &lba, &size) != FS_ERR_OK) return NULL;
    for (int i = 0; i < GD_MAX_OPEN; i++) {
        GDFS h = &g_handles[i];
        if (h->used) continue;
        h->used = 1;
        h->lba = lba;
        h->size = size;
        h->sectors = (s32)((size + GD_SECTOR - 1) / GD_SECTOR);
        h->pos = 0;
        return h;
    }
    port_log("gdfs: no free handle for %s\n", fileName);
    return NULL;
}

void gdFsClose(GDFS gdfs)
{
    if (gdfs) gdfs->used = 0;
}

static int valid(GDFS h) { return h && h->used; }

s32 gdFsGetFileSize(GDFS gdfs, s32 *size)
{
    if (!valid(gdfs)) return 0; /* FALSE */
    *size = (s32)gdfs->size;
    return 1;
}

s32 gdFsSeek(GDFS gdfs, s32 offset, s32 origin)
{
    s32 pos;
    if (!valid(gdfs)) return FS_ERR_ILLHNDL;
    pos = origin == GDD_SEEK_CUR ? gdfs->pos + offset : origin == GDD_SEEK_END ? gdfs->sectors + offset : offset;
    if (pos < 0 || pos > gdfs->sectors) return FS_ERR_SEEK;
    gdfs->pos = pos;
    return FS_ERR_OK;
}

s32 gdFsTell(GDFS gdfs) { return valid(gdfs) ? gdfs->pos : FS_ERR_ILLHNDL; }

/* Reads whole sectors from the position; a read that runs past the end stops there and zero-fills the rest. */
s32 gdFsRead(GDFS gdfs, s32 sectors, void *buffer)
{
    s32 count;
    u8 *out = (u8 *)buffer;
    if (!valid(gdfs)) return FS_ERR_ILLHNDL;
    if (sectors <= 0) return FS_ERR_SIZE;
    if (gdfs->pos >= gdfs->sectors) return FS_ERR_EOF;
    count = gdfs->sectors - gdfs->pos < sectors ? gdfs->sectors - gdfs->pos : sectors;
    if (!host_disc_read(gdfs->lba + (u32)gdfs->pos, (u32)count, out)) {
        port_log("gdfs: read error at LBA %u\n", gdfs->lba + (u32)gdfs->pos);
        return FS_ERR_READ;
    }
    for (u32 i = (u32)count * GD_SECTOR; i < (u32)sectors * GD_SECTOR; i++) out[i] = 0;
    gdfs->pos += count;
    return FS_ERR_OK;
}

/* The library's request returns the sectors it took (clipped at the end of the file), 0 when it took none (the drive
 * busy, or the position at the end), or an error. Callers loop `while (r == 0)` and advance by the count (a
 * movie reader does), so a plain 0 for "done" would read on to the end of the file. */
s32 gdFsReqRd32(GDFS gdfs, s32 sectors, void *buffer)
{
    s32 count;
    if (!valid(gdfs)) return FS_ERR_ILLHNDL;
    if (((uintptr_t)buffer & 31u) != 0) return FS_ERR_32ALIGN;
    count = gdfs->sectors - gdfs->pos < sectors ? gdfs->sectors - gdfs->pos : sectors;
    if (count <= 0) return 0;
    return gdFsRead(gdfs, count, buffer) == FS_ERR_OK ? count : FS_ERR_READ;
}

s32 gdFsGetStat(GDFS gdfs) { return valid(gdfs) ? FS_TRANSFER_DONE : FS_ERR_ILLHNDL; }

/* A handle's error status (the library's gdFsGetErrStat field): every read has ended without an error. */
s32 Gd_StreamGetStat(void *stream) { return valid((GDFS)stream) ? FS_ERR_OK : FS_ERR_ILLHNDL; }

void Gd_ServerTick(void) {} /* the library's server step: reads are done when their call returns */

void gdFsEntryErrFuncAll(SdkProc func, void *obj) { (void)func, (void)obj; } /* reads never fail asynchronously */

s32 Gd_RemountDisc(void)
{
    g_mounted = 0;
    return mount() ? FS_ERR_OK : FS_ERR_DIRREC;
}

/* The drive's CD audio plays the image's audio tracks (platform.h host_cd_*). A play that ends leaves the drive in
 * pause, as on the console, which is how games see a track end. An image without the tracks plays silence. */
static int g_da_live; /* the drive status follows host_cd_playing */

s32 gdFsGetDrvStat(void)
{
    if (g_drive_stat == GDD_DRVSTAT_PLAY && g_da_live && !host_cd_playing()) g_drive_stat = GDD_DRVSTAT_PAUSE;
    return g_drive_stat;
}

s32 gdFsReqDrvStat(void) { return FS_ERR_OK; }

/* The library's own handle, the one its drive requests (gdFsReqDrvStat) run on: there once gdFsInit has mounted the
 * disc, NULL before. Gd_FsGetHandleStatus is a handle's request status (FS_ERR_ILLHNDL for no handle), and every
 * request here has finished when the call that made it returns. */
static struct GdfsHandle g_system;

GDFS Gd_GetSystemHandle(void) { return g_mounted ? &g_system : NULL; }

s32 Gd_FsGetHandleStatus(GDFS gdfs)
{
    return valid(gdfs) || (gdfs == &g_system && g_mounted) ? FS_TRANSFER_DONE : FS_ERR_ILLHNDL;
}

s32 gdFsDaPlay(s32 firstTrack, s32 lastTrack, s32 repeat)
{
    g_da_live = host_cd_play((u32)firstTrack, (u32)lastTrack, (u32)repeat);
    g_drive_stat = GDD_DRVSTAT_PLAY;
    return FS_ERR_OK;
}

s32 Gd_DaStop(void)
{
    host_cd_stop();
    g_da_live = 0;
    g_drive_stat = GDD_DRVSTAT_STANDBY;
    return FS_ERR_OK;
}

s32 Gd_DaPause(void)
{
    host_cd_pause(1);
    g_drive_stat = GDD_DRVSTAT_PAUSE;
    return FS_ERR_OK;
}

s32 Gd_DaResume(void)
{
    host_cd_pause(0);
    g_drive_stat = GDD_DRVSTAT_PLAY;
    return FS_ERR_OK;
}

/* The table of contents of the high-density area (area 1), from the image's track list. */
s32 Gd_GetToc(s32 area, u32 *toc)
{
    if (area != 1 || !host_disc_toc(toc)) return FS_ERR_DISC;
    return FS_ERR_OK;
}
