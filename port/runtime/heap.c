/* Platform layer: the allocator over one region, used by the Shinobi heap (sdk/sy_heap.c) and the audio heap
 * (sdk/am_heap.c). It lays blocks out as the console's syMalloc does, since game code reads past its blocks (a
 * sprite clip test reads the next block's first two words): every block is whole 32-byte units, its first unit a
 * header {next free block, size in units} whose other six words are never written; free blocks form a ring in
 * address order through a sentinel of size 0; an allocation takes the top of the first free block large enough,
 * searching from the one after the last block visited; a freed block merges with free neighbours. */
#include <stddef.h>
#include "platform.h"

typedef struct HeapBlock_ {
    struct HeapBlock_ *next;   /* the next free block (free blocks only; an allocated block's word is stale) */
    u32 units;                 /* size in HEAP_ALIGN units, header included */
} HeapBlock_;

#define BLOCK_AT(b, n) ((HeapBlock_ *)((u8 *)(b) + (n) * HEAP_ALIGN))

void heap_init(Heap *h, const char *name, void *region, u32 size)
{
    u32 base = ((u32)(uintptr_t)region + HEAP_ALIGN - 1u) & ~(HEAP_ALIGN - 1u);
    u32 end = ((u32)(uintptr_t)region + size) & ~(HEAP_ALIGN - 1u);
    HeapBlock_ *ring = (HeapBlock_ *)h->ring, *first = (HeapBlock_ *)(uintptr_t)base;
    ring->units = 0;
    first->next = ring;
    first->units = (end - base) / HEAP_ALIGN;
    ring->next = first;
    h->name = name;
    h->rover = ring;
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
    u32 units = (size + HEAP_ALIGN - 1u) / HEAP_ALIGN + 1u;
    HeapBlock_ *prev = (HeapBlock_ *)h->rover, *b;
    if (!h->first) return NULL;
    for (b = prev->next;; prev = b, b = b->next) {
        if (b->units >= units) {
            if (b->units == units) {
                prev->next = b->next;
            } else { /* the top of the block: the rest stays free below it */
                b->units -= units;
                b = BLOCK_AT(b, b->units);
                b->units = units;
            }
            h->rover = prev;
            h->used += units * HEAP_ALIGN;
            if (h->used > h->peak) h->peak = h->used;
            return (u8 *)b + HEAP_ALIGN;
        }
        if (b == (HeapBlock_ *)h->rover) break; /* round the ring once */
    }
    if (!h->failed_logged++) port_log("%s: alloc(%u) failed, %u KB in use\n", h->name, size, h->used >> 10);
    return NULL;
}

/* 1 if b can be a live block: inside the region, aligned, its size within it and in no free block. */
static int live_block(const Heap *h, HeapBlock_ *b)
{
    HeapBlock_ *f = (HeapBlock_ *)h->ring;
    if ((u8 *)b < (u8 *)h->first || (u8 *)b >= (u8 *)h->end || ((u32)(uintptr_t)b & (HEAP_ALIGN - 1u))) return 0;
    if (!b->units || b->units > (u32)((u8 *)h->end - (u8 *)b) / HEAP_ALIGN) return 0;
    for (f = f->next; f != (HeapBlock_ *)h->ring; f = f->next)
        if ((u8 *)b < (u8 *)BLOCK_AT(f, f->units) && (u8 *)BLOCK_AT(b, b->units) > (u8 *)f) return 0;
    return 1;
}

int heap_free(Heap *h, void *ptr)
{
    HeapBlock_ *b, *p;
    if (!ptr) return 0;
    b = (HeapBlock_ *)((u8 *)ptr - HEAP_ALIGN);
    if (!h->first || !live_block(h, b)) {
        port_log("%s: free(%08x) is not a live block\n", h->name, (u32)(uintptr_t)ptr);
        return 0;
    }
    h->used -= b->units * HEAP_ALIGN;
    for (p = (HeapBlock_ *)h->rover; !(b > p && b < p->next); p = p->next)
        if (p >= p->next && (b > p || b < p->next)) break; /* at the ring's end */
    if (BLOCK_AT(b, b->units) == p->next) { /* merge the free block above */
        b->units += p->next->units;
        b->next = p->next->next;
    } else {
        b->next = p->next;
    }
    if (BLOCK_AT(p, p->units) == b) { /* merge into the free block below */
        p->units += b->units;
        p->next = b->next;
    } else {
        p->next = b;
    }
    h->rover = p;
    return 1;
}
