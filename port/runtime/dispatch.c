/* Calls into guest code. Readable C calls host functions directly, but the game also calls through function
 * pointers held in guest memory (callbacks, tables, the image's own data), which are guest addresses. Guest RAM is
 * not executable, so such a call faults with the instruction pointer on the guest address; the handler looks the
 * address up in the generated dispatch table and resumes in the host function with the caller's cdecl stack
 * untouched. Any other fault ends the run with a report. */
#include <stdlib.h>
#include <string.h>
#include <windows.h>
#include "port.h"
#include "port_dispatch.h"

#define DISPATCH_TOP 40u

static uintptr_t g_mod_lo, g_mod_hi;
static u32 g_dispatched;
#define HITS 1024u /* a power of two */
static struct {
    const PortDispatch *row;
    u32 n;
} g_hits[HITS];


const PortDispatch *port_dispatch_find(u32 addr)
{
    for (int c = 0; c < port_dispatch_nchunks; c++) {
        const PortDispatch *rows = port_dispatch_chunks[c].rows;
        int lo = 0, hi = *port_dispatch_chunks[c].n - 1;
        if (hi < 0 || addr < rows[0].addr || addr > rows[hi].addr) continue;
        while (lo <= hi) {
            int mid = (lo + hi) / 2;
            if (rows[mid].addr == addr) return &rows[mid];
            if (rows[mid].addr < addr) lo = mid + 1;
            else hi = mid - 1;
        }
        return NULL;
    }
    return NULL;
}

int dispatch_count(void)
{
    int n = 0;
    for (int c = 0; c < port_dispatch_nchunks; c++) n += *port_dispatch_chunks[c].n;
    return n;
}

/* Counts a call through a guest pointer, per function (the first HITS distinct ones). */
static void hit(const PortDispatch *d)
{
    u32 h = (d->addr >> 1) * 2654435761u;
    for (u32 i = 0; i < HITS; i++) {
        u32 k = (h + i) & (HITS - 1u);
        if (g_hits[k].row == d || !g_hits[k].row) {
            g_hits[k].row = d;
            g_hits[k].n++;
            return;
        }
    }
}

/* The calls through guest pointers: the total and the most called functions. */
void dispatch_report(void)
{
    u32 shown[DISPATCH_TOP], nshown = 0;
    port_log("dispatched guest calls: %u\n", g_dispatched);
    while (nshown < DISPATCH_TOP) {
        u32 best = HITS;
        for (u32 i = 0; i < HITS; i++) {
            int taken = 0;
            for (u32 j = 0; j < nshown; j++) taken |= shown[j] == i;
            if (g_hits[i].row && !taken && (best == HITS || g_hits[i].n > g_hits[best].n)) best = i;
        }
        if (best == HITS) break;
        shown[nshown++] = best;
        port_log("  %10u calls  %08x %s\n", g_hits[best].n, g_hits[best].row->addr, g_hits[best].row->name);
    }
}

static u32 __cdecl no_function(void) { return 0; }

/* A host code address as module+offset (the linker map, port.map, names it). */
static void log_host(const char *what, uintptr_t a)
{
    if (a >= g_mod_lo && a < g_mod_hi) port_log("  %s: port.exe+%08x\n", what, (u32)(a - g_mod_lo));
    else port_log("  %s: %08x\n", what, (u32)a);
}

