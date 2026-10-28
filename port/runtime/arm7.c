/* The ARM7DI core (arm7.h): the register banks and modes, the condition codes, the data-processing instructions with
 * the barrel shifter, the PSR transfers, the multiplies, the branches and the exceptions. The memory transfers are in
 * arm7_ls.c. Written from the ARM Architecture Reference Manual (ARMv4, ARM state). */
#include "arm7.h"

#define BIT(op, n) (((op) >> (n)) & 1u)
#define RN(op) (((op) >> 16) & 15u)
#define RD(op) (((op) >> 12) & 15u)
#define RS(op) (((op) >> 8) & 15u)
#define RM(op) ((op) & 15u)

/* The bank a mode's r13, r14 and SPSR live in: 0 user and system, 1 FIQ, 2 IRQ, 3 SVC, 4 abort, 5 undefined. */
static u32 bank_of(u32 mode)
{
    switch (mode & 0x1fu) {
    case ARM_MODE_FIQ: return 1;
    case ARM_MODE_IRQ: return 2;
    case ARM_MODE_SVC: return 3;
    case ARM_MODE_ABT: return 4;
    case ARM_MODE_UND: return 5;
    default: return 0;
    }
}

u32 arm7_cpsr(const Arm7 *a) { return a->n << 31 | a->z << 30 | a->c << 29 | a->v << 28 | (a->psr & 0xffu); }

void arm7_set_cpsr(Arm7 *a, u32 value)
{
    u32 from = bank_of(a->psr), to = bank_of(value);
    a->n = value >> 31;
    a->z = BIT(value, 30);
    a->c = BIT(value, 29);
    a->v = BIT(value, 28);
    if (from != to) {
        a->bank[from][0] = a->r[13];
        a->bank[from][1] = a->r[14];
        a->bank[from][2] = a->spsr;
        if (from == 1 || to == 1) { /* FIQ has its own r8..r12 */
            u32 *out = from == 1 ? a->fiq_hi : a->usr_hi, *in = to == 1 ? a->fiq_hi : a->usr_hi;
            for (int i = 0; i < 5; i++) {
                out[i] = a->r[8 + i];
                a->r[8 + i] = in[i];
            }
        }
        a->r[13] = a->bank[to][0];
        a->r[14] = a->bank[to][1];
        a->spsr = a->bank[to][2];
    }
    a->psr = (value & 0xffu) | 0x10u; /* the mode's bit 4 is always set (no 26-bit modes) */
}

/* A user-mode register, whatever the current mode (LDM/STM with the S bit and no PC). */
u32 arm7_user_reg(Arm7 *a, u32 n)
{
    u32 b = bank_of(a->psr);
    if (n >= 13 && n <= 14 && b) return a->bank[0][n - 13];
    if (n >= 8 && n <= 12 && b == 1) return a->usr_hi[n - 8];
    return a->r[n];
}

void arm7_set_user_reg(Arm7 *a, u32 n, u32 value)
{
    u32 b = bank_of(a->psr);
    if (n >= 13 && n <= 14 && b) a->bank[0][n - 13] = value;
    else if (n >= 8 && n <= 12 && b == 1) a->usr_hi[n - 8] = value;
    else if (n == 15) a->next = value & ~3u;
    else a->r[n] = value;
}

void arm7_reset(Arm7 *a)
{
    for (int i = 0; i < 16; i++) a->r[i] = 0;
    for (int i = 0; i < 5; i++) a->usr_hi[i] = a->fiq_hi[i] = 0;
    for (int i = 0; i < 6; i++) a->bank[i][0] = a->bank[i][1] = a->bank[i][2] = 0;
    a->n = a->z = a->c = a->v = 0;
    a->psr = ARM_MODE_SVC | ARM_PSR_I | ARM_PSR_F;
    a->spsr = 0;
    a->next = 0;
    a->cycles = 0;
    a->executed = 0;
    a->undefined = 0;
}

