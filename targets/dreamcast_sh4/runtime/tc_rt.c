/* DecompPipeline Dreamcast SH-4 runtime implementation (guest RAM model, call log, SH-4 helpers; the FPU registers
 * and their helpers are in tc_fpu.c). */
#include <math.h>
#include <stdio.h>
#include <emmintrin.h>
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
static u32 g_ro[4]; /* r4..r7 as the last call left them (TcOut): its arguments, a K callee's output added */
static u32 g_ro_mask; /* header line O: the r4..r7 compared at exit (bit k: r(4+k)), reported as RO */
static u32 g_t; /* T as the C handed it over (sh4_set_t), or as a K line for register 16 left it after a call */
static int g_t_export; /* header line t: T compared at exit, reported as RT (TcTflag) */
static int g_area5; /* header line y: a read in area 5 returns 0 (tcrt_area5, TcHw "read area5") */
static u32 g_low[4]; /* r0..r3 at entry (vectors.txt line L) */
static u32 g_retx;   /* the extra result word (a nonstandard result register, a u64's high word) */
static int g_has_retx;
static u32 g_rets[TCRT_RETS];
static u32 g_ncalls;
static u32 g_call_log[MAX_CALLS][LOG_FIELDS];
static char g_faults[MAX_FAULTS][64];
static u32 g_nfaults;
static u32 g_code_read;
static u32 g_fsca[2][65536][2]; /* fsca_table.bin: {sin, cos} bits per angle, round to nearest, then toward zero */
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
    if (!write && g_area5 && tcrt_area5(addr, size)) return 0; /* reads 0, no fault (the caller returns 0) */
    if (addr < TCRT_VALID_LO || addr > 0x8d000000u - size) {
        fault(write ? "write" : "read", addr);
        return 0;
    }
    if (!write && bit_get(g_code, addr - TCRT_RAM_BASE)) {
        if (!g_code_read) g_code_read = addr;
    }
    return 1;
}

/* A 2-byte access at an odd address or a 4-byte one off a multiple of 4 is an SH-4 address error: no run of the
 * original makes one (the oracle drops such a vector, TcFold), so the C making one is wrong, whatever the bytes. */
static int aligned(u32 addr, u32 size, int write)
{
    if (!(addr & (size - 1))) return 1;
    fault(write ? "write-misaligned" : "read-misaligned", addr);
    return 0;
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
    if (!aligned(a, 2, 0) || !check(a, 2, 0)) return 0;
    const u8 *p = g_ram + (a - TCRT_RAM_BASE);
    return (u16)(p[0] | (p[1] << 8));
}

u32 rd32(u32 a)
{
    a = tcrt_fold(a);
    if (!aligned(a, 4, 0) || !check(a, 4, 0)) return 0;
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
    if (!aligned(a, 2, 1) || tcrt_p4_store(a, v, 2) || !check(a, 2, 1)) return;
    u8 *p = g_ram + (a - TCRT_RAM_BASE);
    p[0] = (u8)v;
    p[1] = (u8)(v >> 8);
    mark(a, 2);
}

void wr32(u32 a, u32 v)
{
    a = tcrt_fold(a);
    if (!aligned(a, 4, 1) || tcrt_p4_store(a, v, 4) || !check(a, 4, 1)) return;
    u8 *p = g_ram + (a - TCRT_RAM_BASE);
    p[0] = (u8)v;
    p[1] = (u8)(v >> 8);
    p[2] = (u8)(v >> 16);
    p[3] = (u8)(v >> 24);
    mark(a, 4);
}

void wrf32(u32 a, f32 v) { wr32(a, f2u(v)); }

static u32 next_return(void) { return g_ncalls < TCRT_RETS ? g_rets[g_ncalls] : 0; }

int tcrt_sq_window; /* header line S (TcSq): tcrt_fold maps the store-queue window onto RAM */

char tcrt_sq_line[1024]; /* line S itself: its callee words are for xemu (TcSq.header) */

void tcrt_set_sq_window(void) { tcrt_sq_window = 1; }

/* TcLive: the callees that change r8..r14, or hand back r4..r7 (TcOut), vectors.txt header lines K, kept across
 * vectors, and the vector's draws (line A). After the mocked call number k to such a callee, rN += its delta: the
 * constant, one of its set (draw k), or 0x20 * (1 + draw % 3) when unknown, exactly as the oracle's mocked call.
 * Register 16 is T (TcTflag): T = (draw k >> 8) & 1. */
#define MAX_LIVE 256
static struct { u32 target, reg, n, d[4]; } g_live[MAX_LIVE];
static u32 g_nlive;
static u32 g_draws[16];

