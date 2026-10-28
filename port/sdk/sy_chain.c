/* Platform layer: Shinobi's interrupt chain (syChainAddHandler, syChainDeleteHandler).
 *
 * A game hangs handlers on interrupt events; Shinobi calls each event's handlers in priority order, the lowest number
 * first. The port has one interrupt that happens: the vertical blank (event 0x900), which runs at every vertical
 * blank the kit waits for. The others (0x920 horizontal blank, 0xa40 GD-ROM DMA end, 0xb00 the external / GD-ROM
 * line) need hardware the port has none of: their handlers are kept and never run, and each one goes to the log.
 *
 * Needs from the game's headers: SYCHAIN (a u32 handle), SyChainFunc (void (*)(void *)) and the two function names. */
#include "game.h"
#include "platform.h"

#define EV_VBLANK 0x900
#define HANDLERS 16u

static struct {
    s32 event;
    SyChainFunc fn;
    u32 priority;
    void *arg;
    u32 seq; /* registration order: equal priorities run first come, first served */
} g_chain[HANDLERS];
static u32 g_seq;
static int g_vblank_task;

/* The vertical blank: every live 0x900 handler, by priority then registration order. */
static void vblank_chain(void *unused)
{
    u32 done = 0;
    (void)unused;
    for (;;) {
        u32 best = HANDLERS;
        for (u32 i = 0; i < HANDLERS; i++) {
            if (!g_chain[i].fn || g_chain[i].event != EV_VBLANK || (done >> i & 1u)) continue;
            if (best == HANDLERS || g_chain[i].priority < g_chain[best].priority ||
                (g_chain[i].priority == g_chain[best].priority && g_chain[i].seq < g_chain[best].seq))
                best = i;
        }
        if (best == HANDLERS) return;
        done |= 1u << best;
        g_chain[best].fn(g_chain[best].arg);
    }
}

/* The handle is the slot + 1 (0 means the chain is full, as Shinobi's NULL). */
SYCHAIN syChainAddHandler(s32 event, SyChainFunc func, u32 priority, void *arg)
{
    for (u32 i = 0; i < HANDLERS; i++) {
        if (g_chain[i].fn) continue;
        g_chain[i].event = event;
        g_chain[i].fn = func;
        g_chain[i].priority = priority;
        g_chain[i].arg = arg;
        g_chain[i].seq = g_seq++;
        if (event == EV_VBLANK && !g_vblank_task) {
            port_add_vblank_task(vblank_chain, NULL);
            g_vblank_task = 1;
        } else if (event != EV_VBLANK) {
            port_log("sy_chain: event %03x handler kept, the port never raises it", (u32)event);
        }
        return (SYCHAIN)(i + 1u);
    }
    port_log("sy_chain: full, event %03x handler dropped", (u32)event);
    return 0;
}

void syChainDeleteHandler(SYCHAIN handle)
{
    if (handle >= 1u && handle <= HANDLERS) g_chain[handle - 1u].fn = NULL;
}
