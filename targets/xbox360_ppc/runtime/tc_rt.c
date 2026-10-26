/* DecompPipeline Xbox 360 runtime implementation: guest memory (big endian) in four regions, loaded like the
 * oracle's Ghidra program; the write set, the call log and the report (instruction helpers: tc_ppc.c). */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "tc_rt_internal.h"

#define MAX_WRITES (1u << 22)
#define MAX_CALLS 4096 /* TcOracle.CALL_LIMIT: the oracle rejects vectors making more calls */
#define MAX_FAULTS 4
#define NREGIONS 4
#define MAX_POKES 4096

typedef struct {
    u32 lo, size;
    int compared; /* writes reported (every region but the stack) */
    u8 *mem, *pristine, *written;
} Region;

typedef struct {
    u32 target;
    u64 r[8];
    u64 f[TCRT_CALL_FLOATS];
    u64 s[8];
} CallRecord;

static Region g_reg[NREGIONS];
static u8 *g_code; /* 1 bit per image byte that belongs to an instruction */
static u32 *g_wlist;
static u32 g_wcount;
static int g_woverflow;
static u64 g_rets[16];
static u64 g_frets[16];
static u32 g_ncalls;
static CallRecord *g_calls;
static char g_faults[MAX_FAULTS][64];
static u32 g_nfaults;
static u32 g_code_read;
static struct {
    u32 addr, len;
} g_plist[MAX_POKES];
static u32 g_pcount;
static int g_poverflow;

static Region *region_of(u32 addr)
{
    for (int i = 0; i < NREGIONS; i++) {
        if (addr - g_reg[i].lo < g_reg[i].size) return &g_reg[i];
    }
    return NULL;
}

static int bit_get(const u8 *bits, u32 off) { return (bits[off >> 3] >> (off & 7)) & 1; }
static void bit_set(u8 *bits, u32 off) { bits[off >> 3] |= (u8)(1u << (off & 7)); }

static void fault(const char *kind, u32 addr)
{
    if (g_nfaults < MAX_FAULTS) snprintf(g_faults[g_nfaults], sizeof g_faults[0], "%s %08x", kind, addr);
    g_nfaults++;
}

/* The region holding [addr, addr + size), or NULL (a fault). */
static Region *region(u32 addr, u32 size, int write)
{
    for (int i = 0; i < NREGIONS; i++) {
        Region *r = &g_reg[i];
        if (addr - r->lo < r->size && r->size - (addr - r->lo) >= size) {
            if (!write && r == &g_reg[0] && bit_get(g_code, addr - r->lo) && !g_code_read) g_code_read = addr;
            return r;
        }
    }
    fault(write ? "write" : "read", addr);
    return NULL;
}

static void mark(Region *r, u32 addr, u32 size)
{
    for (u32 i = 0; i < size; i++) {
        u32 off = addr + i - r->lo;
        if (bit_get(r->written, off)) continue;
        bit_set(r->written, off);
        if (g_wcount < MAX_WRITES) g_wlist[g_wcount++] = addr + i;
        else g_woverflow = 1;
    }
}

static u64 rd_be(u32 a, u32 size)
{
    Region *r = region(a, size, 0);
    if (!r) return 0;
    const u8 *p = r->mem + (a - r->lo);
    u64 v = 0;
    for (u32 i = 0; i < size; i++) v = (v << 8) | p[i];
    return v;
}

static void wr_be(u32 a, u64 v, u32 size)
{
    Region *r = region(a, size, 1);
    if (!r) return;
    u8 *p = r->mem + (a - r->lo);
    for (u32 i = 0; i < size; i++) p[i] = (u8)(v >> (8 * (size - 1 - i)));
    mark(r, a, size);
}

u8 rd8(u32 a) { return (u8)rd_be(a, 1); }
u16 rd16(u32 a) { return (u16)rd_be(a, 2); }
u32 rd32(u32 a) { return (u32)rd_be(a, 4); }
u64 rd64(u32 a) { return rd_be(a, 8); }

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

void wr8(u32 a, u8 v) { wr_be(a, v, 1); }
void wr16(u32 a, u16 v) { wr_be(a, v, 2); }
void wr32(u32 a, u32 v) { wr_be(a, v, 4); }
void wr64(u32 a, u64 v) { wr_be(a, v, 8); }

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

