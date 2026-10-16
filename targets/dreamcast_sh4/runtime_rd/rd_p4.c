/* Readable-C runtime: stores to the P4 operand-cache address array (0xF4000000..0xF4FFFFFF), as runtime/tc_p4.c
 * logs them for raw C (that file is linked here too: the log, its limits and its W lines are the same code).
 *
 * Raw C stores through wr8/wr16/wr32 (rd_mem.c checks the range). Readable C stores through a pointer: the array is
 * kept reserved and inaccessible, so the store faults; the handler (rd_veh.c) asks rd_p4_fault, which decodes the
 * size of the storing instruction, opens the page and single-steps it, and rd_p4_step then logs the bytes stored and
 * closes the page again. A load faults and is not let through (the original's test run faults on it too:
 * TcFold.p4Read), and so does a store by an instruction the decoder does not know: the vector then differs, it can
 * never match wrongly.
 */
#include <string.h>
#include <windows.h>
#include "rd_internal.h"

static int g_mapped;
static u32 g_addr, g_size; /* the store being single-stepped (g_size 0: none) */

void rd_p4_init(void)
{
    void *want = (void *)(uintptr_t)TCRT_P4_OCA_LO;
    g_mapped = VirtualAlloc(want, TCRT_P4_OCA_HI - TCRT_P4_OCA_LO, MEM_RESERVE | MEM_COMMIT, PAGE_NOACCESS) == want;
}

/* Bytes of a ModRM memory operand after the opcode byte (the ModRM byte, a SIB byte, the displacement). */
static u32 modrm_len(const u8 *m)
{
    u32 mod = m[0] >> 6, rm = m[0] & 7u, n = 1;
    if (rm == 4) n++; /* SIB */
    if (mod == 1) n += 1;
    else if (mod == 2 || (mod == 0 && rm == 5) || (mod == 0 && rm == 4 && (m[1] & 7u) == 5)) n += 4;
    return n;
}

/* An immediate AND with 0 or OR with all ones (0x80/0x81/0x83 /4 or /1): its result does not depend on the old
 * contents, so it is a store. MSVC /O1 stores a zero as `and DWORD PTR [eax+1448], 0` (TC 8c058dca: before batch 22
 * the decoder did not know it and the run crashed). */
static u32 const_rmw(const u8 *p, int opsize)
{
    u32 reg = (p[1] >> 3) & 7u, width = p[0] == 0x80 ? 1u : (opsize ? 2u : 4u);
    if ((p[1] >> 6) == 3 || (reg != 4 && reg != 1)) return 0;
    const u8 *imm = p + 1 + modrm_len(p + 1);
    u32 v = 0, n = p[0] == 0x81 ? width : 1u;
    memcpy(&v, imm, n);
    u32 all = n == 1 ? 0xffu : (n == 2 ? 0xffffu : 0xffffffffu);
    return (reg == 4 && v == 0) || (reg == 1 && v == all) ? width : 0;
}

/* Bytes stored by the instruction at p, or 0 when it is not a plain store the decoder knows. */
static u32 store_size(const u8 *p)
{
    int opsize = 0, rep = 0;
    for (;; p++) {
        if (*p == 0x66) opsize = 1;
        else if (*p == 0xf3 || *p == 0xf2) rep = *p;
        else if (*p == 0x26 || *p == 0x2e || *p == 0x36 || *p == 0x3e || *p == 0x64 || *p == 0x65) continue;
        else break;
    }
    u8 reg = (u8)((p[1] >> 3) & 7);
    switch (p[0]) {
    case 0x88: return 1;                         /* mov r/m8, r8 */
    case 0xc6: return reg == 0 ? 1 : 0;          /* mov r/m8, imm8 */
    case 0x89: return opsize ? 2 : 4;            /* mov r/m, r */
    case 0xc7: return reg == 0 ? (opsize ? 2u : 4u) : 0; /* mov r/m, imm */
    case 0x80: case 0x81: case 0x83: return const_rmw(p, opsize); /* and 0 / or -1 */
    case 0xd9: return (reg == 2 || reg == 3) ? 4 : 0;    /* fst / fstp m32 */
    case 0xaa: return rep ? 0 : 1;               /* stosb (one at a time only) */
    case 0xab: return rep ? 0 : (opsize ? 2u : 4u);
    case 0x0f:
        if (p[1] == 0x11) return rep == 0xf3 ? 4 : 0; /* movss m32, xmm */
        if (p[1] == 0x7e && opsize) return 4;         /* movd m32, xmm */
        return 0;
    default: return 0;
    }
}

static void protect(u32 addr, DWORD prot)
{
    DWORD old;
    VirtualProtect((void *)(uintptr_t)(addr & ~(RD_PAGE - 1)), RD_PAGE, prot, &old);
}

int rd_p4_fault(u32 addr, int is_write, void *context)
{
    CONTEXT *c = (CONTEXT *)context;
    if (!g_mapped || !is_write || g_size || addr < TCRT_P4_OCA_LO || addr >= TCRT_P4_OCA_HI) return 0;
    u32 size = store_size((const u8 *)(uintptr_t)c->Eip);
    if (!size || addr > TCRT_P4_OCA_HI - size || ((addr ^ (addr + size - 1)) & ~(RD_PAGE - 1))) return 0;
    g_addr = addr;
    g_size = size;
    protect(addr, PAGE_READWRITE);
    c->EFlags |= 0x100; /* trap flag: rd_p4_step after this one instruction */
    return 1;
}

int rd_p4_step(void)
{
    if (!g_size) return 0;
    u32 v = 0;
    memcpy(&v, (const void *)(uintptr_t)g_addr, g_size);
    protect(g_addr, PAGE_NOACCESS);
    tcrt_p4_store(g_addr, v, g_size);
    g_size = 0;
    return 1;
}

void rd_p4_reset(void)
{
    g_size = 0;
    tcrt_p4_reset();
}
