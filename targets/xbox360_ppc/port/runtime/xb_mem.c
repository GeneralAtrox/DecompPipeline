/* Guest memory: one 4 GB host reserve; the regions the game has asked for are committed. Everything else faults, and the
 * fault is a stop that names the guest address (the GPU window 0x7FC8xxxx is never mapped: the library that touches it is
 * replaced, so a guest access there means the cut is wrong). */
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "xb.h"
#include "xb_game.h"
#include "console.h"

u8 *tc_base;
__declspec(thread) u64 tc_resv_v;   /* each guest thread's reservation (tc_rt.h, tc_lwarx and tc_stwcx) */
__declspec(thread) u32 tc_resv_s;
static u32 s_next = 0x20000000u;   /* the 4 KB-page line's next address (xb_alloc, xb_alloc_block), also named by a fault */
/* The console's layout: 4 KB-page memory below 0x40000000, 64 KB-page memory from 0x40000000 to 0x7FFFFFFF. Too Human's heap free (8281f3e0) relies on
 * it: an address in 0x40000001..0x7FFFFFFF is taken for a small block in one of its 64 KB pages and only the others are looked up as large blocks, so a
 * large block (4 KB pages, 82a6f7a0 with 0x801000) handed out at 0x40730000 faulted at frame 809 of the level load. NtAllocateVirtualMemory's
 * MEM_LARGE_PAGES regions come from this second line */
static u32 s_kit_n, s_untracked_n, s_untracked, s_lost_n, s_lost, s_unknown_free_n;   /* XB_MEMLOG's counts: xb_alloc calls; blocks not kept because the live list was full
                                                                 * (they can never be given back); freed spans dropped because the free list was full;
                                                                 * frees of an address no block starts at */
static u32 s_kit_small;   /* xb_alloc's bytes in the 4 KB-page line: the kit's own memory, never freed (the out-of-memory census) */
static u32 s_next_large = 0x40000000u;
#define XB_LINE_SMALL_END 0x40000000u
#define XB_LINE_LARGE_END 0x70000000u

/* XB_DATABP=[r]<guest address>[@frame]: a write that changes that word, by any guest thread, from that frame on, logs its new value and the writer's guest
 * calls (a hardware breakpoint on each thread, set by xb_databp_thread when the thread starts). Which code moves a counter nothing else names: Too Human's
 * map load waits on one */
static u32 s_databp, s_databp_from, s_databp_last, s_databp_reads;   /* r<address>: reads too, each distinct caller chain once */
static volatile LONG s_databp_hits;

static char s_databp_chain[128];   /* an address that follows pointers (XB_WATCH's form), found at frame s_databp_from: an object the game allocates */

static void databp_set(HANDLE t)
{
    CONTEXT c;
    memset(&c, 0, sizeof c);
    c.ContextFlags = CONTEXT_DEBUG_REGISTERS;
    c.Dr0 = (DWORD64)(tc_base + (s_databp & ~3u));
    c.Dr7 = 1u | ((s_databp_reads ? 3u : 1u) << 16) | (3u << 18);   /* local enable 0, break on write (or read and write), 4 bytes */
    SetThreadContext(t, &c);
}

void xb_databp_thread(void)
{
    if (s_databp) databp_set(GetCurrentThread());
}

static void mem_census(unsigned frame);

void xb_databp_frame(unsigned frame)
{
    unsigned long ids[64];
    unsigned i, n;
    u32 at;
    mem_census(frame);
    if (!s_databp_chain[0] || s_databp || frame < s_databp_from || !(at = xb_watch_resolve(s_databp_chain))) return;
    s_databp = at;
    s_databp_last = rd32(at & ~3u);
    port_log("databp %s is %08x (value %08x), armed at frame %u\n", s_databp_chain, at, s_databp_last, frame);
    databp_set(GetCurrentThread());
    n = xb_thread_ids(ids, 64);
    for (i = 0; i < n; i++) {
        HANDLE t;
        if (ids[i] == GetCurrentThreadId() || !(t = OpenThread(THREAD_SUSPEND_RESUME | THREAD_GET_CONTEXT | THREAD_SET_CONTEXT, FALSE, ids[i]))) continue;
        if (SuspendThread(t) != (DWORD)-1) {
            databp_set(t);
            ResumeThread(t);
        }
        CloseHandle(t);
    }
}