u16 rd16r(u32 a)
{
    u16 v = rd16(a);
    return (u16)((v >> 8) | (v << 8));
}

u32 rd32r(u32 a)
{
    u32 v = rd32(a);
    return (v >> 24) | ((v >> 8) & 0xff00u) | ((v << 8) & 0xff0000u) | (v << 24);
}

void wr16r(u32 a, u16 v) { wr16(a, (u16)((v >> 8) | (v << 8))); }

void wr32r(u32 a, u32 v) { wr32(a, (v >> 24) | ((v >> 8) & 0xff00u) | ((v << 8) & 0xff0000u) | (v << 24)); }

static void log_call(u32 target, const TcArgs *args)
{
    if (g_ncalls >= MAX_CALLS) return;
    CallRecord *c = &g_calls[g_ncalls];
    memset(c, 0, sizeof *c);
    c->target = target;
    if (!args) return;
    for (int i = 0; i < 8; i++) c->r[i] = args->r[i];
    for (int i = 0; i < 8; i++) c->s[i] = args->s[i];
    for (int i = 0; i < TCRT_CALL_FLOATS; i++) memcpy(&c->f[i], &args->f[i], 8);
}

u64 tc_call(u32 target, const TcArgs *args)
{
    u64 rv = g_ncalls < 16 ? g_rets[g_ncalls] : 0;
    log_call(target, args);
    g_ncalls++;
    return rv;
}

f64 tc_callf(u32 target, const TcArgs *args)
{
    u64 b = g_ncalls < 16 ? g_frets[g_ncalls] : 0;
    log_call(target, args);
    g_ncalls++;
    f64 d;
    memcpy(&d, &b, 8);
    return d;
}

static FILE *open_file(const char *path, const char *mode)
{
    FILE *f = NULL;
    return fopen_s(&f, path, mode) == 0 ? f : NULL;
}

static u8 *read_file(const char *path, long *size)
{
    FILE *f = open_file(path, "rb");
    if (!f) return NULL;
    fseek(f, 0, SEEK_END);
    *size = ftell(f);
    fseek(f, 0, SEEK_SET);
    u8 *buf = (u8 *)malloc((size_t)*size + 1);
    if (buf && fread(buf, 1, (size_t)*size, f) != (size_t)*size) {
        free(buf);
        buf = NULL;
    }
    fclose(f);
    return buf;
}

static int region_init(Region *r, u32 lo, u32 size, int compared)
{
    r->lo = lo;
    r->size = size;
    r->compared = compared;
    r->mem = (u8 *)calloc(size, 1);
    r->pristine = (u8 *)calloc(size, 1);
    r->written = (u8 *)calloc(size / 8 + 1, 1);
    return r->mem && r->pristine && r->written;
}

int tcrt_init(const char *image, const char *code_ranges)
{
    long size = 0;
    u8 *img = read_file(image, &size);
    if (!img || size <= 0 || (u32)size > TCRT_IMAGE_MAX) return 0;
    u32 isize = ((u32)size + 0xffffu) & ~0xffffu;
    if (!region_init(&g_reg[0], TCRT_IMAGE_BASE, isize, 1) || !region_init(&g_reg[1], TCRT_HEAP_BASE, TCRT_HEAP_SIZE, 1)
        || !region_init(&g_reg[2], TCRT_STACK_BASE, TCRT_STACK_SIZE, 0)
        || !region_init(&g_reg[3], TCRT_KERNEL_BASE, TCRT_KERNEL_SIZE, 1))
        return 0;
    memcpy(g_reg[0].pristine, img, (size_t)size);
    memcpy(g_reg[0].mem, img, (size_t)size);
    free(img);
    g_code = (u8 *)calloc(isize / 8 + 1, 1);
    g_wlist = (u32 *)malloc(sizeof(u32) * MAX_WRITES);
    g_calls = (CallRecord *)malloc(sizeof(CallRecord) * MAX_CALLS);
    if (!g_code || !g_wlist || !g_calls) return 0;
    FILE *f = open_file(code_ranges, "r");
    if (!f) return 0;
    unsigned lo, hi;
    while (fscanf_s(f, "%x %x", &lo, &hi) == 2) {
        for (u32 a = lo; a < hi && a - TCRT_IMAGE_BASE < isize; a++) bit_set(g_code, a - TCRT_IMAGE_BASE);
    }
    fclose(f);
    return 1;
}

