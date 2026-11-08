/* Translator C in the port (tools/tx_port.py): the judged runtime interface tc_rt.h over the port. Guest memory is
 * direct (a guest pointer is a host pointer), calls by guest address go through tc_call's generated glue, and the
 * translated bodies run on a guest stack where the original's was. The rest of tc_rt.h (the FPU and its modes, r8..r14,
 * FPSCR, GBR, SR, the hardware hooks, tc_halt, tc_spin, the double helpers) is sh4_rt.c's, shared with readable C. */
#include <intrin.h>
#include <windows.h>
#include "platform.h"
#include "port.h"
#include "port_dispatch.h"
#include "overlay.h"
#include "tx_rt.h"
#include "prof.h"
#include "work_count.h"

/* The original's stack: the boot ROM starts the game with r15 at RAM + 0xf400, below the boot file at RAM + 0x10000
 * (the heap runs from the boot file's end to the top of RAM, so the judge's entry r15 is no place for it here). The
 * first 1 KB of RAM (the boot ROM's vectors) stays clear. */
#define TX_STACK_TOP (GUEST_RAM_BASE + 0xf400u)
#define TX_STACK_SIZE (TX_STACK_TOP - GUEST_RAM_BASE - 0x400u)
#define TX_REPORT_STEP 0x1000u

extern const PortTcChunk port_tx_chunks[]; /* generated: txcall_index.c */
extern const int port_tx_nchunks;

static u32 g_sp = TX_STACK_TOP, g_low = TX_STACK_TOP, g_reported = TX_STACK_TOP, g_word_calls, g_no_fn;
static PortTcRet g_last;
static int g_fpu_whole;
static u32 g_rlo[4]; /* r0..r3 of the last tc_call, until a translated callee takes them */
/* The 8 stack words of the last tc_call (TcArgs.s), until a translated callee's frame takes them: the original's
 * callee finds them at its r15, also those its registry prototype does not name (a va_list pointer, the words
 * after a variadic function's format), which its judged body reads there. */
static u32 g_slo[8];
static int g_slo_set;

typedef union {
    u32 u;
    f32 f;
} TxWord;

float port_tx_u2f(uint32_t bits)
{
    TxWord w;
    w.u = bits;
    return w.f;
}

uint32_t port_tx_f2u(float value)
{
    TxWord w;
    w.f = value;
    return w.u;
}

/* Area 5 (external devices, 0x14000000..0x17ffffff in each P0..P3 view): nothing answers on a retail Dreamcast and
 * a read returns 0 (Flycast sh4_mem.cpp ReadMem_extdev_T; the judge's TcHw "read area5"). Game code can read there, e.g. a table indexed
 * with its own stack pointer. */
#define AREA5(a) ((a) < 0xe0000000u && ((a) & 0x1c000000u) == 0x14000000u)
/* P4's reserved space (0xe4000000..0xefffffff, between the store queues and the cache arrays) answers nothing either
 * and a read returns 0 (Flycast's P4 read of an unknown address). The same stack-indexed table read lands here, not in
 * area 5, when the stack is where the console's is (area 3, TX_STACK_TOP; the judge's oracle stack gives area 5). */
#define P4_RESERVED(a) ((a) >= 0xe4000000u && (a) < 0xf0000000u)
#define ABSENT(a) (AREA5(a) || P4_RESERVED(a))
u8 rd8(u32 addr) { return ABSENT(addr) ? 0 : *(const volatile u8 *)(uintptr_t)addr; }
u16 rd16(u32 addr) { return ABSENT(addr) ? 0 : *(const volatile u16 *)(uintptr_t)addr; }
u32 rd32(u32 addr)
{
    if (ABSENT(addr)) return 0;
    u32 value = *(const volatile u32 *)(uintptr_t)addr;
    return port_tx_spin_on ? port_tx_spin_read(addr, value) : value; /* tx_spin.c: a named spin word waits */
}
u32 port_tx_rd32_slow(u32 addr) { return rd32(addr); }
f32 rdf32(u32 addr) { return port_tx_u2f(rd32(addr)); }
/* game.json sq_queue_only: the translated C models the store queues itself (SqQueues: the bytes stored and the pref that sends
 * them to RAM), so a store into the window 0xe0000000..0xe3ffffff must not also land in the RAM it mirrors: a drawer that stores
 * into one block and sends the queue to another would overwrite the blocks it has already sent. */
