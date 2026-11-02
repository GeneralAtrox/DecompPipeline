/* xemu: the run of one vector (tc_thunk), the function's config, instruction fetch, the mocked calls, the store
 * queues and the hand-over of state to and from the runtime.
 *
 * The runner calls tc_thunk once per vector after the runtime has taken the vector's lines. The entry state is
 * the oracle's (TcOracle.setEntry): r4..r7 and fr4..fr11 from lines R and F, r0..r3 from L, r8..r14 from G,
 * fr12..fr15 from H, fr0..fr15 from Q (over F), xf0..xf15 from X, r15 = 0x8cffe000, PR = the sentinel 0x8cfff000,
 * everything else 0. A transfer to an address outside the function's body is a call to a mocked callee (tc_call,
 * then on at PR, as the oracle does); reaching the sentinel ends the run.
 *
 * The config file named by environment variable XEMU_FN (tools/xemu_check.py writes it) has one item per line:
 *   entry ADDR | body LO HI (half-open; several) | retx rN or frN (meta ret_reg) | u64 (r1 is RETX) | fpscr HEX
 *   | szx CALLEE XY (TcFpscr.Sz: the callee's FPSCR.SZ at return entered with 0, X, and 1, Y; another digit: unknown)
 * The machine code comes from the image file itself (the runner's first argument), not through rd16: the runtime
 * reads of code bytes are the C's rule R8 and are reported. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <xmmintrin.h>
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include "tc_rt_internal.h"
#include "xe.h"
#define STEP_LIMIT 4000000L /* instructions; the oracle stops at 500000 steps (TcBase.STEP_LIMIT) */
#define IMAGE_BASE 0x8c010000u

static int g_loaded;
static u8 *g_image;
static u32 g_image_size;
static u32 g_entry, g_body[XE_MAX_BODY][2], g_nbody, g_fpscr_entry = 0x00040001u, g_szx[32][2], g_nszx;
static int g_retx_reg = -1, g_retx_fr, g_u64;
static u32 g_rt_mode; /* the runtime's FPU mode bits as xemu last set them (0 after its reset) */
static u32 g_rc_entry; /* MXCSR rounding control at entry (the runtime's, from line P) */
static int g_trace;   /* environment variable XEMU_TRACE set: each instruction and the registers after it, to stderr */

void xe_fail(const char *why, u32 pc, u32 op)
{
    fprintf(stderr, "xemu: %s at %08x (op %04x)\n", why, pc, op);
    RaiseException(0xe0580001u, 0, 0, NULL);
}

static u8 *read_all(const char *path, u32 *size)
{
    FILE *f = NULL;
    if (fopen_s(&f, path, "rb") != 0 || !f) return NULL;
    fseek(f, 0, SEEK_END);
    long n = ftell(f);
    fseek(f, 0, SEEK_SET);
    u8 *buf = n > 0 ? (u8 *)malloc((size_t)n + 1) : NULL;
    if (buf && fread(buf, 1, (size_t)n, f) != (size_t)n) {
        free(buf);
        buf = NULL;
    }
    fclose(f);
    *size = buf ? (u32)n : 0;
    if (buf) buf[n] = 0;
    return buf;
}

static void load_config(void)
{
    char path[1024];
    DWORD n = GetEnvironmentVariableA("XEMU_FN", path, sizeof path);
    if (n == 0 || n >= sizeof path) xe_fail("no XEMU_FN config", 0, 0);
    u32 size = 0;
    char *text = (char *)read_all(path, &size);
    g_image = __argc > 1 ? read_all(__argv[1], &g_image_size) : NULL;
    if (!text || !g_image) xe_fail("cannot read the config or the image", 0, 0);
    char *ctx = NULL;
    for (char *ln = strtok_s(text, "\r\n", &ctx); ln; ln = strtok_s(NULL, "\r\n", &ctx)) {
        char *p = ln;
        if (strncmp(ln, "entry ", 6) == 0) {
            g_entry = (u32)strtoul(ln + 6, NULL, 16);
        } else if (strncmp(ln, "body ", 5) == 0) {
            if (g_nbody == XE_MAX_BODY) xe_fail("more body ranges than XE_MAX_BODY", 0, 0);
            g_body[g_nbody][0] = (u32)strtoul(ln + 5, &p, 16);
            g_body[g_nbody++][1] = (u32)strtoul(p, NULL, 16);
        } else if (strncmp(ln, "retx fr", 7) == 0) {
            g_retx_reg = atoi(ln + 7);
            g_retx_fr = 1;
        } else if (strncmp(ln, "retx r", 6) == 0) {
            g_retx_reg = atoi(ln + 6);
        } else if (strcmp(ln, "u64") == 0) {
            g_u64 = 1;
        } else if (strncmp(ln, "fpscr ", 6) == 0) {
            g_fpscr_entry = (u32)strtoul(ln + 6, NULL, 16);
        } else if (strncmp(ln, "szx ", 4) == 0 && g_nszx < 32) {
            g_szx[g_nszx][0] = (u32)strtoul(ln + 4, &p, 16), g_szx[g_nszx++][1] = (u32)strtoul(p, NULL, 16);
        }
    }
    free(text);
    if (!g_entry || !g_nbody) xe_fail("config has no entry or body", 0, 0);
    char model[256];
    g_trace = GetEnvironmentVariableA("XEMU_TRACE", model, sizeof model) != 0;
    n = GetEnvironmentVariableA("XEMU_MODEL", model, sizeof model);
    if (n && n < sizeof model) {
        xe_model_ftrc = strstr(model, "ftrcnan") ? 2 : strstr(model, "ftrc") ? 1 : 0;
        xe_model_ghmem = strstr(model, "ghmem") != NULL;
        xe_model_sqwt = strstr(model, "sqwt") != NULL; xe_model_sqlog = strstr(model, "sqlog") != NULL;
    }
    g_loaded = 1;
}