/* Restores memory to the image: only the bytes the last vector wrote or patched (a full copy of every region only
 * after an overflow of either list), so a vector costs its own writes, not the 20 MB image. */
void tcrt_reset(void)
{
    if (g_woverflow || g_poverflow) {
        for (int i = 0; i < NREGIONS; i++) {
            Region *r = &g_reg[i];
            memset(r->written, 0, r->size / 8 + 1);
            memcpy(r->mem, r->pristine, r->size);
        }
    } else {
        for (u32 i = 0; i < g_wcount; i++) {
            Region *r = region_of(g_wlist[i]);
            u32 off = g_wlist[i] - r->lo;
            r->mem[off] = r->pristine[off];
            r->written[off >> 3] = 0;
        }
        for (u32 i = 0; i < g_pcount; i++) {
            Region *r = region_of(g_plist[i].addr);
            u32 off = g_plist[i].addr - r->lo;
            memcpy(r->mem + off, r->pristine + off, g_plist[i].len);
        }
    }
    g_wcount = 0;
    g_woverflow = 0;
    g_pcount = 0;
    g_poverflow = 0;
    g_ncalls = 0;
    g_nfaults = 0;
    g_code_read = 0;
    memset(g_rets, 0, sizeof g_rets);
    memset(g_frets, 0, sizeof g_frets);
}

void tcrt_poke(u32 addr, const u8 *data, u32 len)
{
    Region *r = region_of(addr);
    if (!r || r->size - (addr - r->lo) < len) return;
    memcpy(r->mem + (addr - r->lo), data, len);
    if (g_pcount < MAX_POKES) {
        g_plist[g_pcount].addr = addr;
        g_plist[g_pcount].len = len;
        g_pcount++;
    } else {
        g_poverflow = 1;
    }
}

void tcrt_set_call_returns(const u64 rets[16], const u64 frets[16])
{
    memcpy(g_rets, rets, sizeof g_rets);
    memcpy(g_frets, frets, sizeof g_frets);
}

static int cmp_u32(const void *a, const void *b)
{
    u32 x = *(const u32 *)a, y = *(const u32 *)b;
    return x < y ? -1 : (x > y);
}

void tcrt_report(FILE *out, u64 r3, u64 f1_bits)
{
    fprintf(out, "RET %016llx %016llx\n", (unsigned long long)r3, (unsigned long long)f1_bits);
    if (g_woverflow) fprintf(out, "FAULT too-many-writes\n");
    qsort(g_wlist, g_wcount, sizeof(u32), cmp_u32);
    u32 i = 0;
    while (i < g_wcount) {
        u32 start = g_wlist[i], end = start + 1;
        const Region *r = region_of(start);
        while (i + 1 < g_wcount && g_wlist[i + 1] == end && end - r->lo < r->size) {
            i++;
            end++;
        }
        i++;
        if (!r->compared) continue;
        fprintf(out, "W %08x ", start);
        for (u32 a = start; a < end; a++) fprintf(out, "%02x", r->mem[a - r->lo]);
        fputc('\n', out);
    }
    for (u32 c = 0; c < g_ncalls && c < MAX_CALLS; c++) {
        const CallRecord *k = &g_calls[c];
        fprintf(out, "CALL %08x", k->target);
        for (int j = 0; j < 8; j++) fprintf(out, " %016llx", (unsigned long long)k->r[j]);
        for (int j = 0; j < TCRT_CALL_FLOATS; j++) fprintf(out, " %016llx", (unsigned long long)k->f[j]);
        for (int j = 0; j < 8; j++) fprintf(out, " %016llx", (unsigned long long)k->s[j]);
        fputc('\n', out);
    }
    if (g_ncalls > MAX_CALLS) fprintf(out, "FAULT too-many-calls\n");
    for (u32 k = 0; k < g_nfaults && k < MAX_FAULTS; k++) fprintf(out, "FAULT %s\n", g_faults[k]);
    if (g_code_read) fprintf(out, "CODEREAD %08x\n", g_code_read);
}
