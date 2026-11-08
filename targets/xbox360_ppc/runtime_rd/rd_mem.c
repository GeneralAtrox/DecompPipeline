/* Xbox 360 readable-C runtime: the guest's four memory regions mapped at their own addresses (tc_rt_internal.h: the
 * test heap 0x40000000, the stack 0x70000000, the processor block 0x7fff0000, the image 0x82000000), code-page guards,
 * and the write report. Memory holds the guest's bytes in the guest's (big-endian) order: readable code reads it
 * through rd_be.h, raw C through rd32 and the others. The runner is a 64-bit program linked at readable_image_base
 * (core/readable_check.link_flags), below 4 GB and clear of every region.
 *
 * Writes are found per page (GetWriteWatch) and compared with the region as it was just before the run (its pristine
 * bytes plus the vector's patches), so a write that stores the value already there is invisible. The judge compares
 * final memory contents, so this does not change any verdict. As in the raw runtime (tc_rt.c), writes to the stack
 * region are not reported. Only the pages a vector wrote or patched are restored after it.
 */
#include <stdlib.h>
#include <string.h>
#include <windows.h>
#include <xmmintrin.h>
#include "rd_be.h"
#include "rd_internal.h"

#define NREGIONS 4
#define MAX_FAULTS 4
#define MAX_POKES 4096

typedef struct {
    u32 lo, size;
    int compared; /* writes reported (every region but the stack) */
    u8 *pristine; /* before any vector: the image's bytes, else zero */
    u8 *snapshot; /* just before the run: pristine plus this vector's patches (rd_arm) */
    void **pages; /* the pages GetWriteWatch found written */
} Region;

static Region g_reg[NREGIONS]; /* in address order: heap, stack, kernel, image */
#define IMAGE (&g_reg[3])
static u8 *g_code;     /* 1 bit per image byte that belongs to an instruction */
static u8 *g_codepage; /* 1 per image page holding an instruction byte */
static int g_armed;
static char g_faults[MAX_FAULTS][64];
static u32 g_nfaults;
static struct {
    u32 addr, len;
} g_plist[MAX_POKES];
static u32 g_pcount;
static int g_poverflow;

static Region *region_of(u64 addr, u64 size)
{
    for (int i = 0; i < NREGIONS; i++) {
        Region *r = &g_reg[i];
        if (addr >= r->lo && addr - r->lo < r->size && r->size - (addr - r->lo) >= size) return r;
    }
    return NULL;
}

static void protect(u64 addr, u64 size, DWORD prot)
{
    DWORD old;
    VirtualProtect((void *)(uintptr_t)addr, (SIZE_T)size, prot, &old);
}

int rd_is_code(u32 addr)
{
    u32 off = addr - IMAGE->lo;
    return addr >= IMAGE->lo && off < IMAGE->size && ((g_code[off >> 3] >> (off & 7)) & 1);
}

int rd_page_guarded(u64 addr)
{
    return g_armed && addr >= IMAGE->lo && addr - IMAGE->lo < IMAGE->size && g_codepage[(addr - IMAGE->lo) / RD_PAGE];
}

void rd_page_open(u64 addr) { protect(addr & ~(u64)(RD_PAGE - 1), RD_PAGE, PAGE_READWRITE); }
void rd_page_close(u64 addr) { protect(addr & ~(u64)(RD_PAGE - 1), RD_PAGE, PAGE_NOACCESS); }

static FILE *open_file(const char *path, const char *mode)
{
    FILE *f = NULL;
    return fopen_s(&f, path, mode) == 0 ? f : NULL;
}

static int region_init(Region *r, u32 lo, u32 size, int compared)
{
    r->lo = lo;
    r->size = size;
    r->compared = compared;
    void *p = VirtualAlloc((void *)(uintptr_t)lo, size, MEM_RESERVE | MEM_COMMIT | MEM_WRITE_WATCH, PAGE_READWRITE);
    if (p != (void *)(uintptr_t)lo) {
        fprintf(stderr, "runner: cannot map guest memory %08x..%08x at its own address\n", lo, lo + size);
        return 0;
    }
    r->pristine = (u8 *)calloc(size, 1);
    r->snapshot = (u8 *)calloc(size, 1);
    r->pages = (void **)malloc(sizeof(void *) * (size / RD_PAGE + 1));
    return r->pristine && r->snapshot && r->pages;
}

