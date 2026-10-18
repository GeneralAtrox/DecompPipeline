/* DecompPipeline Dreamcast SH-4 runtime implementation (guest RAM model, call log, SH-4 helpers; the FPU registers
 * and their helpers are in tc_fpu.c). */
#include <math.h>
#include <stdio.h>
#include <xmmintrin.h>
#include <stdlib.h>
#include <string.h>
#include "tc_rt_internal.h"

#define MAX_WRITES (1u << 22)
#define MAX_CALLS 4096 /* TcOracle.CALL_LIMIT: the oracle rejects vectors making more calls */
#define MAX_FAULTS 4
/* TcOracle.callRecord: target, r4..r7, fr4..fr11, s0..s7, fr12..fr15, r0..r3, r8..r14 (36 fields), and with line Q
 * fr0..fr3, xf0..xf15 and the FPSCR mode word (57 fields: tc_fpu.c) */
#define LOG_FIELDS 57

static u8 *g_ram;
static u8 *g_pristine;
static u8 *g_written;   /* 1 bit per guest byte */
static u8 *g_code;      /* 1 bit per guest byte that belongs to an instruction */
static u32 *g_wlist;
static u32 g_wcount;
static int g_woverflow;
static u32 g_gs[7]; /* r8..r14 */
static u32 g_low[4]; /* r0..r3 at entry (vectors.txt line L) */
static u32 g_retx;   /* the extra result word (a nonstandard result register, a u64's high word) */
static int g_has_retx;
static u32 g_rets[16];
static u32 g_ncalls;
static u32 g_call_log[MAX_CALLS][LOG_FIELDS];
static char g_faults[MAX_FAULTS][64];
static u32 g_nfaults;
static u32 g_code_read;
static u32 g_fsca[65536][2];
static u32 g_mxcsr_rc; /* MXCSR rounding control for FPSCR.RM (vectors.txt line P): 0 nearest, 0x6000 toward zero */

static int bit_get(const u8 *bits, u32 off) { return (bits[off >> 3] >> (off & 7)) & 1; }
static void bit_set(u8 *bits, u32 off) { bits[off >> 3] |= (u8)(1u << (off & 7)); }

static u32 f2u(f32 f) { u32 u; memcpy(&u, &f, 4); return u; }
static f32 u2f(u32 u) { f32 f; memcpy(&f, &u, 4); return f; }

static void fault(const char *kind, u32 addr)
{
    if (g_nfaults < MAX_FAULTS) snprintf(g_faults[g_nfaults], sizeof g_faults[0], "%s %08x", kind, addr);
    g_nfaults++;
}

