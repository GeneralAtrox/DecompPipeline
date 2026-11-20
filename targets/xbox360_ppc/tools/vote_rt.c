/* The vote harness's runner (tools/vote_leg_a.py): runs the translator's exit-state form (Facts.exit_seed) of every
 * instruction test case and prints the registers each case names. Linked with the judge runtime (tc_rt.c, tc_ppc.c,
 * tc_exit.c: the exit record) and the generated vote_table.c; the judge's runner.c is not used.
 *
 * usage: vote_rt <image.bin> <code_ranges> <cases.txt> <out.txt>
 * cases.txt per case: C id fn / R n hex / F n hex (bits) / V n w0 w1 w2 w3 / M addr hexbytes / O name (r3, f1, v4,
 * cr) / Q addr len / E. Every register not given is 0 at entry (CR, XER, CTR too); r1 is the judge's stack pointer.
 * out.txt per case: C id / [CRASH code] / O name value (r, f: 16 hex digits; v: four words; cr: 8) / Q addr hexbytes /
 * E. The exit record (tc_exit.c) prints a register the C never names with its tcx_begin value: r3..r10, r14..r31,
 * f1..f31 and CR are seeded there from the case; r0, r2, r11, r12, f0 and v* not named by the C are taken from the
 * case's inputs by vote_leg_a.py (the C cannot change a register it does not name).
 *
 * Nearest existing: runtime/runner.c - seeds only r3..r10, f1..f13, r14..r31, f14..f31, CR2..CR4 and calls one function
 * through tc_thunk; this seeds every register and dispatches to many functions in one run.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <windows.h>
#include <io.h>
#include "tc_rt_internal.h"

typedef struct { u32 w[4]; } xv;
extern int tcx_on;
extern void (*const vote_fns[])(void);
extern const int vote_nfns;

static u64 g_r[32], g_f[32];
static u32 g_v[128][4];

u64 tcv_in_r(u32 n) { return n < 32 ? g_r[n] : 0; }

f64 tcv_in_f(u32 n)
{
    f64 d = 0.0;
    if (n < 32) memcpy(&d, &g_f[n], 8);
    return d;
}

xv tcv_in_v(u32 n)
{
    xv v;
    memset(&v, 0, sizeof v);
    if (n < 128) memcpy(v.w, g_v[n], 16);
    return v;
}

static FILE *open_file(const char *path, const char *mode)
{
    FILE *f = NULL;
    return fopen_s(&f, path, mode) == 0 ? f : NULL;
}

static int call(int fn)
{
    __try {
        vote_fns[fn]();
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return (int)GetExceptionCode();
    }
    return 0;
}

static int hexval(int c)
{
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

#define MAXO 64
static char outs[MAXO][8];
static unsigned qa[16], ql[16];

/* The exit record as tc_exit.c prints it, read back from a scratch file. */
static u64 xs[32], xf[32];
static u32 xcr, xvv[128][4];
static u8 xvw[128];

static void read_record(FILE *t)
{
    static char line[4096];
    memset(xvw, 0, sizeof xvw);
    rewind(t);
    while (fgets(line, sizeof line, t)) {
        char *p = line + 2;
        if (line[0] == 'X' && (line[1] == 'S' || line[1] == 'F')) {
            u64 *dst = line[1] == 'S' ? xs : xf;
            for (int i = 0; i < 32; i++) dst[i] = (u64)_strtoui64(p, &p, 16);
        } else if (line[0] == 'X' && line[1] == 'C') {
            xcr = (u32)strtoul(p, &p, 16);
        } else if (line[0] == 'X' && line[1] == 'V') {
            int n = (int)strtol(p, &p, 10);
            if (n >= 0 && n < 128) {
                for (int k = 0; k < 4; k++) xvv[n][k] = (u32)strtoul(p, &p, 16);
                xvw[n] = 1;
            }
        }
    }
}

