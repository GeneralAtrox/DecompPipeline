/* The SH-4 instructions readable C uses through rd_rt.h, for the port: the same arithmetic as the pipeline's
 * readable-C runtime (single-precision SSE operations in the SH-4's order), with the FPU state as plain globals. */
#include <float.h>
#include <intrin.h>
#include <math.h>
#include <stdlib.h>
#include <string.h>
#include <emmintrin.h>
#include <xmmintrin.h>
#include "port.h"
#include "platform.h"

#define MODE_FR 0x00200000u
#define MODE_SZ 0x00100000u
#define MODE_PR 0x00080000u

static u32 g_xf[16];
static u32 g_fr[16];
static u32 g_mode;  /* FPSCR.FR | SZ | PR in their FPSCR bit positions */
static u32 g_fpscr = 0x00040001u; /* FPSCR's other fields: sh4_rt_set_fpu_mode's DN=1, RM=1 until sh4_lds_fpscr */
static u32 g_gs[7]; /* r8..r14 */
static u32 g_ro[4]; /* r4..r7 as the last tc_call passed them, or as a translated callee handed one back (TcOut) */
static u32 g_t; /* T: as a translated caller handed it over, or as a translated callee handed it back (TcTflag) */
extern const unsigned int port_fsca_sin[2][65536]; /* fsca: gen fsca_table.c (core/fsca.py): [0] nearest, [1] to zero */

/* Unions, not memcpy: the kit builds with the judge's /O1, which has no /Oi, so a memcpy here is a CRT call. */
typedef union {
    u32 u;
    f32 f;
} Bits;
static u32 f2u(f32 f)
{
    Bits b;
    b.f = f;
    return b.u;
}
static f32 u2f(u32 u)
{
    Bits b;
    b.u = u;
    return b.f;
}

/* The SH-4's ftrc: truncation toward zero, saturating at 0x7fffffff above the int range and 0x80000000 below it;
 * a NaN gives 0x80000000 (as the judge's runtime, tc_rt.c, does since r15). */
s32 sh4_ftrc(f32 x)
{
    if (x != x) return (s32)0x80000000u;
    double d = x;
    if (d >= 2147483648.0) return 0x7fffffff;
    if (d <= -2147483648.0) return (s32)0x80000000u;
    return (s32)d;
}

f32 sh4_fsrra(f32 x) { return _mm_cvtss_f32(_mm_div_ss(_mm_set_ss(1.0f), _mm_sqrt_ss(_mm_set_ss(x)))); }

f32 sh4_fsqrt_rm(f32 x) { return _mm_cvtss_f32(_mm_sqrt_ss(_mm_set_ss(x))); }

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

/* fsca: the table of the rounding the FPU runs in, MXCSR.RC (sh4_rt_set_fpu_mode: FPSCR.RM=1, toward zero), as the
 * judge's runtimes pick it. The SH-4 rounds no way up or down: those end the run. */
void sh4_fsca(u32 fpul, f32 *s, f32 *c)
{
    u32 rc = _mm_getcsr() & 0x6000u, i = fpul & 0xffffu;
    if (rc != 0u && rc != 0x6000u) port_exit("fsca: MXCSR rounds up or down, no FPSCR.RM does: no fsca table", 1);
    const unsigned int *tab = port_fsca_sin[rc != 0u];
    *s = u2f(tab[i]);
    *c = u2f(tab[(i + 0x4000u) & 0xffffu]); /* the cosine is the sine a quarter turn on */
}

/* fipr FVm,FVn and ftrv XMTRX,FVn: one inner product rounded once, as the reference emulator (Flycast: sh4_fpu.cpp,
 * shil_canonical.h innerProduct) computes the SH-4's (the software manual's FIPR and FTRV: one hardware inner product,
 * no rounded intermediate values): each product in double (exact), the four summed in element order 0..3 in double,
 * the sum converted once to single, all in MXCSR's mode (RC from FPSCR.RM, DAZ|FTZ from FPSCR.DN), as the oracle's
 * TcRound.fipr. SSE2 operations, never the x87 or a fused multiply-add. */
static __m128d widen(f32 x) { return _mm_cvtss_sd(_mm_setzero_pd(), _mm_set_ss(x)); }

static __m128d dmul(f32 a, f32 b) { return _mm_mul_sd(widen(a), widen(b)); }

f32 sh4_fipr(const f32 a[4], const f32 b[4])
{
    __m128d t = dmul(a[0], b[0]);
    t = _mm_add_sd(t, dmul(a[1], b[1]));
    t = _mm_add_sd(t, dmul(a[2], b[2]));
    t = _mm_add_sd(t, dmul(a[3], b[3]));
    return _mm_cvtss_f32(_mm_cvtsd_ss(_mm_setzero_ps(), t));
}

void sh4_ftrv(f32 v[4])
{
    f32 out[4];
    for (int row = 0; row < 4; row++) { /* XMTRX is column-major: row r is xf r, r+4, r+8, r+12 */
        f32 m[4] = {u2f(g_xf[row]), u2f(g_xf[row + 4]), u2f(g_xf[row + 8]), u2f(g_xf[row + 12])};
        out[row] = sh4_fipr(m, v);
    }
    for (int i = 0; i < 4; i++) v[i] = out[i];
}

