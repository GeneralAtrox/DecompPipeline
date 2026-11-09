/* The profiler's instruction-pointer sampler (prof.c's family, split off for the file-size cap): --prof-sample US, the
 * game thread's instruction pointer every US microseconds from --prof-from on, as port.exe+OFFSET counts in FILE
 * (--prof-samples FILE), for tools/symbolize.py-style reading against port.map. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <windows.h>
#include <timeapi.h>
#include "port.h"
#include "platform.h"
#include "prof.h"

#define SAMPLES_MAX 4000000u
static HANDLE g_game_thread;
static u32 g_sample_us, *g_samples, g_nsamples;
static const char *g_samples_path;
static volatile LONG g_sampling_on, g_sampler_stop;

#pragma comment(lib, "winmm.lib")
/* The sample for a thread context: Eip when it is in port.exe. Eip in a system DLL (a wait, a file read) says nothing
 * of who called it, so the stack is scanned for the nearest value that points into port.exe (a return address, or a
 * stale one: close enough to name the caller) and the sample is that address with bit 31 set. */
static u32 sample_of(const CONTEXT *c)
{
    u32 base = (u32)(uintptr_t)GetModuleHandleW(NULL);
    const IMAGE_DOS_HEADER *dos = (const IMAGE_DOS_HEADER *)(uintptr_t)base;
    u32 size = ((const IMAGE_NT_HEADERS32 *)(uintptr_t)(base + (u32)dos->e_lfanew))->OptionalHeader.SizeOfImage;
    if (c->Eip - base < size) return c->Eip;
    __try {
        const u32 *sp = (const u32 *)(uintptr_t)c->Esp;
        for (int i = 0; i < 512; i++)
            if (sp[i] - base < size) return sp[i] | 0x80000000u;
    } __except (EXCEPTION_EXECUTE_HANDLER) {
    }
    return c->Eip;
}

static DWORD WINAPI sampler(void *arg)
{
    (void)arg;
    timeBeginPeriod(1); /* Sleep(1) otherwise sleeps a scheduler tick (15 ms): too few samples */
    while (!g_sampler_stop) {
        Sleep(1); /* not the tick: a watchdog or sampler thread */
        if (!g_sampling_on || g_nsamples >= SAMPLES_MAX) continue;
        if (SuspendThread(g_game_thread) == (DWORD)-1) continue;
        CONTEXT c;
        c.ContextFlags = CONTEXT_CONTROL;
        if (GetThreadContext(g_game_thread, &c)) g_samples[g_nsamples++] = sample_of(&c);
        ResumeThread(g_game_thread);
    }
    return 0;
}

static void sampler_start(void)
{
    if (!g_sample_us || g_game_thread || !g_samples) return;
    if (!DuplicateHandle(GetCurrentProcess(), GetCurrentThread(), GetCurrentProcess(), &g_game_thread, 0, FALSE,
                         DUPLICATE_SAME_ACCESS))
        return;
    CloseHandle(CreateThread(NULL, 0, sampler, NULL, 0, NULL));
}

/* At each blank under --prof, on the game thread: on from --prof-from on. */
void prof_sample_frame(int on)
{
    sampler_start();
    g_sampling_on = on;
}

static int cmp_addr(const void *a, const void *b)
{
    u32 x = *(const u32 *)a, y = *(const u32 *)b;
    return x < y ? -1 : x > y;
}

void prof_sample_report(void)
{
    u32 base = (u32)(uintptr_t)GetModuleHandleW(NULL);
    FILE *f;
    g_sampling_on = 0;
    if (!g_nsamples || !g_samples_path) return;
    g_sampler_stop = 1;
    qsort(g_samples, g_nsamples, sizeof *g_samples, cmp_addr);
    if (fopen_s(&f, g_samples_path, "w") || !f) return;
    fprintf(f, "# %u samples, one every ~1 ms of the game thread; address count (port.exe+offset)\n", g_nsamples);
    for (u32 i = 0; i < g_nsamples;) {
        u32 j = i;
        while (j < g_nsamples && g_samples[j] == g_samples[i]) j++;
        u32 v = g_samples[i], flag = v & 0x80000000u; /* flag: a system wait, named by its caller in port.exe */
        fprintf(f, "%08x %u\n", ((v & 0x7fffffffu) - base) | flag, j - i);
        i = j;
    }
    fclose(f);
}

int prof_sample_option(const char *arg, const char *value)
{
    if (!strcmp(arg, "--prof-sample")) {
        g_sample_us = (u32)strtoul(value, NULL, 10);
        if (!g_samples) g_samples = malloc(SAMPLES_MAX * sizeof *g_samples);
        return 2;
    }
    if (!strcmp(arg, "--prof-samples")) {
        g_samples_path = value;
        return 2;
    }
    return 0;
}
