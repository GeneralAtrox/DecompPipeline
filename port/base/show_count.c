/* The counts taken from the swap chain's frame-latency object. The object holds as many counts as the swap chain may
 * queue frames (2, present_device.c); a wait takes one and only a presented frame leaving the queue gives one back.
 * So a count taken and not followed by a Present is gone for good, and with both gone the display takes no frame
 * ever again: the picture stops while the game runs on.
 *
 * That happened (2026-11-12, a 16-vehicle battle in one game, on a loaded laptop). The counts were one flag in
 * present.c, read and written by two threads with no lock: the game's thread (gfx_can_show from port_extra_idle) and
 * the thread that shows frames (gfx_show_wait, which holds no lock on purpose). Two ways to lose one:
 *   - both threads saw the flag clear and each took a count: two taken, one flag, one Present;
 *   - one thread's look found nothing and wrote 0 over the 1 the other had just written for the count it took.
 * Here they are a number that only goes up by a count taken and down by a Present made, each in one step, so any
 * interleaving keeps every count: held + the object's own = the swap chain's.
 * No Direct3D here: tests/show_count_test.c builds this file alone, with a semaphore as the object. */
#include <windows.h>
#include "show_count.h"

#ifdef SHOW_COUNT_WAIT /* the test's wait: it runs the other thread's step in the middle of this one's */
DWORD SHOW_COUNT_WAIT(HANDLE h, DWORD ms);
#else
#define SHOW_COUNT_WAIT(h, ms) WaitForSingleObject((h), (ms))
#endif

static volatile LONG g_held;

int show_count_take(void *latency, unsigned wait_ms)
{
    if (!latency || g_held > 0) return 1; /* no object (no window): nothing holds a frame back */
    if (SHOW_COUNT_WAIT((HANDLE)latency, (DWORD)wait_ms) == WAIT_OBJECT_0) InterlockedIncrement(&g_held);
    return g_held > 0;
}

/* The caller holds gfx_lock (every Present does), so only one thread takes a count down at a time. */
void show_count_used(void *latency)
{
    if (latency && g_held > 0) InterlockedDecrement(&g_held);
}

long show_count_held(void) { return g_held; }
