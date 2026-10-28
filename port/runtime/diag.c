/* Bring-up diagnostics (tools/bu_sweep.py), off unless a switch names a file; never in a play build's command line.
 *
 *   --diag-skip FILE    each line "RVA ADDR KIND NAME [FROM]" (hex RVA in port.exe, the guest address, i or f, the
 *                       name, a vertical blank): from vertical blank FROM on (default 0, the start), the host function
 *                       at RVA is a logging stub that returns 0 (KIND f: 0.0 on the x87 stack too), as gen_port.py's
 *                       stubs are, so a sweep steps past a blocker without a rebuild and the run up to FROM is
 *                       unchanged (a --fixed-clock run repeats).
 *   --diag-cover FILE   each line "RVA ADDR NAME": a one-shot int3 at each function start logs "COVER ADDR NAME at
 *                       vblank V" the first time it runs, then puts the byte back (one exception per function).
 *
 * The RVAs come from the build's port.map. A fault then logs a deeper chain of host return addresses as well. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <windows.h>
#include "port.h"
#include "platform.h"

#define THUNK 32u

typedef struct {
    u32 rva, addr, calls, from;
    u8 orig, patched;
    char name[64];
} DiagFn;

static const char *g_skip_path, *g_cover_path;
static DiagFn *g_skip, *g_cover;
static u32 g_nskip, g_ncover, g_covered;
static uintptr_t g_base, g_text_lo, g_text_hi;

int diag_option(const char *arg, const char *value)
{
    if (!value) return 0;
    if (!strcmp(arg, "--diag-skip")) g_skip_path = value;
    else if (!strcmp(arg, "--diag-cover")) g_cover_path = value;
    else return 0;
    return 2;
}

int diag_on(void) { return g_skip_path || g_cover_path; }

/* The rows of FILE (kind != NULL: four fields, else three), in an array of *n. */
static DiagFn *load(const char *path, u32 *n, char *kinds)
{
    FILE *f = NULL;
    char line[256], name[64], kind[4];
    u32 cap = 256, rva, addr, from = 0;
    DiagFn *t = malloc(cap * sizeof *t);
    *n = 0;
    if (!t || fopen_s(&f, path, "r") != 0 || !f) {
        port_log("diag: cannot read %s\n", path);
        free(t);
        return NULL;
    }
    while (fgets(line, sizeof line, f)) {
        int ok = kinds ? sscanf_s(line, "%x %x %3s %63s %u", &rva, &addr, kind, 4, name, 64, &from) >= 4
                       : sscanf_s(line, "%x %x %63s", &rva, &addr, name, 64) == 3;
        if (!ok || line[0] == '#') continue;
        if (rva < g_text_lo - g_base || rva >= g_text_hi - g_base) {
            port_log("diag: %s %08x outside the code section; left alone\n", name, rva);
            continue;
        }
        if (*n == cap) {
            DiagFn *m = realloc(t, (cap *= 2) * sizeof *t);
            if (!m) break;
            t = m;
        }
        memset(&t[*n], 0, sizeof t[*n]);
        t[*n].rva = rva;
        t[*n].addr = addr;
        t[*n].from = kinds ? from : 0;
        from = 0;
        strncpy_s(t[*n].name, sizeof t[*n].name, name, _TRUNCATE);
        if (kinds) kinds[*n < 4096u ? *n : 4095u] = kind[0];
        (*n)++;
    }
    fclose(f);
    return t;
}

static void __cdecl skip_hit(u32 i)
{
    DiagFn *d = &g_skip[i];
    if (!(++d->calls & (d->calls - 1u))) /* the first call, then every power of two */
        port_log("DIAG SKIP %08x %s call %u at vblank %u\n", d->addr, d->name, d->calls, port_vblank_count());
}

static u8 *g_thunks;

/* Skip i's function start jumps to its thunk from now on. */
static void patch(u32 i)
{
    DWORD old;
    u8 *at = (u8 *)(g_base + g_skip[i].rva);
    s32 rel = (s32)((uintptr_t)(g_thunks + i * THUNK) - (uintptr_t)(at + 5));
    if (!VirtualProtect(at, 5, PAGE_EXECUTE_READWRITE, &old)) port_exit("diag: cannot patch a function", 1);
    at[0] = 0xe9;
    memcpy(at + 1, &rel, 4);
    VirtualProtect(at, 5, old, &old);
    FlushInstructionCache(GetCurrentProcess(), at, 5);
    g_skip[i].patched = 1;
    port_log("diag: skip %08x %s (port.exe+%08x) from vblank %u\n", g_skip[i].addr, g_skip[i].name, g_skip[i].rva,
             port_vblank_count());
}

static void skip_task(void *arg)
{
    (void)arg;
    for (u32 i = 0; i < g_nskip; i++)
        if (!g_skip[i].patched && port_vblank_count() >= g_skip[i].from) patch(i);
}

/* Each skipped function's start jumps to a thunk: push i; call skip_hit; add esp, 4; xor eax, eax; xor edx, edx;
 * (fldz;) ret. The caller pops its own arguments (cdecl), as for any stub. A skip from a later vertical blank is
 * patched in by a vertical blank task, on the game's thread. */
