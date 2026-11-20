/* Calls between translated functions, the register state they share, and the two SH-2s.
 * The translator's C takes a callee's arguments as a TcArgs block (r4..r7, the 8 words at the call's sp, r0..r3) and
 * keeps r8..r14 in a shared array between functions (sh4_r / sh4_set_r). tc_call finds the callee by guest address
 * in the generated table, gives it a fresh frame on the guest stack below every live frame (each function's push
 * bound is known from its text, and a computed stack move reports its real sp as it moves: tools/gen_saturn.py)
 * and runs it. An address with no translated function is a
 * logging stub that returns 0, named once, so a boot run lists what is missing; with --fallback on the SH-2 interpreter
 * runs it instead and counts it (sat_interp.c); --fallback check also runs translated functions there and compares.
 * The master SH-2 runs on the game's thread; the slave runs on a Windows fiber of its own, started when the master
 * signals it (MINIT) the way the BIOS' idle loop jumps to the entry in work RAM 0x06000250. The two take turns at the
 * points where a real CPU would wait for the other: a tc_spin, a poll of the cache-through mirror (the shared
 * mailboxes), the slave's poll of its own timer flags, and every 4096 calls. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <windows.h>
#include "sat.h"
#include "sat_cpu.h"
#include "sat_sys.h"
#include "work_count.h"
#include "save_state.h"

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
static int g_irq, g_hook; /* an interrupt handler, a kit hook (logos, movie, menus, feature hooks) running: no safe point */
static u32 g_slave_hint0; /* the slave's poll count when a load starts it again */
int sat_body_nest;
u32 tc_call_sp(u32 target, const TcArgs *a, u32 sp) { g_call_sp = sp; return tc_call(target, a); }
/* A function whose r15 moves by a computed amount gives its real sp after each move (tools/gen_saturn.py track_sp): it is
 * the lowest word in use, so a callee placed without an sp, and an interrupt taken inside the function (sat_call_here),
 * go below it, as on the CPU, not below a bound guessed for it. */
void sat_sp_at(u32 sp) { cur->sp = sp; }
static u32 g_trace, g_trace_ret, g_trace_from, g_trace_count, g_trace_vb;
void sat_set_trace(u32 n) { g_trace = n; g_trace_ret = n != 0; }
void sat_set_trace_vb(u32 vb, u32 n) { g_trace_vb = vb ? vb : 0; g_trace_count = n; }
void sat_set_trace_from(u32 addr, u32 n) { g_trace_from = addr, g_trace_count = n; }

u32 sat_ret = SAT_PR_NONE;
u32 sat_pr_in(void) { return cur->depth >= 0 && cur->depth <= MAXDEPTH + 1 ? cur->site[cur->depth] : SAT_PR_NONE; }
/* a frame opened: the PR its caller handed it and the caller's r8..r14 (sat.h sat_at) */
static void frame_site(SatCpu *c, u32 site)
{
    if (c->depth < 0 || c->depth > MAXDEPTH + 1) return;
    c->site[c->depth] = site;
    memcpy(c->saved[c->depth], &c->r[8], sizeof c->saved[0]);
}

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

int sat_fn_resident(const SatFn *f)
{
    return !f->guard || sat_resident(f->addr, f->guard[0], f->guard[1], f->guard[2], f->guard[3]);
}

/* The table is sorted by address. Overlays that load at the same place (Dragon Force's DF.BIN and FIELD0..7.BIN over /0
 * at 06010000) each have their own translation of an address (gen_saturn.py --overlay): the one whose guard words are in
 * memory now runs; with none of them there, the first, whose guard then hands the call to sat_absent. */
static const SatFn *find(u32 addr)
{
    int lo = 0, hi = sat_nfns;
    while (lo < hi) {
        int mid = (lo + hi) >> 1;
        if (sat_fns[mid].addr < addr) lo = mid + 1;
        else hi = mid;
    }
    if (lo >= sat_nfns || sat_fns[lo].addr != addr) return NULL;
    if (lo + 1 < sat_nfns && sat_fns[lo + 1].addr == addr)
        for (int i = lo; i < sat_nfns && sat_fns[i].addr == addr; i++)
            if (sat_fn_resident(&sat_fns[i])) return &sat_fns[i];
    return &sat_fns[lo];
}

