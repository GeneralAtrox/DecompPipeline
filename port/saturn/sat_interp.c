/* The SH-2 interpreter fallback: an onboarding and bring-up aid, not a play path.
 * A call or jump from translated code to an address with no translated function was a STUB that returned 0 and the game
 * stalled (Saturn Bomberman: five of its eight onboarding rounds were such a stop, 2026-11-16). With --fallback on (the
 * onboard tool's boot runs) the kit runs that code here instead, on the same CPU state as translated code (sat_call.c's
 * SatCpu: r1..r14 shared, SR and GBR, r15 the guest stack, memory through the kit's bus rd/wr), and carries on. A call
 * the interpreted code makes to an address that has a translated function (or a BIOS service) goes back to translated C
 * through tc_call_sp, so a translated function always wins and control returns to translated code as soon as it can.
 * Each address that ran here is counted (entries, instructions) with the translated call chain at its first entry and
 * logged as "FALLBACK" lines plus a "fallbacks:" summary at exit; tools/AutoOnboard/onboard_sat.py reads them, reports the count and
 * feeds the addresses to the next translation as seeds, so the count is driven towards zero (tmcleroy/satrecomp's
 * "fallback, counted, drive to zero" is the model; the idea only, no code: that project is GPL-3).
 * Nearest existing: the judge's oracle TcEmu (targets/saturn_sh2, Java over Ghidra p-code) - it cannot run inside the
 * 32-bit kit and shares no state with it. The SH-2 decode here is written from the SH7604 instruction set; the
 * division step follows the manual's DIV1 description.
 * --fallback check (a test of this interpreter, not a mode to play in): every translated function's first three calls
 * also run here, from the same state, in a pure mode that touches work RAM only, and r0, r8..r14 and work RAM are compared
 * with what the translated C did (sat_call.c check_call); the translated result is what the game keeps.
 * Off (the default for play runs) nothing here runs and a missing function is a STUB, as before. Timing: about 480,000
 * instructions are one frame of a 28.6 MHz SH-2, so every 2^19 instructions the master waits for the next vertical blank
 * (tc_spin), and the bus's own read count still gives interrupts and the other CPU their turns. */
#include <stdlib.h>
#include <string.h>
#include "sat.h"
#include "sat_cpu.h"
#include "sat_sys.h"

typedef long long fb_s64;
typedef unsigned long long fb_u64;

int sat_fallback_on;
void sat_fallback_option(const char *v) { sat_fallback_on = !v ? 0 : !strcmp(v, "on") ? 1 : !strcmp(v, "check") ? 2 : 0; }

SatCpu *sat_cpu_cur(void);           /* sat_call.c */
const SatFn *sat_find_fn(u32 addr);  /* sat_call.c */

enum { FB_MAX = 4096, SPIN_STEPS = 1 << 19 };
#define SENT 0xfffffff0u
typedef struct {
    u32 addr, entries, nested, chain[4];
    fb_u64 steps;
} FbRow;
static FbRow *g_fb;
static int g_nfb, g_faults;
static fb_u64 g_steps;
static u32 g_vbr = 0x06000000u;
static u32 g_past;       /* interpreted code that returned past its caller (its pr came off the stack) */
static int g_entry_tail; /* the next sat_interp is a jump from an open frame (tc_tail_sp): its return is the jumper's */
void sat_interp_tail_next(void) { g_entry_tail = 1; }

/* --fallback check's pure mode: work RAM only, straight from the arrays, no bus, no interrupts, no calls out; anything
 * else (a hardware register, a BIOS service, a sleep, a fault, a runaway) makes the run not comparable */
static int g_pure, g_pure_bad;
static u8 *pure_at(u32 a, u32 size)
{
    u32 p = a & 0x1fffffffu;
    if (!(a & (size - 1u))) {
        if (p >> 24 == 6u || p >> 24 == 7u) return sat_ram_h() + (p & (SAT_WRAM_SIZE - 1u));
        if (p - SAT_WRAM_L_BASE < SAT_WRAM_SIZE) return sat_ram_l() + (p - SAT_WRAM_L_BASE);
    }
    g_pure_bad = 1;
    return NULL;
}
static u32 i_rd32(u32 a)
{
    u8 *q;
    if (!g_pure) return rd32(a);
    return (q = pure_at(a, 4u)) ? (u32)q[0] << 24 | (u32)q[1] << 16 | (u32)q[2] << 8 | q[3] : 0u;
}
static u16 i_rd16(u32 a)
{
    u8 *q;
    if (!g_pure) return rd16(a);
    return (q = pure_at(a, 2u)) ? (u16)(q[0] << 8 | q[1]) : (u16)0;
}
static u8 i_rd8(u32 a)
{
    u8 *q;
    if (!g_pure) return rd8(a);
    return (q = pure_at(a, 1u)) ? q[0] : (u8)0;
}
static void i_wr32(u32 a, u32 v)
{
    u8 *q;
    if (!g_pure) { wr32(a, v); return; }
    if ((q = pure_at(a, 4u))) q[0] = (u8)(v >> 24), q[1] = (u8)(v >> 16), q[2] = (u8)(v >> 8), q[3] = (u8)v;
}
static void i_wr16(u32 a, u16 v)
{
    u8 *q;
    if (!g_pure) { wr16(a, v); return; }
    if ((q = pure_at(a, 2u))) q[0] = (u8)(v >> 8), q[1] = (u8)v;
}
static void i_wr8(u32 a, u8 v)
{
    u8 *q;
    if (!g_pure) { wr8(a, v); return; }
    if ((q = pure_at(a, 1u))) q[0] = v;
}
/* code and pc-relative constants straight from RAM: translated code fetches neither, so these reads must not reach the
 * bus, whose read count drives the poll, interrupt and other-CPU heuristics (Dragon Force's movie ran a thousand vertical
 * blanks long while a shift ladder was interpreted, 2026-11-16) */
