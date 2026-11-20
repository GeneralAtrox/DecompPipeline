/* The SH-2 interpreter fallback (port/saturn/sat_interp.c) through the kit's real call machinery (sat_call.c, included
 * whole): a translated function calls code that has no translated function. With --fallback off that is a STUB that
 * returns 0 (the kit before the change); with it on the interpreter runs the planted SH-2 code on the shared CPU state,
 * calls a translated function from it (which must win), calls nested untranslated code, and hands back r0 and r8..r14.
 * Also checks DIV0U/DIV1 (a 32-step unsigned division) and DMULS.L, and --fallback check: a translated function whose guest
 * code does the same agrees, one whose C writes memory its guest code does not is a DIFF. Run by tests/test_sat_interp.py. */
#define _CRT_SECURE_NO_WARNINGS
#include <stdarg.h>
#include <stdio.h>
#include "sat_call.c"
#include "sat_interp.c"

static int g_fail;
static u8 g_ram[0x100000], g_raml[0x100000];
u8 *sat_ram_h(void) { return g_ram; }
u8 *sat_ram_l(void) { return g_raml; }
#define RAM 0x06000000u
#define UNT 0x06001000u
#define DIVF 0x06001100u
#define DMUL 0x06001200u
#define TR 0x06002000u
#define MAINF 0x06003000u
#define GUARDF 0x06001300u
#define BADF 0x06002100u
#define MAIN2 0x06003100u
#define LOOPF 0x060014feu /* untranslated: a loop whose bf/s goes back to GUARD2, the start of another overlay's translation */
#define GUARD2 0x06001500u
#define OVA 0x06001600u /* two overlays' translations of one address (gen_saturn.py --overlay) */

/* stand-ins for the kit functions sat_call.c and sat_interp.c reach */
int g_work_on;
u64 g_work_now[WC_N];
int sat_task_on, sat_menus_on;
unsigned char *sat_task_flags;
u32 sat_logo_fn, sat_movie_fn;
static int g_log;
void port_log(const char *fmt, ...)
{
    va_list ap;
    if (g_log++ > 40) return;
    va_start(ap, fmt);
    vprintf(fmt, ap);
    va_end(ap);
}
void port_exit(const char *why, int code)
{
    printf("FAIL: port_exit %s\n", why);
    exit(code ? code : 1);
}
static u8 *at(u32 a)
{
    a &= 0xdfffffffu;
    return a - RAM < sizeof g_ram - 3u ? &g_ram[a - RAM] : NULL;
}
u32 sat_peek32(u32 a)
{
    u8 *p = at(a);
    return p ? (u32)p[0] << 24 | (u32)p[1] << 16 | (u32)p[2] << 8 | p[3] : 0;
}
u32 rd32(u32 a) { return sat_peek32(a); }
static u32 g_bus_reads;
u16 rd16(u32 a)
{
    u8 *p = at(a);
    g_bus_reads++;
    return p ? (u16)(p[0] << 8 | p[1]) : 0;
}
u8 rd8(u32 a)
{
    u8 *p = at(a);
    return p ? p[0] : 0;
}
void wr32(u32 a, u32 v)
{
    u8 *p = at(a);
    if (p) p[0] = (u8)(v >> 24), p[1] = (u8)(v >> 16), p[2] = (u8)(v >> 8), p[3] = (u8)v;
}
void wr16(u32 a, u16 v)
{
    u8 *p = at(a);
    if (p) p[0] = (u8)(v >> 8), p[1] = (u8)v;
}
void wr8(u32 a, u8 v)
{
    u8 *p = at(a);
    if (p) p[0] = v;
}
void sat_where(void) {}
void sat_mem_cpu(int id) { (void)id; }
void sat_mem_icf(int cpu) { (void)cpu; }
void sat_perf_poll(void) {}
void sat_irq_poll(void) {}
void sat_spin(void) {}
u32 sat_vblank_count(void) { return 0; }
void sat_task_cpu_off(int cpu) { (void)cpu; }
u32 sat_task_run(const SatFn *f, u32 top, const TcArgs *a) { (void)f, (void)top, (void)a; return 0; }
/* a task longjmp the kit models (sat_task.c's g_lj) with untranslated guest code at it: the hook must take it */
#define TASK_LJ 0x06001400u
static u32 g_task_calls;
int sat_task_call(u32 target, const TcArgs *a, u32 *ret)
{
    (void)a;
    if (target != TASK_LJ) return 0;
    g_task_calls++;
    *ret = 0x7a5cu;
    return 1;
}
SatNative sat_bios_native(u32 addr) { (void)addr; return 0; }
int sat_logos_skip(const TcArgs *a, u32 *ret) { (void)a, (void)ret; return 0; }
int sat_movie_play(const TcArgs *a, u32 *ret) { (void)a, (void)ret; return 0; }
int sat_menus_enter(u32 fn, const TcArgs **a, TcArgs *tmp, u32 *ret) { (void)fn, (void)a, (void)tmp, (void)ret; return 0; }
const TcArgs *sat_feat_enter(u32 fn, const TcArgs *a, SatFeat *t) { (void)fn, (void)t; return a; }
u32 sat_feat_leave(u32 fn, const TcArgs *a, u32 r) { (void)fn, (void)a; return r; }
void sat_saved_note(u32 addr, const u32 keep[7]) { (void)addr, (void)keep; }