#ifdef PORT_SQ_QUEUE_ONLY
#define SQ_WINDOW(a) ((a) - 0xe0000000u < 0x04000000u)
#else
#define SQ_WINDOW(a) 0
#endif
void wr8(u32 addr, u8 value) { if (SQ_WINDOW(addr)) return; *(volatile u8 *)(uintptr_t)addr = value; }
void wr16(u32 addr, u16 value) { if (SQ_WINDOW(addr)) return; *(volatile u16 *)(uintptr_t)addr = value; }
void wr32(u32 addr, u32 value) { if (SQ_WINDOW(addr)) return; *(volatile u32 *)(uintptr_t)addr = value; }
void wrf32(u32 addr, f32 value) { wr32(addr, port_tx_f2u(value)); }
void port_tx_wr32(uint32_t addr, uint32_t value) { wr32(addr, value); }

uint32_t port_tx_enter(uint32_t frame, uint32_t args)
{
    u32 sp = g_sp - args;
    if (sp - frame < TX_STACK_TOP - TX_STACK_SIZE || sp - frame > sp) {
        port_log("tx: guest stack overflow: a frame of %u bytes below %08x (floor %08x)\n", frame, sp,
                 TX_STACK_TOP - TX_STACK_SIZE);
        port_exit("translator C: guest stack overflow", 3);
    }
    g_sp = sp - frame;
    if (g_slo_set && args >= 32u)
        for (u32 i = 0; i < 8u; i++) wr32(sp + 4u * i, g_slo[i]);
    g_slo_set = 0;
    if (g_sp < g_low) {
        g_low = g_sp;
        if (g_reported - g_low >= TX_REPORT_STEP) {
            g_reported = g_low;
            port_log("tx: guest stack in use: %u bytes\n", TX_STACK_TOP - g_low);
        }
    }
    return sp;
}

void port_tx_leave(uint32_t sp, uint32_t args) { g_sp = sp + args; }

static PortTcGlue glue_search(u32 addr)
{
    for (int c = 0; c < port_tx_nchunks; c++) {
        const PortTcRow *rows = port_tx_chunks[c].rows;
        int lo = 0, hi = *port_tx_chunks[c].n - 1;
        if (hi < 0 || addr < rows[0].addr || addr > rows[hi].addr) continue;
        while (lo <= hi) {
            int mid = (lo + hi) / 2;
            if (rows[mid].addr == addr) return rows[mid].fn;
            if (rows[mid].addr < addr) lo = mid + 1;
            else hi = mid - 1;
        }
        return NULL;
    }
    return NULL;
}

/* The calls repeat a few hundred targets a frame: the last glue found for each slot (the table is fixed once the port runs). */
#define GLUE_CACHE 2048u
static struct {
    u32 addr;
    PortTcGlue fn; /* NULL: the address has no glue (the call goes to a readable function: dispatch_of) */
    const PortDispatch *dispatch;
    int set, dispatch_set;
} g_glue_cache[GLUE_CACHE];

static PortTcGlue glue_of(u32 addr)
{
    u32 slot = ((addr >> 2) ^ (addr >> 13)) % GLUE_CACHE;
    if (g_glue_cache[slot].set && g_glue_cache[slot].addr == addr) return g_glue_cache[slot].fn;
    g_glue_cache[slot].addr = addr;
    g_glue_cache[slot].fn = glue_search(addr);
    g_glue_cache[slot].set = 1;
    g_glue_cache[slot].dispatch_set = 0;
    return g_glue_cache[slot].fn;
}

/* The dispatch table's row for a folded address, kept beside its glue slot (the table is fixed once the port runs). */
static const PortDispatch *dispatch_of(u32 addr)
{
    u32 slot = ((addr >> 2) ^ (addr >> 13)) % GLUE_CACHE;
    if (!(g_glue_cache[slot].set && g_glue_cache[slot].addr == addr)) glue_of(addr);
    if (!g_glue_cache[slot].dispatch_set) {
        g_glue_cache[slot].dispatch = port_dispatch_find(addr);
        g_glue_cache[slot].dispatch_set = 1;
    }
    return g_glue_cache[slot].dispatch;
}

/* The guest address of a host function readable C handed over as a pointer (a callback it stored where the original
 * stores a guest address): the dispatch table's row for it, 0 if none. The last few are kept. */
#define TX_HOST_CACHE 64u
static struct {
    u32 host, guest;
} g_host_cache[TX_HOST_CACHE];

static u32 guest_of_host(u32 host)
{
    u32 slot = (host >> 4) % TX_HOST_CACHE;
    if (g_host_cache[slot].host == host) return g_host_cache[slot].guest;
    for (int c = 0; c < port_dispatch_nchunks; c++)
        for (int i = 0; i < *port_dispatch_chunks[c].n; i++)
            if ((u32)(uintptr_t)port_dispatch_chunks[c].rows[i].fn == host) {
                g_host_cache[slot].host = host;
                g_host_cache[slot].guest = port_dispatch_chunks[c].rows[i].addr;
                return g_host_cache[slot].guest;
            }
    return 0;
}