static u8 *code_at(u32 a)
{
    u32 p = a & 0x1fffffffu;
    if (p >> 24 == 6u || p >> 24 == 7u) return sat_ram_h() + (p & (SAT_WRAM_SIZE - 1u) & ~1u);
    if (p - SAT_WRAM_L_BASE < SAT_WRAM_SIZE) return sat_ram_l() + ((p - SAT_WRAM_L_BASE) & ~1u);
    return NULL;
}
static u16 code16(u32 a)
{
    u8 *q = code_at(a);
    return q ? (u16)(q[0] << 8 | q[1]) : i_rd16(a);
}
static u32 code32(u32 a) { return (u32)code16(a) << 16 | code16(a + 2u); }
#define rd32 i_rd32
#define rd16 i_rd16
#define rd8 i_rd8
#define wr32 i_wr32
#define wr16 i_wr16
#define wr8 i_wr8

/* code runs from work RAM only: the kit has no BIOS image to interpret */
int sat_fallback_ok(u32 a)
{
    a &= 0xdfffffffu;
    return !(a & 1u) && ((a >= 0x06000000u && a < 0x06100000u) || (a >= 0x00200000u && a < 0x00300000u));
}

static FbRow *fb_note(u32 addr, int nested)
{
    SatCpu *c = sat_cpu_cur();
    if (g_pure) return NULL;
    addr &= 0xdfffffffu;
    for (int i = 0; i < g_nfb; i++)
        if (g_fb[i].addr == addr) {
            g_fb[i].entries++;
            return &g_fb[i];
        }
    if (!g_fb) g_fb = (FbRow *)calloc(FB_MAX, sizeof *g_fb);
    if (!g_fb || g_nfb >= FB_MAX) return NULL;
    FbRow *r = &g_fb[g_nfb++];
    r->addr = addr, r->entries = 1, r->nested = (u32)nested;
    int top = c->depth > 0 && c->depth <= 512 && c->stack[c->depth - 1] == addr; /* its own frame, pushed by fallback_run */
    for (int k = 0; k < 4; k++) {
        int d = c->depth - 1 - top - k;
        r->chain[k] = d >= 0 && d < 512 ? c->stack[d] : 0;
    }
    port_log("FALLBACK #%d (%s): %08x has no translated function; the SH-2 interpreter runs it%s; from %08x < %08x < %08x < %08x\n",
             g_nfb, sat_cpu_id() ? "slave" : "master", addr, nested ? " (called by interpreted code)" : "", r->chain[0], r->chain[1],
             r->chain[2], r->chain[3]);
    return r;
}

static void fault(u32 pc, u32 op, const char *why)
{
    if (g_pure) {
        g_pure_bad = 1;
        return;
    }
    if (++g_faults <= 32) port_log("FALLBACK FAULT: %s at %08x (op %04x); the interpreted call returns r0\n", why, pc, op);
}

/* one CPU's registers while it runs here; SR is the SatCpu's own (sh4_ldc_sr keeps its bookkeeping) */
typedef struct {
    u32 R[16], pr, mach, macl, next, tgt;
} Ctx;

enum { K_NONE, K_CALL, K_JUMP, K_RET, K_RTE, K_FAULT, K_SLEEP };
#define SR (sat_cpu_cur()->sr)
#define T_SET(v) (SR = (SR & ~1u) | ((v) ? 1u : 0u))
#define T_GET (SR & 1u)
#define SX8(v) ((u32)(s32)(s8)(v))
#define SX16(v) ((u32)(s32)(s16)(v))

static u32 fetch(u32 pc) { return code16(pc & 0xdfffffffu); }
static void set_sr(u32 v) { sh4_ldc_sr(v); }

static void div1(Ctx *x, int n, int m)
{
    u32 *Rn = &x->R[n], Rm = x->R[m], sr = SR, oq = (sr >> 8) & 1u, M = (sr >> 9) & 1u, Q = *Rn >> 31, t0, t1;
    *Rn = (*Rn << 1) | (sr & 1u);
    t0 = *Rn;
    if (oq == M) {
        *Rn -= Rm;
        t1 = *Rn > t0;
        Q = M ? (Q ? t1 : !t1) : (Q ? !t1 : t1);
    } else {
        *Rn += Rm;
        t1 = *Rn < t0;
        Q = M ? (Q ? t1 : !t1) : (Q ? !t1 : t1);
    }
    SR = (sr & ~0x301u) | (Q << 8) | (M << 9) | (u32)(Q == M);
}

static void mac_l(Ctx *x, int n, int m)
{
    fb_s64 a = (s32)rd32(x->R[n]), b;
    x->R[n] += 4;
    b = (s32)rd32(x->R[m]);
    x->R[m] += 4;
    fb_s64 acc = (fb_s64)(((fb_u64)x->mach << 32) | x->macl) + a * b;
    if (SR & 2u) { /* S: saturate to 48 bits */
        const fb_s64 hi = 0x00007fffffffffffLL, lo = -0x0000800000000000LL;
        if (acc > hi) acc = hi;
        if (acc < lo) acc = lo;
    }
    x->mach = (u32)((fb_u64)acc >> 32), x->macl = (u32)acc;
}