static LONG WINAPI on_fault(EXCEPTION_POINTERS *e)
{
    /* 256: the guest fault's line below runs to about 140 characters, and sprintf_s past 128 ended the run as fail-fast 0xC0000409 (invalid
     * argument) with no line in the log: Too Human's first tries at 2560x1600, caught under a debugger on build box A 2026-11-07 */
    char d[256];
    if (e->ExceptionRecord->ExceptionCode == EXCEPTION_SINGLE_STEP && s_databp && (e->ContextRecord->Dr6 & 1u)) {
        char calls[160];
        u32 now = s_databp_reads ? s_databp_last : rd32(s_databp & ~3u);   /* a read here would break again, without end */
        e->ContextRecord->Dr6 = 0;
        if (s_databp_reads && port_vblank_count() >= s_databp_from) {
            static u32 seen[64];
            static unsigned seen_n;
            u32 h = 2166136261u;
            const char *q;
            unsigned i;
            xb_stack_line(calls, sizeof calls);
            for (q = calls; *q; q++) h = (h ^ (u8)*q) * 16777619u;
            for (i = 0; i < seen_n && seen[i] != h; i++) {}
            if (i == seen_n && seen_n < 64) {
                seen[seen_n++] = h;
                port_log("databp %08x touched (value when armed %08x), frame %u, host thread %lu:%s\n", s_databp, now, port_vblank_count(), GetCurrentThreadId(), calls);
            }
        } else if (now != s_databp_last && port_vblank_count() >= s_databp_from && InterlockedIncrement(&s_databp_hits) <= 400) {
            xb_stack_line(calls, sizeof calls);
            port_log("databp %08x now %08x (was %08x), frame %u, host thread %lu:%s\n", s_databp, now, s_databp_last, port_vblank_count(), GetCurrentThreadId(), calls);
        }
        s_databp_last = now;
        return EXCEPTION_CONTINUE_EXECUTION;
    }
    if (e->ExceptionRecord->ExceptionCode == EXCEPTION_STACK_OVERFLOW)
        xb_stop("host_stack_overflow", "the translated code recursed past the host stack", 0);
    if (e->ExceptionRecord->ExceptionCode == EXCEPTION_ACCESS_VIOLATION && e->ExceptionRecord->NumberParameters >= 2) {
        u64 at = (u64)e->ExceptionRecord->ExceptionInformation[1];
        if (at >= (u64)tc_base && at < (u64)tc_base + 0x100000000ull) {
            /* Where in the host function: its offset, which the compiler's listing of that one generated file (/FAsc) turns into the guest instruction */
            DWORD64 img = 0, rip = e->ContextRecord->Rip;
            PRUNTIME_FUNCTION fn = RtlLookupFunctionEntry(rip, &img, NULL);
            sprintf_s(d, sizeof d, "guest %s of %08x (memory handed out up to %08x and %08x), host instruction +%llx in its function at RVA %lx (xbox_run.map)", e->ExceptionRecord->ExceptionInformation[0] ? "write" : "read",
                      (u32)(at - (u64)tc_base), s_next, s_next_large, fn ? (unsigned long long)(rip - (img + fn->BeginAddress)) : 0ull, fn ? (unsigned long)fn->BeginAddress : 0ul);
            xb_gcb_report();   /* the GPU callbacks before it (xb_d3d_res.h) */
            xb_stop("fault", d, (u32)(at - (u64)tc_base));
        }
    }
    return EXCEPTION_CONTINUE_SEARCH;
}