u16 xe_fetch(u32 pc)
{
    u32 off = tcrt_fold(pc) - IMAGE_BASE;
    if (off >= g_image_size || off + 2 > g_image_size || (pc & 1)) xe_fail("fetch outside the image", pc, 0);
    return (u16)(g_image[off] | (g_image[off + 1] << 8));
}

/* A literal-pool word (mov.w/mov.l @(disp,PC)): program text, from the image as the oracle has it (TcText: a
 * vector's patch over a pool word gets the image's bytes back), and not a data read of code (rule R8 is the C's) */
u32 xe_rd_text(u32 a, int size)
{
    u32 off = tcrt_fold(a) - IMAGE_BASE;
    if (off >= g_image_size || off + (u32)size > g_image_size) return xe_rd(a, size);
    u32 v = 0;
    for (int i = size - 1; i >= 0; i--) v = v << 8 | g_image[off + (u32)i];
    return v;
}

static int in_body(u32 pc)
{
    for (u32 i = 0; i < g_nbody; i++) {
        if (pc >= g_body[i][0] && pc < g_body[i][1]) return 1;
    }
    return 0;
}

/* ---------- FPSCR and SR ---------- */
void xe_set_fpscr(Xe *x, u32 v)
{
    v &= 0x003fffffu;
    if ((v ^ x->fpscr) & XE_FPSCR_FR) {
        for (int i = 0; i < 16; i++) {
            u32 t = x->fr[i];
            x->fr[i] = x->xf[i];
            x->xf[i] = t;
        }
    }
    /* rounding: the entry mode is the runtime's (MXCSR from line P); another RM rounds as it says */
    u32 rc = (v & 3u) == (g_fpscr_entry & 3u) ? g_rc_entry : (v & 3u) == 1u ? 0x6000u : 0u;
    _mm_setcsr((_mm_getcsr() & ~0xe040u) | rc | ((v & XE_FPSCR_DN) ? 0x8040u : 0u));
    x->fpscr = v;
}

u32 xe_sr(const Xe *x)
{
    return (x->sr_rest & ~0x303u) | x->t | x->s << 1 | x->q << 8 | x->m << 9;
}

void xe_set_sr(Xe *x, u32 v)
{
    x->sr_rest = v & 0x700083f3u;
    x->t = v & 1;
    x->s = (v >> 1) & 1;
    x->q = (v >> 8) & 1;
    x->m = (v >> 9) & 1;
}

/* ---------- state to and from the runtime ---------- */
static f32 bits_f(u32 u)
{
    f32 f;
    memcpy(&f, &u, 4);
    return f;
}

static void sync_mode(u32 want)
{
    want &= XE_FPSCR_FR | XE_FPSCR_SZ | XE_FPSCR_PR;
    if (g_rt_mode == want) return;
    if (g_rt_mode & XE_FPSCR_PR) sh4_fpchg();
    g_rt_mode &= ~XE_FPSCR_PR;
    if ((g_rt_mode ^ want) & XE_FPSCR_FR) sh4_frchg();
    if ((g_rt_mode ^ want) & XE_FPSCR_SZ) sh4_fschg();
    if (want & XE_FPSCR_PR) sh4_fpchg();
    g_rt_mode = want;
}

static void sync_out(const Xe *x)
{
    for (int i = xe_model_sqlog ? 4 : 8; i < 15; i++) sh4_set_r(i, x->r[i]); /* sqlog: a harness's RO line needs r4..r7 */
    sync_mode(x->fpscr);
    for (int i = 0; i < 16; i++) {
        sh4_set_fr(i, bits_f(x->fr[i]));
        sh4_set_xf(i, bits_f(x->xf[i]));
    }
}