static int check(u32 addr, u32 size, int write)
{
    if (addr < TCRT_VALID_LO || addr > 0x8d000000u - size) {
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

u8 rd8(u32 a)
{
    a = tcrt_fold(a);
    return check(a, 1, 0) ? g_ram[a - TCRT_RAM_BASE] : 0;
}

u16 rd16(u32 a)
{
    a = tcrt_fold(a);
    if (!check(a, 2, 0)) return 0;
    const u8 *p = g_ram + (a - TCRT_RAM_BASE);
    return (u16)(p[0] | (p[1] << 8));
}

u32 rd32(u32 a)
{
    a = tcrt_fold(a);
    if (!check(a, 4, 0)) return 0;
    const u8 *p = g_ram + (a - TCRT_RAM_BASE);
    return (u32)p[0] | ((u32)p[1] << 8) | ((u32)p[2] << 16) | ((u32)p[3] << 24);
}

f32 rdf32(u32 a) { return u2f(rd32(a)); }

void wr8(u32 a, u8 v)
{
    a = tcrt_fold(a);
    if (tcrt_p4_store(a, v, 1) || !check(a, 1, 1)) return;
    g_ram[a - TCRT_RAM_BASE] = v;
    mark(a, 1);
}

void wr16(u32 a, u16 v)
{
    a = tcrt_fold(a);
    if (tcrt_p4_store(a, v, 2) || !check(a, 2, 1)) return;
    u8 *p = g_ram + (a - TCRT_RAM_BASE);
    p[0] = (u8)v;
    p[1] = (u8)(v >> 8);
    mark(a, 2);
}

void wr32(u32 a, u32 v)
{
    a = tcrt_fold(a);
    if (tcrt_p4_store(a, v, 4) || !check(a, 4, 1)) return;
    u8 *p = g_ram + (a - TCRT_RAM_BASE);
    p[0] = (u8)v;
    p[1] = (u8)(v >> 8);
    p[2] = (u8)(v >> 16);
    p[3] = (u8)(v >> 24);
    mark(a, 4);
}

void wrf32(u32 a, f32 v) { wr32(a, f2u(v)); }

static u32 next_return(void) { return g_ncalls < 16 ? g_rets[g_ncalls] : 0; }

int tcrt_sq_window; /* header line S (TcSq): tcrt_fold maps the store-queue window onto RAM */

void tcrt_set_sq_window(void) { tcrt_sq_window = 1; }

/* TcLive: the callees that change r8..r14 (vectors.txt header lines K, kept across vectors) and the vector's draws
 * (line A). After the mocked call number k to such a callee, rN += its delta: the constant, one of its set (draw k),
 * or 0x20 * (1 + draw % 3) when unknown, exactly as the oracle's mocked call. */
#define MAX_LIVE 256
static struct { u32 target, reg, n, d[4]; } g_live[MAX_LIVE];
static u32 g_nlive;
static u32 g_draws[16];

void tcrt_add_live(u32 target, u32 reg, u32 n, const u32 d[4])
{
    if (g_nlive >= MAX_LIVE || reg < 8 || reg > 14 || n > 4) return;
    g_live[g_nlive].target = tcrt_fold(target);
    g_live[g_nlive].reg = reg;
    g_live[g_nlive].n = n;
    memcpy(g_live[g_nlive].d, d, sizeof g_live[0].d);
    g_nlive++;
}

void tcrt_set_draws(const u32 draws[16]) { memcpy(g_draws, draws, sizeof g_draws); }

static void live_apply(u32 target, u32 k)
{
    u32 draw = g_draws[k % 16];
    for (u32 i = 0; i < g_nlive; i++) {
        if (g_live[i].target != target) continue;
        u32 n = g_live[i].n;
        g_gs[g_live[i].reg - 8] += n == 1 ? g_live[i].d[0] : n ? g_live[i].d[draw % n] : 0x20u * (1u + draw % 3u);
    }
}

static void log_call(u32 target, const TcArgs *args)
{
    if (g_ncalls < MAX_CALLS) {
        g_call_log[g_ncalls][0] = target;
        for (int i = 0; i < 4; i++) g_call_log[g_ncalls][1 + i] = args ? args->r[i] : 0;
        for (int i = 0; i < 8; i++) g_call_log[g_ncalls][13 + i] = args ? args->s[i] : 0;
        tcrt_fpu_log(g_call_log[g_ncalls], args); /* fr4..fr11, fr12..fr15 and, with line Q, the FPU */
        for (int i = 0; i < 4; i++) g_call_log[g_ncalls][25 + i] = args ? args->rlo[i] : 0;
        for (int i = 0; i < 7; i++) g_call_log[g_ncalls][29 + i] = g_gs[i];
    }
}

u32 tc_call(u32 target, const TcArgs *args)
{
    u32 rv = next_return();
    log_call(tcrt_fold(target), args);
    live_apply(tcrt_fold(target), g_ncalls); /* after the record: the callee saw the old rN */
    g_ncalls++;
    tcrt_fpu_result(rv); /* the oracle's mocked callee leaves its result in r0 and fr0 */
    return rv;
}

f32 tc_callf(u32 target, const TcArgs *args) { return u2f(tc_call(target, args)); }

/* Matches Ghidra's p-code float truncation: a saturating 64-bit cast, then the low 32 bits. */
s32 sh4_ftrc(f32 x)
{
    if (x != x) return 0;
    double d = x;
    if (d >= 9223372036854775807.0) return (s32)0xffffffffu;
    if (d <= -9223372036854775808.0) return 0;
    return (s32)(u32)(unsigned long long)(long long)d;
}

f32 sh4_fsrra(f32 x)
{
    f32 one = 1.0f;
    return one / sqrtf(x);
}

/* fsqrt: the SSE root (sqrtss), so MXCSR decides it exactly as FPSCR does on the SH-4: RC from FPSCR.RM (TcRound: the
 * correctly rounded root, toward zero when RM=1), DAZ|FTZ from FPSCR.DN. A negative operand gives a NaN (the judge
 * compares NaNs as equal). */
f32 sh4_fsqrt_rm(f32 x)
{
    return _mm_cvtss_f32(_mm_sqrt_ss(_mm_set_ss(x)));
}

void sh4_fsca(u32 fpul, f32 *s, f32 *c)
{
    u32 i = fpul & 0xffffu;
    *s = u2f(g_fsca[i][0]);
    *c = u2f(g_fsca[i][1]);
}

u32 sh4_r(int i) { return (i >= 8 && i < 15) ? g_gs[i - 8] : 0; }

void sh4_set_r(int i, u32 v)
{
    if (i >= 8 && i < 15) g_gs[i - 8] = v;
}

/* ---------------- runner-only ---------------- */

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

int tcrt_init(const char *image, const char *code_ranges, const char *fsca_table)
{
    g_ram = (u8 *)calloc(TCRT_RAM_SIZE, 1);
    g_pristine = (u8 *)calloc(TCRT_RAM_SIZE, 1);
    g_written = (u8 *)calloc(TCRT_RAM_SIZE / 8, 1);
    g_code = (u8 *)calloc(TCRT_RAM_SIZE / 8, 1);
    g_wlist = (u32 *)malloc(sizeof(u32) * MAX_WRITES);
    if (!g_ram || !g_pristine || !g_written || !g_code || !g_wlist) return 0;
    long size = 0;
    u8 *img = read_file(image, &size);
    if (!img || size > (long)(TCRT_RAM_SIZE - 0x10000)) return 0;
    memcpy(g_pristine + 0x10000, img, (size_t)size);
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
    u8 *tab = read_file(fsca_table, &size);
    if (!tab || size != (long)sizeof g_fsca) return 0;
    memcpy(g_fsca, tab, sizeof g_fsca);
    free(tab);
    tcrt_reset();
    return 1;
}

void tcrt_reset(void)
{
    for (u32 i = 0; i < g_wcount; i++) {
        u32 off = g_wlist[i];
        g_written[off >> 3] = 0;
    }
    if (g_woverflow) memset(g_written, 0, TCRT_RAM_SIZE / 8);
    memcpy(g_ram, g_pristine, TCRT_RAM_SIZE);
    g_wcount = 0;
    g_woverflow = 0;
    tcrt_p4_reset();
    g_ncalls = 0;
    g_nfaults = 0;
    g_code_read = 0;
    tcrt_fpu_reset();
    memset(g_gs, 0, sizeof g_gs);
    memset(g_low, 0, sizeof g_low);
    memset(g_rets, 0, sizeof g_rets);
    memset(g_draws, 0, sizeof g_draws);
    g_retx = 0;
    g_has_retx = 0;
    /* FPSCR.DN=1, as the game runs (TcOracle sets FPSCR 0x00040001; TcFpu models it): denormal operands count as
     * zero (DAZ) and denormal results are flushed to zero (FTZ). FPSCR.RM=1 (line P, only when the oracle models
     * it: TcRound) rounds toward zero (RC=11). Here, not in the runner: main's frame is unchanged. */
    _mm_setcsr((_mm_getcsr() & ~0x6000u) | 0x8040u | g_mxcsr_rc);
}

void tcrt_set_fpscr(u32 fpscr)
{
    g_mxcsr_rc = (fpscr & 3u) == 1u ? 0x6000u : 0u;
}

void tcrt_poke(u32 addr, const u8 *data, u32 len)
{
    addr = tcrt_fold(addr);
    if (addr < TCRT_RAM_BASE || addr + len > TCRT_RAM_BASE + TCRT_RAM_SIZE) return;
    memcpy(g_ram + (addr - TCRT_RAM_BASE), data, len);
}

void tcrt_set_gs(const u32 bits[7]) { memcpy(g_gs, bits, sizeof g_gs); }

void tcrt_report_gs(FILE *out)
{
    fprintf(out, "GH");
    for (int k = 0; k < 7; k++) fprintf(out, " %08x", g_gs[k]);
    fputc('\n', out);
}

void tcrt_set_low(const u32 bits[4]) { memcpy(g_low, bits, sizeof g_low); }

u32 tcrt_low(int i) { return (i >= 0 && i < 4) ? g_low[i] : 0; }

void tcrt_set_retx(u32 v)
{
    g_retx = v;
    g_has_retx = 1;
}

void tcrt_set_call_returns(const u32 rets[16]) { memcpy(g_rets, rets, sizeof g_rets); }

static int cmp_u32(const void *a, const void *b)
{
    u32 x = *(const u32 *)a, y = *(const u32 *)b;
    return x < y ? -1 : (x > y);
}

void tcrt_report(FILE *out, u32 r0, u32 fr0_bits)
{
    fprintf(out, "RET %08x %08x\n", r0, fr0_bits);
    if (g_has_retx) fprintf(out, "RETX %08x\n", g_retx);
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
    tcrt_p4_report(out);
    int fields = tcrt_fpu_state() ? LOG_FIELDS : 36;
    for (u32 c = 0; c < g_ncalls && c < MAX_CALLS; c++) {
        fprintf(out, "CALL");
        for (int k = 0; k < fields; k++) fprintf(out, " %08x", g_call_log[c][k]);
        fputc('\n', out);
    }
    if (g_ncalls > MAX_CALLS) fprintf(out, "FAULT too-many-calls\n");
    tcrt_report_xf(out);
    for (u32 k = 0; k < g_nfaults && k < MAX_FAULTS; k++) fprintf(out, "FAULT %s\n", g_faults[k]);
    if (g_code_read) fprintf(out, "CODEREAD %08x\n", g_code_read);
}
