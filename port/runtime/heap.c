/* Platform layer: a first-fit allocator over one region, used by the Shinobi heap (sdk/sy_heap.c) and the audio heap
 * (sdk/am_heap.c). Every block is 32-byte aligned. Blocks are kept in address order (size and previous block in a
 * 32-byte header just below the block), and a freed block merges with free neighbours. */
#include <stddef.h>
#include "platform.h"

#define HEAP_MAGIC 0x48454150u /* "HEAP" */

typedef struct HeapBlock_ {
    u32 size;                  /* bytes, header included; a multiple of HEAP_ALIGN */
    u32 used;
    struct HeapBlock_ *prev;   /* the block just below, NULL for the first */
    u32 magic;
    u32 pad[4];
} HeapBlock_;

static HeapBlock_ *next_block(const Heap *h, HeapBlock_ *b)
{
    u8 *n = (u8 *)b + b->size;
    return n < (u8 *)h->end ? (HeapBlock_ *)n : NULL;
}

void heap_init(Heap *h, const char *name, void *region, u32 size)
{
    u32 base = ((u32)(uintptr_t)region + HEAP_ALIGN - 1u) & ~(HEAP_ALIGN - 1u);
    u32 end = ((u32)(uintptr_t)region + size) & ~(HEAP_ALIGN - 1u);
    HeapBlock_ *first = (HeapBlock_ *)(uintptr_t)base;
    first->size = end - base;
    first->used = 0;
    first->prev = NULL;
    first->magic = HEAP_MAGIC;
    h->name = name;
    h->first = first;
    h->end = (void *)(uintptr_t)end;
    h->used = h->peak = 0;
    h->failed_logged = 0;
    port_log("%s: %08x..%08x (%u KB)\n", name, base, end, (end - base) >> 10);
}

void heap_finish(Heap *h)
{
    port_log("%s: finish, %u KB in use, peak %u KB\n", h->name, h->used >> 10, h->peak >> 10);
    h->first = NULL;
}

void *heap_alloc(Heap *h, u32 size)
{
    u32 need = (size + sizeof(HeapBlock_) + HEAP_ALIGN - 1u) & ~(HEAP_ALIGN - 1u);
    for (HeapBlock_ *b = (HeapBlock_ *)h->first; b; b = next_block(h, b)) {
        if (b->used || b->size < need) continue;
        if (b->size - need >= 2u * HEAP_ALIGN) { /* split: the rest stays a free block */
            HeapBlock_ *rest = (HeapBlock_ *)((u8 *)b + need);
            HeapBlock_ *after;
            rest->size = b->size - need;
            rest->used = 0;
            rest->prev = b;
            rest->magic = HEAP_MAGIC;
            after = next_block(h, rest);
            if (after) after->prev = rest;
            b->size = need;
        }
        b->used = 1;
        h->used += b->size;
        if (h->used > h->peak) h->peak = h->used;
        return b + 1;
    }
    if (!h->failed_logged++) port_log("%s: alloc(%u) failed, %u KB in use\n", h->name, size, h->used >> 10);
    return NULL;
}

int heap_free(Heap *h, void *ptr)
{
    HeapBlock_ *b, *n;
    if (!ptr) return 0;
    b = (HeapBlock_ *)ptr - 1;
    if (!h->first || b->magic != HEAP_MAGIC || !b->used) {
        port_log("%s: free(%08x) is not a live block\n", h->name, (u32)(uintptr_t)ptr);
        return 0;
    }
    b->used = 0;
    h->used -= b->size;
    n = next_block(h, b);
    if (n && !n->used) { /* merge the free block above */
        b->size += n->size;
        n->magic = 0;
        n = next_block(h, b);
        if (n) n->prev = b;
    }
    if (b->prev && !b->prev->used) { /* merge into the free block below */
        HeapBlock_ *p = b->prev;
        p->size += b->size;
        b->magic = 0;
        n = next_block(h, p);
        if (n) n->prev = p;
    }
    return 1;
}