SatCpu *sat_cpu_cur(void) { return cur; }
const SatFn *sat_find_fn(u32 addr) { return find(addr); }

static void stub_why(u32 addr, const char *why)
{
    for (int i = 0; i < g_nstub; i++)
        if (g_stub_seen[i] == addr) return;
    if (g_nstub < kMaxStubs) g_stub_seen[g_nstub++] = addr;
    port_log("STUB #%d (%s): call to %08x has %s (returns 0); the guest holds %08x %08x %08x %08x there\n", g_nstub,
             sat_cpu_id() ? "slave" : "master", addr, why, sat_peek32(addr), sat_peek32(addr + 4u), sat_peek32(addr + 8u), sat_peek32(addr + 12u));
    if (g_nstub <= 8) {
        port_log("stub: r8..r14 %08x %08x %08x %08x %08x %08x %08x; words before r9: %08x %08x %08x %08x\n", sh4_r(8), sh4_r(9), sh4_r(10),
                 sh4_r(11), sh4_r(12), sh4_r(13), sh4_r(14), sat_peek32(sh4_r(9) - 16u), sat_peek32(sh4_r(9) - 12u), sat_peek32(sh4_r(9) - 8u),
                 sat_peek32(sh4_r(9) - 4u));
        sat_where();
    }
}

static void stub_named(u32 addr) { stub_why(addr, "no translated function"); }

/* The guard gen_saturn.py --guard puts at the start of a translation of code the game loads at run time (Dragon Force's
 * /0 carries code that runs at 00210000 in work RAM low; DF.BIN and FIELD0..7.BIN load over /0 at 06010000): the translation is
 * that code only while the 16 bytes at its start are the ones it was made from. Another overlay's code there is never run
 * as this one's: it is a STUB until a translation of it (or an interpreter) is there. */
int sat_resident(u32 addr, u32 w0, u32 w1, u32 w2, u32 w3)
{
    u32 a = addr & ~3u;
    if (sat_peek32(a) == w0 && sat_peek32(a + 4u) == w1 && sat_peek32(a + 8u) == w2 && sat_peek32(a + 12u) == w3) {
        sat_interp_resident_seen();
        return 1;
    }
    return 0;
}

u32 sat_absent(u32 addr)
{
    if (sat_interp_declines(addr)) return 0; /* the interpreter branched here: it runs the code that is there itself */
    if (sat_fallback_on && sat_fallback_ok(addr)) { /* the interpreter runs the code that is there, in this frame (sat_interp.c) */
        TcArgs a;
        memset(&a, 0, sizeof a);
        for (int i = 0; i < 4; i++) a.r[i] = cur->r[4 + i], a.rlo[i] = cur->in_r[i];
        return sat_interp_marked((u32)(cur - g_cpu), cur->depth, addr, sat_entry_sp, &a, 0); /* the rest of this frame */
    }
    stub_why(addr, "no matching translation (the code there is not the code translated)");
    return 0;
}

