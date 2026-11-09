/* The edited arena's level file read (sat_level.c; sat_level_priv.h shares what its files need): the file read whole,
 * the value after a key on an entity's line, an entity's id, and the edits a level file holds (start points, the light,
 * the stands, blockers, layout records), each checked against the game's range.
 * Nearest existing: sat_level.c - split from it at 400 lines (its level-file section). */
#include <intrin.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <windows.h>
#include "sat.h"
#include "sat_sys.h"
#include "sat_net.h"
#include "sat_disc.h"
#include "console.h"
#include "sat_game.h"
#include "session.h"
#include "game_log.h"
#include "host.h"
#include "level_edit.h"
#include "sat_level_priv.h"

#ifdef SAT_LEVEL_EDIT
char *sat_lv_read_all(const char *path, size_t *len)
{
    FILE *f = NULL;
    char *b;
    long n;
    if (fopen_s(&f, path, "rb") || !f) return NULL;
    fseek(f, 0, SEEK_END);
    n = ftell(f);
    fseek(f, 0, SEEK_SET);
    b = n >= 0 && n < (8 << 20) ? (char *)malloc((size_t)n + 1) : NULL;
    if (b && fread(b, 1, (size_t)n, f) != (size_t)n) free(b), b = NULL;
    fclose(f);
    if (b) b[n] = 0, *len = (size_t)n;
    return b;
}

/* the value after "key": on this line (line .. end), NULL if absent */
const char *sat_lv_field(const char *line, const char *end, const char *key)
{
    char k[40];
    size_t n = (size_t)snprintf(k, sizeof k, "\"%s\"", key);
    for (const char *p = line; p + n <= end; p++)
        if (!memcmp(p, k, n)) {
            p += n;
            while (p < end && (*p == ' ' || *p == ':')) p++;
            return p < end ? p : NULL;
        }
    return NULL;
}
int sat_lv_id_of(const char *line, const char *end, char *id, size_t cap)
{
    const char *p = sat_lv_field(line, end, "id"), *q;
    if (!p || *p != '"' || !(q = memchr(p + 1, '"', (size_t)(end - p - 1))) || (size_t)(q - p - 1) >= cap) return 0;
    memcpy(id, p + 1, (size_t)(q - p - 1));
    id[q - p - 1] = 0;
    return 1;
}
static int three(const char *p, long long v[3])
{
    char *e;
    if (!p || *p != '[') return 0;
    p++;
    for (int k = 0; k < 3; k++) {
        v[k] = strtoll(p, &e, 10);
        if (e == p) return 0;
        p = e;
        while (*p == ' ' || *p == ',') p++;
    }
    return *p == ']';
}
static int whole(const long long v[3])
{
    return v[0] >= -32768 && v[0] <= 32767 && v[1] >= -32768 && v[1] <= 32767 && v[2] >= -32768 && v[2] <= 32767;
}

/* the edits a level file holds; 0 if none (logged with why) */
int sat_lv_load(const char *path, Edits *e)
{
    size_t len = 0;
    char *t = sat_lv_read_all(path, &len), id[32];
    int n = 0, bad = 0;
    memset(e, 0, sizeof *e);
    e->magic = MAGIC;
    if (!t) return 0;
    for (char *line = t, *end; *line; line = *end ? end + 1 : end) {
        long long v[3], s[3];
        end = strchr(line, '\n');
        if (!end) end = line + strlen(line);
        if (!sat_lv_id_of(line, end, id, sizeof id)) continue;
        if (!strncmp(id, "spawn-", 6) && (id[6] == '1' || id[6] == '2') && !id[7]) {
            int k = id[6] - '1';
            if (!three(sat_lv_field(line, end, "pos"), v)) { bad++; continue; }
            for (int j = 0; j < 3; j++) e->spawn[k][j] = (s32)v[j];
            e->has_spawn |= 1u << k, n++;
        } else if (!strcmp(id, "sun")) {
            if (!three(sat_lv_field(line, end, "dir"), v) || (!v[0] && !v[1] && !v[2])) { bad++; continue; }
            for (int j = 0; j < 3; j++) e->light[j] = (s32)v[j];
            e->has_light = 1, n++;
        } else if (!strcmp(id, "stands")) {
            if (!three(sat_lv_field(line, end, "pos"), v) || !whole(v)) { bad++; continue; }
            for (int j = 0; j < 3; j++) e->stands[j] = (s32)v[j];
            e->has_stands = 1, n++;
        } else if (!strncmp(id, "blocker-", 8)) {
            int ok = e->nblock < MAXBLOCK && three(sat_lv_field(line, end, "pos"), v) && three(sat_lv_field(line, end, "size"), s) && whole(v);
            for (int j = 0; ok && j < 3; j++) ok = s[j] > 0 && s[j] <= 8192;
            if (!ok) { bad++; continue; }
            for (int j = 0; j < 3; j++) e->block[e->nblock][j] = (s32)v[j], e->block[e->nblock][3 + j] = (s32)s[j];
            e->nblock++, n++;
        } else if (!strncmp(id, "object-", 7)) {
            int i = atoi(id + 7), ok = i >= 0 && i < (int)G.max && i < MAXREC && three(sat_lv_field(line, end, "pos"), v) && sat_lv_field(line, end, "kind") && whole(v);
            if (!ok) { bad++; continue; }
            e->kind[i] = (s16)atoi(sat_lv_field(line, end, "kind"));
            for (int j = 0; j < 3; j++) e->pos[i][j] = (s16)v[j];
            e->has_rec |= 1u << i, n++;
        }
    }
    free(t);
    if (bad) game_log_line("level: %d entities in %s were left out (a number missing or out of the game's range)", bad, path);
    return n;
}
#endif
