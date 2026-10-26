/* The stricter judge, the runtimes' side (target.json "mock_junk", "mock_writes", "call_snapshots", "exit_frame"; the
 * oracle's is ghidra_scripts/TcStrict.java, its random stream TcJunk.java). The same file in every target's runtime.
 * Exactly one file of each runtime includes it (tc_rt.c; the readable runtime's rd_veh.c), after defining for its own
 * memory and registers:
 *   static int tcs_read(u32 p, u32 n, u8 *out);        the n bytes at pointer p (1), or 0 when they are not readable
 *   static void tcs_write(u32 p, u32 n, const u8 *in); store them only where the oracle does (TcStrict.writable:
 *                                                      wholly in RAM, no instruction byte), logged as the C's writes
 *   static void tcs_scratch(int i, u32 bits);          a mocked call's junk for fri, i 1..11 (the SH-4's FPU, else
 *                                                      nothing)
 * Nothing here runs without a vectors.txt header line J. The lines (the runners pass them to tcrt_strict_line):
 *   J flags                header: 1 mock_junk, 2 mock_writes, 4 call_snapshots, 8 exit_frame (kept for every vector)
 *   U callee 0 mask        header, with mock_junk: the callee may change only the fri whose bit i is set (TcStrict's
 *                          callee summary); a callee without one may change all of fr1..fr11
 *   U callee field size    header, with mock_writes: the callee writes SIZE bytes through call record field FIELD
 *   N seed vid             per vector (hex): the junk's key (TcJunk.at's seed and vid)
 *   W call field size      per vector, with call_snapshots: report the SIZE bytes at call CALL's record field
 *                          FIELD as the call is made
 * At each call (tcs_call, once the call's record is logged and before the scripted result is returned), in the
 * oracle's order: the snapshots (the W lines of this call; reported as "CS call field hex", "-" when not readable),
 * the junk (the callee's fr1..fr11 of its mask: TcJunk slots 17..27, as tcs_fr), the writes (each U line of this
 * callee with a field in file order: byte k is byte k % 8 of tcs_at(seed, vid, call, 0x10000 * (1 + field) + k / 8)).
 * The junk in r1..r7 and the GameCube's volatile registers is the oracle's alone: no runtime models those registers.
 * The exit frame
 * ("XR") is each runtime's own report (tcrt_strict_flags() & 8). tests/test_strict_judge.py checks tcs_at against
 * TcJunk.at on the same keys. */
#ifndef TC_STRICT_H
#define TC_STRICT_H

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define TCS_MAX_FACTS 256
#define TCS_MAX_REQ 64   /* TcStrict.MAX_SNAPS */
#define TCS_MAX_SIZE 256 /* a fact's size and so a snapshot's extent (core/strict_judge.MAX_SIZE) */
#define TCS_FIELDS 25    /* record fields a pointer argument can be in: DC r4..r7 1..4, s0..s7 13..20; GC 1..24 */
#define TCS_FR 0xffeu    /* fr1..fr11 of a mask */

static int tcs_read(u32 p, u32 n, u8 *out);
static void tcs_write(u32 p, u32 n, const u8 *in);
static void tcs_scratch(int i, u32 bits);

static struct {
    u32 flags, nfacts, nreq, nmasks;
    uint64_t seed, vid;
    struct { u32 target, field, size; } fact[TCS_MAX_FACTS];
    struct { u32 target, fr; } mask[TCS_MAX_FACTS];
    struct { u32 call, field, size, got; } req[TCS_MAX_REQ]; /* got: 0 not yet, 1 read, 2 not readable */
    u8 pool[TCS_MAX_REQ][TCS_MAX_SIZE];
} g_tcs;

/* splitmix64's finalizer (TcJunk.mix), chained over the key (TcJunk.at). */
static uint64_t tcs_mix(uint64_t z)
{
    z += 0x9e3779b97f4a7c15ull;
    z = (z ^ (z >> 30)) * 0xbf58476d1ce4e5b9ull;
    z = (z ^ (z >> 27)) * 0x94d049bb133111ebull;
    return z ^ (z >> 31);
}

static uint64_t tcs_at(uint64_t seed, uint64_t vid, uint64_t call, uint64_t slot)
{
    return tcs_mix(tcs_mix(tcs_mix(tcs_mix(seed) ^ vid) ^ call) ^ slot);
}

int tcrt_strict_flags(void) { return (int)g_tcs.flags; }

