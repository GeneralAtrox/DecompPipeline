/* Xbox 360 readable-C runtime: the call log, and the exception handler that turns faults into observable events (the
 * x64 counterpart of targets/gamecube_ppc/runtime_rd/rd_veh.c).
 *
 * A call to an original function (by name, or through a function pointer) jumps to its guest address. Guest memory is
 * not executable, so the jump faults with the faulting address equal to the instruction pointer. The handler logs the
 * call in the oracle's record (x360_compare: target, r3..r10, f1..f13 as doubles, s0..s7, each 64-bit), with the
 * arguments read where the x64 host passes them (rcx, rdx, r8, r9 or xmm0..xmm3 by position, then the stack) and
 * placed where the PowerPC passes them (RdProto), and returns to the caller with the scripted values, exactly like the
 * oracle's mocked callee: r3 = line C's doubleword for the call (rax), f1 = line D's double (xmm0).
 *
 * Pages holding machine code are kept inaccessible during the run: a data access there is let through one instruction
 * at a time (single-step) and a read of an instruction byte is reported as CODEREAD (rule R8).
 *
 * The stricter judge (tc_strict.h) runs at each call once it is logged, as in tc_rt.c.
 */
#include <string.h>
#include <windows.h>
#include "rd_internal.h"

#define NFLOATS TCRT_CALL_FLOATS

typedef struct {
    u32 target;
    u64 r[8];
    u64 f[NFLOATS];
    u64 s[8];
} CallRecord;

static u64 g_rets[RD_RETS];
static u64 g_frets[RD_RETS];
static u32 g_ncalls;
static CallRecord g_calls[RD_MAX_CALLS];
static u32 g_code_read;
static u64 g_rearm[4];
static u32 g_nrearm;
static uintptr_t g_mod_lo, g_mod_hi;
static u64 g_self_host;
static u32 g_self_guest; /* the function under test: host and guest address (rd_set_self) */

static f64 bitsd(u64 u)
{
    f64 d;
    memcpy(&d, &u, 8);
    return d;
}

static u64 dbits(f64 d)
{
    u64 u;
    memcpy(&u, &d, 8);
    return u;
}

/* In the verification game.h every other original function is a pointer to its guest address, but the function under
 * test is compiled here, so its own name is a host address. The thunk names it (rd_set_self), and a value it stores in
 * guest memory, passes to a call or returns that equals that host address is reported as its guest address. */
void rd_set_self(u64 host, u32 guest)
{
    g_self_host = host;
    g_self_guest = guest;
}

u64 rd_self_guest(u64 w) { return g_self_host && w == g_self_host ? g_self_guest : w; }

static void log_call(u32 target, const u64 r[8], const u64 f[NFLOATS], const u64 s[8])
{
    if (g_ncalls < RD_MAX_CALLS) {
        CallRecord *c = &g_calls[g_ncalls];
        c->target = target;
        for (int i = 0; i < 8; i++) c->r[i] = rd_self_guest(r[i]);
        for (int i = 0; i < 8; i++) c->s[i] = rd_self_guest(s[i]);
        memcpy(c->f, f, sizeof c->f);
    }
    g_ncalls++;
}

/* The stricter judge (tc_strict.h). The call record's fields are 32-bit guest words; a pointer argument whose whole
 * register points into the host stack is a readable local's (the stack-pointer pairing's): its bytes are read and
 * written there. Any other is a guest one (rd_guest_copy). A fact's size is the callee's: a readable local smaller
 * than it would be overrun, as a C mistake can overrun it. The runtime models no register: no fr junk. */
static u64 g_tcs_args[25]; /* TCS_FIELDS: this call's argument registers whole (strict_call) */

static u8 *tcs_host(u32 p, u32 n)
{
    ULONG_PTR lo = 0, hi = 0;
    GetCurrentThreadStackLimits(&lo, &hi);
    for (int i = 1; i < 25; i++) {
        u64 a = g_tcs_args[i];
        if ((u32)a == p && a >= lo && a < hi && hi - a >= n) return (u8 *)(uintptr_t)a;
    }
    return NULL;
}

static int tcs_read(u32 p, u32 n, u8 *out)
{
    u8 *h = tcs_host(p, n);
    if (!h) return rd_guest_copy(p, n, out, NULL);
    memcpy(out, h, n);
    return 1;
}

static void tcs_write(u32 p, u32 n, const u8 *in)
{
    u8 *h = tcs_host(p, n);
    if (!h) (void)rd_guest_copy(p, n, NULL, in);
    else memcpy(h, in, n);
}

static void tcs_scratch(int i, u32 bits) { (void)i, (void)bits; }

#include "tc_strict.h"
_Static_assert(sizeof g_tcs_args / sizeof g_tcs_args[0] == TCS_FIELDS, "g_tcs_args: one per call record field");

/* The call just logged (number g_ncalls - 1), in tc_strict.h's fields (TcStrict.field): target, r3..r10's low words
 * 1..8, s0..s7's 17..24. */