/* Which guest pages are committed, one bit a 4 KB page (128 KB for the 4 GB reserve), so the draw code asks the kit, not Windows, whether memory
 * can be read (xb_mem_readable, the d3d layer's guest_readable). It stays right because the kit is the only thing that changes the reserve: commit()
 * sets a page's bit after VirtualAlloc commits it, and xb_mem_uncommit clears the bits before it decommits. Nothing else in the kit commits,
 * decommits or re-protects guest pages (no protection calls, no guard pages); a block the game frees (xb_free_block) stays committed and zeroed, as
 * before, so it still reads as readable, as VirtualQuery says too. Too Human's gameplay asked VirtualQuery about 24 ms a frame through guest_readable's
 * 16 regions kept for the frame (build box A profile, 2026-11-14) */
static volatile LONG64 s_page_bits[(0x100000000ull >> 12) / 64u];

static void pages_mark(u32 lo, u32 size, int on)
{
    u32 p0 = lo >> 12, p1 = (u32)(((u64)lo + size - 1u) >> 12), w;
    if (!size) return;
    for (w = p0 >> 6; w <= p1 >> 6; w++) {
        u64 m = ~0ull;
        if (w == p0 >> 6) m &= ~0ull << (p0 & 63u);
        if (w == p1 >> 6) m &= ~0ull >> (63u - (p1 & 63u));
        if (on) InterlockedOr64(&s_page_bits[w], (LONG64)m);
        else InterlockedAnd64(&s_page_bits[w], (LONG64)~m);
    }
}

/* Whether every byte of a..a+n-1 (a's page when n is 0) is committed guest memory */
int xb_mem_readable(u32 a, u32 n)
{
    u64 end = (u64)a + (n ? n : 1u);
    u32 p0 = a >> 12, p1, w;
    if (end > 0x100000000ull) return 0;
    p1 = (u32)((end - 1u) >> 12);
    for (w = p0 >> 6; w <= p1 >> 6; w++) {
        u64 m = ~0ull;
        if (w == p0 >> 6) m &= ~0ull << (p0 & 63u);
        if (w == p1 >> 6) m &= ~0ull >> (63u - (p1 & 63u));
        if (((u64)s_page_bits[w] & m) != m) return 0;
    }
    return 1;
}

/* Write generations (XB_HASH_KEPT, on by default; =0 reserves without write watch, as before 2026-11-14). The 4 GB reserve is made with
 * MEM_WRITE_WATCH, so Windows notes every page written since its note was last reset, by any thread, the kit's own writes and the system's
 * (a file read into guest memory) included. xb_mem_write_gen collects the notes of a range's pages into s_wgen, one number a page: the
 * collection that saw it written. Collections count up in s_gen. A caller that hashed a range when s_gen was G and later finds no page there
 * above G knows no byte of the whole range was written since: the d3d layer's data_hash (xb_d3d_tex.h) keeps a buffer's hash so. A page
 * committed or decommitted counts as written (its bytes become zero). Only s_ww_lock's holder resets notes, and it records every page it
 * resets before it lets go, so a write is never lost between two callers */
#ifndef XB_WW_PAGES
#define XB_WW_PAGES 4096u   /* xb.h's; here too for test_x360_mem_readable's cut of this file */
#endif
static int s_ww;
static u64 s_wgen[0x100000000ull >> 12], s_gen = 1;
static SRWLOCK s_ww_lock = SRWLOCK_INIT;
static PVOID s_ww_at[XB_WW_PAGES];

static void ww_written(u32 lo, u32 size)   /* every page of lo..lo+size-1 counts as written now */
{
    u32 p0 = lo >> 12, p1 = (u32)(((u64)lo + size - 1u) >> 12), i;
    if (!s_ww || !size) return;
    AcquireSRWLockExclusive(&s_ww_lock);
    s_gen++;
    for (i = p0; i <= p1; i++) s_wgen[i] = s_gen;
    ReleaseSRWLockExclusive(&s_ww_lock);
}