static void call(Xe *x, u32 target)
{
    TcArgs a;
    memset(&a, 0, sizeof a);
    for (int i = 0; i < 4; i++) {
        a.r[i] = x->r[4 + i];
        a.rlo[i] = x->r[i];
    }
    memcpy(a.fr, x->fr + 4, sizeof a.fr);
    u32 sp = tcrt_fold(x->r[15]);
    for (u32 i = 0; i < 8; i++) {
        u32 w = sp + 4 * i;
        if (w >= IMAGE_BASE && w <= 0x8d000000u - 4) xe_ghmem_read(w, 4); /* TcOracle.callRecord reads them */
        a.s[i] = (w >= IMAGE_BASE && w <= 0x8d000000u - 4) ? rd32(w) : 0;
    }
    sync_out(x);
    u32 before = xe_sq_ptr(x);
    if (tcrt_sq_window) xe_sq_callee(x, target);
    u32 rv = tc_call(target, &a);
    for (u32 k = 0, d; k < g_nszx; k++) /* a callee that returns with another FPSCR.SZ (TcFpscr.Sz) */
        if (g_szx[k][0] == target && (d = g_szx[k][1] >> (x->fpscr & XE_FPSCR_SZ ? 0 : 4) & 15u) < 2u)
            x->fpscr = (x->fpscr & ~XE_FPSCR_SZ) | (d ? XE_FPSCR_SZ : 0u);
    x->r[0] = rv;
    x->fr[0] = rv;
    for (int i = 8; i < 15; i++) x->r[i] = sh4_r(i);
    xe_sq_moved(x, before);
}

static void trace(const Xe *x, u32 pc)
{
    fprintf(stderr, "%08x %04x T%u", pc, xe_fetch(pc), x->t);
    for (int i = 0; i < 16; i++) fprintf(stderr, " %08x", x->r[i]);
    fprintf(stderr, " pr %08x fpul %08x |", x->pr, x->fpul);
    for (int i = 0; i < 16; i++) fprintf(stderr, " %08x", x->fr[i]);
    fputc('\n', stderr);
}

void tc_thunk(const u32 *r, const f32 *fr, u32 *out_r0, f32 *out_fr0)
{
    static Xe x;
    if (!g_loaded) load_config();
    memset(&x, 0, sizeof x);
    xe_ghmem_begin(IMAGE_BASE, g_image_size);
    g_rt_mode = 0;
    g_rc_entry = _mm_getcsr() & 0x6000u;
    for (int i = 0; i < 4; i++) {
        x.r[i] = tcrt_low(i);
        x.r[4 + i] = r[i];
    }
    for (int i = 8; i < 15; i++) x.r[i] = sh4_r(i);
    x.gbr = sh4_stc_gbr(); /* line b (0 without it) */
    x.r[15] = TCRT_SP;
    x.pr = XE_SENTINEL;
    for (int i = 0; i < 16; i++) {
        f32 f = sh4_fr(i), g = sh4_xf(i);
        memcpy(&x.fr[i], &f, 4);
        memcpy(&x.xf[i], &g, 4);
    }
    if (!tcrt_fpu_state()) memcpy(x.fr + 4, fr, 8 * sizeof(u32));
    x.fpscr = (g_fpscr_entry & ~(XE_FPSCR_FR | XE_FPSCR_SZ | XE_FPSCR_PR)) | (sh4_sz() ? XE_FPSCR_SZ : 0u); /* line z */
    xe_sq_entry(&x);
    u32 pc = g_entry;
    long steps = 0;
    for (;;) {
        pc = tcrt_fold(pc);
        if (pc == XE_SENTINEL) break;
        if (!in_body(pc)) {
            call(&x, pc);
            pc = x.pr;
            continue;
        }
        if (++steps > STEP_LIMIT) xe_fail("step limit", pc, 0);
        x.pc = pc;
        x.slot = 0;
        x.branch = 0;
        xe_exec(&x, xe_fetch(pc));
        if (g_trace) trace(&x, pc);
        if (x.branch == 1) {
            u32 to = x.target;
            u16 op = xe_fetch(pc + 2);
            x.pc = pc + 2;
            x.slot = 1;
            x.branch = 0;
            xe_exec(&x, op);
            if (g_trace) trace(&x, pc + 2);
            if (x.branch) xe_fail("a branch in a delay slot", pc + 2, op);
            pc = to;
        } else {
            pc = x.branch == 2 ? x.target : pc + 2;
        }
    }
    sync_out(&x);
    *out_r0 = x.r[0];
    memcpy(out_fr0, &x.fr[0], 4);
    if (g_u64) tcrt_set_retx(x.r[1]);
    if (g_retx_reg >= 0) tcrt_set_retx(g_retx_fr ? x.fr[g_retx_reg & 15] : x.r[g_retx_reg & 15]);
}
