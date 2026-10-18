/* Platform layer: the audio heap (amHeapAlloc, amHeapFree) in the sound RAM. The sound RAM is the AICA's 2 MB at
 * 0xa0800000 (its uncached SH-4 address), backed by host memory at that address (runtime/aica.c), where the kit's
 * voices read the samples the game copies in; the sound driver's 64 KB at its start is left out of the heap. Both
 * calls return TRUE (1) or FALSE (0). Needs SdkProc (a callback pointer type) from the game's headers. */
#include "game.h"
#include "platform.h"
#include "aica.h"

#define SOUND_DRIVER_SIZE 0x00010000u

static Heap g_am_heap;
static int g_am_ready;

static int am_heap_ready(void)
{
    if (!g_am_ready) {
        if (!aica_ram()) return 0;
        heap_init(&g_am_heap, "sound RAM", (void *)(uintptr_t)(AICA_RAM_BASE + SOUND_DRIVER_SIZE),
                  AICA_RAM_SIZE - SOUND_DRIVER_SIZE);
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