u64 xb_mem_write_gen(u32 a, u32 n, u64 *now)
{
    u32 p0 = a >> 12, p1, i;
    u64 top = 0;
    ULONG_PTR count = 1;
    DWORD gran;
    PVOID one;
    u8 *lo;
    size_t size;
    if (!s_ww || !n || (u64)a + n > 0x100000000ull) return ~0ull;
    p1 = (u32)(((u64)a + n - 1u) >> 12);
    if (p1 - p0 >= XB_WW_PAGES) return ~0ull;
    lo = tc_base + ((size_t)p0 << 12), size = (size_t)(p1 - p0 + 1u) << 12;
    AcquireSRWLockExclusive(&s_ww_lock);
    if (GetWriteWatch(0, lo, size, &one, &count, &gran)) top = ~0ull;   /* a look, no reset: most ranges were not written */
    else if (count) {
        s_gen++;
        count = XB_WW_PAGES;
        if (GetWriteWatch(WRITE_WATCH_FLAG_RESET, lo, size, s_ww_at, &count, &gran) || count >= XB_WW_PAGES)
            for (i = p0; i <= p1; i++) s_wgen[i] = s_gen;   /* not told which: all of them */
        else
            for (i = 0; i < (u32)count; i++) s_wgen[(size_t)((u8 *)s_ww_at[i] - tc_base) >> 12] = s_gen;
    }
    if (!top)
        for (i = p0; i <= p1; i++)
            if (s_wgen[i] > top) top = s_wgen[i];
    *now = s_gen;
    ReleaseSRWLockExclusive(&s_ww_lock);
    return top;
}

static int commit(u32 lo, u32 size)
{
    if (VirtualAlloc(tc_base + lo, size, MEM_COMMIT, PAGE_READWRITE) == NULL) return 0;
    pages_mark(lo, size, 1);
    ww_written(lo, size);
    return 1;
}

/* Gives pages back to Windows (decommitted: a read faults). The kit's map says not readable first, so no draw reads them after */
void xb_mem_uncommit(u32 at, u32 size)
{
    u32 lo = at & ~0xFFFu, hi = (u32)(((u64)at + size + 0xFFFu) & ~0xFFFull);
    pages_mark(lo, hi - lo, 0);
    ww_written(lo, hi - lo);
    VirtualFree(tc_base + lo, hi - lo, MEM_DECOMMIT);
}

int xb_mem_init(const char *image_path, u32 load_addr)
{
    FILE *f = NULL;
    long n;
    {
        const char *e = getenv("XB_HASH_KEPT");
        if (!(e && e[0] == '0' && !e[1])) tc_base = (u8 *)VirtualAlloc(NULL, 0x100000000ull, MEM_RESERVE | MEM_WRITE_WATCH, PAGE_NOACCESS);
        s_ww = tc_base != NULL;
        if (!tc_base) tc_base = (u8 *)VirtualAlloc(NULL, 0x100000000ull, MEM_RESERVE, PAGE_NOACCESS);
        port_log("guest memory: writes %s (XB_HASH_KEPT)\n", s_ww ? "tracked by the reserve's write watch" : "not tracked");
    }
    if (!tc_base) return 0;
    AddVectoredExceptionHandler(1, on_fault);
    if (fopen_s(&f, image_path, "rb") || !f) return 0;
    fseek(f, 0, SEEK_END);
    n = ftell(f);
    fseek(f, 0, SEEK_SET);
    if (!commit(load_addr, ((u32)n + 0xFFFFu) & ~0xFFFFu)) return 0;
    if (fread(tc_base + load_addr, 1, (size_t)n, f) != (size_t)n) return 0;
    fclose(f);
    if (!commit(0x70000000u, 0x00100000u)) return 0;   /* the main thread's stack (target.json memory.stack) */
    if (!commit(0x7FFF0000u, 0x00010000u)) return 0;   /* the processor control region (r13) and the kernel variables */
#ifdef XB_XMA_WINDOW
    if (!commit(0x7FEA0000u, 0x00010000u)) return 0;   /* plain memory in the XMA registers' place, for bring-up: see xb_game.h */
#endif
    /* The game reads its thread id as PCR[0x100]->[0x14c] (GetCurrentThreadId, 822cdde0, first fault): a block of memory with
     * the one field it has read, nothing else of a kernel thread. */
    wr32(0x7FFF0100u, 0x7FFF1000u);
    wr32(0x7FFF1000u + 0x14cu, GetCurrentThreadId());
    xb_tls_new(0x7FFF0000u);
    if (getenv("XB_DATABP")) {
        char *end;
        const char *v = getenv("XB_DATABP");
        if (*v == 'r') s_databp_reads = 1, v++;
        if (strchr(v, '>')) {
            const char *at = strchr(v, '@');
            size_t len = at ? (size_t)(at - v) : strlen(v);
            if (len >= sizeof s_databp_chain) len = sizeof s_databp_chain - 1;
            memcpy(s_databp_chain, v, len);
            if (at) s_databp_from = (u32)strtoul(at + 1, NULL, 10);
        } else {
            s_databp = (u32)strtoul(v, &end, 16);
            if (*end == '@') s_databp_from = (u32)strtoul(end + 1, NULL, 10);
        }
    }
    xb_databp_thread();
    return 1;
}

