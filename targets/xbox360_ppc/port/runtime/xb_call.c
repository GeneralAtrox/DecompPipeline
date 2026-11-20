/* tc_call for the whole game: a guest address is looked up in the table gen_xbox.py wrote (the game's own translated
 * functions), then in the import table (host calls). A target in neither is a stop: that function is translated next. */
#include <windows.h>
#include <setjmp.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "xb.h"
#include "xb_game.h"
#if __has_include("perf.h")   /* the Core's sampler (perf_thread_stack, perf_thread): not in the Core yet (the perf toolkit's round); */
#include "perf.h"              /* Too Human's own kit line carries it, and this kit's hook below is built only where it is there */
#define XB_PERF 1
#endif

__declspec(thread) u32 tc_sp;
__declspec(thread) u32 tc_r13 = 0x7FFF0000u;   /* the guest thread's processor block; the main thread's is set up by xb_mem_init */
__declspec(thread) u32 tc_vr[128][4]; __declspec(thread) u64 tc_gr[32]; __declspec(thread) f64 tc_fr[32];   /* the port form's v0..v127, and r / f values a caller hands on (fn_parts.vector_abi, hand_over) */
__declspec(thread) const u64 *tc_nv;      /* the caller's r14..r31 for a funclet */
__declspec(thread) u32 tc_r12;      /* the caller r12: a funclet (a __finally block) takes its parent frame from it */
static __declspec(thread) u64 s_last_r;
static __declspec(thread) f64 s_last_f;
static __declspec(thread) u32 s_trace[32];
static __declspec(thread) unsigned s_trace_n;
static __declspec(thread) u32 s_stack[256];
static __declspec(thread) const TcArgs *s_frame[256];   /* each live call's TcArgs: the C++ throw's frame walk (xb_eh.h) reads ip and sp */
static __declspec(thread) u32 s_stack_arg[256][3];   /* r3..r5 of each call on the stack, for a stop's report */
static __declspec(thread) unsigned s_depth;
static __declspec(thread) int s_listed;
static unsigned char *s_touched;
static unsigned s_touched_n;
static u32 s_first[48];   /* the first functions called, in order: where a run began to go wrong */

/* XB_ARGLOG=target@caller (hex, bring-up): calls of target with that caller among the three innermost frames (no @: every call). The first 8 go in the
 * log; the last 8 are kept and shown with a stop's last calls (xb_trace_dump): the arguments a faulting direct call got, which nothing else records */
typedef struct { u32 tid, frame, r[6], w3[8], w4[8], up[3]; } ArgRec;
static ArgRec s_argring[8];
static volatile LONG s_argring_n;
static u32 s_arglog_t = ~0u, s_arglog_c;

static void arglog_line(const char *head, long n, const ArgRec *a)
{
    port_log("%scall %ld of %08x (frame %u, thread %u, from %08x %08x %08x): r3..r8 %08x %08x %08x %08x %08x %08x; at r3 %08x %08x %08x %08x %08x %08x %08x %08x; at r4 %08x %08x %08x %08x %08x %08x %08x %08x\n",
             head, n, s_arglog_t, a->frame, a->tid, a->up[0], a->up[1], a->up[2], a->r[0], a->r[1], a->r[2], a->r[3], a->r[4], a->r[5],
             a->w3[0], a->w3[1], a->w3[2], a->w3[3], a->w3[4], a->w3[5], a->w3[6], a->w3[7], a->w4[0], a->w4[1], a->w4[2], a->w4[3], a->w4[4], a->w4[5], a->w4[6], a->w4[7]);
}

static void arglog_report(void)
{
    long n = s_argring_n, i;
    if (!n) return;
    port_log("arglog: %ld calls of %08x from %08x; the last %ld, oldest first:\n", n, s_arglog_t, s_arglog_c, n < 8 ? n : 8L);
    for (i = n < 8 ? 0 : n - 8; i < n; i++) arglog_line("arglog kept ", i, &s_argring[i & 7]);
}