int main(int argc, char **argv)
{
    if (argc != 5) {
        fprintf(stderr, "usage: vote_rt image code_ranges cases out\n");
        return 2;
    }
    tcx_on = 1;
    if (!tcrt_init(argv[1], argv[2])) {
        fprintf(stderr, "vote_rt: runtime init failed\n");
        return 2;
    }
    FILE *in = open_file(argv[3], "r"), *out = open_file(argv[4], "w"), *t = NULL;
    if (!in || !out || tmpfile_s(&t) != 0 || !t) {
        fprintf(stderr, "vote_rt: cannot open files\n");
        return 2;
    }
    static char line[1 << 16];
    static u8 buf[1 << 12];
    static u64 r8[8], f13[TCRT_CALL_FLOATS], nv[18], fnv[18];
    int id = -1, fn = -1, no = 0, nq = 0;
    while (fgets(line, sizeof line, in)) {
        char *p = line + 2;
        switch (line[0]) {
        case 'C':
            sscanf_s(p, "%d %d", &id, &fn);
            tcrt_reset();
            memset(g_r, 0, sizeof g_r);
            memset(g_f, 0, sizeof g_f);
            memset(g_v, 0, sizeof g_v);
            no = nq = 0;
            break;
        case 'R': { u32 n = (u32)strtoul(p, &p, 10); if (n < 32) g_r[n] = (u64)_strtoui64(p, &p, 16); break; }
        case 'F': { u32 n = (u32)strtoul(p, &p, 10); if (n < 32) g_f[n] = (u64)_strtoui64(p, &p, 16); break; }
        case 'V': {
            u32 n = (u32)strtoul(p, &p, 10);
            for (int k = 0; k < 4 && n < 128; k++) g_v[n][k] = (u32)strtoul(p, &p, 16);
            break;
        }
        case 'M': {
            u32 a = (u32)strtoul(p, &p, 16), n = 0;
            while (*p == ' ') p++;
            while (hexval(p[0]) >= 0 && hexval(p[1]) >= 0 && n < sizeof buf) {
                buf[n++] = (u8)(hexval(p[0]) * 16 + hexval(p[1]));
                p += 2;
            }
            tcrt_poke(a, buf, n);
            break;
        }
        case 'O':
            if (no < MAXO) sscanf_s(p, "%7s", outs[no++], (unsigned)sizeof outs[0]);
            break;
        case 'Q':
            if (nq < 16) {
                sscanf_s(p, "%x %u", &qa[nq], &ql[nq]);
                nq++;
            }
            break;
        case 'E': {
            for (int i = 0; i < 8; i++) r8[i] = g_r[3 + i];
            for (int i = 0; i < TCRT_CALL_FLOATS; i++) f13[i] = g_f[1 + i];
            for (int i = 0; i < 18; i++) {
                nv[i] = g_r[14 + i];
                fnv[i] = g_f[14 + i];
            }
            tcx_begin(r8, f13, nv, fnv, 0, g_r[12]);
            fprintf(out, "C %d\n", id);
            int code = fn >= 0 && fn < vote_nfns ? call(fn) : -1;
            if (code) fprintf(out, "CRASH %08x\n", (u32)code);
            _chsize_s(_fileno(t), 0);
            rewind(t);
            tcx_report(t);
            fflush(t);
            read_record(t);
            for (int i = 0; i < no; i++) {
                char *o = outs[i];
                int n = atoi(o + 1);
                if (o[0] == 'r' && n < 32) fprintf(out, "O %s %016llx\n", o, (unsigned long long)xs[n]);
                else if (o[0] == 'f' && n < 32) fprintf(out, "O %s %016llx\n", o, (unsigned long long)xf[n]);
                else if (o[0] == 'v' && n < 128 && xvw[n])
                    fprintf(out, "O %s %08x %08x %08x %08x\n", o, xvv[n][0], xvv[n][1], xvv[n][2], xvv[n][3]);
                else if (strcmp(o, "cr") == 0) fprintf(out, "O cr %08x\n", xcr);
            }
            for (int i = 0; i < nq; i++) {
                fprintf(out, "Q %08x ", qa[i]);
                for (u32 k = 0; k < ql[i]; k++) fprintf(out, "%02x", rd8(qa[i] + k));
                fputc('\n', out);
            }
            fputs("E\n", out);
            break;
        }
        default:
            break;
        }
    }
    fclose(in);
    fclose(out);
    fclose(t);
    return 0;
}
