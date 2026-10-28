/* Host coroutines (platform.h host_coro_*): Windows fibers. A fiber runs only when switched to, on the thread that
 * switches, so no switch happens that the program did not ask for. The thread itself becomes the first one at the
 * first use. */
#include <windows.h>
#include "port.h"
#include "platform.h"

#define CORO_STACK_COMMIT 0x10000u  /* host stack committed at first */
#define CORO_STACK_RESERVE 0x100000u /* and at most: the main thread's default */

struct HostCoro {
    void *fiber;
    HostCoroFn fn;
    void *arg;
};

static HostCoro g_thread; /* the thread's own */
static HostCoro *g_running;

static void WINAPI coro_main(void *p)
{
    HostCoro *c = p;
    c->fn(c->arg);
    port_exit("host coroutine: its function returned", 1);
}

HostCoro *host_coro_self(void)
{
    if (!g_running) {
        g_thread.fiber = IsThreadAFiber() ? GetCurrentFiber() : ConvertThreadToFiber(NULL);
        if (!g_thread.fiber) return NULL;
        g_running = &g_thread;
    }
    return g_running;
}

HostCoro *host_coro_create(HostCoroFn fn, void *arg)
{
    HostCoro *c;
    if (!host_coro_self()) return NULL;
    c = HeapAlloc(GetProcessHeap(), HEAP_ZERO_MEMORY, sizeof *c);
    if (!c) return NULL;
    c->fn = fn;
    c->arg = arg;
    c->fiber = CreateFiberEx(CORO_STACK_COMMIT, CORO_STACK_RESERVE, 0, coro_main, c);
    if (!c->fiber) {
        HeapFree(GetProcessHeap(), 0, c);
        return NULL;
    }
    return c;
}

void host_coro_switch(HostCoro *to)
{
    if (!host_coro_self() || to == g_running) return;
    g_running = to;
    SwitchToFiber(to->fiber);
}

void host_coro_delete(HostCoro *c)
{
    if (!c || c == g_running || c == &g_thread) return;
    DeleteFiber(c->fiber);
    HeapFree(GetProcessHeap(), 0, c);
}
