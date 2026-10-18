/* Readable-C runtime: the call log, and the exception handler that turns faults into observable events.
 *
 * A call to an original function (by name, or through a function pointer) jumps to its guest address. Guest RAM is
 * not executable, so the jump faults with the faulting address equal to the instruction pointer. The handler logs
 * the call (arguments read from the cdecl stack and placed in the SH-4 argument registers, or stack words, they
 * would occupy) and
 * resumes in a trampoline that returns the scripted value, exactly like the mocked callees of the oracle.
 *
 * A jump to the 0x0c... mirror of a function is the same call: the target is logged in its 0x8c... form.
 *
 * Pages holding machine code are kept inaccessible during the run: a data access there is let through one
 * instruction at a time (single-step) and a read of an instruction byte is reported as CODEREAD (rule R8).
 */
#include <string.h>
#include <windows.h>
#include "rd_internal.h"

static u32 g_rets[16];
static u32 g_ncalls;
static u32 g_call_log[RD_MAX_CALLS][RD_LOG_FIELDS];
static u32 g_low[4]; /* r0..r3 at entry (line L), for a thunk passing a parameter there (tcrt_low) */
static u32 g_retx;   /* the extra result word (a nonstandard result register, a u64's high word) */
static int g_has_retx;
static u32 g_va_host, g_va_guest; /* a variadic function's first variable word: host and guest address */
static u32 g_code_read;
static u32 g_ret_bits;
static u32 g_rearm[4];
static u32 g_nrearm;
static uintptr_t g_mod_lo, g_mod_hi;
static u32 g_self_host, g_self_guest; /* the function under test: host and guest address (rd_set_self) */

static f32 u2f(u32 u) { f32 f; memcpy(&f, &u, 4); return f; }
static u32 f2u(f32 f) { u32 u; memcpy(&u, &f, 4); return u; }

static u32 next_return(void) { return g_ncalls < 16 ? g_rets[g_ncalls] : 0; }

/* In the verification game.h every other original function is a pointer to its guest address, but the function under
 * test is compiled here, so its own name is a host address. The thunk names it (rd_set_self), and a word it stores in
 * guest RAM, passes to a call or returns that equals that host address is reported as its guest address (8c06dc28
 * stores its own address in a hook table). */
void rd_set_self(u32 host, u32 guest)
{
    g_self_host = host;
    g_self_guest = guest;
}

u32 rd_self_guest(u32 w) { return g_self_host && w == g_self_host ? g_self_guest : w; }

/* lo: r0..r3 at the call (fields 25..28); fr12..fr15, r8..r14 and, with line Q, the FPU come from the state. */
static void log_call(u32 target, const u32 r[4], const u32 fr[8], const u32 st[8], const u32 lo[4])
{
    if (g_ncalls < RD_MAX_CALLS) {
        u32 *rec = g_call_log[g_ncalls];
        memset(rec, 0, sizeof g_call_log[0]);
        rec[0] = target;
        for (int i = 0; i < 4; i++) rec[1 + i] = rd_self_guest(r[i]);
        for (int i = 0; i < 8; i++) rec[13 + i] = rd_self_guest(st[i]);
        for (int i = 0; i < 4; i++) rec[25 + i] = rd_self_guest(lo[i]);
        rd_sh4_log(rec, fr);
    }
    rd_sh4_live(target, g_ncalls); /* after the record: the callee saw the old rN */
    g_ncalls++;
}

static void out_copy(u32 call, u32 target, const u32 *rec);

/* The raw-C call API (runs raw matched C through this runtime, for its own regression, and for the original's bytes
 * at out-pointer calls: vectors line B). */
u32 tc_call(u32 target, const TcArgs *args)
{
    u32 r[4] = {0}, fr[8] = {0}, st[8] = {0}, lo[4] = {0};
    for (int i = 0; args && i < 4; i++) r[i] = args->r[i];
    for (int i = 0; args && i < 8; i++) fr[i] = f2u(args->fr[i]);
    for (int i = 0; args && i < 8; i++) st[i] = args->s[i];
    for (int i = 0; args && i < 4; i++) lo[i] = args->rlo[i];
    u32 rv = next_return();
    log_call(tcrt_fold(target), r, fr, st, lo);
    if (g_ncalls <= RD_MAX_CALLS) out_copy(g_ncalls - 1, tcrt_fold(target), g_call_log[g_ncalls - 1]);
    if (rd_tracing && g_ncalls <= RD_MAX_CALLS) rd_trace_call(g_ncalls - 1, tcrt_fold(target), g_call_log[g_ncalls - 1]);
    rd_sh4_result(rv); /* the oracle's mocked callee leaves its result in r0 and fr0 */
    return rv;
}