static void install_skips(void)
{
    static char kinds[4096];
    int later = 0;
    g_skip = load(g_skip_path, &g_nskip, kinds);
    if (!g_skip || !g_nskip) return;
    u8 *code = g_thunks = VirtualAlloc(NULL, g_nskip * THUNK, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
    if (!code) port_exit("diag: no memory for the skip thunks", 1);
    for (u32 i = 0; i < g_nskip; i++) {
        u8 *t = code + i * THUNK;
        s32 rel;
        t[0] = 0x68;
        memcpy(t + 1, &i, 4);
        t[5] = 0xe8;
        rel = (s32)((uintptr_t)skip_hit - (uintptr_t)(t + 10));
        memcpy(t + 6, &rel, 4);
        memcpy(t + 10, "\x83\xc4\x04\x31\xc0\x31\xd2", 7);
        if (kinds[i < 4096u ? i : 4095u] == 'f') memcpy(t + 17, "\xd9\xee\xc3", 3);
        else t[17] = 0xc3;
        if (g_skip[i].from) later = 1;
        else patch(i);
    }
    if (later) port_add_vblank_task(skip_task, NULL);
}

static int by_rva(const void *a, const void *b)
{
    u32 x = ((const DiagFn *)a)->rva, y = ((const DiagFn *)b)->rva;
    return x < y ? -1 : x > y;
}

static LONG CALLBACK cover_hit(EXCEPTION_POINTERS *ep)
{
    uintptr_t at = (uintptr_t)ep->ExceptionRecord->ExceptionAddress;
    if (ep->ExceptionRecord->ExceptionCode != EXCEPTION_BREAKPOINT || at < g_text_lo || at >= g_text_hi)
        return EXCEPTION_CONTINUE_SEARCH;
    DiagFn key, *d;
    key.rva = (u32)(at - g_base);
    d = bsearch(&key, g_cover, g_ncover, sizeof *g_cover, by_rva);
    if (!d) return EXCEPTION_CONTINUE_SEARCH;
    if (*(u8 *)at == 0xcc) { /* a second thread may have put it back already */
        *(u8 *)at = d->orig;
        FlushInstructionCache(GetCurrentProcess(), (void *)at, 1);
        g_covered++;
        port_log("COVER %08x %s at vblank %u\n", d->addr, d->name, port_vblank_count());
    }
    ep->ContextRecord->Eip = (DWORD)at;
    return EXCEPTION_CONTINUE_EXECUTION;
}

/* The code section stays writable while coverage is on: each first call writes its byte back. */
static void install_cover(void)
{
    DWORD old;
    u32 kept = 0;
    g_cover = load(g_cover_path, &g_ncover, NULL);
    if (!g_cover || !g_ncover) return;
    qsort(g_cover, g_ncover, sizeof *g_cover, by_rva);
    for (u32 i = 0; i < g_ncover; i++) /* one breakpoint per body (folded bodies keep their first name) */
        if (!kept || g_cover[kept - 1].rva != g_cover[i].rva) g_cover[kept++] = g_cover[i];
    g_ncover = kept;
    for (u32 i = 0; i < g_nskip; i++) /* a skipped start is a jump now (a later skip writes over its int3) */
        for (u32 j = 0; j < g_ncover; j++)
            if (!g_skip[i].from && g_cover[j].rva + 5u > g_skip[i].rva && g_cover[j].rva < g_skip[i].rva + 5u) g_cover[j].orig = 1;
    if (!VirtualProtect((void *)g_text_lo, g_text_hi - g_text_lo, PAGE_EXECUTE_READWRITE, &old))
        port_exit("diag: cannot make the code section writable", 1);
    if (!AddVectoredExceptionHandler(1, cover_hit)) port_exit("diag: cannot install the coverage handler", 1);
    for (u32 i = 0; i < g_ncover; i++) {
        u8 *at = (u8 *)(g_base + g_cover[i].rva);
        if (g_cover[i].orig) continue; /* skipped: never covered */
        g_cover[i].orig = *at;
        *at = 0xcc;
    }
    FlushInstructionCache(GetCurrentProcess(), NULL, 0);
    port_log("diag: coverage breakpoints at %u function starts\n", g_ncover);
}

void diag_install(void)
{
    HMODULE m = GetModuleHandleW(NULL);
    const IMAGE_NT_HEADERS *nt = (const IMAGE_NT_HEADERS *)((const u8 *)m + ((const IMAGE_DOS_HEADER *)m)->e_lfanew);
    const IMAGE_SECTION_HEADER *s = IMAGE_FIRST_SECTION(nt);
    if (!diag_on()) return;
    g_base = (uintptr_t)m;
    for (int i = 0; i < nt->FileHeader.NumberOfSections; i++, s++)
        if (s->Characteristics & IMAGE_SCN_CNT_CODE) {
            g_text_lo = g_base + s->VirtualAddress;
            g_text_hi = g_text_lo + s->Misc.VirtualSize;
            break;
        }
    if (g_skip_path) install_skips();
    if (g_cover_path) install_cover();
}

/* At a fault: every word on the stack (up to its top) that points into port.exe's code, the innermost first. */
void diag_fault_chain(u32 esp)
{
    MEMORY_BASIC_INFORMATION mi;
    const u32 *sp = (const u32 *)(uintptr_t)esp, *end = sp + 0x8000;
    if (!diag_on() || !VirtualQuery(sp, &mi, sizeof mi)) return;
    port_log("diag: fault at vblank %u\n", port_vblank_count());
    if ((const u8 *)mi.BaseAddress + mi.RegionSize < (const u8 *)end)
        end = (const u32 *)((const u8 *)mi.BaseAddress + mi.RegionSize);
    for (int n = 0; sp < end && n < 64; sp++)
        if (*sp >= g_text_lo && *sp < g_text_hi) {
            port_log("  chain: port.exe+%08x\n", (u32)(*sp - g_base));
            n++;
        }
    port_log("diag: %u functions covered\n", g_covered);
}
