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

u8 rd8(u32 addr) { return *(const volatile u8 *)(uintptr_t)addr; }
u16 rd16(u32 addr) { return *(const volatile u16 *)(uintptr_t)addr; }
/* A word the game busy-waits on until an interrupt handler changes it (the game's sources name it: port_spin_word),
 * in a loop the judge's TcSpin cannot turn into tc_spin (it stores counters each pass). Read SPIN_READS times
 * unchanged within one vertical blank, the read waits for the next one, as the interrupt comes while the console
 * spins (sdk/ac_digi.c's rule for a polled voice). Up to SPIN_WORDS words; none unless a game names one. */
#define SPIN_WORDS 4
#define SPIN_READS 64u
#define AREA_MASK 0x1fffffffu
static u32 g_spin_word[SPIN_WORDS] = {AREA_MASK, AREA_MASK, AREA_MASK, AREA_MASK}, g_spin_value[SPIN_WORDS],
                                      g_spin_reads[SPIN_WORDS], g_spin_blank[SPIN_WORDS];

void port_spin_word(u32 addr)
{
    for (int i = 0; i < SPIN_WORDS; i++) {
        if (g_spin_word[i] != AREA_MASK && g_spin_word[i] != (addr & AREA_MASK)) continue;
        g_spin_word[i] = addr & AREA_MASK;
        port_log("tx: spin word %08x: a read unchanged %u times within a vertical blank waits for the next\n", addr,
                 SPIN_READS);
        return;
    }
    port_log("tx: spin word %08x not kept (%d words at most)\n", addr, SPIN_WORDS);
}

static u32 spin_read(int i, u32 addr, u32 value)
{
    u32 blank = port_vblank_count();
    if (value != g_spin_value[i] || blank != g_spin_blank[i]) {
        g_spin_value[i] = value;
        g_spin_blank[i] = blank;
        g_spin_reads[i] = 0;
    } else if (++g_spin_reads[i] >= SPIN_READS) {
        port_wait_vblank();
        g_spin_reads[i] = 0;
        g_spin_blank[i] = port_vblank_count();
        value = *(const volatile u32 *)(uintptr_t)addr;
        g_spin_value[i] = value;
    }
    return value;
}

u32 rd32(u32 addr)
{
    u32 value = *(const volatile u32 *)(uintptr_t)addr;
    for (int i = 0; i < SPIN_WORDS && g_spin_word[i] != AREA_MASK; i++)
        if ((addr & AREA_MASK) == g_spin_word[i]) return spin_read(i, addr, value);
    return value;
}
f32 rdf32(u32 addr) { return port_tx_u2f(rd32(addr)); }
void wr8(u32 addr, u8 value) { *(volatile u8 *)(uintptr_t)addr = value; }
void wr16(u32 addr, u16 value) { *(volatile u16 *)(uintptr_t)addr = value; }
void wr32(u32 addr, u32 value) { *(volatile u32 *)(uintptr_t)addr = value; }
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

static PortTcGlue glue_of(u32 addr)
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

static void call(u32 target, const PortTcArgs *a, PortTcRet *o, int want_f, const void *site)
{
    PortTcGlue g;
    const PortDispatch *d = NULL;
    PortFn fn = NULL;
    const char *name = "a host function";
    o->r0 = 0;
    o->fr0 = 0.0f;
    /* fr4..fr11 as the callee finds them: readable C may read one from the FPU state (a loader's hidden argument).
     * A body that keeps the whole FPU as state has them there already (and its locals may be another bank's). */
    if (!g_fpu_whole)
        for (int i = 0; i < 8; i++) sh4_set_fr(4 + i, a->fr[i]);
    /* r4..r7 as the callee got them: a caller reading one after the call (meta r_out) reads the callee's output, which
     * a translated callee hands back with sh4_set_r before returning (TcOut), else the argument it kept */
    for (int i = 0; i < 4; i++) sh4_set_r(4 + i, a->r[i]);
    if (target && !guest_is_ram(target)) { /* a host function pointer from readable C */
        u32 guest = guest_of_host(target);
        fn = (PortFn)(uintptr_t)target;
        if (guest) target = guest;
    }
    /* overlay RAM: the loaded unit's glue (its translator C), else its function by words; never main's glue there */
    d = fn ? NULL : overlay_find(target);
    if (d) {
        const PortDispatch *og = overlay_glue(target);
        g = og ? (PortTcGlue)og->fn : NULL;
    } else
        g = glue_of(guest_fold(target));
    if (g) {
        for (int i = 0; i < 4; i++) g_rlo[i] = a->rlo[i];
        g(a, o);
        for (int i = 0; i < 4; i++) g_rlo[i] = 0; /* a readable callee left them */
        return;
    }
    if (!fn) {
        if (!d) d = port_dispatch_find(guest_fold(target));
        if (d) fn = d->fn, name = d->name;
    }
    if (!fn) {
        if (g_no_fn++ < 64u)
            port_log("tx: call %08x from port.exe+%08x: no host function; returns 0\n", target, site_off(site));
        return;
    }
    if (g_word_calls++ < 64u)
        port_log("tx: call %08x (%s) from port.exe+%08x by argument words\n", target, name, site_off(site));
    if (want_f)
        o->fr0 = ((TxWordFnF)(uintptr_t)fn)(a->r[0], a->r[1], a->r[2], a->r[3], a->s[0], a->s[1], a->s[2],
                                              a->s[3], a->s[4], a->s[5], a->s[6], a->s[7]);
    else
        o->r0 = ((TxWordFn)(uintptr_t)fn)(a->r[0], a->r[1], a->r[2], a->r[3], a->s[0], a->s[1], a->s[2],
                                             a->s[3], a->s[4], a->s[5], a->s[6], a->s[7]);
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