void tcrt_set_low(const u32 bits[4]) { memcpy(g_low, bits, sizeof g_low); }

u32 tcrt_low(int i) { return (i >= 0 && i < 4) ? g_low[i] : 0; }

void tcrt_set_retx(u32 v)
{
    g_retx = v;
    g_has_retx = 1;
}

void rd_set_va_anchor(u32 host, u32 guest)
{
    g_va_host = host;
    g_va_guest = guest;
}

void rd_report_retx(FILE *out)
{
    if (g_has_retx) fprintf(out, "RETX %08x\n", g_retx);
    if (g_va_host) fprintf(out, "VAANCHOR %08x %08x\n", g_va_host, g_va_guest);
}

f32 tc_callf(u32 target, const TcArgs *args) { return u2f(tc_call(target, args)); }

static u32 __cdecl ret_u32(void) { return g_ret_bits; }
static f32 __cdecl ret_f32(void) { return u2f(g_ret_bits); }
static u64 __cdecl ret_u64(void) { return g_ret_bits; } /* r1 is not scripted (tc_call returns r0 only) */

static const RdProto *find_proto(u32 addr)
{
    for (int i = 0; i < rd_nprotos; i++)
        if (rd_protos[i].addr == addr) return &rd_protos[i];
    return NULL;
}

/* Unknown target: does the caller take an f32 from the x87 stack after the call? (MSVC: fstp/fld right after,
 * possibly behind the caller's stack cleanup.) A wrong guess can only make results differ, never match. */
static char guess_ret(const u8 *p)
{
    for (int k = 0; k < 3; k++) {
        if (p[0] == 0x83 && p[1] == 0xc4) p += 3;
        else if (p[0] == 0x81 && p[1] == 0xc4) p += 6;
        else if (p[0] == 0x59) p += 1;
    }
    return (p[0] == 0xd9 || p[0] == 0xdd) ? 'f' : 'i';
}

/* Out-pointers into the caller's stack (vectors lines O and D, from core/readable_outptr.py). The oracle mocks a
 * callee: it writes nothing, and the original reads back from its own guest stack what is there at the call (the
 * vector's bytes, and what the original itself wrote before the call): that is the callee's "output". A readable
 * local lives on the host stack, so it would never see it. For call N to TARGET, whose record field FIELD the
 * original passes as the guest stack address GUEST (the pointee of a non-const pointer parameter of the callee's
 * prototype, SIZE bytes):
 *  - B, run on the matched raw C (the original's own code, passing GUEST): the runner paints [GUEST, GUEST + SIZE)
 *    with rd_canary ^ FLIP (B's optional 6th field) before the vector's M lines, and prints the SIZE bytes there as
 *    they are at the call, line "OB N TARGET FIELD hex" (core/readable_outptr.py runs two paints and tells the bytes
 *    the original wrote before the call from the paints and the vector's own bytes);
 *  - D (the vector's bytes where the original did not write, "xx" where it did), run on readable C: copied at the
 *    call to the host address the readable call passed in FIELD, except the "xx" bytes, which keep what the readable
 *    code wrote there (it must have written what the original did): the readable local then holds what the
 *    original's local holds after the mocked call, whatever initializer the readable code gave it;
 *  - O on readable C (when the raw run is not available): the vector's bytes at GUEST are copied, but only where the
 *    host bytes still hold the runner's paint (rd_canary: bytes the readable code has not written; a whole aligned
 *    word only when all four bytes do).
 * The judge pairs the two pointers afterwards (map_stack_args) as before. */
#define RD_MAX_OUTS 64
#define RD_OUT_POOL 0x40000u
static struct { u32 call, target, field, guest, size; const u8 *data; } g_outs[RD_MAX_OUTS];
static int g_nouts;
static u8 g_out_pool[RD_OUT_POOL];
static u32 g_out_used;
static FILE *g_out_log;

u8 rd_canary(uintptr_t a) { return (u8)(0x91u + (u32)((a * 37u + (a >> 7) * 11u) % 0x60u)); }

static int out_add(u32 call, u32 target, u32 field, u32 guest, u32 size)
{
    if (g_nouts >= RD_MAX_OUTS || field >= RD_LOG_FIELDS || size > 0x2000u) return -1;
    g_outs[g_nouts].call = call;
    g_outs[g_nouts].target = target;
    g_outs[g_nouts].field = field;
    g_outs[g_nouts].guest = guest;
    g_outs[g_nouts].size = size;
    g_outs[g_nouts].data = NULL;
    return g_nouts++;
}