static u32 g_tr_r8, g_tr_calls;
/* the translated function the interpreted code calls: r4 + r5 + 1000, and it notes the shared r8 it was handed */
static u32 fn_tr(u32 a0, u32 a1, u32 a2, u32 a3)
{
    (void)a2, (void)a3;
    g_tr_calls++;
    g_tr_r8 = sh4_r(8);
    return a0 + a1 + 1000u;
}
/* a translation of overlay code (gen_saturn.py --guard): the code in RAM now is another overlay's, so it is absent */
static u32 fn_guard(u32 a0, u32 a1, u32 a2, u32 a3)
{
    (void)a1, (void)a2, (void)a3;
    if (!sat_resident(GUARDF, 0x11111111u, 0x22222222u, 0x33333333u, 0x44444444u)) return sat_absent(GUARDF);
    return a0 + 999u;
}
/* another overlay's translation at GUARD2 (Dragon Force: /0's 06010cb0), whose bytes are never in memory here: in RAM
 * GUARD2 is the top of LOOPF's loop (DF.BIN's bf/s back to 06010cb0) */
static u32 fn_guard2(u32 a0, u32 a1, u32 a2, u32 a3)
{
    (void)a1, (void)a2, (void)a3;
    if (!sat_resident(GUARD2, 0x55555555u, 0x66666666u, 0x77777777u, 0x88888888u)) return sat_absent(GUARD2);
    return a0 + 777u;
}
static const u32 g_ova_a[4] = {0xe07a000bu, 0x00090009u, 0xa2a2a2a2u, 0xa3a3a3a3u};
static const u32 g_ova_b[4] = {0xe07b000bu, 0x00090009u, 0xb2b2b2b2u, 0xb3b3b3b3u};
static u32 fn_ova_a(u32 a0, u32 a1, u32 a2, u32 a3)
{
    (void)a1, (void)a2, (void)a3;
    if (!sat_resident(OVA, g_ova_a[0], g_ova_a[1], g_ova_a[2], g_ova_a[3])) return sat_absent(OVA);
    return a0 + 0xa00u;
}
static u32 fn_ova_b(u32 a0, u32 a1, u32 a2, u32 a3)
{
    (void)a1, (void)a2, (void)a3;
    if (!sat_resident(OVA, g_ova_b[0], g_ova_b[1], g_ova_b[2], g_ova_b[3])) return sat_absent(OVA);
    return a0 + 0xb00u;
}
/* --fallback check: its C writes a word its guest code (mov r4,r0; add #1,r0; rts) does not: a DIFF */
static u32 fn_bad(u32 a0, u32 a1, u32 a2, u32 a3)
{
    (void)a1, (void)a2, (void)a3;
    wr32(0x06008000u, a0);
    return a0 + 1u;
}
static u32 g_m2[2];
static u32 fn_main2(u32 a0, u32 a1, u32 a2, u32 a3)
{
    TcArgs a;
    (void)a0, (void)a1, (void)a2, (void)a3;
    memset(&a, 0, sizeof a);
    a.r[0] = 5, a.r[1] = 6;
    g_m2[0] = tc_call(TR, &a);
    a.r[0] = 9;
    g_m2[1] = tc_call(BADF, &a);
    return 0;
}
static u32 g_main_ret[4];
static u32 fn_main(u32 a0, u32 a1, u32 a2, u32 a3)
{
    TcArgs a;
    (void)a0, (void)a1, (void)a2, (void)a3;
    memset(&a, 0, sizeof a);
    a.r[0] = 3, a.r[1] = 4, a.r[2] = 5;
    g_main_ret[0] = tc_call(UNT, &a);
    a.r[0] = 1000000u, a.r[1] = 7u;
    g_main_ret[1] = tc_call(DIVF, &a);
    a.r[0] = (u32)-3, a.r[1] = 0x40000000u;
    g_main_ret[2] = tc_call(DMUL, &a);
    a.r[0] = 41u;
    g_main_ret[3] = tc_call(GUARDF, &a);
    return g_main_ret[0];
}
const SatFn sat_fns[] = {{GUARDF, fn_guard, 0}, {GUARD2, fn_guard2, 0}, {OVA, fn_ova_a, 0, g_ova_a}, {OVA, fn_ova_b, 0, g_ova_b}, {TR, fn_tr, 0}, {BADF, fn_bad, 0}, {MAINF, fn_main, 64}, {MAIN2, fn_main2, 64}};
const int sat_nfns = 8;

