/* The 68000 core: registers, operands, flags, exceptions and the run loop. The instruction groups are in
 * sat_m68k_g0.c (bit ops, immediates, MOVE, ADDQ, branches), sat_m68k_g4.c (the miscellaneous group) and sat_m68k_g8.c
 * (OR, SUB, CMP, AND, ADD and the shifts).
 *
 * Nearest existing: runtime/arm7.c (the Dreamcast sound CPU, an ARM) - a different instruction set, so nothing to share. */
#include "sat_m68k_i.h"

M68k m68k;
const u32 m_mask[3] = {0xffu, 0xffffu, 0xffffffffu};
const u32 m_msb[3] = {0x80u, 0x8000u, 0x80000000u};

u32 m_rd(u32 a, int sz)
{
    m68k.cyc += sz == 4 ? 8u : 4u;
    return sat_snd_rd(a & 0xffffffu, sz);
}

void m_wr(u32 a, int sz, u32 v)
{
    m68k.cyc += sz == 4 ? 8u : 4u;
    sat_snd_wr(a & 0xffffffu, sz, v);
}

u32 m_fetch(void)
{
    u32 v = m_rd(m68k.pc, 2);
    m68k.pc += 2;
    return v;
}

u32 m_fetch32(void)
{
    u32 hi = m_fetch();
    return hi << 16 | m_fetch();
}

void m_push32(u32 v)
{
    m68k.a[7] -= 4;
    m_wr(m68k.a[7], 4, v);
}

u32 m_pop32(void)
{
    u32 v = m_rd(m68k.a[7], 4);
    m68k.a[7] += 4;
    return v;
}

u32 getd(int r, int sz) { return m68k.d[r] & m_mask[sz]; }

void setd(int r, int sz, u32 v)
{
    if (sz == 2) m68k.d[r] = v;
    else m68k.d[r] = (m68k.d[r] & ~m_mask[sz]) | (v & m_mask[sz]);
}

int sz_of(u32 op) { return (int)((op >> 6) & 3u); }

u32 m_sext(u32 v, int sz) { return sz == 0 ? (u32)(s32)(s8)v : sz == 1 ? (u32)(s32)(s16)v : v; }

static u32 index_ea(u32 base)
{
    u32 x = m_fetch();
    u32 r = (x & 0x8000u) ? m68k.a[(x >> 12) & 7u] : m68k.d[(x >> 12) & 7u];
    if (!(x & 0x800u)) r = (u32)(s32)(s16)r;
    return base + r + (u32)(s32)(s8)x;
}

void ea_decode(Ea *e, int mode, int reg, int sz)
{
    int bytes = 1 << sz;
    u32 a;
    switch (mode) {
    case 0: e->kind = 0, e->v = (u32)reg; return;
    case 1: e->kind = 1, e->v = (u32)reg; return;
    case 2: a = m68k.a[reg]; break;
    case 3:
        a = m68k.a[reg];
        m68k.a[reg] += (u32)(bytes == 1 && reg == 7 ? 2 : bytes);
        break;
    case 4:
        m68k.a[reg] -= (u32)(bytes == 1 && reg == 7 ? 2 : bytes);
        a = m68k.a[reg];
        break;
    case 5: a = m68k.a[reg] + (u32)(s32)(s16)m_fetch(); break;
    case 6: a = index_ea(m68k.a[reg]); break;
    default:
        switch (reg) {
        case 0: a = (u32)(s32)(s16)m_fetch(); break;
        case 1: a = m_fetch32(); break;
        case 2: {
            u32 base = m68k.pc;
            a = base + (u32)(s32)(s16)m_fetch();
            break;
        }
        case 3: a = index_ea(m68k.pc); break;
        default:
            e->kind = 3;
            e->v = sz == 2 ? m_fetch32() : (m_fetch() & m_mask[sz]);
            return;
        }
        break;
    }
    e->kind = 2, e->v = a;
}

u32 ea_get(const Ea *e, int sz)
{
    switch (e->kind) {
    case 0: return m68k.d[e->v] & m_mask[sz];
    case 1: return m68k.a[e->v] & m_mask[sz];
    case 2: return m_rd(e->v, 1 << sz);
    default: return e->v;
    }
}

void ea_put(const Ea *e, int sz, u32 v)
{
    if (e->kind == 0) setd((int)e->v, sz, v);
    else if (e->kind == 1) m68k.a[e->v] = v;
    else if (e->kind == 2) m_wr(e->v, 1 << sz, v & m_mask[sz]);
}

u32 ea_addr(int mode, int reg)
{
    Ea e;
    if (mode == 2) return m68k.a[reg]; /* no bus access */
    ea_decode(&e, mode, reg, 2);
    return e.v;
}

void set_sr(u32 v)
{
    if ((v ^ m68k.sr) & SR_S) {
        u32 t = m68k.a[7];
        m68k.a[7] = m68k.other_sp;
        m68k.other_sp = t;
    }
    m68k.sr = (u16)(v & 0xa71fu);
}

void exception(int vec)
{
    u32 old = m68k.sr;
    set_sr((old | SR_S) & ~0x8000u);
    m_push32(m68k.pc);
    m68k.a[7] -= 2;
    m_wr(m68k.a[7], 2, old);
    m68k.pc = m_rd((u32)vec * 4u, 4);
    m68k.cyc += 34;
}

