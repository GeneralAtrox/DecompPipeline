/* The lockstep check's parts in their own files (lockstep.c is the rest). */
#ifndef LOCKSTEP_PARTS_H
#define LOCKSTEP_PARTS_H

#include "platform.h"

#define MAX_KEY 16u

typedef struct EmuFrame {
    u32 label, on, rtc, jump, nkey;
    u8 r, l;
    s8 x, y;
    u32 key[MAX_KEY];
} EmuFrame;

/* lockstep_emu.c */
u32 lockstep_load_emu(const char *path, EmuFrame **frames); /* the --lockstep file; the frame count, 0 if none */
void lockstep_rtc_date(u32 seconds, HostTime *t);            /* the AICA's RTC as a date */

/* lockstep_watch.c */
void lockstep_watch(volatile u32 *at, u32 frame); /* watches writes to at from here (on this thread); NULL stops */

#endif
