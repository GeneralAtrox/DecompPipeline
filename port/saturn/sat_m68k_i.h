/* The 68000 core's internals shared by its instruction groups (sat_m68k*.c). */
#ifndef SAT_M68K_I_H
#define SAT_M68K_I_H
#include "sat_m68k.h"

enum { FC = 1, FV = 2, FZ = 4, FN = 8, FX = 16, SR_S = 0x2000 };

/* an operand: 0 Dn (v = register), 1 An (v = register), 2 memory (v = address), 3 immediate (v = value) */
typedef struct {
    int kind;
    u32 v;
} Ea;

extern const u32 m_mask[3], m_msb[3];

u32 m_fetch(void);
u32 m_fetch32(void);
u32 m_rd(u32 a, int sz);
void m_wr(u32 a, int sz, u32 v);
void m_push32(u32 v);
u32 m_pop32(void);
void ea_decode(Ea *e, int mode, int reg, int sz);
u32 ea_get(const Ea *e, int sz);
void ea_put(const Ea *e, int sz, u32 v);
u32 ea_addr(int mode, int reg); /* a control addressing mode's address (LEA, PEA, JMP, JSR, MOVEM) */
void set_sr(u32 v);
void exception(int vec);
int cond(int c);
u32 m_sext(u32 v, int sz);
u32 getd(int r, int sz);
void setd(int r, int sz, u32 v);
int sz_of(u32 op); /* bits 7-6 as 0 byte, 1 word, 2 long (3 is not a size) */

/* ALU: operands and results are masked to the size; flags are set as the instructions do */
u32 alu_add(u32 s, u32 d, int sz, u32 cin, int ext);
u32 alu_sub(u32 s, u32 d, int sz, u32 cin, int ext);
void alu_cmp(u32 s, u32 d, int sz);
u32 alu_logic(u32 r, int sz);
void set_nz(u32 r, int sz);
u32 bcd_op(int sub, u32 s, u32 d); /* ABCD (sub 0) or SBCD (sub 1): d op s with the X flag */
u32 shift(int type, int left, u32 v, u32 cnt, int sz);

void op_g0(u32 op);
void op_move(u32 op);
void op_g4(u32 op);
void op_g5(u32 op);
void op_g6(u32 op);
void op_g8(u32 op);
void op_g9d(u32 op);
void op_gb(u32 op);
void op_gc(u32 op);
void op_ge(u32 op);

#endif
