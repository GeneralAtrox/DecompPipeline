/* The console's saved content on the host's file system. A content package is a folder under XB_SAVE_DIR (else "save" beside the
 * program), named by its XCONTENT_DATA's file name and holding that record in a file of its own so a list can give it back. While a
 * package is open its root name ("SAVEDRIVE") is a drive for xb_file.c's paths. */
#include <windows.h>
#include <stdlib.h>
#include <string.h>
#include "xb.h"

#define CONTENT_DATA_SIZE 0x134u   /* XCONTENT_DATA: the game steps a list by it (8209bd10) */
#define CONTENT_NAME 0x108u        /* its file name, 42 characters */
#define RECORD_NAME "_xcontent"
#define ERR_PENDING 0x3E5u
#define ERR_PATH_NOT_FOUND 3u
#define ERR_ALREADY_EXISTS 183u
#define ERR_NO_MORE_FILES 0x12u

typedef struct { char root[64]; char dir[MAX_PATH]; } Mount;
static Mount s_mounts[8];
static SRWLOCK s_lock = SRWLOCK_INIT;

static void save_dir(char *out, unsigned room)
{
    const char *d = getenv("XB_SAVE_DIR");
    if (!d) d = host_arg("--save-dir");
    if (d) {
        strcpy_s(out, room, d);
    } else {
        char *slash;
        GetModuleFileNameA(NULL, out, room);
        slash = strrchr(out, '\\');
        strcpy_s(slash ? slash + 1 : out, room - (unsigned)(slash ? slash + 1 - out : 0), "save");
    }
    CreateDirectoryA(out, NULL);
}

static void guest_text(u32 at, char *out, unsigned room)
{
    unsigned i;
    for (i = 0; i + 1 < room && rd8(at + i); i++) out[i] = (char)rd8(at + i);
    out[i] = 0;
}

static void package_dir(u32 data, char *out, unsigned room)
{
    char name[43];
    size_t n;
    guest_text(data + CONTENT_NAME, name, sizeof name);
    if (!name[0] || strpbrk(name, "\\/:*?\"<>|") || strstr(name, "..")) xb_stop("bad_path", "a content package name the host cannot hold", data);
    save_dir(out, room);
    n = strlen(out);
    if (n + 1 + strlen(name) >= room) xb_stop("bad_path", "a path longer than the host's", data);
    out[n] = '\\';
    strcpy(out + n + 1, name);
}

/* The overlapped block of a call that finishes at once: +0 the result the game's check watches (0x3e5 until done), +4 what it returned, +0xc an event to set
 * (82090c00 passes one), +0x18 the extended error (821b8780). A routine is written when a stop passes one */
u32 xb_overlapped_finish(u32 overlapped, u32 error, u32 information)
{
    if (!overlapped) return error;
    if (rd32(overlapped + 0x10)) xb_stop("content", "an overlapped call with a routine", overlapped);
    wr32(overlapped + 4, information);
    wr32(overlapped + 0x18, error);
    wr32(overlapped, error);
    if (rd32(overlapped + 0xC)) xb_token_set_event(rd32(overlapped + 0xC));
    return ERR_PENDING;
}

/* name is a game path: on an open package's drive, its host path */
int xb_content_path(const char *name, char *out, unsigned room)
{
    const char *colon = strchr(name, ':');
    size_t n = colon ? (size_t)(colon - name) : 0;
    unsigned i;
    int found = 0;
    if (!n) return 0;
    AcquireSRWLockShared(&s_lock);
    for (i = 0; i < sizeof s_mounts / sizeof *s_mounts && !found; i++) {
        if (s_mounts[i].root[0] && strlen(s_mounts[i].root) == n && !_strnicmp(s_mounts[i].root, name, n)) {
            if (strlen(s_mounts[i].dir) + strlen(colon + 1) + 1 >= room) xb_stop("bad_path", "a path longer than the host's", 0);
            strcpy(out, s_mounts[i].dir);
            strcat(out, colon + 1);
            found = 1;
        }
    }
    ReleaseSRWLockShared(&s_lock);
    return found;
}

