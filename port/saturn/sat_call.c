/* Calls between translated functions, the register state they share, and the two SH-2s.
 * The translator's C takes a callee's arguments as a TcArgs block (r4..r7, the 8 words at the call's sp, r0..r3) and
 * keeps r8..r14 in a shared array between functions (sh4_r / sh4_set_r). tc_call finds the callee by guest address
 * in the generated table, gives it a fresh frame on the guest stack below every live frame (each function's push
 * bound is known from its text: tools/gen_saturn.py) and runs it. An address with no translated function is a
 * logging stub that returns 0, named once, so a boot run lists what is missing.
 * The master SH-2 runs on the game's thread; the slave runs on a Windows fiber of its own, started when the master
 * signals it (MINIT) the way the BIOS' idle loop jumps to the entry in work RAM 0x06000250. The two take turns at the
 * points where a real CPU would wait for the other: a tc_spin, a poll of the cache-through mirror (the shared
 * mailboxes), the slave's poll of its own timer flags, and every 4096 calls. */
#include <stdlib.h>
#include <string.h>
#include <windows.h>
#include "sat.h"
#include "sat_cpu.h"
#include "sat_sys.h"
#include "work_count.h"

SatCpu g_cpu[2] = {{.sr = 0xf0u}, {.sr = 0xf0u}};
static SatCpu *cur = &g_cpu[MASTER];
/* The slave runs from a MINIT until it waits for the next one (or a budget of polls and calls runs out): the real slave
 * is concurrent and fast, so it is idle again before the master's next write; sliced by the master's polls it would
 * stop inside a record of the command ring (a cache-through read counts as a poll) and read the master's next frame. */
static int g_slave_run;
static u32 g_slave_budget;
enum { SLAVE_BUDGET = 60000000 };
u32 sat_entry_sp;
static const int kMaxStubs = 4096;
static u32 *g_stub_seen;
static int g_nstub;
static void *g_dead;
static u32 g_slave_sp = 0x060fbc00u;
static u32 g_watch_saved;
void sat_set_watch_saved(u32 on) { g_watch_saved = on; }
/* A call from a function on the guest stack gives its r15 (tc_call_sp): the callee's frame starts there, as on the CPU, so
 * it finds every stack word the caller pushed, past a TcArgs' 8 too (Robo Pit's 06034204 passes 06054440 nine). */
static u32 g_call_sp;
u32 tc_call_sp(u32 target, const TcArgs *a, u32 sp) { g_call_sp = sp; return tc_call(target, a); }
static u32 g_trace, g_trace_ret, g_trace_from, g_trace_count, g_trace_vb;
void sat_set_trace(u32 n) { g_trace = n; g_trace_ret = n != 0; }
void sat_set_trace_vb(u32 vb, u32 n) { g_trace_vb = vb ? vb : 0; g_trace_count = n; }
void sat_set_trace_from(u32 addr, u32 n) { g_trace_from = addr, g_trace_count = n; }

u32 sh4_r(int index) { return cur->r[index & 15]; }
u32 sat_in_r(int i) { return cur->in_r[i & 3]; }
void sh4_set_r(int index, u32 value) { cur->r[index & 15] = value; }
u32 sh4_stc_sr(void) { return cur->sr; }
void sh4_ldc_sr(u32 value)
{
    SatCpu *c = cur;
    if (((value ^ c->sr) & 0xf0u) && c->depth > 0 && c->depth <= 512) {
        c->srlvl[c->nsr & 7u] = (value >> 4) & 15u;
        c->srlog[c->nsr++ & 7u] = c->stack[c->depth - 1];
    }
    c->sr = value & 0x3f3u;
}
u32 sh4_stc_gbr(void) { return cur->gbr; }
void sh4_ldc_gbr(u32 value) { cur->gbr = value; }
u32 sat_sr(void) { return cur->sr; }
int sat_cpu_id(void) { return (int)(cur - g_cpu); }