f32 sh4_xf(int i) { return (i >= 0 && i < 16) ? u2f(g_xf[i]) : 0.0f; }

void sh4_set_xf(int i, f32 v)
{
    if (i >= 0 && i < 16) g_xf[i] = f2u(v);
}

f32 sh4_fr(int i) { return (i >= 0 && i < 16) ? u2f(g_fr[i]) : 0.0f; }

void sh4_set_fr(int i, f32 v)
{
    if (i >= 0 && i < 16) g_fr[i] = f2u(v);
}

void sh4_frchg(void)
{
    if (g_mode & MODE_PR) return;
    for (int i = 0; i < 16; i++) {
        u32 t = g_fr[i];
        g_fr[i] = g_xf[i];
        g_xf[i] = t;
    }
    g_mode ^= MODE_FR;
}

void sh4_fschg(void)
{
    if (!(g_mode & MODE_PR)) g_mode ^= MODE_SZ;
}

void sh4_fpchg(void) { g_mode ^= MODE_PR; }

u32 sh4_sz(void) { return (g_mode & MODE_SZ) != 0u; }

/* Double precision (FPSCR.PR=1) on fr pairs: the judge's definitions (sh4_double.h), PR from g_mode */
#define SH4D_PR() ((g_mode & MODE_PR) != 0u)
#define SH4D_UNDEFINED(s) port_log("%s: undefined on the SH-4, registers left as they were\n", s)
#include "../../targets/dreamcast_sh4/runtime/sh4_double.h"

void sh4_ftrv_fv(int n)
{
    if (n < 0 || n > 12 || (n & 3)) return;
    f32 v[4];
    for (int i = 0; i < 4; i++) v[i] = u2f(g_fr[n + i]);
    sh4_ftrv(v);
    for (int i = 0; i < 4; i++) g_fr[n + i] = f2u(v[i]);
}

void sh4_fipr_fv(int m, int n)
{
    if (m < 0 || m > 12 || (m & 3) || n < 0 || n > 12 || (n & 3)) return;
    f32 a[4], b[4];
    for (int i = 0; i < 4; i++) {
        a[i] = u2f(g_fr[m + i]);
        b[i] = u2f(g_fr[n + i]);
    }
    g_fr[n + 3] = f2u(sh4_fipr(a, b));
}

u32 sh4_r(int i) { return (i >= 8 && i < 15) ? g_gs[i - 8] : (i >= 4 && i < 8) ? g_ro[i - 4] : 0; }

void sh4_set_r(int i, u32 v)
{
    if (i >= 8 && i < 15) g_gs[i - 8] = v;
    if (i >= 4 && i < 8) g_ro[i - 4] = v;
}

u32 sh4_t(void) { return g_t; }

void sh4_set_t(u32 v) { g_t = v & 1u; }

/* SR (rd_rt.h): a word only; the port's interrupts (the vertical-blank hook) do not look at its mask. */
static u32 g_sr = 0x400000f0u;

u32 sh4_stc_sr(void) { return g_sr; }

void sh4_ldc_sr(u32 v) { g_sr = v & 0x700083f3u; }

/* Listed hardware (rd_rt.h, project.json "hw_ops"): each register's value on a retail Dreamcast, as
 * targets/dreamcast_sh4/target.json "hw_reads" (tests/test_hw_ops.py checks they agree). GBR is a word. */
static const struct { u32 reg, value; } k_hw[] = {
    {0xa05f689cu, 0x0000000bu}, /* SB_SBREV, the system bus revision */
};
static u32 g_gbr;

u32 tc_hw_read32(u32 reg)
{
    for (size_t i = 0; i < sizeof k_hw / sizeof k_hw[0]; i++)
        if (k_hw[i].reg == reg) return k_hw[i].value;
    port_log("tc_hw_read32: %08x is no listed hardware register\n", reg);
    return 0;
}

/* A listed hardware register's store and the listed cache block ops: the port has no such hardware (its own code
 * does what the register drove), and its memory is coherent. */
void tc_hw_write32(u32 reg, u32 value)
{
    (void)reg;
    (void)value;
}

void tc_hw_write16(u32 reg, u16 value)
{
    (void)reg;
    (void)value;
}

void sh4_ocbi(u32 addr) { (void)addr; }

void sh4_ocbp(u32 addr) { (void)addr; }

void sh4_ocbwb(u32 addr) { (void)addr; }

u32 sh4_stc_gbr(void) { return g_gbr; }

/* sts / lds FPSCR (rd_rt.h): the SH-4 keeps bits 0..21; RM and DN set the host's rounding and denormal flushing as
 * sh4_rt_set_fpu_mode does (RM 0 nearest, 1 toward zero; the reserved 2 and 3 as 0). The enables cannot trap here. */
