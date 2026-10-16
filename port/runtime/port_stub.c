/* Logging stubs: the first call of each stubbed function is logged in order; every call is counted. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "port.h"
#include "port_stub.h"

#define SLOTS 8192u /* power of two; the table fills to at most SLOTS - 1 distinct stubs */

typedef struct {
    u32 addr;
    const char *name;
    u32 calls;
    u32 order;
} Hit;

static Hit g_hits[SLOTS];
static u32 g_distinct;
static u32 g_total;
static u32 g_last;

#define MUST_MAX 64
static char g_must[MUST_MAX][64];
static int g_must_n;

int port_stub_must_work(const char *path)
{
    FILE *f = NULL;
    char line[256];
    if (fopen_s(&f, path, "r") != 0 || !f) return 0;
    while (g_must_n < MUST_MAX && fgets(line, sizeof line, f)) {
        size_t n = strcspn(line, "# \t\r\n");
        if (n == 0 || n >= sizeof g_must[0]) continue;
        memcpy(g_must[g_must_n], line, n);
        g_must[g_must_n++][n] = '\0';
    }
    fclose(f);
    port_log("must-work list: %d function(s) from %s\n", g_must_n, path);
    return 1;
}

static void check_must_work(u32 addr, const char *name)
{
    char why[160];
    for (int i = 0; i < g_must_n; i++)
        if (!strcmp(g_must[i], name)) {
            snprintf(why, sizeof why, "first must-work stub reached: %08x %s", addr, name);
            port_exit(why, 3);
        }
}

void port_stub(u32 addr, const char *name)
{
    u32 i = (addr * 2654435761u) & (SLOTS - 1);
    while (g_hits[i].addr && g_hits[i].addr != addr) i = (i + 1) & (SLOTS - 1);
    Hit *h = &g_hits[i];
    g_total++;
    g_last = addr;
    if (!h->addr) {
        if (g_distinct >= SLOTS - 1u) { /* full but for the empty slot that ends every probe: counted only */
            check_must_work(addr, name);
            return;
        }
        h->addr = addr;
        h->name = name;
        h->order = ++g_distinct;
        port_log("STUB #%u %08x %s\n", h->order, addr, name);
        h->calls++;
        check_must_work(addr, name);
        return;
    }
    h->calls++;
}

static int by_calls(const void *a, const void *b)
{
    const Hit *x = (const Hit *)a, *y = (const Hit *)b;
    return x->calls < y->calls ? 1 : x->calls > y->calls ? -1 : 0;
}

void port_stub_hot(int top)
{
    Hit *all = (Hit *)malloc(sizeof(Hit) * (g_distinct ? g_distinct : 1));
    u32 n = 0;
    if (!all) return;
    for (u32 i = 0; i < SLOTS; i++)
        if (g_hits[i].addr) all[n++] = g_hits[i];
    qsort(all, n, sizeof(Hit), by_calls);
    for (u32 i = 0; i < n && (int)i < top; i++)
        port_log("  %10u calls  %08x %s (first #%u)\n", all[i].calls, all[i].addr, all[i].name, all[i].order);
    free(all);
}

void port_stub_report(void)
{
    port_log("STUBS: %u distinct stubbed functions called, %u calls; last stub called: %08x\n", g_distinct, g_total,
             g_last);
    port_stub_hot(15);
}
