/* Guest task switching by setjmp and longjmp: each guest context runs on a host fiber of its own.
 * A cooperative kernel (Saturn Bomberman's KRNL) saves a task with a setjmp-like routine (registers r8..r15, mach, macl
 * and pr stored through r4, 0 returned) and switches with a longjmp-like one (the same words loaded back, pr among them,
 * r5 returned). The translator cannot express a return to an address read from memory, so the kit does it:
 *
 * - sat_task_init finds the pair by their code, not by address: a function whose body is only stores through r4 of
 *   r8..r15 and pr (sts pr,r0) ending `rts; mov #0,r0`, and a routine whose body is only loads through r4 of the same
 *   words ending `rts; mov r5,r0`, r15 and pr at the same offsets in both. A game without such a pair is not touched.
 *   It also marks the functions that may call the setjmp: each one starting within 1020 bytes before its literal.
 * - A marked function runs inside sat_task_run, which keeps a host jmp_buf for that call (an SjFrame on this fiber).
 * - setjmp (sat_task_call): the buffer is written as the routine would, and remembered with the context and the frame
 *   that made the call. It returns 0, or the longjmp's value when that frame is being resumed.
 * - longjmp: r8..r14 are loaded from the buffer. A buffer saved by a frame still open is resumed: its context's fiber
 *   becomes the running one and that frame's function runs again from its start, its setjmp on the same buffer then
 *   returning the value (as on the CPU, where execution carries on after that setjmp with the saved registers). It
 *   starts again with r8..r14 as they were when it was first called, not the longjmp's: its prologue pushes them a
 *   second time over the words the CPU's epilogue pops, so the setjmp-time values would go back to its caller (KRNL's
 *   yield 06006d36 handed Saturn Bomberman's main task r13 = 26006020 for its own pointer 06006d36). A buffer
 *   the program filled itself (a new task: pr and r15 words not the ones the setjmp left) starts a new fiber that
 *   calls pr's function on r15's stack with r4 from the buffer; a task entry that returns starts over (pr is its entry).
 * Contexts belong to the SH-2 that made them: g_cpu[cpu].fiber is the running context's fiber, so the two CPUs' turns
 * (sat_call.c switch_to) carry on with whichever task each runs. Switches happen only at these calls, in program order.
 * Nearest existing: port/sdk/lib_ctx.c - the Dreamcast library's Lib_SwapContext on platform.h host coroutines; it is
 * keyed by named SDK calls, and host_coro keeps its own running fiber, which the Saturn kit's CPU fibers would bypass. */
#pragma warning(disable : 4611) /* setjmp in C: no destructors to skip */
#include <setjmp.h>
#include <stdlib.h>
#include <string.h>
#include <windows.h>
#include "sat.h"
#include "sat_cpu.h"

enum { CTXS = 64, BUFS = 256, R_PR = 16, R_MACH = 17, R_MACL = 18, NREG = 19, SPECIAL_R4 = 19 };
typedef struct SjFrame {
    jmp_buf jb;
    struct SjFrame *up;
    const SatFn *fn;
    TcArgs args;
    u32 serial, top;
    u32 entry_r[7]; /* r8..r14 when the function was called: what its prologue pushed */
    int depth, replay;
    u32 replay_buf, replay_val;
} SjFrame;
typedef struct {
    void *fiber;
    int cpu, used, base;
    SjFrame *top, *pending;
    int depth;
    u32 sp, entry_sp, last_top, entry, sp0, a0, val;
    u32 stack[512];
    unsigned char popped[MAXDEPTH + 2];
    u32 site[MAXDEPTH + 2], saved[MAXDEPTH + 2][7]; /* SatCpu's, per frame of this context */
} Ctx;
typedef struct { u32 buf, serial, prw, spw; Ctx *ctx; int logged; } BufRec;

int sat_task_on;
unsigned char *sat_task_flags;
static u32 g_sj, g_lj, g_serial, g_nstart, g_nresume;
static int g_st[NREG], g_ld[NREG + 1]; /* byte offsets in the buffer, -1 none: what the setjmp stores, what the longjmp loads */
static Ctx g_ctx[CTXS], *g_cur[2];
static BufRec g_buf[BUFS];
extern u32 sat_entry_sp;

