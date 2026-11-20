/* The game's files: the kernel's file calls on the host's file system. XB_GAME_DIR is the package's contents on disk (tools/stfs.py extracts
 * them), and it stands for the "game:" drive. A path on any other drive stops, so each is answered on purpose. Like every import, a call is
 * written when a run stops on it, and its case names that stop. */
#include <windows.h>
#include <stdlib.h>
#include <string.h>
#include "xb.h"
#include "console.h"

/* A file the game holds: a token in a range of its own, so that NtClose can tell it from a kernel handle. Its position is the host handle's, so a
 * read or write at an offset (a seek, then the transfer) holds the file's lock: Too Human's loader thread 82767520 and its render thread's stream
 * reads (82a6f128) run at once, and two seeks before two reads gave each the other's data */
#define FILE_BASE 0x7FFF0000u
#define FILE_MAX 1024
typedef struct { int used; char path[MAX_PATH]; HANDLE find; int started, listed; HANDLE h; SRWLOCK at; } XbFile;   /* listed: a directory query gave an entry */
static XbFile s_files[FILE_MAX];
static SRWLOCK s_files_lock = SRWLOCK_INIT;

/* The last reads, so a stop on an overwritten object can name a read that landed on it: where in guest memory, and from which file
 * and where in it (Too Human's heap was found full of "0000" at a fault) */
#define READ_RING 4096u   /* the recent reads a stop can name */
static struct { u32 buf, len, file; u64 at; } s_reads[READ_RING];
static volatile LONG s_read_n;

void xb_file_reads_over(u32 lo, u32 hi)
{
    LONG n = s_read_n, i, first = n > (LONG)READ_RING ? n - (LONG)READ_RING : 0, shown = 0;
    for (i = n - 1; i >= first && shown < 8; i--) {   /* the newest eight */
        unsigned k = (unsigned)i & (READ_RING - 1);
        if (s_reads[k].buf < hi && lo < s_reads[k].buf + s_reads[k].len && ++shown)
            port_log("read %ld (of %ld) over it: %08x + %x from %s at %llx\n", i, n, s_reads[k].buf, s_reads[k].len,
                     s_files[s_reads[k].file].path, s_reads[k].at);
    }
}

/* The reads since the last call, for the run's samples (XB_SAMPLE): how many, how much, and the newest one's file: a load still going, or nothing read */
void xb_file_reads_since(void)
{
    static LONG last;
    LONG n = s_read_n, i, first = last > n - (LONG)READ_RING ? last : n - (LONG)READ_RING;
    unsigned long long bytes = 0;
    for (i = first; i < n; i++) bytes += s_reads[(unsigned)i & (READ_RING - 1)].len;
    port_log("file reads since the last sample: %ld, %llu bytes%s%s\n", n - last, bytes, n > last ? ", newest from " : "",
             n > last ? s_files[s_reads[(unsigned)(n - 1) & (READ_RING - 1)].file].path : "");
    last = n;
}

static u32 file_new(const char *path)
{
    unsigned i;
    AcquireSRWLockExclusive(&s_files_lock);
    for (i = 0; i < FILE_MAX && s_files[i].used; i++) {}
    if (i == FILE_MAX) xb_stop("out_of_memory", "open files", i);
    memset(&s_files[i], 0, sizeof s_files[i]);
    s_files[i].used = 1;
    s_files[i].find = INVALID_HANDLE_VALUE;
    strcpy(s_files[i].path, path);
    ReleaseSRWLockExclusive(&s_files_lock);
    return FILE_BASE + 4u * i;
}

static XbFile *file_of(u32 guest)
{
    u32 n = (guest - FILE_BASE) >> 2;
    if (guest < FILE_BASE || n >= FILE_MAX || !s_files[n].used) xb_stop("bad_handle", "a file the port did not open", guest);
    return &s_files[n];
}

/* struct { u16 length, u16 room, u32 text } *, in guest memory */
static void ansi_of(u32 str, char *out, unsigned room)
{
    u32 n = rd16(str), p = rd32(str + 4), i;
    if (n >= room) xb_stop("bad_path", "a path longer than the host's", n);
    for (i = 0; i < n; i++) out[i] = (char)rd8(p + i);
    out[n] = 0;
}