/* The image file (tools/xex.py's image.bin) at 0x82000000, the other regions zero, exactly as tc_rt.c loads them. */
int tcrt_init(const char *image, const char *code_ranges)
{
    FILE *f = open_file(image, "rb");
    if (!f) return 0;
    fseek(f, 0, SEEK_END);
    long size = ftell(f);
    fseek(f, 0, SEEK_SET);
    u8 *img = size > 0 && (u32)size <= TCRT_IMAGE_MAX ? (u8 *)malloc((size_t)size) : NULL;
    int ok = img && fread(img, 1, (size_t)size, f) == (size_t)size;
    fclose(f);
    if (!ok) return 0;
    u32 isize = ((u32)size + 0xffffu) & ~0xffffu;
    if (!region_init(&g_reg[0], TCRT_HEAP_BASE, TCRT_HEAP_SIZE, 1)
        || !region_init(&g_reg[1], TCRT_STACK_BASE, TCRT_STACK_SIZE, 0)
        || !region_init(&g_reg[2], TCRT_KERNEL_BASE, TCRT_KERNEL_SIZE, 1)
        || !region_init(&g_reg[3], TCRT_IMAGE_BASE, isize, 1))
        return 0;
    memcpy(IMAGE->pristine, img, (size_t)size);
    memcpy(IMAGE->snapshot, img, (size_t)size);
    memcpy((void *)(uintptr_t)IMAGE->lo, img, (size_t)size);
    free(img);
    g_code = (u8 *)calloc(isize / 8 + 1, 1);
    g_codepage = (u8 *)calloc(isize / RD_PAGE + 1, 1);
    if (!g_code || !g_codepage) return 0;
    f = open_file(code_ranges, "r");
    if (!f) return 0;
    unsigned lo, hi;
    while (fscanf_s(f, "%x %x", &lo, &hi) == 2) {
        for (u32 a = lo; a < hi && a >= IMAGE->lo && a - IMAGE->lo < isize; a++) {
            u32 off = a - IMAGE->lo;
            g_code[off >> 3] |= (u8)(1u << (off & 7));
            g_codepage[off / RD_PAGE] = 1;
        }
    }
    fclose(f);
    if (!rd_veh_install()) return 0;
    for (int i = 0; i < NREGIONS; i++) ResetWriteWatch((void *)(uintptr_t)g_reg[i].lo, g_reg[i].size);
    tcrt_reset();
    return 1;
}

static void disarm(void)
{
    if (g_armed) protect(IMAGE->lo, IMAGE->size, PAGE_READWRITE);
    g_armed = 0;
}

/* The pages of [addr, addr + len) one at a time: fn(region, page offset). */
static void each_page(u32 addr, u32 len, void (*fn)(Region *, u32))
{
    Region *r = region_of(addr, len ? len : 1);
    if (!r || !len) return;
    for (u32 p = (addr - r->lo) & ~(RD_PAGE - 1); p < addr - r->lo + len; p += RD_PAGE) fn(r, p);
}

static void restore_page(Region *r, u32 p)
{
    memcpy((u8 *)(uintptr_t)r->lo + p, r->pristine + p, RD_PAGE);
    memcpy(r->snapshot + p, r->pristine + p, RD_PAGE);
}

static void snapshot_page(Region *r, u32 p) { memcpy(r->snapshot + p, (u8 *)(uintptr_t)r->lo + p, RD_PAGE); }

/* The pages of r written since the write watch was last reset (reset: and reset it), or -1. */
static long long written(Region *r, DWORD flags)
{
    DWORD gran = 0;
    ULONG_PTR n = r->size / RD_PAGE + 1;
    if (GetWriteWatch(flags, (void *)(uintptr_t)r->lo, r->size, r->pages, &n, &gran) != 0) return -1;
    return (long long)n;
}

