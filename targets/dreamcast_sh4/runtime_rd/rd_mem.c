/* Readable-C runtime: the guest RAM mapped at its Dreamcast address, code-page guards, and the write report.
 * One shared section is mapped twice: at 0x8c... and at its 0x0c... mirror (RD_MIRROR_BASE), as on the SH-4 with
 * the MMU off; page protections are always changed on both views.
 *
 * Writes are found by comparing RAM with a snapshot taken just before the run, so a write that stores the value
 * already there is invisible. The judge compares final memory contents, so this does not change any verdict.
 */
#include <float.h>
#include <stdlib.h>
#include <string.h>
#include <windows.h>
#include <xmmintrin.h>
#include "rd_internal.h"

#define NPAGES (TCRT_RAM_SIZE / RD_PAGE)
#define RAM ((u8 *)(uintptr_t)TCRT_RAM_BASE)

enum { PAGE_PLAIN, PAGE_CODE };

static u8 *g_pristine;
static u8 *g_snapshot;
static u8 *g_code; /* 1 bit per guest byte that belongs to an instruction */
static u8 g_page[NPAGES];
static int g_armed;

static void protect(u32 addr, u32 size, DWORD prot)
{
    DWORD old;
    VirtualProtect((void *)(uintptr_t)addr, size, prot, &old);
    VirtualProtect((void *)(uintptr_t)(addr - TCRT_RAM_BASE + RD_MIRROR_BASE), size, prot, &old);
}

int rd_is_code(u32 addr)
{
    addr = tcrt_fold(addr);
    u32 off = addr - TCRT_RAM_BASE;
    return off < TCRT_RAM_SIZE && ((g_code[off >> 3] >> (off & 7)) & 1);
}

int rd_page_guarded(u32 addr)
{
    addr = tcrt_fold(addr);
    u32 off = addr - TCRT_RAM_BASE;
    return g_armed && off < TCRT_RAM_SIZE && g_page[off / RD_PAGE] == PAGE_CODE;
}

void rd_page_open(u32 addr) { protect(tcrt_fold(addr) & ~(RD_PAGE - 1), RD_PAGE, PAGE_READWRITE); }
void rd_page_close(u32 addr) { protect(tcrt_fold(addr) & ~(RD_PAGE - 1), RD_PAGE, PAGE_NOACCESS); }

static FILE *open_file(const char *path, const char *mode)
{
    FILE *f = NULL;
    return fopen_s(&f, path, mode) == 0 ? f : NULL;
}