static const XbFn *find_fn(u32 a)
{
    unsigned lo = 0, hi = xb_fn_count;
    while (lo < hi) {
        unsigned m = (lo + hi) >> 1;
        if (xb_fn_table[m].addr < a) lo = m + 1;
        else hi = m;
    }
    return lo < xb_fn_count && xb_fn_table[lo].addr == a ? &xb_fn_table[lo] : NULL;
}

#define DYN_BASE 0x7FFD0000u   /* dynamic import addresses: 16 bytes apart, never mapped */
static XbImp s_dyn[256];
static unsigned s_dyn_n;

u32 xb_dyn_import(const char *lib, unsigned ord)
{
    unsigned l, i;
    for (l = 0; l < xb_lib_count && strcmp(xb_lib_name[l], lib); l++) {}
    if (l == xb_lib_count) return 0;
    for (i = 0; i < s_dyn_n; i++)
        if (s_dyn[i].lib == l && s_dyn[i].ord == ord) return DYN_BASE + 16 * i;
    if (s_dyn_n == 256) xb_stop("out_of_memory", "dynamic imports", ord);
    s_dyn[s_dyn_n].thunk = DYN_BASE + 16 * s_dyn_n;
    s_dyn[s_dyn_n].lib = (u16)l;
    s_dyn[s_dyn_n].ord = (u16)ord;
    return DYN_BASE + 16 * s_dyn_n++;
}

static const XbImp *find_imp(u32 a)
{
    if (a >= DYN_BASE && a < DYN_BASE + 16 * s_dyn_n && !(a & 15)) return &s_dyn[(a - DYN_BASE) >> 4];
    unsigned lo = 0, hi = xb_imp_count;
    while (lo < hi) {
        unsigned m = (lo + hi) >> 1;
        if (xb_imp_table[m].thunk < a) lo = m + 1;
        else hi = m;
    }
    return lo < xb_imp_count && xb_imp_table[lo].thunk == a ? &xb_imp_table[lo] : NULL;
}

const char *xb_export_name(const char *lib, unsigned ord)
{
    unsigned i;
    for (i = 0; i < xb_name_count; i++)
        if (xb_name_table[i].ord == ord && !strcmp(xb_name_table[i].lib, lib)) return xb_name_table[i].name;
    return "?";
}

unsigned xb_touched(void) { return s_touched_n; }

/* The translated function at table index i, when the run called it (0 otherwise): the list a judged-or-not count is made from. */
u32 xb_touched_addr(unsigned i) { return s_touched && s_touched[i] ? xb_fn_table[i].addr : 0; }

/* Every guest thread's stack, so a run the watchdog ends says where each thread was (a hang has no stop of its own). The entries point into
 * each thread's own storage (__declspec(thread)), which Windows frees when the thread ends: a thread that ends (returns, or ExTerminateThread's
 * ExitThread) clears its entry from a fiber-local slot's callback, which runs on its way out before that storage goes, under the lock the dump
 * holds while it reads, so a dump never reads a thread that has gone (it crashed in xb_threads_dump with an access violation). A critical
 * section, not an SRW lock: during process exit Windows hands over one a killed thread held. Entries stay in place (a profiler numbers threads by
 * their slot); an ended thread's id reads 0 */
static struct { const u32 *stack; const u32 (*arg)[3]; const unsigned *depth; unsigned long id; } s_threads[64];
static volatile long s_thread_n;
static CRITICAL_SECTION s_threads_cs;
static INIT_ONCE s_threads_once = INIT_ONCE_STATIC_INIT;
static DWORD s_threads_fls = FLS_OUT_OF_INDEXES;

static void WINAPI thread_gone(void *slot)
{
    long k = (long)(UINT_PTR)slot - 1;
    EnterCriticalSection(&s_threads_cs);
    if (k >= 0 && k < 64) s_threads[k].stack = NULL, s_threads[k].arg = NULL, s_threads[k].depth = NULL;
    LeaveCriticalSection(&s_threads_cs);
}

static BOOL CALLBACK threads_init(PINIT_ONCE once, void *unused, void **ctx)
{
    (void)once, (void)unused, (void)ctx;
    InitializeCriticalSection(&s_threads_cs);
    s_threads_fls = FlsAlloc(thread_gone);
    return TRUE;
}