u32 arm7_read(Arm7 *a, u32 addr, u32 size)
{
    if (addr < a->ram_size) {
        const u8 *p = a->ram + (addr & a->ram_mask);
        return size == 4 ? (u32)p[0] | (u32)p[1] << 8 | (u32)p[2] << 16 | (u32)p[3] << 24
             : size == 2 ? (u32)p[0] | (u32)p[1] << 8 : p[0];
    }
    return a->rd(a, addr, size);
}

void arm7_write(Arm7 *a, u32 addr, u32 value, u32 size)
{
    if (addr < a->ram_size) {
        u8 *p = a->ram + (addr & a->ram_mask);
        p[0] = (u8)value;
        if (size >= 2) p[1] = (u8)(value >> 8);
        if (size == 4) {
            p[2] = (u8)(value >> 16);
            p[3] = (u8)(value >> 24);
        }
        return;
    }
    a->wr(a, addr, value, size);
}

/* An exception: the mode's r14 is ret, its SPSR the CPSR, IRQ masked (and FIQ for reset and FIQ), ARM state. */
void arm7_exception(Arm7 *a, u32 mode, u32 vector, u32 ret)
{
    u32 cpsr = arm7_cpsr(a);
    arm7_set_cpsr(a, (cpsr & ~0x1fu) | mode | ARM_PSR_I | (mode == ARM_MODE_FIQ ? ARM_PSR_F : 0u));
    a->spsr = cpsr;
    a->r[14] = ret;
    a->next = vector;
}

static int condition(const Arm7 *a, u32 cond)
{
    switch (cond) {
    case 0x0: return a->z;
    case 0x1: return !a->z;
    case 0x2: return a->c;
    case 0x3: return !a->c;
    case 0x4: return a->n;
    case 0x5: return !a->n;
    case 0x6: return a->v;
    case 0x7: return !a->v;
    case 0x8: return a->c && !a->z;
    case 0x9: return !a->c || a->z;
    case 0xa: return a->n == a->v;
    case 0xb: return a->n != a->v;
    case 0xc: return !a->z && a->n == a->v;
    case 0xd: return a->z || a->n != a->v;
    case 0xe: return 1;
    default: return 0; /* NV: never, on ARMv4 */
    }
}

/* The shifter: value shifted by type (0 LSL, 1 LSR, 2 ASR, 3 ROR) by n, n a register's low byte (by_reg) or an
 * immediate 0..31, whose 0 means LSR #32, ASR #32 and RRX. *carry: the shifter's carry out (in: the C flag). */
static u32 shift(u32 value, u32 type, u32 n, int by_reg, u32 *carry)
{
    if (by_reg && n == 0) return value;
    switch (type) {
    case 0:
        if (n == 0) return value;
        if (n < 32) {
            *carry = BIT(value, 32 - n);
            return value << n;
        }
        *carry = n == 32 ? value & 1u : 0u;
        return 0;
    case 1:
        if (n == 0) n = 32;
        if (n < 32) {
            *carry = BIT(value, n - 1);
            return value >> n;
        }
        *carry = n == 32 ? value >> 31 : 0u;
        return 0;
    case 2:
        if (n == 0 || n >= 32) {
            *carry = value >> 31;
            return value >> 31 ? 0xffffffffu : 0u;
        }
        *carry = BIT(value, n - 1);
        return (u32)((s32)value >> n);
    default:
        if (n == 0) { /* RRX */
            u32 out = *carry << 31 | value >> 1;
            *carry = value & 1u;
            return out;
        }
        n &= 31u;
        if (n == 0) {
            *carry = value >> 31;
            return value;
        }
        *carry = BIT(value, n - 1);
        return value >> n | value << (32 - n);
    }
}

u32 arm7_shift_imm(Arm7 *a, u32 op, u32 *carry)
{
    return shift(a->r[RM(op)], (op >> 5) & 3u, (op >> 7) & 31u, 0, carry);
}

static void set_nz(Arm7 *a, u32 result)
{
    a->n = result >> 31;
    a->z = result == 0;
}

