/* 68000 groups 8 (OR, DIV, SBCD), 9 and D (SUB, ADD and their A and X forms), B (CMP, EOR), C (AND, MUL, ABCD, EXG) and E (shifts). */
#include "sat_m68k_i.h"

u32 bcd_op(int sub, u32 s, u32 d)
{
    int x = (m68k.sr & FX) != 0;
    u32 r, c = 0;
    if (!sub) {
        u32 lo = (d & 15u) + (s & 15u) + (u32)x, hi = (d >> 4) + (s >> 4);
        if (lo > 9) lo -= 10, hi++;
        if (hi > 9) hi -= 10, c = 1;
        r = hi << 4 | lo;
    } else {
        int lo = (int)(d & 15u) - (int)(s & 15u) - x, hi = (int)(d >> 4) - (int)(s >> 4);
        if (lo < 0) lo += 10, hi--;
        if (hi < 0) hi += 10, c = 1;
        r = (u32)hi << 4 | (u32)lo;
    }
    r &= 0xffu;
    m68k.sr = (u16)((m68k.sr & ~(FC | FX | FN)) | (c ? FC | FX : 0u) | (r & 0x80u ? FN : 0u));
    if (r) m68k.sr = (u16)(m68k.sr & ~FZ);
    return r;
}

static void bcd_inst(u32 op, int sub)
{
    int rx = (int)(op & 7u), ry = (int)((op >> 9) & 7u), md = (op & 8u) ? 4 : 0;
    Ea s, d;
    ea_decode(&s, md, rx, 0);
    ea_decode(&d, md, ry, 0);
    ea_put(&d, 0, bcd_op(sub, ea_get(&s, 0), ea_get(&d, 0)));
}

static void logic_op(u32 op, int and_)
{
    int dn = (int)((op >> 9) & 7u), mode = (int)((op >> 3) & 7u), reg = (int)(op & 7u), opm = (int)((op >> 6) & 7u), sz = opm & 3;
    Ea e;
    u32 s, d;
    ea_decode(&e, mode, reg, sz);
    s = ea_get(&e, sz);
    d = getd(dn, sz);
    if (opm < 3) setd(dn, sz, alu_logic(and_ ? s & d : s | d, sz));
    else ea_put(&e, sz, alu_logic(and_ ? s & d : s | d, sz));
}

static void divide(u32 op)
{
    int dn = (int)((op >> 9) & 7u), sgn = ((op >> 6) & 7u) == 7u;
    Ea e;
    u32 d = m68k.d[dn], s, q, r;
    ea_decode(&e, (int)((op >> 3) & 7u), (int)(op & 7u), 1);
    s = ea_get(&e, 1);
    if (s == 0) {
        exception(5);
        return;
    }
    m68k.cyc += 120;
    if (!sgn) {
        q = d / s, r = d % s;
        if (q > 0xffffu) {
            m68k.sr = (u16)((m68k.sr & ~FC) | FV | FN);
            return;
        }
    } else {
        s32 ds = (s32)d, ss = (s16)s, qs, rs;
        if (ds == (s32)0x80000000 && ss == -1) {
            m68k.sr = (u16)((m68k.sr & ~FC) | FV);
            return;
        }
        qs = ds / ss, rs = ds % ss;
        if (qs < -32768 || qs > 32767) {
            m68k.sr = (u16)((m68k.sr & ~FC) | FV | FN);
            return;
        }
        q = (u32)qs & 0xffffu, r = (u32)rs & 0xffffu;
    }
    m68k.d[dn] = r << 16 | q;
    set_nz(q, 1);
}

void op_g8(u32 op)
{
    int opm = (int)((op >> 6) & 7u), mode = (int)((op >> 3) & 7u);
    if (opm == 3 || opm == 7) divide(op);
    else if (opm == 4 && mode < 2) bcd_inst(op, 1);
    else logic_op(op, 0);
}

