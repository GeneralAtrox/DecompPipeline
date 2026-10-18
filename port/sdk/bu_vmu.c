/* Platform layer: the memory card library (Shinobi's bu* calls) on host files. Each card is a 128 KB VMU flash image
 * (sdk/vmu_flash.c), kept in memory and written back whole to <save dir>/vmu_save_<socket>.bin after each change: the
 * file an emulator keeps, so saves move between them. A missing file is a freshly formatted card.
 *
 * Timing follows the library's. A TYPE_B call (save, load, delete, set attribute) only accepts the request; the
 * work is done at once on the image, and the progress callback (once per block moved) and the completion callback
 * reach the game at the next vertical blank, or at its next buStat on that drive, which games spin on. Until then
 * the drive is busy. buInit calls the game's init callback before it returns; each card's mount (BUD_OP_MOUNT)
 * completes at the first vertical blank after it, and the card is unknown until then.
 *
 * Cards: --vmu A1[,B1,..] picks the sockets (default A1; "none": no card). runtime/host.c places the files.
 *
 * Needs from the game's headers (game.h): BUS_TIME, BUS_FILEINFO and BUS_DISKINFO as the game's names for the SDK's
 * records (written here by the SDK's offsets), SdkProc, and the function names below as the game's registry has
 * them. List vmu_flash with this module in game.json "sdk". */
#include "game.h"
#include "vmu_flash.h"

#define DRIVES 8
#define ERR_OK 0
#define ERR_BUSY (-1)
#define ERR_NO_DISK ((s32)0xffffff01u)
#define ERR_DISK_FULL ((s32)0xffffff04u)
#define ERR_FILE_NOT_FOUND ((s32)0xffffff05u)
#define ERR_EXECFILE_EXIST ((s32)0xffffff09u)
#define OP_MOUNT 1
#define OP_DELETEFILE 7
#define OP_SETFILEATTR 0x0a
#define OP_LOADFILE 0x0b
#define OP_SAVEFILE 0x0c
#define FLAG_COPY_MASK 0xffu /* buSaveFile's flag: the copy flag (BUD_FLAG_COPY) */

typedef s32 (*CompleteFn)(s32 drive, s32 op, s32 status, u32 param);
typedef s32 (*ProgressFn)(s32 drive, s32 op, s32 count, s32 max);

typedef struct Card {
    int mounted, pending; /* pending: a request whose callbacks have not reached the game yet */
    s32 op, status;
    u32 moved;            /* blocks the request moved, for the progress callback */
    u32 find;             /* buFindNextFile's next directory entry */
    u8 image[VMFS_SIZE];
} Card;

static Card g_cards[DRIVES];
static int g_running;
static CompleteFn g_complete;
static ProgressFn g_progress;

static void file_name(char *out, s32 drive)
{
    const char *f = "vmu_save_A1.bin";
    u32 i = 0;
    for (; f[i]; i++) out[i] = f[i];
    out[i] = '\0';
    out[9] = (char)('A' + drive / 2);
    out[10] = (char)('1' + drive % 2);
}

/* A TYPE_B request: done now; its callbacks wait for the next vertical blank (or buStat). */
static s32 request(s32 drive, s32 op, const char *file, s32 status, u32 moved, int changed)
{
    Card *c = &g_cards[drive];
    char name[16];
    if (file)
        port_log("vmu: %c%d: %s %.12s: %s%08x, %u blocks\n", 'A' + drive / 2, 1 + drive % 2,
                 op == OP_SAVEFILE ? "save" : op == OP_LOADFILE ? "load" : op == OP_DELETEFILE ? "delete" : "attribute",
                 file, status == ERR_OK ? "ok " : "error ", (u32)status, moved);
    c->pending = 1;
    c->op = op;
    c->status = status;
    c->moved = moved;
    if (changed && status == ERR_OK) {
        file_name(name, drive);
        if (!host_save_write(name, c->image, VMFS_SIZE))
            port_log("vmu: cannot write %s: the change lasts only until the port exits\n", name);
    }
    return ERR_OK;
}

static int busy(s32 drive) { return drive < 0 || drive >= DRIVES || g_cards[drive].pending; }

/* A TYPE_B call's card when it can take a request (NULL: the drive is busy), and *status NO_DISK when unmounted. */
static Card *take(s32 drive, s32 *status)
{
    if (busy(drive)) return NULL;
    *status = g_cards[drive].mounted ? ERR_OK : ERR_NO_DISK;
    return &g_cards[drive];
}

/* A query's (TYPE_A) card when it can answer, else NULL and the error to return. */
static Card *ready(s32 drive, s32 *err)
{
    *err = busy(drive) ? ERR_BUSY : ERR_NO_DISK;
    return !busy(drive) && g_cards[drive].mounted ? &g_cards[drive] : NULL;
}

