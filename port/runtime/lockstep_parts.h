/* The lockstep check's parts in their own files (lockstep.c is the rest). */
#ifndef LOCKSTEP_PARTS_H
#define LOCKSTEP_PARTS_H

#include <stdio.h>
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
    char name[32];
    const struct LsItem *gate; /* flag if=ITEM:VALUE: read only while that item's word equals gate_value */
    u32 gate_word, gate_value;
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
/* logs each call of the host functions fn[0..count) (guest addresses guest[]) from here (on this thread; count 0
 * stops): words words at its first argument and n at the pointer at argument + off, with the frames word;
 * lockstep_entry_frame labels the calls that follow */
#define ENTRY_MAX 4u
void lockstep_entry(const u32 *fn, const u32 *guest, u32 count, u32 words, u32 off, u32 n, volatile u32 *frames);
void lockstep_entry_frame(u32 line, u32 label);

/* ta_digest.c: the display-list digest (hw_check.c's walk feeds it; tools/ta_digest.py compares) */
int ta_digest_option(const char *arg, const char *value);   /* --hw-dump F:FILE; 0 if not it */
void ta_digest_list(u32 list);                              /* a list's walk begins */
void ta_digest_header(const u32 *w, u32 words, int sprite); /* a polygon or sprite header */
void ta_digest_vertex(const u32 *w);                        /* a vertex parameter (a sprite's whole one) */
void ta_digest_strip(u32 frame);                            /* a strip ends */
void ta_digest_write(FILE *out, u32 list);                  /* the list's D line */
void ta_digest_scene_done(u32 frame);                       /* the scene's lists are walked */
int ta_digest_dumping(void);                                 /* --hw-dump is on and not done */

#endif