/* This thread joins the list (its first guest call) */
static void thread_listed(void)
{
    long k;
    InitOnceExecuteOnce(&s_threads_once, threads_init, NULL, NULL);
    EnterCriticalSection(&s_threads_cs);
    k = s_thread_n;
    s_thread_n = k + 1;
    if (k < 64) {
        s_threads[k].stack = s_stack, s_threads[k].arg = s_stack_arg, s_threads[k].depth = &s_depth, s_threads[k].id = GetCurrentThreadId();
        if (s_threads_fls != FLS_OUT_OF_INDEXES) FlsSetValue(s_threads_fls, (void *)(UINT_PTR)(k + 1));
    }
    LeaveCriticalSection(&s_threads_cs);
    s_listed = 1;
#ifdef XB_PERF
    if (g_perf_on) perf_thread_stack(s_stack, &s_depth, 255u), perf_thread(PERF_ANY); /* the Core's sampler (base/perf.h) reads this stack */
#endif
}

void xb_threads_dump(void)
{
    long k, n;
    unsigned i, d;
    InitOnceExecuteOnce(&s_threads_once, threads_init, NULL, NULL);
    EnterCriticalSection(&s_threads_cs);
    n = s_thread_n < 64 ? s_thread_n : 64;
    for (k = 0; k < n; k++) {
        if (!s_threads[k].stack) { port_log("guest thread %lu has ended\n", s_threads[k].id); continue; }
        d = *s_threads[k].depth;
        port_log("guest thread %lu stack (innermost first):", s_threads[k].id);
        for (i = 0; i < d && i < 24; i++) port_log(" %08x", s_threads[k].stack[(d - 1 - i) & 255]);
        port_log(";");   /* r3 r4 r5 of the innermost calls: what a wait waits on */
        for (i = 0; i < d && i < 4; i++) {
            const u32 *g = s_threads[k].arg[(d - 1 - i) & 255];
            port_log(" [%08x %08x %08x]", g[0], g[1], g[2]);
        }
        port_log("\n");
    }
    LeaveCriticalSection(&s_threads_cs);
}

/* The host thread ids of the guest threads, for XB_DATABP to arm them all (0 for one that has ended: OpenThread refuses it) */
unsigned xb_thread_ids(unsigned long *out, unsigned max)
{
    long k, n;
    InitOnceExecuteOnce(&s_threads_once, threads_init, NULL, NULL);
    EnterCriticalSection(&s_threads_cs);
    n = s_thread_n < (long)max ? s_thread_n : (long)max;
    if (n > 64) n = 64;
    for (k = 0; k < n; k++) out[k] = s_threads[k].stack ? s_threads[k].id : 0;
    LeaveCriticalSection(&s_threads_cs);
    return (unsigned)n;
}
/* end of the thread list */

/* The function `up` calls out from this thread's innermost one (0 the innermost), or 0: the frame log names a library call's caller by it */
u32 xb_caller(unsigned up) { return up < s_depth ? s_stack[(s_depth - 1 - up) & 255] : 0; }

/* This thread's innermost calls into buf, for a log line */
void xb_stack_line(char *buf, unsigned size)
{
    unsigned i, at = 0;
    buf[0] = 0;
    for (i = 0; i < s_depth && i < 12 && at + 10 < size; i++) at += (unsigned)sprintf_s(buf + at, size - at, " %08x", s_stack[(s_depth - 1 - i) & 255]);
}

