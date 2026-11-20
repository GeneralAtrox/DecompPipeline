/* The host side of the game's kernel and XAM imports. An import is written when a run stops on it and not before: every
 * case below names the stop (the arguments the log showed) that first needed it. The XEX holds ordinals only; the names come
 * from the Xenia export tables (data/export_names.json, data/NOTICE-xenia.txt) and each case was first identified from what the
 * callers pass, whose evidence stays in its comment. */
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "xb.h"
#include "xb_game.h"
#include "xb_wait_stats.h"   /* XB_WAIT_STATS=1: each thread's waits by the thread it waited on */

#define KRNL(imp) (xb_lib_name[(imp)->lib][1] == 'b')
#define KRNL_LIB(imp) (KRNL(imp) ? 1u : 0u)

/* A guest critical section's host one, made on first use under a lock: two threads entering one the game never initialised (a
 * static, or one RtlInitializeCriticalSectionAndSpinCount set up) could each make it and both be inside */
typedef struct { volatile LONG made; CRITICAL_SECTION cs; } HostCs;

static CRITICAL_SECTION *cs_of(u32 guest)
{
    static SRWLOCK lock = SRWLOCK_INIT;
    HostCs *h = (HostCs *)xb_host_object(guest, sizeof *h);
    if (h->made) return &h->cs;
    AcquireSRWLockExclusive(&lock);
    if (!h->made) { InitializeCriticalSection(&h->cs); h->made = 1; }
    ReleaseSRWLockExclusive(&lock);
    return &h->cs;
}

/* The critical section each waiting thread is held at, for the watchdog's dump (xb_cs_waits_dump): which thread owns it */
static struct { DWORD tid; u32 guest; } s_cs_wait[64];
static LONG s_cs_wait_n;
static __declspec(thread) int s_cs_slot = -1;

static u32 *cs_wait_slot(void)
{
    if (s_cs_slot < 0) {
        LONG n = InterlockedIncrement(&s_cs_wait_n) - 1;
        if (n >= 64) return NULL;
        s_cs_wait[n].tid = GetCurrentThreadId();
        s_cs_slot = (int)n;
    }
    return &s_cs_wait[s_cs_slot].guest;
}

void xb_cs_waits_dump(void)
{
    LONG i, n = s_cs_wait_n < 64 ? s_cs_wait_n : 64;
    for (i = 0; i < n; i++) {
        u32 g = s_cs_wait[i].guest;
        CRITICAL_SECTION *c;
        if (!g) continue;
        c = cs_of(g);
        port_log("guest thread %lu waits for critical section %08x: owner host thread %lu, entered %ld times\n", s_cs_wait[i].tid, g,
                 (DWORD)(UINT_PTR)c->OwningThread, c->RecursionCount);
    }
}

/* A kernel handle the game holds: a token, with the host handle in this table. */
#define HANDLE_BASE 0x7FFE2000u
static HANDLE s_handles[1024];
static LONG s_handle_n;

static u32 handle_new(HANDLE h)
{
    LONG n = InterlockedIncrement(&s_handle_n) - 1;
    if (n >= 1024) xb_stop("out_of_memory", "kernel handles", (u32)n);
    s_handles[n] = h;
    return HANDLE_BASE + 4u * (u32)n;
}

u32 xb_token_new(void) { return handle_new(NULL); }

static HANDLE host_handle(u32 guest)
{
    u32 n = (guest - HANDLE_BASE) >> 2;
    if (guest < HANDLE_BASE || n >= (u32)s_handle_n) xb_stop("bad_handle", "a kernel handle the port did not give", guest);
    return s_handles[n];
}

/* Set on the host thread that runs the console's vertical blank interrupt. A callback there can fire before the game has made what it signals: Too
 * Human installs 829a4a88 (829a4aa0) before the event at [[833332e0] +0x4c] +0x954, and the first blanks find 0x30303030. The kernel answers that
 * with STATUS_INVALID_HANDLE and the callback ignores it, so on this thread only a handle the port did not give is that answer, logged once */
static __declspec(thread) int s_interrupt;

