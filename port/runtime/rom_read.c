/* Reads of the boot ROM area. The Dreamcast's boot ROM (BIOS) is at physical 0x00000000..0x001fffff, seen at
 * 0x00000000 (U0/P0), 0x80000000 (P1) and 0xa0000000 (P2). Games read it by accident: a NULL table pointer plus
 * an offset reads ROM bytes on the console, where nothing faults. The port has no BIOS and the host cannot map its
 * first 64 KB, so such a read faults; the exception handler (dispatch.c) asks rom_read() to finish the instruction
 * as if the ROM held zeros. A store there (a NULL object pointer plus a field offset; PS2's Mtx_CalcPoint writing
 * through 0x6c when Fighter_UpdateLook's target lookup returns 0) changes nothing on the console, so it is skipped.
 *
 * Only the load forms MSVC emits for 32-bit readable C are modelled: mov/movzx/movsx into a general register, and
 * movss/movsd/movd/movq/movups/movaps/movdqu/movdqa/movlps into an XMM register, and the scalar arithmetic that
 * takes its second operand from memory (addss/subss/mulss/divss and their sd forms, with that operand 0), and cmp
 * and test with a memory operand (the flags a 0 operand gives). Any other
 * instruction is reported and ends the run, so a new form shows up in the log as the thing to add. Stores: mov
 * from a general register or an immediate, the XMM stores movss/movsd/movups/movaps/movdqu/movdqa/movd/movq/
 * movlps, and the x87 fst/fstp of a float or a double (fstp still pops the x87 stack). */
#include <string.h>
#include <windows.h>
#include "port.h"

#define ROM_SIZE 0x00200000u
#define XMM_OFFSET 160u /* FXSAVE area: XMM0 */

static int in_rom(u32 a)
{
    return a < ROM_SIZE || a - 0x80000000u < ROM_SIZE || a - 0xa0000000u < ROM_SIZE;
}

/* A general register's slot in the context, in ModRM register order. */
static DWORD *gpr(CONTEXT *c, u32 r)
{
    DWORD *regs[8] = {&c->Eax, &c->Ecx, &c->Edx, &c->Ebx, &c->Esp, &c->Ebp, &c->Esi, &c->Edi};
    return regs[r & 7u];
}

/* The length of a ModRM memory operand (ModRM, SIB, displacement) starting at p. */
static u32 modrm_len(const u8 *p)
{
    u32 mod = p[0] >> 6, rm = p[0] & 7u, n = 1;
    if (mod == 3) return 0; /* a register operand: not a read */
    if (rm == 4) {
        n++;
        if (mod == 0 && (p[1] & 7u) == 5) n += 4;
    }
    if (mod == 0 && rm == 5) n += 4;
    else if (mod == 1) n += 1;
    else if (mod == 2) n += 4;
    return n;
}

/* XMM load: how many low bytes it writes (the rest of the register is cleared, except for movlps), 0 if the
 * opcode is not one of the modelled loads. */
static u32 xmm_load(u8 prefix, u8 op, int *keep_high)
{
    *keep_high = 0;
    if (op == 0x10 && prefix == 0xf3) return 4;                              /* movss */
    if (op == 0x10 && prefix == 0xf2) return 8;                              /* movsd */
    if ((op == 0x10 || op == 0x28) && (prefix == 0 || prefix == 0x66)) return 16; /* movups/movaps/movupd/movapd */
    if (op == 0x6f && (prefix == 0x66 || prefix == 0xf3)) return 16;         /* movdqa/movdqu */
    if (op == 0x6e && prefix == 0x66) return 4;                              /* movd */
    if (op == 0x7e && prefix == 0xf3) return 8;                              /* movq */
    if (op == 0x12 && prefix == 0) {                                         /* movlps */
        *keep_high = 1;
        return 8;
    }
    return 0;
}

/* Scalar arithmetic with a memory operand that reads as 0: the low lane of the XMM register becomes x op 0, in the
 * game thread's rounding mode (the handler runs on the faulting thread). Returns 0 if the opcode is not one. */
