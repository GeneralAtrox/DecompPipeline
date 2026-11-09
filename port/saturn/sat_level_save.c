/* Saving the edited arena (sat_level.c; sat_level_priv.h shares what its files need): the level file kept as it is
 * around its entity lines with their numbers patched and the blockers written anew, or written afresh, then replaced
 * atomically; Play; and the provider the base's level editor calls.
 * Nearest existing: sat_level.c - split from it at 400 lines (its saving section). */
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
typedef struct {
    char *p;
    size_t n, cap;
} Buf;
static int put(Buf *b, const char *s, size_t n)
{
    if (b->n + n + 1 > b->cap) {
        size_t c = (b->cap + n) * 2 + 4096;
        char *q = (char *)realloc(b->p, c);
        if (!q) return 0;
        b->p = q, b->cap = c;
    }
    memcpy(b->p + b->n, s, n), b->n += n, b->p[b->n] = 0;
    return 1;
}
static int puts_(Buf *b, const char *s) { return put(b, s, strlen(s)); }

/* the [x, y, z] after "key" in a line (NUL-terminated, room for cap) replaced by v */
static int patch(char *line, size_t cap, const char *key, const s64 v[3])
{
    char num[96], *b = (char *)sat_lv_field(line, line + strlen(line), key), *e;
    size_t nl, tail;
    if (!b || *b != '[' || !(e = strchr(b, ']'))) return 0;
    snprintf(num, sizeof num, "[%lld, %lld, %lld]", v[0], v[1], v[2]);
    nl = strlen(num), tail = strlen(e + 1);
    if ((size_t)(b - line) + nl + tail + 1 > cap) return 0;
    memmove(b + nl, e + 1, tail + 1);
    memcpy(b, num, nl);
    return 1;
}

/* the file kept as it is around its entity lines: their numbers patched, unknown entities kept, blockers written anew;
 * 0 when it cannot be (no entity lines, lines not together, an entity of the editor's missing): then afresh */
static int merge(const LevelScene *sc, const char *old, Buf *out, int *changed, char *msg, size_t cap)
{
    const char *first = NULL, *last_end = NULL, *next;
    u8 seen[MAXENT] = {0};
    int lines = 0;
    for (const char *line = old; *line; line = next) {
        const char *end = strchr(line, '\n');
        char id[32], buf[1024];
        size_t n;
        u32 j;
        next = end ? end + 1 : line + strlen(line);
        if (!end) end = next;
        if (!sat_lv_id_of(line, end, id, sizeof id)) continue;
        if (first && line != last_end) return 0; /* entity lines apart */
        if (!first) {
            first = line;
            put(out, old, (size_t)(line - old));
        }
        last_end = next;
        n = (size_t)(end - line);
        while (n && (line[n - 1] == '\r' || line[n - 1] == ' ' || line[n - 1] == ',')) n--;
        if (n >= sizeof buf - 64) return 0;
        memcpy(buf, line, n), buf[n] = 0;
        if (!strncmp(id, "blocker-", 8)) continue; /* written anew below */
        for (j = 0; j < sc->nent && strcmp(sc->ent[j].id, id); j++) {}
        if (j < sc->nent) {
            s64 v[3], z[3];
            int r = sat_lv_saved(sc, &sc->ent[j], v, z);
            if (r < 0) return snprintf(msg, cap, "%s is out of the game's range", sc->ent[j].name), -1;
            if (r > 0) {
                char was[1024];
                memcpy(was, buf, n + 1);
                if (!patch(buf, sizeof buf, r == 2 ? "dir" : "pos", v)) return 0;
                *changed += strcmp(was, buf) != 0;
            }
            seen[j] = 1;
        }
        if (lines++) puts_(out, ",\n");
        puts_(out, buf);
    }
    if (!first) return 0;
    for (u32 j = 0; j < sc->nent; j++) {
        s64 v[3], z[3];
        int r = sat_lv_saved(sc, &sc->ent[j], v, z);
        if (r < 0) return snprintf(msg, cap, "%s is out of the game's range", sc->ent[j].name), -1;
        if (r == 3) {
            char b[400];
            sat_lv_line_of(sc, &sc->ent[j], b, sizeof b);
            puts_(out, lines++ ? ",\n  " : "  "), puts_(out, b);
        } else if (r > 0 && !seen[j]) return 0;
    }
    puts_(out, "\n");
    return puts_(out, last_end);
}

static int write_atomic(const char *path, const char *text, size_t len)
{
    char tmp[320];
    FILE *f = NULL;
    snprintf(tmp, sizeof tmp, "%s.tmp", path);
    if (fopen_s(&f, tmp, "wb") || !f) return 0;
    if (fwrite(text, 1, len, f) != len) return fclose(f), 0;
    fclose(f);
    return MoveFileExA(tmp, path, MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH) != 0;
}

int sat_lv_scene_save_to(LevelScene *sc, const char *path, char *msg, size_t cap)
{
    size_t len = 0;
    char *old = sat_lv_read_all(path, &len), b[400], head[300];
    Buf out = {0};
    int changed = 0, m = old ? merge(sc, old, &out, &changed, msg, cap) : 0, ok;
    if (m < 0) return free(old), free(out.p), 0;
    if (m) {
        ok = write_atomic(path, out.p, out.n);
        snprintf(msg, cap, ok ? "Saved %s (%d changed)" : "Could not write %s", path, changed);
        return free(old), free(out.p), ok;
    }
    free(old);
    out.n = 0;
    snprintf(head, sizeof head, "{\"format\": \"dp-level\", \"version\": 0, \"game\": \"%s\", \"level\": \"%s\", \"schema\": \"%s\", \"entities\": [\n",
             SAT_LEVEL_GAME, SAT_LEVEL_SLUG, SAT_LEVEL_SCHEMA);
    puts_(&out, head);
    for (u32 i = 0, first = 1; i < sc->nent; i++) {
        int r = sat_lv_line_of(sc, &sc->ent[i], b, sizeof b);
        if (r < 0) return free(out.p), snprintf(msg, cap, "%s is out of the game's range", sc->ent[i].name), 0;
        if (!r) continue;
        puts_(&out, first ? "  " : ",\n  "), puts_(&out, b), first = 0;
    }
    ok = puts_(&out, "\n]}\n") && write_atomic(path, out.p, out.n);
    free(out.p);
    snprintf(msg, cap, ok ? "Saved %s" : "Could not write %s", path);
    return ok;
}

static int scene_save(LevelScene *sc, char *msg, size_t cap)
{
    char path[300], dir[300], *slash;
    sat_lv_level_path(path, sizeof path);
    snprintf(dir, sizeof dir, "%s", path);
    if ((slash = strrchr(dir, '\\')) != NULL) *slash = 0, CreateDirectoryA(dir, NULL);
    if (!sat_lv_scene_save_to(sc, path, msg, cap)) {
        game_log_line("level editor: %s", msg);
        return 0;
    }
    snprintf(sc->saved_to, sizeof sc->saved_to, "%s", path);
    game_log_line("level editor: %s", msg);
    return 1;
}

static void scene_play(LevelScene *sc)
{
    (void)sc;
    port_setting_set("level-mod", "on");
    if (!port_start_again("--level-mod on --level-play on")) game_log_line("level editor: Play could not start the game again");
}

const LevelEdProvider sat_level_provider = {SAT_LEVEL_TITLE, sat_lv_scene_open, scene_save, scene_play, sat_lv_scene_close};
#endif
