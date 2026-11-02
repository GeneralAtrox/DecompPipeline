/* 68000 groups 0 (bit operations, immediate arithmetic, MOVEP), 1-3 (MOVE), 5 (ADDQ, SUBQ, Scc, DBcc) and 6 (branches). */
#include "sat_m68k_i.h"

static void bitop(u32 op, u32 bitnum)
{
    int mode = (int)((op >> 3) & 7u), reg = (int)(op & 7u), t = (int)((op >> 6) & 3u);
    Ea e;
    u32 v, bit;
    int dn = mode == 0;
    ea_decode(&e, mode, reg, dn ? 2 : 0);
    v = ea_get(&e, dn ? 2 : 0);
    bit = 1u << (bitnum & (dn ? 31u : 7u));
    m68k.sr = (u16)((m68k.sr & ~FZ) | (v & bit ? 0u : FZ));
    if (t == 0) return;
    v = t == 1 ? v ^ bit : t == 2 ? v & ~bit : v | bit;
    ea_put(&e, dn ? 2 : 0, v);
    m68k.cyc += 2;
}

static void movep(u32 op)
{
    int dreg = (int)((op >> 9) & 7u), areg = (int)(op & 7u), long_ = (op >> 6) & 1u, to_mem = (op >> 7) & 1u;
    u32 a = m68k.a[areg] + (u32)(s32)(s16)m_fetch();
    int n = long_ ? 4 : 2;
    if (to_mem) {
        for (int i = 0; i < n; i++) m_wr(a + 2u * (u32)i, 1, m68k.d[dreg] >> (8 * (n - 1 - i)));
    } else {
        u32 v = 0;
        for (int i = 0; i < n; i++) v = v << 8 | m_rd(a + 2u * (u32)i, 1);
        setd(dreg, long_ ? 2 : 1, v);
    }
}

static void immop(u32 op)
{
    int sz = sz_of(op), mode = (int)((op >> 3) & 7u), reg = (int)(op & 7u), kind = (int)((op >> 9) & 7u);
    u32 imm = sz == 2 ? m_fetch32() : m_fetch() & m_mask[sz], d, r;
    Ea e;
    if (mode == 7 && reg == 4 && (kind == 0 || kind == 1 || kind == 5)) { /* to CCR (byte) or SR (word) */
        u32 cur = sz == 0 ? m68k.sr & 0xffu : m68k.sr;
        r = kind == 0 ? cur | imm : kind == 1 ? cur & imm : cur ^ imm;
        if (sz == 0) m68k.sr = (u16)((m68k.sr & 0xff00u) | (r & 0x1fu));
        else if (m68k.sr & SR_S) set_sr(r);
        else {
            m68k.pc -= 4;
            exception(8);
        }
        return;
    }
    ea_decode(&e, mode, reg, sz);
    d = ea_get(&e, sz);
    switch (kind) {
    case 0: r = alu_logic(d | imm, sz); break;
    case 1: r = alu_logic(d & imm, sz); break;
    case 2: r = alu_sub(imm, d, sz, 0, 0); break;
    case 3: r = alu_add(imm, d, sz, 0, 0); break;
    case 5: r = alu_logic(d ^ imm, sz); break;
    default: alu_cmp(imm, d, sz); return;
    }
    ea_put(&e, sz, r);
}

void op_g0(u32 op)
{
    if (op & 0x100u) {
        if (((op >> 3) & 7u) == 1u) movep(op);
        else bitop(op, m68k.d[(op >> 9) & 7u]);
    } else if (((op >> 9) & 7u) == 4u) {
        u32 n = m_fetch();
        bitop(op, n);
    } else {
        immop(op);
    }
}

void op_move(u32 op)
{
    int sz = (op >> 12) == 1u ? 0 : (op >> 12) == 3u ? 1 : 2, dm = (int)((op >> 6) & 7u), dr = (int)((op >> 9) & 7u);
    Ea s, d;
    u32 v;
    ea_decode(&s, (int)((op >> 3) & 7u), (int)(op & 7u), sz);
    v = ea_get(&s, sz);
    if (dm == 1) {
        m68k.a[dr] = m_sext(v, sz);
        return;
    }
    ea_decode(&d, dm, dr, sz);
    set_nz(v, sz);
    ea_put(&d, sz, v);
}

void op_g5(u32 op)
{
    int sz = sz_of(op), mode = (int)((op >> 3) & 7u), reg = (int)(op & 7u);
    Ea e;
    if (sz == 3) {
        int c = (int)((op >> 8) & 15u);
        if (mode == 1) { /* DBcc */
            u32 base = m68k.pc;
            s32 disp = (s16)m_fetch();
            if (!cond(c)) {
                u32 n = (getd(reg, 1) - 1u) & 0xffffu;
                setd(reg, 1, n);
                if (n != 0xffffu) m68k.pc = base + (u32)disp;
            }
            m68k.cyc += 4;
        } else {
            ea_decode(&e, mode, reg, 0);
            ea_put(&e, 0, cond(c) ? 0xffu : 0u);
        }
    } else {
        u32 q = (op >> 9) & 7u, d, r;
        if (!q) q = 8;
        if (mode == 1) {
            m68k.a[reg] = (op & 0x100u) ? m68k.a[reg] - q : m68k.a[reg] + q;
            return;
        }
        ea_decode(&e, mode, reg, sz);
        d = ea_get(&e, sz);
        r = (op & 0x100u) ? alu_sub(q, d, sz, 0, 0) : alu_add(q, d, sz, 0, 0);
        ea_put(&e, sz, r);
    }
}

void op_g6(u32 op)
{
    u32 base = m68k.pc;
    s32 disp = (s8)op;
    int c = (int)((op >> 8) & 15u);
    if ((op & 0xffu) == 0) disp = (s16)m_fetch();
    if (c == 1) {
        m_push32(m68k.pc);
        m68k.pc = base + (u32)disp;
    } else if (cond(c)) {
        m68k.pc = base + (u32)disp;
        m68k.cyc += 2;
    }
}