static void strict_call(u32 target, const u64 r[8], const u64 s[8])
{
    u32 rec[TCS_FIELDS] = {0};
    rec[0] = target;
    for (int i = 0; i < 8; i++) {
        g_tcs_args[1 + i] = r[i];
        g_tcs_args[17 + i] = s[i];
        rec[1 + i] = (u32)rd_self_guest(r[i]);
        rec[17 + i] = (u32)rd_self_guest(s[i]);
    }
    tcs_call(g_ncalls - 1, target, rec);
    memset(g_tcs_args, 0, sizeof g_tcs_args);
}

static u64 next_word(void) { return g_ncalls < RD_RETS ? g_rets[g_ncalls] : 0; }
static u64 next_dbits(void) { return g_ncalls < RD_RETS ? g_frets[g_ncalls] : 0; }

/* The raw-C call API (tc_rt.h), for running raw matched C through this runtime (its own regression test). */
u64 tc_call(u32 target, const TcArgs *args)
{
    u64 r[8] = {0}, f[NFLOATS] = {0}, s[8] = {0};
    for (int i = 0; args && i < 8; i++) {
        r[i] = args->r[i];
        s[i] = args->s[i];
    }
    for (int i = 0; args && i < NFLOATS; i++) f[i] = dbits(args->f[i]);
    u64 rv = next_word();
    log_call(target, r, f, s);
    strict_call(target, r, s);
    return rv;
}

f64 tc_callf(u32 target, const TcArgs *args)
{
    u64 b = next_dbits();
    (void)tc_call(target, args);
    return bitsd(b);
}

/* The last call's other result: its r3 after a tc_callf, its f1 after a tc_call (the mocked callee sets both). */
u64 tc_lastr(void) { return g_ncalls >= 1 && g_ncalls <= RD_RETS ? g_rets[g_ncalls - 1] : 0; }
f64 tc_lastf(void) { return bitsd(g_ncalls >= 1 && g_ncalls <= RD_RETS ? g_frets[g_ncalls - 1] : 0); }

static const RdProto *find_proto(u32 addr)
{
    for (int i = 0; i < rd_nprotos; i++)
        if (rd_protos[i].addr == addr) return &rd_protos[i];
    return NULL;
}

/* Unknown result type: does the caller take an f32 from xmm0 after the call (movss, cvtss2sd: the F3 0F prefix, maybe
 * behind a REX byte)? Else xmm0 holds the double. rax holds the word either way. A wrong guess can only make results
 * differ, never match. */
static char guess_ret(const u8 *p)
{
    if (p[0] >= 0x40 && p[0] <= 0x4f) p++;
    return p[0] == 0xf3 && (p[1] == 0x0f || (p[1] >= 0x40 && p[1] <= 0x4f && p[2] == 0x0f)) ? 'f' : 'd';
}

/* An integer of kind t, as the original's 64-bit register holds it. */
static u64 widen(char t, u64 v)
{
    switch (t) {
    case 'c': return (u64)(s64)(s8)v;
    case 'C': return (u8)v;
    case 'h': return (u64)(s64)(s16)v;
    case 'H': return (u16)v;
    case 'i': return (u64)(s64)(s32)v;
    case 'I': return (u32)v;
    default: return v; /* L, P */
    }
}

/* Parameter k of the host call: rcx, rdx, r8, r9 or xmm0..xmm3 by position, then [rsp+8+8k] (rsp: the return
 * address). A float parameter's 64 bits are the double (d) or the single in the low word (f). */
static u64 host_arg(const CONTEXT *c, int k, int is_float)
{
    u64 v = 0;
    if (k < 4 && is_float) return (&c->Xmm0)[k].Low;
    if (k < 4) {
        const DWORD64 ints[4] = {c->Rcx, c->Rdx, c->R8, c->R9};
        return ints[k];
    }
    memcpy(&v, (const u8 *)(uintptr_t)c->Rsp + 8 + 8 * (size_t)k, 8);
    return v;
}