static int xmm_scalar(u8 prefix, u8 op, u8 *xmm)
{
    if ((prefix != 0xf3 && prefix != 0xf2) || (op != 0x58 && op != 0x59 && op != 0x5c && op != 0x5e)) return 0;
    if (prefix == 0xf3) {
        volatile f32 x, zero = 0.0f;
        memcpy((void *)&x, xmm, 4);
        x = op == 0x58 ? x + zero : op == 0x59 ? x * zero : op == 0x5c ? x - zero : x / zero;
        memcpy(xmm, (const void *)&x, 4);
    } else {
        volatile double x, zero = 0.0;
        memcpy((void *)&x, xmm, 8);
        x = op == 0x58 ? x + zero : op == 0x59 ? x * zero : op == 0x5c ? x - zero : x / zero;
        memcpy(xmm, (const void *)&x, 8);
    }
    return 1;
}

/* The first site of each read, as port.exe+OFFSET (as the watchdog logs it, so tools/symbolize.py names the
 * function), then the words on the stack that point into port.exe: the callers, since the guest NULL usually
 * comes from one of them. */
static void log_site(const CONTEXT *c, u32 addr, int store)
{
    static u32 seen[64], n;
    u32 eip = (u32)c->Eip, lo = (u32)(uintptr_t)GetModuleHandleW(NULL);
    const IMAGE_DOS_HEADER *dos = (const IMAGE_DOS_HEADER *)(uintptr_t)lo;
    const IMAGE_NT_HEADERS *nt = (const IMAGE_NT_HEADERS *)(uintptr_t)(lo + (u32)dos->e_lfanew);
    const u32 *sp = (const u32 *)(uintptr_t)c->Esp;
    u32 hi = lo + nt->OptionalHeader.SizeOfImage;
    for (u32 i = 0; i < n; i++)
        if (seen[i] == eip) return;
    if (n < 64) seen[n++] = eip;
    port_log(store ? "boot ROM write of %08x at port.exe+%08x: skipped (the console's ROM ignores it)\n"
                   : "boot ROM read of %08x at port.exe+%08x: reads as zero (the port has no BIOS)\n", addr, eip - lo);
    for (int i = 0, shown = 0; i < 64 && shown < 6; i++)
        if (sp[i] > lo && sp[i] < hi) {
            port_log("  stack+%03x: port.exe+%08x\n", i * 4, sp[i] - lo);
            shown++;
        }
}

/* fstp's pop: the x87 TOP moves up by one and the register it left is empty, in the legacy save area and in the
 * FXSAVE image (status word at byte 2, abridged tag byte at 4), whichever the context restores. */
static void x87_pop(CONTEXT *c)
{
    u32 top = (c->FloatSave.StatusWord >> 11) & 7u;
    c->FloatSave.TagWord |= 3u << (2u * top);
    c->FloatSave.StatusWord = (c->FloatSave.StatusWord & ~0x3800u) | (((top + 1u) & 7u) << 11);
    if (c->ContextFlags & CONTEXT_EXTENDED_REGISTERS & ~CONTEXT_i386) {
        u8 *fx = c->ExtendedRegisters;
        u16 sw = (u16)(fx[2] | fx[3] << 8);
        top = (sw >> 11) & 7u;
        sw = (u16)((sw & ~0x3800u) | (((top + 1u) & 7u) << 11));
        fx[2] = (u8)sw;
        fx[3] = (u8)(sw >> 8);
        fx[4] &= (u8)~(1u << top);
    }
}

/* A store's length from its opcode at p (after the prefixes), 0 if it is not one of the modelled stores; *pop is
 * set for fstp. */
static u32 mlen(const u8 *p, u32 add) /* add + the ModRM operand's length at p, or 0 when it is not one modeled */
{
    u32 len = modrm_len(p);
    return len ? add + len : 0;
}

