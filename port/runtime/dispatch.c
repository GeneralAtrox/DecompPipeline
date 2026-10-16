/* Calls into guest code. Readable C calls host functions directly, but the game also calls through function
 * pointers held in guest memory (callbacks, tables, the image's own data), which are guest addresses. Guest RAM is
 * not executable, so such a call faults with the instruction pointer on the guest address; the handler looks the
 * address up in the generated dispatch table and resumes in the host function with the caller's cdecl stack
 * untouched. Any other fault ends the run with a report. */
#include <stdlib.h>
#include <windows.h>
#include "port.h"
#include "port_dispatch.h"

static uintptr_t g_mod_lo, g_mod_hi;
static u32 g_dispatched;

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
    port_log("dispatched guest calls: %u\n", g_dispatched);
    port_stub_report();
    port_log_flush();
    ExitProcess(3);
}

static LONG CALLBACK handler(EXCEPTION_POINTERS *ep)
{
    const EXCEPTION_RECORD *er = ep->ExceptionRecord;
    CONTEXT *c = ep->ContextRecord;
    DWORD code = er->ExceptionCode;
    if (code == EXCEPTION_ACCESS_VIOLATION && er->NumberParameters >= 2 && er->ExceptionInformation[1] == c->Eip) {
        u32 target = (u32)c->Eip;
        if (guest_is_ram(target)) {
            const PortDispatch *d = port_dispatch_find(guest_fold(target));
            g_dispatched++;
            if (d && d->fn) {
                c->Eip = (DWORD)(uintptr_t)d->fn;
                return EXCEPTION_CONTINUE_EXECUTION;
            }
            port_log("CALL %08x: guest code with no host function (not a function start); returns 0\n", target);
            c->Eip = (DWORD)(uintptr_t)no_function;
            return EXCEPTION_CONTINUE_EXECUTION;
        }
        port_log("CALL %08x: not guest code\n", target);
        fatal(ep);
    }
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