static int handle_given(u32 guest)
{
    u32 n = (guest - HANDLE_BASE) >> 2;
    if (guest >= HANDLE_BASE && n < (u32)s_handle_n) return 1;
    if (!s_interrupt) return 1;     /* host_handle stops on it */
    {
        static volatile long logged;
        if (!InterlockedExchange(&logged, 1)) port_log("vblank callback: handle %08x the port did not give, answered STATUS_INVALID_HANDLE as the kernel does\n", guest);
    }
    return 0;
}

void xb_token_set_event(u32 token) { HANDLE e = host_handle(token); ws_signal(e); SetEvent(e); }

/* NtSuspendThread. The console stops the thread where it is; a host SuspendThread could stop it inside the CRT's heap or the log's lock,
 * which every other thread then waits on for ever. So the count is the kit's own and a suspended thread stops at its next guest call
 * (xb_call.c), outside any host lock. A thread made suspended by ExCreateThread is still started with the host's ResumeThread */
typedef struct { DWORD tid; volatile LONG count; HANDLE wake; } XbSuspend;
static XbSuspend s_suspend[64];
static SRWLOCK s_suspend_lock = SRWLOCK_INIT;
volatile long xb_suspends;   /* threads whose count is above 0: xb_call.c looks only while there are some */

static XbSuspend *suspend_of(DWORD tid, int make)
{
    XbSuspend *s = NULL;
    unsigned i;
    AcquireSRWLockExclusive(&s_suspend_lock);
    for (i = 0; i < 64 && !s; i++)
        if (s_suspend[i].tid == tid) s = &s_suspend[i];
    for (i = 0; i < 64 && !s && make; i++)
        if (!s_suspend[i].tid) s = &s_suspend[i], s->tid = tid, s->wake = CreateEventW(NULL, TRUE, TRUE, NULL);
    ReleaseSRWLockExclusive(&s_suspend_lock);
    if (!s && make) xb_stop("out_of_memory", "more than 64 threads suspended", tid);
    return s;
}

void xb_thread_safe_point(void)
{
    XbSuspend *s = suspend_of(GetCurrentThreadId(), 0);
    while (s && s->count > 0) WaitForSingleObject(s->wake, INFINITE);   /* a suspended guest thread: until NtResumeThread */
}

/* 1 when NtSuspendThread holds the calling thread */
static int thread_held(void)
{
    XbSuspend *s = suspend_of(GetCurrentThreadId(), 0);
    return s && s->count > 0;
}

static u32 thread_suspend(HANDLE h)
{
    XbSuspend *s = suspend_of(GetThreadId(h), 1);
    LONG prev;
    ResetEvent(s->wake);
    prev = InterlockedIncrement(&s->count) - 1;
    if (!prev) InterlockedIncrement(&xb_suspends);
    if (s->tid == GetCurrentThreadId()) xb_thread_safe_point();
    return (u32)prev;
}

/* The previous count, and 1, when the thread was suspended by thread_suspend; 0 when it was not (made suspended, or not at all) */
static int thread_resume(HANDLE h, u32 *prev)
{
    XbSuspend *s = suspend_of(GetThreadId(h), 0);
    LONG c;
    if (!s || s->count <= 0) return 0;
    c = InterlockedDecrement(&s->count);
    *prev = (u32)c + 1;
    if (!c) {
        InterlockedDecrement(&xb_suspends);
        SetEvent(s->wake);
    }
    return 1;
}

/* A kernel event the game set up in place: on first use the host event takes its type (byte +0: 0 stays set until reset, 1 releases one
 * waiter) and its state (word +4) from the guest structure. Any other kind of waitable object stops. */
typedef struct { HANDLE h; } HostEvent;

static HANDLE kevent_of(u32 guest)
{
    static SRWLOCK lock = SRWLOCK_INIT;
    HostEvent *e = (HostEvent *)xb_host_object(guest, sizeof *e);
    if (e->h) return e->h;
    AcquireSRWLockExclusive(&lock);
    if (!e->h) {
        u32 type = rd8(guest);
        if (type == 5) e->h = CreateSemaphoreA(NULL, (LONG)rd32(guest + 4), (LONG)rd32(guest + 16), NULL);   /* KSEMAPHORE: the count, then at +16 the limit */
        else if (type > 1) xb_stop("wait_object", "a waitable object that is neither an event nor a semaphore", type);
        else e->h = CreateEventA(NULL, type == 0, rd32(guest + 4) != 0, NULL);
    }
    ReleaseSRWLockExclusive(&lock);
    return e->h;
}