/* Per vector (tcrt_reset): its key and snapshot requests are forgotten; the header's flags and facts stay. */
void tcrt_strict_reset(void)
{
    g_tcs.seed = g_tcs.vid = 0;
    g_tcs.nreq = 0;
}

void tcrt_strict_line(const char *line)
{
    char *p = (char *)line + 1;
    if (line[0] == 'J') {
        g_tcs.flags = (u32)strtoul(p, NULL, 10);
    } else if (line[0] == 'U') {
        u32 t = (u32)strtoul(p, &p, 16), f = (u32)strtoul(p, &p, 10), n = (u32)strtoul(p, &p, 10);
        if (f == 0 && g_tcs.nmasks < TCS_MAX_FACTS) { /* a callee's fr mask (mock_junk) */
            g_tcs.mask[g_tcs.nmasks].target = t;
            g_tcs.mask[g_tcs.nmasks++].fr = n & TCS_FR;
            return;
        }
        if (f == 0 || f >= TCS_FIELDS || n == 0 || n > TCS_MAX_SIZE || g_tcs.nfacts >= TCS_MAX_FACTS) return;
        g_tcs.fact[g_tcs.nfacts].target = t;
        g_tcs.fact[g_tcs.nfacts].field = f;
        g_tcs.fact[g_tcs.nfacts++].size = n;
    } else if (line[0] == 'N') {
        g_tcs.seed = strtoull(p, &p, 16);
        g_tcs.vid = strtoull(p, &p, 16);
    } else if (line[0] == 'W' && g_tcs.nreq < TCS_MAX_REQ) {
        u32 c = (u32)strtoul(p, &p, 10), f = (u32)strtoul(p, &p, 10), n = (u32)strtoul(p, &p, 10);
        if (f >= TCS_FIELDS || n == 0 || n > TCS_MAX_SIZE) return;
        g_tcs.req[g_tcs.nreq].call = c;
        g_tcs.req[g_tcs.nreq].field = f;
        g_tcs.req[g_tcs.nreq].size = n;
        g_tcs.req[g_tcs.nreq++].got = 0;
    }
}

/* A float register's junk (TcJunk.fr): the sign and fraction of z's low 32 bits with 1.0's exponent, +-[1, 2). */
static u32 tcs_fr(uint64_t z) { return (u32)(z & 0x807fffffu) | 0x3f800000u; }

/* Call number `call` (from 0) to `target`, its record rec (fields in TcOracle.callRecord's order). */
static void tcs_call(u32 call, u32 target, const u32 *rec)
{
    if (!g_tcs.flags) return;
    for (u32 i = 0; (g_tcs.flags & 4u) && i < g_tcs.nreq; i++) {
        if (g_tcs.req[i].call == call)
            g_tcs.req[i].got = tcs_read(rec[g_tcs.req[i].field], g_tcs.req[i].size, g_tcs.pool[i]) ? 1u : 2u;
    }
    if (g_tcs.flags & 1u) {
        u32 m = TCS_FR;
        for (u32 i = 0; i < g_tcs.nmasks; i++)
            if (g_tcs.mask[i].target == target) m = g_tcs.mask[i].fr;
        for (int i = 1; i <= 11; i++)
            if (m >> i & 1u) tcs_scratch(i, tcs_fr(tcs_at(g_tcs.seed, g_tcs.vid, call, 16u + (u32)i)));
    }
    if (!(g_tcs.flags & 2u)) return;
    for (u32 i = 0; i < g_tcs.nfacts; i++) {
        u8 b[TCS_MAX_SIZE];
        u32 f = g_tcs.fact[i].field, n = g_tcs.fact[i].size;
        if (g_tcs.fact[i].target != target) continue;
        for (u32 k = 0; k < n; k++)
            b[k] = (u8)(tcs_at(g_tcs.seed, g_tcs.vid, call, 0x10000ull * (1u + f) + k / 8u) >> (8u * (k % 8u)));
        tcs_write(rec[f], n, b);
    }
}

/* The snapshots taken: "CS call field hex" ("-": the range was not readable). */
void tcrt_strict_report(FILE *out)
{
    for (u32 i = 0; i < g_tcs.nreq; i++) {
        if (!g_tcs.req[i].got) continue;
        fprintf(out, "CS %u %u ", g_tcs.req[i].call, g_tcs.req[i].field);
        if (g_tcs.req[i].got == 2u) fputc('-', out);
        for (u32 k = 0; g_tcs.req[i].got == 1u && k < g_tcs.req[i].size; k++) fprintf(out, "%02x", g_tcs.pool[i][k]);
        fputc('\n', out);
    }
}

#endif