static u16 op(u32 a) { u32 w = sat_peek32(a & ~3u); return (u16)((a & 2u) ? w : w >> 16); }

/* mov.l Rm,@(d,r4) and sts pr/mach/macl,r0 + mov.l r0,@(d,r4), ending rts; mov #0,r0 */
static int shape_setjmp(u32 a, int st[NREG])
{
    int r0src = -1;
    for (int i = 0; i < NREG; i++) st[i] = -1;
    for (int i = 0; i < 40; i++, a += 2u) {
        u16 o = op(a);
        if ((o & 0xff00u) == 0x1400u) {
            int m = (o >> 4) & 15;
            if (m == 0 && r0src < 0) return 0;
            st[m ? m : r0src] = (o & 15) * 4;
        } else if (o == 0x000au || o == 0x001au || o == 0x002au)
            r0src = o == 0x000au ? R_MACH : o == 0x001au ? R_MACL : R_PR;
        else if (o == 0x000bu) {
            if (op(a + 2u) != 0xe000u) return 0;
            for (int r = 8; r <= 15; r++)
                if (st[r] < 0) return 0;
            return st[R_PR] >= 0;
        } else
            return 0;
    }
    return 0;
}

/* mov.l @(d,r4),Rn, lds r0,pr/mach/macl, mov r0,r4, ending rts; mov r5,r0 */
static int shape_longjmp(u32 a, int ld[NREG + 1])
{
    int r0 = -1;
    for (int i = 0; i <= NREG; i++) ld[i] = -1;
    for (int i = 0; i < 40; i++, a += 2u) {
        u16 o = op(a);
        if ((o & 0xf0f0u) == 0x5040u) {
            int n = (o >> 8) & 15;
            if (n == 4) return 0;
            if (n == 0) r0 = (o & 15) * 4;
            else ld[n] = (o & 15) * 4;
        } else if (o == 0x400au || o == 0x401au || o == 0x402au) {
            if (r0 < 0) return 0;
            ld[o == 0x400au ? R_MACH : o == 0x401au ? R_MACL : R_PR] = r0;
        } else if (o == 0x6403u)
            ld[SPECIAL_R4] = r0;
        else if (o == 0x000bu) {
            if (op(a + 2u) != 0x6053u) return 0;
            for (int r = 8; r <= 15; r++)
                if (ld[r] < 0) return 0;
            return ld[R_PR] >= 0;
        } else
            return 0;
    }
    return 0;
}

static int fn_index(u32 addr) /* the function holding addr: the last one starting at or before it */
{
    int lo = 0, hi = sat_nfns - 1, best = -1;
    while (lo <= hi) {
        int mid = (lo + hi) >> 1;
        if (sat_fns[mid].addr <= addr) best = mid, lo = mid + 1;
        else hi = mid - 1;
    }
    return best;
}

void sat_task_init(void)
{
    int ld[NREG + 1], callers = 0;
    u32 lo, hi;
    if (sat_nfns <= 0) return;
    lo = sat_fns[0].addr & ~3u, hi = sat_fns[sat_nfns - 1].addr + 0x1000u;
    for (int i = 0; i < sat_nfns && !g_sj; i++)
        if (shape_setjmp(sat_fns[i].addr, g_st)) g_sj = sat_fns[i].addr;
    if (!g_sj) return;
    for (u32 a = lo; a < hi && !g_lj; a += 2u) /* the longjmp may have no translated function: look in the image */
        if ((op(a) & 0xf0f0u) == 0x5040u && (a < 2u || (op(a - 2u) & 0xf0f0u) != 0x5040u) && shape_longjmp(a, ld) &&
            ld[15] == g_st[15] && ld[R_PR] == g_st[R_PR])
            g_lj = a, memcpy(g_ld, ld, sizeof ld);
    if (!g_lj) {
        port_log("task: setjmp-like routine at %08x but no longjmp to pair it with; not modelled\n", g_sj);
        return;
    }
    sat_task_flags = (unsigned char *)calloc((size_t)sat_nfns, 1);
    for (u32 a = lo; a < hi; a += 4u) {
        u32 w = sat_peek32(a) & 0xdfffffffu;
        /* a mov.l @(disp,pc) reaches at most 1020 bytes ahead: mark every function starting that close before the
         * literal (the table holds entries inside functions too, so the one just before it need not be the caller) */
        for (int i = w == g_sj ? fn_index(a) : -1; i >= 0 && sat_fns[i].addr + 1024u > a; i--)
            if (!sat_task_flags[i]) sat_task_flags[i] = 1, callers++;
    }
    sat_task_on = 1;
    port_log("task: setjmp %08x, longjmp %08x (r15 at +%x, pr at +%x): each guest context on a fiber; %d functions call the setjmp\n",
             g_sj, g_lj, g_ld[15], g_ld[R_PR], callers);
}