void xb_trace_dump(void)
{
    unsigned i, n = s_trace_n < 32 ? s_trace_n : 32;
    port_log("last calls (oldest first):");
    for (i = 0; i < n; i++) port_log(" %08x", s_trace[(s_trace_n - n + i) & 31]);
    port_log("\n");
    port_log("first calls:");
    for (i = 0; i < s_touched_n && i < 48; i++) port_log(" %08x", s_first[i]);
    port_log("\n");
    arglog_report();
    port_log("call stack (innermost first):");
    for (i = 0; i < s_depth && i < 40; i++) port_log(" %08x", s_stack[(s_depth - 1 - i) & 255]);
    port_log("\n");
    port_log("call stack arguments r3 r4 r5 (innermost first):");
    for (i = 0; i < s_depth && i < 12; i++) {
        const u32 *g = s_stack_arg[(s_depth - 1 - i) & 255];
        port_log(" [%08x: %08x %08x %08x]", s_stack[(s_depth - 1 - i) & 255], g[0], g[1], g[2]);
    }
    port_log("\n");
    if (s_depth) {   /* the objects the innermost call was given (r3, then r4): a fault on a pointer read from one shows what overwrote it (text where pointers were) */
        const u32 *g = s_stack_arg[(s_depth - 1) & 255];
        unsigned k;
        for (k = 0; k < 2 && !(k && (g[1] & ~15u) == (g[0] & ~15u)); k++) {
            u32 at = (g[k] & ~15u) - 0x40u, j, b;
            MEMORY_BASIC_INFORMATION m;
            if (at < 0xFFFF0000u && VirtualQuery(tc_base + at, &m, sizeof m) && m.State == MEM_COMMIT && (u8 *)m.BaseAddress + m.RegionSize >= tc_base + at + 0x140u)
                for (j = 0; j < 0x140u; j += 32) {
                    char text[33];
                    port_log("memory %08x:", at + j);
                    for (b = 0; b < 32; b += 4) port_log(" %08x", rd32(at + j + b));
                    for (b = 0; b < 32; b++) { u8 c = rd8(at + j + b); text[b] = c >= 32 && c < 127 ? (char)c : '.'; }
                    text[32] = 0;
                    port_log("  %s\n", text);
                }
            xb_file_reads_over(at, at + 0x140u);
        }
    }
    xb_scan_fill(0);   /* the game's known fill word, if its xb_game.h names one */
}

/* XB_TRACE=821b9b58,82266010 logs the arguments and result of calls to those functions (a diagnostic; unset it is one test) */
static u32 s_watch[16];
static unsigned s_watch_n;
static int s_watch_init;

static void watch_init(void)
{
    const char *e = getenv("XB_TRACE");
    s_watch_init = 1;
    while (e && *e && s_watch_n < 16) {
        char *end;
        s_watch[s_watch_n++] = (u32)strtoul(e, &end, 16);
        e = *end == ',' ? end + 1 : NULL;
    }
}

/* XB_TRACE_AT=n[,n...]: XB_TRACE's lines only in the frames n after the scene marker's latest start (xb_mark_frame), at most 6000 lines; unset, every frame */
static int trace_now(void)
{
    static int nat = -1;
    static unsigned at[8];
    static volatile LONG lines;
    unsigned i, m = xb_mark_frame(), f = xb_frame_number();
    if (nat < 0) {
        const char *e = getenv("XB_TRACE_AT");
        nat = 0;
        while (e && *e && nat < 8) {
            char *end;
            at[nat++] = (unsigned)strtoul(e, &end, 10);
            e = *end == ',' ? end + 1 : NULL;
        }
        if (!getenv("XB_TRACE_AT")) nat = -2;
    }
    if (nat == -2) return 1;
    if (m == ~0u) return 0;
    for (i = 0; i < (unsigned)nat; i++)
        if (f == m + at[i]) return InterlockedIncrement(&lines) <= 6000;
    return 0;
}

static int watched(u32 a)
{
    unsigned i;
    if (!s_watch_init) watch_init();
    for (i = 0; i < s_watch_n; i++) if (s_watch[i] == a) return trace_now();
    return 0;
}

/* With XB_SAMPLE, how often each import was called and what it last returned, so a sample says which kernel or XAM answer a waiting screen keeps asking for */
static volatile LONG *s_imp_calls;
static LONG *s_imp_shown;
static u64 *s_imp_last;

void xb_import_counts_start(void)
{
    s_imp_shown = (LONG *)calloc(xb_imp_count, sizeof *s_imp_shown);
    s_imp_last = (u64 *)calloc(xb_imp_count, sizeof *s_imp_last);
    s_imp_calls = (volatile LONG *)calloc(xb_imp_count, sizeof *s_imp_calls);
}

