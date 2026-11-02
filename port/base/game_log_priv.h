/* What game_log.c (the lines) and game_log_end.c (the last line, the crash recording, the start) share. */
#ifndef GAME_LOG_PRIV_H
#define GAME_LOG_PRIV_H

#include <windows.h>
#include "base.h"
#include "console.h"

#define PATH_CAP 520u

typedef struct GameLogCost {
    unsigned long long total, longest, count; /* QueryPerformanceCounter ticks of the poll, the longest one, the polls */
} GameLogCost;

extern int gl_on, gl_started;
extern u32 gl_every, gl_save, gl_poll_frame, gl_poll_vblank, gl_saved_at, gl_fault_at;
extern ULONGLONG gl_t0;
extern char gl_note[600], gl_note_rec[PATH_CAP];
extern GameLogCost gl_cost;

void end_line(const char *fmt, ...);
void log_dir(char *out, size_t cap);
int autosave_path(char *out, size_t cap);
void recording_header(char *out, size_t cap);

#endif