int tcrt_init(const char *image, const char *code_ranges, const char *fsca_table)
{
    HANDLE sec = CreateFileMappingW(INVALID_HANDLE_VALUE, NULL, PAGE_READWRITE, 0, TCRT_RAM_SIZE, NULL);
    void *mirror = (void *)(uintptr_t)RD_MIRROR_BASE;
    if (!sec || MapViewOfFileEx(sec, FILE_MAP_ALL_ACCESS, 0, 0, TCRT_RAM_SIZE, RAM) != RAM
        || MapViewOfFileEx(sec, FILE_MAP_ALL_ACCESS, 0, 0, TCRT_RAM_SIZE, mirror) != mirror) {
        fprintf(stderr, "runner: cannot map guest RAM at %08x and %08x\n", TCRT_RAM_BASE, RD_MIRROR_BASE);
        return 0;
    }
    /* Below the image (system area) and just past RAM: any access faults. */
    protect(TCRT_RAM_BASE, TCRT_VALID_LO - TCRT_RAM_BASE, PAGE_NOACCESS);
    VirtualAlloc((void *)(uintptr_t)(TCRT_RAM_BASE + TCRT_RAM_SIZE), 0x10000, MEM_RESERVE, PAGE_NOACCESS);
    VirtualAlloc((void *)(uintptr_t)(RD_MIRROR_BASE + TCRT_RAM_SIZE), 0x10000, MEM_RESERVE, PAGE_NOACCESS);
    g_pristine = (u8 *)calloc(TCRT_RAM_SIZE, 1);
    g_snapshot = (u8 *)calloc(TCRT_RAM_SIZE, 1);
    g_code = (u8 *)calloc(TCRT_RAM_SIZE / 8, 1);
    if (!g_pristine || !g_snapshot || !g_code) return 0;
    FILE *f = open_file(image, "rb");
    if (!f) return 0;
    size_t cap = TCRT_RAM_SIZE - (TCRT_VALID_LO - TCRT_RAM_BASE);
    size_t n = fread(g_pristine + (TCRT_VALID_LO - TCRT_RAM_BASE), 1, cap, f);
    int too_big = fgetc(f) != EOF;
    fclose(f);
    if (!n || too_big) return 0;
    f = open_file(code_ranges, "r");
    if (!f) return 0;
    unsigned lo, hi;
    while (fscanf_s(f, "%x %x", &lo, &hi) == 2) {
        for (u32 a = lo; a < hi && a >= TCRT_VALID_LO && a < TCRT_RAM_BASE + TCRT_RAM_SIZE; a++) {
            u32 off = a - TCRT_RAM_BASE;
            g_code[off >> 3] |= (u8)(1u << (off & 7));
            g_page[off / RD_PAGE] = PAGE_CODE;
        }
    }
    fclose(f);
    if (!rd_sh4_init(fsca_table) || !rd_veh_install()) return 0;
    rd_p4_init();
    tcrt_reset();
    return 1;
}

static void disarm(void)
{
    if (g_armed) protect(TCRT_VALID_LO, TCRT_RAM_BASE + TCRT_RAM_SIZE - TCRT_VALID_LO, PAGE_READWRITE);
    g_armed = 0;
}

static u32 g_mxcsr_rc; /* FPSCR.RM from vectors.txt line P (tc_rt.c): 0 nearest, 0x6000 toward zero */

void tcrt_set_fpscr(u32 fpscr)
{
    g_mxcsr_rc = (fpscr & 3u) == 1u ? 0x6000u : 0u;
}

void tcrt_reset(void)
{
    static const u32 zero[16] = {0};
    disarm();
    u32 lo = TCRT_VALID_LO - TCRT_RAM_BASE;
    memcpy(RAM + lo, g_pristine + lo, TCRT_RAM_SIZE - lo);
    rd_sh4_reset();
    rd_calls_reset(zero);
    rd_abi_reset();
    rd_p4_reset();
    _mm_setcsr((_mm_getcsr() & ~0x6000u) | 0x8040u | g_mxcsr_rc); /* FPSCR.DN=1: DAZ|FTZ; RM: RC (tc_rt.c) */
    /* The x87 too (this build is 32-bit): it converts integers to f32 (fild, then a rounding fstp, where the 64-bit
     * build's cvtsi2ss rounds by MXCSR.RC), so it rounds as FPSCR.RM says. It does no other float arithmetic here:
     * readable_check.build recompiles such code unoptimized, which keeps it in SSE registers. */
    unsigned int x87 = 0;
    __control87_2(g_mxcsr_rc ? _RC_CHOP : _RC_NEAR, _MCW_RC, &x87, NULL);
}

void tcrt_poke(u32 addr, const u8 *data, u32 len)
{
    addr = tcrt_fold(addr);
    if (addr < TCRT_VALID_LO || addr + len > TCRT_RAM_BASE + TCRT_RAM_SIZE || addr + len < addr) return;
    memcpy(RAM + (addr - TCRT_RAM_BASE), data, len);
}

void tcrt_set_call_returns(const u32 rets[16]) { rd_calls_reset(rets); }

