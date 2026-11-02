/* The game's files: the kernel's file calls on the host's file system. XB_GAME_DIR is the package's contents on disk (tools/stfs.py extracts
 * them), and it stands for the "game:" drive. A path on any other drive stops, so each is answered on purpose. Like every import, a call is
 * written when a run stops on it, and its case names that stop. */
#include <windows.h>
#include <stdlib.h>
#include <string.h>
#include "xb.h"

/* A file the game holds: a token in a range of its own, so that NtClose can tell it from a kernel handle. */
#define FILE_BASE 0x7FFF0000u
#define FILE_MAX 1024
typedef struct { int used; char path[MAX_PATH]; HANDLE find; int started; HANDLE h; } XbFile;
static XbFile s_files[FILE_MAX];
static SRWLOCK s_files_lock = SRWLOCK_INIT;

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

/* struct { u32 root, u32 name, u32 attributes } * to a host path */
static void host_path(u32 attrs, char *out, unsigned room)
{
    char name[MAX_PATH];
    const char *dir = getenv("XB_GAME_DIR");
    size_t n;
    ansi_of(rd32(attrs + 4), name, sizeof name);
    if (xb_content_path(name, out, room)) return;
    if (_strnicmp(name, "game:", 5)) {
        port_log("file path on another drive: \"%s\"\n", name);
        xb_stop("file_drive", name, attrs);
    }
    if (!dir) dir = host_arg("--game-dir");
    if (!dir) xb_stop("no_game_dir", "neither XB_GAME_DIR nor --game-dir is set", 0);
    if (strlen(dir) + strlen(name) >= room) xb_stop("bad_path", "a path longer than the host's", attrs);
    strcpy(out, dir);
    strcat(out, name + 5);
    n = strlen(out);
    while (n && out[n - 1] == '\\') out[--n] = 0;
}

static void io_done(u32 io, u32 status, u32 information)
{
    if (io) { wr32(io, status); wr32(io + 4, information); }
}

static void wr_filetime(u32 p, FILETIME t)
{
    wr32(p, t.dwHighDateTime);
    wr32(p + 4, t.dwLowDateTime);
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
        ansi_of(rd32((u32)a->r[2] + 4), name, sizeof name);
        if (disposition > 5u || (writes && !xb_content_path(name, ignored, sizeof ignored))) xb_stop("file_write", "a file written outside saved content", disposition);
        host_path((u32)a->r[2], path, sizeof path);
        h = CreateFileA(path, writes ? GENERIC_READ | GENERIC_WRITE : GENERIC_READ, FILE_SHARE_READ, NULL, how[disposition], FILE_ATTRIBUTE_NORMAL, NULL);
        if (h == INVALID_HANDLE_VALUE) {
            io_done((u32)a->r[3], 0xC0000034u, 0);
            *r3 = XB_STATUS(0xC0000034u);
            return 1;
        }
        g = file_new(path);
        file_of(g)->h = h;
        wr32((u32)a->r[0], g);
        io_done((u32)a->r[3], 0, 1);
        *r3 = 0;
        return 1;
    }
    case 255: {   /* NtWriteFile(file, event, routine, context, io status *, buffer, length, u64 *offset): the pair of 240: every write finishes before the call returns */
        XbFile *f = file_of((u32)a->r[0]);
        u32 buf = (u32)a->r[5], len = (u32)a->r[6], off = (u32)a->r[7];
        DWORD put = 0;
        if ((u32)a->r[1] || (u32)a->r[2] || !f->h) xb_stop("file_write", "a write with an event or a routine, or of a directory", (u32)a->r[1]);
        if (off) {
            LARGE_INTEGER at;
            at.QuadPart = (LONGLONG)rd64(off);
            SetFilePointerEx(f->h, at, NULL, FILE_BEGIN);
        }
        if (len && !WriteFile(f->h, tc_base + buf, len, &put, NULL)) xb_stop("file_write", "the host's write failed", GetLastError());
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
        SetFilePointerEx(f->h, at, NULL, FILE_BEGIN);
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
            SetFilePointerEx(f->h, zero, &now, FILE_CURRENT);
            wr64(rec, (u64)now.QuadPart);
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
    case 240: {   /* NtReadFile(file, event, routine, context, io status *, buffer, length, u64 *offset): first stop 821b8408 (ReadFile) under 82096fe0, no event. Every read
                   * finishes before the call returns. At the end of the file, the kernel's error. An event or a routine is written when a stop passes one */
        XbFile *f = file_of((u32)a->r[0]);
        u32 buf = (u32)a->r[5], len = (u32)a->r[6], off = (u32)a->r[7];
        DWORD got = 0;
        if ((u32)a->r[1] || (u32)a->r[2] || !f->h) xb_stop("file_read", "a read with an event or a routine, or of a directory", (u32)a->r[1]);
        if (off) {
            LARGE_INTEGER at;
            at.QuadPart = (LONGLONG)rd64(off);
            SetFilePointerEx(f->h, at, NULL, FILE_BEGIN);
        }
        if (len && !ReadFile(f->h, tc_base + buf, len, &got, NULL)) xb_stop("file_read", "the host's read failed", GetLastError());
        if (len && !got) {
            io_done((u32)a->r[4], 0xC0000011u, 0);
            *r3 = XB_STATUS(0xC0000011u);                   /* STATUS_END_OF_FILE */
            return 1;
        }
        io_done((u32)a->r[4], 0, got);
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
        if (!ok) {
            io_done(io, 0x80000006u, 0);
            *r3 = XB_STATUS(0x80000006u);                   /* STATUS_NO_MORE_FILES */
            return 1;
        }
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
        if (f->h) CloseHandle(f->h);
        f->used = 0;
        *r3 = 0;
        return 1;
    }
    }
    return 0;
}