void sat_call_init(void) { g_stub_seen = (u32 *)calloc((size_t)kMaxStubs, sizeof(u32)); }
void sat_set_slave_sp(u32 sp) { g_slave_sp = sp; }

static const SatFn *find(u32 addr)
{
    int lo = 0, hi = sat_nfns - 1;
    while (lo <= hi) {
        int mid = (lo + hi) >> 1;
        if (sat_fns[mid].addr == addr) return &sat_fns[mid];
        if (sat_fns[mid].addr < addr) lo = mid + 1;
        else hi = mid - 1;
    }
    return NULL;
}

static void stub_named(u32 addr)
{
    for (int i = 0; i < g_nstub; i++)
        if (g_stub_seen[i] == addr) return;
    if (g_nstub < kMaxStubs) g_stub_seen[g_nstub++] = addr;
    port_log("STUB #%d (%s): call to %08x has no translated function (returns 0); the guest holds %08x %08x %08x %08x there\n", g_nstub,
             sat_cpu_id() ? "slave" : "master", addr, sat_peek32(addr), sat_peek32(addr + 4u), sat_peek32(addr + 8u), sat_peek32(addr + 12u));
    if (g_nstub <= 8) {
        port_log("stub: r8..r14 %08x %08x %08x %08x %08x %08x %08x; words before r9: %08x %08x %08x %08x\n", sh4_r(8), sh4_r(9), sh4_r(10),
                 sh4_r(11), sh4_r(12), sh4_r(13), sh4_r(14), sat_peek32(sh4_r(9) - 16u), sat_peek32(sh4_r(9) - 12u), sat_peek32(sh4_r(9) - 8u),
                 sat_peek32(sh4_r(9) - 4u));
        sat_where();
    }
}

/* ---- the two CPUs */
static void switch_to(SatCpu *to)
{
    cur->entry_sp = sat_entry_sp;
    cur = to;
    sat_entry_sp = to->entry_sp;
    sat_mem_cpu((int)(to - g_cpu));
    SwitchToFiber(to->fiber);
}

/* the other CPU gets a turn, if it has a program running */
void sat_cpu_yield(void)
{
    if (!g_cpu[SLAVE].alive) return;
    switch_to(cur == &g_cpu[MASTER] ? &g_cpu[SLAVE] : &g_cpu[MASTER]);
    if (g_dead && cur == &g_cpu[MASTER]) {
        DeleteFiber(g_dead);
        g_dead = NULL;
    }
}

/* a read of the cache-through mirror or a CPU's own flag register: probably a wait on the other CPU */
void sat_poll_hint(u32 every)
{
    if (cur == &g_cpu[SLAVE] && g_slave_run) {
        if (every > 8u && g_slave_budget) { g_slave_budget--; return; } /* a ring read: not a wait */
        g_slave_run = 0;
        sat_cpu_yield();
        return;
    }
    if (++cur->hint >= every) {
        cur->hint = 0;
        if (cur == &g_cpu[MASTER]) {
            sat_perf_poll();
            sat_irq_poll(); /* the flag may be one the vertical-blank handler sets */
        }
        sat_cpu_yield();
    }
}

/* a long run of plain reads: a loop waiting for a flag, so an interrupt may be due and the other CPU may have to act */
void sat_busy_poll(void)
{
    if (cur == &g_cpu[SLAVE]) {
        if (g_slave_run && g_slave_budget) { /* a command handler that walks a long list: not a wait either */
            g_slave_budget = g_slave_budget > 65536u ? g_slave_budget - 65536u : 0;
            return;
        }
        g_slave_run = 0;
    }
    if (cur == &g_cpu[MASTER]) {
        sat_perf_poll();
        sat_irq_poll();
    }
    sat_cpu_yield();
}

