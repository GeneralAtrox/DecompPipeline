/* GameCube readable-C runtime: MEM1 mapped at its GameCube address, code-page guards, and the write report.
 * One shared section is mapped twice: at 0x80000000 and at its uncached view 0xc0000000 (RD_UNCACHED_BASE), the
 * same bytes, as on the GameCube; page protections are always changed on both views. Memory holds the guest's bytes
 * in the guest's (big-endian) order: readable code reads it through rd_be.h, raw C through rd32 and the others.
 *
 * Writes are found by comparing MEM1 with a snapshot taken just before the run, so a write that stores the value
 * already there is invisible. The judge compares final memory contents, so this does not change any verdict. As in
 * the raw runtime (tc_rt.c), writes at and above TCRT_STACK_LO (the stack window) are not reported.
 */
#include <float.h>
#include <stdlib.h>
#include <string.h>
#include <windows.h>
#include <xmmintrin.h>
#include "rd_be.h"
#include "rd_internal.h"

#define NPAGES (TCRT_RAM_SIZE / RD_PAGE)
#define RAM ((u8 *)(uintptr_t)TCRT_RAM_BASE)
#define MAX_FAULTS 4

enum { PAGE_PLAIN, PAGE_CODE };

static u8 *g_pristine;
static u8 *g_snapshot;
static u8 *g_code; /* 1 bit per guest byte that belongs to an instruction */
static u8 g_page[NPAGES];
static u8 g_dirty[NPAGES]; /* tcrt_report: the page differs from the snapshot */
static int g_armed;
static char g_faults[MAX_FAULTS][64];
static u32 g_nfaults;

static void protect(u32 addr, u32 size, DWORD prot)
{
    DWORD old;
    VirtualProtect((void *)(uintptr_t)addr, size, prot, &old);
    VirtualProtect((void *)(uintptr_t)(addr - TCRT_RAM_BASE + RD_UNCACHED_BASE), size, prot, &old);
}

int rd_is_code(u32 addr)
{
    addr = tcrt_fold(addr);
    u32 off = addr - TCRT_RAM_BASE;
    return off < TCRT_RAM_SIZE && ((g_code[off >> 3] >> (off & 7)) & 1);
}

int rd_page_guarded(u32 addr)
{
    addr = tcrt_fold(addr);
    u32 off = addr - TCRT_RAM_BASE;
    return g_armed && off < TCRT_RAM_SIZE && g_page[off / RD_PAGE] == PAGE_CODE;
}

void rd_page_open(u32 addr) { protect(tcrt_fold(addr) & ~(RD_PAGE - 1), RD_PAGE, PAGE_READWRITE); }
void rd_page_close(u32 addr) { protect(tcrt_fold(addr) & ~(RD_PAGE - 1), RD_PAGE, PAGE_NOACCESS); }

static FILE *open_file(const char *path, const char *mode)
{
    FILE *f = NULL;
    return fopen_s(&f, path, mode) == 0 ? f : NULL;
}

static u32 be32(const u8 *p) { return (u32)p[0] << 24 | (u32)p[1] << 16 | (u32)p[2] << 8 | p[3]; }

/* The DOL's 7 text and 11 data sections, copied to their load addresses (the rest of MEM1, bss included, is 0),
 * exactly as tc_rt.c loads them. */
static int load_dol(const char *path)
{
    FILE *f = open_file(path, "rb");
    if (!f) return 0;
    fseek(f, 0, SEEK_END);
    long size = ftell(f);
    fseek(f, 0, SEEK_SET);
    u8 *dol = (u8 *)malloc((size_t)size + 1);
    int ok = dol && size >= 0x100 && fread(dol, 1, (size_t)size, f) == (size_t)size;
    fclose(f);
    for (int i = 0; ok && i < 18; i++) {
        u32 off = be32(dol + 4 * i), addr = be32(dol + 0x48 + 4 * i), len = be32(dol + 0x90 + 4 * i);
        if (!len) continue;
        if ((long)off + (long)len > size || addr < TCRT_RAM_BASE || addr - TCRT_RAM_BASE + len > TCRT_RAM_SIZE) ok = 0;
        else memcpy(g_pristine + (addr - TCRT_RAM_BASE), dol + off, len);
    }
    free(dol);
    return ok;
}