static void mac_w(Ctx *x, int n, int m)
{
    s32 a = (s16)rd16(x->R[n]), b;
    x->R[n] += 2;
    b = (s16)rd16(x->R[m]);
    x->R[m] += 2;
    if (SR & 2u) { /* S: 32-bit saturation on MACL, MACH bit 0 marks an overflow */
        fb_s64 acc = (fb_s64)(s32)x->macl + (fb_s64)a * b;
        if (acc > 0x7fffffffLL) acc = 0x7fffffffLL, x->mach |= 1u;
        if (acc < -0x80000000LL) acc = -0x80000000LL, x->mach |= 1u;
        x->macl = (u32)acc;
    } else {
        fb_s64 acc = (fb_s64)(((fb_u64)x->mach << 32) | x->macl) + (fb_s64)a * b;
        x->mach = (u32)((fb_u64)acc >> 32), x->macl = (u32)acc;
    }
}

/* one instruction at pc; a branch leaves its kind and target (x->tgt) and the loop runs the delay slot */
static int step(Ctx *x, u32 op, u32 pc)
{
    u32 *R = x->R;
    int n = (op >> 8) & 15, m = (op >> 4) & 15;
    u32 t;
    x->next = pc + 2u;
    switch (op >> 12) {
    case 0x0:
        switch (op & 0xff) {
        case 0x02: R[n] = SR; return K_NONE;
        case 0x12: R[n] = sat_cpu_cur()->gbr; return K_NONE;
        case 0x22: R[n] = g_vbr; return K_NONE;
        case 0x03: x->pr = pc + 4u; x->tgt = pc + 4u + R[n]; return K_CALL; /* bsrf */
        case 0x23: x->tgt = pc + 4u + R[n]; return K_JUMP;                  /* braf */
        case 0x08: T_SET(0); return K_NONE;
        case 0x18: T_SET(1); return K_NONE;
        case 0x28: x->mach = x->macl = 0; return K_NONE;
        case 0x09: return K_NONE;
        case 0x19: SR &= ~0x301u; return K_NONE; /* div0u */
        case 0x29: R[n] = T_GET; return K_NONE;
        case 0x0a: R[n] = x->mach; return K_NONE;
        case 0x1a: R[n] = x->macl; return K_NONE;
        case 0x2a: R[n] = x->pr; return K_NONE;
        case 0x0b: x->tgt = x->pr; return K_RET;
        case 0x1b: return K_SLEEP;
        case 0x2b: return K_RTE;
        }
        switch (op & 15) {
        case 0x4: wr8(R[0] + R[n], (u8)R[m]); return K_NONE;
        case 0x5: wr16(R[0] + R[n], (u16)R[m]); return K_NONE;
        case 0x6: wr32(R[0] + R[n], R[m]); return K_NONE;
        case 0x7: x->macl = R[n] * R[m]; return K_NONE;
        case 0xc: R[n] = SX8(rd8(R[0] + R[m])); return K_NONE;
        case 0xd: R[n] = SX16(rd16(R[0] + R[m])); return K_NONE;
        case 0xe: R[n] = rd32(R[0] + R[m]); return K_NONE;
        case 0xf: mac_l(x, n, m); return K_NONE;
        }
        return K_FAULT;
    case 0x1: wr32(R[n] + (op & 15) * 4u, R[m]); return K_NONE;
    case 0x2:
        switch (op & 15) {
        case 0x0: wr8(R[n], (u8)R[m]); return K_NONE;
        case 0x1: wr16(R[n], (u16)R[m]); return K_NONE;
        case 0x2: wr32(R[n], R[m]); return K_NONE;
        case 0x4: t = R[m]; R[n] -= 1; wr8(R[n], (u8)t); return K_NONE;
        case 0x5: t = R[m]; R[n] -= 2; wr16(R[n], (u16)t); return K_NONE;
        case 0x6: t = R[m]; R[n] -= 4; wr32(R[n], t); return K_NONE;
        case 0x7: { /* div0s */
            u32 q = R[n] >> 31, mm = R[m] >> 31;
            SR = (SR & ~0x301u) | (q << 8) | (mm << 9) | (q ^ mm);
            return K_NONE;
        }
        case 0x8: T_SET((R[n] & R[m]) == 0); return K_NONE;
        case 0x9: R[n] &= R[m]; return K_NONE;
        case 0xa: R[n] ^= R[m]; return K_NONE;
        case 0xb: R[n] |= R[m]; return K_NONE;
        case 0xc: t = R[n] ^ R[m]; T_SET(!(t >> 24 & 255) || !(t >> 16 & 255) || !(t >> 8 & 255) || !(t & 255)); return K_NONE;
        case 0xd: R[n] = (R[n] >> 16) | (R[m] << 16); return K_NONE;
        case 0xe: x->macl = (u32)(u16)R[n] * (u32)(u16)R[m]; return K_NONE;
        case 0xf: x->macl = (u32)((s32)(s16)R[n] * (s32)(s16)R[m]); return K_NONE;
        }
        return K_FAULT;
    case 0x3:
        switch (op & 15) {
        case 0x0: T_SET(R[n] == R[m]); return K_NONE;
        case 0x2: T_SET(R[n] >= R[m]); return K_NONE;
        case 0x3: T_SET((s32)R[n] >= (s32)R[m]); return K_NONE;
        case 0x4: div1(x, n, m); return K_NONE;
        case 0x5: { fb_u64 p = (fb_u64)R[n] * R[m]; x->mach = (u32)(p >> 32), x->macl = (u32)p; return K_NONE; }
        case 0x6: T_SET(R[n] > R[m]); return K_NONE;
        case 0x7: T_SET((s32)R[n] > (s32)R[m]); return K_NONE;
        case 0x8: R[n] -= R[m]; return K_NONE;
        case 0xa: { u32 a = R[n], b = R[m], tb = T_GET; R[n] = a - b - tb; T_SET(a < b || (a - b) < tb); return K_NONE; }
        case 0xb: { fb_s64 r = (fb_s64)(s32)R[n] - (s32)R[m]; R[n] = (u32)r; T_SET(r != (s32)r); return K_NONE; }
        case 0xc: R[n] += R[m]; return K_NONE;
        case 0xd: { fb_s64 p = (fb_s64)(s32)R[n] * (s32)R[m]; x->mach = (u32)((fb_u64)p >> 32), x->macl = (u32)p; return K_NONE; }
        case 0xe: { u32 a = R[n], s = a + R[m], r = s + T_GET; T_SET(s < a || r < s); R[n] = r; return K_NONE; }
        case 0xf: { fb_s64 r = (fb_s64)(s32)R[n] + (s32)R[m]; R[n] = (u32)r; T_SET(r != (s32)r); return K_NONE; }
        }
        return K_FAULT;
    case 0x4:
        if ((op & 15) == 0xf) { mac_w(x, n, m); return K_NONE; }
        switch (op & 0xff) {
        case 0x00: T_SET(R[n] >> 31); R[n] <<= 1; return K_NONE;
        case 0x01: T_SET(R[n] & 1); R[n] >>= 1; return K_NONE;
        case 0x02: R[n] -= 4; wr32(R[n], x->mach); return K_NONE;
        case 0x03: R[n] -= 4; wr32(R[n], SR); return K_NONE;
        case 0x04: T_SET(R[n] >> 31); R[n] = (R[n] << 1) | (R[n] >> 31); return K_NONE;
        case 0x05: T_SET(R[n] & 1); R[n] = (R[n] >> 1) | (R[n] << 31); return K_NONE;
        case 0x06: x->mach = rd32(R[n]); R[n] += 4; return K_NONE;
        case 0x07: t = rd32(R[n]); R[n] += 4; set_sr(t); return K_NONE;
        case 0x08: R[n] <<= 2; return K_NONE;
        case 0x09: R[n] >>= 2; return K_NONE;
        case 0x0a: x->mach = R[n]; return K_NONE;
        case 0x0b: x->pr = pc + 4u; x->tgt = R[n]; return K_CALL; /* jsr */
        case 0x0e: set_sr(R[n]); return K_NONE;
        case 0x10: R[n] -= 1; T_SET(R[n] == 0); return K_NONE;
        case 0x11: T_SET((s32)R[n] >= 0); return K_NONE;
        case 0x12: R[n] -= 4; wr32(R[n], x->macl); return K_NONE;
        case 0x13: R[n] -= 4; wr32(R[n], sat_cpu_cur()->gbr); return K_NONE;
        case 0x15: T_SET((s32)R[n] > 0); return K_NONE;
        case 0x16: x->macl = rd32(R[n]); R[n] += 4; return K_NONE;
        case 0x17: sat_cpu_cur()->gbr = rd32(R[n]); R[n] += 4; return K_NONE;
        case 0x18: R[n] <<= 8; return K_NONE;
        case 0x19: R[n] >>= 8; return K_NONE;
        case 0x1a: x->macl = R[n]; return K_NONE;
        case 0x1b: { u8 b = rd8(R[n]); T_SET(b == 0); wr8(R[n], (u8)(b | 0x80u)); return K_NONE; }
        case 0x1e: sat_cpu_cur()->gbr = R[n]; return K_NONE;
        case 0x20: T_SET(R[n] >> 31); R[n] <<= 1; return K_NONE;
        case 0x21: T_SET(R[n] & 1); R[n] = (u32)((s32)R[n] >> 1); return K_NONE;
        case 0x22: R[n] -= 4; wr32(R[n], x->pr); return K_NONE;
        case 0x23: R[n] -= 4; wr32(R[n], g_vbr); return K_NONE;
        case 0x24: t = T_GET; T_SET(R[n] >> 31); R[n] = (R[n] << 1) | t; return K_NONE;
        case 0x25: t = T_GET; T_SET(R[n] & 1); R[n] = (R[n] >> 1) | (t << 31); return K_NONE;
        case 0x26: x->pr = rd32(R[n]); R[n] += 4; return K_NONE;
        case 0x27: g_vbr = rd32(R[n]); R[n] += 4; return K_NONE;
        case 0x28: R[n] <<= 16; return K_NONE;
        case 0x29: R[n] >>= 16; return K_NONE;
        case 0x2a: x->pr = R[n]; return K_NONE;
        case 0x2b: x->tgt = R[n]; return K_JUMP; /* jmp */
        case 0x2e: g_vbr = R[n]; return K_NONE;
        }
        return K_FAULT;
    case 0x5: R[n] = rd32(R[m] + (op & 15) * 4u); return K_NONE;
    case 0x6:
        switch (op & 15) {
        case 0x0: R[n] = SX8(rd8(R[m])); return K_NONE;
        case 0x1: R[n] = SX16(rd16(R[m])); return K_NONE;
        case 0x2: R[n] = rd32(R[m]); return K_NONE;
        case 0x3: R[n] = R[m]; return K_NONE;
        case 0x4: t = SX8(rd8(R[m])); if (n != m) R[m] += 1; R[n] = t; return K_NONE;
        case 0x5: t = SX16(rd16(R[m])); if (n != m) R[m] += 2; R[n] = t; return K_NONE;
        case 0x6: t = rd32(R[m]); if (n != m) R[m] += 4; R[n] = t; return K_NONE;
        case 0x7: R[n] = ~R[m]; return K_NONE;
        case 0x8: R[n] = (R[m] & 0xffff0000u) | ((R[m] & 0xff) << 8) | ((R[m] >> 8) & 0xff); return K_NONE;
        case 0x9: R[n] = (R[m] << 16) | (R[m] >> 16); return K_NONE;
        case 0xa: { u32 b = R[m], tb = T_GET; R[n] = 0u - b - tb; T_SET(b != 0 || tb); return K_NONE; }
        case 0xb: R[n] = 0u - R[m]; return K_NONE;
        case 0xc: R[n] = R[m] & 0xffu; return K_NONE;
        case 0xd: R[n] = R[m] & 0xffffu; return K_NONE;
        case 0xe: R[n] = SX8(R[m]); return K_NONE;
        case 0xf: R[n] = SX16(R[m]); return K_NONE;
        }
        return K_FAULT;
    case 0x7: R[n] += SX8(op & 0xff); return K_NONE;
    case 0x8: {
        u32 d = op & 15, s8d = SX8(op & 0xff);
        switch ((op >> 8) & 15) {
        case 0x0: wr8(R[m] + d, (u8)R[0]); return K_NONE;
        case 0x1: wr16(R[m] + d * 2u, (u16)R[0]); return K_NONE;
        case 0x4: R[0] = SX8(rd8(R[m] + d)); return K_NONE;
        case 0x5: R[0] = SX16(rd16(R[m] + d * 2u)); return K_NONE;
        case 0x8: T_SET(R[0] == s8d); return K_NONE;
        case 0x9: if (T_GET) x->next = pc + 4u + s8d * 2u; return K_NONE;  /* bt */
        case 0xb: if (!T_GET) x->next = pc + 4u + s8d * 2u; return K_NONE; /* bf */
        case 0xd: x->tgt = T_GET ? pc + 4u + s8d * 2u : pc + 4u; return K_JUMP;  /* bt/s */
        case 0xf: x->tgt = !T_GET ? pc + 4u + s8d * 2u : pc + 4u; return K_JUMP; /* bf/s */
        }
        return K_FAULT;
    }
    case 0x9: R[n] = SX16(code16(pc + 4u + (op & 0xff) * 2u)); return K_NONE;
    case 0xa: x->tgt = pc + 4u + (u32)(((s32)(op << 20)) >> 19); return K_JUMP; /* bra */
    case 0xb: x->pr = pc + 4u; x->tgt = pc + 4u + (u32)(((s32)(op << 20)) >> 19); return K_CALL; /* bsr */
    case 0xc: {
        u32 d = op & 0xff, gbr = sat_cpu_cur()->gbr;
        switch ((op >> 8) & 15) {
        case 0x0: wr8(gbr + d, (u8)R[0]); return K_NONE;
        case 0x1: wr16(gbr + d * 2u, (u16)R[0]); return K_NONE;
        case 0x2: wr32(gbr + d * 4u, R[0]); return K_NONE;
        case 0x3: return K_FAULT; /* trapa: no BIOS vector table to run */
        case 0x4: R[0] = SX8(rd8(gbr + d)); return K_NONE;
        case 0x5: R[0] = SX16(rd16(gbr + d * 2u)); return K_NONE;
        case 0x6: R[0] = rd32(gbr + d * 4u); return K_NONE;
        case 0x7: R[0] = ((pc + 4u) & ~3u) + d * 4u; return K_NONE; /* mova */
        case 0x8: T_SET((R[0] & d) == 0); return K_NONE;
        case 0x9: R[0] &= d; return K_NONE;
        case 0xa: R[0] ^= d; return K_NONE;
        case 0xb: R[0] |= d; return K_NONE;
        case 0xc: T_SET((rd8(gbr + R[0]) & d) == 0); return K_NONE;
        case 0xd: wr8(gbr + R[0], (u8)(rd8(gbr + R[0]) & d)); return K_NONE;
        case 0xe: wr8(gbr + R[0], (u8)(rd8(gbr + R[0]) ^ d)); return K_NONE;
        case 0xf: wr8(gbr + R[0], (u8)(rd8(gbr + R[0]) | d)); return K_NONE;
        }
        return K_FAULT;
    }
    case 0xd: R[n] = code32(((pc + 4u) & ~3u) + (op & 0xff) * 4u); return K_NONE;
    case 0xe: R[n] = SX8(op & 0xff); return K_NONE;
    }
    return K_FAULT; /* 0xf...: FPU, absent on the SH-2 */
}