/* ---- the two CPUs */
static u32 g_switches; /* CPU switches and interrupts so far: --fallback check compares only calls with none inside */
static void switch_to(SatCpu *to)
{
    g_switches++;
    cur->entry_sp = sat_entry_sp;
    cur->ret_next = sat_ret;
    cur = to;
    sat_ret = to->ret_next ? to->ret_next : SAT_PR_NONE;
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
            sat_wait_poll(); /* the flag may be one the vertical-blank handler sets */
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
        sat_wait_poll();
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
        s->hint = g_slave_hint0; /* 0, or the count it had when a state was saved (sat_call_loaded) */
        g_slave_hint0 = 0;
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
        /* a window makes the thread a fiber first (port/base/host_modal.cpp): ConvertThreadToFiber fails then, and the
         * NULL it returned was the slave's switch back (Dragon Force's windowed launch, 2026-11-16) */
        if (!g_cpu[MASTER].fiber) g_cpu[MASTER].fiber = IsThreadAFiber() ? GetCurrentFiber() : ConvertThreadToFiber(NULL);
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
    sat_task_cpu_off(SLAVE);
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
        r = sat_task_flags && sat_task_flags[f - sat_fns] ? sat_task_run(f, top, a) : f->fn(a->r[0], a->r[1], a->r[2], a->r[3]); /* sat_task.c */
        c = cur;
        if (!c->tail) return r;
        c->tail = 0;
        a = &c->tail_args;
        f = find(c->tail_target);
        if (c->tail_sp) top = c->tail_sp; /* the CPU keeps r15 across a jump: the target's pops and pushes meet the frame it was jumped from */
        if (c->depth >= 0 && c->depth <= MAXDEPTH + 1) c->ftop[c->depth] = top | (c->ftop[c->depth] & 1u);
        if (g_trace && g_trace--) port_log("tail %s %08x (%08x %08x %08x %08x) sp %08x\n", c == &g_cpu[SLAVE] ? "S" : "M", f->addr, a->r[0], a->r[1], a->r[2], a->r[3], top);
        c->last[c->nlast++ & 63u] = f->addr;
        if (c->depth <= 512) c->stack[c->depth - 1] = f->addr;
    }
}

/* --fallback on: the SH-2 interpreter runs code that has no translated function, counted (sat_interp.c), on a frame of its
 * own as a translated callee would have */
static u32 fallback_run(u32 addr, u32 top, const TcArgs *a, int stack_args, u32 site, int sp_given)
{
    SatCpu *c = cur;
    u32 saved_sp = c->sp, saved_entry = sat_entry_sp, r;
    if (c->depth < 512) c->stack[c->depth] = addr & 0xdfffffffu;
    c->last[c->nlast++ & 63u] = addr;
    if (++c->depth > MAXDEPTH) {
        sat_where();
        port_exit("call depth over 400", 1);
    }
    c->popped[c->depth] = 0;
    frame_site(c, site);
    c->ftop[c->depth] = top | (sp_given ? 0u : 1u); /* top is the caller's r15 when the call gave it */
    sat_entry_sp = top;
    r = sat_interp_marked((u32)(c - g_cpu), c->depth, addr, top, a, stack_args); /* the sampler sees it interpreted */
    c = cur;
    c->depth--;
    c->sp = saved_sp;
    sat_entry_sp = saved_entry;
    return r;
}

/* --fallback check: a test of the SH-2 interpreter against translated C. The first three calls of each translated function
 * (one at a time, never nested) run twice from the same state: the translated C (the run the game keeps) and the
 * interpreter on the guest code in RAM (sat_interp_pure: work RAM only). r8..r14 and work RAM outside the call's stack
 * window must agree (r0 is logged apart: a function with no result hands back whatever r0 held). A call during which the
 * other CPU ran, an interrupt came or a vertical blank passed, and a run that reached hardware or a BIOS service, are not
 * comparable and only counted. */
static int g_in_check;
static u32 g_ck_same, g_ck_diff, g_ck_hw, g_ck_moved, g_ck_r0;
static u8 *g_ck[4];

static int check_want(u32 addr)
{
    enum { N = 1 << 15 };
    static u32 *keys;
    static u8 *cnt;
    if (!keys && (!(keys = (u32 *)calloc(N, sizeof *keys)) || !(cnt = (u8 *)calloc(N, 1)))) return 0;
    if (!cnt) return 0;
    for (u32 i = 0, h = ((addr >> 1) * 2654435761u) >> 17; i < N; i++, h = (h + 1u) & (N - 1u)) {
        if (keys[h] == addr) return cnt[h] < 3u ? (cnt[h]++, 1) : 0;
        if (!keys[h]) return keys[h] = addr, cnt[h] = 1, 1;
    }
    return 0;
}