int tcrt_init(const char *dol, const char *code_ranges)
{
    HANDLE sec = CreateFileMappingW(INVALID_HANDLE_VALUE, NULL, PAGE_READWRITE, 0, TCRT_RAM_SIZE, NULL);
    void *uncached = (void *)(uintptr_t)RD_UNCACHED_BASE;
    if (!sec || MapViewOfFileEx(sec, FILE_MAP_ALL_ACCESS, 0, 0, TCRT_RAM_SIZE, RAM) != RAM
        || MapViewOfFileEx(sec, FILE_MAP_ALL_ACCESS, 0, 0, TCRT_RAM_SIZE, uncached) != uncached) {
        fprintf(stderr, "runner: cannot map MEM1 at %08x and %08x\n", TCRT_RAM_BASE, RD_UNCACHED_BASE);
        return 0;
    }
    /* Just past each view: any access faults. */
    VirtualAlloc((void *)(uintptr_t)(TCRT_RAM_BASE + TCRT_RAM_SIZE), 0x10000, MEM_RESERVE, PAGE_NOACCESS);
    VirtualAlloc((void *)(uintptr_t)(RD_UNCACHED_BASE + TCRT_RAM_SIZE), 0x10000, MEM_RESERVE, PAGE_NOACCESS);
    g_pristine = (u8 *)calloc(TCRT_RAM_SIZE, 1);
    g_snapshot = (u8 *)calloc(TCRT_RAM_SIZE, 1);
    g_code = (u8 *)calloc(TCRT_RAM_SIZE / 8, 1);
    if (!g_pristine || !g_snapshot || !g_code || !load_dol(dol)) return 0;
    FILE *f = open_file(code_ranges, "r");
    if (!f) return 0;
    unsigned lo, hi;
    while (fscanf_s(f, "%x %x", &lo, &hi) == 2) {
        for (u32 a = lo; a < hi && a >= TCRT_RAM_BASE && a < TCRT_RAM_BASE + TCRT_RAM_SIZE; a++) {
            u32 off = a - TCRT_RAM_BASE;
            g_code[off >> 3] |= (u8)(1u << (off & 7));
            g_page[off / RD_PAGE] = PAGE_CODE;
        }
    }
    fclose(f);
    if (!rd_veh_install()) return 0;
    tcrt_reset();
    return 1;
}

static void disarm(void)
{
    if (g_armed) protect(TCRT_RAM_BASE, TCRT_RAM_SIZE, PAGE_READWRITE);
    g_armed = 0;
}

void tcrt_reset(void)
{
    disarm();
    memcpy(RAM, g_pristine, TCRT_RAM_SIZE);
    rd_calls_reset();
    g_nfaults = 0;
    /* The PowerPC's default FPSCR: round to nearest, denormals kept (NI = 0), as the 64-bit raw runner's MXCSR. */
    _mm_setcsr(0x1f80u);
    unsigned int x87 = 0;
    __control87_2(_RC_NEAR | _PC_53, _MCW_RC | _MCW_PC, &x87, NULL); /* x87 moves and integer conversions only */
}

void tcrt_poke(u32 addr, const u8 *data, u32 len)
{
    addr = tcrt_fold(addr);
    if (addr < TCRT_RAM_BASE || addr - TCRT_RAM_BASE + len > TCRT_RAM_SIZE) return;
    memcpy(RAM + (addr - TCRT_RAM_BASE), data, len);
}

/* Just before the run: remember memory, then make every page holding machine code fault on access. */
void rd_arm(void)
{
    memcpy(g_snapshot, RAM, TCRT_RAM_SIZE);
    for (u32 p = 0; p < NPAGES;) {
        u32 q = p;
        while (q < NPAGES && g_page[q] == PAGE_CODE) q++;
        if (q > p) protect(TCRT_RAM_BASE + p * RD_PAGE, (q - p) * RD_PAGE, PAGE_NOACCESS);
        p = q + 1;
    }
    g_armed = 1;
}

