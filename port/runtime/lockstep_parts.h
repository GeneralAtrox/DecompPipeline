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

#define LS_MAX_ITEMS 512u
#define LS_MAX_STEPS 8u

/* One watched item of the spec (lockstep_spec.c): its address steps and flags. */
typedef struct LsItem {
    u32 base, words, steps, key, rand, frames, paused, counter;
    s32 step[LS_MAX_STEPS];
} LsItem;

typedef struct LsSpec {
    LsItem items[LS_MAX_ITEMS];
    u32 nitems, nkey;
    const LsItem *frames;  /* the game's frame counter (flag frames), if the spec names one */
    const LsItem *paused;  /* the game clock's pause count (flag paused) */
    const LsItem *counter; /* the label as a frame counter, not a timer copy (flag counter) */
} LsSpec;

/* lockstep_spec.c */
int lockstep_spec_load(const char *path, LsSpec *spec);           /* appends the file's items; 0 on an error */
int lockstep_item_word(const LsItem *it, u32 j, u32 *w);          /* word j; 0 if it is not in guest RAM */
volatile u32 *lockstep_word_host(const LsSpec *spec, u32 n);      /* trace word n in host memory; NULL if none */

/* lockstep_emu.c */
u32 lockstep_load_emu(const char *path, EmuFrame **frames); /* the --lockstep file; the frame count, 0 if none */
void lockstep_rtc_date(u32 seconds, HostTime *t);            /* the AICA's RTC as a date */

/* lockstep_watch.c */
void lockstep_watch(volatile u32 *at, u32 frame); /* watches writes to at from here (on this thread); NULL stops */

#endif
