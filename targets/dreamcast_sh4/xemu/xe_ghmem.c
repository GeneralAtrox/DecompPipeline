/* xemu triage model "ghmem": guest memory as Ghidra's emulator gives it to the oracle.
 *
 * Ghidra's MemoryBank.getChunk copies the initialized bytes a read starts with, then hands every byte from the
 * first uninitialized one to the end of the read (within the page) to the fault handler, initialized or not.
 * TcOracle's uninitializedRead zeroes all of them and returns true, so the page keeps the zeros: a read that starts
 * in bytes no input set and runs into bytes one did reads 0 there, and so does every later read of them. The
 * runtime (and the hardware) reads the input bytes. With this model xemu does what the oracle does: a byte is
 * initialized when it is in the image, a vector's memory patch (line M) holds it or the function stored it, and a
 * read zeroes (and initializes) everything from its first uninitialized byte in each page. A disagreement this
 * model removes is that Ghidra behaviour. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "xe.h"
#include "tc_rt_internal.h"

#define PAGE 4096u /* Ghidra's emulator page */

int xe_model_ghmem;

typedef struct {
    int vec;
    u32 addr, len;
} Patch;

static Patch *g_patch;
static int g_npatch, g_cap, g_vec = -1;
static u8 *g_init; /* one bit per RAM byte */

static void mark(u32 a, u32 n)
{
    for (u32 i = 0; i < n; i++) {
        u32 o = tcrt_fold(a + i) - TCRT_RAM_BASE;
        if (o < TCRT_RAM_SIZE) g_init[o >> 3] |= (u8)(1u << (o & 7));
    }
}

static int known(u32 o) { return (g_init[o >> 3] >> (o & 7)) & 1; }

/* The M lines of the vectors the runner runs (its arguments: vectors file, set), in its order. */
static void parse(void)
{
    static char line[1 << 16];
    FILE *f = NULL;
    if (__argc < 6 || fopen_s(&f, __argv[4], "r") || !f) xe_fail("ghmem: cannot read the vectors", 0, 0);
    int vec = -1, active = 0;
    while (fgets(line, sizeof line, f)) {
        if (line[0] == 'V') {
            char set[16] = {0};
            int id;
            sscanf_s(line + 2, "%d %15s", &id, set, (unsigned)sizeof set);
            active = strcmp(__argv[5], "all") == 0 || strcmp(__argv[5], set) == 0;
            vec += active;
        } else if (active && line[0] == 'M') {
            char *p = line + 2;
            u32 a = (u32)strtoul(p, &p, 16), n = 0;
            while (*p == ' ') p++;
            while (p[n] && p[n] != '\n' && p[n] != '\r') n++;
            if (g_npatch == g_cap) {
                g_cap = g_cap ? g_cap * 2 : 1024;
                g_patch = (Patch *)realloc(g_patch, (size_t)g_cap * sizeof *g_patch);
                if (!g_patch) xe_fail("ghmem: out of memory", 0, 0);
            }
            g_patch[g_npatch++] = (Patch){vec, a, n / 2};
        }
    }
    fclose(f);
}

/* At each vector's entry (tc_thunk): the image and this vector's patches are initialized, nothing else. */
void xe_ghmem_begin(u32 image_base, u32 image_size)
{
    if (!xe_model_ghmem) return;
    if (!g_init) {
        g_init = (u8 *)malloc(TCRT_RAM_SIZE / 8);
        if (!g_init) xe_fail("ghmem: out of memory", 0, 0);
        parse();
    }
    memset(g_init, 0, TCRT_RAM_SIZE / 8);
    mark(image_base, image_size);
    g_vec++;
    for (int i = 0; i < g_npatch; i++)
        if (g_patch[i].vec == g_vec) mark(g_patch[i].addr, g_patch[i].len);
}

void xe_ghmem_write(u32 a, int size)
{
    if (xe_model_ghmem) mark(a, (u32)size);
}

void xe_ghmem_read(u32 a, int size)
{
    static const u8 zero[32] = {0};
    if (!xe_model_ghmem) return;
    u32 i = 0;
    while (i < (u32)size) {
        u32 o = tcrt_fold(a + i) - TCRT_RAM_BASE, end = (u32)size - i;
        if (o >= TCRT_RAM_SIZE) return;
        if (PAGE - (o & (PAGE - 1)) < end) end = PAGE - (o & (PAGE - 1)); /* the bytes of this read in this page */
        u32 k = 0;
        while (k < end && known(o + k)) k++;
        if (k < end) { /* the fault handler zeroes the rest of the chunk */
            tcrt_poke(TCRT_RAM_BASE + o + k, zero, end - k);
            mark(TCRT_RAM_BASE + o + k, end - k);
        }
        i += end;
    }
}
