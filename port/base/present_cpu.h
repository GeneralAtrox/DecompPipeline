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
void cpu_present_panel(int on); /* a dot whose top byte is 1 + G/2 is shown mixed half and half with grey G (a menu panel) */
unsigned cpu_present_fps(void);
void cpu_present_report(void);                             /* --present-check: the log's line */
/* Pictures drawn over the next posted frame at the window's resolution (present_overlay.c): each at x, y, w, h in the
 * frame's dots, from an image of premultiplied 0xAARRGGBB with an id no other image has, never changed once posted and
 * kept by the poster until no slot can still show it (8 posts). Given before cpu_present_post(_wide); n 0 for none. */
typedef struct PresentImage { u32 id, w, h; const u32 *px; void *srv; } PresentImage; /* srv: its texture, made ahead (cpu_present_prepare) or NULL */
void cpu_present_prepare(PresentImage *im);   /* any thread: the image's texture made now, so the frame that first shows it does not pay */
void cpu_present_unprepare(PresentImage *im); /* that texture let go (the image is no longer posted) */
typedef struct PresentOverlay { float x, y, w, h; const PresentImage *img; } PresentOverlay;
void cpu_present_overlay(const PresentOverlay *o, unsigned n);

#ifdef __cplusplus
}
#endif

#endif
