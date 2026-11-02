/* 68000 group 4: single-operand arithmetic, MOVEM, LEA, PEA, JSR, JMP, LINK, TRAP and the control instructions. */
#include "sat_m68k_i.h"

static void movem(u32 op)
{
    int mode = (int)((op >> 3) & 7u), reg = (int)(op & 7u), lg = (op & 0x40u) != 0, dir = (op & 0x400u) != 0, bytes = lg ? 4 : 2;
    u32 mask = m_fetch(), a;
    if (!dir && mode == 4) {
        a = m68k.a[reg];
        for (int i = 0; i < 16; i++) {
            int r = 15 - i;
            if (!(mask & (1u << i))) continue;
            a -= (u32)bytes;
            m_wr(a, bytes, r < 8 ? m68k.d[r] : m68k.a[r - 8]);
        }
        m68k.a[reg] = a;
    } else if (!dir) {
        a = ea_addr(mode, reg);
        for (int i = 0; i < 16; i++) {
            if (!(mask & (1u << i))) continue;
            m_wr(a, bytes, i < 8 ? m68k.d[i] : m68k.a[i - 8]);
            a += (u32)bytes;
        }
    } else {
        a = mode == 3 ? m68k.a[reg] : ea_addr(mode, reg);
        for (int i = 0; i < 16; i++) {
            u32 v;
            if (!(mask & (1u << i))) continue;
            v = m_rd(a, bytes);
            if (!lg) v = m_sext(v, 1);
            if (i < 8) m68k.d[i] = v;
            else m68k.a[i - 8] = v;
            a += (u32)bytes;
        }
        if (mode == 3) m68k.a[reg] = a;
    }
}

static void control(u32 op)
{
    u32 v;
    switch (op) {
    case 0x4e70u: case 0x4e71u: return; /* RESET, NOP */
    case 0x4e72u:                       /* STOP */
        set_sr(m_fetch());
        m68k.stopped = 1;
        return;
    case 0x4e73u:                       /* RTE */
        v = m_rd(m68k.a[7], 2);
        m68k.a[7] += 2;
        m68k.pc = m_pop32();
        set_sr(v);
        return;
    case 0x4e75u: m68k.pc = m_pop32(); return;
    case 0x4e76u:
        if (m68k.sr & FV) exception(7);
        return;
    case 0x4e77u: /* RTR */
        v = m_rd(m68k.a[7], 2);
        m68k.a[7] += 2;
        m68k.sr = (u16)((m68k.sr & 0xff00u) | (v & 0x1fu));
        m68k.pc = m_pop32();
        return;
    default: m68k.pc -= 2; exception(4); return;
    }
}

static void unary(u32 op, int mode, int reg)
{
    int sz = sz_of(op);
    Ea e;
    u32 d, r;
    ea_decode(&e, mode, reg, sz);
    d = ea_get(&e, sz);
    switch ((op >> 9) & 7u) {
    case 0: r = alu_sub(d, 0, sz, (m68k.sr & FX) != 0, 1); break; /* NEGX */
    case 1: /* CLR */
        r = alu_logic(0, sz);
        break;
    case 2: r = alu_sub(d, 0, sz, 0, 0); break;                   /* NEG */
    default: r = alu_logic(~d, sz); break;                        /* NOT */
    }
    ea_put(&e, sz, r);
}

void op_g4(u32 op)
{
    int mode = (int)((op >> 3) & 7u), reg = (int)(op & 7u), sz = sz_of(op);
    Ea e;
    u32 v;
    if ((op & 0xfff0u) == 0x4e40u) {
        exception(32 + (int)(op & 15u));
        return;
    }
    if ((op & 0x1c0u) == 0x1c0u) { /* LEA */
        m68k.a[(op >> 9) & 7u] = ea_addr(mode, reg);
        return;
    }
    if ((op & 0x1c0u) == 0x180u) { /* CHK */
        s32 bound;
        ea_decode(&e, mode, reg, 1);
        bound = (s16)ea_get(&e, 1);
        v = (u32)(s32)(s16)m68k.d[(op >> 9) & 7u];
        if ((s32)v < 0 || (s32)v > bound) {
            m68k.sr = (u16)((m68k.sr & ~FN) | ((s32)v < 0 ? FN : 0u));
            exception(6);
        }
        return;
    }
    switch (op & 0xfff8u) {
    case 0x4e50u: /* LINK */
        v = (u32)(s32)(s16)m_fetch();
        m_push32(m68k.a[reg]);
        m68k.a[reg] = m68k.a[7];
        m68k.a[7] += v;
        return;
    case 0x4e58u: m68k.a[7] = m68k.a[reg], m68k.a[reg] = m_pop32(); return; /* UNLK */
    case 0x4e60u: m68k.other_sp = m68k.a[reg]; return;                       /* MOVE An,USP */
    case 0x4e68u: m68k.a[reg] = m68k.other_sp; return;
    default: break;
    }
    if ((op & 0xff80u) == 0x4e80u) { /* JSR, JMP */
        v = ea_addr(mode, reg);
        if (!(op & 0x40u)) m_push32(m68k.pc);
        m68k.pc = v;
        return;
    }
    if ((op & 0xfb80u) == 0x4880u) { /* EXT, MOVEM */
        if (mode == 0 && !(op & 0x400u)) {
            if (sz == 2) m68k.d[reg] = (m68k.d[reg] & 0xffff0000u) | (m_sext(m68k.d[reg], 0) & 0xffffu);
            else m68k.d[reg] = m_sext(m68k.d[reg], 1);
            set_nz(m68k.d[reg], sz == 2 ? 1 : 2);
        } else {
            movem(op);
        }
        return;
    }
    if ((op & 0xff00u) == 0x4e00u) {
        control(op);
        return;
    }
    switch (op & 0xff00u) {
    case 0x4800u:
        if (sz == 1 && mode == 0) { /* SWAP */
            m68k.d[reg] = m68k.d[reg] << 16 | m68k.d[reg] >> 16;
            set_nz(m68k.d[reg], 2);
        } else if (sz == 1) { /* PEA */
            m_push32(ea_addr(mode, reg));
        } else if (sz == 0) { /* NBCD */
            ea_decode(&e, mode, reg, 0);
            ea_put(&e, 0, bcd_op(1, ea_get(&e, 0), 0));
        } else {
            m68k.pc -= 2;
            exception(4);
        }
        return;
    case 0x4a00u:
        ea_decode(&e, mode, reg, sz == 3 ? 0 : sz);
        v = ea_get(&e, sz == 3 ? 0 : sz);
        set_nz(v, sz == 3 ? 0 : sz);
        if (sz == 3) ea_put(&e, 0, v | 0x80u); /* TAS */
        return;
    case 0x4000u: case 0x4200u: case 0x4400u: case 0x4600u:
        if (sz != 3) {
            unary(op, mode, reg);
            return;
        }
        ea_decode(&e, mode, reg, 1);
        if ((op & 0xff00u) == 0x4000u) {
            ea_put(&e, 1, m68k.sr); /* MOVE from SR */
        } else if ((op & 0xff00u) == 0x4400u) {
            m68k.sr = (u16)((m68k.sr & 0xff00u) | (ea_get(&e, 1) & 0x1fu)); /* MOVE to CCR */
        } else if ((op & 0xff00u) == 0x4600u) {
            set_sr(ea_get(&e, 1)); /* MOVE to SR */
        } else {
            m68k.pc -= 2;
            exception(4);
        }
        return;
    default: m68k.pc -= 2; exception(4); return;
    }
}
