/* Saturn SH-2 readable-C runtime: work RAM mapped at its Saturn addresses, code-page guards, and the write report.
 *
 * One shared section holds both 1 MB banks in the raw runtime's order (low at offset 0, high at TCRT_BANK: tc_rt.c).
 * Work RAM high is mapped at 0x06000000 and again at its cache-through view 0x26000000, so a guest pointer in either
 * form is a host pointer. Work RAM low is mapped at its cache-through view 0x20200000 only (rd_internal.h: 0x00200000
 * cannot be had); the raw API below reaches it at its 0x002... address. Page protections are always changed on every
 * view. Memory holds the guest's bytes in the guest's (big-endian) order: readable code reads it through rd_be.h,
 * raw C through rd32 and the others.
 *
 * Writes are found by comparing work RAM with a snapshot taken just before the run, so a write that stores the value
 * already there is invisible. The judge compares final memory contents, so this does not change any verdict.
 */
#include <float.h>
#include <stdlib.h>
#include <string.h>
#include <windows.h>
#include <xmmintrin.h>
#include "rd_internal.h"

#define NPAGES (TCRT_RAM_SIZE / RD_PAGE)
#define MAX_FAULTS 4

enum { PAGE_PLAIN, PAGE_CODE };

static u8 *g_pristine;
static u8 *g_snapshot;
static u8 *g_code; /* 1 bit per guest byte that belongs to an instruction */
static u8 g_page[NPAGES];
static int g_armed;
static char g_faults[MAX_FAULTS][64];
static u32 g_nfaults;
int tcrt_sq_window;

/* The host address of a buffer offset (low bank: its cache-through view). */
static u8 *host(u32 off)
{
    return (u8 *)(uintptr_t)(off < TCRT_BANK ? RD_LOW_VIEW + off : TCRT_HIGH_BASE + (off - TCRT_BANK));
}

/* [off, off + size) within one bank, on every view of it */
static void protect(u32 off, u32 size, DWORD prot)
{
    DWORD old;
    VirtualProtect(host(off), size, prot, &old);
    if (off >= TCRT_BANK) VirtualProtect((void *)(uintptr_t)(RD_HIGH_VIEW2 + (off - TCRT_BANK)), size, prot, &old);
}

/* Header line S: the SH-2 has no store-queue window (TcSq never writes the line), as tc_rt.c. */
void tcrt_set_sq_window(void) { tcrt_sq_window = 1; }

static int ram_off(u32 addr, u32 size, u32 *off) { return tcrt_ram_off(tcrt_fold(addr), size, off); }

int rd_is_code(u32 addr)
{
    u32 off;
    return ram_off(addr, 1, &off) && ((g_code[off >> 3] >> (off & 7)) & 1);
}

int rd_page_guarded(u32 addr)
{
    u32 off;
    return g_armed && ram_off(addr, 1, &off) && g_page[off / RD_PAGE] == PAGE_CODE;
}

void rd_page_open(u32 addr)
{
    u32 off;
    if (ram_off(addr, 1, &off)) protect(off & ~(RD_PAGE - 1), RD_PAGE, PAGE_READWRITE);
}

void rd_page_close(u32 addr)
{
    u32 off;
    if (ram_off(addr, 1, &off)) protect(off & ~(RD_PAGE - 1), RD_PAGE, PAGE_NOACCESS);
}

static FILE *open_file(const char *path, const char *mode)
{
    FILE *f = NULL;
    return fopen_s(&f, path, mode) == 0 ? f : NULL;
}

static int map_view(HANDLE sec, u32 off, u32 at)
{
    void *want = (void *)(uintptr_t)at;
    if (MapViewOfFileEx(sec, FILE_MAP_ALL_ACCESS, 0, off, TCRT_BANK, want) != want) {
        fprintf(stderr, "runner: cannot map work RAM at %08x\n", at);
        return 0;
    }
    /* Just before and just past each view: any access faults. */
    VirtualAlloc((void *)(uintptr_t)(at - 0x10000u), 0x10000, MEM_RESERVE, PAGE_NOACCESS);
    VirtualAlloc((void *)(uintptr_t)(at + TCRT_BANK), 0x10000, MEM_RESERVE, PAGE_NOACCESS);
    return 1;
}