static void deliver(s32 drive)
{
    Card *c = &g_cards[drive];
    if (!c->pending) return;
    c->pending = 0;
    if (c->op == OP_MOUNT) c->mounted = c->status == ERR_OK;
    for (u32 k = 1; g_progress && k <= c->moved; k++) g_progress(drive, c->op, (s32)k, (s32)c->moved);
    if (g_complete) g_complete(drive, c->op, c->status, 0);
}

static void server(void *arg)
{
    (void)arg;
    for (s32 d = 0; g_running && d < DRIVES; d++) deliver(d);
}

/* --vmu: socket names (A1..D2) to a drive mask; "none": no card; default A1. */
static u32 sockets(void)
{
    const char *s = host_arg("--vmu");
    u32 mask = 0;
    if (!s) return 1u;
    for (; s[0] && s[1]; s++)
        if (s[0] >= 'A' && s[0] <= 'D' && (s[1] == '1' || s[1] == '2'))
            mask |= 1u << ((u32)(s[0] - 'A') * 2u + (u32)(s[1] - '1'));
    return mask;
}

s32 buInit(s32 capacity, s32 drive, void *work, SdkProc func)
{
    u32 use = sockets() & (u32)drive;
    (void)capacity;
    (void)work;
    for (s32 d = 0; d < DRIVES; d++) {
        Card *c = &g_cards[d];
        char name[16];
        int got;
        c->mounted = c->pending = 0;
        if (!((use >> d) & 1u)) continue;
        file_name(name, d);
        got = host_save_read(name, c->image, VMFS_SIZE);
        if (got != 1 || !vmfs_formatted(c->image)) {
            port_log("vmu: %c%d: a blank card%s\n", 'A' + d / 2, 1 + d % 2,
                     got < 0 ? " (not kept: a --fixed-clock run without --save-dir)" : "");
            vmfs_format(c->image);
        }
        port_log("vmu: %c%d: %u of %u blocks free\n", 'A' + d / 2, 1 + d % 2, vmfs_free(c->image), VMFS_USER_BLOCKS);
        request(d, OP_MOUNT, NULL, ERR_OK, 0, 0);
    }
    g_running = 1;
    port_add_vblank_task(server, NULL);
    if (func) func();
    return ERR_OK;
}

s32 buExit(void)
{
    for (s32 d = 0; d < DRIVES; d++)
        if (g_cards[d].pending) return ERR_BUSY;
    g_running = 0;
    return ERR_OK;
}

void buSetCompleteCallback(SdkProc func) { g_complete = (CompleteFn)func; }
void buSetProgressCallback(SdkProc func) { g_progress = (ProgressFn)func; }

s32 buStat(s32 drive)
{
    if (drive >= 0 && drive < DRIVES) deliver(drive);
    return ERR_OK;
}

s32 buSaveFile(s32 drive, const char *fileName, const void *buffer, u32 blocks, const BUS_TIME *time, u32 flag)
{
    s32 status;
    Card *c = take(drive, &status);
    if (!c) return ERR_BUSY;
    if (status == ERR_OK) {
        int r = vmfs_save(c->image, fileName, (const u8 *)buffer, blocks, (const u8 *)time, (u8)(flag & FLAG_COPY_MASK));
        status = r == VMFS_OK ? ERR_OK : r == VMFS_GAME ? ERR_EXECFILE_EXIST : ERR_DISK_FULL;
    }
    return request(drive, OP_SAVEFILE, fileName, status, status == ERR_OK ? blocks : 0, 1);
}

s32 buLoadFile(s32 drive, const char *fileName, void *buffer, u32 blocks)
{
    s32 status;
    Card *c = take(drive, &status);
    u32 moved = 0;
    int i;
    if (!c) return ERR_BUSY;
    if (status == ERR_OK) {
        i = vmfs_find(c->image, fileName);
        status = i < 0 ? ERR_FILE_NOT_FOUND : ERR_OK;
        if (i >= 0) moved = vmfs_load(c->image, (u32)i, (u8 *)buffer, blocks);
    }
    return request(drive, OP_LOADFILE, fileName, status, moved, 0);
}

s32 buDeleteFile(s32 drive, const char *fileName)
{
    s32 status;
    Card *c = take(drive, &status);
    int i;
    if (!c) return ERR_BUSY;
    if (status == ERR_OK) {
        i = vmfs_find(c->image, fileName);
        status = i < 0 ? ERR_FILE_NOT_FOUND : ERR_OK;
        if (i >= 0) vmfs_delete(c->image, (u32)i);
    }
    return request(drive, OP_DELETEFILE, fileName, status, 0, 1);
}

