/* tc_call for the whole game: a guest address is looked up in the table gen_xbox.py wrote (the game's own translated
 * functions), then in the import table (host calls). A target in neither is a stop: that function is translated next. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "xb.h"

__declspec(thread) u32 tc_sp;
__declspec(thread) u32 tc_r13 = 0x7FFF0000u;   /* the guest thread's processor block; the main thread's is set up by xb_mem_init */
__declspec(thread) const u64 *tc_nv;      /* the caller's r14..r31 for a funclet */
__declspec(thread) u32 tc_r12;      /* the caller r12: a funclet (a __finally block) takes its parent frame from it */
static __declspec(thread) u64 s_last_r;
static __declspec(thread) f64 s_last_f;
static __declspec(thread) u32 s_trace[32];
static __declspec(thread) unsigned s_trace_n;
static __declspec(thread) u32 s_stack[256];
static __declspec(thread) u32 s_stack_arg[256][3];   /* r3..r5 of each call on the stack, for a stop's report */
static __declspec(thread) unsigned s_depth;
static unsigned char *s_touched;
static unsigned s_touched_n;
static u32 s_first[48];   /* the first functions called, in order: where a run began to go wrong */

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

void xb_trace_dump(void)
{
    unsigned i, n = s_trace_n < 32 ? s_trace_n : 32;
    port_log("last calls (oldest first):");
    for (i = 0; i < n; i++) port_log(" %08x", s_trace[(s_trace_n - n + i) & 31]);
    port_log("\n");
    port_log("first calls:");
    for (i = 0; i < s_touched_n && i < 48; i++) port_log(" %08x", s_first[i]);
    port_log("\n");
    port_log("call stack (innermost first):");
    for (i = 0; i < s_depth && i < 40; i++) port_log(" %08x", s_stack[(s_depth - 1 - i) & 255]);
    port_log("\n");
    port_log("call stack arguments r3 r4 r5 (innermost first):");
    for (i = 0; i < s_depth && i < 12; i++) {
        const u32 *g = s_stack_arg[(s_depth - 1 - i) & 255];
        port_log(" [%08x: %08x %08x %08x]", s_stack[(s_depth - 1 - i) & 255], g[0], g[1], g[2]);
    }
    port_log("\n");
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

static int watched(u32 a)
{
    unsigned i;
    if (!s_watch_init) watch_init();
    for (i = 0; i < s_watch_n; i++) if (s_watch[i] == a) return 1;
    return 0;
}

static u64 tc_call_inner(u32 target, const TcArgs *args);

u64 tc_call(u32 target, const TcArgs *args)
{
    u64 r;
    int w = watched(target);
    if (w) port_log("trace call %08x r3=%llx r4=%llx r5=%llx r6=%llx r7=%llx\n", target, args->r[0], args->r[1], args->r[2], args->r[3], args->r[4]);
    r = tc_call_inner(target, args);
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
    s_trace[s_trace_n++ & 31] = target;
    tc_sp = args->sp[0];
    tc_r12 = args->r12[0];
    tc_nv = args->nv;
    s_stack_arg[s_depth & 255][0] = (u32)args->r[0];
    s_stack_arg[s_depth & 255][1] = (u32)args->r[1];
    s_stack_arg[s_depth & 255][2] = (u32)args->r[2];
    s_stack[s_depth++ & 255] = target;
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
        fn->fn(args->r, args->f, &r3, &f1);
    } else if ((imp = find_imp(target)) != NULL) {
        if (!xb_import_call(imp, args, &r3, &f1)) {
            char d[160];
            sprintf_s(d, sizeof d, "%s #%u %s thunk %08x r3=%llx r4=%llx r5=%llx r6=%llx", xb_lib_name[imp->lib], imp->ord,
                      xb_export_name(xb_lib_name[imp->lib], imp->ord), imp->thunk, args->r[0], args->r[1], args->r[2], args->r[3]);
            xb_stop("import", d, ((u32)imp->lib << 16) | imp->ord);
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

f64 tc_callf(u32 target, const TcArgs *args)
{
    (void)tc_call(target, args);
    return s_last_f;
}

u64 tc_lastr(void) { return s_last_r; }
f64 tc_lastf(void) { return s_last_f; }