/* ---- contexts */
static SatCpu *cpu_now(void) { return &g_cpu[sat_cpu_id()]; }

static Ctx *ctx_new(int cpu)
{
    for (int i = 0; i < CTXS; i++)
        if (!g_ctx[i].used) {
            Ctx *x = &g_ctx[i];
            memset(x, 0, sizeof *x);
            x->used = 1, x->cpu = cpu;
            return x;
        }
    port_exit("task: more guest contexts than the kit keeps", 1);
    return NULL;
}

static Ctx *ctx_cur(void) /* the running context; the CPU's own fiber the first time */
{
    int cpu = sat_cpu_id();
    if (!g_cur[cpu]) {
        if (!g_cpu[cpu].fiber) /* already a fiber with a window (host_modal.cpp): see sat_call.c sat_minit */
            g_cpu[cpu].fiber = IsThreadAFiber() ? GetCurrentFiber() : ConvertThreadToFiber(NULL);
        g_cur[cpu] = ctx_new(cpu);
        g_cur[cpu]->base = 1;
        g_cur[cpu]->fiber = g_cpu[cpu].fiber;
    }
    return g_cur[cpu];
}

static void ctx_kill(Ctx *x)
{
    if (x->fiber && !x->base) DeleteFiber(x->fiber);
    for (int i = 0; i < BUFS; i++)
        if (g_buf[i].ctx == x) g_buf[i].ctx = NULL;
    x->used = 0, x->fiber = NULL;
}

/* SSHOFF: the slave's contexts go; its own fiber is deleted by the caller (sat_call.c sat_slave_off) */
void sat_task_cpu_off(int cpu)
{
    for (int i = 0; i < CTXS; i++)
        if (g_ctx[i].used && g_ctx[i].cpu == cpu) {
            if (g_ctx[i].fiber == g_cpu[cpu].fiber) g_ctx[i].fiber = NULL, g_ctx[i].base = 1; /* deleted by the caller */
            ctx_kill(&g_ctx[i]);
        }
    g_cur[cpu] = NULL;
}

static void ctx_save(Ctx *x, const SatCpu *c)
{
    int n = c->depth < 0 ? 0 : c->depth;
    x->depth = c->depth, x->sp = c->sp, x->entry_sp = sat_entry_sp, x->last_top = c->last_top;
    memcpy(x->stack, c->stack, sizeof(u32) * (size_t)(n + 1 < 512 ? n + 1 : 512));
    memcpy(x->popped, c->popped, (size_t)(n + 2 < MAXDEPTH + 2 ? n + 2 : MAXDEPTH + 2));
    memcpy(x->site, c->site, sizeof(u32) * (size_t)(n + 2 < MAXDEPTH + 2 ? n + 2 : MAXDEPTH + 2));
    memcpy(x->saved, c->saved, sizeof x->saved[0] * (size_t)(n + 2 < MAXDEPTH + 2 ? n + 2 : MAXDEPTH + 2));
}