static VOID CALLBACK slave_proc(PVOID unused)
{
    (void)unused;
    for (;;) {
        u32 entry = sat_peek32(0x06000250u);
        SatCpu *s = &g_cpu[SLAVE];
        s->depth = 0;
        s->sp = 0;
        s->hint = 0;
        port_log("slave: starts at %08x\n", entry);
        sat_call(g_slave_sp, entry, 0, 0, 0, 0);
        port_log("slave: its program returned; waiting for MINIT\n");
        s->alive = 0;
        g_dead = s->fiber;
        s->fiber = NULL;
        switch_to(&g_cpu[MASTER]);
    }
}

/* MINIT: the master signals the slave's input capture. The slave's idle loop (BIOS) starts the program whose entry is
 * in work RAM 0x06000250; a running program sees the flag in its own timer registers. */
void sat_minit(void)
{
    SatCpu *s = &g_cpu[SLAVE];
    sat_mem_icf(SLAVE);
    if (!s->alive && sat_peek32(0x06000250u)) {
        static int once;
        if (!g_cpu[MASTER].fiber) g_cpu[MASTER].fiber = ConvertThreadToFiber(NULL);
        if (g_dead) {
            DeleteFiber(g_dead);
            g_dead = NULL;
        }
        s->fiber = CreateFiberEx(0, 16u << 20, 0, slave_proc, NULL);
        s->alive = 1;
        (void)once;
    }
    if (s->alive && cur == &g_cpu[MASTER]) {
        g_slave_run = 1;
        g_slave_budget = SLAVE_BUDGET;
        sat_cpu_yield();
    }
}

/* SMPC SSHOFF: the slave stops where it is; SSHON then leaves it in the BIOS idle loop, waiting for MINIT to start the
 * program whose entry is in work RAM 0x06000250. The master calls this, so the slave's fiber is suspended. */
void sat_slave_off(void)
{
    SatCpu *s = &g_cpu[SLAVE];
    if (cur == s) return;
    if (s->fiber) {
        DeleteFiber(s->fiber);
        s->fiber = NULL;
    }
    if (s->alive) port_log("slave: stopped (SSHOFF)\n");
    s->alive = 0;
    s->depth = 0;
    s->hint = 0;
}

void sat_sinit(void) { sat_mem_icf(MASTER); }

/* Runs a function at the entry sp `top`. A function that ends in a jump to another (an SH-2 `jmp`, here a tail call made
 * with tc_tail) leaves the jump pending and returns; the next runs in the same frame, so a chain of them (SGL's polygon
 * transform steps through a table of code addresses) does not grow the C stack. */
static u32 run_frame(const SatFn *f, u32 top, const TcArgs *a, int stack_args)
{
    SatCpu *c = cur;
    u32 r;
    for (;;) {
        c->r[4] = a->r[0], c->r[5] = a->r[1], c->r[6] = a->r[2], c->r[7] = a->r[3];
        c->r[1] = a->rlo[1], c->r[2] = a->rlo[2], c->r[3] = a->rlo[3]; /* r1..r3 for exit_regs: a callee that does not write one hands the caller's back */
        if (stack_args)
            for (int i = 0; i < 8; i++) wr32(top + 4u * (u32)i, a->s[i]);
        stack_args = 1;
        sat_entry_sp = top;
        c->sp = top - f->frame;
        for (int i = 0; i < 4; i++) c->in_r[i] = a->rlo[i];
        r = f->fn(a->r[0], a->r[1], a->r[2], a->r[3]);
        c = cur;
        if (!c->tail) return r;
        c->tail = 0;
        a = &c->tail_args;
        f = find(c->tail_target);
        if (c->tail_sp) top = c->tail_sp; /* the CPU keeps r15 across a jump: the target's pops and pushes meet the frame it was jumped from */
        if (g_trace && g_trace--) port_log("tail %s %08x (%08x %08x %08x %08x) sp %08x\n", c == &g_cpu[SLAVE] ? "S" : "M", f->addr, a->r[0], a->r[1], a->r[2], a->r[3], top);
        c->last[c->nlast++ & 63u] = f->addr;
        if (c->depth <= 512) c->stack[c->depth - 1] = f->addr;
    }
}