/* A wait with the kernel's timeout: a pointer to 100 ns units, negative for a time from now; no pointer is for ever. */
static u32 wait_host(HANDLE h, u32 timeout_p)
{
    DWORD ms = INFINITE;
    if (timeout_p) {
        s64 t = (s64)rd64(timeout_p);
        ms = t >= 0 ? 0 : (DWORD)((-t + 9999) / 10000);
    }
    {
        LONG64 t0 = ws_start();
        DWORD r = WaitForSingleObject(h, ms);
        if (r != WAIT_TIMEOUT) ws_waited(t0, h, 0);
        return r == WAIT_TIMEOUT ? 0x102u : 0u;
    }
}

/* A guest thread's processor block (r13) with the fields the game reads: +0x100 the thread block, whose +0x14c is the thread id. */
static u32 new_processor_block(u32 id)
{
    u32 pcr = xb_alloc(0x10000u);
    wr32(pcr + 0x100u, pcr + 0x1000u);
    wr32(pcr + 0x1000u + 0x14cu, id);
    xb_tls_new(pcr);
    return pcr;
}

/* The processor number in a thread's block (+0x10c), from the affinity it was made with or given. The host still places the threads; the
 * number is what the game's code reads to pick per-processor data. Too Human's XMA thread (82a041b8, made for processor 4) meets the audio
 * engine's processors at a barrier (82a04b30) by that byte, and spun there for good when every block said 0: no sound frame was ever made */
static struct { u32 handle, pcr; } s_thread_pcr[64];
static unsigned s_thread_pcr_n;

static void set_processor(u32 pcr, u32 mask)
{
    unsigned p;
    for (p = 0; p < 6 && !(mask >> p & 1); p++) {}
    if (p < 6) wr8(pcr + 0x10cu, (u8)p);
}

static u32 thread_pcr(u32 handle)
{
    unsigned i;
    if (handle == 0xFFFFFFFEu) return tc_r13;
    for (i = 0; i < s_thread_pcr_n; i++)
        if (s_thread_pcr[i].handle == handle) return s_thread_pcr[i].pcr;
    return 0;
}

/* A guest thread: a host thread, its own guest stack, and its own processor block. */
typedef struct { u32 entry, r3, r4, sp, pcr; } XbThread;

static DWORD WINAPI guest_thread(LPVOID p)
{
    XbThread t = *(XbThread *)p;
    TcArgs a;
    free(p);
    xb_databp_thread();
    tc_r13 = t.pcr;
    memset(&a, 0, sizeof a);
    a.r[0] = t.r3;
    a.r[1] = t.r4;
    a.sp[0] = t.sp;
    (void)tc_call(t.entry, &a);
    return 0;
}

/* The console's audio output: one client, whose routine is called on the real clock for each frame (256 samples at 48 kHz) and which submits
 * the frame, which goes to the sound output (xb_sound_driver_frame). The log also counts the frames and keeps the loudest sample. */
#define AUDIO_DRIVER 0x41550000u
static u32 s_audio_routine, s_audio_context;
static volatile LONG s_audio_frames;
static float s_audio_peak;

static void audio_frame(u32 samples)
{
    unsigned i;
    for (i = 0; i < 6 * 256; i++) {
        u32 w = rd32(samples + 4 * i);
        float v;
        memcpy(&v, &w, 4);
        if (v < 0) v = -v;
        if (v == v && v > s_audio_peak) s_audio_peak = v;
    }
    xb_sound_driver_frame(samples);
    if (!(InterlockedIncrement(&s_audio_frames) % 1875)) port_log("audio %ld frames, loudest sample %.3f\n", s_audio_frames, s_audio_peak);
}

static DWORD WINAPI audio_clock(LPVOID p)
{
    LARGE_INTEGER f, now, next;
    TcArgs a;
    (void)p;
    xb_databp_thread();
    tc_r13 = new_processor_block(GetCurrentThreadId());
    memset(&a, 0, sizeof a);
    a.sp[0] = xb_alloc(0x40000u) + 0x40000u - 0x100u;
    wr32(a.sp[0] + 0x80u, s_audio_context);   /* the routine takes the address of a word that holds the context (82282ad8 reads it first) */
    QueryPerformanceFrequency(&f);
    QueryPerformanceCounter(&next);
    for (;;) {
        a.r[0] = a.sp[0] + 0x80u;
        (void)tc_call(s_audio_routine, &a);
        next.QuadPart += f.QuadPart * 256 / 48000;
        for (QueryPerformanceCounter(&now); now.QuadPart < next.QuadPart; QueryPerformanceCounter(&now)) Sleep(1); /* not the tick: the game's audio routine's own clock, on its own thread */
    }
}