int tcrt_init(const char *image, const char *code_ranges, const char *fsca_table)
{
    HANDLE sec = CreateFileMappingW(INVALID_HANDLE_VALUE, NULL, PAGE_READWRITE, 0, TCRT_RAM_SIZE, NULL);
    if (!sec || !map_view(sec, 0, RD_LOW_VIEW) || !map_view(sec, TCRT_BANK, TCRT_HIGH_BASE)
        || !map_view(sec, TCRT_BANK, RD_HIGH_VIEW2))
        return 0;
    g_pristine = (u8 *)calloc(TCRT_RAM_SIZE, 1);
    g_snapshot = (u8 *)calloc(TCRT_RAM_SIZE, 1);
    g_code = (u8 *)calloc(TCRT_RAM_SIZE / 8, 1);
    if (!g_pristine || !g_snapshot || !g_code) return 0;
    FILE *f = open_file(image, "rb");
    if (!f) return 0;
    u32 at = TCRT_BANK + (TCRT_IMAGE_BASE - TCRT_HIGH_BASE); /* a work RAM high image from 0x06000000 (tc_rt.c) */
    size_t n = fread(g_pristine + at, 1, TCRT_RAM_SIZE - at, f);
    int too_big = fgetc(f) != EOF;
    fclose(f);
    if (!n || too_big) return 0;
    f = open_file(code_ranges, "r");
    if (!f) return 0;
    unsigned lo, hi;
    while (fscanf_s(f, "%x %x", &lo, &hi) == 2) {
        u32 off;
        for (u32 a = lo; a < hi; a++) {
            if (!tcrt_ram_off(a, 1, &off)) continue;
            g_code[off >> 3] |= (u8)(1u << (off & 7));
            g_page[off / RD_PAGE] = PAGE_CODE;
        }
    }
    fclose(f);
    if (!rd_sh4_init(fsca_table) || !rd_veh_install() || !rd_p4_init()) return 0;
    tcrt_reset();
    return 1;
}

static void disarm(void)
{
    if (g_armed) {
        protect(0, TCRT_BANK, PAGE_READWRITE);
        protect(TCRT_BANK, TCRT_BANK, PAGE_READWRITE);
    }
    g_armed = 0;
}

/* Copy between a buffer and work RAM (both banks, through their views). */
static void ram_to(u8 *buf)
{
    memcpy(buf, host(0), TCRT_BANK);
    memcpy(buf + TCRT_BANK, host(TCRT_BANK), TCRT_BANK);
}

static u32 g_mxcsr_rc; /* FPSCR.RM from vectors.txt line P (tc_rt.c): 0 nearest, 0x6000 toward zero */

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

void tcrt_set_fpscr(u32 fpscr)
{
    g_mxcsr_rc = (fpscr & 3u) == 1u ? 0x6000u : 0u;
}

void tcrt_reset(void)
{
    static const u32 zero[TCRT_RETS] = {0};
    disarm();
    memcpy(host(0), g_pristine, TCRT_BANK);
    memcpy(host(TCRT_BANK), g_pristine + TCRT_BANK, TCRT_BANK);
    rd_sh4_reset();
    rd_calls_reset(zero);
    rd_abi_reset();
    rd_p4_reset();
    g_nfaults = 0;
    tcrt_halted = 0;
    /* As the Dreamcast runtime (and tc_rt.c, which keeps its FPU code): the SH-2 has no FPU, so no SH-2 code computes
     * on floats; the x87 rounds integer conversions the same way. */
    _mm_setcsr((_mm_getcsr() & ~0x6000u) | 0x8040u | g_mxcsr_rc);
    unsigned int x87 = 0;
    __control87_2(g_mxcsr_rc ? _RC_CHOP : _RC_NEAR, _MCW_RC, &x87, NULL);
}

void tcrt_poke(u32 addr, const u8 *data, u32 len)
{
    u32 off;
    if (!len || !ram_off(addr, len, &off)) return;
    memcpy(host(off), data, len);
}

void tcrt_set_call_returns(const u32 rets[TCRT_RETS]) { rd_calls_reset(rets); }

/* Just before the run: remember memory, then make every page holding machine code fault on access. */
void rd_arm(void)
{
    ram_to(g_snapshot);
    for (u32 p = 0; p < NPAGES;) {
        u32 q = p;
        while (q < NPAGES && g_page[q] == PAGE_CODE && (q == p || q * RD_PAGE != TCRT_BANK)) q++;
        if (q > p) protect(p * RD_PAGE, (q - p) * RD_PAGE, PAGE_NOACCESS);
        p = q > p ? q : p + 1;
    }
    g_armed = 1;
}

