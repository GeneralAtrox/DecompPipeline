/* RAM frames: main RAM as each game frame starts (lockstep_timer), written as the 32-byte blocks that changed since
 * the frame before (the first frame in full), so the lockstep compare can name every word that differs from the
 * emulator's reference at a frame (tools/reference.py, tools/ram_delta.py, which compresses the file after the run).
 *
 *   --ram-frames FILE        writes the frames
 *   --ram-frames-range A:B   only port frames A to B (the first of them in full)
 *
 * A record, little-endian: u32 'RAMF', u32 port frame, u32 label (the frame label the game holds), s32 emulator
 * frame (-1: not driven; the run ends with the emulator's frames), u32 run count, then each run's u32 start and
 * u32 length (bytes from the RAM's start), then the runs' bytes. The file is written through a 64 MB buffer. */
#include <stdlib.h>
#include <string.h>
#include "port.h"
#include "lockstep.h"

#define RAMF_MAGIC 0x464d4152u
#define BLOCK 32u
#define BLOCKS (GUEST_RAM_SIZE / BLOCK)
#define FILE_BUFFER (64u << 20)

static FILE *g_f;
static u8 *g_prev;
static u32 *g_runs; /* start, length pairs */
static u32 g_from, g_to = 0xffffffffu, g_frames;
static u64 g_bytes;

int ram_frames_option(const char *arg, const char *value)
{
    if (!strcmp(arg, "--ram-frames")) {
        if (fopen_s(&g_f, value, "wb") || !g_f) {
            port_log("ram frames: cannot write %s\n", value);
            exit(2);
        }
        setvbuf(g_f, NULL, _IOFBF, FILE_BUFFER);
        g_prev = malloc(GUEST_RAM_SIZE);
        g_runs = malloc(BLOCKS * sizeof *g_runs); /* at most BLOCKS / 2 runs: changed runs are apart */
        if (!g_prev || !g_runs) exit(2);
        return 1;
    }
    if (!strcmp(arg, "--ram-frames-range")) {
        if (sscanf_s(value, "%u:%u", &g_from, &g_to) != 2) exit(2);
        return 1;
    }
    return 0;
}

static int block_differs(const u32 *a, const u32 *b)
{
    u32 d = 0;
    for (u32 i = 0; i < BLOCK / 4u; i++) d |= a[i] ^ b[i];
    return d != 0;
}

void ram_frames_capture(u32 frame, u32 label, s32 emu)
{
    const u8 *ram = (const u8 *)(uintptr_t)GUEST_RAM_BASE;
    u32 n = 0, head[5];
    if (!g_f || frame < g_from || frame > g_to) return;
    if (emu == -2) port_exit("ram frames: the emulator frames ended", 0); /* nothing past them to compare */
    if (!g_frames++) {
        g_runs[0] = 0;
        g_runs[1] = GUEST_RAM_SIZE;
        n = 1;
    } else {
        for (u32 b = 0; b < BLOCKS; b++) {
            if (!block_differs((const u32 *)(ram + b * BLOCK), (const u32 *)(g_prev + b * BLOCK))) continue;
            if (n && g_runs[2u * n - 2u] + g_runs[2u * n - 1u] == b * BLOCK) {
                g_runs[2u * n - 1u] += BLOCK;
            } else {
                g_runs[2u * n] = b * BLOCK;
                g_runs[2u * n + 1u] = BLOCK;
                n++;
            }
        }
    }
    head[0] = RAMF_MAGIC;
    head[1] = frame;
    head[2] = label;
    head[3] = (u32)emu;
    head[4] = n;
    fwrite(head, sizeof head, 1, g_f);
    fwrite(g_runs, 2u * sizeof *g_runs, n, g_f);
    for (u32 i = 0; i < n; i++) {
        fwrite(ram + g_runs[2u * i], 1, g_runs[2u * i + 1u], g_f);
        memcpy(g_prev + g_runs[2u * i], ram + g_runs[2u * i], g_runs[2u * i + 1u]);
        g_bytes += g_runs[2u * i + 1u];
    }
}

void ram_frames_close(void)
{
    if (!g_f) return;
    fclose(g_f);
    g_f = NULL;
    port_log("ram frames: %u frames, %llu changed bytes\n", g_frames, (unsigned long long)g_bytes);
}