static void ctx_load(const Ctx *x, SatCpu *c)
{
    int n = x->depth < 0 ? 0 : x->depth;
    c->depth = x->depth, c->sp = x->sp, sat_entry_sp = x->entry_sp, c->last_top = x->last_top, c->tail = 0;
    memcpy(c->stack, x->stack, sizeof(u32) * (size_t)(n + 1 < 512 ? n + 1 : 512));
    memcpy(c->popped, x->popped, (size_t)(n + 2 < MAXDEPTH + 2 ? n + 2 : MAXDEPTH + 2));
    memcpy(c->site, x->site, sizeof(u32) * (size_t)(n + 2 < MAXDEPTH + 2 ? n + 2 : MAXDEPTH + 2));
    memcpy(c->saved, x->saved, sizeof x->saved[0] * (size_t)(n + 2 < MAXDEPTH + 2 ? n + 2 : MAXDEPTH + 2));
}

/* The running context hands the CPU to `to`. Returns only if this context is resumed with nothing to unwind to. */
static void ctx_switch(Ctx *to)
{
    int cpu = sat_cpu_id();
    SatCpu *c = &g_cpu[cpu];
    Ctx *me = ctx_cur();
    ctx_save(me, c);
    ctx_load(to, c);
    g_cur[cpu] = to;
    c->fiber = to->fiber;
    SwitchToFiber(to->fiber);
    me = g_cur[sat_cpu_id()];
    if (me->pending) {
        SjFrame *f = me->pending;
        me->pending = NULL;
        longjmp(f->jb, 1);
    }
}

static VOID CALLBACK task_proc(PVOID p)
{
    Ctx *x = (Ctx *)p;
    for (int n = 0;; n++) {
        if (n == 1) port_log("task: the context started at %08x returned; it starts over (its pr is its entry)\n", x->entry);
        sat_call(x->sp0, x->entry, x->a0, 0, 0, 0);
    }
}

/* ---- the marked functions' frames */
u32 sat_task_run(const SatFn *f, u32 top, const TcArgs *a)
{
    SjFrame fr;
    SjFrame *volatile p = &fr;
    Ctx *x = ctx_cur();
    SatCpu *c = cpu_now();
    memset(&fr, 0, sizeof fr);
    fr.up = x->top, fr.fn = f, fr.args = *a, fr.serial = ++g_serial, fr.top = top, fr.depth = c->depth;
    for (int i = 0; i < 7; i++) fr.entry_r[i] = c->r[8 + i];
    x->top = &fr;
    if (setjmp(fr.jb)) { /* resumed by a longjmp: the frames above are gone; run the function again from its start */
        c = cpu_now();
        x = ctx_cur();
        x->top = p;
        c->depth = p->depth, c->tail = 0, c->sp = p->top - f->frame, sat_entry_sp = p->top;
        for (int i = 0; i < 4; i++) c->r[4 + i] = p->args.r[i], c->in_r[i] = p->args.rlo[i];
        for (int i = 0; i < 7; i++) c->r[8 + i] = p->entry_r[i]; /* the caller's, as its prologue saw them */
    }
    u32 r = p->fn->fn(p->args.r[0], p->args.r[1], p->args.r[2], p->args.r[3]);
    ctx_cur()->top = p->up;
    return r;
}

static BufRec *buf_rec(u32 buf, int make)
{
    BufRec *free_rec = NULL;
    for (int i = 0; i < BUFS; i++) {
        if (g_buf[i].buf == buf) return &g_buf[i];
        if (!g_buf[i].buf && !free_rec) free_rec = &g_buf[i];
    }
    if (!make) return NULL;
    if (!free_rec) port_exit("task: more saved contexts than the kit keeps", 1);
    memset(free_rec, 0, sizeof *free_rec);
    free_rec->buf = buf;
    return free_rec;
}