/* A branch or call from interpreted code to an address whose translation is of other code (an overlay's: gen_saturn.py
 * --guard, the translation's first 16 bytes are not in memory) is declined: the translated function's guard reaches
 * sat_absent at once, which hands it back here (sat_interp_declines) instead of starting a nested interpreter, and the
 * code that is there runs on in this run. Before, each such branch was a call out that never returned to its loop:
 * Dragon Force's battle (DF.BIN over /0 at 06010000) loops back with bf/s to 06010cb0, the start of a /0 function, so
 * every turn of the loop nested one more frame until "call depth over 400" at vertical blank 12,120 (2026-11-17).
 * Addresses declined are remembered with the 16 bytes that were there, so a loop pays for the call out once. */
static u32 g_decline_at[2];
static int g_decline_depth[2], g_declined[2]; /* per SH-2: the other CPU's turn can come inside a call out */
enum { NORES = 64 };
static struct { u32 addr, w[4]; } g_nores[NORES];
static u32 g_ndeclined;
int sat_interp_declines(u32 addr)
{
    int id = sat_cpu_id() & 1;
    if (!g_decline_at[id] || (addr & 0xdfffffffu) != g_decline_at[id] || sat_cpu_cur()->depth != g_decline_depth[id]) return 0;
    g_decline_at[id] = 0, g_declined[id] = 1;
    return 1;
}
void sat_interp_resident_seen(void) { g_decline_at[sat_cpu_id() & 1] = 0; } /* the translation's guard held: its body runs */
static int nores_hit(u32 t)
{
    int i = (int)((t >> 1) & (NORES - 1));
    return g_nores[i].addr == t && code32(t) == g_nores[i].w[0] && code32(t + 4u) == g_nores[i].w[1] && code32(t + 8u) == g_nores[i].w[2] &&
           code32(t + 12u) == g_nores[i].w[3];
}
static void nores_note(u32 t)
{
    int i = (int)((t >> 1) & (NORES - 1));
    g_nores[i].addr = t;
    for (int k = 0; k < 4; k++) g_nores[i].w[k] = code32(t + 4u * (u32)k); /* RAM, not the bus (see code_at) */
}