typedef u32(__cdecl *TxWordFn)(u32, u32, u32, u32, u32, u32, u32, u32, u32, u32, u32, u32);
typedef f32(__cdecl *TxWordFnF)(u32, u32, u32, u32, u32, u32, u32, u32, u32, u32, u32, u32);

/* A call by guest address: its glue (in overlay RAM, the loaded unit's: overlay.c), else the dispatch table's function
 * (an overlay's readable C, a prototype the glue cannot express) with r4..r7 and the 8 stack words as cdecl words (the
 * caller pops them: extra words are harmless). */
/* site: the host code that called tc_call, logged as an offset in port.exe (which port.map names). */
static u32 site_off(const void *site)
{
    return (u32)(uintptr_t)site - (u32)(uintptr_t)GetModuleHandleW(NULL);
}

#define MAX_CALL_HOOKS 32u
static struct {
    u32 addr;
    PortCallHookFn before, after;
    void *arg;
} g_call_hooks[MAX_CALL_HOOKS];
static u32 g_ncall_hooks;
static const PortTcArgs *g_call_args;
static int g_call_skip; /* a before hook said the call this time does not happen */

void port_hook_skip_call(void) { g_call_skip = 1; }
const PortTcArgs *port_hook_args(void) { return g_call_args; }

void port_hook_call(u32 addr, PortCallHookFn before, PortCallHookFn after, void *arg)
{
    if (g_ncall_hooks >= MAX_CALL_HOOKS) {
        port_log("tx: call hook for %08x refused: %u hooks already\n", addr, MAX_CALL_HOOKS);
        return;
    }
    if (addr && !guest_is_ram(addr) && guest_of_host(addr)) addr = guest_of_host(addr); /* a host function pointer */
    g_call_hooks[g_ncall_hooks].addr = guest_fold(addr);
    g_call_hooks[g_ncall_hooks].before = before;
    g_call_hooks[g_ncall_hooks].after = after;
    g_call_hooks[g_ncall_hooks].arg = arg;
    g_ncall_hooks++;
}

static void call_hooks(u32 target, int after)
{
    u32 at = guest_fold(target);
    for (u32 i = 0; i < g_ncall_hooks; i++) {
        PortCallHookFn fn = after ? g_call_hooks[i].after : g_call_hooks[i].before;
        if (g_call_hooks[i].addr == at && fn) fn(at, g_call_hooks[i].arg);
    }
}

/* A target with no host function, or one called by argument words, is logged the first time it is called, whatever
 * came before: the first 64 calls used to hide a late one behind a loop (per-character code a game loads late). */
#define NO_FN_SEEN 1024u
#define FIRST_SLOTS 4096u /* a power of two over NO_FN_SEEN: the table is never full */
static int first_call(int words, u32 target)
{
    static u32 seen[2][FIRST_SLOTS]; /* open addressing; a slot is taken where used[] says so */
    static unsigned char used[2][FIRST_SLOTS];
    static u32 n[2];
    static int full_logged[2];
    u32 slot = ((target >> 2) ^ (target >> 14)) % FIRST_SLOTS;
    while (used[words][slot]) {
        if (seen[words][slot] == target) return 0;
        slot = (slot + 1u) % FIRST_SLOTS;
    }
    if (n[words] == NO_FN_SEEN) {
        if (!full_logged[words]++)
            port_log("tx: more than %u distinct targets %s; later ones are not logged\n", NO_FN_SEEN,
                     words ? "called by argument words" : "with no host function");
        return 0;
    }
    seen[words][slot] = target;
    used[words][slot] = 1;
    n[words]++;
    return 1;
}

