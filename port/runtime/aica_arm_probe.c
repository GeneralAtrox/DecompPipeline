/* The AICA ARM7's measures and proof tools (aica_arm_probe.h). Off unless an environment variable asks, so the play
 * path runs none of it:
 *  - DP_ARM_TRACE=FILE: one CSV row per vertical blank with the samples run, the instructions executed, the host time
 *    they took, the SH-4's polls of the driver and the three hottest 16-byte blocks of the driver's code (sampled
 *    once per chip sample).
 *  - DP_ARM_LOG=FILE: the proof log. Every write the ARM makes beyond the sound RAM, with the sample and instruction
 *    count it happened at and the value, and at each vertical blank a digest of the ARM's whole state and the sound
 *    RAM. Two runs of one replay (the interpreter alone; the fast paths; the native blocks) must write the same file.
 *  - DP_ARM_PCS=BASE: the recorder. Which words of the sound RAM the ARM executed, and the RAM at the last dump, for
 *    tools/arm7_translate.py: BASE.pcs holds one byte per word of the RAM window, BASE.ram the 2 MB sound RAM. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include "aica.h"
#include "aica_arm_probe.h"

#define PROBE_WINDOW 0x00800000u /* the sound RAM as the ARM addresses it (aica_arm.c's ARM_RAM_WINDOW) */
#define TRACE_BLOCKS 4096u

typedef struct {
    u64 sample, executed;
    u32 addr, value, kind, pad; /* kind 1: ARM write (size in pad), 2: digest (addr, value: the hash) */
} LogRec;

static const Arm7 *g_a;
static FILE *g_log, *g_trace;
static u32 g_log_blank;
static u8 *g_pcs;
static char g_pcs_base[480];
static u32 g_tr_blank, g_tr_runs, g_tr_samples, g_tr_polls, g_tr_waits, g_pcs_dump;
static u64 g_tr_ns, g_tr_instr, g_tr_fiq0, g_tr_bus0, g_tr_scan0, g_tr_nb0, g_tr_ni0;
static u32 g_tr_pc[TRACE_BLOCKS];

u64 arm7_probe_now_ns(void)
{
    struct timespec t;
    timespec_get(&t, TIME_UTC);
    return (u64)t.tv_sec * 1000000000u + (u64)t.tv_nsec;
}

static u64 mix64(u64 h, u64 v)
{
    h ^= v + 0x9e3779b97f4a7c15ull + (h << 6) + (h >> 2);
    h *= 0xff51afd7ed558ccdull;
    return h ^ (h >> 33);
}

static void log_rec(u64 samples, u32 kind, u32 addr, u32 value, u32 size)
{
    LogRec r = {samples, g_a->executed, addr, value, kind, size};
    fwrite(&r, sizeof r, 1, g_log);
}

static void log_blank(u64 samples)
{
    u32 b = port_vblank_count();
    if (!g_log || b == g_log_blank) return;
    g_log_blank = b;
    const Arm7 *a = g_a;
    u64 h = 0x1234567ull;
    for (u32 i = 0; i < 15; i++) h = mix64(h, a->r[i]);
    h = mix64(h, a->next), h = mix64(h, a->n | a->z << 1 | a->c << 2 | a->v << 3), h = mix64(h, a->psr);
    h = mix64(h, a->spsr), h = mix64(h, (u64)a->cycles), h = mix64(h, a->executed), h = mix64(h, a->fiq);
    for (u32 i = 0; i < 5; i++) h = mix64(mix64(h, a->usr_hi[i]), a->fiq_hi[i]);
    for (u32 i = 0; i < 6; i++) h = mix64(mix64(mix64(h, a->bank[i][0]), a->bank[i][1]), a->bank[i][2]);
    const u64 *ram = (const u64 *)a->ram;
    for (u32 i = 0; i < AICA_RAM_SIZE / 8u; i++) h = mix64(h, ram[i]);
    log_rec(samples, 2, (u32)h, (u32)(h >> 32), b);
}

static void pcs_dump(void)
{
    char path[512];
    FILE *f = NULL;
    if (!g_pcs || !g_a->ram) return;
    snprintf(path, sizeof path, "%s.pcs", g_pcs_base);
    if (!fopen_s(&f, path, "wb") && f) fwrite(g_pcs, 1, PROBE_WINDOW / 4u, f), fclose(f);
    snprintf(path, sizeof path, "%s.ram", g_pcs_base);
    if (!fopen_s(&f, path, "wb") && f) fwrite(g_a->ram, 1, AICA_RAM_SIZE, f), fclose(f);
}