/* a call from interpreted code to translated C (or a BIOS service): r4..r7, the stack words and r0..r3 as TcArgs.
 * Returns 0 when the translation there was declined (not the code in memory): nothing ran, the interpreter runs it */
static int call_out(Ctx *x, u32 target)
{
    SatCpu *c = sat_cpu_cur();
    TcArgs a;
    u32 t = target & 0xdfffffffu;
    if (nores_hit(t)) return 0;
    memset(&a, 0, sizeof a);
    for (int i = 0; i < 4; i++) a.r[i] = x->R[4 + i], a.rlo[i] = x->R[i];
    for (int i = 0; i < 8; i++) a.s[i] = rd32(x->R[15] + 4u * (u32)i);
    for (int i = 1; i < 15; i++) c->r[i] = x->R[i];
    c->sp = x->R[15];
    int id = sat_cpu_id() & 1;
    g_decline_at[id] = t, g_decline_depth[id] = c->depth + 1, g_declined[id] = 0;
    sat_ret = x->pr == SENT ? SAT_PR_NONE : x->pr; /* the PR the SH-2 hands the callee (a call: the address after it) */
    u32 r = tc_call_sp(target, &a, x->R[15]);
    int declined = g_declined[id];
    g_decline_at[id] = 0, g_declined[id] = 0;
    if (declined) {
        nores_note(t);
        if (++g_ndeclined <= 8) port_log("FALLBACK DECLINE: %08x's translation is not the code in memory; the interpreter runs it\n", t);
        return 0;
    }
    c = sat_cpu_cur();
    for (int i = 1; i < 15; i++) x->R[i] = c->r[i];
    x->R[0] = r;
    return 1;
}