void tcrt_report(FILE *out, u32 r0, u32 fr0_bits)
{
    disarm();
    fprintf(out, "RET %08x %08x\n", rd_self_guest(r0), fr0_bits);
    rd_report_retx(out);
    if (tcrt_halted) fprintf(out, tcrt_halted == 2 ? "HALT SPIN\n" : "HALT\n");
    static u8 now[TCRT_RAM_SIZE];
    ram_to(now);
    for (u32 o = 0; o + 4 <= TCRT_RAM_SIZE; o += 4) { /* a stored host address of the function under test: its guest one */
        u32 w = (u32)now[o] << 24 | (u32)now[o + 1] << 16 | (u32)now[o + 2] << 8 | now[o + 3];
        if (memcmp(now + o, g_snapshot + o, 4) != 0 && rd_self_guest(w) != w) {
            w = rd_self_guest(w);
            for (int k = 0; k < 4; k++) now[o + k] = (u8)(w >> (24 - 8 * k));
        }
    }
    /* all of work RAM, the guest stack window too (globals below the frame): core/compare.judged leaves out the frame */
    for (u32 o = 0; o < TCRT_RAM_SIZE;) {
        if (now[o] == g_snapshot[o]) {
            o++;
            continue;
        }
        u32 end = o + 1;
        while (end < TCRT_RAM_SIZE && end != TCRT_BANK && now[end] != g_snapshot[end]) end++; /* a run stays in its bank */
        fprintf(out, "W %08x ", tcrt_ram_addr(o));
        for (u32 k = o; k < end; k++) fprintf(out, "%02x", now[k]);
        fputc('\n', out);
        o = end;
    }
    tcrt_p4_report(out);
    rd_calls_report(out);
    rd_sh4_report(out);
    for (u32 k = 0; k < g_nfaults && k < MAX_FAULTS; k++) fprintf(out, "FAULT %s\n", g_faults[k]);
    if (rd_code_read()) fprintf(out, "CODEREAD %08x\n", rd_code_read());
    ULONG_PTR slo = 0, shi = 0;
    GetCurrentThreadStackLimits(&slo, &shi);
    fprintf(out, "HOSTSTACK %08x %08x\n", (u32)slo, (u32)shi);
}

/* The raw guest-memory API, for the generated thunk (stack parameters) and for running raw matched C: work RAM only,
 * as tc_rt.c (any other address is a FAULT line and reads 0); a read of an instruction byte faults on its guarded
 * page and is reported as CODEREAD (rd_veh.c). Big endian.
 * rd_tracing: a T line is set (rd_trace.c watches the original's reads of an out-buffer and its own writes). */
static volatile u8 *at(u32 a, u32 size, int write)
{
    u32 off;
    a = tcrt_fold(a);
    if (!tcrt_ram_off(a, size, &off)) {
        if (g_nfaults < MAX_FAULTS)
            snprintf(g_faults[g_nfaults], sizeof g_faults[0], "%s %08x", write ? "write" : "read", a);
        g_nfaults++;
        return NULL;
    }
    return host(off);
}

u8 rd8(u32 a)
{
    if (rd_tracing) rd_trace_read(a, 1);
    volatile u8 *p = at(a, 1, 0);
    return p ? p[0] : 0;
}
u16 rd16(u32 a)
{
    if (rd_tracing) rd_trace_read(a, 2);
    volatile u8 *p = at(a, 2, 0);
    return p ? (u16)(p[0] << 8 | p[1]) : 0;
}
u32 rd32(u32 a)
{
    if (rd_tracing) rd_trace_read(a, 4);
    volatile u8 *p = at(a, 4, 0);
    return p ? (u32)p[0] << 24 | (u32)p[1] << 16 | (u32)p[2] << 8 | p[3] : 0;
}
f32 rdf32(u32 a)
{
    u32 u = rd32(a);
    f32 f;
    memcpy(&f, &u, 4);
    return f;
}
void wr8(u32 a, u8 v)
{
    if (rd_tracing || rd_writes) rd_trace_write(a, 1);
    volatile u8 *p = at(a, 1, 1);
    if (p) p[0] = v;
}
void wr16(u32 a, u16 v)
{
    if (rd_tracing || rd_writes) rd_trace_write(a, 2);
    volatile u8 *p = at(a, 2, 1);
    if (!p) return;
    p[0] = (u8)(v >> 8);
    p[1] = (u8)v;
}
void wr32(u32 a, u32 v)
{
    if (rd_tracing || rd_writes) rd_trace_write(a, 4);
    volatile u8 *p = at(a, 4, 1);
    if (!p) return;
    p[0] = (u8)(v >> 24);
    p[1] = (u8)(v >> 16);
    p[2] = (u8)(v >> 8);
    p[3] = (u8)v;
}
void wrf32(u32 a, f32 v)
{
    u32 u;
    memcpy(&u, &v, 4);
    wr32(a, u);
}
