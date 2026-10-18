/* The launcher contract (Lodoss's, so one launcher drives every port; port/README.md "Launcher contract"):
 *
 *   port.exe --setup-only [--no-dialog] --data-directory WORK --disc IMAGE
 *       checks the image is this game (game.json boot_sha256), copies it (the .cue or .gdi and its track files) into
 *       WORK\assets\disc, checks the copy, then writes WORK\installation.json {"version", "assets"} last.
 *   port.exe --assets DIR --verify-assets
 *       checks the image in DIR. Neither opens a window or the sound device.
 *
 * Exit codes: 0 done, 1 another failure (a copy that failed), 4 the disc is missing or unreadable, 5 it is another
 * game or region. A failure prints one plain line to stderr and the log. host/host_main.cpp calls these. */
#include <stdarg.h>
#include <stdlib.h>
#include <string.h>
#include <windows.h>
#include <bcrypt.h>
#include "port.h"
#include "host.h"

#define MAX_FILES 128
#define PATH_CAP 1024
#define ASSETS "assets\\disc"


typedef struct {
    char name[MAX_FILES][PATH_CAP]; /* as the image names them, relative to its folder */
    int n;
} FileList;

static int fail(int code, const char *fmt, ...)
{
    char line[1024];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf_s(line, sizeof line, _TRUNCATE, fmt, ap);
    va_end(ap);
    fprintf(stderr, "error: %s\n", line); /* Phangrade shows an "error:" line in its failure banner */
    port_log("setup: %s\n", line);
    port_log_flush();
    return code;
}

static int is_file(const char *path)
{
    DWORD a = GetFileAttributesA(path);
    return a != INVALID_FILE_ATTRIBUTES && !(a & FILE_ATTRIBUTE_DIRECTORY);
}

static void folder_of(const char *path, char *out, size_t cap)
{
    strncpy_s(out, cap, path, _TRUNCATE);
    char *slash = strrchr(out, '\\'), *fwd = strrchr(out, '/');
    if (fwd > slash) slash = fwd;
    if (slash) slash[1] = 0;
    else out[0] = 0;
}

static const char *base_of(const char *path)
{
    const char *slash = strrchr(path, '\\'), *fwd = strrchr(path, '/');
    if (fwd > slash) slash = fwd;
    return slash ? slash + 1 : path;
}

static void add_name(FileList *l, const char *p, size_t len)
{
    if (l->n >= MAX_FILES || !len || len >= PATH_CAP) return;
    for (int i = 0; i < l->n; i++)
        if (strlen(l->name[i]) == len && !_strnicmp(l->name[i], p, len)) return;
    memcpy(l->name[l->n], p, len);
    l->name[l->n++][len] = 0;
}

/* The track files a .cue (FILE "name" lines) or a .gdi (the fifth field of each track line) names. */
static int list_files(const char *image, FileList *l)
{
    FILE *f = NULL;
    char line[PATH_CAP + 64];
    size_t n = strlen(image);
    int gdi = n > 4 && !_stricmp(image + n - 4, ".gdi"), first = 1;
    l->n = 0;
    if (fopen_s(&f, image, "r") != 0 || !f) return 0;
    while (fgets(line, sizeof line, f)) {
        char *p = line;
        while (*p == ' ' || *p == '\t') p++;
        if (!gdi) {
            if (strncmp(p, "FILE \"", 6) || !strchr(p + 6, '"')) continue;
            add_name(l, p + 6, (size_t)(strchr(p + 6, '"') - (p + 6)));
            continue;
        }
        if (first) { /* the track count */
            first = 0;
            continue;
        }
        for (int k = 0; k < 4 && *p; k++) {
            while (*p && *p != ' ' && *p != '\t') p++;
            while (*p == ' ' || *p == '\t') p++;
        }
        char *end = *p == '"' ? strchr(++p, '"') : p + strcspn(p, " \t\r\n");
        if (end) add_name(l, p, (size_t)(end - p));
    }
    fclose(f);
    return l->n > 0;
}