static int translated(u32 t)
{
    if (g_pure) { /* pure: translated code is interpreted too; a BIOS service is outside the test */
        if (sat_bios_native(t & 0xdfffffffu)) g_pure_bad = 1;
        return 0;
    }
    const SatFn *f = sat_find_fn(t & 0xdfffffffu);
    if (f) return sat_fn_resident(f); /* an overlay's translation whose code is not in memory now: the interpreter runs what is */
    return sat_bios_native(t & 0xdfffffffu) != 0;
}

/* Runs the untranslated code at `entry` as a function: it returns when the code returns to its caller (rts to the
 * sentinel pr). top is the entry r15; with stack_args the TcArgs' 8 stack words are stored there first, as run_frame does. */
u32 sat_interp(u32 entry, u32 top, const TcArgs *a, int stack_args)
{
    SatCpu *c = sat_cpu_cur();
    Ctx x;
    FbRow *row = fb_note(entry, 0);
    fb_u64 n0 = g_steps;
    int depth = 0, tail = g_entry_tail; /* depth: calls made inside the interpreted code that are still open */
    u32 *ftop = !g_pure && c->depth >= 0 && c->depth <= MAXDEPTH + 1 ? &c->ftop[c->depth] : NULL, had = ftop ? *ftop & 2u : 0u;
    g_entry_tail = 0;
    if (ftop) *ftop |= 2u; /* this frame runs interpreted: a save state resumes it so (sat_interp_resume) */
    memset(&x, 0, sizeof x);
    for (int i = 0; i < 4; i++) x.R[i] = a->rlo[i], x.R[4 + i] = a->r[i];
    for (int i = 8; i < 15; i++) x.R[i] = c->r[i];
    x.R[15] = top;
    x.pr = SENT;
    if (stack_args)
        for (int i = 0; i < 8; i++) wr32(top + 4u * (u32)i, a->s[i]);
    u32 pc = entry & 0xdfffffffu;
    for (;;) {
        if (!sat_fallback_ok(pc)) {
            fault(pc, 0, "code outside work RAM");
            break;
        }
        sat_cpu_cur()->sp = x.R[15]; /* an interrupt's frame goes below the interpreted code's stack */
        u32 op = fetch(pc);
        int k = step(&x, op, pc);
        if (g_pure) {
            if (g_pure_bad || k == K_SLEEP || ++g_steps - n0 > 4000000u) {
                g_pure_bad = 1;
                break;
            }
        } else if ((++g_steps & (SPIN_STEPS - 1)) == 0)
            tc_spin(); /* a frame of SH-2 time has passed */
        if (k == K_NONE) {
            pc = x.next;
            continue;
        }
        if (k == K_FAULT) {
            fault(pc, op, (op >> 8) == 0xc3 ? "trapa" : "an instruction the SH-2 lacks");
            break;
        }
        if (k == K_SLEEP) {
            tc_spin();
            pc += 2u;
            continue;
        }
        if (k == K_RTE && depth == 0) { /* an interrupt handler's return: sat_call_here pushed no frame and restores sr */
            step(&x, fetch(pc + 2u), pc + 2u);
            break;
        }
        if (k == K_RTE) { /* pc and sr from the stack, the delay slot, then the return */
            u32 npc = rd32(x.R[15]), nsr = rd32(x.R[15] + 4u);
            x.R[15] += 8u;
            step(&x, fetch(pc + 2u), pc + 2u);
            set_sr(nsr);
            pc = npc;
            continue;
        }
        u32 tgt = x.tgt, slot = fetch(pc + 2u);
        if (step(&x, slot, pc + 2u) != K_NONE) {
            fault(pc + 2u, slot, "a branch in a delay slot");
            break;
        }
        g_steps++;
        if (k == K_RET) {
            if (tgt == SENT) break;
            if (depth > 0) {
                depth--;
                pc = tgt;
                continue;
            }
            /* a return with no call of ours open, to a pr that came off the stack: the code returns for the frame below
             * (a jump from an open frame returns for its jumper; a called routine that pops its caller's pr returns past
             * it, which translated code marks with sat_pr_popped). Translated C carries on from here. */
            if (g_pure) {
                g_pure_bad = 1;
                break;
            }
            if (++g_past <= 16)
                port_log("FALLBACK RETURN: code entered at %08x returned to %08x, a pr off the stack (%s)\n", entry, tgt,
                         tail ? "for the open frame that jumped to it" : "past its caller: the call site sees a popped frame");
            if (!tail) sat_pr_popped();
            break;
        }
        if (translated(tgt) && call_out(&x, tgt)) { /* translated C wins: a call comes back here, a jump returns to our pr */
            if (k == K_CALL) {
                pc += 4u;
                continue;
            }
            if (x.pr == SENT) break;
            pc = x.pr;
            continue;
        }
        if (!sat_fallback_ok(tgt)) {
            fault(pc, op, "a branch outside work RAM");
            break;
        }
        if (k == K_CALL) {
            fb_note(tgt, 1);
            depth++;
        }
        pc = tgt;
    }
    c = sat_cpu_cur();
    for (int i = 1; i < 15; i++) c->r[i] = x.R[i]; /* r1..r3 handed on as exit registers, r8..r14 as shared state */
    if (row) row->steps += g_steps - n0;
    if (ftop) *ftop = (*ftop & ~2u) | had;
    return x.R[0];
}

