/* The console's actions and the player's assignment of host buttons to them (Input > Controls): base.h host_actions, host.h
 * input_bind_*. Split from input.c, which reads the devices; a button is a source here by name ("LB", "RSUP", ...), and
 * the settings keep a choice as bind-<name>, its sources joined by '+' ("a+lb").
 *
 * An assignment is applied to the pad as it is read, before it is recorded (input_binds_apply): an action's sources press
 * its default button. So the rest of the kit, the game log and a recording only ever see the default layout, and a replay
 * needs no settings. A scripted run ignores assignments. */
#include <stdio.h>
#include <string.h>
#include "base.h"
#include "console.h"
#include "host.h"

/* ---- the console's actions and the player's assignment of host buttons to them ---- */
static const char *const k_sources[] = {"none", "A",  "B",  "X",  "Y",      "START",  "UP",     "DOWN",   "LEFT",    "RIGHT",   "LB",
                                        "RB",   "LT", "RT", "L3", "R3",     "BACK",   "GUIDE",  "RSUP",   "RSDOWN",  "RSLEFT",  "RSRIGHT"};
#define N_SOURCES (sizeof k_sources / sizeof k_sources[0])
#define MAX_ACTIONS 32
static u32 g_bind[MAX_ACTIONS];  /* the sources assigned to action i (bit s: source s), when g_bound has bit i */
static u32 g_bound;

static int source_index(const char *name)
{
    for (unsigned i = 0; name && i < N_SOURCES; i++)
        if (!_stricmp(name, k_sources[i])) return (int)i;
    return -1;
}

unsigned input_source_count(void) { return (unsigned)N_SOURCES; }

const char *input_source_name(unsigned s) { return s < N_SOURCES ? k_sources[s] : "none"; }

static u32 default_mask(const PortAction *a)
{
    int s = source_index(a->dflt);
    return s > 0 ? 1u << s : 0u;
}

/* The sources action i takes (bit s: source s): the player's choice, else its default. */
u32 input_bind_get(unsigned i)
{
    unsigned n;
    const PortAction *a = console_actions(&n);
    if (i >= n || i >= MAX_ACTIONS) return 0;
    return g_bound & (1u << i) ? g_bind[i] : default_mask(&a[i]);
}

/* The menu: a source goes on or off the action; "none" clears it. */
void input_bind_toggle(unsigned i, unsigned source)
{
    u32 mask = input_bind_get(i);
    if (i >= MAX_ACTIONS || source >= N_SOURCES) return;
    g_bind[i] = source ? mask ^ (1u << source) : 0u;
    g_bound |= 1u << i;
}

/* The settings file's words for action i's sources. */
const char *input_bind_text(unsigned i, char *buf, size_t cap)
{
    u32 mask = input_bind_get(i);
    size_t at = 0;
    buf[0] = 0;
    for (unsigned s = 1; s < N_SOURCES; s++)
        if (mask >> s & 1u) at += (size_t)snprintf(buf + at, cap - at, "%s%s", at ? "+" : "", k_sources[s]);
    if (!at) snprintf(buf, cap, "none");
    return buf;
}

/* --bind-NAME SOURCE[+SOURCE...]: the settings file's key for an assignment. */
int input_bind_option(const char *arg, const char *value)
{
    unsigned n;
    const PortAction *a;
    if (strncmp(arg, "--bind-", 7) || !value) return 0;
    a = console_actions(&n);
    for (unsigned i = 0; i < n && i < MAX_ACTIONS; i++)
        if (!_stricmp(arg + 7, a[i].name)) {
            u32 mask = 0;
            for (const char *p = value; *p;) {
                char one[16];
                size_t len = strcspn(p, "+");
                int s = -1;
                if (len < sizeof one) {
                    memcpy(one, p, len);
                    one[len] = 0;
                    s = source_index(one);
                }
                if (s < 0) {
                    port_log("input: %s: no host button called \"%s\"\n", arg, value);
                    return 2;
                }
                if (s) mask |= 1u << s;
                p += len + (p[len] == '+');
            }
            g_bind[i] = mask;
            g_bound |= 1u << i;
            return 2;
        }
    return 0;
}

/* Is the host button `source` held in this pad? Directions also come from the left stick. */
static int source_held(const HostPad *p, int source)
{
    const int on = 64; /* of 128 */
    const char *name = input_source_name((unsigned)source);
    if (source <= 0) return 0;
    if (!strcmp(name, "LT")) return p->l >= 128;
    if (!strcmp(name, "RT")) return p->r >= 128;
    if (!strcmp(name, "UP")) return (p->buttons & HOST_PAD_UP) != 0 || p->y < -on;
    if (!strcmp(name, "DOWN")) return (p->buttons & HOST_PAD_DOWN) != 0 || p->y > on;
    if (!strcmp(name, "LEFT")) return (p->buttons & HOST_PAD_LEFT) != 0 || p->x < -on;
    if (!strcmp(name, "RIGHT")) return (p->buttons & HOST_PAD_RIGHT) != 0 || p->x > on;
    return (p->buttons & input_name_bit(name)) != 0;
}

/* The default button `name` let go, or pressed, in this pad. */
static void press(HostPad *p, const char *name, int down)
{
    if (!_stricmp(name, "LT")) {
        if (!down) p->l = 0;
        else if (p->l < 128) p->l = 255;
    } else if (!_stricmp(name, "RT")) {
        if (!down) p->r = 0;
        else if (p->r < 128) p->r = 255;
    } else {
        u32 bit = input_name_bit(name);
        if (down) {
            p->buttons |= bit;
        } else {
            p->buttons &= ~bit;
            if (bit == HOST_PAD_UP && p->y < 0) p->y = 0;
            if (bit == HOST_PAD_DOWN && p->y > 0) p->y = 0;
            if (bit == HOST_PAD_LEFT && p->x < 0) p->x = 0;
            if (bit == HOST_PAD_RIGHT && p->x > 0) p->x = 0;
        }
    }
}

/* From host_pads_read, before the recording: nothing to do (and no cost) until the player assigns a button. Then
 * twin-stick (pad_twin.c), on the buttons as assigned. */
void input_binds_apply(HostPad pad[HOST_PADS])
{
    unsigned n;
    const PortAction *a = console_actions(&n);
    for (u32 p = 0; g_bound && !input_scripted() && p < HOST_PADS; p++) {
        const HostPad raw = pad[p];
        for (unsigned i = 0; i < n && i < MAX_ACTIONS; i++)
            if (g_bound >> i & 1u && !(g_bind[i] & default_mask(&a[i]))) press(&pad[p], a[i].dflt, 0);
        for (unsigned i = 0; i < n && i < MAX_ACTIONS; i++) {
            if (!(g_bound >> i & 1u)) continue;
            for (unsigned s = 1; s < N_SOURCES; s++)
                if (g_bind[i] >> s & 1u && source_held(&raw, (int)s)) press(&pad[p], a[i].dflt, 1);
        }
    }
    port_twin_apply(pad);
}

/* Which of the console's actions this pad holds: each action's default button (an assignment has already pressed it). */
u32 host_actions(const HostPad *pad)
{
    unsigned n;
    const PortAction *a = console_actions(&n);
    u32 held = 0;
    for (unsigned i = 0; i < n && i < MAX_ACTIONS; i++)
        if (source_held(pad, source_index(a[i].dflt))) held |= 1u << i;
    return held;
}
