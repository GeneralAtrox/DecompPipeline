/* The lockstep check's writer watch (--lockstep-watch): an x86 debug register on one watched word for one port frame,
 * so each write the frame makes to it is logged with the instruction after it and the stack, and tools/lockstep.py
 * names the functions from the build's port.map. Debug registers are the thread's own: the game thread arms them by
 * raising an exception whose handler sets them in the context it resumes with. One register per view of guest RAM
 * (P1, P0, P2 and the store-queue window, guest_mem.c), so a write through any of them is seen. */
#include <stdio.h>
#include <windows.h>
#include "port.h"
#include "lockstep_parts.h"

#define WATCH_ARM 0xe04c5357u /* the arming exception's code */
#define WATCH_DR7 0xdddd0055u /* DR0-DR3 on (L0-L3), each on a write (RW 01), four bytes (LEN 11) */
#define WATCH_HITS 32u        /* writes logged per frame */
#define WATCH_STACK 48u       /* stack words logged per write */

static volatile u32 *g_at;
static u32 g_frame, g_hits;
static PVOID g_handler;

static void hit(const CONTEXT *c)
{
    char line[64 + WATCH_STACK * 9];
    const u32 *sp = (const u32 *)(uintptr_t)c->Esp;
    int n = snprintf(line, sizeof line, "lockstep watch: frame %u write %08x after %08x stack", g_frame, *g_at,
                     (u32)c->Eip);
    for (u32 i = 0; i < WATCH_STACK && n > 0 && (size_t)n < sizeof line; i++)
        n += snprintf(line + n, sizeof line - (size_t)n, " %08x", sp[i]);
    port_log("%s\n", line);
}

/* The watched word in one view of guest RAM (itself if it is not in RAM). */
static DWORD view(u32 base)
{
    u32 at = (u32)(uintptr_t)g_at, off = at - GUEST_RAM_BASE;
    return off < GUEST_RAM_SIZE ? base + off : at;
}

static LONG CALLBACK watch_handler(EXCEPTION_POINTERS *ep)
{
    CONTEXT *c = ep->ContextRecord;
    DWORD code = ep->ExceptionRecord->ExceptionCode;
    if (code == WATCH_ARM) {
        c->Dr0 = view(GUEST_RAM_BASE);
        c->Dr1 = view(GUEST_MIRROR_BASE);
        c->Dr2 = view(GUEST_UNCACHED_BASE);
        c->Dr3 = view(GUEST_SQ_BASE);
        c->Dr6 = 0;
        c->Dr7 = g_at ? WATCH_DR7 : 0u;
        c->ContextFlags |= CONTEXT_DEBUG_REGISTERS;
        return EXCEPTION_CONTINUE_EXECUTION;
    }
    if (code != EXCEPTION_SINGLE_STEP || !(c->Dr6 & 0xfu) || !g_at) return EXCEPTION_CONTINUE_SEARCH;
    c->Dr6 = 0;
    c->ContextFlags |= CONTEXT_DEBUG_REGISTERS;
    if (g_hits++ < WATCH_HITS) hit(c);
    return EXCEPTION_CONTINUE_EXECUTION;
}

void lockstep_watch(volatile u32 *at, u32 frame)
{
    if (!g_handler) g_handler = AddVectoredExceptionHandler(1, watch_handler);
    if (!g_handler) return;
    if (at) {
        port_log("lockstep watch: frame %u word %08x image %08x\n", frame, (u32)(uintptr_t)at,
                 (u32)(uintptr_t)GetModuleHandleW(NULL));
    } else if (g_hits > WATCH_HITS) {
        port_log("lockstep watch: frame %u: %u more writes\n", g_frame, g_hits - WATCH_HITS);
    }
    g_at = at;
    g_frame = frame;
    g_hits = 0;
    RaiseException(WATCH_ARM, 0, 0, NULL);
}