int cond(int c)
{
    int fc = m68k.sr & FC, fv = (m68k.sr & FV) != 0, fz = (m68k.sr & FZ) != 0, fn = (m68k.sr & FN) != 0;
    switch (c) {
    case 0: return 1;
    case 1: return 0;
    case 2: return !fc && !fz;
    case 3: return fc || fz;
    case 4: return !fc;
    case 5: return fc;
    case 6: return !fz;
    case 7: return fz;
    case 8: return !fv;
    case 9: return fv;
    case 10: return !fn;
    case 11: return fn;
    case 12: return fn == fv;
    case 13: return fn != fv;
    case 14: return !fz && fn == fv;
    default: return fz || fn != fv;
    }
}

static void flags(u32 r, int sz, u32 c, u32 v, int x, int ext)
{
    u32 f = (u32)m68k.sr & 0xffe0u;
    int z = (r & m_mask[sz]) == 0;
    if (ext) z = z && (m68k.sr & FZ);
    f |= (r & m_msb[sz] ? FN : 0u) | (z ? FZ : 0u) | (v ? FV : 0u) | (c ? FC : 0u);
    f |= x ? (c ? FX : 0u) : (m68k.sr & FX);
    m68k.sr = (u16)f;
}

u32 alu_add(u32 s, u32 d, int sz, u32 cin, int ext)
{
    unsigned long long t = (unsigned long long)s + d + cin;
    u32 r = (u32)t & m_mask[sz];
    flags(r, sz, (u32)(t >> (8 << sz)) & 1u, (s ^ r) & (d ^ r) & m_msb[sz], 1, ext);
    return r;
}

u32 alu_sub(u32 s, u32 d, int sz, u32 cin, int ext)
{
    unsigned long long t = (unsigned long long)d - s - cin;
    u32 r = (u32)t & m_mask[sz];
    flags(r, sz, (u32)(t >> (8 << sz)) & 1u, (s ^ d) & (d ^ r) & m_msb[sz], 1, ext);
    return r;
}

void alu_cmp(u32 s, u32 d, int sz)
{
    unsigned long long t = (unsigned long long)d - s;
    u32 r = (u32)t & m_mask[sz];
    flags(r, sz, (u32)(t >> (8 << sz)) & 1u, (s ^ d) & (d ^ r) & m_msb[sz], 0, 0);
}

u32 alu_logic(u32 r, int sz)
{
    r &= m_mask[sz];
    flags(r, sz, 0, 0, 0, 0);
    return r;
}

void set_nz(u32 r, int sz) { flags(r, sz, 0, 0, 0, 0); }

u32 shift(int type, int left, u32 v, u32 cnt, int sz)
{
    u32 m = m_mask[sz], msb = m_msb[sz], x = (m68k.sr & FX) != 0, c = 0, ov = 0;
    v &= m;
    if (cnt == 0) c = type == 2 ? x : 0;
    for (u32 i = 0; i < cnt; i++) {
        u32 out;
        if (left) {
            out = (v & msb) != 0;
            v = (v << 1) & m;
            if (type == 2) v |= x;
            else if (type == 3) v |= out;
            if (type == 0 && ((v & msb) != 0) != (out != 0)) ov = 1;
        } else {
            u32 sign = v & msb;
            out = v & 1u;
            v >>= 1;
            if (type == 0) v |= sign;
            else if (type == 2 && x) v |= msb;
            else if (type == 3 && out) v |= msb;
        }
        c = out;
        if (type != 3) x = out;
    }
    flags(v, sz, c, ov, 0, 0);
    if (type != 3 && cnt) m68k.sr = (u16)((m68k.sr & ~FX) | (x ? FX : 0u));
    return v;
}

void m68k_reset(void)
{
    for (int i = 0; i < 8; i++) m68k.d[i] = m68k.a[i] = 0;
    m68k.sr = SR_S | 0x0700u;
    m68k.other_sp = 0;
    m68k.a[7] = m_rd(0, 4);
    m68k.pc = m_rd(4, 4);
    m68k.stopped = 0;
    m68k.ipl = 0;
    m68k.cyc = 0;
}

void m68k_set_ipl(int level) { m68k.ipl = (u8)level; }

static void execute(u32 op)
{
    switch (op >> 12) {
    case 0: op_g0(op); break;
    case 1: case 2: case 3: op_move(op); break;
    case 4: op_g4(op); break;
    case 5: op_g5(op); break;
    case 6: op_g6(op); break;
    case 7:
        m68k.d[(op >> 9) & 7u] = m_sext(op & 0xffu, 0);
        set_nz(m68k.d[(op >> 9) & 7u], 2);
        break;
    case 8: op_g8(op); break;
    case 9: case 13: op_g9d(op); break;
    case 11: op_gb(op); break;
    case 12: op_gc(op); break;
    case 14: op_ge(op); break;
    default: m68k.pc -= 2; exception(op >> 12 == 10 ? 10 : 11); break;
    }
}

u32 m68k_run(u32 cycles)
{
    u32 start = m68k.cyc;
    while (m68k.cyc - start < cycles) {
        u32 lvl = m68k.ipl;
        if (lvl && (lvl == 7 || lvl > (u32)((m68k.sr >> 8) & 7u))) {
            u32 old = m68k.sr;
            m68k.stopped = 0;
            set_sr((old | SR_S) & ~0x8000u);
            m_push32(m68k.pc);
            m68k.a[7] -= 2;
            m_wr(m68k.a[7], 2, old);
            m68k.sr = (u16)((m68k.sr & ~0x0700u) | (lvl << 8));
            m68k.pc = m_rd((24u + lvl) * 4u, 4);
            m68k.cyc += 44;
            continue;
        }
        if (m68k.stopped) {
            m68k.cyc = start + cycles;
            break;
        }
        execute(m_fetch());
    }
    return m68k.cyc - start;
}