static void put(u32 addr, const u16 *ops, int n)
{
    for (int i = 0; i < n; i++) wr16(addr + 2u * (u32)i, ops[i]);
}

static void plant(void)
{
    /* UNT(a, b, c): r8 = a (saved and restored), r0 = a * b by a loop, r0 = TR(r0, c) (translated), r0 = NEST(r0) = r0 * 2 */
    static const u16 unt[] = {0x4f22, 0x2f86, 0x6843, 0xe000, 0x6153, 0x2118, 0x8903, 0x308c, 0x4110, 0x8bfc, 0x0009,
                              0x6403, 0xd104, 0x410b, 0x6563, 0xb007, 0x6403, 0x68f6, 0x4f26, 0x000b, 0x0009, 0x0009,
                              0x0000, 0x0000, /* 0600102c: .long TR */ 0x6043, 0x000b, 0x4000};
    static const u16 dmul[] = {0x354d, 0x000a, 0x000b, 0x0009};
    static const u16 guard[] = {0x7401, 0x6043, 0x000b, 0x0009}; /* the other overlay's code: add #1,r4; mov r4,r0; rts; nop */
    static const u16 tr[] = {0x6043, 0x305c, 0x9102, 0x301c, 0x000b, 0x0009, 0x03e8}; /* TR's guest code: r4 + r5 + 1000 */
    static const u16 bad[] = {0x6043, 0x7001, 0x000b, 0x0009};                         /* BADF's: r4 + 1, no store */
    u16 div[2 + 64 + 4];
    int n = 0;
    memset(g_ram, 0, sizeof g_ram);
    put(UNT, unt, (int)(sizeof unt / sizeof unt[0]));
    wr32(0x0600102cu, TR);
    div[n++] = 0xe100, div[n++] = 0x0019; /* mov #0,r1; div0u */
    for (int i = 0; i < 32; i++) div[n++] = 0x4424, div[n++] = 0x3154; /* rotcl r4; div1 r5,r1 */
    div[n++] = 0x4424, div[n++] = 0x6043, div[n++] = 0x000b, div[n++] = 0x0009; /* rotcl r4; mov r4,r0; rts; nop */
    put(DIVF, div, n);
    put(DMUL, dmul, 4);
    put(GUARDF, guard, 4);
    put(TR, tr, 7);
    put(BADF, bad, 4);
    {   /* LOOPF(n): mov #0,r0; GUARD2: add #1,r0; dt r4; bf/s GUARD2; nop; rts; nop: r0 = n */
        static const u16 loop[] = {0xe000, 0x7001, 0x4410, 0x8ffc, 0x0009, 0x000b, 0x0009};
        put(LOOPF, loop, 7);
    }
}

