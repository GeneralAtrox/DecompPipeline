/* The lockstep check's writer watch (--lockstep-watch, --watch-ram-frame): x86 debug registers on up to four watched words
 * for one port frame, so each write the frame makes to them is logged with the word, the instruction after it and the
 * stack, and the tools name the functions from the build's port.map (the tools/lockstep_watch.py hits, in the order
 * they ran). Debug registers are the thread's own: the game thread arms them by raising an exception whose handler
 * sets them in the context it resumes with. The four registers go to the views of guest RAM (P1, P0, P2 and the
 * store-queue window, guest_mem.c) in that order: all four for one word, so a write through any of them is seen; P1
 * and P0 each for two words; P1 alone for three or four (the arming line says how many views each word has). Each
 * word logs its first writes (its share of WATCH_HITS), and its last write is always logged as the watch ends (a busy
 * word cannot hide another's), each with its place in the order the writes ran (seq). */
#include <stdio.h>
#include <windows.h>
#include "port.h"
#include "lockstep_parts.h"

#define WATCH_ARM 0xe04c5357u /* the arming exception's code */
#define WATCH_WORDS 4u        /* one debug register each, at least */
#define WATCH_HITS 64u        /* writes logged as they run per frame, shared by the words */
#define WATCH_STACK 48u       /* stack words logged per write */

typedef struct {
    u32 seq, value, eip, stack[WATCH_STACK];
} Write;

static volatile u32 *g_at[WATCH_WORDS];
static u32 g_n, g_frame, g_seq, g_hits[WATCH_WORDS];
static Write g_last[WATCH_WORDS]; /* each word's latest write; past its share, logged as the watch ends */
static int g_pending[WATCH_WORDS];
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

static void log_write(u32 i, const Write *w)
{
    char line[96 + WATCH_STACK * 9];
    int n = snprintf(line, sizeof line, "lockstep watch: frame %u write %08x after %08x word %u seq %u stack", g_frame,
                     w->value, w->eip, i, w->seq);
    for (u32 k = 0; k < WATCH_STACK && n > 0 && (size_t)n < sizeof line; k++)
        n += snprintf(line + n, sizeof line - (size_t)n, " %08x", w->stack[k]);
    port_log("%s\n", line);
}

static void hit(const CONTEXT *c, u32 i)
{
    Write *w = &g_last[i];
    const u32 *sp = (const u32 *)(uintptr_t)c->Esp;
    w->seq = g_seq++;
    w->value = *g_at[i];
    w->eip = (u32)c->Eip;
    for (u32 k = 0; k < WATCH_STACK; k++) w->stack[k] = sp[k];
    g_pending[i] = g_hits[i]++ >= WATCH_HITS / g_n;
    if (!g_pending[i]) log_write(i, w);
}

/* The watch ends: each word's last write not yet logged, and how many went unlogged. */
static void flush(void)
{
    for (u32 i = 0; i < g_n; i++) {
        u32 share = WATCH_HITS / g_n;
        if (g_pending[i]) log_write(i, &g_last[i]);
        if (g_hits[i] > share + 1u)
            port_log("lockstep watch: frame %u: word %u: %u more writes\n", g_frame, i, g_hits[i] - share - 1u);
        g_pending[i] = 0;
        g_hits[i] = 0;
    }
}

/* Watched word i in one view of guest RAM (itself if it is not in RAM). */
static DWORD view(u32 i, u32 base)
{
    u32 at = (u32)(uintptr_t)g_at[i], off = at - GUEST_RAM_BASE;
    return off < GUEST_RAM_SIZE ? base + off : at;
}

/* Debug registers per watched word: register r watches word r / per_word() in view r % per_word(). */
static u32 per_word(void) { return g_n ? WATCH_WORDS / g_n : 0u; }

static LONG CALLBACK watch_handler(EXCEPTION_POINTERS *ep)
{
    static const u32 views[WATCH_WORDS] = {GUEST_RAM_BASE, GUEST_MIRROR_BASE, GUEST_UNCACHED_BASE, GUEST_SQ_BASE};
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
        DWORD64 dr[WATCH_WORDS] = {0};
        u32 per = per_word(), dr7 = 0;
        for (u32 r = 0; r < g_n * per; r++) {
            dr[r] = view(r / per, views[r % per]);
            dr7 |= 1u << (2u * r) | 0xdu << (16u + 4u * r); /* Lr on, on a write (RW 01), four bytes (LEN 11) */
        }
        c->Dr0 = (DWORD)dr[0];
        c->Dr1 = (DWORD)dr[1];
        c->Dr2 = (DWORD)dr[2];
        c->Dr3 = (DWORD)dr[3];
        c->Dr6 = 0;
        c->Dr7 = dr7;
        c->ContextFlags |= CONTEXT_DEBUG_REGISTERS;
        return EXCEPTION_CONTINUE_EXECUTION;
    }
    if (code != EXCEPTION_SINGLE_STEP || !(c->Dr6 & 0xfu) || !g_n) return EXCEPTION_CONTINUE_SEARCH;
    u32 r = 0;
    while (!(c->Dr6 & 1u << r)) r++;
    c->Dr6 = 0;
    c->ContextFlags |= CONTEXT_DEBUG_REGISTERS;
    hit(c, r / per_word());
    return EXCEPTION_CONTINUE_EXECUTION;
}

void lockstep_watch_words(volatile u32 *const *at, u32 n, u32 frame)
{
    if (!g_handler) g_handler = AddVectoredExceptionHandler(1, watch_handler);
    if (!g_handler) return;
    if (n > WATCH_WORDS) n = WATCH_WORDS;
    flush();
    if (n) {
        char line[64 + WATCH_WORDS * 9];
        int k = snprintf(line, sizeof line, "lockstep watch: frame %u word", frame);
        for (u32 i = 0; i < n && k > 0 && (size_t)k < sizeof line; i++)
            k += snprintf(line + k, sizeof line - (size_t)k, " %08x", (u32)(uintptr_t)at[i]);
        port_log("%s views %u image %08x\n", line, WATCH_WORDS / n, (u32)(uintptr_t)GetModuleHandleW(NULL));
    }
    for (u32 i = 0; i < WATCH_WORDS; i++) g_at[i] = i < n ? at[i] : NULL;
    g_n = n;
    g_frame = frame;
    g_seq = 0;
    RaiseException(WATCH_ARM, 0, 0, NULL);
}

void lockstep_watch(volatile u32 *at, u32 frame)
{
    volatile u32 *one[1] = {at};
    lockstep_watch_words(one, at ? 1u : 0u, frame);
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