static int sha256_hex(const u8 *data, u32 size, char out[65])
{
    u8 digest[32];
    if (!BCRYPT_SUCCESS(BCryptHash(BCRYPT_SHA256_ALG_HANDLE, NULL, 0, (PUCHAR)data, size, digest, sizeof digest)))
        return 0;
    for (int i = 0; i < 32; i++) sprintf_s(out + 2 * i, 3, "%02x", digest[i]);
    return 1;
}

/* 0 when the image is this game, else 4 (missing or unreadable) or 5 (another game or region). */
static int check_image(const char *image)
{
    static const char *const accepted[] = PORT_BOOT_SHA256;
    FileList *l = (FileList *)calloc(1, sizeof *l);
    char dir[PATH_CAP], path[2 * PATH_CAP], boot[32], sha[65];
    u32 size = 0;
    if (!l) return fail(1, "out of memory");
    if (!is_file(image) || !list_files(image, l)) {
        free(l);
        return fail(4, "the disc image %s is missing or unreadable", image);
    }
    folder_of(image, dir, sizeof dir);
    for (int i = 0; i < l->n; i++) {
        _snprintf_s(path, sizeof path, _TRUNCATE, "%s%s", strchr(l->name[i], ':') ? "" : dir, l->name[i]);
        if (!is_file(path)) {
            int code = fail(4, "the disc image's track file %s is missing", path);
            free(l);
            return code;
        }
    }
    free(l);
    PortDisc *d = disc_open(image);
    if (!d) return fail(5, "%s is not a Dreamcast GD-ROM image", image);
    if (PORT_BOOT_FILE[0]) strncpy_s(boot, sizeof boot, PORT_BOOT_FILE, _TRUNCATE);
    else if (!disc_boot_name(d, boot, sizeof boot)) boot[0] = 0;
    u8 *data = boot[0] ? disc_read_file(d, boot, &size) : NULL;
    disc_close(d);
    if (!data) return fail(5, "%s is not %s: it has no boot file %s", image, PORT_GAME_NAME, boot);
    int hashed = sha256_hex(data, size, sha);
    free(data);
    if (!hashed) return fail(1, "cannot hash the boot file");
    port_log("setup: %s boot file %s, SHA-256 %s\n", image, boot, sha);
    if (!accepted[0][0]) return 0;
    for (int i = 0; accepted[i][0]; i++)
        if (!strcmp(accepted[i], sha)) return 0;
    return fail(5, "%s is another game or region than this port's %s (boot file SHA-256 %s)", image, PORT_GAME_NAME,
                sha);
}

/* The first .cue, else the first .gdi, in dir or one folder below it. */
int port_find_image(const char *dir, char *out, size_t cap)
{
    static const char *const kinds[] = {"*.cue", "*.gdi"};
    char pattern[PATH_CAP], sub[PATH_CAP];
    WIN32_FIND_DATAA fd;
    for (int k = 0; k < 2; k++) {
        _snprintf_s(pattern, sizeof pattern, _TRUNCATE, "%s\\%s", dir, kinds[k]);
        HANDLE h = FindFirstFileA(pattern, &fd);
        if (h != INVALID_HANDLE_VALUE) {
            FindClose(h);
            return _snprintf_s(out, cap, _TRUNCATE, "%s\\%s", dir, fd.cFileName) > 0;
        }
    }
    _snprintf_s(pattern, sizeof pattern, _TRUNCATE, "%s\\*", dir);
    HANDLE h = FindFirstFileA(pattern, &fd);
    if (h == INVALID_HANDLE_VALUE) return 0;
    int found = 0;
    do {
        if (!(fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) || fd.cFileName[0] == '.') continue;
        _snprintf_s(sub, sizeof sub, _TRUNCATE, "%s\\%s", dir, fd.cFileName);
        for (int k = 0; k < 2 && !found; k++) {
            WIN32_FIND_DATAA f2;
            _snprintf_s(pattern, sizeof pattern, _TRUNCATE, "%s\\%s", sub, kinds[k]);
            HANDLE h2 = FindFirstFileA(pattern, &f2);
            if (h2 == INVALID_HANDLE_VALUE) continue;
            FindClose(h2);
            found = _snprintf_s(out, cap, _TRUNCATE, "%s\\%s", sub, f2.cFileName) > 0;
        }
    } while (!found && FindNextFileA(h, &fd));
    FindClose(h);
    return found;
}