/* A thread's own copy of the game's thread-local data, at PCR[0]: Too Human, first fault 828243b0 reading r13[0] + 0x448. */
void xb_tls_new(u32 pcr)
{
    u32 at;
    if (!XB_TLS_SIZE) return;
    at = xb_alloc(XB_TLS_SIZE);
    memcpy(tc_base + at, tc_base + XB_TLS_RAW, XB_TLS_SIZE);
    wr32(pcr, at);
}

u64 tc_timebase(void)
{
    static LARGE_INTEGER f;
    LARGE_INTEGER c;
    if (host_fixed_clock()) return host_ticks(49875000u);
    if (!f.QuadPart) QueryPerformanceFrequency(&f);
    QueryPerformanceCounter(&c);
    return (u64)c.QuadPart / (u64)f.QuadPart * 49875000ull + (u64)c.QuadPart % (u64)f.QuadPart * 49875000ull / (u64)f.QuadPart;
}

static SRWLOCK s_alloc_lock = SRWLOCK_INIT;

/* Guest virtual memory is handed out in order from 0x20000000: the game never sees where, only that the range is its own. */
u32 xb_alloc(u32 size)
{
    u32 at;
    size = (size + 0xFFFFu) & ~0xFFFFu;
    AcquireSRWLockExclusive(&s_alloc_lock);
    at = (s_next + 0xFFFFu) & ~0xFFFFu;
    if (at + size <= XB_LINE_SMALL_END) {
        if (!commit(at, size)) xb_stop("out_of_memory", "guest virtual memory", size);
        s_next = at + size;
        s_kit_small += size;
        s_kit_n++;
    } else {   /* the kit's own memory, never given to the game's heap, may go on in the 64 KB-page line */
        at = (s_next_large + 0xFFFFu) & ~0xFFFFu;
        if (at + size > XB_LINE_LARGE_END || !commit(at, size)) xb_stop("out_of_memory", "guest virtual memory", size);
        s_next_large = at + size;
    }
    ReleaseSRWLockExclusive(&s_alloc_lock);
    return at;
}

/* The game's own blocks (NtAllocateVirtualMemory's regions, MmAllocatePhysicalMemoryEx's blocks) are kept so the frees can give them back: a freed block
 * is zeroed and handed out again, first fit. Too Human loads and frees for thousands of frames; with nothing reused its blocks passed 0x40000000, which
 * its heap (8281f3e0) takes for the console's 64 KB-page range, and it faulted at frame 2688. Kept committed, so a stale read still reads zero */
typedef struct { u32 at, size; } XbSpan;
static XbSpan s_live[32768], s_free[8192];
static unsigned s_live_n, s_free_n;

