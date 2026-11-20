/* XB_PROF=US: where a stretch of real-clock time goes. Every US microseconds (1000 at least) each guest thread is stopped for a moment and
 * its instruction pointer kept with the vertical blank; at the end of the run all of them go to <log>.prof, records of
 * {vertical blank, thread index, offset}, the offset from the exe's base. A pointer outside the exe (a wait, the host GPU's driver) is
 * kept as the nearest return address into the exe on the thread's stack, with bit 31 set. Read against xbox_run.map
 * (the linker's map: fn_<guest address> for the game's functions, the kit's own by name) for the logos, the menu, any stretch the
 * "state:" lines bound.
 * Nearest existing: XB_SAMPLE (every guest thread's stack of indirect calls every few seconds: what a stuck screen waits on, not
 * where time goes) and port/runtime/prof_sample.c (the Dreamcast kit's 32-bit game-thread sampler, Eip only, not linked here). */
#include <windows.h>
#include <timeapi.h>
#include <stdio.h>
#include <stdlib.h>
#include "xb.h"
#include "console.h"

#define PROF_MAX 6000000u
typedef struct { u32 vblank, off; u32 thread; } ProfRec;
static ProfRec *s_rec;
static volatile LONG s_n;
static uintptr_t s_base, s_size;

static u32 offset_of(const CONTEXT *c)
{
    if (c->Rip - s_base < s_size) return (u32)(c->Rip - s_base);
    __try {
        const uintptr_t *sp = (const uintptr_t *)(uintptr_t)c->Rsp;
        for (int i = 0; i < 1024; i++)
            if (sp[i] - s_base < s_size) return (u32)(sp[i] - s_base) | 0x80000000u;
    } __except (EXCEPTION_EXECUTE_HANDLER) {
    }
    return 0xffffffffu;
}

static DWORD WINAPI profiler(LPVOID p)
{
    DWORD ms = (DWORD)(UINT_PTR)p / 1000u;
    HANDLE th[64] = {0};
    unsigned long ids[64];
    unsigned n = 0, i, m;
    if (!ms) ms = 1;
    timeBeginPeriod(1);
    for (;;) {
        Sleep(ms);   /* not the tick: a profiler's period */
        for (m = xb_thread_ids(ids, 64); n < m; n++)
            th[n] = OpenThread(THREAD_SUSPEND_RESUME | THREAD_GET_CONTEXT | THREAD_QUERY_INFORMATION, FALSE, ids[n]);
        for (i = 0; i < n; i++) {
            CONTEXT c;
            LONG k;
            if (!th[i] || SuspendThread(th[i]) == (DWORD)-1) continue;
            c.ContextFlags = CONTEXT_CONTROL;
            if (GetThreadContext(th[i], &c) && (k = InterlockedIncrement(&s_n) - 1) < (LONG)PROF_MAX) {
                s_rec[k].vblank = port_vblank_count();
                s_rec[k].thread = i;
                s_rec[k].off = offset_of(&c);
            }
            ResumeThread(th[i]);
        }
    }
}

void xb_prof_start(void)
{
    const char *e = getenv("XB_PROF");
    const IMAGE_DOS_HEADER *dos;
    if (!e || atoi(e) <= 0) return;
    s_base = (uintptr_t)GetModuleHandleW(NULL);
    dos = (const IMAGE_DOS_HEADER *)s_base;
    s_size = ((const IMAGE_NT_HEADERS *)(s_base + (uintptr_t)dos->e_lfanew))->OptionalHeader.SizeOfImage;
    s_rec = (ProfRec *)VirtualAlloc(NULL, PROF_MAX * sizeof *s_rec, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE);
    if (!s_rec) return;
    port_log("prof: every %d us, exe base %llx size %llx, written to %s.prof at the end\n", atoi(e), (unsigned long long)s_base,
             (unsigned long long)s_size, port_log_path());
    CreateThread(NULL, 0, profiler, (LPVOID)(UINT_PTR)atoi(e), 0, NULL);
}

void xb_prof_write(void)
{
    FILE *f = NULL;
    char p[540];
    LONG n = s_n < (LONG)PROF_MAX ? s_n : (LONG)PROF_MAX;
    if (!s_rec) return;
    sprintf_s(p, sizeof p, "%s.prof", port_log_path());
    if (!fopen_s(&f, p, "wb") && f) {
        fwrite(s_rec, sizeof *s_rec, (size_t)n, f);
        fclose(f);
    }
    port_log("prof: %ld samples written to %s\n", n, p);
}