void op_g9d(u32 op)
{
    int add = (op >> 12) == 13u, dn = (int)((op >> 9) & 7u), mode = (int)((op >> 3) & 7u), reg = (int)(op & 7u), opm = (int)((op >> 6) & 7u);
    int sz = opm & 3;
    u32 s, d, r, x = (m68k.sr & FX) != 0;
    Ea e, e2;
    if (opm == 3 || opm == 7) { /* ADDA, SUBA */
        sz = opm == 3 ? 1 : 2;
        ea_decode(&e, mode, reg, sz);
        s = m_sext(ea_get(&e, sz), sz);
        m68k.a[dn] = add ? m68k.a[dn] + s : m68k.a[dn] - s;
        return;
    }
    if (opm >= 4 && mode < 2) { /* ADDX, SUBX */
        ea_decode(&e, mode ? 4 : 0, reg, sz);
        ea_decode(&e2, mode ? 4 : 0, dn, sz);
        s = ea_get(&e, sz);
        d = ea_get(&e2, sz);
        ea_put(&e2, sz, add ? alu_add(s, d, sz, x, 1) : alu_sub(s, d, sz, x, 1));
        return;
    }
    ea_decode(&e, mode, reg, sz);
    if (opm < 3) {
        s = ea_get(&e, sz);
        d = getd(dn, sz);
        r = add ? alu_add(s, d, sz, 0, 0) : alu_sub(s, d, sz, 0, 0);
        setd(dn, sz, r);
    } else {
        s = getd(dn, sz);
        d = ea_get(&e, sz);
        r = add ? alu_add(s, d, sz, 0, 0) : alu_sub(s, d, sz, 0, 0);
        ea_put(&e, sz, r);
    }
}

void op_gb(u32 op)
{
    int dn = (int)((op >> 9) & 7u), mode = (int)((op >> 3) & 7u), reg = (int)(op & 7u), opm = (int)((op >> 6) & 7u), sz = opm & 3;
    Ea e;
    u32 s, d;
    if (opm == 3 || opm == 7) { /* CMPA */
        sz = opm == 3 ? 1 : 2;
        ea_decode(&e, mode, reg, sz);
        alu_cmp(m_sext(ea_get(&e, sz), sz), m68k.a[dn], 2);
    } else if (opm < 3) {
        ea_decode(&e, mode, reg, sz);
        alu_cmp(ea_get(&e, sz), getd(dn, sz), sz);
    } else if (mode == 1) { /* CMPM (Ay)+,(Ax)+ */
        Ea e2;
        ea_decode(&e, 3, reg, sz);
        ea_decode(&e2, 3, dn, sz);
        s = ea_get(&e, sz);
        d = ea_get(&e2, sz);
        alu_cmp(s, d, sz);
    } else { /* EOR Dn,<ea> */
        ea_decode(&e, mode, reg, sz);
        d = ea_get(&e, sz);
        ea_put(&e, sz, alu_logic(d ^ getd(dn, sz), sz));
    }
}

void op_gc(u32 op)
{
    int dn = (int)((op >> 9) & 7u), mode = (int)((op >> 3) & 7u), reg = (int)(op & 7u), opm = (int)((op >> 6) & 7u);
    Ea e;
    u32 s, t;
    if (opm == 3 || opm == 7) { /* MULU, MULS */
        ea_decode(&e, mode, reg, 1);
        s = ea_get(&e, 1);
        t = m68k.d[dn] & 0xffffu;
        if (opm == 7) m68k.d[dn] = (u32)((s32)(s16)s * (s32)(s16)t);
        else m68k.d[dn] = s * t;
        set_nz(m68k.d[dn], 2);
        m68k.cyc += 50;
    } else if (opm == 4 && mode < 2) {
        bcd_inst(op, 0);
    } else if (opm == 5 && mode == 0) {
        t = m68k.d[dn], m68k.d[dn] = m68k.d[reg], m68k.d[reg] = t;
    } else if (opm == 5 && mode == 1) {
        t = m68k.a[dn], m68k.a[dn] = m68k.a[reg], m68k.a[reg] = t;
    } else if (opm == 6 && mode == 1) {
        t = m68k.d[dn], m68k.d[dn] = m68k.a[reg], m68k.a[reg] = t;
    } else {
        logic_op(op, 1);
    }
}

void op_ge(u32 op)
{
    int sz = sz_of(op), left = (int)((op >> 8) & 1u), reg = (int)(op & 7u);
    Ea e;
    if (sz == 3) {
        ea_decode(&e, (int)((op >> 3) & 7u), reg, 1);
        ea_put(&e, 1, shift((int)((op >> 9) & 3u), left, ea_get(&e, 1), 1, 1));
    } else {
        u32 cnt = (op & 0x20u) ? m68k.d[(op >> 9) & 7u] & 63u : (((op >> 9) & 7u) ? (op >> 9) & 7u : 8u);
        setd(reg, sz, shift((int)((op >> 3) & 3u), left, getd(reg, sz), cnt, sz));
        m68k.cyc += 2u * cnt;
    }
}