void tcrt_add_live(u32 target, u32 reg, u32 n, const u32 d[4])
{
    if (g_nlive >= MAX_LIVE || reg < 4 || reg > 14 && reg != 16 || n > 4) return;
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
        if (g_live[i].reg == 16) { g_t = draw >> 8 & 1u; continue; } /* T (TcTflag) */
        u32 n = g_live[i].n;
        u32 *rn = g_live[i].reg >= 8 ? &g_gs[g_live[i].reg - 8] : &g_ro[g_live[i].reg - 4];
        *rn += n == 1 ? g_live[i].d[0] : n ? g_live[i].d[draw % n] : 0x20u * (1u + draw % 3u);
    }
}

static void log_call(u32 target, const TcArgs *args)
{
    tcrt_fpscr_call();
    if (g_ncalls < MAX_CALLS) {
        g_call_log[g_ncalls][0] = target;
        for (int i = 0; i < 4; i++) g_call_log[g_ncalls][1 + i] = args ? args->r[i] : 0;
        for (int i = 0; i < 8; i++) g_call_log[g_ncalls][13 + i] = args ? args->s[i] : 0;
        tcrt_fpu_log(g_call_log[g_ncalls], args); /* fr4..fr11, fr12..fr15 and, with line Q, the FPU */
        for (int i = 0; i < 4; i++) g_call_log[g_ncalls][25 + i] = args ? args->rlo[i] : 0;
        for (int i = 0; i < 7; i++) g_call_log[g_ncalls][29 + i] = g_gs[i];
    }
}

/* The stricter judge (tc_strict.h): guest RAM in the oracle's bounds (TcBase.inRam: from TCRT_VALID_LO); a write
 * only where no byte is an instruction's (TcStrict.writable), logged like the C's own. */
static int tcs_read(u32 p, u32 n, u8 *out)
{
    p = tcrt_fold(p);
    if (p < TCRT_VALID_LO || p > 0x8d000000u - n) return 0;
    memcpy(out, g_ram + (p - TCRT_RAM_BASE), n);
    return 1;
}

static void tcs_write(u32 p, u32 n, const u8 *in)
{
    p = tcrt_fold(p);
    if (p < TCRT_VALID_LO || p > 0x8d000000u - n) return;
    for (u32 k = 0; k < n; k++)
        if (bit_get(g_code, p + k - TCRT_RAM_BASE)) return;
    memcpy(g_ram + (p - TCRT_RAM_BASE), in, n);
    mark(p, n);
}

static void tcs_scratch(int i, u32 bits) { tcrt_fpu_scratch(i, bits); }

#include "tc_strict.h"

jmp_buf tcrt_halt_jmp;
int tcrt_halted;

void tc_halt(void)
{
    tcrt_halted = 1;
    longjmp(tcrt_halt_jmp, 1);
}

void tc_spin(void)
{
    tcrt_halted = 2;
    longjmp(tcrt_halt_jmp, 1);
}

#include "tc_flag.h" /* a run flag (header line f, TcCalls): the Nth call to the listed callee clears or sets a word */

u32 tc_call(u32 target, const TcArgs *args)
{
    u32 rv = next_return();
    log_call(tcrt_fold(target), args);
    for (int k = 0; k < 4; k++) g_ro[k] = args->r[k]; /* the callee got them; a K line moves its output */
    live_apply(tcrt_fold(target), g_ncalls); /* after the record: the callee saw the old rN */
    if (g_ncalls < MAX_CALLS) tcs_call(g_ncalls, tcrt_fold(target), g_call_log[g_ncalls]); /* tc_strict.h */
    flag_apply(tcrt_fold(target));
    g_ncalls++;
    tcrt_fpu_result(rv); /* the oracle's mocked callee leaves its result in r0 and fr0 */
    return rv;
}

f32 tc_callf(u32 target, const TcArgs *args) { return u2f(tc_call(target, args)); }

/* The SH-4's ftrc: truncation toward zero, saturating at 0x7fffffff above the int range and 0x80000000 below it;
 * a NaN gives 0x80000000. The oracle's FPUL is the same (TcFpu: Ghidra's p-code trunc wraps a 64-bit cast). */
s32 sh4_ftrc(f32 x)
{
    if (x != x) return (s32)0x80000000u;
    double d = x;
    if (d >= 2147483648.0) return 0x7fffffff;
    if (d <= -2147483648.0) return (s32)0x80000000u;
    return (s32)d;
}

f32 sh4_fsrra(f32 x)
{
    f32 one = 1.0f;
    return one / sqrtf(x);
}