/* a + b + carry_in, setting C and V when s. */
static u32 add(Arm7 *a, u32 x, u32 y, u32 carry_in, int s)
{
    u64 wide = (u64)x + y + carry_in;
    u32 r = (u32)wide;
    if (s) {
        a->c = (u32)(wide >> 32);
        a->v = ((~(x ^ y) & (x ^ r)) >> 31) & 1u;
    }
    return r;
}

static void data_processing(Arm7 *a, u32 op)
{
    u32 carry = a->c, operand, rn_value, result = 0;
    u32 opcode = (op >> 21) & 15u, s = BIT(op, 20), rd = RD(op);
    if (BIT(op, 25)) {
        u32 rot = ((op >> 8) & 15u) * 2u, imm = op & 0xffu;
        operand = rot ? imm >> rot | imm << (32 - rot) : imm;
        if (rot) carry = operand >> 31;
        rn_value = a->r[RN(op)];
    } else if (BIT(op, 4)) { /* shift by a register: the PC reads one word further on */
        a->r[15] += 4;
        operand = shift(a->r[RM(op)], (op >> 5) & 3u, a->r[RS(op)] & 0xffu, 1, &carry);
        rn_value = a->r[RN(op)];
        a->r[15] -= 4;
        a->cycles--;
    } else {
        operand = arm7_shift_imm(a, op, &carry);
        rn_value = a->r[RN(op)];
    }
    int logical = 0, write = 1;
    switch (opcode) {
    case 0x0: result = rn_value & operand; logical = 1; break;
    case 0x1: result = rn_value ^ operand; logical = 1; break;
    case 0x2: result = add(a, rn_value, ~operand, 1, s); break;
    case 0x3: result = add(a, operand, ~rn_value, 1, s); break;
    case 0x4: result = add(a, rn_value, operand, 0, s); break;
    case 0x5: result = add(a, rn_value, operand, a->c, s); break;
    case 0x6: result = add(a, rn_value, ~operand, a->c, s); break;
    case 0x7: result = add(a, operand, ~rn_value, a->c, s); break;
    case 0x8: result = rn_value & operand; logical = 1; write = 0; break;
    case 0x9: result = rn_value ^ operand; logical = 1; write = 0; break;
    case 0xa: result = add(a, rn_value, ~operand, 1, 1); write = 0; break;
    case 0xb: result = add(a, rn_value, operand, 0, 1); write = 0; break;
    case 0xc: result = rn_value | operand; logical = 1; break;
    case 0xd: result = operand; logical = 1; break;
    case 0xe: result = rn_value & ~operand; logical = 1; break;
    default: result = ~operand; logical = 1; break;
    }
    if (s && rd == 15) { /* the mode's SPSR back into the CPSR (an exception return) */
        if (bank_of(a->psr)) arm7_set_cpsr(a, a->spsr);
    } else if (s) {
        set_nz(a, result);
        if (logical) a->c = carry;
    }
    if (write) {
        if (rd == 15) {
            a->next = result & ~3u;
            a->cycles -= 2;
        } else {
            a->r[rd] = result;
        }
    }
}

static void psr_transfer(Arm7 *a, u32 op)
{
    int spsr = BIT(op, 22);
    if (!BIT(op, 21)) { /* MRS */
        if (RD(op) != 15) a->r[RD(op)] = spsr ? a->spsr : arm7_cpsr(a);
        return;
    }
    u32 value, mask = 0;
    if (BIT(op, 25)) {
        u32 rot = ((op >> 8) & 15u) * 2u, imm = op & 0xffu;
        value = rot ? imm >> rot | imm << (32 - rot) : imm;
    } else {
        value = a->r[RM(op)];
    }
    if (BIT(op, 16)) mask |= 0x000000ffu;
    if (BIT(op, 17)) mask |= 0x0000ff00u;
    if (BIT(op, 18)) mask |= 0x00ff0000u;
    if (BIT(op, 19)) mask |= 0xff000000u;
    if (spsr) {
        if (bank_of(a->psr)) a->spsr = (a->spsr & ~mask) | (value & mask);
        return;
    }
    if ((a->psr & 0x1fu) == ARM_MODE_USR) mask &= 0xff000000u; /* user mode may set the flags only */
    arm7_set_cpsr(a, (arm7_cpsr(a) & ~mask) | (value & mask));
}

