/* Platform layer: the library's execution contexts (Lib_InitContext, Lib_SwapContext), a coroutine switch.
 *
 * A context is a register file: r0 at +0x00, r1..r15 at +0x08..+0x40, pr at +0x44, SR at +0x48 (the game keeps them
 * 0x50 bytes apart). Lib_InitContext(ctx, entry, stackTop) makes a fresh one: the 0x50 bytes cleared, the library's
 * tag "$CTX" in r0, stackTop in r15, entry in pr and SR 0x600000f0 (privileged, register bank 1, interrupts masked).
 * Lib_SwapContext(load, save) saves the running code into save and resumes load: a fresh context starts at its entry,
 * a saved one returns from the Lib_SwapContext call that saved it. A fresh context's pr is its entry, so an entry that
 * returns starts over.
 *
 * The port runs each context on a host coroutine (platform.h host_coro_*). A switch happens only at a
 * Lib_SwapContext call, so the order of everything the contexts run is the program's, the same every run. SR is the
 * one register the port models (sh4_stc_sr): it is saved to save's +0x48 and loaded from load's, as the library does,
 * so code in a context sees its own interrupt mask. The coroutine keeps the rest of the state, and the other register
 * words stay as Lib_InitContext left them. A context whose entry is guest code the port has no function for cannot
 * run: switching to it returns at once, as a context that switched straight back would, and the first such switch
 * goes to the log.
 *
 * Needs from the game's headers: LibCpuContext (r0, unknown_04, gpr[15] = r1..r15, pr, sr), SdkProc and the two
 * function names. */
#include "game.h"
#include "platform.h"

#define CTX_BYTES 0x50u     /* what Lib_InitContext clears */
#define CTX_TAG 0x58544324u /* "$CTX" */
#define CTX_SR 0x600000f0u
#define CTX_R15 14          /* gpr[] index */
#define CONTEXTS 8u

static struct {
    LibCpuContext *ctx;
    HostCoro *coro; /* the coroutine holding the context's state; NULL while the context is fresh */
    PortCodeFn fn;  /* a fresh context's entry, as a host function */
    int fresh, logged;
} g_ctx[CONTEXTS];

/* The context's slot; a new one when make (CONTEXTS if the table is full or ctx is unknown). */
static u32 slot_of(LibCpuContext *ctx, int make)
{
    u32 free_slot = CONTEXTS;
    for (u32 i = 0; i < CONTEXTS; i++) {
        if (g_ctx[i].ctx == ctx) return i;
        if (!g_ctx[i].ctx && free_slot == CONTEXTS) free_slot = i;
    }
    if (!make) return CONTEXTS;
    if (free_slot == CONTEXTS) port_exit("Lib_SwapContext: more contexts than the port keeps", 1);
    g_ctx[free_slot].ctx = ctx;
    return free_slot;
}

static void ctx_main(void *arg)
{
    PortCodeFn entry = g_ctx[(u32)(uintptr_t)arg].fn;
    for (;;) entry();
}

void Lib_InitContext(LibCpuContext *ctx, SdkProc entry, void *stackTop)
{
    u32 s = slot_of(ctx, 1);
    memset(ctx, 0, CTX_BYTES);
    ctx->r0 = CTX_TAG;
    ctx->gpr[CTX_R15] = (u32)(uintptr_t)stackTop;
    ctx->pr = (u32)(uintptr_t)entry;
    ctx->sr = CTX_SR;
    if (g_ctx[s].coro == host_coro_self())
        port_log("Lib_InitContext: context %08x made fresh while it runs\n", (u32)(uintptr_t)ctx);
    else
        host_coro_delete(g_ctx[s].coro);
    g_ctx[s].coro = NULL;
    g_ctx[s].fn = port_code_function(ctx->pr);
    g_ctx[s].fresh = 1;
}

void Lib_SwapContext(LibCpuContext *load, LibCpuContext *save)
{
    HostCoro *self = host_coro_self();
    u32 l = slot_of(load, 0), s;
    if (!self) port_exit("Lib_SwapContext: the host cannot make coroutines", 1);
    if (l == CONTEXTS || (!g_ctx[l].fresh && !g_ctx[l].coro)) {
        port_log("Lib_SwapContext: context %08x was never made or saved\n", (u32)(uintptr_t)load);
        port_exit("Lib_SwapContext: a context with nothing to resume", 1);
    }
    save->sr = sh4_stc_sr();
    if (g_ctx[l].fresh && !g_ctx[l].fn) {
        if (!g_ctx[l].logged)
            port_log("Lib_SwapContext: context %08x starts at %08x, guest code the port has no function for; "
                     "switching to it returns at once\n", (u32)(uintptr_t)load, load->pr);
        g_ctx[l].logged = 1;
        return;
    }
    s = slot_of(save, 1);
    for (u32 i = 0; i < CONTEXTS; i++) /* the running state moves to save: whatever held it holds nothing now */
        if (i != s && g_ctx[i].coro == self && !g_ctx[i].fresh) g_ctx[i].coro = NULL;
    g_ctx[s].coro = self;
    g_ctx[s].fresh = 0;
    if (g_ctx[l].fresh) {
        g_ctx[l].coro = host_coro_create(ctx_main, (void *)(uintptr_t)l);
        if (!g_ctx[l].coro) port_exit("Lib_SwapContext: the host could not make a coroutine", 1);
        g_ctx[l].fresh = 0;
    }
    sh4_ldc_sr(load->sr);
    host_coro_switch(g_ctx[l].coro);
}
