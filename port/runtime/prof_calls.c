/* The profiler's count of tc_call by target (prof.c's --prof): which translated functions the game calls, and how often. */
#include <stdlib.h>
#include "port.h"
#include "platform.h"
#include "prof.h"

/* ---- calls by guest address (translator C's tc_call), counted by target from --prof-from on ---- */
#define CALL_SLOTS 16384u
static struct {
    u32 target, n;
} g_calls[CALL_SLOTS];
static u32 g_ncalls_total, g_calls_distinct, g_calls_on;
#define TRACE_LEN 400u /* the first calls after the counting starts, in order: the shape of a loop */
static u32 g_trace[TRACE_LEN], g_trace_n;

void prof_calls_on(int on) { g_calls_on = (u32)on; }

void prof_call(u32 target)
{
    u32 s = ((target >> 1) * 2654435761u) >> 18; /* 14 bits */
    if (!g_calls_on) return;
    if (g_trace_n < TRACE_LEN && g_ncalls_total >= 100000u) g_trace[g_trace_n++] = target;
    g_ncalls_total++;
    while (g_calls[s].n && g_calls[s].target != target) s = (s + 1u) & (CALL_SLOTS - 1u);
    if (!g_calls[s].n) {
        if (g_calls_distinct >= CALL_SLOTS - 64u) return;
        g_calls_distinct++;
        g_calls[s].target = target;
    }
    g_calls[s].n++;
}

void prof_calls_report(u32 blanks)
{
    u32 top[24] = {0}, topn[24] = {0};
    if (!g_ncalls_total) return;
    for (u32 s = 0; s < CALL_SLOTS; s++) {
        u32 n = g_calls[s].n, t = g_calls[s].target;
        if (!n) continue;
        for (u32 k = 0; k < 24u; k++)
            if (n > topn[k]) {
                for (u32 j = 23u; j > k; j--) top[j] = top[j - 1u], topn[j] = topn[j - 1u];
                top[k] = t, topn[k] = n;
                break;
            }
    }
    port_log("prof: tc_call: %u calls over the counted blanks (%.0f a blank), %u distinct targets; the most called:\n",
             g_ncalls_total, (double)g_ncalls_total / (double)(blanks ? blanks : 1u), g_calls_distinct);
    for (u32 k = 0; k < 24u && topn[k]; k++)
        port_log("prof:   %08x  %9u  %5.1f%%\n", top[k], topn[k], 100.0 * topn[k] / g_ncalls_total);
    port_log("prof: tc_call order from call 100000:");
    for (u32 k = 0; k < g_trace_n; k++) port_log("%s%08x", k % 10u ? " " : "\nprof:   ", g_trace[k]);
    port_log("\n");
}
