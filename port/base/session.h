/* session.c: the session recording, for replaying a play run exactly (its file comment has the details). The SDK's
 * timer goes through session_timer (platform.h). */
#ifndef SESSION_H
#define SESSION_H

#include "base.h"
#include "console.h"

int session_option(const char *arg, const char *value); /* --replay FILE, --record off; 0 if not ours */
int session_load(const char *path);                     /* a recording to replay; 0 if it cannot be read */
void session_pad(HostPad *pad);                         /* each pad read: recorded, or replaced by the replay's */
void session_time(HostTime *t);                         /* each RTC read */
int session_save_replayed(const char *name, void *buf, u32 size); /* the replay's save read, -2 if none */
void session_save_read(const char *name, const void *buf, u32 size, int got); /* records a save read */
int session_note_replayed(const char *name, void *buf, u32 size); /* the replay's next save entry if it has this name: 1 and taken; else 0 and left */
void session_mix_ahead(u32 blanks);                     /* records the sound chip's blanks mixed ahead at this blank */
u32 session_mix_ahead_replayed(void);                   /* the replay's blanks mixed ahead at this blank, 0 if none */
u32 session_reads(void);                                /* pad reads so far */
int session_replaying(void);
int session_write(const char *path, const char *header); /* the recording so far; 0 on failure or if cut short */

#endif