u32 sat_call(u32 sp, u32 addr, u32 a0, u32 a1, u32 a2, u32 a3)
{
    const SatFn *f = find(addr);
    SatCpu *c = cur;
    u32 saved_sp = c->sp, saved_entry = sat_entry_sp, r;
    TcArgs a = {{a0, a1, a2, a3}};
    if (!f) {
        stub_named(addr);
        return 0;
    }
    if (c->depth < 512) c->stack[c->depth] = addr;
    c->last[c->nlast++ & 63u] = addr;
    if (++c->depth > MAXDEPTH) {
        sat_where();
        port_exit("call depth over 400", 1);
    }
    c->popped[c->depth] = 0;
    r = run_frame(f, sp, &a, 0);
    c = cur;
    c->depth--;
    c->sp = saved_sp;
    sat_entry_sp = saved_entry;
    return r;
}

u32 tc_call(u32 target, const TcArgs *a)
{
    u32 csp = g_call_sp; g_call_sp = 0; /* the caller's r15 at the call, from tc_call_sp */
    work_add(WC_GUEST_CALLS, 1);
    const SatFn *f;
    SatCpu *c = cur;
    u32 top, saved_sp = c->sp, saved_entry = sat_entry_sp, r;
    if ((++c->calls & 0xfffu) == 0) {
        if (c == &g_cpu[SLAVE] && g_slave_run && g_slave_budget) g_slave_budget = g_slave_budget > 4096u ? g_slave_budget - 4096u : 0;
        else {
            if (c == &g_cpu[MASTER]) sat_irq_poll();
            sat_cpu_yield();
            c = cur;
        }
    }
    f = find(target & 0xdfffffffu);
    if (!f) {
        SatNative nat;
        for (int i = 0; i < 4; i++) c->r[4 + i] = a->r[i]; /* a callee that does not touch r4..r7 leaves its arguments in them */
        nat = sat_bios_native(target & 0xdfffffffu);
        if (nat) return nat(a->r[0], a->r[1], a->r[2], a->r[3]);
        stub_named(target);
        return 0;
    }
    if (f->addr == sat_logo_fn && sat_logos_skip(a, &r)) return r; /* an opening logo skipped (sat_logos.c) */
    if (f->addr == sat_movie_fn && sat_movie_play(a, &r)) return r; /* game.json movie: the host plays it (sat_movie.c) */
    TcArgs menu; if (sat_menus_on && sat_menus_enter(f->addr, &a, &menu, &r)) return r; /* the kit's in-game menus (sat_menus.c) */
    top = csp ? csp : (c->sp ? c->sp : 0x060ffc00u) - 32u;
    if (g_trace_vb && sat_vblank_count() >= g_trace_vb) {
        g_trace_vb = 0;
        sat_set_trace(g_trace_count);
    }
    if (g_trace_from && f->addr == g_trace_from) {
        g_trace_from = 0;
        sat_set_trace(g_trace_count);
    }
    if (g_trace && g_trace--) port_log("call %s %08x (%08x %08x %08x %08x) sp %08x r8 %08x\n", c == &g_cpu[SLAVE] ? "S" : "M", f->addr, a->r[0], a->r[1], a->r[2], a->r[3], top, sh4_r(8));
    c->last[c->nlast++ & 63u] = target;
    if (c->depth < 512) c->stack[c->depth] = f->addr;
    if (++c->depth > MAXDEPTH) {
        sat_where();
        port_exit("call depth over 400", 1);
    }
    c->popped[c->depth] = 0;
    u32 keep[7]; for (int k = 0; k < 7; k++) keep[k] = sh4_r(8 + k);
    SatFeat feat; a = sat_feat_enter(f->addr, a, &feat); /* the feature hooks on this call, before and after (sat_call_feat.c) */
    r = run_frame(f, top, a, 1);
    r = sat_feat_leave(f->addr, a, r);
    if (g_watch_saved) sat_saved_note(f->addr, keep);
    cur->last_top = top;
    if (g_trace_ret && g_trace) port_log("ret %08x = %08x\n", f->addr, r);
    c = cur;
    c->depth--;
    c->sp = saved_sp;
    sat_entry_sp = saved_entry;
    return r;
}

