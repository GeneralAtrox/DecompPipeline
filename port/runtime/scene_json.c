/* The scene dump's JSON (scene_json.h). */
#include <stdarg.h>
#include <stdlib.h>
#include <string.h>
#include "port.h"
#include "scene_host.h"
#include "scene_json.h"

#define LOG_MAX (4u << 20)
#define LOG_TAIL 40u
#define LOG_WARNINGS 30u
#define MAX_FILES 256u

static void put(Json *j, const char *s, u32 n)
{
    if (j->n + n + 1u > j->cap) {
        u32 cap = j->cap ? j->cap * 2u : 16384u;
        while (cap < j->n + n + 1u) cap *= 2u;
        char *q = (char *)realloc(j->p, cap);
        if (!q) return;
        j->p = q;
        j->cap = cap;
    }
    memcpy(j->p + j->n, s, n);
    j->n += n;
    j->p[j->n] = '\0';
}

void json_put(Json *j, const char *s) { put(j, s, (u32)strlen(s)); }

void json_f(Json *j, const char *fmt, ...)
{
    char tmp[2048];
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(tmp, sizeof tmp, fmt, ap);
    va_end(ap);
    if (n > 0) put(j, tmp, (u32)n < sizeof tmp ? (u32)n : (u32)sizeof tmp - 1u);
}

void json_str(Json *j, const char *s, u32 max)
{
    put(j, "\"", 1);
    for (u32 i = 0; s && i < max && s[i]; i++) {
        u8 c = (u8)s[i];
        if (c == '"' || c == '\\') json_f(j, "\\%c", c);
        else if (c == '\n') put(j, "\\n", 2);
        else if (c < 0x20 || c == 0x7f) json_f(j, "\\u%04x", c);
        else put(j, (const char *)&c, 1);
    }
    put(j, "\"", 1);
}

/* The field's bytes in guest RAM, through its pointer chain; NULL if any step is outside RAM. */
static const u8 *field_at(const SceneField *f)
{
    u32 a = (u32)(uintptr_t)f->addr;
    for (u32 i = 0; i < f->deref_count; i++) {
        if (!guest_is_ram(guest_fold(a)) || !guest_is_ram(guest_fold(a + 3u))) return NULL;
        a = *(const u32 *)(uintptr_t)guest_fold(a) + (u32)f->deref[i];
    }
    a = guest_fold(a);
    return guest_is_ram(a) && guest_is_ram(a + f->size - 1u) ? (const u8 *)(uintptr_t)a : NULL;
}

/* An integer field's value; 0 if it is not one or cannot be read. */
static int field_int(const SceneField *f, s32 *v)
{
    const u8 *p = field_at(f);
    if (!p || f->kind > SCENE_UINT) return 0;
    if (f->size == 1) *v = f->kind == SCENE_INT ? *(const s8 *)p : *p;
    else if (f->size == 2) *v = f->kind == SCENE_INT ? *(const s16 *)p : *(const u16 *)p;
    else *v = *(const s32 *)p;
    return 1;
}

const SceneField *scene_field(const char *key)
{
    for (u32 i = 0; i < scene_field_count; i++)
        if (!strcmp(scene_fields[i].key, key)) return &scene_fields[i];
    return NULL;
}

void scene_field_value(Json *j, const SceneField *f, int text)
{
    const u8 *p = field_at(f);
    const char *name = NULL;
    char num[16];
    s32 v;
    if (!p) {
        json_put(j, text ? "?" : "null");
        return;
    }
    if (f->kind == SCENE_STRING) {
        if (text) json_f(j, "%.*s", (int)strnlen((const char *)p, f->size), (const char *)p);
        else json_str(j, (const char *)p, f->size);
        return;
    }
    if (f->kind == SCENE_FLOAT) {
        json_f(j, "%.4g", f->size == 8 ? *(const double *)p : (double)*(const f32 *)p);
        return;
    }
    if (!field_int(f, &v)) return;
    sprintf_s(num, sizeof num, f->kind == SCENE_UINT && f->size == 4 ? "%u" : "%d", v);
    for (u32 i = 0; i < f->name_count && !name; i++)
        if (f->names[i].value == v) name = f->names[i].name;
    if (!name) json_put(j, num);
    else if (text) json_f(j, "%s (%s)", num, name);
    else {
        json_f(j, "{\"value\": %s, \"name\": ", num);
        json_str(j, name, 256);
        put(j, "}", 1);
    }
}