/* The header offset is ignored, as the library documents. */
s32 buSetFileAttr(s32 drive, const char *fileName, u32 header, s32 copyFlag)
{
    s32 status;
    Card *c = take(drive, &status);
    int i;
    (void)header;
    if (!c) return ERR_BUSY;
    if (status == ERR_OK) {
        i = vmfs_find(c->image, fileName);
        status = i < 0 ? ERR_FILE_NOT_FOUND : ERR_OK;
        if (i >= 0) vmfs_entry(c->image, (u32)i)[1] = (u8)copyFlag;
    }
    return request(drive, OP_SETFILEATTR, fileName, status, 0, 1);
}

s32 buIsExistFile(s32 drive, const char *fileName)
{
    s32 err;
    Card *c = ready(drive, &err);
    return !c ? err : vmfs_find(c->image, fileName) < 0 ? ERR_FILE_NOT_FOUND : ERR_OK;
}

s32 buGetFileSize(s32 drive, const char *fileName)
{
    s32 err;
    Card *c = ready(drive, &err);
    int i = c ? vmfs_find(c->image, fileName) : -1;
    return !c ? err : i < 0 ? ERR_FILE_NOT_FOUND : (s32)vmfs_rd16(vmfs_entry(c->image, (u32)i) + 0x18);
}

/* BUS_FILEINFO {u32 filesize; u16 blocks; u8 type (1: a game), copyflag; u16 headerofs; BUS_TIME time}. */
s32 buGetFileInfo(s32 drive, const char *fileName, BUS_FILEINFO *info)
{
    s32 err;
    Card *c = ready(drive, &err);
    int i = c ? vmfs_find(c->image, fileName) : -1;
    u8 out[18];
    const u8 *e;
    if (!c) return err;
    if (i < 0) return ERR_FILE_NOT_FOUND;
    e = vmfs_entry(c->image, (u32)i);
    vmfs_wr16(out, vmfs_rd16(e + 0x18) * VMFS_BLOCK); /* filesize: a u32, at most 200 * 512 */
    vmfs_wr16(out + 2, vmfs_rd16(e + 0x18) * VMFS_BLOCK >> 16);
    vmfs_wr16(out + 4, vmfs_rd16(e + 0x18));
    out[6] = (u8)(e[0] == VMFS_TYPE_GAME);
    out[7] = e[1];
    vmfs_wr16(out + 8, vmfs_rd16(e + 0x1a));
    vmfs_bus_time(out + 10, e + 0x10);
    vmfs_copy((u8 *)info, out, sizeof out);
    return ERR_OK;
}

/* BUS_DISKINFO {u8 volume[32]; u16 total_blocks, total_user_blocks, free_blocks, free_user_blocks,
 * total_exe_blocks, free_exe_blocks, block_size, icon_no; BUS_TIME time}. */
s32 buGetDiskInfo(s32 drive, BUS_DISKINFO *info)
{
    s32 err;
    Card *c = ready(drive, &err);
    u8 out[0x38];
    const u8 *root;
    _Static_assert(sizeof(BUS_DISKINFO) == 0x38, "BUS_DISKINFO is 0x38 bytes");
    if (!c) return err;
    root = vmfs_root(c->image);
    vmfs_copy(out, root + 0x10, 0x20);
    vmfs_wr16(out + 0x20, VMFS_BLOCKS);
    vmfs_wr16(out + 0x22, VMFS_USER_BLOCKS);
    vmfs_wr16(out + 0x24, vmfs_free(c->image));
    vmfs_wr16(out + 0x26, vmfs_free(c->image));
    vmfs_wr16(out + 0x28, VMFS_USER_BLOCKS);
    vmfs_wr16(out + 0x2a, vmfs_free_from_start(c->image));
    vmfs_wr16(out + 0x2c, VMFS_BLOCK);
    vmfs_wr16(out + 0x2e, vmfs_rd16(root + 0x4e));
    vmfs_bus_time(out + 0x30, root + 0x30);
    vmfs_copy((u8 *)info, out, sizeof out);
    return ERR_OK;
}

/* The directory in order; fileName gets "" and the result is BUD_ERR_FILE_NOT_FOUND past the last file. */
static s32 find_from(s32 drive, char *fileName, int first)
{
    s32 err;
    Card *c = ready(drive, &err);
    fileName[0] = '\0';
    if (!c) return err;
    if (first) c->find = 0;
    for (; c->find < VMFS_ENTRIES; c->find++)
        if (vmfs_entry(c->image, c->find)[0]) {
            vmfs_name(vmfs_entry(c->image, c->find++), fileName);
            return ERR_OK;
        }
    return ERR_FILE_NOT_FOUND;
}

s32 buFindFirstFile(s32 drive, char *fileName) { return find_from(drive, fileName, 1); }
s32 buFindNextFile(s32 drive, char *fileName) { return find_from(drive, fileName, 0); }
