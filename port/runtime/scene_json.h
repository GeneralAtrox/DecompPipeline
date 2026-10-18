/* scene_json.c: the scene dump's JSON (scene_dump.c): a growing text buffer, the map's fields and file lists, and
 * the log's warnings and tail. */
#ifndef SCENE_JSON_H
#define SCENE_JSON_H

#include "rd_rt.h"
#include "scene_dump.h"

typedef struct Json {
    char *p;
    u32 n, cap;
} Json;

void json_put(Json *j, const char *s);
void json_f(Json *j, const char *fmt, ...);
void json_str(Json *j, const char *s, u32 max); /* a JSON string of s (at most max bytes, to a NUL) */
const SceneField *scene_field(const char *key);
void scene_field_value(Json *j, const SceneField *f, int text); /* JSON, or (text) as in the task line */
void scene_file_list(Json *j, int edit);  /* files.read or files.edit: the rules that apply, each path once */
void scene_log_lists(Json *j, const char *log, const char *indent); /* "log_warnings" and "log_tail" members */

#endif
