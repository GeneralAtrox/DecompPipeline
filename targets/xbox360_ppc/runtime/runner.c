/* Test runner: executes the decompiled function on every vector and prints its observable effects in the
 * oracle's expected.txt format (x360_compare.py documents both formats).
 *
 * usage: runner <image.bin> <code_ranges> <vectors.txt> <dev|hidden|all> <out.txt>
 *
 * vectors.txt per vector: V id set / R r3..r10 (64-bit) / F f1..f13 (double bits) / C r3 per mocked call /
 * D f1 per mocked call (double bits) / M addr bytes (memory patches) / E.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <windows.h>
#include "tc_rt_internal.h"

/* Generated per function (x360_check.gen_thunk): unpacks the argument registers and calls fn_<addr>. */
void tc_thunk(const u64 *r, const f64 *f, u64 *out_r3, f64 *out_f1);

static int hexval(int c)
{
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

static int run_one(const u64 *r, const f64 *f, u64 *r3, f64 *f1)
{
    __try {
        tc_thunk(r, f, r3, f1);
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

static void dwords(const char *p, u64 *out, int n)
{
    char *e = (char *)p;
    for (int i = 0; i < n; i++) out[i] = (u64)_strtoui64(e, &e, 16);
}

int main(int argc, char **argv)
{
    if (argc != 6) {
        fprintf(stderr, "usage: runner image code_ranges vectors set out\n");
        return 2;
    }
    if (!tcrt_init(argv[1], argv[2])) {
        fprintf(stderr, "runner: runtime init failed\n");
        return 2;
    }
    FILE *in = open_file(argv[3], "r");
    FILE *out = open_file(argv[5], "w");
    if (!in || !out) {
        fprintf(stderr, "runner: cannot open vectors or output\n");
        return 2;
    }
    const char *want = argv[4];
    static char line[1 << 16];
    static u8 buf[1 << 15];
    static u64 r[8], fbits[TCRT_CALL_FLOATS], rets[16], frets[16];
    static f64 f[TCRT_CALL_FLOATS];
    int id = -1, active = 0;
    while (fgets(line, sizeof line, in)) {
        if (line[0] == 'V') {
            char set[16] = {0};
            sscanf_s(line + 2, "%d %15s", &id, set, (unsigned)sizeof set);
            active = strcmp(want, "all") == 0 || strcmp(want, set) == 0;
            if (active) {
                tcrt_reset();
                memset(r, 0, sizeof r);
                memset(fbits, 0, sizeof fbits);
                memset(rets, 0, sizeof rets);
                memset(frets, 0, sizeof frets);
                fprintf(out, "V %d %s\n", id, set);
            }
        } else if (!active) {
            continue;
        } else if (line[0] == 'R') {
            dwords(line + 1, r, 8);
        } else if (line[0] == 'F') {
            dwords(line + 1, fbits, TCRT_CALL_FLOATS);
        } else if (line[0] == 'C') {
            dwords(line + 1, rets, 16);
        } else if (line[0] == 'D') {
            dwords(line + 1, frets, 16);
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
            memcpy(f, fbits, sizeof f);
            tcrt_set_call_returns(rets, frets);
            u64 r3 = 0;
            f64 f1 = 0.0;
            int code = run_one(r, f, &r3, &f1);
            if (code) fprintf(out, "CRASH %08x\n", (u32)code);
            u64 b;
            memcpy(&b, &f1, 8);
            tcrt_report(out, r3, b);
            fprintf(out, "E\n");
            fflush(out);
            active = 0;
        }
    }
    fclose(in);
    fclose(out);
    return 0;
}
