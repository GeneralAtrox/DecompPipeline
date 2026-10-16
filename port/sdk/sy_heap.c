/* Platform layer: the Shinobi heap (syMallocInit, syMalloc, syFree) over guest RAM (runtime/heap.c). Whatever
 * calls syMallocInit (the game's start-up, or the game's own replacement of it) chooses the region, usually from the
 * end of the boot file's BSS to the end of main RAM. Needs only those SDK names in the game's registry. */
#include "game.h"
#include "platform.h"

static Heap g_sy_heap;

void syMallocInit(void *heap, u32 size) { heap_init(&g_sy_heap, "heap", heap, size); }

void syMallocFinish(void) { heap_finish(&g_sy_heap); }

void *syMalloc(u32 size) { return heap_alloc(&g_sy_heap, size); }

void syFree(void *ptr) { (void)heap_free(&g_sy_heap, ptr); }