void tcrt_report(FILE *out, u32 r3, u64 f1_bits, u32 r4)
{
    disarm();
    fprintf(out, "RET %08x %016llx %08x\n", rd_self_guest(r3), (unsigned long long)f1_bits, r4);
    u32 hi = TCRT_STACK_LO - TCRT_RAM_BASE;
    /* Only the pages that differ from the snapshot hold writes: each page is compared whole (memcmp) and the word and
     * byte walks below skip an equal one, the same report as walking all of MEM1 (~30 ms a vector over 24 MB). The
     * word rewrite changes only words that differ, so a page equal before it is still equal after. */
    for (u32 p = 0; p < hi; p += RD_PAGE) {
        u32 n = hi - p < RD_PAGE ? hi - p : RD_PAGE;
        g_dirty[p / RD_PAGE] = memcmp(RAM + p, g_snapshot + p, n) != 0;
    }
    for (u32 o = 0; o + 4 <= hi; o += 4) { /* a stored host address of the function under test: its guest one */
        if ((o & (RD_PAGE - 1)) == 0 && !g_dirty[o / RD_PAGE]) {
            o += RD_PAGE - 4;
            continue;
        }
        u32 w = _byteswap_ulong(*(u32 *)(RAM + o)), s = _byteswap_ulong(*(u32 *)(g_snapshot + o));
        if (w != s && rd_self_guest(w) != w) *(u32 *)(RAM + o) = _byteswap_ulong(rd_self_guest(w));
    }
    for (u32 o = 0; o < hi;) {
        if ((o & (RD_PAGE - 1)) == 0 && !g_dirty[o / RD_PAGE]) {
            o += RD_PAGE;
            continue;
        }
        if (RAM[o] == g_snapshot[o]) {
            o++;
            continue;
        }
        u32 end = o + 1;
        while (end < hi && RAM[end] != g_snapshot[end]) end++;
        fprintf(out, "W %08x ", o + TCRT_RAM_BASE);
        for (u32 k = o; k < end; k++) fprintf(out, "%02x", RAM[k]);
        fputc('\n', out);
        o = end;
    }
    rd_calls_report(out);
    for (u32 k = 0; k < g_nfaults && k < MAX_FAULTS; k++) fprintf(out, "FAULT %s\n", g_faults[k]);
    if (rd_code_read()) fprintf(out, "CODEREAD %08x\n", rd_code_read());
    ULONG_PTR slo = 0, shi = 0; /* the host stack: a readable local passed by address (stack-pointer pairing) */
    GetCurrentThreadStackLimits(&slo, &shi);
    fprintf(out, "HOSTSTACK %08x %08x\n", (u32)slo, (u32)shi);
}

/* The raw guest-memory API (tc_rt.h), for the generated thunk (stack parameters) and for running raw matched C:
 * MEM1 only, as tc_rt.c (any other address is a FAULT line and reads 0); a read of an instruction byte faults on
 * its guarded page and is reported as CODEREAD (rd_veh.c). Big endian. */
static int check(u32 *a, u32 size, int write)
{
    *a = tcrt_fold(*a);
    if (*a < TCRT_RAM_BASE || *a > TCRT_RAM_BASE + TCRT_RAM_SIZE - size) {
        if (g_nfaults < MAX_FAULTS)
            snprintf(g_faults[g_nfaults], sizeof g_faults[0], "%s %08x", write ? "write" : "read", *a);
        g_nfaults++;
        return 0;
    }
    return 1;
}

u8 rd8(u32 a) { return check(&a, 1, 0) ? *(volatile u8 *)(uintptr_t)a : 0; }
u16 rd16(u32 a) { return check(&a, 2, 0) ? _byteswap_ushort(*(volatile u16 *)(uintptr_t)a) : 0; }
u32 rd32(u32 a) { return check(&a, 4, 0) ? _byteswap_ulong(*(volatile u32 *)(uintptr_t)a) : 0; }
u64 rd64(u32 a) { return check(&a, 8, 0) ? _byteswap_uint64(*(volatile u64 *)(uintptr_t)a) : 0; }

f32 rdf32(u32 a)
{
    u32 u = rd32(a);
    f32 f;
    memcpy(&f, &u, 4);
    return f;
}

f64 rdf64(u32 a)
{
    u64 u = rd64(a);
    f64 d;
    memcpy(&d, &u, 8);
    return d;
}

void wr8(u32 a, u8 v)
{
    if (check(&a, 1, 1)) *(volatile u8 *)(uintptr_t)a = v;
}
void wr16(u32 a, u16 v)
{
    if (check(&a, 2, 1)) *(volatile u16 *)(uintptr_t)a = _byteswap_ushort(v);
}
void wr32(u32 a, u32 v)
{
    if (check(&a, 4, 1)) *(volatile u32 *)(uintptr_t)a = _byteswap_ulong(v);
}
void wr64(u32 a, u64 v)
{
    if (check(&a, 8, 1)) *(volatile u64 *)(uintptr_t)a = _byteswap_uint64(v);
}

void wrf32(u32 a, f32 v)
{
    u32 u;
    memcpy(&u, &v, 4);
    wr32(a, u);
}

void wrf32d(u32 a, f64 v) { wr32(a, ppc_single_bits(v)); }

void wrf64(u32 a, f64 v)
{
    u64 u;
    memcpy(&u, &v, 8);
    wr64(a, u);
}

void be_set_stfs(be_f32 *p, f64 v) { be_set_bits32(p, ppc_single_bits(v)); }