static void trace_flush(void)
{
    u32 top[3] = {0, 0, 0}, best[3] = {0, 0, 0};
    if (!g_trace) return;
    for (u32 i = 0; i < TRACE_BLOCKS; i++)
        for (u32 k = 0; k < 3; k++)
            if (g_tr_pc[i] > best[k]) {
                for (u32 j = 2; j > k; j--) best[j] = best[j - 1], top[j] = top[j - 1];
                best[k] = g_tr_pc[i], top[k] = i;
                break;
            }
    fprintf(g_trace, "%u,%u,%u,%llu,%llu,%u,%u,%x:%u,%x:%u,%x:%u,%llu,%llu,%llu,%llu,%llu\n", g_tr_blank, g_tr_runs, g_tr_samples,
            (unsigned long long)g_tr_instr, (unsigned long long)g_tr_ns, g_tr_polls, g_tr_waits, top[0] << 4, best[0],
            top[1] << 4, best[1], top[2] << 4, best[2], (unsigned long long)(g_a->fiqs - g_tr_fiq0),
            (unsigned long long)(g_a->bus - g_tr_bus0), (unsigned long long)(g_a->scanned - g_tr_scan0),
            (unsigned long long)(g_a->native_blocks - g_tr_nb0), (unsigned long long)(g_a->native_instr - g_tr_ni0));
    g_tr_fiq0 = g_a->fiqs, g_tr_bus0 = g_a->bus, g_tr_scan0 = g_a->scanned;
    g_tr_nb0 = g_a->native_blocks, g_tr_ni0 = g_a->native_instr;
    g_tr_runs = g_tr_samples = g_tr_polls = g_tr_waits = 0;
    g_tr_ns = g_tr_instr = 0;
}

static void trace_blank(void)
{
    u32 b = port_vblank_count();
    if (g_trace && b != g_tr_blank) {
        trace_flush();
        g_tr_blank = b;
    }
    if (g_pcs && b % 250u == 0 && b != g_pcs_dump) {
        g_pcs_dump = b;
        pcs_dump();
    }
}

static int env_text(const char *name, char *out, size_t size)
{
    size_t n = 0;
    return getenv_s(&n, out, size, name) == 0 && n > 1;
}

void arm7_probe_start(Arm7 *a)
{
    char v[480];
    g_a = a;
    if (!g_log && env_text("DP_ARM_LOG", v, sizeof v)) fopen_s(&g_log, v, "wb");
    if (!g_pcs && env_text("DP_ARM_PCS", v, sizeof v) && (g_pcs = (u8 *)calloc(PROBE_WINDOW / 4u, 1)) != NULL) {
        memcpy(g_pcs_base, v, strlen(v) + 1u);
        atexit(pcs_dump);
    }
    a->pcs = g_pcs;
    if (!g_trace && env_text("DP_ARM_TRACE", v, sizeof v)) {
        fopen_s(&g_trace, v, "w");
        if (g_trace) {
            fprintf(g_trace, "blank,runs,samples,instr,ns,polls,waits,hot1,hot2,hot3,fiqs,bus,scanned,nblocks,ninstr\n");
            atexit(trace_flush);
        }
    }
}

int arm7_probe_recording(void) { return g_pcs != NULL; }

void arm7_probe_sample(const Arm7 *a)
{
    if (g_trace) g_tr_pc[(a->next >> 4) & (TRACE_BLOCKS - 1u)]++;
}

void arm7_probe_blank(u64 samples)
{
    log_blank(samples);
    trace_blank();
}

u64 arm7_probe_run_begin(void) { return g_trace ? arm7_probe_now_ns() : 0; }

void arm7_probe_run_end(u64 begin, u64 executed0, u32 n, const Arm7 *a)
{
    if (!g_trace) return;
    g_tr_ns += arm7_probe_now_ns() - begin;
    g_tr_instr += a->executed - executed0;
    g_tr_samples += n;
    g_tr_runs++;
}

void arm7_probe_poll(void)
{
    trace_blank();
    g_tr_polls++;
}

void arm7_probe_wait(void) { g_tr_waits++; }

void arm7_probe_write(u64 samples, u32 addr, u32 value, u32 size)
{
    if (g_log) log_rec(samples, 1, addr, value, size);
}