static u32 do_setjmp(u32 buf)
{
    SatCpu *c = cpu_now();
    Ctx *x = ctx_cur();
    SjFrame *f = x->top;
    u32 caller = c->depth > 0 && c->depth <= 512 ? c->stack[c->depth - 1] : 0;
    BufRec *b = buf_rec(buf, 1);
    for (int r = 8; r <= 14; r++) wr32(buf + (u32)g_st[r], c->r[r]);
    wr32(buf + (u32)g_st[15], c->sp);
    wr32(buf + (u32)g_st[R_PR], caller);
    if (g_st[R_MACH] >= 0) wr32(buf + (u32)g_st[R_MACH], 0);
    if (g_st[R_MACL] >= 0) wr32(buf + (u32)g_st[R_MACL], 0);
    b->ctx = x, b->prw = caller, b->spw = c->sp, b->serial = 0;
    if (!f || f->fn->addr != caller) {
        if (!b->logged) port_log("task: setjmp(%08x) from %08x, which the kit did not mark; a longjmp to it cannot resume\n", buf, caller);
        b->logged = 1;
        return 0;
    }
    b->serial = f->serial;
    if (f->replay && f->replay_buf == buf) {
        f->replay = 0;
        return f->replay_val;
    }
    return 0;
}

static SjFrame *frame_of(const Ctx *x, u32 serial)
{
    for (SjFrame *f = x->top; f; f = f->up)
        if (f->serial == serial) return f;
    return NULL;
}

static u32 do_longjmp(u32 buf, u32 val)
{
    SatCpu *c = cpu_now();
    Ctx *me = ctx_cur(), *t;
    BufRec *b = buf_rec(buf, 0);
    u32 prw = sat_peek32(buf + (u32)g_ld[R_PR]), spw = sat_peek32(buf + (u32)g_ld[15]);
    for (int r = 8; r <= 14; r++) c->r[r] = sat_peek32(buf + (u32)g_ld[r]);
    if (b && b->ctx && b->prw == prw && b->spw == spw) { /* a saved context: resume the frame that saved it */
        SjFrame *f = b->serial ? frame_of(b->ctx, b->serial) : NULL;
        if (!f || b->ctx->cpu != sat_cpu_id()) {
            if (!b->logged) port_log("task: longjmp(%08x): the context that saved it has no open frame to resume\n", buf);
            b->logged = 1;
            return 0;
        }
        f->replay = 1, f->replay_buf = buf, f->replay_val = val;
        if (g_nresume++ < 4) port_log("task: resume buffer %08x in %08x (value %08x)\n", buf, f->fn->addr, val);
        if (b->ctx == me) longjmp(f->jb, 1);
        b->ctx->pending = f;
        ctx_switch(b->ctx);
        return 0;
    }
    /* a new context: pr's function on r15's stack, r4 from the buffer */
    int i = fn_index(prw & 0xdfffffffu);
    if (i < 0 || sat_fns[i].addr != (prw & 0xdfffffffu) || !spw) {
        if (!b || !b->logged) port_log("task: longjmp(%08x) starts pr %08x sp %08x, which the kit cannot run\n", buf, prw, spw);
        if (b) b->logged = 1;
        return 0;
    }
    if (b && b->ctx && b->ctx != me && !b->ctx->base) ctx_kill(b->ctx); /* the slot's old task is gone */
    t = ctx_new(sat_cpu_id());
    t->entry = sat_fns[i].addr, t->sp0 = spw, t->val = val, t->entry_sp = spw;
    t->a0 = g_ld[SPECIAL_R4] >= 0 ? sat_peek32(buf + (u32)g_ld[SPECIAL_R4]) : 0;
    t->fiber = CreateFiberEx(0, 16u << 20, 0, task_proc, t);
    if (!t->fiber) port_exit("task: the host could not make a fiber", 1);
    b = buf_rec(buf, 1);
    b->ctx = t, b->prw = prw, b->spw = spw, b->serial = 0;
    if (g_nstart++ < 24) port_log("task: buffer %08x starts %08x sp %08x r4 %08x\n", buf, t->entry, spw, t->a0);
    ctx_switch(t);
    return 0;
}

/* sat_call.c tc_call, while sat_task_on: 1 and the result when the call is the setjmp or the longjmp */
int sat_task_call(u32 target, const TcArgs *a, u32 *ret)
{
    if (target == g_sj) return *ret = do_setjmp(a->r[0]), 1;
    if (target == g_lj) return *ret = do_longjmp(a->r[0], a->r[1]), 1;
    return 0;
}