u32 sh4_sts_fpscr(void) { return (g_fpscr & ~(MODE_FR | MODE_SZ | MODE_PR)) | g_mode; }

/* The x87's precision as FPSCR.PR: 24 bits for single, 53 for double. 32-bit code keeps a float that a function
 * returned (rdf32, sh4_fr) on the x87 and goes on computing there; at the default 53 bits `(a * b) * c` rounds once
 * where the SH-4's two fmuls round twice (TC's Body_InitK10Dynamics: 1 ulp, then a vehicle drifts). x64 has no x87. */
#if defined(_M_IX86)
#define X87_PC(fpscr) (((fpscr) & MODE_PR) ? _PC_53 : _PC_24)
#define X87_MCW_PC _MCW_PC
#else
#define X87_PC(fpscr) 0u
#define X87_MCW_PC 0u
#endif

void sh4_lds_fpscr(u32 v)
{
    unsigned int x87 = 0;
    v &= 0x003fffffu;
    g_fpscr = v & ~(MODE_FR | MODE_SZ | MODE_PR);
    g_mode = v & (MODE_FR | MODE_SZ | MODE_PR);
    _mm_setcsr((_mm_getcsr() & ~0xe040u) | ((v >> 18) & 1u ? 0x8040u : 0u) | ((v & 3u) == 1u ? 0x6000u : 0u));
    __control87_2(((v & 3u) == 1u ? _RC_CHOP : _RC_NEAR) | X87_PC(v), _MCW_RC | X87_MCW_PC, &x87, NULL);
}

void sh4_ldc_gbr(u32 v) { g_gbr = v; }

/* The register state an in-between frame (frame_extra.c) must leave as it found it. */
void sh4_rt_extra_state(void)
{
    port_extra_state(g_xf, sizeof g_xf);
    port_extra_state(g_fr, sizeof g_fr);
    port_extra_state(&g_mode, sizeof g_mode);
    port_extra_state(&g_fpscr, sizeof g_fpscr);
    port_extra_state(g_gs, sizeof g_gs);
    port_extra_state(&g_gbr, sizeof g_gbr);
}

/* fsca's tables are the hardware's, rebuilt from the ROM's rules (tools/fsca_rom.py). check_table: the decompiler's
 * state/fsca_table.bin (2 x 65,536 x {sin, cos} bits, round to nearest then toward zero: core/fsca.py), which must hold
 * the same values; the one-table file of before (512 KB) is refused. */
int sh4_rt_init(const char *check_table)
{
    if (!check_table) return 1;
    FILE *f = NULL;
    if (fopen_s(&f, check_table, "rb") != 0 || !f) {
        port_log("fsca: cannot open %s\n", check_table);
        return 0;
    }
    u32 ref[2], diff = 0, i = 0;
    for (; i < 2 * 65536 && fread(ref, sizeof ref, 1, f) == 1; i++) {
        const unsigned int *tab = port_fsca_sin[i >> 16];
        diff += ref[0] != tab[i & 0xffffu] || ref[1] != tab[(i + 0x4000u) & 0xffffu];
    }
    int extra = fgetc(f) != EOF;
    fclose(f);
    port_log("fsca: tables vs %s: %u of %u entries differ\n", check_table, diff, i);
    if (i != 2 * 65536 || extra) port_log("fsca: %s is not 2 x 65,536 entries (python -m core.fsca)\n", check_table);
    return diff == 0 && i == 2 * 65536 && !extra;
}

/* FPSCR 0x00040001 (target.json fpscr_entry): DN=1 (denormals flush to zero: DAZ|FTZ), RM=1 (round toward zero).
 * The x87 too: 32-bit code converts integers to float on it, and its precision is PR's (X87_PC). */
void sh4_rt_set_fpu_mode(void)
{
    unsigned int x87 = 0;
    _mm_setcsr((_mm_getcsr() & ~0x6000u) | 0x8040u | 0x6000u);
    __control87_2(_RC_CHOP | X87_PC(0u), _MCW_RC | X87_MCW_PC, &x87, NULL);
}

/* tc_halt (rd_rt.h): the original's endless loop (`bra .`, an error halt). The port ends the run there instead of
 * hanging: exit code 1 (a failure) and an EXIT line starting "HALT" that names the caller, which tools/lockstep.py,
 * tools/hw_check.py and tools/regress.py report. */
extern const char __ImageBase;
void tc_halt(void)
{
    char why[96];
    snprintf(why, sizeof why, "HALT: the original's endless loop (tc_halt), called from port.exe+%08x",
             (u32)((uintptr_t)_ReturnAddress() - (uintptr_t)&__ImageBase));
    port_exit(why, 1);
    for (;;) {
    } /* port_exit returns only while another thread is already ending the process */
}

/* tc_spin (rd_rt.h): the body of the original's wait loop on state an interrupt or the hardware changes (TcSpin).
 * The port waits for the next vertical blank (which runs the game's vertical-blank handler, as the interrupt did);
 * the loop then tests its condition again. */
void tc_spin(void) { port_wait_vblank(); }
