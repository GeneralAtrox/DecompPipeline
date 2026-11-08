/* present_cpu.c: for a console whose frame is a CPU buffer (a Saturn's composed VDP2 picture): the frame drawn into the base's
 * frame target (present.h), kept at the console's 4:3 whatever the window's shape. A console with a GPU renderer draws
 * into g_gfx's target itself and does not use this. */
#ifndef PRESENT_CPU_H
#define PRESENT_CPU_H

#include "base.h"
#include "console.h"

#ifdef __cplusplus
extern "C" {
#endif

/* gfx_option's, --present-thread, --present-check and --fps (the showing thread's rate); --present and --interp-frames
 * are taken with no effect. */
int cpu_present_option(const char *arg, const char *value);
int cpu_present_open(void);                                /* the window and the device (gfx_init); 0 on failure */
void cpu_present(const u32 *xrgb, unsigned w, unsigned h); /* shows a frame (0x00RRGGBB) and pumps the window's events */
void cpu_present_view(PortView *v);                        /* out size, frame size, shown frames a second */
int cpu_present_thread_start(int play_run);                /* after cpu_present_open: 1 if frames are shown by their own thread */
int cpu_present_threaded(void);
void cpu_present_post(const u32 *xrgb, unsigned w, unsigned h, u32 vb); /* a finished frame: to that thread, or shown now */
/* a frame wider than the console's screen, which is its middle sw dots: the screen is drawn where a frame of sw would be,
 * the sides beside it at the same dot size (a widescreen picture: saturn/sat_wide.c) */
void cpu_present_post_wide(const u32 *xrgb, unsigned w, unsigned h, u32 vb, unsigned sw);
void cpu_present_set_fps(unsigned fps);                    /* the menu's frame-rate cap, 0 none */
unsigned cpu_present_fps(void);
void cpu_present_report(void);                             /* --present-check: the log's line */

#ifdef __cplusplus
}
#endif

#endif