/* bytes of one work RAM that differ outside [lo, hi); *first: the lowest such address */
static u32 ram_diff(const u8 *x, const u8 *y, u32 base, u32 lo, u32 hi, u32 *first)
{
    u32 n = 0;
    for (u32 o = 0; o < SAT_WRAM_SIZE; o += 4096u) {
        if (!memcmp(x + o, y + o, 4096u)) continue;
        for (u32 k = o; k < o + 4096u; k++)
            if (x[k] != y[k] && !(base + k >= lo && base + k < hi) && !n++) *first = base + k;
    }
    return n;
}

static u32 run_frame(const SatFn *f, u32 top, const TcArgs *a, int stack_args);
static u32 check_call(const SatFn *f, u32 top, const TcArgs *a)
{
    SatCpu *c = cur;
    static SatCpu c0, c1; /* never nested (g_in_check) */
    TcArgs a0 = *a;
    u8 *h = sat_ram_h(), *l = sat_ram_l();
    u32 e0 = sat_entry_sp, sw = g_switches, vb = sat_vblank_count(), e1, r, ri, nh, nl, fh = 0, fl = 0;
    int ok;
    for (int i = 0; i < 4; i++)
        if (!g_ck[i] && !(g_ck[i] = (u8 *)malloc(SAT_WRAM_SIZE))) return run_frame(f, top, a, 1);
    c0 = *c;
    memcpy(g_ck[0], h, SAT_WRAM_SIZE), memcpy(g_ck[1], l, SAT_WRAM_SIZE);
    g_in_check = 1;
    r = run_frame(f, top, a, 1);
    g_in_check = 0;
    if (cur != c || g_switches != sw || sat_vblank_count() != vb || c->popped[c->depth]) {
        g_ck_moved++;
        return r;
    }
    c1 = *c, e1 = sat_entry_sp;
    memcpy(g_ck[2], h, SAT_WRAM_SIZE), memcpy(g_ck[3], l, SAT_WRAM_SIZE);
    memcpy(h, g_ck[0], SAT_WRAM_SIZE), memcpy(l, g_ck[1], SAT_WRAM_SIZE);
    *c = c0, sat_entry_sp = e0;
    ri = sat_interp_pure(f->addr, top, &a0, &ok);
    if (!ok) g_ck_hw++;
    else {
        u32 rd = 0;
        for (int k = 8; k < 15; k++)
            if (c->r[k] != c1.r[k]) rd |= 1u << (k - 8);
        nh = ram_diff(h, g_ck[2], SAT_WRAM_H_BASE, top - 0x4000u, top + 32u, &fh);
        nl = ram_diff(l, g_ck[3], SAT_WRAM_L_BASE, top - 0x4000u, top + 32u, &fl);
        if (!rd && !nh && !nl) {
            g_ck_same++;
            if (ri != r && g_ck_r0++ < 40) port_log("fallback check: %08x hands back r0 %08x translated, %08x interpreted (memory and r8..r14 agree)\n", f->addr, r, ri);
        } else if (g_ck_diff++ < 200) {
            u32 at = nh ? fh : fl, o = at - (nh ? SAT_WRAM_H_BASE : SAT_WRAM_L_BASE);
            port_log("FALLBACK CHECK DIFF %08x (entry sp %08x): r0 %08x translated, %08x interpreted; r8..r14 differ %02x (r8 %08x/%08x); "
                     "%u byte(s) of work RAM differ, first %08x: %02x translated, %02x interpreted\n",
                     f->addr, top, r, ri, rd, c1.r[8], c->r[8], nh + nl, nh + nl ? at : 0u, nh + nl ? (nh ? g_ck[2] : g_ck[3])[o] : 0u,
                     nh + nl ? (nh ? h : l)[o] : 0u);
        }
    }
    memcpy(h, g_ck[2], SAT_WRAM_SIZE), memcpy(l, g_ck[3], SAT_WRAM_SIZE);
    *c = c1, sat_entry_sp = e1;
    return r;
}

