/* Readable-C test runner (32-bit, guest RAM at its own address): executes the function on every vector and prints its observable effects.
 *
 * usage: rd_runner <image> <code_ranges> <fsca_table> <vectors.txt> <dev|hidden|all> <out.txt>
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <windows.h>
#include "rd_internal.h"

/* Generated per function: unpacks argument registers and calls fn_<addr>. */
void tc_thunk(const u32 *r, const f32 *fr, u32 *out_r0, f32 *out_fr0);

static int hexval(int c)
{
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

static int run_one(const u32 *r, const f32 *fr, u32 *r0, f32 *fr0)
{
    __try {
        tc_thunk(r, fr, r0, fr0);
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return (int)GetExceptionCode();
    }
    return 0;
}

/* Before a vector with out-pointer lines (O): the host stack below main's frame, where the thunk and the readable
 * function's frames will be, holds the paint (rd_canary), so the call trap can tell bytes the readable code has
 * written from bytes it has not (rd_veh.c out_copy). Other vectors are run exactly as before. */
#define RD_PAINT 0x10000
static __declspec(noinline) void paint_stack(void)
{
    volatile u8 pad[RD_PAINT];
    for (u32 i = 0; i < RD_PAINT; i++) pad[i] = rd_canary((uintptr_t)&pad[i]);
}

static FILE *open_file(const char *path, const char *mode)
{
    FILE *f = NULL;
    return fopen_s(&f, path, mode) == 0 ? f : NULL;
}

int main(int argc, char **argv)
{
    if (argc != 7) {
        fprintf(stderr, "usage: rd_runner image code_ranges fsca vectors set out\n");
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
    rd_out_log(out);
    static char line[1 << 16];
    static u8 buf[1 << 15];
    int id = -1, active = 0;
    u32 r[4] = {0}, xf[16] = {0}, rets[16] = {0};
    /* fr12..fr15 from line H (reported at exit as FH only when the vector had H). Static, like line and buf:
     * main's frame stays the size it was, so readable code that reads an uninitialized local behaves as before. */
    static u32 fh[4];
    static int has_fh;
    static u32 fq[16]; /* fr0..fr15 (line Q: the whole FPU is state; reported at exit as QH) */
    static int has_fq;
    static u32 low[4], gs[7]; /* r0..r3 (line L) and r8..r14 (line G, reported at exit as GH), as runner.c */
    static int has_gs;
    f32 fr[8] = {0};
    while (fgets(line, sizeof line, in)) {
        if (line[0] == 'P') { /* entry FPSCR, before the first vector (TcVectors.fpscrLine) */
            tcrt_set_fpscr((u32)strtoul(line + 1, NULL, 16));
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
        } else if (line[0] == 'Q') {
            char *p = line + 1;
            for (int i = 0; i < 16; i++) fq[i] = (u32)strtoul(p, &p, 16);
            has_fq = 1;
        } else if (line[0] == 'O' || line[0] == 'B') { /* B: the raw run, right after V (rd_veh.c out_copy) */
            u32 call = 0, target = 0, field = 0, guest = 0, size = 0, flip = 0; /* B's optional 6th: paint ^ flip */
            if (sscanf_s(line + 1, "%u %x %u %x %u %x", &call, &target, &field, &guest, &size, &flip) >= 5) {
                rd_out_add(call, target, field, guest, size);
                if (line[0] == 'B' && size <= sizeof buf) {
                    for (u32 k = 0; k < size; k++) buf[k] = (u8)(rd_canary(guest + k) ^ flip);
                    tcrt_poke(guest, buf, size);
                }
            }
        } else if (line[0] == 'T') { /* T call target field guest span: the raw run's read trace (rd_trace.c) */
            u32 call = 0, target = 0, field = 0, guest = 0, span = 0;
            if (sscanf_s(line + 1, "%u %x %u %x %u", &call, &target, &field, &guest, &span) == 5)
                rd_trace_add(call, target, field, guest, span);
        } else if (line[0] == 'D') { /* D call target field hex ("xx": the readable code's byte stays) */
            char *p = line + 1;
            u32 call = (u32)strtoul(p, &p, 10), target = (u32)strtoul(p, &p, 16), field = (u32)strtoul(p, &p, 10);
            while (*p == ' ') p++;
            static u8 mask[sizeof buf];
            u32 n = 0;
            for (; n < sizeof buf && p[0] && p[1]; n++, p += 2) {
                if (p[0] == 'x' && p[1] == 'x') {
                    buf[n] = 0;
                    mask[n] = 0;
                } else if (hexval(p[0]) >= 0 && hexval(p[1]) >= 0) {
                    buf[n] = (u8)(hexval(p[0]) * 16 + hexval(p[1]));
                    mask[n] = 1;
                } else {
                    break;
                }
            }
            rd_out_data(call, target, field, buf, mask, n);
        } else if (line[0] == 'C') {
            char *p = line + 1;
            for (int i = 0; i < 16; i++) rets[i] = (u32)strtoul(p, &p, 16);
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
            if (has_fq) rd_sh4_set_fq(fq);
            tcrt_set_call_returns(rets);
            if (rd_out_paint()) paint_stack();
            rd_arm();
            u32 r0 = 0;
            f32 fr0 = 0.0f;
            int code = run_one(r, fr, &r0, &fr0);
            if (code) fprintf(out, "CRASH %08x\n", (u32)code);
            rd_trace_report(out);
            u32 frbits;
            memcpy(&frbits, &fr0, 4);
            tcrt_report(out, r0, frbits);
            if (has_fh) tcrt_report_fh(out);
            if (has_gs) tcrt_report_gs(out);
            if (has_fq) rd_sh4_report_fq(out);
            fprintf(out, "E\n");
            fflush(out);
            active = 0;
        }
    }
    fclose(in);
    fclose(out);
    return 0;
}