static LONG call_trap(CONTEXT *c)
{
    u64 ret_addr;
    memcpy(&ret_addr, (const void *)(uintptr_t)c->Rsp, 8);
    if (ret_addr < g_mod_lo || ret_addr >= g_mod_hi) return EXCEPTION_CONTINUE_SEARCH;
    u32 target = (u32)c->Rip;
    u64 r[8] = {0}, f[NFLOATS] = {0}, s[8] = {0};
    const RdProto *p = find_proto(target);
    int ni = 0, nf = 0, ns = 0;
    for (int k = 0; p && p->params[k] && p->params[k] != '*'; k++) {
        char t = p->params[k], reg = (p->regs && p->regs[0]) ? p->regs[k] : '.';
        int is_float = t == 'f' || t == 'd';
        u64 v = host_arg(c, k, is_float);
        if (t == 'f') { /* a float register holds the single as a double (exact) */
            f32 x;
            u32 w = (u32)v;
            memcpy(&x, &w, 4);
            v = dbits((f64)x);
        } else if (!is_float) {
            v = widen(t, v);
        }
        if (reg >= 'a' && reg < 'a' + NFLOATS) {
            f[reg - 'a'] = v; /* an int there: its bits */
            if (nf < reg - 'a' + 1) nf = reg - 'a' + 1;
        } else if (reg >= '0' && reg <= '7') {
            r[reg - '0'] = v; /* a float there: its bits */
            if (ni < reg - '0' + 1) ni = reg - '0' + 1;
        } else if (reg >= 'S' && reg <= 'Z') {
            s[reg - 'S'] = v;
            if (ns < reg - 'S' + 1) ns = reg - 'S' + 1;
        } else if (reg == 'x') {
            continue; /* a stack doubleword past s7: the call record has none */
        } else if (is_float) {
            if (nf < NFLOATS) f[nf++] = v;
            else if (ns < 8) s[ns++] = v;
        } else if (ni < 8) {
            r[ni++] = v;
        } else if (ns < 8) {
            s[ns++] = v;
        }
    }
    u64 word = next_word(), fb = next_dbits();
    log_call(target, r, f, s);
    strict_call(target, r, s);
    char ret = p ? p->ret : '?';
    if (ret == '?') ret = guess_ret((const u8 *)(uintptr_t)ret_addr);
    c->Rax = word;
    if (ret == 'f') {
        f32 x = (f32)bitsd(fb);
        u32 w;
        memcpy(&w, &x, 4);
        c->Xmm0.Low = w;
    } else {
        c->Xmm0.Low = fb;
    }
    c->Xmm0.High = 0;
    c->Rip = ret_addr; /* return to the caller, as the callee's ret would */
    c->Rsp += 8;
    return EXCEPTION_CONTINUE_EXECUTION;
}

static LONG CALLBACK handler(EXCEPTION_POINTERS *ep)
{
    EXCEPTION_RECORD *er = ep->ExceptionRecord;
    CONTEXT *c = ep->ContextRecord;
    if (er->ExceptionCode == EXCEPTION_SINGLE_STEP) {
        int ours = g_nrearm != 0;
        while (g_nrearm) rd_page_close(g_rearm[--g_nrearm]);
        return ours ? EXCEPTION_CONTINUE_EXECUTION : EXCEPTION_CONTINUE_SEARCH;
    }
    if (er->ExceptionCode != EXCEPTION_ACCESS_VIOLATION || er->NumberParameters < 2) return EXCEPTION_CONTINUE_SEARCH;
    u64 addr = (u64)er->ExceptionInformation[1];
    if (addr == c->Rip && addr < 0x100000000ull) return call_trap(c);
    if (!rd_page_guarded(addr) || g_nrearm >= 4) return EXCEPTION_CONTINUE_SEARCH;
    if (er->ExceptionInformation[0] == 0 && rd_is_code((u32)addr) && !g_code_read) g_code_read = (u32)addr;
    rd_page_open(addr);
    g_rearm[g_nrearm++] = addr;
    c->EFlags |= 0x100; /* trap flag: fault again after this one instruction */
    return EXCEPTION_CONTINUE_EXECUTION;
}

int rd_veh_install(void)
{
    HMODULE m = GetModuleHandleW(NULL);
    const IMAGE_DOS_HEADER *dos = (const IMAGE_DOS_HEADER *)m;
    const IMAGE_NT_HEADERS *nt = (const IMAGE_NT_HEADERS *)((const u8 *)m + dos->e_lfanew);
    g_mod_lo = (uintptr_t)m;
    g_mod_hi = g_mod_lo + nt->OptionalHeader.SizeOfImage;
    return AddVectoredExceptionHandler(1, handler) != NULL;
}

void tcrt_set_call_returns(const u64 rets[16], const u64 frets[16])
{
    memcpy(g_rets, rets, sizeof g_rets);
    memcpy(g_frets, frets, sizeof g_frets);
}

void rd_calls_reset(void)
{
    memset(g_rets, 0, sizeof g_rets);
    memset(g_frets, 0, sizeof g_frets);
    g_ncalls = 0;
    g_code_read = 0;
    g_nrearm = 0;
    g_self_host = 0;
    g_self_guest = 0;
    tcrt_strict_reset(); /* the vector's key and snapshot requests: N and W come after V */
}

u32 rd_code_read(void) { return g_code_read; }

void rd_calls_report(FILE *out)
{
    for (u32 k = 0; k < g_ncalls && k < RD_MAX_CALLS; k++) {
        const CallRecord *c = &g_calls[k];
        fprintf(out, "CALL %08x", c->target);
        for (int j = 0; j < 8; j++) fprintf(out, " %016llx", (unsigned long long)c->r[j]);
        for (int j = 0; j < NFLOATS; j++) fprintf(out, " %016llx", (unsigned long long)c->f[j]);
        for (int j = 0; j < 8; j++) fprintf(out, " %016llx", (unsigned long long)c->s[j]);
        fputc('\n', out);
    }
    if (g_ncalls > RD_MAX_CALLS) fprintf(out, "FAULT too-many-calls\n");
}