/* A call that comes back to a label of the function that made it (see sat_pr_popped) is one routine split in two: the
 * callee works on the stack words the caller passed it (a loop counter, say) and the caller carries on with them. */
void sat_window_back(u32 sp)
{
    for (int i = 0; i < 8; i++) wr32(sp + 4u * (u32)i, rd32(cur->last_top + 4u * (u32)i));
}

/* A function that restores pr from its stack returns past the call site that set pr to one of its own labels (the way SGL
 * jumps into the middle of a handler); sat_pr_popped marks the running frame, and the call site asks sat_pr_took. */
void sat_pr_popped(void) { if (cur->depth <= MAXDEPTH) cur->popped[cur->depth] = 1; }
int sat_pr_took(void)
{
    int d = cur->depth + 1, v;
    if (d > MAXDEPTH) return 0;
    v = cur->popped[d];
    cur->popped[d] = 0;
    return v;
}

/* A jump to another function as the last thing a function does: the target runs in this function's frame once it returns */
u32 tc_tail(u32 target, const TcArgs *a)
{
    SatCpu *c = cur;
    u32 t = target & 0xdfffffffu;
    if (!find(t)) return tc_call(target, a); /* a BIOS routine or a stub: nothing to run in this frame */
    c->tail = 1;
    c->tail_target = t;
    c->tail_args = *a;
    c->tail_sp = 0;
    return 0;
}

/* A jump made with the function's frame still open (a loop's tail, an interpreter's handler: SGL's transform routines push
 * their registers in one function and pop them in another): the target starts at the jumper's r15, not at the entry sp, so
 * what it pops is what the jumper pushed. The stack words are already in place, so the arguments' window is not copied. */
u32 tc_tail_sp(u32 target, const TcArgs *a, u32 sp) { u32 r = tc_tail(target, a); if (cur->tail) cur->tail_sp = sp; return r; }

/* An interrupt handler: runs a translated function on a fresh frame with every register kept, as the CPU's
 * exception entry and rte do. The handler runs with the mask raised to the interrupt's level. */
u32 sat_call_here(u32 addr, u32 level)
{
    SatCpu *c = cur;
    u32 saved_r[16], saved_sr = c->sr, r;
    memcpy(saved_r, c->r, sizeof saved_r);
    c->sr = (c->sr & ~0xf0u) | ((level & 15u) << 4);
    r = sat_call((c->sp ? c->sp : 0x060ffc00u) - 64u, addr, 0, 0, 0, 0);
    c = cur;
    memcpy(c->r, saved_r, sizeof saved_r);
    c->sr = saved_sr;
    return r;
}

void tc_halt(void)
{
    port_log("HALT: the %s SH-2 stopped in an endless loop (tc_halt) after %u vertical blanks\n", sat_cpu_id() ? "slave" : "master",
             sat_vblank_count());
    sat_where();
    port_exit("the game halted", 3);
}

void tc_spin(void)
{
    if (cur == &g_cpu[MASTER]) {
        sat_cpu_yield();
        sat_spin();
    } else {
        sat_cpu_yield();
    }
}

static int g_vblank_wait = 1; /* game.json "vblank_flags": a loop on a flag the vertical-blank handler sets; --vblank-wait off burns */
void sat_set_vblank_wait(int on) { g_vblank_wait = on; }
void sat_vblank_wait(void) { if (g_vblank_wait) tc_spin(); }

void sat_stub_report(void) { port_log("stubs hit: %d (STUB lines above)\n", g_nstub); }