void sat_check_counts(u32 n[5]) { n[0] = g_ck_same, n[1] = g_ck_diff, n[2] = g_ck_hw, n[3] = g_ck_moved, n[4] = g_ck_r0; }
void sat_check_report(void)
{
    port_log("fallback check: %u call(s) compared: %u agree (%u of them with another r0), %u DIFFER; not comparable: %u reached hardware or a BIOS "
             "service, %u had the other CPU, an interrupt or a vertical blank inside\n",
             g_ck_same + g_ck_diff, g_ck_same, g_ck_r0, g_ck_diff, g_ck_hw, g_ck_moved);
}

u32 sat_call(u32 sp, u32 addr, u32 a0, u32 a1, u32 a2, u32 a3)
{
    const SatFn *f = find(addr);
    SatCpu *c = cur;
    u32 saved_sp = c->sp, saved_entry = sat_entry_sp, r;
    TcArgs a = {{a0, a1, a2, a3}};
    if (!f) {
        if (sat_fallback_on && sat_fallback_ok(addr)) return fallback_run(addr, sp, &a, 0, SAT_PR_NONE, 0);
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
    frame_site(c, SAT_PR_NONE);
    c->ftop[c->depth] = sp | 1u;
    r = run_frame(f, sp, &a, 0);
    c = cur;
    c->depth--;
    c->sp = saved_sp;
    sat_entry_sp = saved_entry;
    return r;
}

/* the kit's hooks that can run game code of their own: no safe point inside them (sat_state.c) */
static int hook_logos(const TcArgs *a, u32 *r) { int d; g_hook++, d = sat_logos_skip(a, r), g_hook--; return d; }
static int hook_movie(const TcArgs *a, u32 *r) { int d; g_hook++, d = sat_movie_play(a, r), g_hook--; return d; }
static int hook_menus(u32 fn, const TcArgs **a, TcArgs *tmp, u32 *r) { int d; g_hook++, d = sat_menus_enter(fn, a, tmp, r), g_hook--; return d; }

u32 tc_call(u32 target, const TcArgs *a)
{
    u32 site = sat_ret; sat_ret = SAT_PR_NONE; /* the PR this call hands the callee (sat.h sat_at), taken before anything runs */
    u32 csp = g_call_sp; g_call_sp = 0; /* the caller's r15 at the call, from tc_call_sp */
    if (save_state_pending && cur == &g_cpu[MASTER]) sat_state_point(target, a, site, csp); /* a save or load waits (sat_state.c) */
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
    if (sat_task_on && sat_task_call(target & 0xdfffffffu, a, &r)) return r; /* a setjmp or longjmp between guest tasks (sat_task.c) */
    if (!f) {
        SatNative nat;
        for (int i = 0; i < 4; i++) c->r[4 + i] = a->r[i]; /* a callee that does not touch r4..r7 leaves its arguments in them */
        nat = sat_bios_native(target & 0xdfffffffu);
        if (nat) return nat(a->r[0], a->r[1], a->r[2], a->r[3]);
        if (sat_fallback_on && sat_fallback_ok(target)) return fallback_run(target, csp ? csp : (c->sp ? c->sp : 0x060ffc00u) - 32u, a, 1, site, csp != 0);
        stub_named(target);
        return 0;
    }
    if (f->addr == sat_logo_fn && hook_logos(a, &r)) return r; /* an opening logo skipped (sat_logos.c) */
    if (f->addr == sat_movie_fn && hook_movie(a, &r)) return r; /* game.json movie: the host plays it (sat_movie.c) */
    TcArgs menu; if (sat_menus_on && hook_menus(f->addr, &a, &menu, &r)) return r; /* the kit's in-game menus (sat_menus.c) */
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
    frame_site(c, site);
    c->ftop[c->depth] = top | (csp ? 0u : 1u);
    u32 keep[7]; for (int k = 0; k < 7; k++) keep[k] = sh4_r(8 + k);
    SatFeat feat; g_hook++, a = sat_feat_enter(f->addr, a, &feat), g_hook--; /* the feature hooks on this call, before and after (sat_call_feat.c) */
    r = sat_fallback_on == 2 && !g_in_check && !(sat_task_flags && sat_task_flags[f - sat_fns]) && sat_fallback_ok(f->addr) && check_want(f->addr)
            ? check_call(f, top, a)
            : run_frame(f, top, a, 1);
    g_hook++, r = sat_feat_leave(f->addr, a, r), g_hook--;
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
u32 tc_tail_sp(u32 target, const TcArgs *a, u32 sp)
{
    u32 t = target & 0xdfffffffu, r;
    /* a guest task's setjmp or longjmp is the kit's to run first, as tc_call does (sat_task.c): KRNL's longjmp has no
     * translated function, and interpreted it restores r15 and pr but switches no fiber, so the resumed task never runs
     * (Saturn Bomberman's 060061e6, jumped to from 06006174's open frame: every run stopped at vertical blank 6, 2026-11-17) */
    if (sat_task_on && sat_task_call(t, a, &r)) return r;
    if (!find(t) && sat_fallback_on && sat_fallback_ok(t) && !sat_bios_native(t)) { /* the interpreter starts at the jumper's r15 */
        sat_interp_tail_next();
        return fallback_run(t, sp, a, 0, sat_pr_in(), 1); /* a jump: the frame's own PR, at the jumper's real r15 */
    }
    r = tc_tail(target, a);
    if (cur->tail) cur->tail_sp = sp;
    return r;
}

/* An interrupt handler: runs a translated function on a fresh frame with every register kept, as the CPU's
 * exception entry and rte do. The handler runs with the mask raised to the interrupt's level. */
u32 sat_call_here(u32 addr, u32 level)
{
    SatCpu *c = cur;
    u32 saved_r[16], saved_sr = c->sr, r, keep_ret = sat_ret; /* an interrupt between a call's sat_at and its tc_call */
    g_switches++;
    memcpy(saved_r, c->r, sizeof saved_r);
    c->sr = (c->sr & ~0xf0u) | ((level & 15u) << 4);
    g_irq++;
    r = sat_call((c->sp ? c->sp : 0x060ffc00u) - 64u, addr, 0, 0, 0, 0);
    g_irq--;
    c = cur;
    memcpy(c->r, saved_r, sizeof saved_r);
    c->sr = saved_sr;
    sat_ret = keep_ret;
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

/* ---- save states (sat_state.c) */
/* Why this call cannot be the safe point (NULL: it can). A load needs only a moment it can leave (the master, no
 * interrupt, no kit hook); a save also needs every open frame to resume: the return address its caller handed it and,
 * for a translated frame whose calls do not give their r15, a frame that pushes nothing (an interpreted frame's stack is
 * the guest's own, ftop bit 1). */
const char *sat_call_unsafe(int saving, u32 site, u32 csp)
{
    SatCpu *c = cur, *s = &g_cpu[SLAVE];
    if (c != &g_cpu[MASTER]) return "the slave SH-2 is running";
    if (g_irq) return "an interrupt handler is running";
    if (g_hook) return "a kit hook is running (a movie, the logos, a kit menu)";
    if (g_in_check) return "--fallback check is comparing a call";
    if (sat_task_on) return "the game switches KRNL tasks";
    if (!saving) return NULL;
    if (c->tail) return "a jump is waiting";
    if (sat_body_nest) return "a recursive function is inside itself";
    if (c->depth < 1 || c->depth > MAXDEPTH) return "the call chain is empty or too deep";
    if (site == SAT_PR_NONE || !sat_fallback_ok(site)) return "the call has no return address";
    for (int k = 1; k <= c->depth; k++) {
        const SatFn *f = find(c->stack[k - 1] & 0xdfffffffu);
        int sp_given = k == c->depth ? csp != 0 : !(c->ftop[k + 1] & 1u), interp = (c->ftop[k] & 2u) != 0;
        if (k > 1 && (c->site[k] == SAT_PR_NONE || !sat_fallback_ok(c->site[k]))) return "an open frame has no return address";
        if (interp) continue; /* its calls always give r15 (call_out) */
        if (!f) return "an open frame's function has no translation";
        if (!sp_given && f->frame > 64u) { /* the last such frame named (sat_state.c counts by this text's address) */
            static char why[128];
            snprintf(why, sizeof why, "an open frame's stack pointer is not known (frame %d of %d: %08x, %u bytes%s)", k, c->depth,
                     f->addr, f->frame, k < c->depth && (c->ftop[k + 1] & 2u) ? ", its callee interpreted" : "");
            return why;
        }
    }
    if (s->alive && (g_slave_run || s->tail || s->depth != 1 ||
                     (s->stack[0] & 0xdfffffffu) != (sat_peek32(0x06000250u) & 0xdfffffffu)))
        return "the slave SH-2 is busy";
    return NULL;
}

u32 sat_fn_frame(u32 addr)
{
    const SatFn *f = find(addr & 0xdfffffffu);
    return f ? f->frame : 0u;
}

/* The SH-2s: the master's registers and open frames (depth first: sat_state.c checks the block's length by it), the
 * slave's registers (on a load it starts again at its entry, so its frames are not kept) and the call bookkeeping. */
void sat_call_state(SatSt *io)
{
    SatCpu *m = &g_cpu[MASTER];
    u32 d = (u32)m->depth;
    sat_st(io, &d, 4);
    if (sat_st_loading(io)) m->depth = (int)d;
    sat_st(io, &sat_entry_sp, 4), sat_st(io, &g_slave_run, 4), sat_st(io, &g_slave_budget, 4);
    for (int i = 0; i < 2; i++) {
        SatCpu *c = &g_cpu[i];
        sat_st(io, c->r, sizeof c->r), sat_st(io, &c->gbr, 4), sat_st(io, &c->sr, 4), sat_st(io, &c->sp, 4);
        sat_st(io, &c->entry_sp, 4), sat_st(io, &c->top, 4), sat_st(io, c->in_r, sizeof c->in_r), sat_st(io, &c->calls, 4);
        sat_st(io, &c->hint, 4), sat_st(io, &c->alive, 4), sat_st(io, &c->last_top, 4);
    }
    for (u32 k = 0; k <= d && k <= MAXDEPTH + 1u; k++) {
        u32 fn = k < 512u ? m->stack[k] : 0u, pop = m->popped[k];
        sat_st(io, &fn, 4), sat_st(io, &m->site[k], 4), sat_st(io, &pop, 4), sat_st(io, m->saved[k], sizeof m->saved[k]);
        sat_st(io, &m->ftop[k], 4);
        if (sat_st_loading(io)) {
            if (k < 512u) m->stack[k] = fn;
            m->popped[k] = (unsigned char)pop;
        }
    }
}

void sat_call_loaded(void)
{
    SatCpu *m = &g_cpu[MASTER], *s = &g_cpu[SLAVE];
    cur = m, sat_interp_depth[0] = sat_interp_depth[1] = 0; /* a load left frames open */
    sat_mem_cpu(MASTER);
    sat_ret = SAT_PR_NONE, g_call_sp = 0, g_in_check = 0, g_irq = 0, g_hook = 0;
    m->tail = 0, s->tail = 0, m->ret_next = 0, s->ret_next = 0;
    if (g_dead) {
        DeleteFiber(g_dead);
        g_dead = NULL;
    }
    if (s->fiber) { /* where it was before the load: suspended, never resumed */
        DeleteFiber(s->fiber);
        s->fiber = NULL;
    }
    s->depth = 0;
    if (s->alive) { /* it was idle in its entry's loop (sat_call_unsafe): it starts that entry again, as SSHON leaves it */
        if (!m->fiber) m->fiber = IsThreadAFiber() ? GetCurrentFiber() : ConvertThreadToFiber(NULL);
        g_slave_hint0 = s->hint;
        g_slave_run = 0;
        s->fiber = CreateFiberEx(0, 16u << 20, 0, slave_proc, NULL);
    }
}
