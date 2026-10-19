/* lockstep.c: the port's half of the lockstep check against an emulator (tools/lockstep.py). */
#ifndef LOCKSTEP_H
#define LOCKSTEP_H

#include "platform.h"

int lockstep_option(const char *arg, const char *value); /* the --lockstep options; 0 if not one */
void lockstep_vblank(void);                              /* each vertical blank, before the game's handler */
void lockstep_scene(void);                               /* each scene's begin (kmBeginScene) */
s32 lockstep_timer(s32 live);                           /* each SDK timer read: a frame starts */
int lockstep_pad(HostPad *pad);                          /* each pad read: 1 if the emulator's frame gave it */
int lockstep_time(HostTime *t);                          /* each RTC read: 1 if the emulator's frame gave it */
void lockstep_close(void);                               /* flushes the trace at exit */
s32 lockstep_frame(void); /* the paired emulator frame (its --lockstep line); -1 when not driven or past them */

/* hw_check.c: the hardware check's port half (tools/hw_check.py). */
struct PvrPass;
int hw_check_option(const char *arg, const char *value);      /* the --hw options; 0 if not one */
void hw_check_begin(void);                                     /* each scene's begin */
void hw_check_scene(const struct PvrPass *passes, u32 count); /* each scene's render, before it draws */
void hw_check_rendered(const struct PvrPass *passes, u32 count); /* and after it took the lists */
void hw_check_vram(u32 offset, u32 size);                      /* each texture memory write */
void hw_check_close(void);                                     /* flushes at exit */

/* ram_frames.c: main RAM as each frame starts, as changed blocks (tools/reference.py). */
int ram_frames_option(const char *arg, const char *value); /* --ram-frames FILE, --ram-frames-range A:B; 0 if not */
void ram_frames_capture(u32 frame, u32 label, s32 emu);    /* each frame's start; emu -2: past them */
void ram_frames_close(void);                               /* flushes at exit */

#endif