static void check(int ok, const char *what)
{
    printf("%s: %s\n", ok ? "ok  " : "FAIL", what);
    if (!ok) g_fail = 1;
}

int main(void)
{
    sat_call_init();
    plant();
    g_cpu[0].r[8] = 0x88u;
    sat_fallback_option("off");
    sat_call(0x0600f000u, MAINF, 0, 0, 0, 0);
    check(g_main_ret[0] == 0 && g_main_ret[3] == 0 && sat_fallback_count() == 0,
          "--fallback off: the missing function and the absent overlay translation are STUBs that return 0");
    plant();
    sat_fallback_option("on");
    sat_call(0x0600f000u, MAINF, 0, 0, 0, 0);
    check(g_main_ret[0] == 2034u, "the planted untranslated function runs in the interpreter: (3*4 + 5 + 1000) * 2 = 2034");
    check(g_tr_calls == 1 && g_tr_r8 == 3u, "its call to a translated function goes to the translated C, with the shared r8 it set");
    check(g_cpu[0].r[8] == 0x88u, "r8 pushed and popped by the interpreted code comes back to the caller unchanged");
    check(g_main_ret[1] == 142857u, "DIV0U and 32 DIV1 steps: 1000000 / 7 = 142857");
    check(g_main_ret[2] == 0xffffffffu, "DMULS.L: the high word of -3 * 0x40000000");
    check(g_main_ret[3] == 42u, "a guarded translation whose overlay is not in RAM runs the code that is there: 41 + 1");
    check(sat_fallback_count() == 5, "five addresses counted: three called from translated code, one nested, one absent overlay");
    check(g_cpu[0].depth == 0, "the call depth is back to 0");
    check(g_bus_reads == 0, "no instruction fetch or pc-relative constant went through the bus (planted code reads no 16-bit data)");
    {
        TcArgs ta = {{0}};
        int n0 = sat_fallback_count();
        u32 tr;
        wr32(TASK_LJ, 0x000b0009u); /* rts; nop: what the interpreter would run */
        sat_task_on = 1;
        tr = tc_tail_sp(TASK_LJ, &ta, 0x0600e000u);
        sat_task_on = 0;
        check(tr == 0x7a5cu && g_task_calls == 1u && sat_fallback_count() == n0,
              "a jump from an open frame to the task longjmp goes to the task hook, not the interpreter (Saturn Bomberman 060061e6)");
    }
    {
        int n0 = sat_fallback_count();
        u32 r = sat_call(0x0600f000u, LOOPF, 500u, 0, 0, 0);
        check(r == 500u && g_cpu[0].depth == 0 && sat_fallback_count() == n0 + 1,
              "a 500-turn interpreted loop whose branch target is another overlay's translation (its guard fails) runs on in "
              "the interpreter: r0 500, depth back to 0, one address counted (Dragon Force's battle: depth over 400 at vb 12120)");
    }
    {   /* the battle's overlay is in memory, not the field's: its translation runs, then the other's */
        u32 ra, rb;
        for (int k = 0; k < 4; k++) wr32(OVA + 4u * (u32)k, g_ova_b[k]);
        rb = sat_call(0x0600f000u, OVA, 1u, 0, 0, 0);
        for (int k = 0; k < 4; k++) wr32(OVA + 4u * (u32)k, g_ova_a[k]);
        ra = sat_call(0x0600f000u, OVA, 1u, 0, 0, 0);
        check(rb == 0xb01u && ra == 0xa01u, "two overlays' translations of one address: the one whose bytes are in memory runs");
    }
    sat_fallback_report();
    plant();
    sat_fallback_option("check");
    sat_call(0x0600f000u, MAIN2, 0, 0, 0, 0);
    {
        u32 n[5];
        sat_check_counts(n);
        check(g_m2[0] == 1011u && g_m2[1] == 10u && rd32(0x06008000u) == 9u, "--fallback check: the game keeps the translated results");
        check(n[0] == 1u && n[1] == 1u && n[2] == 0u && n[3] == 0u,
              "--fallback check: TR's guest code agrees with its C, BADF's C writes a word its guest code does not (one DIFF)");
        sat_check_report();
    }
    printf(g_fail ? "FAIL\n" : "all ok\n");
    return g_fail;
}