void tcrt_reset(void)
{
    disarm();
    for (int i = 0; i < NREGIONS; i++) { /* every page the last run wrote, then the patched ones */
        Region *r = &g_reg[i];
        long long n = written(r, WRITE_WATCH_FLAG_RESET);
        if (n < 0 || g_poverflow) {
            memcpy((void *)(uintptr_t)r->lo, r->pristine, r->size);
            memcpy(r->snapshot, r->pristine, r->size);
            continue;
        }
        for (long long k = 0; k < n; k++) restore_page(r, (u32)((uintptr_t)r->pages[k] - r->lo));
    }
    for (u32 k = 0; k < g_pcount && !g_poverflow; k++) each_page(g_plist[k].addr, g_plist[k].len, restore_page);
    g_pcount = 0;
    g_poverflow = 0;
    rd_calls_reset();
    g_nfaults = 0;
    _mm_setcsr(0x1f80u); /* round to nearest, no flush to zero: the raw runner's MXCSR */
}

void tcrt_poke(u32 addr, const u8 *data, u32 len)
{
    if (!len || !region_of(addr, len)) return;
    memcpy((void *)(uintptr_t)addr, data, len);
    if (g_pcount < MAX_POKES) {
        g_plist[g_pcount].addr = addr;
        g_plist[g_pcount].len = len;
        g_pcount++;
    } else {
        g_poverflow = 1;
    }
}

/* Just before the run: remember the patched pages, start the write watch, then make every page holding machine code
 * fault on access. */
void rd_arm(void)
{
    for (int i = 0; i < NREGIONS && g_poverflow; i++)
        memcpy(g_reg[i].snapshot, (void *)(uintptr_t)g_reg[i].lo, g_reg[i].size);
    for (u32 k = 0; k < g_pcount && !g_poverflow; k++) each_page(g_plist[k].addr, g_plist[k].len, snapshot_page);
    for (int i = 0; i < NREGIONS; i++) ResetWriteWatch((void *)(uintptr_t)g_reg[i].lo, g_reg[i].size);
    u32 npages = IMAGE->size / RD_PAGE;
    for (u32 p = 0; p < npages;) {
        u32 q = p;
        while (q < npages && g_codepage[q]) q++;
        if (q > p) protect(IMAGE->lo + (u64)p * RD_PAGE, (u64)(q - p) * RD_PAGE, PAGE_NOACCESS);
        p = q + 1;
    }
    g_armed = 1;
}

/* The stricter judge's guest access (tc_strict.h through rd_veh.c): [p, p + n) wholly in one region (TcBase.mapped),
 * through a code page's guard (opened for the copy, closed again). A write (in) only where no byte is an
 * instruction's (TcStrict.writable): the write watch then finds it as it finds the C's own. 0: not mapped, or not
 * written. */
int rd_guest_copy(u32 p, u32 n, u8 *out, const u8 *in)
{
    if (!n || !region_of(p, n)) return 0;
    for (u32 k = 0; in && k < n; k++)
        if (rd_is_code(p + k)) return 0;
    u64 first = p & ~(u64)(RD_PAGE - 1), last = ((u64)p + n - 1) & ~(u64)(RD_PAGE - 1);
    int g0 = rd_page_guarded(first), g1 = last != first && rd_page_guarded(last);
    if (g0) rd_page_open(first);
    if (g1) rd_page_open(last);
    if (in) memcpy((void *)(uintptr_t)p, in, n);
    else memcpy(out, (const void *)(uintptr_t)p, n);
    if (g0) rd_page_close(first);
    if (g1) rd_page_close(last);
    return 1;
}

static u32 be32(const u8 *p) { return (u32)p[0] << 24 | (u32)p[1] << 16 | (u32)p[2] << 8 | p[3]; }

/* The writes of one written page of a compared region, as W lines (bytes that differ from the snapshot). A stored host
 * address of the function under test is first rewritten to its guest address (rd_set_self). */
static void report_page(FILE *out, Region *r, u32 p)
{
    u8 *mem = (u8 *)(uintptr_t)r->lo + p, *snap = r->snapshot + p;
    for (u32 o = 0; o + 4 <= RD_PAGE; o += 4) {
        u32 w = be32(mem + o);
        u64 g = rd_self_guest(w);
        if (w != be32(snap + o) && g != w) *(u32 *)(mem + o) = _byteswap_ulong((u32)g);
    }
    for (u32 o = 0; o < RD_PAGE;) {
        if (mem[o] == snap[o]) {
            o++;
            continue;
        }
        u32 end = o + 1;
        while (end < RD_PAGE && mem[end] != snap[end]) end++;
        fprintf(out, "W %08x ", r->lo + p + o);
        for (u32 k = o; k < end; k++) fprintf(out, "%02x", mem[k]);
        fputc('\n', out);
        o = end;
    }
}

