/* scene_host.c: the Windows side of the scene dump (scene_dump.c). */
#ifndef SCENE_HOST_H
#define SCENE_HOST_H

#include "rd_rt.h"

int scene_clipboard(const char *utf8);                  /* 1 if the text is on the clipboard */
char *scene_read_file(const char *path, u32 max, u32 *size); /* the file's last max bytes, malloc'd; NULL if none */
void scene_stamp(char *out, u32 cap);                   /* the local time as YYYYMMDD-HHMMSS */
int scene_make_dir(const char *dir);                    /* with its parents; 1 if it exists after */
void scene_exe_path(char *out, u32 cap);
const char *scene_full_path(const char *path, char *out, u32 cap); /* absolute (from the current folder); out */
const char *scene_command_line(void);                   /* the process's own command line */
const char *scene_env(const char *name, char *out, u32 cap); /* the variable's value, or NULL */

#endif
