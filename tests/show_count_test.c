/* tests/test_show_count.py: port/base/show_count.c alone, with a semaphore of 2 as the frame-latency object and
 * SHOW_COUNT_WAIT=test_wait, which runs the other thread's step in the middle of this thread's wait. A Present is
 * show_count_used followed by the count coming back (the frame leaving the queue). Exit 0 when every count is kept. */
#include <windows.h>
#include <stdio.h>
#include "show_count.h"

#define COUNTS 2
static HANDLE g_sem;
static int g_mode; /* 1: the other thread takes a count first; 2: this look fails, a frame leaves, the other takes it */
static int g_fail;

DWORD test_wait(HANDLE h, DWORD ms)
{
    int mode = g_mode;
    DWORD r;
    g_mode = 0;
    if (mode == 1) show_count_take(g_sem, 0);
    r = WaitForSingleObject(h, ms);
    if (mode == 2) {
        ReleaseSemaphore(g_sem, 1, NULL);
        show_count_take(g_sem, 2);
    }
    return r;
}

static int in_object(void)
{
    int n = 0, i;
    while (WaitForSingleObject(g_sem, 0) == WAIT_OBJECT_0) n++;
    for (i = 0; i < n; i++) ReleaseSemaphore(g_sem, 1, NULL);
    return n;
}

static void check(const char *what)
{
    int obj = in_object();
    long held = show_count_held();
    if (obj + held != COUNTS) {
        printf("FAIL %s: %d in the object + %ld held = %ld, not %d: a count was lost\n", what, obj, held, obj + held, COUNTS);
        g_fail = 1;
    }
}

static int present(void)
{
    if (!show_count_take(g_sem, 0)) return 0;
    show_count_used(g_sem);
    ReleaseSemaphore(g_sem, 1, NULL); /* the frame leaves the queue */
    return 1;
}

/* Two real threads for a moment: one shows as the tick does, one waits as the thread of in-between frames does. */
static CRITICAL_SECTION g_lock;
static volatile LONG g_stop, g_presents;

static DWORD WINAPI shower(void *arg)
{
    int waits = arg != NULL;
    while (!g_stop) {
        EnterCriticalSection(&g_lock);
        if (show_count_take(g_sem, 0)) {
            show_count_used(g_sem);
            LeaveCriticalSection(&g_lock);
            InterlockedIncrement(&g_presents);
            ReleaseSemaphore(g_sem, 1, NULL);
        } else {
            LeaveCriticalSection(&g_lock);
            if (waits) show_count_take(g_sem, 2); /* gfx_show_wait: no lock */
        }
    }
    return 0;
}

int main(void)
{
    HANDLE t[2];
    int i, shown = 0;
    g_sem = CreateSemaphoreW(NULL, COUNTS, COUNTS, NULL);
    InitializeCriticalSection(&g_lock);

    if (!show_count_take(NULL, 0)) printf("FAIL no object: a frame held back\n"), g_fail = 1;

    for (i = 0; i < 100; i++) shown += present();
    if (shown != 100) printf("FAIL plain: %d of 100 presented\n", shown), g_fail = 1;
    check("plain presents");

    g_mode = 1; /* both threads find none held and each takes a count */
    show_count_take(g_sem, 2);
    present();
    present();
    check("two threads each took a count");

    for (i = 0; i < 2; i++) { /* a look that finds nothing must not wipe the count the other thread took meanwhile */
        while (WaitForSingleObject(g_sem, 0) == WAIT_OBJECT_0) {} /* the queue is full: both frames are in it */
        while (show_count_held() > 0) show_count_used(g_sem);
        g_mode = 2;
        show_count_take(g_sem, 0);
        if (i == 0) {
            ReleaseSemaphore(g_sem, 1, NULL); /* the other frame leaves too */
            check("a failed look beside a count taken");
        }
    }
    /* twice over, the old flag had lost both counts here: nothing was presented again */
    shown = present();
    ReleaseSemaphore(g_sem, 1, NULL);
    shown += present();
    if (shown != 2) printf("FAIL the picture stopped: %d of 2 presented after two failed looks beside a taken count\n", shown), g_fail = 1;

    while (WaitForSingleObject(g_sem, 0) == WAIT_OBJECT_0) {} /* a known state for the threads */
    while (show_count_held() > 0) show_count_used(g_sem);
    ReleaseSemaphore(g_sem, COUNTS, NULL);
    t[0] = CreateThread(NULL, 0, shower, NULL, 0, NULL);
    t[1] = CreateThread(NULL, 0, shower, (void *)1, 0, NULL);
    Sleep(300);
    g_stop = 1;
    WaitForMultipleObjects(2, t, TRUE, 5000);
    check("two threads for 300 ms");
    if (g_presents < 1000) printf("FAIL threads: only %ld presents\n", g_presents), g_fail = 1;

    printf("%s (%ld threaded presents)\n", g_fail ? "FAILED" : "show_count: ok", g_presents);
    return g_fail;
}
