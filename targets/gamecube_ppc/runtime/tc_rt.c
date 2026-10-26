/* DecompPipeline GameCube runtime implementation: guest MEM1 (big endian), loaded from the DOL's sections like
 * the oracle's Ghidra program; the write set, the call log and the report (instruction helpers: tc_ppc.c). */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "tc_rt_internal.h"

#define MAX_WRITES (1u << 22)
#define MAX_CALLS 4096 /* TcOracle.CALL_LIMIT: the oracle rejects vectors making more calls */
#define MAX_FAULTS 4
/* TcOracle.callRecord: target, r3..r10, f1..f8 (64-bit), s0..s7 */
#define LOG_INTS 17

static u8 *g_ram;
static u8 *g_pristine;
static u8 *g_written; /* 1 bit per guest byte */
static u8 *g_code;    /* 1 bit per guest byte that belongs to an instruction */
static u32 *g_wlist;
static u32 g_wcount;
static int g_woverflow;
static u32 g_rets[16];
static u64 g_frets[16];
static u32 g_ncalls;
static u32 g_call_int[MAX_CALLS][LOG_INTS]; /* target, r3..r10, s0..s7 */
static u64 g_call_f[MAX_CALLS][8];          /* f1..f8 */
static char g_faults[MAX_FAULTS][64];
static u32 g_nfaults;
static u32 g_code_read;

static int bit_get(const u8 *bits, u32 off) { return (bits[off >> 3] >> (off & 7)) & 1; }
static void bit_set(u8 *bits, u32 off) { bits[off >> 3] |= (u8)(1u << (off & 7)); }

static void fault(const char *kind, u32 addr)
{
    if (g_nfaults < MAX_FAULTS) snprintf(g_faults[g_nfaults], sizeof g_faults[0], "%s %08x", kind, addr);
    g_nfaults++;
}

static int check(u32 addr, u32 size, int write)
{
    if (addr < TCRT_RAM_BASE || addr > TCRT_RAM_BASE + TCRT_RAM_SIZE - size) {
        fault(write ? "write" : "read", addr);
        return 0;
    }
    if (!write && bit_get(g_code, addr - TCRT_RAM_BASE)) {
        if (!g_code_read) g_code_read = addr;
    }
    return 1;
}

static void mark(u32 addr, u32 size)
{
    for (u32 i = 0; i < size; i++) {
        u32 off = addr + i - TCRT_RAM_BASE;
        if (bit_get(g_written, off)) continue;
        bit_set(g_written, off);
        if (g_wcount < MAX_WRITES) g_wlist[g_wcount++] = off;
        else g_woverflow = 1;
    }
}

static u64 rd_be(u32 a, u32 size)
{
    a = tcrt_fold(a);
    if (!check(a, size, 0)) return 0;
    const u8 *p = g_ram + (a - TCRT_RAM_BASE);
    u64 v = 0;
    for (u32 i = 0; i < size; i++) v = (v << 8) | p[i];
    return v;
}

