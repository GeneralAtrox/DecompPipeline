/* Reports on where the game is: the --profile sampler and the call chain a halt, a stub or the watchdog prints. */
#include "sat_cpu.h"
#include "sat_sys.h"

/* --profile: each vertical blank samples what the master was running (its innermost function and last read); the
 * counts print at exit. A stuck wait shows as one signature. */
static struct { u32 fn, rd, n; } g_prof[64];
static int g_nprof, g_profile;
void sat_set_profile(int on) { g_profile = on; }
void sat_sample(void)
{
    const SatCpu *c = &g_cpu[MASTER];
    u32 fn, rd;
    if (!g_profile || c->depth <= 0) return;
    fn = c->stack[c->depth - 1];
    rd = sat_rd_ring[(sat_rd_n - 1u) & 7u];
    for (int i = 0; i < g_nprof; i++)
        if (g_prof[i].fn == fn && g_prof[i].rd == rd) {
            g_prof[i].n++;
            return;
        }
    if (g_nprof < 64) {
        g_prof[g_nprof].fn = fn;
        g_prof[g_nprof].rd = rd;
        g_prof[g_nprof++].n = 1;
    }
}

void sat_profile_report(void)
{
    for (int i = 0; i < g_nprof; i++) port_log("profile: %5u x in %08x, last read %08x\n", g_prof[i].n, g_prof[i].fn, g_prof[i].rd);
}

/* where the game is: each CPU's running call chain and last calls (the watchdog's, a halt's and a stub's report) */
void sat_where(void)
{
    port_log("where: last reads %08x %08x %08x %08x %08x %08x %08x %08x\n", sat_rd_ring[(sat_rd_n + 0) & 7], sat_rd_ring[(sat_rd_n + 1) & 7], sat_rd_ring[(sat_rd_n + 2) & 7], sat_rd_ring[(sat_rd_n + 3) & 7], sat_rd_ring[(sat_rd_n + 4) & 7], sat_rd_ring[(sat_rd_n + 5) & 7], sat_rd_ring[(sat_rd_n + 6) & 7], sat_rd_ring[(sat_rd_n + 7) & 7]);
    for (int k = 0; k < 2; k++) {
        const SatCpu *c = &g_cpu[k];
        if (k == SLAVE && !c->alive && !c->nlast) continue;
        int depth = c->depth;
        u32 nlast = c->nlast;
        {
            u32 ims, ist;
            sat_scu_state(&ims, &ist);
            if (k == MASTER) port_log("where: master sr %03x, scu ims %08x ist %08x, vec40 %08x\n", c->sr, ims, ist, sat_peek32(0x06000100u));
        }
        if (k == MASTER) {
            port_log("where: master mask changes (fn+level), newest last:");
            u32 ns = c->nsr; /* the game may still run (the watchdog's report): read the count once */
            for (u32 i = ns > 8 ? ns - 8 : 0; i < ns; i++) port_log(" %08x>%u", c->srlog[i & 7u], c->srlvl[i & 7u]);
            port_log("\n");
        }
        if (k == MASTER && c->gbr)
            for (u32 a = c->gbr; a < c->gbr + 0xc0u; a += 16u)
                port_log("mem %08x: %08x %08x %08x %08x\n", a, sat_peek32(a), sat_peek32(a + 4u), sat_peek32(a + 8u), sat_peek32(a + 12u));
        port_log("where: %s depth %d, chain:", k ? "slave" : "master", depth);
        for (int i = 0; i < depth && i < 512; i++) port_log(" %08x", c->stack[i]);
        port_log("\nwhere: %s last calls:", k ? "slave" : "master");
        for (u32 i = nlast > 24 ? nlast - 24 : 0; i < nlast; i++) port_log(" %08x", c->last[i & 63u]);
        port_log("\n");
    }
}

/* --watch-saved: a callee must hand r8..r14 back as it found them; one that does not (its saved registers read back as
 * zero, say, because another stack wrote over its frame) is logged once, first 60 only. */
void sat_saved_note(u32 addr, const u32 keep[7])
{
    static u32 seen[60], n;
    u32 bad = 0;
    for (int k = 0; k < 7; k++)
        if (keep[k] != sh4_r(8 + k)) bad |= 1u << k;
    if (!bad || n >= 60) return;
    for (u32 i = 0; i < n; i++)
        if (seen[i] == addr) return;
    seen[n++] = addr;
    port_log("saved: %08x came back with r8..r14 changed (mask %02x): r8 %08x -> %08x, r9 %08x -> %08x\n", addr, bad, keep[0], sh4_r(8), keep[1], sh4_r(9));
}