static u32 store_len(u8 prefix, const u8 *p, int *pop)
{
    *pop = 0;
    if ((p[0] == 0xd9 || p[0] == 0xdd) && (((p[1] >> 3) & 7u) == 2 || ((p[1] >> 3) & 7u) == 3)) { /* fst/fstp m32/m64 */
        *pop = ((p[1] >> 3) & 7u) == 3;
        return mlen(p + 1, 1);
    }
    if (p[0] == 0x89 || p[0] == 0x88) /* mov m, r32/r16 ; mov m, r8 */
        return mlen(p + 1, 1);
    if ((p[0] == 0xc7 || p[0] == 0xc6) && ((p[1] >> 3) & 7u) == 0) /* mov m, imm32/imm16 ; mov m, imm8 */
        return mlen(p + 1, 1u + (p[0] == 0xc6 ? 1u : prefix == 0x66 ? 2u : 4u));
    if (p[0] == 0x0f && (p[1] == 0x11 || p[1] == 0x29 || p[1] == 0x13 ||   /* movss/sd/ups/upd, movaps/apd, movlps */
                         (p[1] == 0x7f && (prefix == 0x66 || prefix == 0xf3)) || /* movdqa/movdqu */
                         (p[1] == 0x7e && prefix == 0x66) || (p[1] == 0xd6 && prefix == 0x66))) /* movd, movq */
        return mlen(p + 2, 2);
    return 0;
}

/* EFLAGS' arithmetic bits (CF PF AF ZF SF OF) after a - b, or a & b for a test, at the operand width. */
static void set_flags(CONTEXT *c, u32 a, u32 b, u32 bits, int test)
{
    u32 mask = bits == 32 ? 0xffffffffu : (1u << bits) - 1u, sign = 1u << (bits - 1u), f = 0, r, x, odd = 0;
    a &= mask;
    b &= mask;
    r = (test ? a & b : a - b) & mask;
    if (!test) {
        if (a < b) f |= 0x1u;
        if ((a ^ b) & (a ^ r) & sign) f |= 0x800u;
        if ((a ^ b ^ r) & 0x10u) f |= 0x10u;
    }
    if (!r) f |= 0x40u;
    if (r & sign) f |= 0x80u;
    for (x = r & 0xffu; x; x >>= 1) odd ^= x & 1u;
    if (!odd) f |= 0x4u;
    c->EFlags = (c->EFlags & ~0x8d5u) | f;
}

/* A register operand's value (8-bit registers: al cl dl bl ah ch dh bh). */
static u32 reg_val(CONTEXT *c, u32 r, u32 bits)
{
    if (bits == 8) return r < 4 ? *gpr(c, r) & 0xffu : *gpr(c, r - 4u) >> 8 & 0xffu;
    return *gpr(c, r);
}

/* cmp and test with a memory operand that reads as 0: sets the flags as the instruction would, and returns its
 * length from p (the opcode, after the prefixes); 0 if p is not one of these forms. Neither writes a register. */
static u32 cmp_form(u8 prefix, const u8 *p, CONTEXT *c)
{
    u8 op = p[0];
    u32 bits = prefix == 0x66 ? 16u : 32u, ml, imm = 0, ilen = 0, a = 0, b = 0;
    int test = 0;
    if (op == 0x38 || op == 0x39 || op == 0x3a || op == 0x3b || op == 0x84 || op == 0x85) {
        if (!(op & 1u)) bits = 8;
        ml = modrm_len(p + 1);
        if (!ml) return 0;
        test = op >= 0x84;
        if (op == 0x3a || op == 0x3b) a = reg_val(c, (p[1] >> 3) & 7u, bits);
        else b = reg_val(c, (p[1] >> 3) & 7u, bits);
    } else if ((op == 0x80 || op == 0x81 || op == 0x83) && ((p[1] >> 3) & 7u) == 7u) { /* cmp m, imm */
        if (op == 0x80) bits = 8;
        ml = modrm_len(p + 1);
        if (!ml) return 0;
        ilen = op == 0x81 ? bits / 8u : 1u;
        memcpy(&imm, p + 1 + ml, ilen);
        if (op != 0x81 && (imm & 0x80u)) imm |= 0xffffff00u; /* 0x83: sign-extended imm8 */
        b = imm;
    } else if ((op == 0xf6 || op == 0xf7) && ((p[1] >> 3) & 7u) == 0) { /* test m, imm */
        if (op == 0xf6) bits = 8;
        ml = modrm_len(p + 1);
        if (!ml) return 0;
        ilen = bits / 8u;
        memcpy(&imm, p + 1 + ml, ilen);
        b = imm;
        test = 1;
    } else {
        return 0;
    }
    set_flags(c, a, b, bits, test);
    return 1u + ml + ilen;
}