int port_verify_assets(const char *assets)
{
    char image[PATH_CAP];
    if (!port_find_image(assets, image, sizeof image))
        return fail(4, "no disc image (.cue or .gdi) in %s", assets);
    int code = check_image(image);
    if (!code) port_log("setup: %s verified\n", image);
    return code;
}

static int make_dirs(const char *path) /* every folder of path */
{
    char p[PATH_CAP];
    strncpy_s(p, sizeof p, path, _TRUNCATE);
    for (char *c = p + 1; *c; c++)
        if ((*c == '\\' || *c == '/') && c[-1] != ':') {
            char keep = *c;
            *c = 0;
            CreateDirectoryA(p, NULL);
            *c = keep;
        }
    return CreateDirectoryA(p, NULL) || GetLastError() == ERROR_ALREADY_EXISTS;
}

static int write_text(const char *path, const char *text)
{
    char tmp[PATH_CAP];
    FILE *f = NULL;
    _snprintf_s(tmp, sizeof tmp, _TRUNCATE, "%s.tmp", path);
    if (fopen_s(&f, tmp, "wb") != 0 || !f) return 0;
    int ok = fputs(text, f) >= 0;
    ok = fclose(f) == 0 && ok;
    return ok && MoveFileExA(tmp, path, MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH);
}

static void json_escape(const char *s, char *out, size_t cap)
{
    size_t n = 0;
    for (; *s && n + 3 < cap; s++) {
        if (*s == '"' || *s == '\\') out[n++] = '\\';
        out[n++] = *s;
    }
    out[n] = 0;
}

int port_setup(const char *image, const char *work)
{
    FileList *l = (FileList *)calloc(1, sizeof *l);
    char src_dir[PATH_CAP], dst_dir[PATH_CAP], src[2 * PATH_CAP], dst[2 * PATH_CAP], copy[PATH_CAP], text[512];
    char version[256];
    int code = check_image(image);
    if (code || !l) {
        free(l);
        return code ? code : fail(1, "out of memory");
    }
    list_files(image, l);
    folder_of(image, src_dir, sizeof src_dir);
    _snprintf_s(dst_dir, sizeof dst_dir, _TRUNCATE, "%s\\%s\\", work, ASSETS);
    if (!make_dirs(dst_dir)) code = fail(1, "cannot create %s", dst_dir);
    for (int i = -1; !code && i < l->n; i++) { /* -1: the .cue or .gdi itself */
        const char *name = i < 0 ? base_of(image) : l->name[i];
        if (strchr(name, ':') || name[0] == '\\' || name[0] == '/' || strstr(name, "..")) {
            code = fail(1, "the image names a track file outside its folder: %s", name);
            break;
        }
        _snprintf_s(src, sizeof src, _TRUNCATE, "%s%s", src_dir, name);
        _snprintf_s(dst, sizeof dst, _TRUNCATE, "%s%s", dst_dir, name);
        folder_of(dst, copy, sizeof copy);
        port_log("setup: copying %s\n", name);
        port_log_flush();
        if (!make_dirs(copy) || (_stricmp(src, dst) && !CopyFileA(src, dst, FALSE)))
            code = fail(1, "cannot copy %s to %s (error %lu)", src, dst, GetLastError());
    }
    free(l);
    if (code) return code;
    _snprintf_s(dst, sizeof dst, _TRUNCATE, "%s%s", dst_dir, base_of(image));
    if ((code = check_image(dst)) != 0) return fail(1, "the copied image %s does not check out", dst);
    json_escape(port_version, version, sizeof version);
    _snprintf_s(text, sizeof text, _TRUNCATE, "{\"version\": \"%s\", \"assets\": \"%s\"}\n", version, "assets/disc");
    _snprintf_s(dst, sizeof dst, _TRUNCATE, "%s\\installation.json", work);
    if (!write_text(dst, text)) return fail(1, "cannot write %s", dst);
    port_log("setup: done, %s\n", dst);
    port_log_flush();
    return 0;
}
