/* The packs a release carries: the folder packs/ beside port.exe (tools/package_release.py writes it), and the choices
 * the menu's Texture pack entry offers. The values are what --texture-pack takes, so the settings file's line is that
 * option. Nearest existing: pvr_pack.c (reads one folder's index and images; this finds the folders). */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <windows.h>
#include "pvr_int.h"

#define MAX_CHOICES 16u /* what one menu entry holds (base/host_menu.cpp kExtra) */

static char g_bundled[MAX_CHOICES][PVR_PACK_NAME];
static const char *g_labels[MAX_CHOICES], *g_values[MAX_CHOICES];
static u32 g_choices; /* 0: packs/ not read yet */

int pack_widen(const char *path, WCHAR *out, int cap) { return MultiByteToWideChar(CP_UTF8, 0, path, -1, out, cap) != 0; }

void pack_bundled_dir(const char *name, char *out, size_t cap)
{
    WCHAR wide[PVR_PACK_PATH];
    char exe[PVR_PACK_PATH] = "", *slash;
    DWORD n = GetModuleFileNameW(NULL, wide, PVR_PACK_PATH);
    if (n && n < PVR_PACK_PATH) WideCharToMultiByte(CP_UTF8, 0, wide, -1, exe, sizeof exe, NULL, NULL);
    if ((slash = strrchr(exe, '\\')) != NULL) *slash = '\0';
    snprintf(out, cap, "%s/packs/%s", exe, name);
}

int pack_has_index(const char *dir)
{
    char path[PVR_PACK_PATH + 16];
    WCHAR wide[PVR_PACK_PATH + 16];
    snprintf(path, sizeof path, "%s/index.txt", dir);
    return pack_widen(path, wide, PVR_PACK_PATH + 16) && GetFileAttributesW(wide) != INVALID_FILE_ATTRIBUTES;
}

static int by_name(const void *a, const void *b) { return strcmp(a, b); }

u32 pack_read_choices(int has_game, const char *game_folder, const char *const **labels, const char *const **values)
{
    *labels = g_labels;
    *values = g_values;
    if (g_choices) return g_choices;
    char pattern[PVR_PACK_PATH];
    WCHAR wide[PVR_PACK_PATH];
    WIN32_FIND_DATAW found = {0};
    u32 bundled = 0, room = MAX_CHOICES - (has_game ? 2u : 1u);
    pack_bundled_dir("*", pattern, sizeof pattern);
    HANDLE find = pack_widen(pattern, wide, PVR_PACK_PATH) ? FindFirstFileW(wide, &found) : INVALID_HANDLE_VALUE;
    if (find != INVALID_HANDLE_VALUE) {
        do {
            char name[PVR_PACK_NAME], dir[PVR_PACK_PATH];
            if (!(found.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) || bundled == room) continue;
            if (!WideCharToMultiByte(CP_UTF8, 0, found.cFileName, -1, name, sizeof name, NULL, NULL)) continue;
            pack_bundled_dir(name, dir, sizeof dir);
            if (!pack_has_index(dir) || !strcmp(name, "on") || !strcmp(name, "off") || !strcmp(name, game_folder)) continue;
            snprintf(g_bundled[bundled++], PVR_PACK_NAME, "%s", name);
        } while (FindNextFileW(find, &found));
        FindClose(find);
    }
    qsort(g_bundled, bundled, sizeof g_bundled[0], by_name);
    g_labels[g_choices] = "&Off";
    g_values[g_choices++] = "off";
    if (has_game) {
        g_labels[g_choices] = "&Game's default";
        g_values[g_choices++] = "on";
    }
    for (u32 i = 0; i < bundled; i++) {
        g_labels[g_choices] = g_bundled[i];
        g_values[g_choices++] = g_bundled[i];
    }
    return g_choices;
}