/* Just before the run: remember memory, then make every page holding machine code fault on access. */
void rd_arm(void)
{
    u32 lo = TCRT_VALID_LO - TCRT_RAM_BASE;
    memcpy(g_snapshot + lo, RAM + lo, TCRT_RAM_SIZE - lo);
    for (u32 p = 0; p < NPAGES;) {
        u32 q = p;
        while (q < NPAGES && g_page[q] == PAGE_CODE) q++;
        if (q > p) protect(TCRT_RAM_BASE + p * RD_PAGE, (q - p) * RD_PAGE, PAGE_NOACCESS);
        p = q + 1;
    }
    g_armed = 1;
}

void tcrt_report(FILE *out, u32 r0, u32 fr0_bits)
{
    disarm();
    fprintf(out, "RET %08x %08x\n", rd_self_guest(r0), fr0_bits);
    rd_report_retx(out);
    u32 lo = TCRT_VALID_LO - TCRT_RAM_BASE, hi = TCRT_STACK_LO - TCRT_RAM_BASE;
    for (u32 o = lo; o + 4 <= hi; o += 4) { /* a stored host address of the function under test: its guest one */
        u32 w, s;
        memcpy(&w, RAM + o, 4);
        memcpy(&s, g_snapshot + o, 4);
        if (w != s && rd_self_guest(w) != w) {
            w = rd_self_guest(w);
            memcpy(RAM + o, &w, 4);
        }
    }
    for (u32 o = lo; o < hi;) {
        if (RAM[o] == g_snapshot[o]) {
            o++;
            continue;
        }
        u32 end = o + 1;
        while (end < hi && RAM[end] != g_snapshot[end]) end++;
        fprintf(out, "W %08x ", o + TCRT_RAM_BASE);
        for (u32 k = o; k < end; k++) fprintf(out, "%02x", RAM[k]);
        fputc('\n', out);
        o = end;
    }
    tcrt_p4_report(out);
    rd_calls_report(out);
    rd_sh4_report(out);
    if (rd_code_read()) fprintf(out, "CODEREAD %08x\n", rd_code_read());
    ULONG_PTR slo = 0, shi = 0;
    GetCurrentThreadStackLimits(&slo, &shi);
    fprintf(out, "HOSTSTACK %08x %08x\n", (u32)slo, (u32)shi);
}

/* The raw guest-memory API, for the generated thunk (stack parameters) and for running raw matched C. */
/* rd_tracing: a T line is set (rd_trace.c watches the original's reads of an out-buffer and its own writes). */
u8 rd8(u32 a)
{
    if (rd_tracing) rd_trace_read(a, 1);
    return *(volatile u8 *)(uintptr_t)a;
}
u16 rd16(u32 a)
{
    if (rd_tracing) rd_trace_read(a, 2);
    return *(volatile u16 *)(uintptr_t)a;
}
u32 rd32(u32 a)
{
    if (rd_tracing) rd_trace_read(a, 4);
    return *(volatile u32 *)(uintptr_t)a;
}
f32 rdf32(u32 a)
{
    if (rd_tracing) rd_trace_read(a, 4);
    return *(volatile f32 *)(uintptr_t)a;
}
/* A store to the P4 operand-cache address array is logged, not made (tc_rt.c, tc_p4.c). */
void wr8(u32 a, u8 v)
{
    if (rd_tracing) rd_trace_write(a, 1);
    if (!tcrt_p4_store(a, v, 1)) *(volatile u8 *)(uintptr_t)a = v;
}
void wr16(u32 a, u16 v)
{
    if (rd_tracing) rd_trace_write(a, 2);
    if (!tcrt_p4_store(a, v, 2)) *(volatile u16 *)(uintptr_t)a = v;
}
void wr32(u32 a, u32 v)
{
    if (rd_tracing) rd_trace_write(a, 4);
    if (!tcrt_p4_store(a, v, 4)) *(volatile u32 *)(uintptr_t)a = v;
}
void wrf32(u32 a, f32 v)
{
    u32 u;
    memcpy(&u, &v, 4);
    wr32(a, u);
}