int sat_fallback_count(void) { return g_nfb; }

/* --fallback check: runs translated code's guest copy from the state the caller set up (sat_call.c has saved and restored
 * everything around it); *ok is 0 when the run left work RAM or could not finish here, so there is nothing to compare */
u32 sat_interp_pure(u32 entry, u32 top, const TcArgs *a, int *ok)
{
    fb_u64 s0 = g_steps;
    int f0 = g_faults;
    u32 r, vbr = g_vbr;
    g_pure = 1, g_pure_bad = 0;
    r = sat_interp(entry, top, a, 1);
    *ok = !g_pure_bad;
    g_pure = 0, g_steps = s0, g_faults = f0, g_vbr = vbr;
    return r;
}

void sat_fallback_report(void)
{
    u32 entries = 0;
    for (int i = 0; i < g_nfb; i++) entries += g_fb[i].entries;
    port_log("fallbacks: %d address(es), %u entries, %llu instructions interpreted, %d fault(s), %u return(s) past the caller%s\n", g_nfb,
             entries, g_steps, g_faults, g_past, sat_fallback_on ? "" : " (--fallback off)");
    if (sat_fallback_on == 2) sat_check_report();
    for (int i = 0; i < g_nfb; i++)
        port_log("fallback: %08x entries %u instructions %llu%s from %08x < %08x < %08x < %08x\n", g_fb[i].addr, g_fb[i].entries,
                 g_fb[i].steps, g_fb[i].nested ? " nested" : "", g_fb[i].chain[0], g_fb[i].chain[1], g_fb[i].chain[2], g_fb[i].chain[3]);
}
/* ---- save states: the frames open at the safe point, resumed (sat_state.c) */
u32 sat_fn_frame(u32 addr); /* sat_call.c */
static u32 g_resume_odd;
static void resume_stop(u32 pc, u32 op, const char *why)
{
    port_log("state: the resumed frames stopped: %s at %08x (op %04x)\n", why, pc, op);
    sat_where();
    port_exit("a resumed save state met code it cannot run", 1);
}

/* frame k's own sp and entry sp, as run_frame gave the translated function */
static void resume_frame(SatCpu *c, int k)
{
    u32 top = c->ftop[k] & ~3u;
    c->sp = top - sat_fn_frame(k - 1 < 512 ? c->stack[k - 1] : 0u);
    sat_entry_sp = top;
}

/* a call from a resumed frame, made as the frame made it: r4..r7, r0..r3, and with the caller's r15 (its 8 stack words
 * read there) when the function passed it (ftop bit 0 of the frame its calls open; interpreted code always does, as
 * call_out), else from its own sp */
static void resume_call(Ctx *x, u32 target, int k, int interp)
{
    SatCpu *c = sat_cpu_cur();
    TcArgs a;
    u32 r;
    int sp_given = interp || !(c->ftop[k + 1] & 1u);
    memset(&a, 0, sizeof a);
    for (int i = 0; i < 4; i++) a.r[i] = x->R[4 + i], a.rlo[i] = x->R[i];
    if (sp_given)
        for (int i = 0; i < 8; i++) a.s[i] = rd32(x->R[15] + 4u * (u32)i);
    for (int i = 1; i < 15; i++) c->r[i] = x->R[i];
    sat_ret = x->pr == SENT ? SAT_PR_NONE : x->pr; /* the return address the SH-2's call hands the callee */
    if (interp) c->sp = x->R[15];
    if (sp_given) r = tc_call_sp(target, &a, x->R[15]);
    else {
        resume_frame(c, k);
        r = tc_call(target, &a);
    }
    c = sat_cpu_cur();
    for (int i = 1; i < 15; i++) x->R[i] = c->r[i];
    x->R[0] = r;
}

