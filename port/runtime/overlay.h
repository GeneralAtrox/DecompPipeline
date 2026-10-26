/* Overlays: code a game loads at run time over the same guest RAM (overlay.c). tools/gen_overlays.py compiles every
 * overlay unit side by side and writes this table; a call into a unit's range runs the unit loaded there now. */
#ifndef OVERLAY_H
#define OVERLAY_H

#include "port_dispatch.h"

#define OVERLAY_WINDOW 16u /* bytes per identity window */

/* Code bytes that differ from every other unit of the group at the same address: all match only while the unit is
 * loaded (code is never written at run time). */
typedef struct {
    u32 addr;
    u8 bytes[OVERLAY_WINDOW];
} PortOverlayWindow;

typedef struct {
    u32 lo, hi; /* [lo, hi) */
} PortOverlayRange;

typedef struct {
    const char *id;
    u32 group; /* the units whose ranges overlap share a group; one of them is active */
    const PortOverlayRange *ranges;
    u32 nranges;
    const PortOverlayWindow *windows;
    u32 nwindows;
    const PortDispatch *rows; /* the unit's functions, by address */
    u32 nrows;
} PortOverlay;

/* Generated (gen_overlays.py overlay_table.c); a game with none links an empty table (overlay.c). */
extern const PortOverlay port_overlays[];
extern const int port_noverlays;

/* NULL if addr is in no unit's range; else the loaded unit's function there, or a row that returns 0 (logged) */
const PortDispatch *overlay_find(u32 addr);
PortFn port_guest_target(u32 addr); /* what a call to guest addr runs (overlay trampolines) */
void overlay_report(void);          /* the unit switches and misses, into the log */

#endif
