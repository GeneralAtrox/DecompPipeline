/* session.c: the session recording, for replaying a play run exactly (its file comment has the details). The SDK's
 * timer goes through session_timer (platform.h). */
#ifndef SESSION_H
#define SESSION_H

#include "rd_rt.h"
#include "platform.h"

int session_option(const char *arg, const char *value); /* --replay FILE, --record off; 0 if not ours */
int session_load(const char *path);                     /* a recording to replay; 0 if it cannot be read */
void session_pad(HostPad *pad);                         /* each pad read: recorded, or replaced by the replay's */
void session_time(HostTime *t);                         /* each RTC read */
int session_save_replayed(const char *name, void *buf, u32 size); /* the replay's save read, -2 if none */
void session_save_read(const char *name, const void *buf, u32 size, int got); /* records a save read */
u32 session_reads(void);                                /* pad reads so far */
int session_replaying(void);
int session_write(const char *path, const char *header); /* the recording so far; 0 on failure or if cut short */

#endif