/* Names on drives only a development kit has: a retail console finds nothing there, so neither does the port. Each row names its first stop */
static const char *const s_kit_only[] = {
    "ShaderDumpxe:",    /* Too Human: 231 under 82983fe0 on the render thread, a shader dump folder ("ShaderDump" joined to xe:\CompareBackEnds) */
    0
};

/* struct { u32 root, u32 name, u32 attributes } * to a host path, or "" for a name on a drive a retail console lacks, which every host call finds missing */
static void host_path(u32 attrs, char *out, unsigned room)
{
    char name[MAX_PATH];
    const char *dir = getenv("XB_GAME_DIR");
    size_t n, at = 5;
    unsigned k;
    ansi_of(rd32(attrs + 4), name, sizeof name);
    if (xb_content_path(name, out, room)) return;
    for (k = 0; s_kit_only[k]; k++)
        if (!_strnicmp(name, s_kit_only[k], strlen(s_kit_only[k]))) { out[0] = 0; return; }
    if (!_strnicmp(name, "d:", 2)) at = 2;   /* the disc the title started from, the same files: Too Human, first stop 8312ecec on its loader thread 82767520, d:\datatables\ */
    else if (_strnicmp(name, "game:", 5)) {
        port_log("file path on another drive: \"%s\"\n", name);
        xb_stop("file_drive", name, attrs);
    }
    if (!dir) dir = host_arg("--game-dir");
    if (!dir) xb_stop("no_game_dir", "neither XB_GAME_DIR nor --game-dir is set", 0);
    if (strlen(dir) + strlen(name) >= room) xb_stop("bad_path", "a path longer than the host's", attrs);
    strcpy(out, dir);
    strcat(out, name + at);
    n = strlen(out);
    while (n && out[n - 1] == '\\') out[--n] = 0;
}

static void io_done(u32 io, u32 status, u32 information)
{
    if (io) { wr32(io, status); wr32(io + 4, information); }
}

/* With XB_SAMPLE set, every name the game asks for and whether it was there: which file a stalled screen waits for, or looks for and misses */
static void open_log(const char *call, u32 attrs, int found)
{
    static int on = -1;
    char name[MAX_PATH];
    if (on < 0) on = getenv("XB_SAMPLE") != NULL;
    if (!on) return;
    ansi_of(rd32(attrs + 4), name, sizeof name);
    port_log("file: %s %s, %s, frame %u\n", call, name, found ? "there" : "missing", port_vblank_count());
}

static void wr_filetime(u32 p, FILETIME t)
{
    wr32(p, t.dwHighDateTime);
    wr32(p + 4, t.dwLowDateTime);
}

/* Finished reads whose routine has not run yet, on the thread that asked: the console runs them there, at its next alertable wait */
#define APC_MAX 64
static __declspec(thread) struct { u32 routine, context, io; } t_apc[APC_MAX];
static __declspec(thread) unsigned t_apcs;

int xb_apc_run(const TcArgs *a)
{
    unsigned i = 0;
    if (!t_apcs) return 0;
    while (i < t_apcs) {        /* a routine may start the next read */
        TcArgs n;
        memset(&n, 0, sizeof n);
        n.r[0] = t_apc[i].context;
        n.r[1] = t_apc[i].io;
        n.sp[0] = a->sp[0] - 0x80;
        (void)tc_call(t_apc[i++].routine, &n);
    }
    t_apcs = 0;
    return 1;
}

