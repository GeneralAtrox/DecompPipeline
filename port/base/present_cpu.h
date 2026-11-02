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

/* gfx_option's, and --present, --fps, --interp-frames, taken with no effect: the picture is shown once a tick. */
int cpu_present_option(const char *arg, const char *value);
int cpu_present_open(void);                                /* the window and the device (gfx_init); 0 on failure */
void cpu_present(const u32 *xrgb, unsigned w, unsigned h); /* shows a frame (0x00RRGGBB) and pumps the window's events */
void cpu_present_view(PortView *v);                        /* out size, frame size, shown frames a second */

#ifdef __cplusplus
}
#endif

#endif
