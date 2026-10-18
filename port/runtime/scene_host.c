/* The Windows side of the scene dump (scene_dump.c): the clipboard, reading a file whole, the stamp, the folder. */
#include <stdlib.h>
#include <string.h>
#include <windows.h>
#include "port.h"
#include "scene_host.h"

int scene_clipboard(const char *utf8)
{
    int n = MultiByteToWideChar(CP_UTF8, 0, utf8, -1, NULL, 0), ok = 0;
    HGLOBAL mem;
    if (n <= 0 || (mem = GlobalAlloc(GMEM_MOVEABLE, (size_t)n * sizeof(wchar_t))) == NULL) return 0;
    wchar_t *w = (wchar_t *)GlobalLock(mem);
    if (w) {
        MultiByteToWideChar(CP_UTF8, 0, utf8, -1, w, n);
        GlobalUnlock(mem);
        for (int tries = 0; tries < 5 && !ok; tries++) { /* another program may hold the clipboard a moment */
            if (OpenClipboard(NULL)) {
                ok = EmptyClipboard() && SetClipboardData(CF_UNICODETEXT, mem) != NULL;
                CloseClipboard();
            } else {
                Sleep(20);
            }
        }
    }
    if (!ok) GlobalFree(mem); /* once set, the clipboard owns it */
    return ok;
}

char *scene_read_file(const char *path, u32 max, u32 *size)
{
    FILE *f = NULL;
    char *buf = NULL;
    long len;
    *size = 0;
    if (!path || fopen_s(&f, path, "rb") || !f) return NULL;
    if (fseek(f, 0, SEEK_END) == 0 && (len = ftell(f)) >= 0) {
        u32 n = (u32)len > max ? max : (u32)len;
        if (fseek(f, (long)((u32)len - n), SEEK_SET) == 0 && (buf = (char *)malloc(n + 1u)) != NULL) {
            *size = (u32)fread(buf, 1, n, f);
            buf[*size] = '\0';
        }
    }
    fclose(f);
    return buf;
}

void scene_stamp(char *out, u32 cap)
{
    SYSTEMTIME t;
    GetLocalTime(&t); /* not host_local_time: that one is the game's clock, and recorded */
    sprintf_s(out, cap, "%04u%02u%02u-%02u%02u%02u", t.wYear, t.wMonth, t.wDay, t.wHour, t.wMinute, t.wSecond);
}

int scene_make_dir(const char *dir)
{
    char path[MAX_PATH];
    strcpy_s(path, sizeof path, dir);
    for (char *p = path + 3; *p; p++) /* each parent, then the folder */
        if (*p == '\\' || *p == '/') {
            char c = *p;
            *p = '\0';
            CreateDirectoryA(path, NULL);
            *p = c;
        }
    return CreateDirectoryA(path, NULL) || GetLastError() == ERROR_ALREADY_EXISTS;
}

void scene_exe_path(char *out, u32 cap)
{
    DWORD n = GetModuleFileNameA(NULL, out, cap);
    if (n == 0 || n >= cap) strcpy_s(out, cap, "port.exe");
}

const char *scene_full_path(const char *path, char *out, u32 cap)
{
    DWORD n = GetFullPathNameA(path, cap, out, NULL);
    if (n == 0 || n >= cap) strcpy_s(out, cap, path);
    return out;
}

const char *scene_command_line(void) { return GetCommandLineA(); }

const char *scene_env(const char *name, char *out, u32 cap)
{
    DWORD n = GetEnvironmentVariableA(name, out, cap);
    return n && n < cap ? out : NULL;
}
