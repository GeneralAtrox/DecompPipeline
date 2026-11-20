/* session.c's recording buffers, shared with session_other.c (players 2..8 and the right sticks): split from session.c
 * at its 400-line cap; also session_events.c (the sixth section). Only those three include it. */
#ifndef SESSION_PRIV_H
#define SESSION_PRIV_H

#include <stdio.h>
#include "base.h"

typedef struct Buf {
    u8 *p;
    u32 n, cap, at; /* at: the replay's read position */
} Buf;

enum { PAD, TIMER, RTC, SAVES, SECTIONS, OTHERS = SECTIONS, ALL };

/* Player 0..7 (1..8): the host's pads A..D, and a game's own players beyond them. Player 0's entry holds only its
 * right stick (its pad is PadEntry's); the others' hold the pad and the right stick. flags bit 0: connected.
 * Nothing is sized at 4 players. */
#define OTHERS_MAX 8
typedef struct OtherEntry {
    u32 read, vblank, buttons;
    u8 l, r;
    s8 x, y;
    u8 player, flags;
    s8 rx, ry;
} OtherEntry;

extern Buf session_rec[ALL], session_play[ALL]; /* the recording made, and the one replayed (session_load) */
extern u32 session_play_reads;                   /* the replayed recording's pad reads */
extern int g_session_replay;                     /* --replay given */
void session_put(Buf *b, const void *p, u32 n);  /* appends to a section being recorded */
const u8 *session_take(int s, u32 n);            /* the replay's next n bytes of a section, or NULL at its end */
int session_events_any(void);                    /* the sixth section, EVENTS (session_events.c), has entries: the fifth section is written too */
int session_events_write(FILE *f);
int session_events_load(FILE *f);                /* 1: read, or the file has none; 0: cut short */

#endif