void xb_import_counts_since(void)
{
    unsigned k;
    if (!s_imp_calls) return;
    port_log("imports since the last sample:");
    for (k = 0; k < xb_imp_count; k++) {
        LONG n = s_imp_calls[k];
        if (n == s_imp_shown[k]) continue;
        port_log(" %s %ld (-> %llx);", xb_export_name(xb_lib_name[xb_imp_table[k].lib], xb_imp_table[k].ord), n - s_imp_shown[k], s_imp_last[k]);
        s_imp_shown[k] = n;
    }
    port_log("\n");
}

static u64 tc_call_inner(u32 target, const TcArgs *args);

/* The word at a guest address for a trace line, or 0 where nothing is committed there (an argument that is a number, not a pointer) */
static u32 trace_word(u32 a)
{
    MEMORY_BASIC_INFORMATION m;
    if (a < 0x10000u || (a & 3u) || !VirtualQuery(tc_base + a, &m, sizeof m) || m.State != MEM_COMMIT) return 0;
    return rd32(a);
}

#ifdef XB_TRACE_PARAMS
/* XB_TRACE_PARAMS (xb_game.h) {target, argument register, effect field, permutation key, handle address, entry size, name field}: at a traced
 * call of target (XB_TRACE, XB_TRACE_AT), the handle word and the name word of each pixel-shader parameter of the effect's permutation (the
 * game's hash map: buckets at +0x18, mask +0x1c minus 1, 12-byte nodes at +0xc of next, key, value; the value's first word the parameter list,
 * its entries at +4, count at +8), with any name string an entry points at: which parameter a draw matches the handle with */
static void trace_params(u32 target, const TcArgs *a)
{
    static const u32 tp[7] = XB_TRACE_PARAMS;
    u32 fx, slot, node = 0, list, base, n, i, k;
    if (target != tp[0]) return;
    fx = trace_word((u32)a->r[tp[1]] + tp[2]);
    port_log("trace params %08x: effect %08x, handle %08x", target, fx, trace_word(tp[4]));
    if (!fx || !trace_word(fx + 0x18) || (int)trace_word(fx + 0x10) <= 0) { port_log(", no permutations\n"); return; }
    for (slot = trace_word(trace_word(fx + 0x18) + 4 * ((trace_word(fx + 0x1c) - 1) & tp[3])), i = 0; slot != ~0u && i < 64; slot = trace_word(node), i++) {
        node = trace_word(fx + 0xc) + slot * 12;
        if (trace_word(node + 4) == tp[3]) break;
    }
    if (slot == ~0u || i == 64) { port_log(", no permutation %x\n", tp[3]); return; }
    list = trace_word(trace_word(node + 8)), base = trace_word(list + 4), n = trace_word(list + 8);
    port_log(", permutation %x pixel parameters %08x: %u\n", tp[3], list, n);
    for (i = 0; i < n && i < 24; i++) {
        u32 e = base + i * tp[5];
        char name[48] = "";
        for (k = 0; k < 4 && !name[0]; k++) {   /* a word of the entry that points at a printable name */
            u32 s = trace_word(e + 4 * k), j;
            if (!s || !trace_word(s & ~3u)) continue;
            for (j = 0; j < 47; j++) { u8 c = rd8(s + j); if (!c) break; if (c < 32 || c > 126) { j = 0; break; } name[j] = (char)c; }
            name[j] = 0;
        }
        port_log("trace params   %u: name word %08x%s, words %08x %08x %08x %08x \"%s\"\n", i, trace_word(e + tp[6]), trace_word(e + tp[6]) == trace_word(tp[4]) ? " = handle" : "",
                 trace_word(e), trace_word(e + 4), trace_word(e + 8), trace_word(e + 12), name);
    }
}
#endif

/* XB_FAULT: a test's planted fault on the game's own path, for the paths a normal run never takes (the C++ throw and longjmp, both
 * error-only in Too Human). Up to four ';'-separated specs, each fired once per run:
 *   zero:TARGET@CALLER#N     the Nth call (default the first) of TARGET made by CALLER returns 0 without running (an allocator that
 *                            fails: Lua's luaM_realloc
 *                            then raises its memory error, which longjmps to luaD_rawrunprotected)
 *   garble:TARGET:REG:BYTES  before the first call of TARGET, BYTES bytes at the guest pointer in register REG become 0xA5 (a tree file
 *                            the SpeedTree parser cannot read, which throws to its loader's catch)
 * Each firing is a log line. Unset, the cost is one test of a flag per call. */