/* After a load the call pending at the safe point has run (r0: its result) and the frames that were open carry on from
 * its return address, here, one flat run over their code. A translated frame's call goes out as the translated function
 * made it (so a translated callee runs native, and the call counts, the interrupt polls and the guest stack are the ones
 * translated code had); its jump stays here, as a translated jump stays in its frame; its return pops the frames up to
 * the one whose return address it took, as the CPU does; no steps are counted (translated code had none). A frame that
 * ran in the interpreter (ftop bit 1) runs as sat_interp ran it: its steps counted, a call or jump to translated code
 * out through call_out's way, others in place, a return to anything but the sentinel its own, the sentinel its frame's.
 * Ends when the outermost frame returns. A load during the run leaves it (sat_state.c). */
u32 sat_interp_resume(u32 r0, u32 pc, u32 csp)
{
    SatCpu *c = sat_cpu_cur();
    Ctx x;
    int k = c->depth, interp = (c->ftop[k] & 2u) != 0;
    memset(&x, 0, sizeof x);
    x.R[0] = r0;
    for (int i = 1; i < 15; i++) x.R[i] = c->r[i];
    resume_frame(c, k);
    x.R[15] = csp ? csp : c->sp; /* the caller's r15 at the pending call: given, or its frame's sp (sat_call_unsafe) */
    x.pr = pc;
    port_log("state: resuming %d frame(s) at %08x\n", k, pc);
    while (k > 0) {
        if (!sat_fallback_ok(pc)) resume_stop(pc, 0, "code outside work RAM");
        if ((sat_interp_depth[(c - g_cpu) & 1] = interp ? (u32)k : 0u) != 0u) sat_cpu_cur()->sp = x.R[15]; /* and the sampler's mark */
        u32 op = fetch(pc), tgt;
        int kd = step(&x, op, pc);
        if (interp && (++g_steps & (SPIN_STEPS - 1)) == 0) tc_spin(); /* as sat_interp counts */
        if (kd == K_NONE) {
            pc = x.next;
            continue;
        }
        if (kd == K_FAULT) resume_stop(pc, op, (op >> 8) == 0xc3 ? "trapa" : "an instruction the SH-2 lacks");
        if (kd == K_SLEEP) {
            tc_spin();
            pc += 2u;
            continue;
        }
        if (kd == K_RTE) {
            u32 npc = rd32(x.R[15]), nsr = rd32(x.R[15] + 4u);
            x.R[15] += 8u;
            step(&x, fetch(pc + 2u), pc + 2u);
            set_sr(nsr);
            pc = npc;
            continue;
        }
        tgt = x.tgt;
        if (step(&x, fetch(pc + 2u), pc + 2u) != K_NONE) resume_stop(pc + 2u, fetch(pc + 2u), "a branch in a delay slot");
        if (interp) g_steps++;
        if (interp && kd != K_RET) { /* sat_interp's way: translated code is called out to, the rest runs in place */
            if (translated(tgt)) {
                resume_call(&x, tgt, k, 1);
                if (kd == K_CALL) {
                    pc += 4u;
                    continue;
                }
                tgt = x.pr; /* a jump to translated code returns for this code */
            } else {
                if (!sat_fallback_ok(tgt)) resume_stop(pc, op, "a branch outside work RAM");
                pc = tgt;
                continue;
            }
        } else if (kd == K_CALL) {
            resume_call(&x, tgt, k, 0);
            pc += 4u;
            continue;
        } else if (kd == K_JUMP) {
            if (sat_fallback_ok(tgt)) {
                pc = tgt;
                continue;
            }
            resume_call(&x, tgt, k, 0); /* a jump to a BIOS service: it runs, then returns for this frame */
            tgt = x.pr;
        }
        int j = k; /* a return: the frames up to the one whose caller's call handed it this address */
        if (tgt == SENT) { /* an interpreted frame returns: its caller carries on where its call handed it */
            tgt = c->site[k];
            x.pr = tgt;
        } else {
            while (j >= 1 && c->site[j] != tgt) j--;
            while (j > 1 && c->site[j - 1] == tgt) j--; /* code jumped to from an open frame returns for the jumper too */
            if (j < 1 && interp) { /* a return inside the interpreted code */
                pc = tgt;
                continue;
            }
            if (j < 1) {
                if (++g_resume_odd <= 8) port_log("state: a resumed frame returned to %08x, no frame's return address: frame %d returns\n", tgt, k);
                j = k;
            }
        }
        if (j >= 2 && (c->ftop[j] & 1u)) x.R[15] = c->ftop[j - 1] & ~3u; /* its caller's r15 was its own entry sp */
        c->last_top = c->ftop[j] & ~3u;
        k = j - 1;
        c->depth = k;
        if (k > 0) resume_frame(c, k);
        interp = k > 0 && (c->ftop[k] & 2u) != 0;
        pc = tgt;
    }
    for (int i = 1; i < 15; i++) c->r[i] = x.R[i];
    return x.R[0];
}

void sat_interp_loaded(void)
{
    g_entry_tail = 0, g_pure = 0;
    g_decline_at[0] = g_decline_at[1] = 0, g_declined[0] = g_declined[1] = 0;
}

void sat_interp_state(SatSt *io) { sat_st(io, &g_vbr, 4), sat_st(io, &g_steps, sizeof g_steps); }
#undef rd32
#undef rd16
#undef rd8
#undef wr32
#undef wr16
#undef wr8