static void wr_be(u32 a, u64 v, u32 size)
{
    a = tcrt_fold(a);
    if (!check(a, size, 1)) return;
    u8 *p = g_ram + (a - TCRT_RAM_BASE);
    for (u32 i = 0; i < size; i++) p[i] = (u8)(v >> (8 * (size - 1 - i)));
    mark(a, size);
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

static void log_call(u32 target, const TcArgs *args)
{
    if (g_ncalls >= MAX_CALLS) return;
    g_call_int[g_ncalls][0] = target;
    for (int i = 0; i < 8; i++) g_call_int[g_ncalls][1 + i] = args ? args->r[i] : 0;
    for (int i = 0; i < 8; i++) g_call_int[g_ncalls][9 + i] = args ? args->s[i] : 0;
    for (int i = 0; i < 8; i++) {
        u64 b = 0;
        if (args) memcpy(&b, &args->f[i], 8);
        g_call_f[g_ncalls][i] = b;
    }
}

/* The stricter judge (tc_strict.h): MEM1 (TcBase.inRam); a write only where no byte is an instruction's
 * (TcStrict.writable), logged like the C's own. The GameCube runtime models no register: no fr junk. */
static int tcs_read(u32 p, u32 n, u8 *out)
{
    p = tcrt_fold(p);
    if (p < TCRT_RAM_BASE || p > TCRT_RAM_BASE + TCRT_RAM_SIZE - n) return 0;
    memcpy(out, g_ram + (p - TCRT_RAM_BASE), n);
    return 1;
}

static void tcs_write(u32 p, u32 n, const u8 *in)
{
    p = tcrt_fold(p);
    if (p < TCRT_RAM_BASE || p > TCRT_RAM_BASE + TCRT_RAM_SIZE - n) return;
    for (u32 k = 0; k < n; k++)
        if (bit_get(g_code, p + k - TCRT_RAM_BASE)) return;
    memcpy(g_ram + (p - TCRT_RAM_BASE), in, n);
    mark(p, n);
}

static void tcs_scratch(int i, u32 bits) { (void)i, (void)bits; }

#include "tc_strict.h"

/* The call just logged, as TcOracle.callRecord's fields for tc_strict.h: target, r3..r10 (1..8), s0..s7 (17..24). */
static void strict_call(u32 target)
{
    u32 rec[TCS_FIELDS] = {0};
    if (g_ncalls >= MAX_CALLS) return;
    for (int i = 0; i < 9; i++) rec[i] = g_call_int[g_ncalls][i];
    for (int i = 0; i < 8; i++) rec[17 + i] = g_call_int[g_ncalls][9 + i];
    tcs_call(g_ncalls, target, rec);
}

u32 tc_call(u32 target, const TcArgs *args)
{
    u32 rv = g_ncalls < 16 ? g_rets[g_ncalls] : 0;
    log_call(tcrt_fold(target), args);
    strict_call(tcrt_fold(target));
    g_ncalls++;
    return rv;
}

f64 tc_callf(u32 target, const TcArgs *args)
{
    u64 b = g_ncalls < 16 ? g_frets[g_ncalls] : 0;
    log_call(tcrt_fold(target), args);
    strict_call(tcrt_fold(target));
    g_ncalls++;
    f64 d;
    memcpy(&d, &b, 8);
    return d;
}

/* The last call's other result: its r3 after a tc_callf, its f1 after a tc_call (the mocked callee sets both). */
u32 tc_lastr(void)
{
    return g_ncalls >= 1 && g_ncalls <= 16 ? g_rets[g_ncalls - 1] : 0;
}

f64 tc_lastf(void)
{
    u64 b = g_ncalls >= 1 && g_ncalls <= 16 ? g_frets[g_ncalls - 1] : 0;
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

static u32 be32(const u8 *p) { return (u32)p[0] << 24 | (u32)p[1] << 16 | (u32)p[2] << 8 | p[3]; }

/* The DOL's 7 text and 11 data sections, copied to their load addresses (the rest of MEM1, bss included, is 0). */
static int load_dol(const u8 *dol, long size)
{
    if (size < 0x100) return 0;
    for (int i = 0; i < 18; i++) {
        u32 off = be32(dol + 4 * i), addr = be32(dol + 0x48 + 4 * i), len = be32(dol + 0x90 + 4 * i);
        if (!len) continue;
        if ((long)off + (long)len > size || addr < TCRT_RAM_BASE || addr - TCRT_RAM_BASE + len > TCRT_RAM_SIZE) return 0;
        memcpy(g_pristine + (addr - TCRT_RAM_BASE), dol + off, len);
    }
    return 1;
}

int tcrt_init(const char *dol, const char *code_ranges)
{
    g_ram = (u8 *)calloc(TCRT_RAM_SIZE, 1);
    g_pristine = (u8 *)calloc(TCRT_RAM_SIZE, 1);
    g_written = (u8 *)calloc(TCRT_RAM_SIZE / 8, 1);
    g_code = (u8 *)calloc(TCRT_RAM_SIZE / 8, 1);
    g_wlist = (u32 *)malloc(sizeof(u32) * MAX_WRITES);
    if (!g_ram || !g_pristine || !g_written || !g_code || !g_wlist) return 0;
    long size = 0;
    u8 *img = read_file(dol, &size);
    if (!img || !load_dol(img, size)) return 0;
    free(img);
    FILE *f = open_file(code_ranges, "r");
    if (!f) return 0;
    unsigned lo, hi;
    while (fscanf_s(f, "%x %x", &lo, &hi) == 2) {
        for (u32 a = lo; a < hi && a >= TCRT_RAM_BASE && a < TCRT_RAM_BASE + TCRT_RAM_SIZE; a++) {
            bit_set(g_code, a - TCRT_RAM_BASE);
        }
    }
    fclose(f);
    tcrt_reset();
    return 1;
}

void tcrt_reset(void)
{
    for (u32 i = 0; i < g_wcount; i++) g_written[g_wlist[i] >> 3] = 0;
    if (g_woverflow) memset(g_written, 0, TCRT_RAM_SIZE / 8);
    memcpy(g_ram, g_pristine, TCRT_RAM_SIZE);
    g_wcount = 0;
    g_woverflow = 0;
    g_ncalls = 0;
    g_nfaults = 0;
    g_code_read = 0;
    tcrt_strict_reset();
    memset(g_rets, 0, sizeof g_rets);
    memset(g_frets, 0, sizeof g_frets);
}

void tcrt_poke(u32 addr, const u8 *data, u32 len)
{
    addr = tcrt_fold(addr);
    if (addr < TCRT_RAM_BASE || addr - TCRT_RAM_BASE + len > TCRT_RAM_SIZE) return;
    memcpy(g_ram + (addr - TCRT_RAM_BASE), data, len);
}

void tcrt_set_call_returns(const u32 rets[16], const u64 frets[16])
{
    memcpy(g_rets, rets, sizeof g_rets);
    memcpy(g_frets, frets, sizeof g_frets);
}

static int cmp_u32(const void *a, const void *b)
{
    u32 x = *(const u32 *)a, y = *(const u32 *)b;
    return x < y ? -1 : (x > y);
}

void tcrt_report(FILE *out, u32 r3, u64 f1_bits, u32 r4)
{
    fprintf(out, "RET %08x %016llx %08x\n", r3, (unsigned long long)f1_bits, r4);
    if (g_woverflow) fprintf(out, "FAULT too-many-writes\n");
    qsort(g_wlist, g_wcount, sizeof(u32), cmp_u32);
    u32 i = 0;
    while (i < g_wcount) {
        u32 start = g_wlist[i], end = start + 1;
        while (i + 1 < g_wcount && g_wlist[i + 1] == end) {
            i++;
            end++;
        }
        i++;
        u32 addr = start + TCRT_RAM_BASE;
        if (addr >= TCRT_STACK_LO) continue;
        if (end + TCRT_RAM_BASE > TCRT_STACK_LO) end = TCRT_STACK_LO - TCRT_RAM_BASE;
        fprintf(out, "W %08x ", addr);
        for (u32 o = start; o < end; o++) fprintf(out, "%02x", g_ram[o]);
        fputc('\n', out);
    }
    for (u32 c = 0; c < g_ncalls && c < MAX_CALLS; c++) {
        fprintf(out, "CALL");
        for (int k = 0; k < 9; k++) fprintf(out, " %08x", g_call_int[c][k]);
        for (int k = 0; k < 8; k++) fprintf(out, " %016llx", (unsigned long long)g_call_f[c][k]);
        for (int k = 9; k < LOG_INTS; k++) fprintf(out, " %08x", g_call_int[c][k]);
        fputc('\n', out);
    }
    if (g_ncalls > MAX_CALLS) fprintf(out, "FAULT too-many-calls\n");
    tcrt_strict_report(out); /* CS lines; XR: the exit frame (tc_strict.h) */
    if (tcrt_strict_flags() & 8) { /* r1; r14..r31 and f14..f31: their entry values, 0 (TcOracle.setEntry) */
        fprintf(out, "XR %08x", TCRT_SP);
        for (int k = 14; k < 32; k++) fprintf(out, " %08x", 0u);
        for (int k = 14; k < 32; k++) fprintf(out, " %016llx", 0ull);
        fputc('\n', out);
    }
    for (u32 k = 0; k < g_nfaults && k < MAX_FAULTS; k++) fprintf(out, "FAULT %s\n", g_faults[k]);
    if (g_code_read) fprintf(out, "CODEREAD %08x\n", g_code_read);
}