#ifdef XB_VBLANK_CALLBACK
/* The console's vertical blank, 60 times a second on the real clock, on its own guest thread as the kernel's interrupt would run it. The host
 * does what Too Human's library handler 8274f528 (device) does: count the blank (device +0x40a4) and call the game's callback, the word at
 * device + XB_VBLANK_CALLBACK, with {blanks, swaps (+0x40b8), flags 0}. Its swap queue (+0x413c) stays empty: the host presents at Swap.
 * Too Human's main thread waits on an event the callback sets whenever a frame took less than a 60th of a second */
static u32 s_vblank_device;

static DWORD WINAPI vblank_clock(LPVOID p)
{
    LARGE_INTEGER f, now, next;
    TcArgs a;
    u32 dev = s_vblank_device, cb, data;
    (void)p;
    s_interrupt = 1;
    xb_databp_thread();
    tc_r13 = new_processor_block(GetCurrentThreadId());
    memset(&a, 0, sizeof a);
    a.sp[0] = xb_alloc(0x40000u) + 0x40000u - 0x100u;
    data = a.sp[0] + 0x50u;
    QueryPerformanceFrequency(&f);
    QueryPerformanceCounter(&next);
    for (;;) {
        next.QuadPart += f.QuadPart / 60;
        for (QueryPerformanceCounter(&now); now.QuadPart < next.QuadPart; QueryPerformanceCounter(&now)) Sleep(1); /* not the tick: the console's display clock */
        wr32(dev + 0x40a4u, rd32(dev + 0x40a4u) + 1u);
        xb_gpu_callbacks_run(&a);   /* InsertCallback's callbacks since the last blank (XB_GPU_CB_DEFER), here as the GPU's interrupt would run them */
        if (!(cb = rd32(dev + XB_VBLANK_CALLBACK))) continue;
        wr32(data, rd32(dev + 0x40a4u));
        wr32(data + 4u, rd32(dev + 0x40b8u));
        wr32(data + 8u, 0);
        a.r[0] = data;
        (void)tc_call(cb, &a);
    }
}
#endif

void xb_vblank_start(u32 device)
{
#ifdef XB_VBLANK_CALLBACK
    if (s_vblank_device) return;
    s_vblank_device = device;
    CreateThread(NULL, 16u << 20, vblank_clock, NULL, STACK_SIZE_PARAM_IS_A_RESERVATION, NULL);
    port_log("vblank: 60 a second, the callback at device %08x + %x\n", device, XB_VBLANK_CALLBACK);
#else
    (void)device;
#endif
}

/* An import the game calls for an effect nothing in the front end reads: answered with 0 and logged once, so the table of
 * what the port does stays honest. */
static int no_effect(const XbImp *imp, u64 *r3)
{
    static unsigned char seen[2][1024];
    unsigned l = KRNL_LIB(imp), o = imp->ord & 1023;
    if (!seen[l][o]) { seen[l][o] = 1; port_log("import %s#%u %s answered 0, no effect\n", xb_lib_name[imp->lib], imp->ord, xb_export_name(xb_lib_name[imp->lib], imp->ord)); }
    *r3 = 0;
    return 1;
}

#include "xb_imports_krnl.h"
#include "xb_format.h"
#include "xb_imports_krnl2.h"
#include "xb_imports_xam.h"

int xb_import_call(const XbImp *imp, const TcArgs *a, u64 *r3, f64 *f1)
{
    (void)f1;
    if (KRNL(imp) && xb_file_import(imp->ord, a, r3)) return 1;
    if (!KRNL(imp) && xb_net_import(imp->ord, a, r3)) return 1;
    if (!KRNL(imp) && xb_content_import(imp->ord, a, r3)) return 1;
    return KRNL(imp) ? krnl_call(imp, a, r3, f1) || krnl2_call(imp, a, r3) : xam_call(imp, a, r3, f1);
}
