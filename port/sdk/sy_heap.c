/* Platform layer: the Shinobi heap (syMallocInit, syMalloc, syFree) over guest RAM (runtime/heap.c). Whatever
 * calls syMallocInit (the game's start-up, or the game's own replacement of it) chooses the region, usually from the
 * end of the boot file's BSS to the end of main RAM. Needs only those SDK names in the game's registry.
 * Shinobi's syMalloc_ object also defines the C library's malloc and free on the same allocator: a registry naming
 * them gets sy_guest_malloc / sy_guest_free (gen_port.py GUEST_CRT maps the names in game.h). */
#include "game.h"
#include "platform.h"

static Heap g_sy_heap;

void syMallocInit(void *heap, u32 size) { heap_init(&g_sy_heap, "heap", heap, size); }

void syMallocFinish(void) { heap_finish(&g_sy_heap); }

void *syMalloc(u32 size) { return heap_alloc(&g_sy_heap, size); }

void syFree(void *ptr) { (void)heap_free(&g_sy_heap, ptr); }

void *sy_guest_malloc(u32 size) { return heap_alloc(&g_sy_heap, size); }

void sy_guest_free(void *ptr) { (void)heap_free(&g_sy_heap, ptr); }