static void multiply(Arm7 *a, u32 op)
{
    u32 m = a->r[RM(op)], s = a->r[RS(op)];
    a->cycles -= 2;
    if (BIT(op, 23)) { /* UMULL, UMLAL, SMULL, SMLAL: RdHi in 19..16, RdLo in 15..12 */
        u64 r = BIT(op, 22) ? (u64)((s64)(s32)m * (s32)s) : (u64)m * s;
        if (BIT(op, 21)) r += (u64)a->r[RN(op)] << 32 | a->r[RD(op)];
        a->r[RD(op)] = (u32)r;
        a->r[RN(op)] = (u32)(r >> 32);
        if (BIT(op, 20)) {
            a->n = (u32)(r >> 63);
            a->z = r == 0;
        }
        a->cycles--;
        return;
    }
    u32 r = m * s + (BIT(op, 21) ? a->r[RD(op)] : 0u); /* MUL, MLA: Rd in 19..16, Rn in 15..12 */
    a->r[RN(op)] = r;
    if (BIT(op, 20)) set_nz(a, r);
}

static void undefined(Arm7 *a, u32 op, u32 pc)
{
    if (!a->undefined++) port_log("arm7: undefined instruction %08x at %08x\n", op, pc);
    arm7_exception(a, ARM_MODE_UND, 0x04u, pc + 4u);
}

static void execute(Arm7 *a, u32 op, u32 pc)
{
    switch ((op >> 25) & 7u) {
    case 0:
        if ((op & 0x0fc000f0u) == 0x00000090u || (op & 0x0f8000f0u) == 0x00800090u) multiply(a, op);
        else if ((op & 0x0fb00ff0u) == 0x01000090u) arm7_swap(a, op);
        else if ((op & 0x0e000090u) == 0x00000090u) arm7_half(a, op);
        else if ((op & 0x0ffffff0u) == 0x012fff10u) { /* BX: ARM state only */
            a->next = a->r[RM(op)] & ~3u;
            a->cycles -= 2;
        } else if ((op & 0x0d900000u) == 0x01000000u) psr_transfer(a, op);
        else data_processing(a, op);
        break;
    case 1:
        if ((op & 0x0d900000u) == 0x01000000u) {
            if (BIT(op, 21)) psr_transfer(a, op); /* MSR immediate */
            else undefined(a, op, pc);
        } else {
            data_processing(a, op);
        }
        break;
    case 2: arm7_single(a, op); break;
    case 3:
        if (BIT(op, 4)) undefined(a, op, pc);
        else arm7_single(a, op);
        break;
    case 4: arm7_block(a, op); break;
    case 5: {
        s32 off = (s32)(op << 8) >> 6;
        if (BIT(op, 24)) a->r[14] = pc + 4u;
        a->next = pc + 8u + (u32)off;
        a->cycles -= 2;
        break;
    }
    case 6: undefined(a, op, pc); break; /* coprocessor transfers: the AICA's ARM has none */
    default:
        if (BIT(op, 24)) arm7_exception(a, ARM_MODE_SVC, 0x08u, pc + 4u); /* SWI */
        else undefined(a, op, pc);
        break;
    }
}

void arm7_run(Arm7 *a, s64 cycles)
{
    a->cycles += cycles;
    while (a->cycles > 0) {
        if (a->fiq && !(a->psr & ARM_PSR_F)) arm7_exception(a, ARM_MODE_FIQ, 0x1cu, a->next + 4u);
        u32 pc = a->next, op;
        if (pc < a->ram_size) {
            const u8 *p = a->ram + (pc & a->ram_mask);
            op = (u32)p[0] | (u32)p[1] << 8 | (u32)p[2] << 16 | (u32)p[3] << 24;
        } else {
            op = a->rd(a, pc, 4);
        }
        a->next = pc + 4u;
        a->r[15] = pc + 8u;
        a->cycles--;
        a->executed++;
        if (condition(a, op >> 28)) execute(a, op, pc);
    }
}
