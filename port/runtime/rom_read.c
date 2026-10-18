/* Reads of the boot ROM area. The Dreamcast's boot ROM (BIOS) is at physical 0x00000000..0x001fffff, seen at
 * 0x00000000 (U0/P0), 0x80000000 (P1) and 0xa0000000 (P2). Games read it by accident: a NULL table pointer plus
 * an offset reads ROM bytes on the console, where nothing faults. The port has no BIOS and the host cannot map its
 * first 64 KB, so such a read faults; the exception handler (dispatch.c) asks rom_read() to finish the instruction
 * as if the ROM held zeros.
 *
 * Only the load forms MSVC emits for 32-bit readable C are modelled: mov/movzx/movsx into a general register, and
 * movss/movsd/movd/movq/movups/movaps/movdqu/movdqa/movlps into an XMM register, and the scalar arithmetic that
 * takes its second operand from memory (addss/subss/mulss/divss and their sd forms, with that operand 0). Any other
 * instruction is reported and ends the run, so a new form shows up in the log as the thing to add. */
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
static void log_site(const CONTEXT *c, u32 addr)
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
    port_log("boot ROM read of %08x at port.exe+%08x: reads as zero (the port has no BIOS)\n", addr, eip - lo);
    for (int i = 0, shown = 0; i < 64 && shown < 6; i++)
        if (sp[i] > lo && sp[i] < hi) {
            port_log("  stack+%03x: port.exe+%08x\n", i * 4, sp[i] - lo);
            shown++;
        }
}

int rom_read(EXCEPTION_POINTERS *ep)
{
    const EXCEPTION_RECORD *er = ep->ExceptionRecord;
    CONTEXT *c = ep->ContextRecord;
    const u8 *p = (const u8 *)(uintptr_t)c->Eip;
    u8 prefix = 0;
    u32 addr, reg, len;
    if (er->ExceptionCode != EXCEPTION_ACCESS_VIOLATION || er->NumberParameters < 2 || er->ExceptionInformation[0])
        return 0;
    addr = (u32)er->ExceptionInformation[1];
    if (!in_rom(addr)) return 0;
    while (*p == 0x66 || *p == 0xf2 || *p == 0xf3) prefix = *p++;
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
    } else {
        port_log("boot ROM read of %08x by an instruction rom_read.c does not model (%02x %02x)\n", addr, p[0], p[1]);
        return 0;
    }
    log_site(c, addr);
    c->Eip = (DWORD)(uintptr_t)(p + len);
    return 1;
}