static u32 alloc_in(u32 size, u32 align, int large)
{
    u32 at = 0, end, lo = large ? XB_LINE_SMALL_END : 0x20000000u, hi = large ? XB_LINE_LARGE_END : XB_LINE_SMALL_END, *next = large ? &s_next_large : &s_next;
    unsigned i;
    size = (size + 0xFFFu) & ~0xFFFu;
    if (align < 0x1000u) align = 0x1000u;
    AcquireSRWLockExclusive(&s_alloc_lock);
    for (i = 0; i < s_free_n && !at; i++) {
        u32 a0 = (s_free[i].at + align - 1) & ~(align - 1);
        end = s_free[i].at + s_free[i].size;
        if (a0 < s_free[i].at || a0 + size > end || s_free[i].at < lo || end > hi) continue;   /* a freed block is reused only in its own line */
        at = a0;
        if (a0 + size < end && a0 > s_free[i].at && s_free_n < sizeof s_free / sizeof *s_free) {
            s_free[s_free_n].at = a0 + size, s_free[s_free_n].size = end - (a0 + size), s_free_n++;
            s_free[i].size = a0 - s_free[i].at;
        } else if (a0 > s_free[i].at)
            s_free[i].size = a0 - s_free[i].at;                     /* the tail past the block is lost when the list is full */
        else if (a0 + size < end)
            s_free[i].at = a0 + size, s_free[i].size = end - (a0 + size);
        else
            s_free[i] = s_free[--s_free_n];
    }
    if (!at) {
        at = (*next + align - 1) & ~(align - 1);
        if (at + size > hi || !commit(at, size)) {
            u32 tot = 0, big = 0, live = 0;
            for (i = 0; i < s_free_n; i++)
                if (s_free[i].at >= lo && s_free[i].at < hi) tot += s_free[i].size, big = s_free[i].size > big ? s_free[i].size : big;
            for (i = 0; i < s_live_n; i++)
                if (s_live[i].at >= lo && s_live[i].at < hi) live += s_live[i].size;
            port_log("memory census at the stop: the game's live blocks %u KB, the kit's own memory %u KB in the 4 KB-page line, request %u KB align %x\n", live >> 10, s_kit_small >> 10, size >> 10, align);
            port_log("memory line %08x..%08x: next %08x, %u spans free holding %u KB (largest %u KB), %u blocks live of %u kept\n", lo, hi, *next,
                     s_free_n, tot >> 10, big >> 10, s_live_n, (unsigned)(sizeof s_live / sizeof *s_live));
            xb_stop("out_of_memory", large ? "guest 64 KB-page memory" : "guest 4 KB-page memory (below 0x40000000)", size);
        }
        *next = at + size;
    }
    if (s_live_n < sizeof s_live / sizeof *s_live) s_live[s_live_n].at = at, s_live[s_live_n].size = size, s_live_n++;
    else s_untracked_n++, s_untracked += size;
    ReleaseSRWLockExclusive(&s_alloc_lock);
    return at;
}

u32 xb_alloc_block(u32 size, u32 align) { return alloc_in(size, align, 0); }
u32 xb_alloc_block_large(u32 size, u32 align) { return alloc_in(size, align, 1); }

/* The console's kernel keeps memory by the page: NtFreeVirtualMemory (MEM_RELEASE, export 220), MmFreePhysicalMemory (189) and
 * NtFreeEncryptedMemory (651) make a region's pages free, and NtAllocateVirtualMemory (204) and MmAllocatePhysicalMemoryEx (186) take any run of
 * free pages, whoever freed its neighbours. So a freed span joins the free spans it touches, in its own line, and one that reaches the line's next
 * address gives it back there. Xenia, read for behaviour only, does the same (BaseHeap::Release puts the pages back in its free-run tracker,
 * joined with the runs beside them). Kept apart, Too Human's level loads cut the line into pieces too small for its 3 MB blocks and it stopped
 * out of memory in the opening (build box A h5, vertical blank 2118, 2026-11-09). XB_MEM_FREE=split selects the old way: each span kept on its own */
