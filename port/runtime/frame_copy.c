/* In-between frames: the copies of guest RAM (frame_extra.c). */
#include <string.h>
#include <windows.h>
#include "port.h"
#include "platform.h"

#define PAGE 4096u

/* Guest RAM copies are memory-bound, and one core does not saturate the memory: a copy is split into slices, one
 * for this thread and one for each helper thread (--interp-copy-threads N: N - 1 helpers). Four threads copy 16 MB
 * in about 0.4 ms where one takes 1.5 to 2. */
#define COPY_HELPERS_MAX 7
static u32 g_copy_threads = 4;
static struct {
    HANDLE go;
    u8 *dst;
    const u8 *src;
    u32 size;
} g_slice[COPY_HELPERS_MAX];
static HANDLE g_copied;
static volatile LONG g_copy_left;

static DWORD WINAPI copy_helper(void *arg)
{
    u32 i = (u32)(uintptr_t)arg;
    for (;;) {
        WaitForSingleObject(g_slice[i].go, INFINITE); /* idle until the next copy */
        memcpy(g_slice[i].dst, g_slice[i].src, g_slice[i].size);
        if (InterlockedDecrement(&g_copy_left) == 0) SetEvent(g_copied);
    }
}

void extra_copy_init(void)
{
    if (g_copy_threads < 1u) g_copy_threads = 1u;
    if (g_copy_threads > COPY_HELPERS_MAX + 1u) g_copy_threads = COPY_HELPERS_MAX + 1u;
    g_copied = CreateEventW(NULL, FALSE, FALSE, NULL);
    for (u32 i = 0; i + 1u < g_copy_threads; i++) {
        g_slice[i].go = CreateEventW(NULL, FALSE, FALSE, NULL);
        HANDLE t = g_copied && g_slice[i].go ? CreateThread(NULL, 0, copy_helper, (void *)(uintptr_t)i, 0, NULL) : NULL;
        if (!t) {
            g_copy_threads = i + 1u;
            break;
        }
        SetThreadPriority(t, THREAD_PRIORITY_ABOVE_NORMAL);
        CloseHandle(t);
    }
}

void extra_copy_ram(u8 *dst, const u8 *src)
{
    u32 helpers = g_copy_threads - 1u, slice = GUEST_RAM_SIZE / g_copy_threads & ~(PAGE - 1u);
    g_copy_left = (LONG)helpers;
    for (u32 i = 0; i < helpers; i++) {
        u32 at = slice * (i + 1u);
        g_slice[i].dst = dst + at;
        g_slice[i].src = src + at;
        g_slice[i].size = i + 1u == helpers ? GUEST_RAM_SIZE - at : slice;
        SetEvent(g_slice[i].go);
    }
    memcpy(dst, src, helpers ? slice : GUEST_RAM_SIZE);
    if (helpers && WaitForSingleObject(g_copied, 5000) != WAIT_OBJECT_0)
        port_exit("frame_extra: a guest RAM copy helper did not finish", 1);
}

void extra_copy_threads(u32 threads) { g_copy_threads = threads; }
u32 extra_copy_thread_count(void) { return g_copy_threads; }
