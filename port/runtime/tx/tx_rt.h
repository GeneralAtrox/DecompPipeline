/* Translator C in the port (tools/tx_port.py): what its generated glue uses of tx_rt.c. Plain C types only: the
 * glue includes this beside the judged runtime's tc_rt.h (a function's own file) or the port's game.h (tc_call's
 * glue), which each define u32 and the rest. */
#ifndef TX_RT_H
#define TX_RT_H

#include <stdint.h>

/* tc_rt.h's TcArgs, the same layout: r[0..3] = r4..r7, fr[0..7] = fr4..fr11, s[0..7] = the stack words at the
 * call's r15, rlo[0..3] = r0..r3 */
typedef struct {
    uint32_t r[4];
    float fr[8];
    uint32_t s[8];
    uint32_t rlo[4];
} PortTcArgs;

typedef struct {
    uint32_t r0;
    float fr0;
} PortTcRet;

typedef void (*PortTcGlue)(const PortTcArgs *a, PortTcRet *o);

typedef struct {
    uint32_t addr;
    PortTcGlue fn;
} PortTcRow;

typedef struct {
    const PortTcRow *rows;
    const int *n;
} PortTcChunk;

/* A translated function's frame on the guest stack: args bytes above the returned sp for its stack arguments, frame
 * bytes below it for what its body pushes. port_tx_leave(sp, args) gives them back. Called
 * from tc_call's glue, the frame's first 8 words are the call's stack words (TcArgs.s), named or not. */
uint32_t port_tx_enter(uint32_t frame, uint32_t args);
void port_tx_leave(uint32_t sp, uint32_t args);
void port_tx_wr32(uint32_t addr, uint32_t value);
float port_tx_u2f(uint32_t bits);
uint32_t port_tx_f2u(float value);
/* What the last tc_call returned (r0 and fr0): a judged body the translator wrote as returning nothing, whose every
 * exit is a call's tail, returns it (tools/tx_port.py tail_return). */
uint32_t port_tx_last_r0(void);
float port_tx_last_fr0(void);
/* A float callee's result for the judged C: tc_call returns its bits as r0 (the C reads `fr0 = u2f(r0)`), and fr0 of
 * the FPU state (sh4_fr) holds it, as tc_rt.h's tc_call leaves it. The glue calls this. */
void port_tx_set_fr0(float value);
/* Marks the running translated body as one that keeps the whole FPU as state (1) or not (0); returns the mark it
 * replaces, which the glue restores when the body returns. */
int port_tx_fpu_whole(int whole);
/* r0..r3 as translator C had them at its last tc_call (the registry prototype carries no such registers), for the
 * translated callee's body; reading them clears them, so a call from readable C finds 0. */
void port_tx_take_rlo(uint32_t *lo);
/* The caller's stack argument at stack+off (32 and up: past the 8 words a TcArgs block carries). */
uint32_t port_tx_stack_word(const PortTcArgs *a, uint32_t off);

#endif