static void mount(const char *root, const char *dir)
{
    unsigned i, free_at = 8;
    AcquireSRWLockExclusive(&s_lock);
    for (i = 0; i < 8; i++) {
        if (!_stricmp(s_mounts[i].root, root)) free_at = i;
        else if (!s_mounts[i].root[0] && free_at == 8) free_at = i;
    }
    if (free_at == 8) xb_stop("content", "more content packages open than the port holds", 8);
    strcpy_s(s_mounts[free_at].root, sizeof s_mounts[free_at].root, root);
    strcpy_s(s_mounts[free_at].dir, sizeof s_mounts[free_at].dir, dir);
    ReleaseSRWLockExclusive(&s_lock);
}

static void unmount(const char *root)
{
    unsigned i;
    AcquireSRWLockExclusive(&s_lock);
    for (i = 0; i < 8; i++)
        if (!_stricmp(s_mounts[i].root, root)) s_mounts[i].root[0] = 0;
    ReleaseSRWLockExclusive(&s_lock);
}

static void write_record(const char *dir, u32 data)
{
    char path[MAX_PATH];
    HANDLE h;
    DWORD put;
    strcpy_s(path, sizeof path, dir);
    strcat_s(path, sizeof path, "\\" RECORD_NAME);
    h = CreateFileA(path, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (h == INVALID_HANDLE_VALUE) xb_stop("content", "the host could not write a package's record", GetLastError());
    WriteFile(h, tc_base + data, CONTENT_DATA_SIZE, &put, NULL);
    CloseHandle(h);
}

/* The list the last XamContentCreateEnumerator asked for. The game reads one at a time, and 8209bd10 leaves an empty one unclosed; reading an older one stops */
static u32 s_list_token, s_list_type, s_list_room_items;
static HANDLE s_list_find = INVALID_HANDLE_VALUE;

static int next_package(u32 out)
{
    char pattern[MAX_PATH], path[MAX_PATH];
    WIN32_FIND_DATAA fd;
    for (;;) {
        HANDLE h;
        DWORD got = 0;
        if (s_list_find == INVALID_HANDLE_VALUE) {
            save_dir(pattern, sizeof pattern);
            strcat_s(pattern, sizeof pattern, "\\*");
            s_list_find = FindFirstFileA(pattern, &fd);
            if (s_list_find == INVALID_HANDLE_VALUE) return 0;
        } else if (!FindNextFileA(s_list_find, &fd)) {
            return 0;
        }
        if (!(fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) || fd.cFileName[0] == '.') continue;
        save_dir(path, sizeof path);
        strcat_s(path, sizeof path, "\\");
        strcat_s(path, sizeof path, fd.cFileName);
        strcat_s(path, sizeof path, "\\" RECORD_NAME);
        h = CreateFileA(path, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
        if (h == INVALID_HANDLE_VALUE) continue;
        ReadFile(h, tc_base + out, CONTENT_DATA_SIZE, &got, NULL);
        CloseHandle(h);
        if (got == CONTENT_DATA_SIZE && rd32(out + 4) == s_list_type) return 1;
    }
}

int xb_content_import(unsigned ord, const TcArgs *a, u64 *r3)
{
    switch (ord) {
    case 601: {   /* XamContentCreateEx(user, root name, XCONTENT_DATA *, flags, u32 *disposition, u32 *license, cache size, u64 size, overlapped on the stack): first stop
                   * 821b8280 under 82090c00, flags 0x11. The low bits: 1 create new, 2 create always, 3 open existing, 4 open always, 5 truncate. Disposition 2 made, 1 opened */
        char root[64], dir[MAX_PATH];
        u32 how = (u32)a->r[3] & 0xF, overlapped = (u32)a->s[0], error = 0, disposition = 1;
        DWORD at;
        int exists;
        guest_text((u32)a->r[1], root, sizeof root);
        package_dir((u32)a->r[2], dir, sizeof dir);
        at = GetFileAttributesA(dir);
        exists = at != INVALID_FILE_ATTRIBUTES && (at & FILE_ATTRIBUTE_DIRECTORY);
        if (how < 1 || how > 5) xb_stop("content", "a content disposition not written", how);
        if (how == 1 && exists) error = ERR_ALREADY_EXISTS;
        else if ((how == 3 || how == 5) && !exists) error = ERR_PATH_NOT_FOUND;
        if (!error) {
            if (!exists) {
                if (!CreateDirectoryA(dir, NULL)) xb_stop("content", "the host could not make a package's folder", GetLastError());
                disposition = 2;
            }
            write_record(dir, (u32)a->r[2]);
            mount(root, dir);
            if ((u32)a->r[4]) wr32((u32)a->r[4], disposition);
            if ((u32)a->r[5]) wr32((u32)a->r[5], 0);
        }
        port_log("content \"%s\" as %s, flags %x: error %u, disposition %u\n", dir, root, (u32)a->r[3], error, disposition);
        *r3 = xb_overlapped_finish(overlapped, error, disposition);
        return 1;
    }
    case 602: {   /* XamContentClose(root name, overlapped) */
        char root[64];
        guest_text((u32)a->r[0], root, sizeof root);
        unmount(root);
        *r3 = xb_overlapped_finish((u32)a->r[1], 0, 0);
        return 1;
    }
    case 603: {   /* XamContentDelete(user, XCONTENT_DATA *, overlapped): the package's files, then its folder */
        char dir[MAX_PATH], path[MAX_PATH];
        WIN32_FIND_DATAA fd;
        HANDLE f;
        package_dir((u32)a->r[1], dir, sizeof dir);
        strcpy_s(path, sizeof path, dir);
        strcat_s(path, sizeof path, "\\*");
        f = FindFirstFileA(path, &fd);
        if (f != INVALID_HANDLE_VALUE) {
            do {
                if (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) continue;
                strcpy_s(path, sizeof path, dir);
                strcat_s(path, sizeof path, "\\");
                strcat_s(path, sizeof path, fd.cFileName);
                DeleteFileA(path);
            } while (FindNextFileA(f, &fd));
            FindClose(f);
        }
        *r3 = xb_overlapped_finish((u32)a->r[2], RemoveDirectoryA(dir) ? 0 : ERR_PATH_NOT_FOUND, 0);
        return 1;
    }
    case 610:     /* XamContentGetCreator(user, XCONTENT_DATA *, u32 *is creator, u64 *xuid, overlapped): first stop 821b8328 under 82091b08, is this save the player's.
                   * The one local profile (the xuid 522 and 551 give) made every package there is */
        if ((u32)a->r[2]) wr32((u32)a->r[2], 1);
        if ((u32)a->r[3]) wr64((u32)a->r[3], 0xE000000000000001ull);
        *r3 = xb_overlapped_finish((u32)a->r[4], 0, 0);
        return 1;
    case 608:     /* XamContentSetThumbnail(user, XCONTENT_DATA *, image, bytes, overlapped): first stop 823290e4, the first press on the title with no save yet.
                   * The dashboard shows that picture and nothing here does */
        *r3 = xb_overlapped_finish((u32)a->r[4], 0, 0);
        return 1;
    case 604:     /* XamContentCreateEnumerator(user, device, type, flags, items, u32 *size, HANDLE *): first stop 821b8338 under 8209bd10, the title looking for saved content,
                   * 5 items of type 2; any non-zero answer is its error screen (820a2010 0x25). A token */
        if (s_list_find != INVALID_HANDLE_VALUE) FindClose(s_list_find);
        s_list_token = xb_token_new();
        s_list_type = (u32)a->r[2];
        s_list_room_items = (u32)a->r[4];
        s_list_find = INVALID_HANDLE_VALUE;
        wr32((u32)a->r[5], (u32)a->r[4] * CONTENT_DATA_SIZE);
        wr32((u32)a->r[6], s_list_token);
        *r3 = 0;
        return 1;
    case 592: {   /* XamEnumerate(handle, flags, buffer, size, u32 *count, overlapped): what the list has left. Nothing left is 0x12, the game's normal end of a list */
        u32 n = 0, room = (u32)a->r[3] / CONTENT_DATA_SIZE;
        if ((u32)a->r[0] != s_list_token) xb_stop("content", "a list that is not the content list", (u32)a->r[0]);
        if ((u32)a->r[5]) xb_stop("content", "an overlapped list", (u32)a->r[5]);
        if (room > s_list_room_items) room = s_list_room_items;
        while (n < room && next_package((u32)a->r[2] + n * CONTENT_DATA_SIZE)) n++;
        if ((u32)a->r[4]) wr32((u32)a->r[4], n);
        *r3 = n ? 0 : ERR_NO_MORE_FILES;
        return 1;
    }
    }
    return 0;
}

/* NtClose of the list's token: its host search ends with it */
void xb_content_token_closed(u32 token)
{
    if (token != s_list_token) return;
    if (s_list_find != INVALID_HANDLE_VALUE) FindClose(s_list_find);
    s_list_find = INVALID_HANDLE_VALUE;
    s_list_token = 0;
}
