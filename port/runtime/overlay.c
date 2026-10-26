/* Overlays (overlay.h): every unit is compiled, and a call into overlay RAM runs the unit whose identity windows match
 * guest RAM now. Each group (units sharing RAM) keeps its active unit: a call checks that unit's windows (a few
 * 16-byte compares) and, when they no longer match, searches the group and switches. No loader hook: any way the
 * game puts the code there (a disc read, a copy, a decompression) is seen. */
#include <string.h>
#include "port.h"
#include "overlay.h"

#define MAX_GROUPS 64u

/* A game without overlays links these (the generated table, if any, takes precedence). */
const PortOverlay port_overlays_none[1] = {{0}};
const int port_noverlays_none = 0;
#pragma comment(linker, "/alternatename:_port_overlays=_port_overlays_none")
#pragma comment(linker, "/alternatename:_port_noverlays=_port_noverlays_none")

static u32 g_active[MAX_GROUPS]; /* index + 1 of each group's active unit; 0: none yet */
static u32 g_switches, g_misses;

static int in_unit(const PortOverlay *o, u32 a)
{
    for (u32 i = 0; i < o->nranges; i++)
        if (a >= o->ranges[i].lo && a < o->ranges[i].hi) return 1;
    return 0;
}

static int loaded(const PortOverlay *o)
{
    for (u32 i = 0; i < o->nwindows; i++) {
        const PortOverlayWindow *w = &o->windows[i];
        if (!guest_is_ram(w->addr) || memcmp((const void *)(uintptr_t)w->addr, w->bytes, OVERLAY_WINDOW)) return 0;
    }
    return o->nwindows != 0;
}

static const PortDispatch *row_at(const PortOverlay *o, u32 a)
{
    s32 lo = 0, hi = (s32)o->nrows - 1;
    while (lo <= hi) {
        s32 mid = (lo + hi) / 2;
        if (o->rows[mid].addr == a) return &o->rows[mid];
        if (o->rows[mid].addr < a) lo = mid + 1;
        else hi = mid - 1;
    }
    return NULL;
}

/* The function at a of the unit loaded now that has one there: group g's active unit if so, else the one that is
 * (it becomes active). Only units with a function at a compete: one loaded over another's data leaves its code. */
static const PortDispatch *loaded_row(u32 g, u32 a)
{
    u32 cur = g < MAX_GROUPS ? g_active[g] : 0;
    const PortOverlay *o = cur ? &port_overlays[cur - 1u] : NULL;
    const PortDispatch *d = o && in_unit(o, a) ? row_at(o, a) : NULL;
    if (d && loaded(o)) return d;
    for (int i = 0; i < port_noverlays; i++) {
        const PortOverlay *u = &port_overlays[i];
        if (u->group != g || (u32)i + 1u == cur || !in_unit(u, a) || (d = row_at(u, a)) == NULL || !loaded(u))
            continue;
        if (g < MAX_GROUPS) g_active[g] = (u32)i + 1u;
        g_switches++;
        port_log("overlay: %s active (call to %08x)\n", u->id, a);
        return d;
    }
    return NULL;
}

static u32 __cdecl no_function(void) { return 0; }

/* A call into overlay RAM that no loaded unit has a function for: logged loudly once per group, a checked build
 * stops; it never falls through to main's table or another unit. */
static PortDispatch g_miss = {0, (PortFn)no_function, "(no loaded overlay)"};
static u8 g_miss_logged[MAX_GROUPS];

static const PortDispatch *miss(const PortOverlay *o, u32 a)
{
    g_misses++;
    if (o->group >= MAX_GROUPS || !g_miss_logged[o->group]) {
        if (o->group < MAX_GROUPS) g_miss_logged[o->group] = 1;
        port_log("OVERLAY MISS: call to %08x in %s's range: no loaded unit has a function there; returns 0 (logged "
                 "once per group)\n", a, o->id);
#ifdef PORT_CHECKED
        port_exit("overlay: a call into overlay RAM with no loaded function", 3);
#endif
    }
    g_miss.addr = a;
    return &g_miss;
}

const PortDispatch *overlay_find(u32 addr)
{
    u32 a = guest_fold(addr);
    for (int i = 0; i < port_noverlays; i++) {
        if (!in_unit(&port_overlays[i], a)) continue;
        const PortDispatch *d = loaded_row(port_overlays[i].group, a); /* units covering one address share a group */
        return d && d->fn ? d : miss(&port_overlays[i], a);
    }
    return NULL;
}

PortFn port_guest_target(u32 addr)
{
    const PortDispatch *d = overlay_find(addr);
    if (!d) d = port_dispatch_find(guest_fold(addr));
    if (d && d->fn) return d->fn;
    port_log("CALL %08x (overlay trampoline): no host function; returns 0\n", addr);
    return (PortFn)no_function;
}

void overlay_report(void)
{
    if (port_noverlays) port_log("overlays: %d units, %u switches, %u calls with no loaded function\n", port_noverlays,
                                 g_switches, g_misses);
}
