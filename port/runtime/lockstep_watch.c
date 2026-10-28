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
/* --lockstep-entry: execute breakpoints (DR0..DR3) on up to four functions' host code; each call logs its argument's
 * words */
static DWORD g_entry_at[ENTRY_MAX];
static u32 g_entry_guest[ENTRY_MAX], g_entry_count;
static u32 g_entry_words, g_entry_off, g_entry_n, g_entry_line, g_entry_label, g_entry_calls;
static volatile u32 *g_entry_frames;
static volatile int g_entry_arming; /* the arming exception is the entry's, not the write watch's */

/* One call of function k: "lockstep entry: line N fn G label L frames F arg A w.. | j..", the emulator half's LS_ENTRY
 * line after "fn" (the words at the first argument, then those at the pointer at argument + off; "-" where it is
 * null). */
static void entry_hit(const CONTEXT *c, u32 k)
{
    const u32 *arg = *(const u32 *const *)(uintptr_t)(c->Esp + 4u); /* cdecl: the first argument above the return */
    const u32 *j = arg ? *(const u32 *const *)((const u8 *)arg + g_entry_off) : NULL;
    static char line[16384];
    int n = snprintf(line, sizeof line, "lockstep entry: line %u fn %08x label %08x frames %u arg %08x", g_entry_line,
                     g_entry_guest[k], g_entry_label, g_entry_frames ? *g_entry_frames : 0u, (u32)(uintptr_t)arg);
    for (u32 i = 0; i < g_entry_words && n > 0 && (size_t)n < sizeof line; i++)
        n += snprintf(line + n, sizeof line - (size_t)n, arg ? " %08x" : " -", arg ? arg[i] : 0u);
    if (n > 0 && (size_t)n < sizeof line) n += snprintf(line + n, sizeof line - (size_t)n, " |");
    for (u32 i = 0; i < g_entry_n && n > 0 && (size_t)n < sizeof line; i++)
        n += snprintf(line + n, sizeof line - (size_t)n, j ? " %08x" : " -", j ? j[i] : 0u);
    port_log("%s\n", line);
    g_entry_calls++;
}

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
    if (code == WATCH_ARM && g_entry_arming) {
        u32 dr7 = 0;
        for (u32 k = 0; k < g_entry_count; k++) dr7 |= 1u << (2u * k); /* Lk, on execution (RW 00, LEN 00) */
        c->Dr0 = g_entry_count > 0u ? g_entry_at[0] : 0u;
        c->Dr1 = g_entry_count > 1u ? g_entry_at[1] : 0u;
        c->Dr2 = g_entry_count > 2u ? g_entry_at[2] : 0u;
        c->Dr3 = g_entry_count > 3u ? g_entry_at[3] : 0u;
        c->Dr6 = 0;
        c->Dr7 = dr7;
        c->ContextFlags |= CONTEXT_DEBUG_REGISTERS;
        return EXCEPTION_CONTINUE_EXECUTION;
    }
    if (code == EXCEPTION_SINGLE_STEP && g_entry_count && (c->Dr6 & 0xfu)) {
        for (u32 k = 0; k < g_entry_count; k++)
            if ((c->Dr6 >> k & 1u) && c->Eip == g_entry_at[k]) entry_hit(c, k);
        c->Dr6 = 0;
        c->EFlags |= 0x10000u; /* RF: the breakpoint's instruction runs once without trapping again */
        c->ContextFlags |= CONTEXT_DEBUG_REGISTERS | CONTEXT_CONTROL;
        return EXCEPTION_CONTINUE_EXECUTION;
    }
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

void lockstep_entry(const u32 *fn, const u32 *guest, u32 count, u32 words, u32 off, u32 n, volatile u32 *frames)
{
    g_entry_words = words;
    g_entry_off = off;
    g_entry_n = n;
    g_entry_frames = frames;
    if (!g_handler) g_handler = AddVectoredExceptionHandler(1, watch_handler);
    if (!g_handler) return;
    if (!count && g_entry_count) port_log("lockstep entry: %u calls logged\n", g_entry_calls);
    g_entry_count = count < ENTRY_MAX ? count : ENTRY_MAX;
    for (u32 k = 0; k < g_entry_count; k++) {
        g_entry_at[k] = fn[k];
        g_entry_guest[k] = guest[k];
    }
    g_entry_arming = 1;
    RaiseException(WATCH_ARM, 0, 0, NULL); /* sets DR0.. on the functions, or clears them (count 0) */
    g_entry_arming = 0;
}

void lockstep_entry_frame(u32 line, u32 label)
{
    g_entry_line = line;
    g_entry_label = label;
}