typedef struct { int kind; u32 target, caller, reg, bytes; volatile LONG skip, fired; } XbFault;
static XbFault s_fault[4];
static int s_fault_n = -1;

static void fault_setup(void)
{
    const char *e = getenv("XB_FAULT");
    int n = 0;
    while (e && *e && n < 4) {
        XbFault *f = &s_fault[n];
        char *end;
        memset(f, 0, sizeof *f);
        if (!strncmp(e, "zero:", 5)) {
            f->kind = 1, f->target = (u32)strtoul(e + 5, &end, 16);
            f->caller = *end == '@' ? (u32)strtoul(end + 1, &end, 16) : 0;
            f->skip = *end == '#' ? (LONG)strtoul(end + 1, &end, 10) - 1 : 0;
        } else if (!strncmp(e, "garble:", 7)) {
            f->kind = 2, f->target = (u32)strtoul(e + 7, &end, 16);
            f->reg = *end == ':' ? (u32)strtoul(end + 1, &end, 10) : 4;
            f->bytes = *end == ':' ? (u32)strtoul(end + 1, &end, 0) : 16;
        } else break;
        port_log("fault planted: %s %08x reg/caller %08x bytes %u (XB_FAULT)\n", f->kind == 1 ? "zero" : "garble", f->target,
                 f->kind == 1 ? f->caller : f->reg, f->bytes);
        n++;
        e = strchr(end, ';') ? strchr(end, ';') + 1 : NULL;
    }
    s_fault_n = n;
}

/* 1: the call is taken by a fault (r3 0) */
static int fault_at(u32 target, const TcArgs *args)
{
    int k;
    for (k = 0; k < s_fault_n; k++) {
        XbFault *f = &s_fault[k];
        if (f->target != target || f->fired) continue;
        if (f->kind == 1 && (!f->caller || xb_caller(0) == f->caller) && InterlockedDecrement(&f->skip) < 0 && !InterlockedExchange(&f->fired, 1)) {
            port_log("fault fired: zero %08x from %08x (frame %u, depth %u)\n", target, xb_caller(0), xb_frame_number(), s_depth);
            return 1;
        }
        if (f->kind == 2 && f->reg >= 3 && f->reg <= 10 && !InterlockedExchange(&f->fired, 1)) {
            u32 p = (u32)args->r[f->reg - 3], i;
            if (!trace_word(p & ~3u)) { port_log("fault fired: garble %08x r%u=%08x is no guest pointer: nothing done\n", target, f->reg, p); continue; }
            for (i = 0; i < f->bytes; i++) tc_base[p + i] = 0xA5;
            port_log("fault fired: garble %08x r%u=%08x, %u bytes (frame %u, from %08x)\n", target, f->reg, p, f->bytes, xb_frame_number(), xb_caller(0));
        }
    }
    return 0;
}

/* XB_PADLOG (bring-up, off by default): each change of the buttons import 401 gives the game, each keystroke of import 408 (xb_imports_xam.h),
 * and each call of Too Human's camera-style handlers 82632bb0 and 82632c58 (D-pad Up and Down in TH1Input.ini), with the vertical blank, so a
 * run shows what reached the game's D-pad. XB_PADLOG_HOLD=<host button bits, hex> holds those host buttons on player 1 and applies the
 * player's assignments (--bind-...) to them, as a real pad's press would be (console_x360.c). Set by xb_pads_poll's first call */
int xb_padlog = -1;

int xb_padlog_on(void)
{
    if (xb_padlog < 0) xb_padlog = getenv("XB_PADLOG") != NULL;
    return xb_padlog;
}