int xb_file_import(unsigned ord, const TcArgs *a, u64 *r3)
{
    switch (ord) {
    case 223: {   /* NtOpenFile(HANDLE *out, access, attributes, io status *, share, options): first stop 821b9f40 (FindFirstFile) under 82096fe0, which walks
                   * game:\media\bin. Options 0x4021 are a directory opened for listing */
        char path[MAX_PATH];
        DWORD at;
        host_path((u32)a->r[2], path, sizeof path);
        at = GetFileAttributesA(path);
        open_log("open", (u32)a->r[2], at != INVALID_FILE_ATTRIBUTES);
        if (at == INVALID_FILE_ATTRIBUTES) {
            io_done((u32)a->r[3], 0xC0000034u, 0);
            *r3 = XB_STATUS(0xC0000034u);                   /* STATUS_OBJECT_NAME_NOT_FOUND */
            return 1;
        }
        wr32((u32)a->r[0], file_new(path));
        io_done((u32)a->r[3], 0, 1);
        *r3 = 0;
        return 1;
    }
    case 210: {   /* NtCreateFile(HANDLE *out, access, attributes, io status *, allocation, file attributes, share, disposition, options): first stop 821b8588
                   * (CreateFile) under 82096fe0, reading each file game:\\media\\bin holds with access 0x80100080. The package is read-only, so opening what is there
                   * is all the drive does; anything else stops */
        char path[MAX_PATH], name[MAX_PATH], ignored[MAX_PATH];
        static const DWORD how[6] = {CREATE_ALWAYS, OPEN_EXISTING, CREATE_NEW, OPEN_ALWAYS, TRUNCATE_EXISTING, CREATE_ALWAYS};
        u32 disposition = (u32)a->r[7], access = (u32)a->r[1];
        int writes = disposition != 1u || (access & 0x40000002u);
        HANDLE h;
        u32 g;
        int existed;
        ansi_of(rd32((u32)a->r[2] + 4), name, sizeof name);
        if (!_strnicmp(name, "\\Device\\Harddisk0\\", 18)) {   /* the port is a console with no hard disk: Too Human, first stop 82a70748 (XMountUtilityDrive) opening
                                                                * \Device\Harddisk0\partition0 to read and write; its caller 82810710 goes on without the utility drive */
            io_done((u32)a->r[3], 0xC0000034u, 0);
            *r3 = XB_STATUS(0xC0000034u);
            return 1;
        }
        if (disposition > 5u || (writes && !xb_content_path(name, ignored, sizeof ignored))) xb_stop("file_write", "a file written outside saved content", disposition);
        host_path((u32)a->r[2], path, sizeof path);
        h = CreateFileA(path, writes ? GENERIC_READ | GENERIC_WRITE : GENERIC_READ, FILE_SHARE_READ, NULL, how[disposition], FILE_ATTRIBUTE_NORMAL, NULL);
        existed = GetLastError() == ERROR_ALREADY_EXISTS;   /* CREATE_ALWAYS and OPEN_ALWAYS say so when the file was there */
        open_log("create", (u32)a->r[2], h != INVALID_HANDLE_VALUE);
        if (h == INVALID_HANDLE_VALUE) {
            io_done((u32)a->r[3], 0xC0000034u, 0);
            *r3 = XB_STATUS(0xC0000034u);
            return 1;
        }
        {   /* a movie or a stereo-folder sound (the data folder's root .xma, its music) opened, with the time since the first file: which one plays when */
            static ULONGLONG t0;
            size_t len = strlen(name);
            const char *base = strrchr(name, '\\');
            if (!t0) t0 = GetTickCount64();
            if (len > 4 && (!_stricmp(name + len - 4, ".wmv") || (!_stricmp(name + len - 4, ".xma") && base && !_strnicmp(name, "game:\\", 6) && base == name + 5)))
                port_log("file: %s opened at %.1f s, frame %u\n", name, (GetTickCount64() - t0) / 1000.0, port_vblank_count());
        }
        xb_flow_file(name);
        g = file_new(path);
        file_of(g)->h = h;
        wr32((u32)a->r[0], g);
        {   /* the status's Information, as NT gives it (XAPI's CreateFile sets ERROR_ALREADY_EXISTS from it): superseded 0, opened 1, created 2, overwritten 3 */
            static const u32 had[6] = {0, 1, 2, 1, 3, 3}, fresh[6] = {2, 1, 2, 2, 3, 2};
            io_done((u32)a->r[3], 0, existed || disposition == 1u || disposition == 4u ? had[disposition] : fresh[disposition]);
        }
        *r3 = 0;
        return 1;
    }
    case 255: {   /* NtWriteFile(file, event, routine, context, io status *, buffer, length, u64 *offset): the pair of 240: every write finishes before the call returns */
        XbFile *f = file_of((u32)a->r[0]);
        u32 buf = (u32)a->r[5], len = (u32)a->r[6], off = (u32)a->r[7];
        DWORD put = 0;
        if ((u32)a->r[1] || (u32)a->r[2] || !f->h) xb_stop("file_write", "a write with an event or a routine, or of a directory", (u32)a->r[1]);
        AcquireSRWLockExclusive(&f->at);
        if (off) {
            LARGE_INTEGER at;
            at.QuadPart = (LONGLONG)rd64(off);
            SetFilePointerEx(f->h, at, NULL, FILE_BEGIN);
        }
        if (len && !WriteFile(f->h, tc_base + buf, len, &put, NULL)) xb_stop("file_write", "the host's write failed", GetLastError());
        ReleaseSRWLockExclusive(&f->at);
        io_done((u32)a->r[4], 0, put);
        *r3 = 0;
        return 1;
    }
    case 247: {   /* NtSetInformationFile(file, io status *, record *, room, class): first stop 821b87b0 (SetFilePointer) under the UI library's loader, room 8: class 14,
                   * the position. Another class stops */
        XbFile *f = file_of((u32)a->r[0]);
        LARGE_INTEGER at;
        if ((u32)a->r[4] != 14u || !f->h) xb_stop("file_info", "a file information class not written", (u32)a->r[4]);
        at.QuadPart = (LONGLONG)rd64((u32)a->r[2]);
        AcquireSRWLockExclusive(&f->at);
        SetFilePointerEx(f->h, at, NULL, FILE_BEGIN);
        ReleaseSRWLockExclusive(&f->at);
        io_done((u32)a->r[1], 0, 0);
        *r3 = 0;
        return 1;
    }
    case 232: {   /* NtQueryInformationFile(file, io status *, record *, room, class): first stop 821b8c20 (GetFileInformationByHandle) under 82096fe0, room 0x38: class
                   * 34, four times, allocated, size, attributes. Class 14 is the position, for 821b87b0 going from where it is. Another class stops */
        XbFile *f = file_of((u32)a->r[0]);
        u32 rec = (u32)a->r[2];
        BY_HANDLE_FILE_INFORMATION d;
        if ((u32)a->r[4] == 14u && f->h) {
            LARGE_INTEGER zero, now;
            zero.QuadPart = 0;
            AcquireSRWLockExclusive(&f->at);
            SetFilePointerEx(f->h, zero, &now, FILE_CURRENT);
            ReleaseSRWLockExclusive(&f->at);
            wr64(rec, (u64)now.QuadPart);
            io_done((u32)a->r[1], 0, 8);
            *r3 = 0;
            return 1;
        }
        if ((u32)a->r[4] == 6u && (u32)a->r[3] >= 8u) {
            /* Class 6, FILE_INTERNAL_INFORMATION { u64 index }: Too Human, 8312e80c under 82a6f498 <- 82774cf0, the save files' check after 239 (build box A
             * mn19a). The host's file index, which names one file on its volume as the console's does; a directory is opened for it */
            HANDLE h = f->h;
            if (!h) h = CreateFileA(f->path, 0, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, NULL, OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS, NULL);
            if (h == INVALID_HANDLE_VALUE || !GetFileInformationByHandle(h, &d)) xb_stop("file_info", "the host file index could not be read", (u32)GetLastError());
            if (h != f->h) CloseHandle(h);
            wr32(rec, d.nFileIndexHigh);
            wr32(rec + 4, d.nFileIndexLow);
            io_done((u32)a->r[1], 0, 8);
            *r3 = 0;
            return 1;
        }
        if ((u32)a->r[4] != 34u || !f->h || !GetFileInformationByHandle(f->h, &d)) xb_stop("file_info", "a file information class not written", (u32)a->r[4]);
        wr_filetime(rec, d.ftCreationTime);
        wr_filetime(rec + 8, d.ftLastAccessTime);
        wr_filetime(rec + 16, d.ftLastWriteTime);
        wr_filetime(rec + 24, d.ftLastWriteTime);
        wr32(rec + 32, d.nFileSizeHigh);
        wr32(rec + 36, d.nFileSizeLow);
        wr32(rec + 40, d.nFileSizeHigh);
        wr32(rec + 44, d.nFileSizeLow);
        wr32(rec + 48, FILE_ATTRIBUTE_NORMAL);
        wr32(rec + 52, 0);
        io_done((u32)a->r[1], 0, 0x38u);
        *r3 = 0;
        return 1;
    }
    case 239: {   /* NtQueryVolumeInformationFile(file, io status *, record *, room, class): first stop in Too Human 8312e81c under 82a6f498 <- 82774cf0, room 0x18,
                   * just after it created saveProfile:\savegame.txt (build box A mn16a, A at vertical blank 2150): the free space before a save. Class 3,
                   * FILE_FS_SIZE_INFORMATION { u64 total units, u64 free units, u32 sectors a unit, u32 bytes a sector }, is the host volume's that holds
                   * the file, as the console gives its device's. Class 1 (build box A mn17a, the same call on the next file, room 0x18),
                   * FILE_FS_VOLUME_INFORMATION { u64 created, u32 serial, u32 label length, u32 supports objects, char label[] }: the host volume's
                   * serial, the package folder's creation time, no label (the kit names no volumes). Another class stops */
        XbFile *f = file_of((u32)a->r[0]);
        u32 rec = (u32)a->r[2], cls = (u32)a->r[4];
        char root[MAX_PATH];
        DWORD per_unit, per_sector, free_units, units;
        ULARGE_INTEGER avail, total, free_all;
        static int told;
        if (cls == 1u && (u32)a->r[3] >= 20u) {
            DWORD serial = 0;
            char dir[MAX_PATH], *cut;
            WIN32_FILE_ATTRIBUTE_DATA d;
            strcpy(dir, f->path);
            if (!(GetFileAttributesA(dir) & FILE_ATTRIBUTE_DIRECTORY) && (cut = strrchr(dir, '\\')) != NULL) *cut = 0;   /* a file: its folder */
            if (!GetVolumePathNameA(f->path, root, sizeof root) || !GetVolumeInformationA(root, NULL, 0, &serial, NULL, NULL, NULL, 0)
                || !GetFileAttributesExA(dir, GetFileExInfoStandard, &d))
                xb_stop("volume_info", "the host volume could not be read", (u32)GetLastError());
            wr_filetime(rec, d.ftCreationTime);
            wr32(rec + 8, serial);
            wr32(rec + 12, 0);
            wr32(rec + 16, 0);
            if (told++ < 8) port_log("import krnl#239 NtQueryVolumeInformationFile %s: class 1, serial %08lx, no label\n", f->path, serial);
            io_done((u32)a->r[1], 0, 20u);
            *r3 = 0;
            return 1;
        }
        if (cls != 3u || (u32)a->r[3] < 0x18u) xb_stop("volume_info", "a volume information class not written", cls);
        if (!GetVolumePathNameA(f->path, root, sizeof root) || !GetDiskFreeSpaceA(root, &per_unit, &per_sector, &free_units, &units)
            || !GetDiskFreeSpaceExA(root, &avail, &total, &free_all))
            xb_stop("volume_info", "the host volume could not be read", (u32)GetLastError());
        wr64(rec, total.QuadPart / ((u64)per_unit * per_sector));       /* the Ex counts: the plain call stops at 2^32 units */
        wr64(rec + 8, avail.QuadPart / ((u64)per_unit * per_sector));   /* what this user may use, as the console answers */
        wr32(rec + 16, per_unit);
        wr32(rec + 20, per_sector);
        if (told++ < 8) port_log("import krnl#239 NtQueryVolumeInformationFile %s: class 3, %llu MB free of %llu MB (host volume %s)\n", f->path,
                                 avail.QuadPart >> 20, total.QuadPart >> 20, root);
        io_done((u32)a->r[1], 0, 0x18u);
        *r3 = 0;
        return 1;
    }
    case 231: {   /* NtQueryFullAttributesFile(attributes, record *): first stop in Too Human, 82a6efd8 under 82768f38. The record of class 34 above, by path */
        char path[MAX_PATH];
        WIN32_FILE_ATTRIBUTE_DATA d;
        BOOL there;
        u32 rec = (u32)a->r[1];
        host_path((u32)a->r[0], path, sizeof path);
        there = GetFileAttributesExA(path, GetFileExInfoStandard, &d);
        open_log("attributes", (u32)a->r[0], there);
        if (!there) {
            *r3 = XB_STATUS(0xC0000034u);                   /* STATUS_OBJECT_NAME_NOT_FOUND */
            return 1;
        }
        wr_filetime(rec, d.ftCreationTime);
        wr_filetime(rec + 8, d.ftLastAccessTime);
        wr_filetime(rec + 16, d.ftLastWriteTime);
        wr_filetime(rec + 24, d.ftLastWriteTime);
        wr32(rec + 32, d.nFileSizeHigh);
        wr32(rec + 36, d.nFileSizeLow);
        wr32(rec + 40, d.nFileSizeHigh);
        wr32(rec + 44, d.nFileSizeLow);
        wr32(rec + 48, d.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY ? FILE_ATTRIBUTE_DIRECTORY : FILE_ATTRIBUTE_NORMAL);   /* as the console's file systems give
                                                                                                                             * them, not the host's other bits */
        wr32(rec + 52, 0);
        *r3 = 0;
        return 1;
    }
    case 240: {   /* NtReadFile(file, event, routine, context, io status *, buffer, length, u64 *offset): first stop 821b8408 (ReadFile) under 82096fe0, no event. Every read
                   * finishes before the call returns. At the end of the file, the kernel's error. An event is written when a stop passes one. A routine (first: Too Human's
                   * 82a6edc8, ReadFileEx, 82a71529: the low bit is a flag) runs at this thread's next alertable wait, as on the console. An event (first: Too Human's
                   * render thread at 82a6f128 under 82c3c7c8, 128 KB reads of a stream) is set when the read is done and its status written, so a wait on it
                   * returns at once */
        XbFile *f = file_of((u32)a->r[0]);
        u32 buf = (u32)a->r[5], len = (u32)a->r[6], off = (u32)a->r[7];
        DWORD got = 0;
        BOOL ok = TRUE;
        if (!f->h) xb_stop("file_read", "a read of a directory", (u32)a->r[1]);
        if (!TryAcquireSRWLockExclusive(&f->at)) {
            static volatile LONG logged;
            if (!InterlockedExchange(&logged, 1)) port_log("file %s: two threads read it at once, one waits for the other's seek and read\n", f->path);
            AcquireSRWLockExclusive(&f->at);
        }
        if (off) {
            LARGE_INTEGER at;
            at.QuadPart = (LONGLONG)rd64(off);
            SetFilePointerEx(f->h, at, NULL, FILE_BEGIN);
        }
        if (len) ok = ReadFile(f->h, tc_base + buf, len, &got, NULL);
        if (ok && got) {
            LARGE_INTEGER zero, now;
            unsigned k = (unsigned)(InterlockedIncrement(&s_read_n) - 1) & (READ_RING - 1);
            zero.QuadPart = 0;
            SetFilePointerEx(f->h, zero, &now, FILE_CURRENT);
            s_reads[k].buf = buf, s_reads[k].len = got, s_reads[k].file = (u32)(f - s_files), s_reads[k].at = (u64)now.QuadPart - got;
        }
        ReleaseSRWLockExclusive(&f->at);
        if (!ok) xb_stop("file_read", "the host's read failed", GetLastError());
        if (len && !got) {
            io_done((u32)a->r[4], 0xC0000011u, 0);
            if ((u32)a->r[1]) xb_token_set_event((u32)a->r[1]);
            *r3 = XB_STATUS(0xC0000011u);                   /* STATUS_END_OF_FILE */
            return 1;
        }
        io_done((u32)a->r[4], 0, got);
        if ((u32)a->r[1]) xb_token_set_event((u32)a->r[1]);
        if ((u32)a->r[2]) {
            if (t_apcs == APC_MAX) xb_stop("file_read", "too many reads waiting for an alertable wait", t_apcs);
            t_apc[t_apcs].routine = (u32)a->r[2] & ~1u;
            t_apc[t_apcs].context = (u32)a->r[3];
            t_apc[t_apcs].io = (u32)a->r[4];
            t_apcs++;
        }
        *r3 = 0;
        return 1;
    }
    case 228: {   /* NtQueryDirectoryFile(file, event, routine, context, io status *, record *, room, u16/u32 pattern string *, restart): first stop 821b9f40, from
                   * 824b0894 in the table at 824b087c. One entry for each call, as 821ba340 reads one: next offset, index, four times, size, allocated, attributes,
                   * name length, name. The console lists a package in its table's order, which is sorted by name; the host's file system lists the same way */
        XbFile *f = file_of((u32)a->r[0]);
        u32 rec = (u32)a->r[5], room = (u32)a->r[6], io = (u32)a->r[4], i, n;
        WIN32_FIND_DATAA d;
        int ok;
        do {
            if (!f->started) {
                char pat[MAX_PATH], full[MAX_PATH * 2];
                pat[0] = 0;
                if ((u32)a->r[7]) ansi_of((u32)a->r[7], pat, sizeof pat);
                strcpy(full, f->path);
                strcat(full, "\\");
                strcat(full, pat[0] ? pat : "*");
                f->find = FindFirstFileA(full, &d);
                f->started = 1;
                ok = f->find != INVALID_HANDLE_VALUE;
            } else {
                ok = f->find != INVALID_HANDLE_VALUE && FindNextFileA(f->find, &d);
            }
        } while (ok && (!strcmp(d.cFileName, ".") || !strcmp(d.cFileName, "..")));
        if (!ok) {   /* STATUS_NO_MORE_FILES after an entry; STATUS_NO_SUCH_FILE when the first query finds none, as the console's file system answers */
            u32 st = f->listed ? 0x80000006u : 0xC000000Fu;
            io_done(io, st, 0);
            *r3 = XB_STATUS(st);
            return 1;
        }
        f->listed = 1;
        n = (u32)strlen(d.cFileName);
        if (0x40u + n > room) xb_stop("file_record", "a directory record longer than the caller's room", n);
        wr32(rec, 0);
        wr32(rec + 4, 0);
        wr_filetime(rec + 8, d.ftCreationTime);
        wr_filetime(rec + 16, d.ftLastAccessTime);
        wr_filetime(rec + 24, d.ftLastWriteTime);
        wr_filetime(rec + 32, d.ftLastWriteTime);
        wr32(rec + 40, d.nFileSizeHigh);
        wr32(rec + 44, d.nFileSizeLow);
        wr32(rec + 48, d.nFileSizeHigh);
        wr32(rec + 52, d.nFileSizeLow);
        wr32(rec + 56, d.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY ? FILE_ATTRIBUTE_DIRECTORY : FILE_ATTRIBUTE_NORMAL);
        wr32(rec + 60, n);
        for (i = 0; i < n; i++) wr8(rec + 64 + i, (u8)d.cFileName[i]);
        io_done(io, 0, 0x40u + n);
        *r3 = 0;
        return 1;
    }
    case 207: {   /* NtClose(handle): first stop 821b9f40. A kernel handle is not a file: xb_imports.c answers that */
        u32 g = (u32)a->r[0];
        XbFile *f;
        if (g < FILE_BASE) return 0;
        f = file_of(g);
        if (f->find != INVALID_HANDLE_VALUE) FindClose(f->find);
        if (f->h) CloseHandle(f->h), xb_flow_close(f->path);
        f->used = 0;
        *r3 = 0;
        return 1;
    }
    }
    return 0;
}