void tcrt_report(FILE *out, u64 r3, u64 f1_bits)
{
    disarm();
    fprintf(out, "RET %016llx %016llx\n", (unsigned long long)rd_self_guest(r3), (unsigned long long)f1_bits);
    for (int i = 0; i < NREGIONS; i++) {
        Region *r = &g_reg[i];
        long long n = written(r, 0);
        if (n < 0) fprintf(out, "FAULT write-watch %08x\n", r->lo);
        for (long long k = 0; k < n && r->compared; k++) report_page(out, r, (u32)((uintptr_t)r->pages[k] - r->lo));
    }
    rd_calls_report(out);
    tcrt_strict_report(out); /* CS lines; XR: the exit frame (tc_strict.h), as tc_rt.c reports it */
    if (tcrt_strict_flags() & 8) { /* r1; r14..r31 and f14..f31: their entry values, 0 (the C keeps none of them) */
        fprintf(out, "XR %08x", TCRT_SP);
        for (int k = 0; k < 36; k++) fprintf(out, " %016llx", 0ull);
        fputc('\n', out);
    }
    for (u32 k = 0; k < g_nfaults && k < MAX_FAULTS; k++) fprintf(out, "FAULT %s\n", g_faults[k]);
    if (rd_code_read()) fprintf(out, "CODEREAD %08x\n", rd_code_read());
    ULONG_PTR slo = 0, shi = 0; /* the host stack: a readable local passed by address (stack-pointer pairing) */
    GetCurrentThreadStackLimits(&slo, &shi);
    fprintf(out, "HOSTSTACK %016llx %016llx\n", (unsigned long long)slo, (unsigned long long)shi);
}

/* The raw guest-memory API (tc_rt.h), for the generated thunk (stack parameters) and for running raw matched C: the
 * four regions only, as tc_rt.c (any other address is a FAULT line and reads 0); a read of an instruction byte faults
 * on its guarded page and is reported as CODEREAD (rd_veh.c). Big endian. */
static int check(u32 a, u32 size, int write)
{
    if (region_of(a, size)) return 1;
    if (g_nfaults < MAX_FAULTS) snprintf(g_faults[g_nfaults], sizeof g_faults[0], "%s %08x", write ? "write" : "read", a);
    g_nfaults++;
    return 0;
}

#define AT(T, a) (*(volatile T *)(uintptr_t)(a))
u8 rd8(u32 a) { return check(a, 1, 0) ? AT(u8, a) : 0; }
u16 rd16(u32 a) { return check(a, 2, 0) ? _byteswap_ushort(AT(u16, a)) : 0; }
u32 rd32(u32 a) { return check(a, 4, 0) ? _byteswap_ulong(AT(u32, a)) : 0; }
u64 rd64(u32 a) { return check(a, 8, 0) ? _byteswap_uint64(AT(u64, a)) : 0; }
u16 rd16r(u32 a) { return check(a, 2, 0) ? AT(u16, a) : 0; }
u32 rd32r(u32 a) { return check(a, 4, 0) ? AT(u32, a) : 0; }

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
    if (check(a, 1, 1)) AT(u8, a) = v;
}
void wr16(u32 a, u16 v)
{
    if (check(a, 2, 1)) AT(u16, a) = _byteswap_ushort(v);
}
void wr32(u32 a, u32 v)
{
    if (check(a, 4, 1)) AT(u32, a) = _byteswap_ulong(v);
}
void wr64(u32 a, u64 v)
{
    if (check(a, 8, 1)) AT(u64, a) = _byteswap_uint64(v);
}
void wr16r(u32 a, u16 v)
{
    if (check(a, 2, 1)) AT(u16, a) = v;
}
void wr32r(u32 a, u32 v)
{
    if (check(a, 4, 1)) AT(u32, a) = v;
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