u64 tc_call(u32 target, const TcArgs *args)
{
    u64 r;
    int w = watched(target);
    if (xb_padlog > 0 && (target == 0x82632bb0u || target == 0x82632c58u))
        port_log("padlog: call %08x from %08x (vertical blank %u)\n", target, xb_caller(0), xb_frame_number());
    if (s_fault_n) {
        if (s_fault_n < 0) fault_setup();
        if (s_fault_n && fault_at(target, args)) return 0;
    }
#ifdef XB_FLOW_CALLS
    if (target >= XB_FLOW_LO && target <= XB_FLOW_HI) xb_flow_call(target, args);
#endif
    if (w) {   /* r3@ and r4@: the first word of each argument that points into guest memory, so a call on an object names its class (vtable) */
        u32 a3 = (u32)args->r[0], a4 = (u32)args->r[1];
        port_log("trace call %08x (frame %u, host thread %lu) r3=%llx r4=%llx r5=%llx r6=%llx r7=%llx r3@=%08x r4@=%08x from %08x %08x %08x\n", target, xb_frame_number(), GetCurrentThreadId(), args->r[0], args->r[1], args->r[2], args->r[3], args->r[4],
                 trace_word(a3), trace_word(a4),
                 s_depth ? s_stack[(s_depth - 1) & 255] : 0, s_depth > 1 ? s_stack[(s_depth - 2) & 255] : 0, s_depth > 2 ? s_stack[(s_depth - 3) & 255] : 0);
#ifdef XB_TRACE_PARAMS
        trace_params(target, args);
#endif
    }
    if (s_arglog_t == ~0u) {
        const char *e = getenv("XB_ARGLOG");
        s_arglog_c = e && strchr(e, '@') ? (u32)strtoul(strchr(e, '@') + 1, NULL, 16) : 0;
        s_arglog_t = e ? (u32)strtoul(e, NULL, 16) : 0;
    }
    if (s_arglog_t && target == s_arglog_t && (!s_arglog_c || xb_caller(0) == s_arglog_c || xb_caller(1) == s_arglog_c || xb_caller(2) == s_arglog_c)) {
        long n = InterlockedIncrement(&s_argring_n) - 1;
        ArgRec *a = &s_argring[n & 7];
        unsigned k;
        a->tid = GetCurrentThreadId(), a->frame = xb_frame_number();
        for (k = 0; k < 6; k++) a->r[k] = (u32)args->r[k];
        for (k = 0; k < 8; k++) a->w3[k] = trace_word((u32)args->r[0] + 4 * k), a->w4[k] = trace_word((u32)args->r[1] + 4 * k);
        for (k = 0; k < 3; k++) a->up[k] = xb_caller(k);
        if (n < 8) arglog_line("arglog ", n, a);
    }
    {   /* XB_TRACE_DUMP=<bytes>: for a traced call, the words at r4 before it and at r3 after it, when the first word r3 points at comes back a NaN
         * (the first three calls too): which input made a matrix NaN */
        static int dump_n = -1, said;
        u32 pre[64], k, n;
        if (dump_n < 0) dump_n = getenv("XB_TRACE_DUMP") ? (int)strtoul(getenv("XB_TRACE_DUMP"), NULL, 0) : 0;
        n = dump_n > 256 ? 64 : (u32)dump_n / 4;
        if (w && n)
            for (k = 0; k < n; k++) pre[k] = trace_word((u32)args->r[1] + 4 * k);
        r = tc_call_inner(target, args);
        if (w && n && (said < 3 || ((trace_word((u32)args->r[0]) & 0x7f800000u) == 0x7f800000u && said < 13))) {
            said++;
            port_log("trace dump %08x r4 before:", target);
            for (k = 0; k < n; k++) port_log(" %08x", pre[k]);
            port_log("\ntrace dump %08x r3 after:", target);
            for (k = 0; k < 64; k++) port_log(" %08x", trace_word((u32)args->r[0] + 4 * k));
            port_log("\n");
        }
    }
    if (w) port_log("trace ret  %08x -> %llx\n", target, r);
    return r;
}

