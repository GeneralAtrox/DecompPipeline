/* The AICA sound driver translated to C (tools/arm7_translate.py): a table of basic blocks, each a C function that does
 * exactly what the interpreter (arm7.c) does for the block's instructions: the same registers, flags, memory writes,
 * cycles and instruction count. The interpreter stays the reference and runs everything the table does not cover.
 *
 * A block is run only when (1) the next instruction is the block's first, (2) the sound RAM still holds the words it
 * was translated from (the SH-4 writes the driver in, and the driver keeps flags beside its code), and (3) the cycle
 * budget starts every one of its instructions (the budget above pre_max, the worst cost of all but the last). No
 * FIQ can come between a block's instructions: the line and the mask change only by a device access or a PSR write,
 * and a block returns right after either (returning 0), at an instruction boundary, for arm7_run to look again.
 * A block that ends by a branch or by running into the next block returns that block's index + 1, and arm7_run runs it
 * without looking again for a FIQ, as nothing since the last look could have raised one. */
#ifndef ARM7_NATIVE_H
#define ARM7_NATIVE_H

typedef struct Arm7Block {
    u32 addr, nwords;
    const u32 *words;     /* what the block was translated from */
    s32 pre_max;          /* the worst-case cycles of every instruction but the last */
    u32 (*run)(Arm7 *a);  /* returns the index + 1 of the block that follows when it is known, else 0 */
} Arm7Block;

struct Arm7Native {
    u32 first, count;          /* lookup[(pc >> 2) - first], for count words from first */
    const u16 *lookup;         /* 0: no block starts there, else the block's index + 1 */
    const Arm7Block *blocks;
};

/* What the generated blocks use: the interpreter's own memory and arithmetic. */
static inline u32 n_rd8(Arm7 *a, u32 addr) { return addr < a->ram_size ? (u32)a->ram[addr & a->ram_mask] : arm7_read(a, addr, 1); }

static inline u32 n_rd32(Arm7 *a, u32 addr) /* a word load: the aligned word rotated right by the misalignment */
{
    u32 al = addr & ~3u, w, rot = (addr & 3u) * 8u;
    if (al < a->ram_size) {
        const u8 *p = a->ram + (al & a->ram_mask);
        w = (u32)p[0] | (u32)p[1] << 8 | (u32)p[2] << 16 | (u32)p[3] << 24;
    } else {
        w = arm7_read(a, al, 4);
    }
    return rot ? w >> rot | w << (32 - rot) : w;
}

static inline void n_wr8(Arm7 *a, u32 addr, u32 v)
{
    if (addr < a->ram_size) a->ram[addr & a->ram_mask] = (u8)v;
    else arm7_write(a, addr, v & 0xffu, 1);
}

static inline void n_wr32(Arm7 *a, u32 addr, u32 v) /* addr is a multiple of 4 */
{
    if (addr < a->ram_size) {
        u8 *p = a->ram + (addr & a->ram_mask);
        p[0] = (u8)v, p[1] = (u8)(v >> 8), p[2] = (u8)(v >> 16), p[3] = (u8)(v >> 24);
    } else {
        arm7_write(a, addr, v, 4);
    }
}

/* x + y + carry_in, setting C and V when s (a constant at the call). */
static inline u32 n_add(Arm7 *a, u32 x, u32 y, u32 carry_in, int s)
{
    u64 wide = (u64)x + y + carry_in;
    u32 r = (u32)wide;
    if (s) {
        a->c = (u32)(wide >> 32);
        a->v = ((~(x ^ y) & (x ^ r)) >> 31) & 1u;
    }
    return r;
}

static inline void n_nz(Arm7 *a, u32 r) { a->n = r >> 31; a->z = r == 0; }

int arm7_native_run(Arm7 *a); /* arm7_native.c: runs the block at a->next and the blocks it chains to; 0 if none is ready */

#endif