void scene_file_list(Json *j, int edit)
{
    const char *seen[MAX_FILES];
    u32 n = 0;
    put(j, "[", 1);
    for (u32 r = 0; r < scene_rule_count; r++) {
        const SceneRule *rule = &scene_rules[r];
        const SceneField *f = rule->key ? scene_field(rule->key) : NULL;
        s32 v = 0;
        int applies = !rule->key || (f && (f->kind > SCENE_UINT ? field_at(f) != NULL : field_int(f, &v)));
        if (applies && rule->value_count) {
            applies = 0;
            for (u32 k = 0; k < rule->value_count; k++) applies |= rule->values[k] == v;
        }
        for (u32 i = 0; applies && i < (edit ? rule->edit_count : rule->read_count) && n < MAX_FILES; i++) {
            const char *path = edit ? rule->edit[i] : rule->read[i];
            int dup = 0;
            for (u32 k = 0; k < n; k++) dup |= !strcmp(seen[k], path);
            if (dup) continue;
            if (n) put(j, ", ", 2);
            json_str(j, path, 520);
            seen[n++] = path;
        }
    }
    put(j, "]", 1);
}

/* The lines of text (cut at each newline, a CR dropped), at most max; the count. */
static u32 split(char *text, char **lines, u32 max)
{
    u32 n = 0;
    for (char *p = text; p && *p && n < max;) {
        char *e = strchr(p, '\n');
        lines[n++] = p;
        if (!e) break;
        *e = '\0';
        if (e > p && e[-1] == '\r') e[-1] = '\0';
        p = e + 1;
    }
    return n;
}

static void list(Json *j, const char *name, char *const *lines, u32 n, const char *indent)
{
    json_f(j, "%s\"%s\": [", indent, name);
    for (u32 i = 0; i < n; i++) {
        json_f(j, "%s\n%s  ", i ? "," : "", indent);
        json_str(j, lines[i], 400);
    }
    if (n) json_f(j, "\n%s", indent);
    put(j, "]", 1);
}

void scene_log_lists(Json *j, const char *log, const char *indent)
{
    static const char *const marks[] = {"TODO", "STUB", "WATCHDOG", "cannot", "failed", "error", "out of step",
                                        "fault"};
    u32 size, max = LOG_MAX / 32u, k = 0;
    char *text = scene_read_file(log, LOG_MAX, &size), **lines = (char **)malloc(max * sizeof(char *));
    char *tail[LOG_TAIL], *warn[LOG_WARNINGS], *ordered[LOG_WARNINGS];
    u32 n = lines ? split(text, lines, max) : 0, first = size == LOG_MAX && n ? 1u : 0u; /* a cut first line */
    u32 t = n - first > LOG_TAIL ? n - LOG_TAIL : first, w;
    for (u32 i = t; i < n; i++) tail[i - t] = lines[i];
    for (u32 i = first; i < n; i++)
        for (u32 m = 0; m < sizeof marks / sizeof marks[0]; m++)
            if (strstr(lines[i], marks[m])) {
                warn[k++ % LOG_WARNINGS] = lines[i]; /* the last LOG_WARNINGS, in a ring */
                break;
            }
    w = k < LOG_WARNINGS ? k : LOG_WARNINGS;
    for (u32 i = 0; i < w; i++) ordered[i] = warn[(k - w + i) % LOG_WARNINGS];
    list(j, "log_warnings", ordered, w, indent);
    json_put(j, ",\n");
    list(j, "log_tail", tail, n - t, indent);
    free(lines);
    free(text);
}
