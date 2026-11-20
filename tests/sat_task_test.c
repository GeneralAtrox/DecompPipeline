/* Guest task switching by setjmp/longjmp (sat_task.c, included whole for its static functions). KRNL's own setjmp and
 * longjmp words (Saturn Bomberman 060061c4/060061e6) sit in a small guest RAM; a yield-like function that the kit marks
 * pushes its caller's r13, sets r13 to a pointer of its own, saves with the setjmp and is resumed by a longjmp on the
 * same buffer, as KRNL's 06006d36 is by its scheduler. The caller must get its r13 back from the resumed function's
 * epilogue, and the setjmp must return 0 then the longjmp's value. Run by tests/test_sat_task.py. */
#define _CRT_SECURE_NO_WARNINGS
#include <stdio.h>
#include <string.h>
#include "sat_task.c"

static int g_fail;
static u8 g_ram[0x10000];
#define RAM 0x06000000u
#define SJ 0x060061c4u
#define LJ 0x060061e6u
#define YIELD 0x06006300u
#define MAINF 0x06006400u
#define BUF 0x06008000u
#define STK 0x0600f000u

/* a stand-in for each kit function sat_task.c calls */
SatCpu g_cpu[2];
u32 sat_entry_sp;
int sat_cpu_id(void) { return 0; }
void port_log(const char *fmt, ...) { (void)fmt; }
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
void wr32(u32 a, u32 v)
{
    u8 *p = at(a);
    if (p) p[0] = (u8)(v >> 24), p[1] = (u8)(v >> 16), p[2] = (u8)(v >> 8), p[3] = (u8)v;
}
static void put16(u32 a, u16 v)
{
    u8 *p = at(a);
    if (p) p[0] = (u8)(v >> 8), p[1] = (u8)v;
}

static u32 fn_yield(u32 a0, u32 a1, u32 a2, u32 a3);
static u32 fn_main(u32 a0, u32 a1, u32 a2, u32 a3);
static u32 fn_none(u32 a0, u32 a1, u32 a2, u32 a3)
{
    (void)a0, (void)a1, (void)a2, (void)a3;
    return 0;
}
const SatFn sat_fns[] = {{SJ, fn_none, 0}, {YIELD, fn_yield, 16}, {MAINF, fn_main, 16}};
const int sat_nfns = 3;

/* sat_call.c's tc_call, cut down: the task hook first, then the callee in its frame (sat_task_run when marked) */
u32 tc_call(u32 target, const TcArgs *a)
{
    SatCpu *c = &g_cpu[0];
    u32 r;
    int i;
    if (sat_task_on && sat_task_call(target & 0xdfffffffu, a, &r)) return r;
    for (i = 0; i < sat_nfns && sat_fns[i].addr != target; i++) {}
    if (i == sat_nfns) {
        printf("FAIL: call to %08x\n", target);
        g_fail = 1;
        return 0;
    }
    c->stack[c->depth++] = target;
    c->sp -= 32u;
    for (int k = 0; k < 4; k++) c->r[4 + k] = a->r[k];
    r = sat_task_flags[i] ? sat_task_run(&sat_fns[i], c->sp, a) : sat_fns[i].fn(a->r[0], a->r[1], a->r[2], a->r[3]);
    c = &g_cpu[0];
    c->sp += 32u, c->depth--;
    return r;
}
u32 sat_call(u32 sp, u32 addr, u32 a0, u32 a1, u32 a2, u32 a3)
{
    TcArgs a = {{a0, a1, a2, a3}};
    g_cpu[0].sp = sp;
    return tc_call(addr, &a);
}

static int g_runs, g_first = -1;
static u32 g_ret;

/* KRNL's yield, as the translator writes it: r13 pushed at entry, set to a pointer of its own, setjmp; when that
 * returns 0, the longjmp on the same buffer (the scheduler's, here at once); then r13 popped and the value returned */
static u32 fn_yield(u32 a0, u32 a1, u32 a2, u32 a3)
{
    SatCpu *c = &g_cpu[0];
    TcArgs a = {{BUF, a1, a2, a3}};
    u32 r13 = c->r[13], r0, sp = STK;
    (void)a0;
    g_runs++;
    sp -= 4u, wr32(sp, r13);
    r13 = 0x26006020u, c->r[13] = r13;
    r0 = tc_call(SJ, &a);
    if (g_runs == 1) g_first = (int)r0;
    if (r0 == 0 && g_runs < 3) {
        a.r[0] = BUF, a.r[1] = 7u;
        tc_call(LJ, &a);
        printf("FAIL: the longjmp came back to its caller\n");
        g_fail = 1;
    }
    r13 = rd32(sp), sp += 4u, c->r[13] = r13;
    return r0;
}

static u32 fn_main(u32 a0, u32 a1, u32 a2, u32 a3)
{
    TcArgs a = {{1u, a1, a2, a3}};
    (void)a0;
    g_cpu[0].r[13] = YIELD; /* the main task keeps the yield's address in r13 across the call (0600623a) */
    g_ret = tc_call(YIELD, &a);
    return g_cpu[0].r[13];
}

static void check(int ok, const char *what)
{
    printf("%s: %s\n", ok ? "ok  " : "FAIL", what);
    if (!ok) g_fail = 1;
}

int main(int argc, char **argv)
{
    static const u16 code[] = {/* 060061c4 setjmp */ 0x1480, 0x1481, 0x1492, 0x14a3, 0x14b4, 0x14c5, 0x14d6, 0x14e7,
                               0x14f8, 0x000a, 0x140a, 0x001a, 0x140b, 0x002a, 0x1409, 0x000b, 0xe000,
                               /* 060061e6 longjmp */ 0x5841, 0x5942, 0x5a43, 0x5b44, 0x5c45, 0x5d46, 0x5e47, 0x5f48,
                               0x504b, 0x401a, 0x504a, 0x400a, 0x5049, 0x402a, 0x5040, 0x6403, 0x000b, 0x6053};
    TcArgs none = {{0}};
    u32 r13;
    if (argc > 1 && !strcmp(argv[1], "fiber")) /* as with a window: host_modal.cpp made the thread a fiber first */
        check(ConvertThreadToFiber(NULL) != NULL, "the thread is a fiber before the kit's first switch");
    for (u32 i = 0; i < sizeof code / sizeof code[0]; i++) put16(SJ + 2u * i, code[i]);
    wr32(YIELD + 0x20u, SJ); /* the yield's literal pool holds the setjmp's address: the kit marks it */
    g_cpu[0].sp = STK - 0x100u;
    sat_task_init();
    check(sat_task_on && g_sj == SJ && g_lj == LJ, "the setjmp and longjmp found by their code");
    check(sat_task_flags && sat_task_flags[1] && !sat_task_flags[2], "the yield marked, its caller not");
    if (!sat_task_on) return 1;
    r13 = tc_call(MAINF, &none);
    check(g_runs == 2, "the yield ran again from its start when resumed");
    check(g_first == 0 && g_ret == 7u, "its setjmp returned 0, then the longjmp's value");
    check(r13 == YIELD, "the caller's r13 came back from the resumed yield's epilogue, not the setjmp-time value");
    if (argc > 1 && !strcmp(argv[1], "fiber")) /* a NULL one is SwitchToFiber(NULL) at the CPU's next switch back */
        check(g_cpu[0].fiber != NULL && g_cpu[0].fiber == GetCurrentFiber(), "the CPU's own fiber is the thread's, a fiber already");
    printf("r13 after the yield: %08x (runs %d, first %d, value %u)\n", r13, g_runs, g_first, g_ret);
    if (!g_fail) printf("all ok\n");
    return g_fail;
}
