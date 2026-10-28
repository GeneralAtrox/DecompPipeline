/* The ARM7DI's memory transfers (arm7.h): single words and bytes, halfwords and signed loads, block transfers and
 * swaps, as the ARM Architecture Reference Manual gives them for ARMv4. A word load from an address that is not a
 * multiple of 4 reads the aligned word rotated right by the misalignment, as the ARM7 does; a store of the PC stores
 * the instruction's address + 12. */
#include "arm7.h"

#define BIT(op, n) (((op) >> (n)) & 1u)
#define RN(op) (((op) >> 16) & 15u)
#define RD(op) (((op) >> 12) & 15u)
#define RM(op) ((op) & 15u)

static u32 load_word(Arm7 *a, u32 addr)
{
    u32 w = arm7_read(a, addr & ~3u, 4), rot = (addr & 3u) * 8u;
    return rot ? w >> rot | w << (32 - rot) : w;
}

static void set_reg(Arm7 *a, u32 n, u32 value)
{
    if (n == 15) {
        a->next = value & ~3u;
        a->cycles -= 2;
    } else {
        a->r[n] = value;
    }
}

void arm7_single(Arm7 *a, u32 op)
{
    u32 rn = RN(op), rd = RD(op), base = a->r[rn], offset, carry = a->c;
    offset = BIT(op, 25) ? arm7_shift_imm(a, op, &carry) : op & 0xfffu;
    u32 moved = BIT(op, 23) ? base + offset : base - offset;
    u32 addr = BIT(op, 24) ? moved : base;
    int writeback = !BIT(op, 24) || BIT(op, 21);
    if (BIT(op, 20)) {
        u32 value = BIT(op, 22) ? arm7_read(a, addr, 1) : load_word(a, addr);
        if (writeback && rn != 15) a->r[rn] = moved;
        set_reg(a, rd, value);
        a->cycles -= 2;
    } else {
        u32 value = rd == 15 ? a->r[15] + 4u : a->r[rd];
        if (BIT(op, 22)) arm7_write(a, addr, value & 0xffu, 1);
        else arm7_write(a, addr & ~3u, value, 4);
        if (writeback && rn != 15) a->r[rn] = moved;
        a->cycles -= 1;
    }
}

void arm7_half(Arm7 *a, u32 op)
{
    u32 rn = RN(op), rd = RD(op), base = a->r[rn], sh = (op >> 5) & 3u;
    u32 offset = BIT(op, 22) ? ((op >> 4) & 0xf0u) | (op & 0xfu) : a->r[RM(op)];
    u32 moved = BIT(op, 23) ? base + offset : base - offset;
    u32 addr = BIT(op, 24) ? moved : base;
    int writeback = !BIT(op, 24) || BIT(op, 21);
    if (BIT(op, 20)) {
        u32 value;
        if (sh == 1) value = arm7_read(a, addr & ~1u, 2);                 /* LDRH */
        else if (sh == 2) value = (u32)(s32)(s8)arm7_read(a, addr, 1);    /* LDRSB */
        else value = (u32)(s32)(s16)arm7_read(a, addr & ~1u, 2);          /* LDRSH */
        if (writeback && rn != 15) a->r[rn] = moved;
        set_reg(a, rd, value);
        a->cycles -= 2;
    } else {
        u32 value = rd == 15 ? a->r[15] + 4u : a->r[rd];
        arm7_write(a, addr & ~1u, value & 0xffffu, 2); /* STRH (the other forms are ARMv5's) */
        if (writeback && rn != 15) a->r[rn] = moved;
        a->cycles -= 1;
    }
}

/* LDM, STM: the lowest register at the lowest address. The S bit: with LDM of the PC the SPSR returns to the CPSR,
 * otherwise the user mode's registers are the ones transferred. A base in the list of an LDM takes the loaded value;
 * an STM stores the base's first value when it is the lowest register in the list, the written-back one otherwise. */
void arm7_block(Arm7 *a, u32 op)
{
    u32 rn = RN(op), list = op & 0xffffu, count = 0, base = a->r[rn], addr;
    for (u32 i = 0; i < 16; i++) count += BIT(list, i);
    if (!count) return;
    u32 span = count * 4u, after = BIT(op, 23) ? base + span : base - span;
    if (BIT(op, 23)) addr = BIT(op, 24) ? base + 4u : base;
    else addr = BIT(op, 24) ? base - span : base - span + 4u;
    int user = BIT(op, 22) && !(BIT(op, 20) && BIT(list, 15));
    a->cycles -= (s64)count + 1;
    if (BIT(op, 20)) {
        if (BIT(op, 21)) a->r[rn] = after;
        for (u32 i = 0; i < 16; i++) {
            if (!BIT(list, i)) continue;
            u32 value = arm7_read(a, addr, 4);
            addr += 4u;
            if (user) arm7_set_user_reg(a, i, value);
            else set_reg(a, i, value);
        }
        if (BIT(list, 15) && BIT(op, 22) && (a->psr & 0x1fu) != ARM_MODE_USR && (a->psr & 0x1fu) != ARM_MODE_SYS)
            arm7_set_cpsr(a, a->spsr);
    } else {
        u32 lowest = 0;
        while (!BIT(list, lowest)) lowest++;
        for (u32 i = 0; i < 16; i++) {
            if (!BIT(list, i)) continue;
            u32 value = i == 15 ? a->r[15] + 4u : user ? arm7_user_reg(a, i) : a->r[i];
            if (i == rn && i != lowest && BIT(op, 21)) value = after;
            arm7_write(a, addr, value, 4);
            addr += 4u;
        }
        if (BIT(op, 21)) a->r[rn] = after;
    }
}

void arm7_swap(Arm7 *a, u32 op)
{
    u32 addr = a->r[RN(op)], src = a->r[RM(op)], value;
    if (BIT(op, 22)) {
        value = arm7_read(a, addr, 1);
        arm7_write(a, addr, src & 0xffu, 1);
    } else {
        value = load_word(a, addr);
        arm7_write(a, addr & ~3u, src, 4);
    }
    set_reg(a, RD(op), value);
    a->cycles -= 3;
}