void rd_out_add(u32 call, u32 target, u32 field, u32 guest, u32 size)
{
    (void)out_add(call, target, field, guest, size);
}

void rd_out_data(u32 call, u32 target, u32 field, const u8 *bytes, const u8 *mask, u32 size)
{
    if (size > (RD_OUT_POOL - g_out_used) / 2) return;
    int i = out_add(call, target, field, 0, size);
    if (i < 0) return;
    memcpy(g_out_pool + g_out_used, bytes, size);
    memcpy(g_out_pool + g_out_used + size, mask, size);
    g_outs[i].data = g_out_pool + g_out_used;
    g_out_used += 2 * size;
}

void rd_out_log(FILE *out) { g_out_log = out; }

/* Only an O line (no data: the canary fallback) needs the host stack painted; D lines copy their bytes whatever the
 * host stack holds, and painting it would change what readable code reads from a local it never wrote. */
int rd_out_paint(void)
{
    for (int i = 0; i < g_nouts; i++)
        if (!g_outs[i].data) return 1;
    return 0;
}

static int unwritten(uintptr_t h, u32 n)
{
    for (u32 k = 0; k < n; k++)
        if (*(const u8 *)(h + k) != rd_canary(h + k)) return 0;
    return 1;
}

static void out_copy(u32 call, u32 target, const u32 *rec)
{
    if (!g_nouts) return;
    ULONG_PTR lo = 0, hi = 0;
    GetCurrentThreadStackLimits(&lo, &hi);
    for (int i = 0; i < g_nouts; i++) {
        uintptr_t h = rec[g_outs[i].field], g = g_outs[i].guest;
        u32 n = g_outs[i].size;
        if (g_outs[i].call != call || g_outs[i].target != target) continue;
        int guest_ok = g >= TCRT_STACK_LO && g + n <= TCRT_RAM_BASE + TCRT_RAM_SIZE;
        if (!g_outs[i].data && h == g && guest_ok && g_out_log) { /* the raw run: the original's bytes at the call */
            fprintf(g_out_log, "OB %u %08x %u ", call, target, g_outs[i].field);
            for (u32 k = 0; k < n; k++) fprintf(g_out_log, "%02x", ((const u8 *)g)[k]);
            fprintf(g_out_log, "\n");
            continue;
        }
        if (h < lo || h + n > hi || h + n < h) continue; /* the readable call passed no host stack object there */
        if (g_outs[i].data) { /* mask byte 0: the original wrote that byte itself; the readable code's write stays */
            for (u32 k = 0; k < n; k++)
                if (g_outs[i].data[n + k]) ((u8 *)h)[k] = g_outs[i].data[k];
            continue;
        }
        if (!guest_ok) continue;
        for (u32 k = 0; k < n;) {
            u32 step = ((h + k) & 3u) == 0 && k + 4 <= n ? 4 : 1;
            if (unwritten(h + k, step)) memcpy((void *)(h + k), (const void *)(g + k), step);
            k += step;
        }
    }
}