static void fatal(EXCEPTION_POINTERS *ep)
{
    const EXCEPTION_RECORD *er = ep->ExceptionRecord;
    const CONTEXT *c = ep->ContextRecord;
    port_log("FAULT %08lx at %08x", er->ExceptionCode, (u32)(uintptr_t)er->ExceptionAddress);
    if (er->ExceptionCode == EXCEPTION_ACCESS_VIOLATION && er->NumberParameters >= 2)
        port_log(" (%s %08x)", er->ExceptionInformation[0] == 1 ? "write" : er->ExceptionInformation[0] == 8 ?
                 "execute" : "read", (u32)er->ExceptionInformation[1]);
    port_log("\n  eax=%08lx ebx=%08lx ecx=%08lx edx=%08lx esi=%08lx edi=%08lx ebp=%08lx esp=%08lx\n", c->Eax, c->Ebx,
             c->Ecx, c->Edx, c->Esi, c->Edi, c->Ebp, c->Esp);
    log_host("eip", c->Eip);
    const u32 *sp = (const u32 *)(uintptr_t)c->Esp;
    for (int i = 0, n = 0; i < 512 && n < 12; i++)
        if (sp[i] >= g_mod_lo && sp[i] < g_mod_hi) log_host("stack return?", sp[i]), n++;
    dispatch_report();
    port_stub_report();
    port_log_flush();
    ExitProcess(3);
}

/* Calls through P1 guest pointers into the boot image skip the fault: each function start in the dispatch table
 * gets a jump to its host function (E9 rel32), and those pages become executable. A function shorter than the jump
 * gets a short jump (EB rel8) to a jump placed in the dead SH-4 bytes of a thunked neighbour (past its own jump,
 * within its first TRAMP_SPAN bytes), or ud2 if none is in reach (the handler takes that fault as a call). The
 * SH-4 bytes are code, which the port never reads as data. Thunked calls are not counted in dispatch_report.
 * --thunks off keeps them all on the fault. */
#define TRAMP_SPAN 64u

typedef struct {
    u32 addr, end, free; /* free: the next byte a trampoline may use (end: none) */
    PortFn fn;
} Thunk;

static void make_executable(u32 a, u32 len)
{
    DWORD old;
    if (!VirtualProtect((void *)(uintptr_t)a, len, PAGE_EXECUTE_READWRITE, &old))
        port_exit("dispatch: cannot make a guest code page executable", 1);
}

static void put_jump(u32 at, PortFn to)
{
    u8 *p = (u8 *)(uintptr_t)at;
    s32 rel = (s32)((uintptr_t)to - ((uintptr_t)at + 5u));
    p[0] = 0xe9;
    memcpy(p + 1, &rel, 4);
    make_executable(at, 5);
}

void dispatch_thunks(u32 lo, u32 hi)
{
    u32 n = 0, k = 0, jumps = 0, shorts = 0, traps = 0;
    for (int c = 0; c < port_dispatch_nchunks; c++) n += (u32)*port_dispatch_chunks[c].n;
    Thunk *t = calloc(n ? n : 1u, sizeof *t);
    if (!t) return;
    for (int c = 0; c < port_dispatch_nchunks; c++)
        for (int i = 0; i < *port_dispatch_chunks[c].n; i++) {
            const PortDispatch *r = &port_dispatch_chunks[c].rows[i];
            if (k) t[k - 1].end = r->addr < t[k - 1].end ? r->addr : t[k - 1].end;
            if (r->addr < lo || r->addr >= hi || !r->fn || (r->addr & 1u)) continue;
            t[k].addr = r->addr;
            t[k].end = hi;
            t[k++].fn = r->fn;
        }
    for (u32 i = 0; i < k; i++) {
        u32 span = t[i].end - t[i].addr;
        t[i].free = t[i].end;
        if (span < 5u) continue;
        put_jump(t[i].addr, t[i].fn);
        t[i].free = t[i].addr + 5u;
        t[i].end = t[i].addr + (span < TRAMP_SPAN ? span : TRAMP_SPAN);
        jumps++;
    }
    for (u32 i = 0; i < k; i++) {
        u8 *p = (u8 *)(uintptr_t)t[i].addr;
        u32 j = i > 24u ? i - 24u : 0u, at = 0;
        if (t[i].end - t[i].addr >= 5u || t[i].free != t[i].end) continue; /* thunked already */
        for (; j < k && j < i + 24u && !at; j++) { /* a jump within reach of rel8 from addr + 2 */
            s32 d = (s32)(t[j].free - (t[i].addr + 2u));
            if (t[j].free + 5u <= t[j].end && d >= -128 && d <= 127) at = t[j].free;
        }
        if (at) {
            put_jump(at, t[i].fn);
            t[j - 1].free += 5u;
            p[0] = 0xeb;
            p[1] = (u8)(s8)(s32)(at - (t[i].addr + 2u));
            shorts++;
        } else {
            p[0] = 0x0f;
            p[1] = 0x0b;
            traps++;
        }
        make_executable(t[i].addr, 2);
    }
    FlushInstructionCache(GetCurrentProcess(), (void *)(uintptr_t)lo, hi - lo);
    port_log("dispatch: guest code thunks: %u jumps, %u short jumps, %u ud2\n", jumps, shorts, traps);
    free(t);
}

