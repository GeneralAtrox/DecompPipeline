/* Platform layer: the audio heap (amHeapAlloc, amHeapFree) in the sound RAM. The sound RAM is the AICA's 2 MB at
 * 0xa0800000 (its uncached SH-4 address), backed here by host memory at that address, so the game's copies into it
 * land somewhere; the sound driver's 64 KB at its start is left out of the heap. A mixer will read the samples from
 * here. Both calls return TRUE (1) or FALSE (0). Needs SdkProc (a callback pointer type) from the game's headers. */
#include "game.h"
#include "platform.h"

#define SOUND_RAM_BASE 0xa0800000u
#define SOUND_RAM_SIZE 0x00200000u
#define SOUND_DRIVER_SIZE 0x00010000u

static Heap g_am_heap;
static int g_am_ready;

static int am_heap_ready(void)
{
    if (!g_am_ready) {
        if (!host_map_fixed(SOUND_RAM_BASE, SOUND_RAM_SIZE)) return 0;
        heap_init(&g_am_heap, "sound RAM", (void *)(uintptr_t)(SOUND_RAM_BASE + SOUND_DRIVER_SIZE),
                  SOUND_RAM_SIZE - SOUND_DRIVER_SIZE);
        g_am_ready = 1;
    }
    return 1;
}

s32 amHeapAlloc(void **buffer, u32 size, u32 alignment, u32 memoryType, SdkProc purgeCallback)
{
    (void)memoryType, (void)purgeCallback;
    if (!buffer || alignment > HEAP_ALIGN || !am_heap_ready()) return 0;
    *buffer = heap_alloc(&g_am_heap, size);
    return *buffer != NULL;
}

s32 amHeapFree(void *buffer) { return g_am_ready && heap_free(&g_am_heap, buffer); }