/* fmac FR0,FRm,FRn: fr0 * frm + frn rounded once, as the SH-4 does (software manual 6.4: "Rounding is performed once
 * in FMAC"; 9.36 normal_fmac: the exact product, one conversion), by MXCSR.RC (FPSCR.RM) with DAZ|FTZ (FPSCR.DN).
 * SSE2 intrinsics only (no fmaf, no x87): the double product of two floats is exact. Toward zero, the double sum
 * truncated and then truncated to float is the exact sum truncated once. To nearest, the double sum is made
 * round-to-odd (TwoSum gives its exact error; an inexact sum gets an odd last bit), which then rounds to float as the
 * exact sum would (53 >= 24 + 2 bits). The oracle's is TcRound.fma (tests/test_fmac.py checks both exactly). */
f32 sh4_fmac(f32 fr0, f32 frm, f32 frn)
{
    __m128d z = _mm_setzero_pd(), p, c, s, t, e;
    double sd, ed;
    unsigned long long bits;
    p = _mm_mul_sd(_mm_cvtss_sd(z, _mm_set_ss(fr0)), _mm_cvtss_sd(z, _mm_set_ss(frm)));
    c = _mm_cvtss_sd(z, _mm_set_ss(frn));
    s = _mm_add_sd(p, c);
    if ((_mm_getcsr() & 0x6000u) == 0u) {
        t = _mm_sub_sd(s, p); /* TwoSum: s + e == p + c exactly (round to nearest) */
        e = _mm_add_sd(_mm_sub_sd(p, _mm_sub_sd(s, t)), _mm_sub_sd(c, t));
        sd = _mm_cvtsd_f64(s);
        ed = _mm_cvtsd_f64(e);
        memcpy(&bits, &sd, 8);
        if (ed != 0.0 && (bits & 0x7ff0000000000000ull) != 0x7ff0000000000000ull && !(bits & 1u)) {
            bits += (ed < 0.0) == (sd < 0.0) ? 1u : ~0ull; /* the odd neighbour on the exact sum's side */
            memcpy(&sd, &bits, 8);
            s = _mm_set_sd(sd);
        }
    }
    return _mm_cvtss_f32(_mm_cvtsd_ss(_mm_setzero_ps(), s));
}

/* fsqrt: the SSE root (sqrtss), so MXCSR decides it exactly as FPSCR does on the SH-4: RC from FPSCR.RM (TcRound: the
 * correctly rounded root, toward zero when RM=1), DAZ|FTZ from FPSCR.DN. A negative operand gives a NaN (the judge
 * compares NaNs as equal). */
f32 sh4_fsqrt_rm(f32 x)
{
    return _mm_cvtss_f32(_mm_sqrt_ss(_mm_set_ss(x)));
}

/* Double precision (FPSCR.PR=1) on fr pairs: sh4_double.h, shared with the readable and port runtimes; PR is
 * tc_fpu.c's mode word, and fcnvsd/fcnvds with PR=0 (undefined) report a fault. */
#define SH4D_PR() tcrt_fpu_pr()
#define SH4D_UNDEFINED(s) fault(s, 0u)
#include "sh4_double.h"

/* fsca: the table of the FPU's rounding, MXCSR.RC (FPSCR.RM: line P, xemu's lds); the SH-4 rounds no way up or down */
void sh4_fsca(u32 fpul, f32 *s, f32 *c)
{
    u32 rc = _mm_getcsr() & 0x6000u, i = fpul & 0xffffu;
    if (rc != 0u && rc != 0x6000u) {
        fprintf(stderr, "fsca: MXCSR.RC %04x rounds up or down, no FPSCR.RM does: no fsca table for it\n", rc);
        exit(3);
    }
    *s = u2f(g_fsca[rc != 0u][i][0]);
    *c = u2f(g_fsca[rc != 0u][i][1]);
}

u32 sh4_r(int i) { return (i >= 8 && i < 15) ? g_gs[i - 8] : (i >= 4 && i < 8) ? g_ro[i - 4] : 0; }

void sh4_set_r(int i, u32 v)
{
    if (i >= 8 && i < 15) g_gs[i - 8] = v;
    if (i >= 4 && i < 8) g_ro[i - 4] = v;
}

u32 sh4_t(void) { return g_t; }

void sh4_set_t(u32 v) { g_t = v & 1u; }

/* SR (TcSr): set from line I, its interrupt mask reported at exit (line IM); 0 and not reported without a line I. */
static u32 g_sr;
static int g_has_sr;

u32 sh4_stc_sr(void) { return g_sr; }

void sh4_ldc_sr(u32 v) { g_sr = v & 0x700083f3u; }

void tcrt_set_sr(u32 imask)
{
    g_sr = 0x40000000u | (imask & 15u) << 4;
    g_has_sr = 1;
}

void tcrt_report_sr(FILE *out)
{
    if (g_has_sr) fprintf(out, "IM %x\n", g_sr >> 4 & 15u);
}

