/* Overlays (overlay.h): every unit is compiled, and a call into overlay RAM runs the unit whose identity windows match
 * guest RAM now. Each group (units sharing RAM) keeps its active unit: a call checks that unit's windows (a few
 * 16-byte compares) and, when they no longer match, searches the group and switches. No loader hook: any way the
 * game puts the code there (a disc read, a copy, a decompression) is seen.
 *
 * Slot units (overlay.h) are the same code loaded at several places: a call at A finds the slot base B = A rounded
 * down to the unit's slot_align, and the group's units compete there by their windows at B (the windows are written
 * at the unit's canonical base, so they are read at B - canon further on). Each slot base keeps its active unit. */
#include <string.h>
#include "port.h"
#include "overlay.h"

#define MAX_GROUPS 64u
#define MAX_SLOTS 32u

/* A game without overlays links these (the generated table, if any, takes precedence). */
const PortOverlay port_overlays_none[1] = {{0}};
const int port_noverlays_none = 0;
#pragma comment(linker, "/alternatename:_port_overlays=_port_overlays_none")
#pragma comment(linker, "/alternatename:_port_noverlays=_port_noverlays_none")

static u32 g_active[MAX_GROUPS]; /* index + 1 of each group's active unit; 0: none yet */
static struct {
    u32 group, base, unit; /* a slot base's active unit (index + 1) */
} g_slots[MAX_SLOTS];
static u32 g_nslots, g_slot_next;
static u32 g_switches, g_misses;
static u32 g_delta;                     /* the delta of the unit resolved last */
static const PortOverlay *g_who;        /* ... and the unit */
u32 tx_ovl_delta;                       /* set by tc_call around a call into a unit (tx_rt.c) */

u32 overlay_delta(void) { return g_delta; }

static int in_unit(const PortOverlay *o, u32 a)
{
    for (u32 i = 0; i < o->nranges; i++)
        if (a >= o->ranges[i].lo && a < o->ranges[i].hi) return 1;
    return 0;
}

/* The unit's windows all match RAM, with the unit loaded delta further on than it was written. */
static int loaded(const PortOverlay *o, u32 delta)
{
    for (u32 i = 0; i < o->nwindows; i++) {
        const PortOverlayWindow *w = &o->windows[i];
        u32 at = w->addr + delta;
        if (!guest_is_ram(at) || memcmp((const void *)(uintptr_t)at, w->bytes, OVERLAY_WINDOW)) return 0;
    }
    return o->nwindows != 0;
}

/* Whether a unit answers for the address at all: its ranges, or (a slot unit) its slots */
static int claims(const PortOverlay *o, u32 a)
{
    return o->slot_align ? a >= o->slot_lo && a < o->slot_hi : in_unit(o, a);
}

static const PortDispatch *search(const PortDispatch *rows, u32 n, u32 a)
{
    s32 lo = 0, hi = (s32)n - 1;
    while (lo <= hi) {
        s32 mid = (lo + hi) / 2;
        if (rows[mid].addr == a) return &rows[mid];
        if (rows[mid].addr < a) lo = mid + 1;
        else hi = mid - 1;
    }
    return NULL;
}

static const PortDispatch *row_at(const PortOverlay *o, u32 a) { return search(o->rows, o->nrows, a); }

/* The function at a of the unit loaded now that has one there: group g's active unit if so, else the one that is
 * (it becomes active). Only units with a function at a compete: one loaded over another's data leaves its code. */
static const PortDispatch *loaded_row(u32 g, u32 a)
{
    u32 cur = g < MAX_GROUPS ? g_active[g] : 0;
    const PortOverlay *o = cur ? &port_overlays[cur - 1u] : NULL;
    const PortDispatch *d = o && in_unit(o, a) ? row_at(o, a) : NULL;
    g_delta = 0;
    if (d && loaded(o, 0)) {
        g_who = o;
        return d;
    }
    for (int i = 0; i < port_noverlays; i++) {
        const PortOverlay *u = &port_overlays[i];
        if (u->group != g || u->slot_align || (u32)i + 1u == cur || !in_unit(u, a) || (d = row_at(u, a)) == NULL ||
            !loaded(u, 0))
            continue;
        if (g < MAX_GROUPS) g_active[g] = (u32)i + 1u;
        g_switches++;
        port_log("overlay: %s active (call to %08x)\n", u->id, a);
        g_who = u;
        return d;
    }
    return NULL;
}

/* The same for a slot unit's call at a: the unit loaded at a's slot base (units are tried at that base only). */
static const PortDispatch *slot_row(const PortOverlay *claim, u32 a)
{
    u32 g = claim->group, base = a & ~(claim->slot_align - 1u), cur = 0, at = MAX_SLOTS;
    const PortDispatch *d;
    for (u32 i = 0; i < g_nslots; i++)
        if (g_slots[i].group == g && g_slots[i].base == base) cur = g_slots[i].unit, at = i;
    if (cur) {
        const PortOverlay *o = &port_overlays[cur - 1u];
        u32 delta = base - o->canon, c = a - delta;
        if (in_unit(o, c) && (d = row_at(o, c)) != NULL && loaded(o, delta)) {
            g_delta = delta, g_who = o;
            return d;
        }
    }
    for (int i = 0; i < port_noverlays; i++) {
        const PortOverlay *u = &port_overlays[i];
        u32 delta = base - u->canon, c = a - delta;
        if (u->group != g || !u->slot_align || (u32)i + 1u == cur || !in_unit(u, c) || (d = row_at(u, c)) == NULL ||
            !loaded(u, delta))
            continue;
        if (at == MAX_SLOTS) {
            at = g_nslots < MAX_SLOTS ? g_nslots++ : g_slot_next++ % MAX_SLOTS;
            g_slots[at].group = g, g_slots[at].base = base;
        }
        g_slots[at].unit = (u32)i + 1u;
        g_switches++;
        port_log("overlay: %s active at %08x (call to %08x)\n", u->id, base, a);
        g_delta = delta, g_who = u;
        return d;
    }
    return NULL;
}

static const PortDispatch *resolve(const PortOverlay *o, u32 a)
{
    g_who = NULL;
    return o->slot_align ? slot_row(o, a) : loaded_row(o->group, a);
}

static u32 __cdecl no_function(void) { return 0; }

/* A call into overlay RAM that no loaded unit has a function for: logged loudly once per group, a checked build
 * stops; it never falls through to main's table or another unit. */
static PortDispatch g_miss = {0, (PortFn)no_function, "(no loaded overlay)"};
static u8 g_miss_logged[MAX_GROUPS];

static const PortDispatch *miss(const PortOverlay *o, u32 a)
{
    g_misses++;
    g_delta = 0;
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
        if (!claims(&port_overlays[i], a)) continue;
        const PortDispatch *d = resolve(&port_overlays[i], a); /* units covering one address share a group */
        return d && d->fn ? d : miss(&port_overlays[i], a);
    }
    return NULL;
}

const PortDispatch *overlay_glue(u32 addr)
{
    u32 a = guest_fold(addr);
    for (int i = 0; i < port_noverlays; i++) {
        const PortOverlay *o = &port_overlays[i];
        if (!claims(o, a)) continue;
        if (o->group >= MAX_GROUPS || !resolve(o, a) || !g_who) return NULL;
        return search(g_who->glue, g_who->nglue, a - g_delta);
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