int rom_read(EXCEPTION_POINTERS *ep)
{
    const EXCEPTION_RECORD *er = ep->ExceptionRecord;
    CONTEXT *c = ep->ContextRecord;
    const u8 *p = (const u8 *)(uintptr_t)c->Eip;
    u8 prefix = 0;
    u32 addr, reg, len;
    if (er->ExceptionCode != EXCEPTION_ACCESS_VIOLATION || er->NumberParameters < 2 || er->ExceptionInformation[0] > 1)
        return 0;
    addr = (u32)er->ExceptionInformation[1];
    if (!in_rom(addr)) return 0;
    while (*p == 0x66 || *p == 0xf2 || *p == 0xf3) prefix = *p++;
    if (er->ExceptionInformation[0] == 1) { /* a store */
        int pop;
        len = store_len(prefix, p, &pop);
        if (!len) {
            port_log("boot ROM write of %08x by an instruction rom_read.c does not model (%02x %02x %02x)\n", addr,
                     prefix, p[0], p[1]);
            return 0;
        }
        if (pop) x87_pop(c);
        log_site(c, addr, 1);
        c->Eip = (DWORD)(uintptr_t)(p + len);
        return 1;
    }
    if (*p == 0x8b || *p == 0x8a) { /* mov r32/r16, m ; mov r8, m */
        u8 op = *p++;
        reg = (p[0] >> 3) & 7u;
        len = modrm_len(p);
        if (!len) return 0;
        if (op == 0x8a) {
            DWORD *r = gpr(c, reg & 3u);
            *r &= reg < 4 ? ~0xffu : ~0xff00u;
        } else {
            *gpr(c, reg) = prefix == 0x66 ? *gpr(c, reg) & ~0xffffu : 0;
        }
    } else if (p[0] == 0x0f && (p[1] == 0xb6 || p[1] == 0xb7 || p[1] == 0xbe || p[1] == 0xbf)) { /* movzx/movsx */
        p += 2;
        reg = (p[0] >> 3) & 7u;
        len = modrm_len(p);
        if (!len) return 0;
        *gpr(c, reg) = 0;
    } else if (p[0] == 0x0f) {
        int keep_high;
        u32 bytes = xmm_load(prefix, p[1], &keep_high);
        u8 op = p[1], *xmm;
        p += 2;
        reg = (p[0] >> 3) & 7u;
        len = modrm_len(p);
        xmm = c->ExtendedRegisters + XMM_OFFSET + 16u * reg;
        if (!len || !(c->ContextFlags & CONTEXT_EXTENDED_REGISTERS & ~CONTEXT_i386) ||
            (!bytes && !xmm_scalar(prefix, op, xmm))) {
            port_log("boot ROM read of %08x by an instruction rom_read.c does not model (%02x %02x %02x)\n", addr,
                     prefix, p[-2], p[-1]);
            return 0;
        }
        if (bytes) memset(xmm, 0, keep_high ? bytes : 16u);
    } else if ((len = cmp_form(prefix, p, c)) != 0) { /* cmp / test against the ROM: flags from a 0 operand */
        /* len counts from the opcode */
    } else {
        port_log("boot ROM read of %08x by an instruction rom_read.c does not model (%02x %02x)\n", addr, p[0], p[1]);
        return 0;
    }
    log_site(c, addr, 0);
    c->Eip = (DWORD)(uintptr_t)(p + len);
    return 1;
}