static void free_span(u32 at, u32 size)
{
    static int split = -1;
    u32 e = at + size, small = at < XB_LINE_SMALL_END, *next = small ? &s_next : &s_next_large;
    unsigned j;
    if (split < 0) {
        const char *v = getenv("XB_MEM_FREE");
        split = v && !strcmp(v, "split");
        if (split) port_log("memory: XB_MEM_FREE=split, freed spans are kept apart (the old way)\n");
    }
    for (j = 0; j < s_free_n && !split;) {
        XbSpan f = s_free[j];
        if ((f.at < XB_LINE_SMALL_END) == small && (f.at + f.size == at || f.at == e)) {
            if (f.at == e) e += f.size; else at = f.at;
            s_free[j] = s_free[--s_free_n];
            j = 0;
        } else
            j++;
    }
    if (e == *next && !split) *next = at;   /* still committed and zeroed: handed out again from the line */
    else if (s_free_n < sizeof s_free / sizeof *s_free) s_free[s_free_n].at = at, s_free[s_free_n].size = e - at, s_free_n++;
    else s_lost_n++, s_lost += e - at;
}

/* XB_MEMLOG=<frames>: every that many frames, one line per memory line saying where its memory is: the line's next address, the game's live blocks, the
 * free spans (and the largest, which is what a big request needs), the kit's own never-freed memory, and the three ways memory is lost for good. Which of
 * them grows is the cause of an out-of-memory stop long before the stop's own census */
static void mem_census(unsigned frame)
{
    static int every = -1;
    unsigned i, l;
    if (every < 0) {
        const char *v = getenv("XB_MEMLOG");
        every = v ? atoi(v) : 0;
    }
    if (every <= 0 || frame % (unsigned)every) return;
    AcquireSRWLockShared(&s_alloc_lock);
    for (l = 0; l < 2; l++) {
        u32 lo = l ? XB_LINE_SMALL_END : 0x20000000u, hi = l ? XB_LINE_LARGE_END : XB_LINE_SMALL_END, live = 0, tot = 0, big = 0, ln = 0, fn = 0;
        for (i = 0; i < s_live_n; i++)
            if (s_live[i].at >= lo && s_live[i].at < hi) live += s_live[i].size, ln++;
        for (i = 0; i < s_free_n; i++)
            if (s_free[i].at >= lo && s_free[i].at < hi) tot += s_free[i].size, fn++, big = s_free[i].size > big ? s_free[i].size : big;
        port_log("memlog frame %u line %08x: next %08x (%u KB used), live %u blocks %u KB, free %u spans %u KB largest %u KB, kit %u calls %u KB, untracked %u blocks %u KB, lost %u spans %u KB, unknown frees %u\n",
                 frame, lo, l ? s_next_large : s_next, ((l ? s_next_large : s_next) - lo) >> 10, ln, live >> 10, fn, tot >> 10, big >> 10,
                 l ? 0 : s_kit_n, l ? 0 : s_kit_small >> 10, s_untracked_n, s_untracked >> 10, s_lost_n, s_lost >> 10, s_unknown_free_n);
    }
    ReleaseSRWLockShared(&s_alloc_lock);
}

/* The size of the block given back, 0 when xb_alloc_block did not make it there (it stays) */
u32 xb_free_block(u32 at)
{
    u32 size = 0;
    unsigned i;
    AcquireSRWLockExclusive(&s_alloc_lock);
    for (i = s_live_n; i-- > 0;)
        if (s_live[i].at == at) {
            size = s_live[i].size;
            s_live[i] = s_live[--s_live_n];
            memset(tc_base + at, 0, size);
            free_span(at, size);
            break;
        }
    if (!size) s_unknown_free_n++;
    ReleaseSRWLockExclusive(&s_alloc_lock);
    return size;
}

/* At a stop: every run of at least 256 bytes of one repeated word in the memory handed out (Too Human's "0000" fill,
 * 0x30303030, where pointers should be), with the recent file reads that landed on each. Sixteen runs at most */
