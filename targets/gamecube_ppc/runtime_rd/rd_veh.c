/* GameCube readable-C runtime: the call log, and the exception handler that turns faults into observable events.
 *
 * A call to an original function (by name, or through a function pointer) jumps to its guest address. Guest RAM is
 * not executable, so the jump faults with the faulting address equal to the instruction pointer. The handler logs
 * the call in the oracle's record (TcOracle.callRecord: target, r3..r10, f1..f8 as doubles, s0..s7), with the
 * arguments read from the cdecl stack and placed where the PowerPC passes them (RdProto), and resumes in a
 * trampoline that returns the scripted value, exactly like the oracle's mocked callee: r3 = line C's word for the
 * call, f1 = line D's double.
 *
 * A jump to the uncached view (0xc0...) of a function is the same call: the target is logged in its 0x80... form.
 *
 * Pages holding machine code are kept inaccessible during the run: a data access there is let through one
 * instruction at a time (single-step) and a read of an instruction byte is reported as CODEREAD (rule R8).
 */
#include <string.h>
#include <windows.h>
#include "rd_internal.h"

#define LOG_INTS 17 /* target, r3..r10, s0..s7 */

static u32 g_rets[RD_RETS];
static u64 g_frets[RD_RETS];
static u32 g_ncalls;
static u32 g_call_int[RD_MAX_CALLS][LOG_INTS];
static u64 g_call_f[RD_MAX_CALLS][8];
static u32 g_code_read;
static u32 g_ret_word;
static u64 g_ret_dbits;
static u32 g_rearm[4];
static u32 g_nrearm;
static uintptr_t g_mod_lo, g_mod_hi;
static u32 g_self_host, g_self_guest; /* the function under test: host and guest address (rd_set_self) */

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
 * test is compiled here, so its own name is a host address. The thunk names it (rd_set_self), and a word it stores in
 * guest RAM, passes to a call or returns that equals that host address is reported as its guest address. */
void rd_set_self(u32 host, u32 guest)
{
    g_self_host = host;
    g_self_guest = guest;
}

u32 rd_self_guest(u32 w) { return g_self_host && w == g_self_host ? g_self_guest : w; }

static void log_call(u32 target, const u32 r[8], const u64 f[8], const u32 s[8])
{
    if (g_ncalls < RD_MAX_CALLS) {
        u32 *rec = g_call_int[g_ncalls];
        rec[0] = target;
        for (int i = 0; i < 8; i++) rec[1 + i] = rd_self_guest(r[i]);
        for (int i = 0; i < 8; i++) rec[9 + i] = rd_self_guest(s[i]);
        memcpy(g_call_f[g_ncalls], f, sizeof g_call_f[0]);
    }
    g_ncalls++;
}

static u32 next_word(void) { return g_ncalls < RD_RETS ? g_rets[g_ncalls] : 0; }
static u64 next_dbits(void) { return g_ncalls < RD_RETS ? g_frets[g_ncalls] : 0; }

/* The raw-C call API (tc_rt.h), for running raw matched C through this runtime (its own regression test). */
u32 tc_call(u32 target, const TcArgs *args)
{
    u32 r[8] = {0}, s[8] = {0};
    u64 f[8] = {0};
    for (int i = 0; args && i < 8; i++) {
        r[i] = args->r[i];
        s[i] = args->s[i];
        f[i] = dbits(args->f[i]);
    }
    u32 rv = next_word();
    log_call(tcrt_fold(target), r, f, s);
    return rv;
}

f64 tc_callf(u32 target, const TcArgs *args)
{
    u64 b = next_dbits();
    (void)tc_call(target, args);
    return bitsd(b);
}

static u32 __cdecl ret_word(void) { return g_ret_word; }
static f32 __cdecl ret_f32(void) { return (f32)bitsd(g_ret_dbits); }
static f64 __cdecl ret_f64(void) { return bitsd(g_ret_dbits); }
static u64 __cdecl ret_u64(void) { return (u64)g_ret_word << 32; } /* r3 high; the mocked callee leaves r4 = 0 */

static const RdProto *find_proto(u32 addr)
{
    for (int i = 0; i < rd_nprotos; i++)
        if (rd_protos[i].addr == addr) return &rd_protos[i];
    return NULL;
}

/* Unknown target: does the caller take a float from the x87 stack after the call? (MSVC: fstp/fld right after,
 * possibly behind the caller's stack cleanup; dword: f32, qword: f64.) A wrong guess can only make results differ,
 * never match. */
static char guess_ret(const u8 *p)
{
    for (int k = 0; k < 3; k++) {
        if (p[0] == 0x83 && p[1] == 0xc4) p += 3;
        else if (p[0] == 0x81 && p[1] == 0xc4) p += 6;
        else if (p[0] == 0x59) p += 1;
    }
    return p[0] == 0xd9 ? 'f' : p[0] == 0xdd ? 'd' : 'i';
}