/* A call into guest code: resumes in its host function (or in no_function, logged, if it is not one). */
static LONG call_guest(CONTEXT *c)
{
    u32 target = (u32)c->Eip;
    const PortDispatch *d = port_dispatch_find(guest_fold(target));
    g_dispatched++;
    if (d && d->fn) {
        hit(d);
        c->Eip = (DWORD)(uintptr_t)d->fn;
        return EXCEPTION_CONTINUE_EXECUTION;
    }
    port_log("CALL %08x: guest code with no host function (not a function start); returns 0\n", target);
    c->Eip = (DWORD)(uintptr_t)no_function;
    return EXCEPTION_CONTINUE_EXECUTION;
}

static LONG CALLBACK handler(EXCEPTION_POINTERS *ep)
{
    const EXCEPTION_RECORD *er = ep->ExceptionRecord;
    CONTEXT *c = ep->ContextRecord;
    DWORD code = er->ExceptionCode;
    if (code == EXCEPTION_ILLEGAL_INSTRUCTION && guest_is_ram((u32)c->Eip)) return call_guest(c); /* a short thunk */
    if (code == EXCEPTION_ACCESS_VIOLATION && er->NumberParameters >= 2 && er->ExceptionInformation[1] == c->Eip) {
        u32 target = (u32)c->Eip;
        if (guest_is_ram(target)) return call_guest(c);
        port_log("CALL %08x: not guest code\n", target);
        fatal(ep);
    }
    if (rom_read(ep)) return EXCEPTION_CONTINUE_EXECUTION;
    switch (code) {
    case EXCEPTION_ACCESS_VIOLATION:
    case EXCEPTION_ILLEGAL_INSTRUCTION:
    case EXCEPTION_PRIV_INSTRUCTION:
    case EXCEPTION_INT_DIVIDE_BY_ZERO:
    case EXCEPTION_INT_OVERFLOW:
    case EXCEPTION_STACK_OVERFLOW:
    case EXCEPTION_IN_PAGE_ERROR:
    case EXCEPTION_ARRAY_BOUNDS_EXCEEDED:
    case EXCEPTION_BREAKPOINT: /* a checked build's /RTC1 failure */
    case EXCEPTION_FLT_DIVIDE_BY_ZERO:
    case EXCEPTION_FLT_INVALID_OPERATION:
        fatal(ep);
        break;
    default:
        break;
    }
    return EXCEPTION_CONTINUE_SEARCH;
}

int dispatch_install(void)
{
    HMODULE m = GetModuleHandleW(NULL);
    const IMAGE_DOS_HEADER *dos = (const IMAGE_DOS_HEADER *)m;
    const IMAGE_NT_HEADERS *nt = (const IMAGE_NT_HEADERS *)((const u8 *)m + dos->e_lfanew);
    g_mod_lo = (uintptr_t)m;
    g_mod_hi = g_mod_lo + nt->OptionalHeader.SizeOfImage;
    for (int c = 0; c < port_dispatch_nchunks; c++) {
        const PortDispatch *rows = port_dispatch_chunks[c].rows;
        for (int i = 1; i < *port_dispatch_chunks[c].n; i++)
            if (rows[i - 1].addr >= rows[i].addr) {
                port_log("dispatch table chunk %d is not sorted at %08x\n", c, rows[i].addr);
                return 0;
            }
    }
    return AddVectoredExceptionHandler(1, handler) != NULL;
}