static u64 tc_call_inner(u32 target, const TcArgs *args)
{
    const XbFn *fn;
    const XbImp *imp;
    u64 r3 = 0;
    f64 f1 = 0.0;
    u32 saved = tc_sp;
    unsigned i;
    if (xb_suspends) xb_thread_safe_point();   /* NtSuspendThread stops a thread here (xb_imports.c) */
    s_trace[s_trace_n++ & 31] = target;
    tc_sp = args->sp[0];
    tc_r12 = args->r12[0];
    tc_nv = args->nv;
    s_stack_arg[s_depth & 255][0] = (u32)args->r[0];
    s_stack_arg[s_depth & 255][1] = (u32)args->r[1];
    s_stack_arg[s_depth & 255][2] = (u32)args->r[2];
    s_frame[s_depth & 255] = args;
    s_stack[s_depth++ & 255] = target;
    if (!s_listed) thread_listed();
    for (i = 0; i < xb_boundary_count; i++)
        if (target >= xb_boundary[i].lo && target < xb_boundary[i].hi) break;
    fn = i < xb_boundary_count ? NULL : find_fn(target);
    if (i < xb_boundary_count) {
        if (!xb_lib_entry(target, args, &r3, &f1)) {
            char d[160];
            sprintf_s(d, sizeof d, "r3=%llx r4=%llx r5=%llx r6=%llx r7=%llx r8=%llx", args->r[0], args->r[1], args->r[2], args->r[3],
                      args->r[4], args->r[5]);
            xb_stop("library_entry", d, target);
        }
    } else if (fn) {
        unsigned t = (unsigned)(fn - xb_fn_table);
        if (!s_touched) s_touched = (unsigned char *)calloc(xb_fn_count, 1);
        if (!s_touched[t]) { s_touched[t] = 1; if (s_touched_n < 48) s_first[s_touched_n] = target; s_touched_n++; }
        /* A body that never writes r3 is void and leaves this alone, and on the console r3 still holds what it came in with: 82e21458 reads a bool through the pointer its
         * comparer 82db14a8 got, which that comparer only writes to */
        r3 = args->r[0];
        fn->fn(args->r, args->f, &r3, &f1);
    } else if ((imp = find_imp(target)) != NULL) {
        if (!xb_import_call(imp, args, &r3, &f1)) {
            char d[160];
            sprintf_s(d, sizeof d, "%s #%u %s thunk %08x r3=%llx r4=%llx r5=%llx r6=%llx", xb_lib_name[imp->lib], imp->ord,
                      xb_export_name(xb_lib_name[imp->lib], imp->ord), imp->thunk, args->r[0], args->r[1], args->r[2], args->r[3]);
            xb_stop("import", d, ((u32)imp->lib << 16) | imp->ord);
        }
        if (s_imp_calls && imp >= xb_imp_table && imp < xb_imp_table + xb_imp_count) {
            InterlockedIncrement(&s_imp_calls[imp - xb_imp_table]);
            s_imp_last[imp - xb_imp_table] = r3;
        }
    } else {
        char d[160];
        sprintf_s(d, sizeof d, "r3=%llx r4=%llx r5=%llx r6=%llx", args->r[0], args->r[1], args->r[2], args->r[3]);
        xb_stop("missing_function", d, target);
    }
    s_depth--;
    s_last_r = r3;
    s_last_f = f1;
    tc_sp = saved;
    return r3;
}

#include "xb_jmp.h"   /* the game's setjmp and longjmp: xb_setjmp_slot, xb_setjmp_landed, xb_longjmp */
#include "xb_eh.h"    /* the game's C++ throw and catch: xb_eh_slot, xb_eh_landed, xb_eh_sp, xb_eh_raise */

f64 tc_callf(u32 target, const TcArgs *args)
{
    (void)tc_call(target, args);
    return s_last_f;
}

u64 tc_lastr(void) { return s_last_r; }
f64 tc_lastf(void) { return s_last_f; }

/* The guest function a thread started in (the bottom of its call stack), for XB_WAIT_STATS (xb_wait_stats.h): this thread's, and a host thread
 * id's (a critical section's owner). 0 when the thread has made no guest call or is not listed */
u32 xb_thread_entry(void)
{
    return s_depth ? s_stack[0] : 0;
}

u32 xb_thread_entry_of(unsigned long tid)
{
    long k, n = s_thread_n < 64 ? s_thread_n : 64;
    for (k = 0; k < n; k++)
        if (s_threads[k].id == tid && s_threads[k].stack && *s_threads[k].depth) return s_threads[k].stack[0];
    return 0;
}
