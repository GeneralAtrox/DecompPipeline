/* The work counters (work_count.h).
 *
 * Nearest existing: play_stats.c (the target's times, read from a play session) and runtime/prof.c (where the time goes,
 * --prof); both measure time on this machine, these count work and repeat exactly. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <windows.h>
#include "work_count.h"

int g_work_on;
u64 g_work_now[WC_N];
static u64 g_total[WC_N], g_peak[WC_N];
static const char *g_file;
static u32 g_from, g_blanks, g_last;
static DWORD g_thread;

static const char *const g_names[WC_N] = {"allocs", "alloc_bytes", "draws", "uploads", "gpu_creates", "gpu_copies", "presents",
                                          "guest_calls", "guest_writes", "vector_ops", "sound_cpu", "scene_headers",
                                          "scene_triangles", "sprite_commands", "composes", "disc_bytes"};

/* counts for the game's thread alone: the others run on the machine's time */
static void note(int id, size_t n)
{
    if (g_thread == GetCurrentThreadId()) g_work_now[id] += n;
}

/* The heap, as the exe imports it: a static CRT reaches HeapAlloc, a DLL CRT is reached by its own names. */
static void *(__cdecl *g_malloc)(size_t);
static void *(__cdecl *g_calloc)(size_t, size_t);
static void *(__cdecl *g_realloc)(void *, size_t);
static LPVOID(WINAPI *g_heap_alloc)(HANDLE, DWORD, SIZE_T);
static LPVOID(WINAPI *g_heap_realloc)(HANDLE, DWORD, LPVOID, SIZE_T);

static void counted(size_t n)
{
    note(WC_ALLOCS, 1);
    note(WC_ALLOC_BYTES, n);
}
static void *__cdecl c_malloc(size_t n) { counted(n); return g_malloc(n); }
static void *__cdecl c_calloc(size_t a, size_t b) { counted(a * b); return g_calloc(a, b); }
static void *__cdecl c_realloc(void *p, size_t n) { counted(n); return g_realloc(p, n); }
static LPVOID WINAPI c_heap_alloc(HANDLE h, DWORD f, SIZE_T n) { counted(n); return g_heap_alloc(h, f, n); }
static LPVOID WINAPI c_heap_realloc(HANDLE h, DWORD f, LPVOID p, SIZE_T n) { counted(n); return g_heap_realloc(h, f, p, n); }

static const struct { const char *name; void **real; void *counted; } g_heap[] = {
    {"malloc", (void **)&g_malloc, (void *)c_malloc},
    {"calloc", (void **)&g_calloc, (void *)c_calloc},
    {"realloc", (void **)&g_realloc, (void *)c_realloc},
    {"HeapAlloc", (void **)&g_heap_alloc, (void *)c_heap_alloc},
    {"HeapReAlloc", (void **)&g_heap_realloc, (void *)c_heap_realloc},
};

static void patch(void **slot, void *to)
{
    DWORD old;
    if (!VirtualProtect(slot, sizeof *slot, PAGE_READWRITE, &old)) return;
    *slot = to;
    VirtualProtect(slot, sizeof *slot, old, &old);
}

static void patch_imports(void)
{
    u8 *base = (u8 *)GetModuleHandleW(NULL);
    IMAGE_NT_HEADERS *nt = (IMAGE_NT_HEADERS *)(base + ((IMAGE_DOS_HEADER *)base)->e_lfanew);
    IMAGE_DATA_DIRECTORY *dir = &nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_IMPORT];
    int found = 0;
    for (IMAGE_IMPORT_DESCRIPTOR *d = (IMAGE_IMPORT_DESCRIPTOR *)(base + dir->VirtualAddress); d->Name; d++) {
        IMAGE_THUNK_DATA *name = (IMAGE_THUNK_DATA *)(base + d->OriginalFirstThunk);
        void **slot = (void **)(base + d->FirstThunk);
        if (!d->OriginalFirstThunk) continue;
        for (; name->u1.AddressOfData; name++, slot++) {
            if (IMAGE_SNAP_BY_ORDINAL(name->u1.Ordinal)) continue;
            const char *what = ((IMAGE_IMPORT_BY_NAME *)(base + name->u1.AddressOfData))->Name;
            for (size_t i = 0; i < sizeof g_heap / sizeof g_heap[0]; i++)
                if (!strcmp(what, g_heap[i].name)) {
                    *g_heap[i].real = *slot;
                    patch(slot, g_heap[i].counted);
                    found++;
                }
        }
    }
    if (!found) port_log("work counts: this exe imports no heap function: allocations are not counted\n");
}

int work_count_option(const char *arg, const char *value)
{
    if (!value) return 0;
    if (!strcmp(arg, "--work-from")) {
        g_from = (u32)strtoul(value, NULL, 10);
        return 2;
    }
    if (strcmp(arg, "--work-counts")) return 0;
    g_file = value;
    g_work_on = 1;
    g_thread = GetCurrentThreadId();
    patch_imports();
    return 2;
}

void work_count_tick(u32 blank)
{
    if (!g_work_on) return;
    g_thread = GetCurrentThreadId();
    g_last = blank;
    if (blank > g_from) {
        for (int i = 0; i < WC_N; i++) {
            g_total[i] += g_work_now[i];
            if (g_work_now[i] > g_peak[i]) g_peak[i] = g_work_now[i];
        }
        g_blanks++;
    }
    memset(g_work_now, 0, sizeof g_work_now);
}

static void rows(FILE *f, const char *title, const u64 *v)
{
    fprintf(f, "  \"%s\": {", title);
    for (int i = 0; i < WC_N; i++) fprintf(f, "%s\n    \"%s\": %llu", i ? "," : "", g_names[i], (unsigned long long)v[i]);
    fprintf(f, "\n  }");
}

void work_count_report(void)
{
    FILE *f;
    if (!g_work_on || fopen_s(&f, g_file, "w") || !f) return;
    g_work_on = 0;
    fprintf(f, "{\n  \"fixed_clock\": %d,\n  \"from\": %u,\n  \"last\": %u,\n  \"blanks\": %u,\n", host_fixed_clock() ? 1 : 0, g_from, g_last, g_blanks);
    rows(f, "counts", g_total);
    fputs(",\n", f);
    rows(f, "peak", g_peak);
    fputs("\n}\n", f);
    fclose(f);
}