/* One stack word (or an 8-aligned pair) past the registers: s0..s7 are the record's, a deeper word is dropped. */
static void put_stack(u32 *s, int *ns, u64 v, int wide)
{
    if (wide && (*ns & 1)) (*ns)++;
    if (wide) {
        if (*ns < 8) s[*ns] = (u32)(v >> 32);
        if (*ns + 1 < 8) s[*ns + 1] = (u32)v;
        *ns += 2;
    } else {
        if (*ns < 8) s[*ns] = (u32)v;
        (*ns)++;
    }
}

static LONG call_trap(CONTEXT *c)
{
    const u32 *sp = (const u32 *)(uintptr_t)c->Esp;
    if (sp[0] < g_mod_lo || sp[0] >= g_mod_hi) return EXCEPTION_CONTINUE_SEARCH;
    u32 target = tcrt_fold(c->Eip), r[8] = {0}, s[8] = {0};
    u64 f[8] = {0};
    const RdProto *p = find_proto(target);
    char ret = p ? p->ret : guess_ret((const u8 *)(uintptr_t)sp[0]);
    const u8 *ap = (const u8 *)(sp + 1); /* the cdecl arguments */
    int ni = 0, nf = 0, ns = 0;
    for (int k = 0; p && p->params[k] && p->params[k] != '*'; k++) {
        char t = p->params[k], reg = (p->regs && p->regs[0]) ? p->regs[k] : '.';
        u64 v = 0;
        f64 d = 0.0;
        if (t == 'd' || t == 'L') {
            memcpy(&v, ap, 8);
            ap += 8;
        } else {
            u32 w;
            memcpy(&w, ap, 4);
            ap += 4;
            v = t == 'c' ? (u32)(s32)(s8)w : t == 'C' ? (u8)w : t == 'h' ? (u32)(s32)(s16)w : t == 'H' ? (u16)w : w;
        }
        if (t == 'f') { /* a float register holds the single as a double (exact) */
            f32 x;
            u32 w = (u32)v;
            memcpy(&x, &w, 4);
            d = (f64)x;
            v = dbits(d);
        }
        int is_float = t == 'f' || t == 'd';
        if (reg >= 'a' && reg <= 'h') {
            f[reg - 'a'] = v; /* an int there: its bits */
            if (nf < reg - 'a' + 1) nf = reg - 'a' + 1;
        } else if (reg >= '0' && reg <= '7') {
            int i = reg - '0';
            if (t == 'L') {
                r[i] = (u32)(v >> 32);
                if (i + 1 < 8) r[i + 1] = (u32)v;
                i++;
            } else {
                r[i] = (u32)v;
            }
            if (ni < i + 1) ni = i + 1;
        } else if (reg >= 'S' && reg <= 'Z') {
            int at = reg - 'S';
            if (t == 'L' || t == 'd') {
                s[at] = (u32)(v >> 32);
                if (at + 1 < 8) s[at + 1] = (u32)v;
                at++;
            } else {
                s[at] = (u32)v;
            }
            if (ns < at + 1) ns = at + 1;
        } else if (reg == 'x') {
            continue; /* a stack word past s7: the call record has none */
        } else if (is_float) {
            if (nf < 8) f[nf++] = v;
            else put_stack(s, &ns, v, 1); /* past f8: a double in the next 8-aligned stack pair */
        } else if (t == 'L') {
            if (ni & 1) ni++; /* an odd-even pair: r3:r4, r5:r6, r7:r8, r9:r10 */
            if (ni + 1 < 8) {
                r[ni] = (u32)(v >> 32);
                r[ni + 1] = (u32)v;
                ni += 2;
            } else {
                ni = 8;
                put_stack(s, &ns, v, 1);
            }
        } else if (ni < 8) {
            r[ni++] = (u32)v;
        } else {
            put_stack(s, &ns, v, 0);
        }
    }
    g_ret_word = next_word();
    g_ret_dbits = next_dbits();
    log_call(target, r, f, s);
    if (ret == 'f') c->Eip = (DWORD)(uintptr_t)ret_f32;
    else if (ret == 'd') c->Eip = (DWORD)(uintptr_t)ret_f64;
    else if (ret == 'L') c->Eip = (DWORD)(uintptr_t)ret_u64;
    else c->Eip = (DWORD)(uintptr_t)ret_word;
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
    u32 addr = (u32)er->ExceptionInformation[1];
    if (addr == c->Eip) return call_trap(c);
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

void tcrt_set_call_returns(const u32 rets[16], const u64 frets[16])
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
}

u32 rd_code_read(void) { return g_code_read; }

void rd_calls_report(FILE *out)
{
    for (u32 c = 0; c < g_ncalls && c < RD_MAX_CALLS; c++) {
        fprintf(out, "CALL");
        for (int k = 0; k < 9; k++) fprintf(out, " %08x", g_call_int[c][k]);
        for (int k = 0; k < 8; k++) fprintf(out, " %016llx", (unsigned long long)g_call_f[c][k]);
        for (int k = 9; k < LOG_INTS; k++) fprintf(out, " %08x", g_call_int[c][k]);
        fputc('\n', out);
    }
    if (g_ncalls > RD_MAX_CALLS) fprintf(out, "FAULT too-many-calls\n");
}
