/* Test runner: executes the decompiled function on every vector and prints its observable effects.
 *
 * usage: runner <image> <code_ranges> <fsca_table> <vectors.txt> <dev|hidden|all> <out.txt>
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <windows.h>
#include "tc_rt_internal.h"

/* Generated per function: unpacks argument registers and calls fn_<addr>. */
void tc_thunk(const u32 *r, const f32 *fr, u32 *out_r0, f32 *out_fr0);

static int hexval(int c)
{
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

/* Line N after V is one of two lines: the stricter judge's key "N seed vid" (TcStrict, two fields) or the entry FPSCR
 * "N fpscr" (TcFpscr, one field). Told apart by the second field, so neither shadows the other (Toy Commander
 * 8c0611d8 and Spawn's fpscr_state functions had FS never printed while every N went to the strict judge). */
static int strict_key(const char *line)
{
    char *p;
    strtoull(line + 1, &p, 16);
    while (*p == ' ') p++;
    return hexval(*p) >= 0;
}

/* Fills the stack the function will run on with one pattern before each vector, as the GameCube runner does. A
 * TcArgs slot the C never sets (Grandia 0605a282's mutants without `a.r[1] = 0`) then reads the same bits on every
 * run and machine, with the strict flags on or off, not what the last vector or the runner's own code left there:
 * with the flags on, that happened to be the expected 0 and two mutants passed by luck. */
#define TC_PAINT 0xa5a5a5a5a5a5a5a5ull
static __declspec(noinline) void paint_stack(void)
{
    volatile unsigned long long pad[1 << 13];
    for (int i = 0; i < (1 << 13); i++) pad[i] = TC_PAINT;
}

static int run_one(const u32 *r, const f32 *fr, u32 *r0, f32 *fr0)
{
    if (setjmp(tcrt_halt_jmp)) return 0; /* tc_halt: the original's endless loop (TcExit), reported as HALT */
    __try {
        tc_thunk(r, fr, r0, fr0);
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return (int)GetExceptionCode();
    }
    return 0;
}

static FILE *open_file(const char *path, const char *mode)
{
    FILE *f = NULL;
    return fopen_s(&f, path, mode) == 0 ? f : NULL;
}

int main(int argc, char **argv)
{
    if (argc != 7) {
        fprintf(stderr, "usage: runner image code_ranges fsca vectors set out\n");
        return 2;
    }
    if (!tcrt_init(argv[1], argv[2], argv[3])) {
        fprintf(stderr, "runner: runtime init failed\n");
        return 2;
    }
    FILE *in = open_file(argv[4], "r");
    FILE *out = open_file(argv[6], "w");
    if (!in || !out) {
        fprintf(stderr, "runner: cannot open vectors or output\n");
        return 2;
    }
    const char *want = argv[5];
    static char line[1 << 16];
    static u8 buf[1 << 15];
    int id = -1, active = 0;
    u32 r[4] = {0}, xf[16] = {0}, rets[TCRT_RETS] = {0};
    /* fr12..fr15 from line H (reported at exit as FH only when the vector had H). Static, like line and buf:
     * main's frame stays the size it was, so C that reads an uninitialized local behaves as before. */
    static u32 fh[4];
    static int has_fh;
    static u32 low[4], gs[7]; /* r0..r3 (line L) and r8..r14 (line G, reported at exit as GH) */
    static int has_gs;
    static u32 fq[16]; /* fr0..fr15 (line Q: the whole FPU is state; reported at exit as QH) */
    static int has_fq;
    f32 fr[8] = {0};
    while (fgets(line, sizeof line, in)) {
        if (line[0] == 'P') { /* entry FPSCR, before the first vector (TcVectors.fpscrLine) */
            tcrt_set_fpscr((u32)strtoul(line + 1, NULL, 16));
        } else if (line[0] == 't') { /* T compared at exit, before the first vector (TcTflag) */
            tcrt_set_t_export();
        } else if (line[0] == 'y') { /* area-5 reads return 0, before the first vector (TcHw "read area5") */
            tcrt_set_area5();
        } else if (line[0] == 'O') { /* the r4..r7 compared at exit, before the first vector (TcOut) */
            tcrt_set_ro_mask((u32)strtoul(line + 1, NULL, 10));
        } else if (line[0] == 'K') { /* a callee changing r8..r14 or r4..r7, before the first vector (TcLive) */
            char *p = line + 1;
            u32 t = (u32)strtoul(p, &p, 16), reg = (u32)strtoul(p, &p, 10), n = (u32)strtoul(p, &p, 10), d[4] = {0};
            for (u32 i = 0; i < n && i < 4; i++) d[i] = (u32)strtoul(p, &p, 16);
            tcrt_add_live(t, reg, n, d);
        } else if (line[0] == 'S') { /* a state pointer into the store-queue window, before the first vector (TcSq) */
            tcrt_set_sq_window();
            unsigned i = 0; /* the line itself: its callee words are for xemu */
            for (; i + 1 < sizeof tcrt_sq_line && line[i]; i++) tcrt_sq_line[i] = line[i];
            tcrt_sq_line[i] = 0;
        } else if (line[0] == 'Y') { /* a listed hardware register and its value, before the first vector (TcHw) */
            char *p = line + 1;
            u32 reg = (u32)strtoul(p, &p, 16);
            tcrt_add_hw(reg, (u32)strtoul(p, &p, 16));
        } else if (line[0] == 'Z') { /* GBR is state, before the first vector (TcHw): reported at exit as GB */
            tcrt_set_gbr_state();
        } else if (line[0] == 'f') { /* a run flag, before the first vector (TcCalls): "f word callee n" */
            char *p = line + 1;
            u32 word = (u32)strtoul(p, &p, 16), callee = (u32)strtoul(p, &p, 16);
            tcrt_set_run_flag(word, callee, (u32)strtoul(p, &p, 10));
        } else if (line[0] == 'J' || line[0] == 'U') { /* the stricter judge's flags and facts (tc_strict.h) */
            tcrt_strict_line(line);
        } else if (line[0] == 'V') {
            char set[16] = {0};
            sscanf_s(line + 2, "%d %15s", &id, set, (unsigned)sizeof set);
            active = strcmp(want, "all") == 0 || strcmp(want, set) == 0;
            if (active) {
                tcrt_reset();
                memset(fh, 0, sizeof fh);
                has_fh = 0;
                memset(low, 0, sizeof low);
                memset(gs, 0, sizeof gs);
                has_gs = 0;
                has_fq = 0;
                fprintf(out, "V %d %s\n", id, set);
            }
        } else if (!active) {
            continue;
        } else if ((line[0] == 'N' && strict_key(line)) || line[0] == 'W') { /* after V's reset: tc_strict.h */
            tcrt_strict_line(line);
        } else if (line[0] == 'R') {
            sscanf_s(line + 1, "%x %x %x %x", &r[0], &r[1], &r[2], &r[3]);
        } else if (line[0] == 'F') {
            u32 b[8];
            sscanf_s(line + 1, "%x %x %x %x %x %x %x %x", &b[0], &b[1], &b[2], &b[3], &b[4], &b[5], &b[6], &b[7]);
            memcpy(fr, b, sizeof fr);
        } else if (line[0] == 'X') {
            char *p = line + 1;
            for (int i = 0; i < 16; i++) xf[i] = (u32)strtoul(p, &p, 16);
        } else if (line[0] == 'H') {
            char *p = line + 1;
            for (int i = 0; i < 4; i++) fh[i] = (u32)strtoul(p, &p, 16);
            has_fh = 1;
        } else if (line[0] == 'L') {
            char *p = line + 1;
            for (int i = 0; i < 4; i++) low[i] = (u32)strtoul(p, &p, 16);
        } else if (line[0] == 'G') {
            char *p = line + 1;
            for (int i = 0; i < 7; i++) gs[i] = (u32)strtoul(p, &p, 16);
            has_gs = 1;
        } else if (line[0] == 'A') { /* after V's reset: the draws for the K callees' deltas */
            char *p = line + 1;
            u32 dr[16];
            for (int i = 0; i < 16; i++) dr[i] = (u32)strtoul(p, &p, 16);
            tcrt_set_draws(dr);
        } else if (line[0] == 'I') { /* after V's reset: the entry SR mask (TcSr) */
            tcrt_set_sr((u32)strtoul(line + 2, NULL, 16));
        } else if (line[0] == 'N') { /* after V's reset: the entry FPSCR, FPSCR is state (TcFpscr) */
            tcrt_set_fpscr_state((u32)strtoul(line + 1, NULL, 16));
        } else if (line[0] == 'z') { /* after V's reset and N: the entry FPSCR.SZ its caller runs it with (TcFpscr.Sz) */
            if (strtoul(line + 1, NULL, 16) & 0x00100000u) sh4_fschg();
        } else if (line[0] == 'Q') {
            char *p = line + 1;
            for (int i = 0; i < 16; i++) fq[i] = (u32)strtoul(p, &p, 16);
            has_fq = 1;
        } else if (line[0] == 'C') {
            char *p = line + 1;
            memset(rets, 0, sizeof rets); /* 16 words in older harnesses */
            for (int i = 0; i < TCRT_RETS && hexval(*(p += strspn(p, " "))) >= 0; i++) rets[i] = (u32)strtoul(p, &p, 16);
        } else if (line[0] == 'M') {
            char *p = line + 2;
            u32 addr = (u32)strtoul(p, &p, 16);
            while (*p == ' ') p++;
            u32 n = 0;
            while (hexval(p[0]) >= 0 && hexval(p[1]) >= 0 && n < sizeof buf) {
                buf[n++] = (u8)(hexval(p[0]) * 16 + hexval(p[1]));
                p += 2;
            }
            tcrt_poke(addr, buf, n);
        } else if (line[0] == 'E') {
            tcrt_set_xf(xf);
            tcrt_set_fh(fh);
            tcrt_set_gs(gs);
            tcrt_set_low(low);
            tcrt_set_ro(r);
            if (has_fq) tcrt_set_fq(fq);
            tcrt_set_call_returns(rets);
            u32 r0 = 0;
            f32 fr0 = 0.0f;
            paint_stack();
            int code = run_one(r, fr, &r0, &fr0);
            if (code) fprintf(out, "CRASH %08x\n", (u32)code);
            u32 frbits;
            memcpy(&frbits, &fr0, 4);
            tcrt_report(out, r0, frbits);
            tcrt_report_sr(out);
            tcrt_report_gbr(out);
            tcrt_report_fpscr(out);
            if (has_fh) tcrt_report_fh(out);
            if (has_gs) tcrt_report_gs(out);
            tcrt_report_ro(out);
            tcrt_report_t(out);
            if (has_fq) tcrt_report_fq(out);
            fprintf(out, "E\n");
            fflush(out);
            active = 0;
        }
    }
    fclose(in);
    fclose(out);
    return 0;
}
