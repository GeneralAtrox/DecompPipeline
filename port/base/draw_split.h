/* PG_DRAW_SPLIT (environment: any value but empty or 0): where a native game's frame time goes, for finding a slow frame.
 * Nothing is timed and nothing changes when it is unset. At exit (pg_native_split_report, from port_exit) one line:
 *   draw split: 3000 frames (p50/p99/worst ms) game .. ticks_in_game .. copy .. present .. gpu_frame .. gpu_present .. outside .. frame ..
 * game: pg_native_frame to pg_native_show_texture or pg_native_present (the game's own draw calls, and its ticks when it
 * ticks there); ticks_in_game: the ticks inside that span; copy: the CopyResource of pg_native_show_texture; present:
 * present_show (the swap chain's Present: back-pressure from the GPU shows here); gpu_frame and gpu_present: Direct3D 11
 * timestamp queries, the GPU's time from pg_native_frame to the present and of the present's own work (read back without
 * waiting, a ring of 8 frames: a frame not back by then is dropped and counted); outside: the end of the cap's wait to
 * the next pg_native_frame; frame: pg_native_frame to the next.
 * native_frame.c calls these only when g_draw_split > 0, after draw_split_on() decided it at the first frame. */
#ifndef DRAW_SPLIT_H
#define DRAW_SPLIT_H

#include "base.h"

extern int g_draw_split; /* -1 not read yet, 0 off, 1 on */
int draw_split_on(void);   /* reads PG_DRAW_SPLIT once */
void draw_split_frame(void); /* in pg_native_frame */
u64 draw_split_now(void);
void draw_split_copied(u64 since);   /* pg_native_show_texture: the CopyResource that began at since */
void draw_split_game_done(void);      /* pg_native_present, before the present */
void draw_split_presented(u64 since); /* after present_show, which began at since */
void draw_split_waited(void);         /* after the cap's wait */
void draw_split_tick(u64 qpc);        /* a tick's QueryPerformanceCounter ticks */
void draw_split_release(void);        /* the device is let go */

#endif
