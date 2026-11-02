/* The ARM7's translated driver (arm7_native.h): finds the block that starts at the next instruction and runs it and
 * the blocks it chains to, in place of the interpreter (arm7.c's arm7_run). */
#include <string.h>
#include "arm7.h"

/* b, if the budget starts all its instructions and the RAM still holds the words it was translated from. */
static const Arm7Block *native_ready(const Arm7 *a, const Arm7Block *b)
{
    u32 pc = b->addr;
    if (a->cycles <= b->pre_max || pc + 4u * b->nwords > a->ram_size) return NULL;
    const u8 *p = a->ram + (pc & a->ram_mask);
    for (u32 k = 0; k < b->nwords; k++, p += 4) {
        u32 w;
        memcpy(&w, p, 4); /* the host is little-endian, as the sound RAM */
        if (w != b->words[k]) return NULL;
    }
    return b;
}

/* The block of the translated driver that starts at a->next. */
static const Arm7Block *native_block(const Arm7 *a)
{
    const Arm7Native *n = a->native;
    u32 i = (a->next >> 2) - n->first;
    if (i >= n->count || !n->lookup[i]) return NULL;
    return native_ready(a, &n->blocks[n->lookup[i] - 1u]);
}

int arm7_native_run(Arm7 *a)
{
    const Arm7Block *b = native_block(a);
    u64 e0 = a->executed;
    if (!b) return 0;
    while (b) {
        a->native_blocks++;
        u32 nx = b->run(a);
        if (a->fault >= 3 && (a->native_blocks & 4095u) == 0) { /* test only: a wrong block, to show the comparison catches it */
            if (a->fault == 3) a->cycles--;
            else a->r[1] ^= 1u;
        }
        b = nx ? native_ready(a, &a->native->blocks[nx - 1u]) : NULL;
        if (b && a->fast && a->scan_map[(b->addr >> 2) & 255u]) b = NULL; /* a scan loop's head: the fast path first */
    }
    a->native_instr += a->executed - e0;
    return 1;
}