static LONG call_trap(CONTEXT *c)
{
    const u32 *sp = (const u32 *)(uintptr_t)c->Esp;
    if (sp[0] < g_mod_lo || sp[0] >= g_mod_hi) return EXCEPTION_CONTINUE_SEARCH;
    u32 target = tcrt_fold(c->Eip), r[4] = {0}, fr[8] = {0}, st[8] = {0}, lo[4] = {0}; /* 0x0c... form too */
    const RdProto *p = find_proto(target);
    char ret = p ? p->ret : guess_ret((const u8 *)(uintptr_t)sp[0]);
    int ni = 0, nf = 0, ns = 0;
    for (int k = 0; p && p->params[k]; k++) {
        if (p->params[k] == '*') { /* variadic: the caller's remaining cdecl words follow the named stack words */
            for (int j = k; ns < 8; j++) st[ns++] = sp[1 + j];
            break;
        }
        u32 v = sp[1 + k];
        char reg = (p->regs && p->regs[0]) ? p->regs[k] : '.';
        int named = (reg >= '0' && reg <= '7') || (reg >= 'a' && reg <= 'h') || reg == 's' || reg == 'x' ||
                    (reg >= 'S' && reg <= 'Z'); /* see RdProto.regs */
        switch (p->params[k]) {
        case 'f':
            if (named) break;
            if (nf < 8) fr[nf++] = v;
            else if (ns < 8) st[ns++] = v; /* past fr11: the next stack word */
            continue;
        case 'c': v = (u32)(s32)(s8)v; break;
        case 'C': v = (u8)v; break;
        case 'h': v = (u32)(s32)(s16)v; break;
        case 'H': v = (u16)v; break;
        default: break;
        }
        if (reg >= 'a' && reg <= 'h') {
            fr[reg - 'a'] = v; /* fr4..fr11, also an int there: its bits */
            if (nf < reg - 'a' + 1) nf = reg - 'a' + 1;
        } else if (reg == 's') {
            if (ns < 8) st[ns++] = v;
        } else if (reg >= 'S' && reg <= 'Z') {
            st[reg - 'S'] = v; /* the stack word @(4N,r15) */
            if (ns < reg - 'S' + 1) ns = reg - 'S' + 1;
        } else if (reg == 'x') {
            continue; /* a stack word past s7: the call record has none */
        } else if (reg >= '0' && reg <= '3') lo[reg - '0'] = v;
        else if (reg >= '4' && reg <= '7') {
            r[reg - '4'] = v;
            if (ni < reg - '3') ni = reg - '3'; /* the C-convention words after it go after it */
        } else if (ni < 4) r[ni++] = v;
        else if (ns < 8) st[ns++] = v; /* past r7: the next stack word */
    }
    g_ret_bits = next_return();
    log_call(target, r, fr, st, lo);
    if (g_nouts && g_ncalls <= RD_MAX_CALLS) out_copy(g_ncalls - 1, target, g_call_log[g_ncalls - 1]);
    rd_sh4_result(g_ret_bits);
    if (ret == 'f') c->Eip = (DWORD)(uintptr_t)ret_f32;
    else if (ret == 'L') c->Eip = (DWORD)(uintptr_t)ret_u64;
    else c->Eip = (DWORD)(uintptr_t)ret_u32;
    return EXCEPTION_CONTINUE_EXECUTION;
}

static LONG CALLBACK handler(EXCEPTION_POINTERS *ep)
{
    EXCEPTION_RECORD *er = ep->ExceptionRecord;
    CONTEXT *c = ep->ContextRecord;
    if (er->ExceptionCode == EXCEPTION_SINGLE_STEP) {
        int ours = rd_p4_step();
        if (g_nrearm) ours = 1;
        while (g_nrearm) rd_page_close(g_rearm[--g_nrearm]);
        return ours ? EXCEPTION_CONTINUE_EXECUTION : EXCEPTION_CONTINUE_SEARCH;
    }
    if (er->ExceptionCode != EXCEPTION_ACCESS_VIOLATION || er->NumberParameters < 2) return EXCEPTION_CONTINUE_SEARCH;
    u32 addr = (u32)er->ExceptionInformation[1];
    if (addr == c->Eip) return call_trap(c);
    if (rd_p4_fault(addr, er->ExceptionInformation[0] == 1, c)) return EXCEPTION_CONTINUE_EXECUTION;
    if (!rd_page_guarded(addr) || g_nrearm >= 4) return EXCEPTION_CONTINUE_SEARCH;
    if (er->ExceptionInformation[0] == 0 && rd_is_code(addr) && !g_code_read) g_code_read = tcrt_fold(addr);
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

void rd_calls_reset(const u32 rets[16])
{
    memcpy(g_rets, rets, sizeof g_rets);
    g_ncalls = 0;
    g_code_read = 0;
    g_nrearm = 0;
}

/* tcrt_reset only (rd_calls_reset also takes the scripted returns, set again before each run). */
void rd_abi_reset(void)
{
    memset(g_low, 0, sizeof g_low);
    g_retx = 0;
    g_has_retx = 0;
    g_va_host = 0;
    g_va_guest = 0;
    g_nouts = 0;
    g_out_used = 0;
    g_self_host = 0;
    g_self_guest = 0;
    rd_trace_reset();
}

u32 rd_code_read(void) { return g_code_read; }

void rd_calls_report(FILE *out)
{
    int fields = rd_sh4_fpu_state() ? RD_LOG_FIELDS : 36; /* as tc_rt.c */
    for (u32 c = 0; c < g_ncalls && c < RD_MAX_CALLS; c++) {
        fprintf(out, "CALL");
        for (int k = 0; k < fields; k++) fprintf(out, " %08x", g_call_log[c][k]);
        fputc('\n', out);
    }
    if (g_ncalls > RD_MAX_CALLS) fprintf(out, "FAULT too-many-calls\n");
}