/* Listed hardware (TcHw): the registers and values of header lines Y, and GBR as state after a header line Z (0 at
 * each vector's entry, reported at exit as GB). A read of any other register is a fault, as in the original's run. */
#define TCRT_MAX_HW 16
static struct { u32 reg, value; } g_hw[TCRT_MAX_HW];
static u32 g_nhw;
static u32 g_gbr;
static int g_has_gbr;

void tcrt_add_hw(u32 reg, u32 value)
{
    if (g_nhw < TCRT_MAX_HW) {
        g_hw[g_nhw].reg = reg;
        g_hw[g_nhw++].value = value;
    }
}

void tcrt_set_gbr_state(void) { g_has_gbr = 1; }

u32 tc_hw_read32(u32 reg)
{
    for (u32 i = 0; i < g_nhw; i++)
        if (g_hw[i].reg == reg) return g_hw[i].value;
    fault("read", reg);
    return 0;
}

u32 sh4_stc_gbr(void) { return g_gbr; }

void sh4_ldc_gbr(u32 v) { g_gbr = v; }

void tcrt_report_gbr(FILE *out)
{
    if (g_has_gbr) fprintf(out, "GB %08x\n", g_gbr);
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
    if (!tab || size != (long)sizeof g_fsca) {
        fprintf(stderr, "fsca table %s: %ld bytes, not %u (both tables: python -m core.fsca)\n", fsca_table, size,
                (unsigned)sizeof g_fsca);
        return 0;
    }
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
    g_flag_seen = 0;
    g_nfaults = 0;
    g_code_read = 0;
    tcrt_fpu_reset();
    tcrt_strict_reset();
    memset(g_gs, 0, sizeof g_gs);
    memset(g_ro, 0, sizeof g_ro);
    g_t = 0;
    memset(g_low, 0, sizeof g_low);
    memset(g_rets, 0, sizeof g_rets);
    memset(g_draws, 0, sizeof g_draws);
    g_retx = 0;
    g_has_retx = 0;
    tcrt_halted = 0;
    g_sr = 0;
    g_has_sr = 0;
    g_gbr = 0;
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

void tcrt_set_ro(const u32 r4_7[4]) { memcpy(g_ro, r4_7, sizeof g_ro); }

void tcrt_set_ro_mask(u32 mask) { g_ro_mask = mask; }

void tcrt_set_t_export(void) { g_t_export = 1; }

void tcrt_report_t(FILE *out)
{
    if (g_t_export && !tcrt_halted) fprintf(out, "RT %u\n", g_t);
}

void tcrt_set_area5(void) { g_area5 = 1; }

void tcrt_report_ro(FILE *out)
{
    if (!g_ro_mask || tcrt_halted) return;
    fprintf(out, "RO %08x %08x %08x %08x\n", g_ro[0], g_ro[1], g_ro[2], g_ro[3]);
}

void tcrt_set_low(const u32 bits[4]) { memcpy(g_low, bits, sizeof g_low); }

u32 tcrt_low(int i) { return (i >= 0 && i < 4) ? g_low[i] : 0; }

void tcrt_set_retx(u32 v)
{
    g_retx = v;
    g_has_retx = 1;
}

void tcrt_set_call_returns(const u32 rets[TCRT_RETS]) { memcpy(g_rets, rets, sizeof g_rets); }

static int cmp_u32(const void *a, const void *b)
{
    u32 x = *(const u32 *)a, y = *(const u32 *)b;
    return x < y ? -1 : (x > y);
}

void tcrt_report(FILE *out, u32 r0, u32 fr0_bits)
{
    fprintf(out, "RET %08x %08x\n", r0, fr0_bits);
    if (g_has_retx) fprintf(out, "RETX %08x\n", g_retx);
    if (tcrt_halted) fprintf(out, tcrt_halted == 2 ? "HALT SPIN\n" : "HALT\n");
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
        u32 addr = start + TCRT_RAM_BASE;  /* all of RAM: core/compare.judged leaves out the original's own frame */
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
    tcrt_strict_report(out); /* CS lines; XR: the exit frame of a run that returned (tc_strict.h) */
    if ((tcrt_strict_flags() & 8) && !tcrt_halted) {
        fprintf(out, "XR %08x", TCRT_SP); /* r15: the C keeps no stack pointer, its frames balance */
        for (int k = 0; k < 7; k++) fprintf(out, " %08x", g_gs[k]);
        for (int k = 12; k < 16; k++) fprintf(out, " %08x", tcrt_fr_bits(k));
        fputc('\n', out);
    }
    tcrt_report_xf(out);
    for (u32 k = 0; k < g_nfaults && k < MAX_FAULTS; k++) fprintf(out, "FAULT %s\n", g_faults[k]);
    if (g_code_read) fprintf(out, "CODEREAD %08x\n", g_code_read);
}
