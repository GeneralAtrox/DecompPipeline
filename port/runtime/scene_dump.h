/* scene_dump.c: "Copy agent prompt" (the copy icon, Help menu, F9): writes <stamp>.json, <stamp>.bmp (the frame as
 * drawn) and <stamp>.ptrec (the session recording, runtime/session.c) to the data folder's scene_dumps, and puts
 * the JSON on the clipboard as an agent prompt (an instruction line, the JSON, Phangrade's context): what the game is
 * doing, what the user did, and the command that replays the session to that frame. Failures are logged and never
 * stop the game.
 *   --scene-dump-at N   also dumps at the first frame presented at or after vertical blank N (scripted checks)
 *   --scene-dir DIR     the folder instead of the data folder's scene_dumps
 * The game's part is its scene_dump.json, which tools/gen_port.py turns into the tables below (build/gen/scene_map.c):
 * guest values by global name, the line the task is made from, and which files to read or edit in which scene. */
#ifndef SCENE_DUMP_H
#define SCENE_DUMP_H

#include "rd_rt.h"

enum { SCENE_INT, SCENE_UINT, SCENE_FLOAT, SCENE_STRING };

typedef struct SceneName {
    s32 value;
    const char *name;
} SceneName;

typedef struct SceneField {
    const char *key;
    const void *addr;    /* the global (a guest address) */
    const s32 *deref;    /* then for each offset: read the pointer there, add the offset (NULL: none) */
    u32 deref_count;
    u32 size, kind;      /* the value's bytes and SCENE_*; a string is a char array of size bytes */
    const SceneName *names; /* value names (from the game's headers or the map), or NULL */
    u32 name_count;
} SceneField;

typedef struct SceneRule {
    const char *key;      /* a field; NULL: always */
    const s32 *values;    /* the rule applies when the field is one of these (none listed: any value) */
    u32 value_count;
    const char *const *read, *const *edit;
    u32 read_count, edit_count;
} SceneRule;

extern const SceneField scene_fields[];
extern const u32 scene_field_count;
extern const char *const scene_task_keys[]; /* the fields the task line names, in order */
extern const u32 scene_task_count;
extern const SceneRule scene_rules[];
extern const u32 scene_rule_count;
extern const char scene_root[]; /* the folder the file paths are relative to (absolute) */

int scene_dump_option(const char *arg, const char *value); /* 0 if not ours */
void scene_dump_request(void); /* the host's button or key: dumps after the next presented frame */
void scene_dump_poll(u32 frame); /* gfx_present.c, after each presented frame */

#endif