static void call_unhooked(u32 target, const PortTcArgs *a, PortTcRet *o, int want_f, const void *site)
{
    PortTcGlue g;
    const PortDispatch *d = NULL, *og = NULL;
    PortFn fn = NULL;
    u32 delta = 0;
    const char *name = "a host function";
    o->r0 = 0;
    o->fr0 = 0.0f;
    /* fr4..fr11 as the callee finds them: readable C may read one from the FPU state (a loader's hidden argument).
     * A body that keeps the whole FPU as state has them there already (and its locals may be another bank's). */
    if (!g_fpu_whole)
        for (int i = 0; i < 8; i++) port_sh4_fr[4 + i] = port_tx_f2u(a->fr[i]);
    /* r4..r7 as the callee got them: a caller reading one after the call (meta r_out) reads the callee's output, which
     * a translated callee hands back with sh4_set_r before returning (TcOut), else the argument it kept */
    for (int i = 0; i < 4; i++) port_sh4_ro[i] = a->r[i];
    if (target && !guest_is_ram(target)) { /* a host function pointer from readable C */
        u32 guest = guest_of_host(target);
        fn = (PortFn)(uintptr_t)target;
        if (guest) target = guest;
    }
    /* overlay RAM: the loaded unit's glue (its translator C), else its function by words; never main's glue there */
    if (!fn) d = overlay_find_glue(target, &og);
    if (d) {
        g = og ? (PortTcGlue)og->fn : NULL;
        delta = overlay_delta();
    } else
        g = glue_of(guest_fold(target));
    if (g) {
        u32 saved = tx_ovl_delta; /* a slot unit's translator C reads it; a call out of it and back sets it again */
        for (int i = 0; i < 4; i++) g_rlo[i] = a->rlo[i];
        for (int i = 0; i < 8; i++) g_slo[i] = a->s[i];
        g_slo_set = 1;
        if (d) tx_ovl_delta = delta;
        g(a, o);
        tx_ovl_delta = saved;
        for (int i = 0; i < 4; i++) g_rlo[i] = 0; /* a readable callee left them */
        g_slo_set = 0;
        return;
    }
    if (!fn) {
        if (!d) d = dispatch_of(guest_fold(target));
        if (d) fn = d->fn, name = d->name;
    }
    if (!fn) {
        g_no_fn++;
        if (first_call(0, target))
            port_log("tx: call %08x from port.exe+%08x: no host function; returns 0 (r4..r7 %08x %08x %08x %08x, caller r0..r3 %08x %08x %08x %08x; each target is logged once; %u calls so far)\n",
                     target, site_off(site), a->r[0], a->r[1], a->r[2], a->r[3], a->rlo[0], a->rlo[1], a->rlo[2], a->rlo[3], g_no_fn);
        return;
    }
    g_word_calls++;
    if (first_call(1, target))
        port_log("tx: call %08x (%s) from port.exe+%08x by argument words (each target is logged once)\n", target, name, site_off(site));
    if (want_f)
        o->fr0 = ((TxWordFnF)(uintptr_t)fn)(a->r[0], a->r[1], a->r[2], a->r[3], a->s[0], a->s[1], a->s[2],
                                              a->s[3], a->s[4], a->s[5], a->s[6], a->s[7]);
    else
        o->r0 = ((TxWordFn)(uintptr_t)fn)(a->r[0], a->r[1], a->r[2], a->r[3], a->s[0], a->s[1], a->s[2],
                                             a->s[3], a->s[4], a->s[5], a->s[6], a->s[7]);
}

static void call(u32 target, const PortTcArgs *a, PortTcRet *o, int want_f, const void *site)
{
    u32 at = target;
    if (g_prof_on) prof_call(target);
    work_add(WC_GUEST_CALLS, 1);
    if (!g_ncall_hooks) {
        call_unhooked(target, a, o, want_f, site);
        return;
    }
    if (at && !guest_is_ram(at) && guest_of_host(at)) at = guest_of_host(at); /* a host function pointer */
    g_call_args = a;
    call_hooks(at, 0);
    if (g_call_skip) {
        g_call_skip = 0;
        memset(o, 0, sizeof *o);
    } else {
        call_unhooked(target, a, o, want_f, site);
    }
    call_hooks(at, 1);
}

u32 tc_call(u32 target, const PortTcArgs *args)
{
    PortTcRet o;
    call(target, args, &o, 0, _ReturnAddress());
    g_last = o;
    return o.r0;
}

f32 tc_callf(u32 target, const PortTcArgs *args)
{
    PortTcRet o;
    call(target, args, &o, 1, _ReturnAddress());
    g_last = o;
    return o.fr0;
}

int port_tx_fpu_whole(int whole)
{
    int was = g_fpu_whole;
    g_fpu_whole = whole;
    return was;
}

void port_tx_take_rlo(uint32_t *lo)
{
    for (int i = 0; i < 4; i++) {
        lo[i] = g_rlo[i];
        g_rlo[i] = 0;
    }
}

/* A stack argument past the 8 words a call carries, where the translated caller stored it: at its r15 + off. Its
 * r15 is where the 8 words lie, at or above the innermost translated frame's floor (g_sp): the lowest match. */
uint32_t port_tx_stack_word(const PortTcArgs *a, uint32_t off)
{
    static u32 g_misses;
    for (u32 p = g_sp; p + off + 4u <= TX_STACK_TOP; p += 4u) {
        int i = 0;
        while (i < 8 && rd32(p + 4u * (u32)i) == a->s[i]) i++;
        if (i == 8) return rd32(p + off);
    }
    if (g_misses++ < 16u) port_log("tx: no caller stack holds the call's stack words; stack+%u is 0\n", off);
    return 0;
}

uint32_t port_tx_last_r0(void) { return g_last.r0; }
float port_tx_last_fr0(void) { return g_last.fr0; }
void port_tx_set_fr0(float value) { sh4_set_fr(0, value); }
