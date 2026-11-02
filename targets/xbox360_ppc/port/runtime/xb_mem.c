/* Guest memory: one 4 GB host reserve; the regions the game has asked for are committed. Everything else faults, and the
 * fault is a stop that names the guest address (the GPU window 0x7FC8xxxx is never mapped: the library that touches it is
 * replaced, so a guest access there means the cut is wrong). */
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include "xb.h"

u8 *tc_base;

static LONG WINAPI on_fault(EXCEPTION_POINTERS *e)
{
    char d[96];
    if (e->ExceptionRecord->ExceptionCode == EXCEPTION_STACK_OVERFLOW)
        xb_stop("host_stack_overflow", "the translated code recursed past the host stack", 0);
    if (e->ExceptionRecord->ExceptionCode == EXCEPTION_ACCESS_VIOLATION && e->ExceptionRecord->NumberParameters >= 2) {
        u64 at = (u64)e->ExceptionRecord->ExceptionInformation[1];
        if (at >= (u64)tc_base && at < (u64)tc_base + 0x100000000ull) {
            sprintf_s(d, sizeof d, "guest %s of %08x", e->ExceptionRecord->ExceptionInformation[0] ? "write" : "read",
                      (u32)(at - (u64)tc_base));
            xb_stop("fault", d, (u32)(at - (u64)tc_base));
        }
    }
    return EXCEPTION_CONTINUE_SEARCH;
}

static int commit(u32 lo, u32 size)
{
    return VirtualAlloc(tc_base + lo, size, MEM_COMMIT, PAGE_READWRITE) != NULL;
}

int xb_mem_init(const char *image_path, u32 load_addr)
{
    FILE *f = NULL;
    long n;
    tc_base = (u8 *)VirtualAlloc(NULL, 0x100000000ull, MEM_RESERVE, PAGE_NOACCESS);
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
    /* The game reads its thread id as PCR[0x100]->[0x14c] (GetCurrentThreadId, 822cdde0, first fault): a block of memory with
     * the one field it has read, nothing else of a kernel thread. */
    wr32(0x7FFF0100u, 0x7FFF1000u);
    wr32(0x7FFF1000u + 0x14cu, GetCurrentThreadId());
    return 1;
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

/* Guest virtual memory is handed out in order from 0x20000000: the game never sees where, only that the range is its own. */
u32 xb_alloc(u32 size)
{
    static u32 next = 0x20000000u;
    static SRWLOCK lock = SRWLOCK_INIT;
    u32 at;
    size = (size + 0xFFFFu) & ~0xFFFFu;
    AcquireSRWLockExclusive(&lock);
    at = next;
    if (at + size > 0x70000000u || !commit(at, size)) xb_stop("out_of_memory", "guest virtual memory", size);
    next = at + size;
    ReleaseSRWLockExclusive(&lock);
    return at;
}

/* A guest structure that the host does the work of (a critical section, an event) is a host object found by the structure's
 * guest address: the game's copy of the structure is never interpreted. Open addressing; 4096 live objects at most. */
void *xb_host_object(u32 guest, unsigned size)
{
    static struct { u32 guest; void *obj; } tab[4096];
    static SRWLOCK lock = SRWLOCK_INIT;
    unsigned i = (guest >> 2) * 2654435761u >> 20;
    void *o;
    AcquireSRWLockExclusive(&lock);
    for (;; i = (i + 1) & 4095) {
        if (tab[i].guest == guest) { o = tab[i].obj; break; }
        if (!tab[i].guest) {
            tab[i].guest = guest;
            o = tab[i].obj = calloc(1, size);
            break;
        }
    }
    ReleaseSRWLockExclusive(&lock);
    return o;
}