void xb_scan_fill(u32 word)
{
    u32 at = 0x20000000u, top = s_next, run = 0, shown = 0, runs = 0, bytes = 0;
#ifdef XB_STOP_FILL
    if (!word) word = XB_STOP_FILL;
#endif
    if (!word) return;
    /* Only committed pages: the line has holes (a block's 64 KB rounding, line 190), and a read in one faulted inside the fault's own stop, whose
     * second stop then waited for ever (Too Human at 2560x1600 reached the watchdog instead of ending on its fault; build box A, 2026-11-07) */
    u64 ok_to = 0;   /* where the committed stretch holding at ends */
    for (; at <= top; at += 4) {
        int in = at < top, hole = 0;
        if (in && at >= ok_to) {
            MEMORY_BASIC_INFORMATION m;
            if (!VirtualQuery(tc_base + at, &m, sizeof m)) break;
            ok_to = (u64)((u8 *)m.BaseAddress + m.RegionSize - tc_base);
            hole = m.State != MEM_COMMIT;
        }
        if (in && !hole && rd32(at) == word) { run += 4; continue; }
        if (run >= 256) {   /* a run ends at a different word, a hole or the top */
            runs++;
            bytes += run;
            if (shown++ < 16) {
                port_log("fill %08x: %08x + %x\n", word, at - run, run);
                xb_file_reads_over(at - run, at);
            }
        }
        run = 0;
        if (hole) {   /* on past the hole: the next pass reads at its end */
            at = (u32)(ok_to < top ? ok_to : top) - 4;
            ok_to = 0;
        }
    }
    port_log("fill %08x: %u runs of 256 bytes or more, %u bytes, in %08x..%08x\n", word, runs, bytes, 0x20000000u, top);
    if (runs) xb_threads_dump();   /* the fill was seen still growing during a stop: its writer is one of these */
}

/* A guest structure that the host does the work of (a critical section, an event) is a host object found by the structure's
 * guest address and its kind, which the size tells apart (a critical section 48 bytes, an event 8, a host texture 40): Too Human
 * frees a critical section and makes a surface header in its memory, and the texture found by that address was the section,
 * whose first word (made, 1) a resolve then copied from as a texture. Open addressing; 4096 live objects at most. */
void *xb_host_object(u32 guest, unsigned size)
{
    /* Open addressing that grows at three quarters full. It was a fixed 4096 with no way out of the probe: nothing is ever removed, and Too Human's menu
     * makes its 4097th object (a texture, an event, a lock) about frame 120 to 180, after which the probe circled forever holding the lock and every thread
     * that made or looked up one stopped (build box A, 2026-11-05: the render thread inside a draw at 829fcfc0, then inside SetDepthStencilSurface 8274ebf0).
     * The objects are allocated apart from the table, so a pointer handed out stays good when it grows */
    typedef struct { u32 guest, size; void *obj; } Slot;
    static Slot *tab;
    static unsigned cap, used, bits;   /* cap is 1 << bits; the slot is the hash's top bits */
    static SRWLOCK lock = SRWLOCK_INIT;
    unsigned i;
    void *o;
    AcquireSRWLockExclusive(&lock);
    if ((used + 1) * 4u > cap * 3u) {
        unsigned ncap = cap ? cap * 2u : 4096u, nbits = cap ? bits + 1u : 12u, k;
        Slot *n = (Slot *)calloc(ncap, sizeof *n);
        if (!n) xb_stop("host_object", "no memory for a larger table of host objects", ncap);
        for (k = 0; k < cap; k++)
            if (tab[k].guest) {
                for (i = (tab[k].guest >> 2) * 2654435761u >> (32u - nbits); n[i].guest; i = (i + 1u) & (ncap - 1u)) {}
                n[i] = tab[k];
            }
        free(tab);
        tab = n, cap = ncap, bits = nbits;
        if (ncap > 4096u) port_log("host objects: %u in use, table now %u\n", used, ncap);
    }
    for (i = (guest >> 2) * 2654435761u >> (32u - bits); ; i = (i + 1u) & (cap - 1u)) {
        if (tab[i].guest == guest && tab[i].size == size) { o = tab[i].obj; break; }
        if (!tab[i].guest) {
            tab[i].guest = guest;
            tab[i].size = size;
            o = tab[i].obj = calloc(1, size);
            used++;
            break;
        }
    }
    ReleaseSRWLockExclusive(&lock);
    return o;
}
