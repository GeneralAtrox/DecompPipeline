/* The host side of the game's kernel and XAM imports. An import is written when a run stops on it and not before: every
 * case below names the stop (the arguments the log showed) that first needed it. The XEX holds ordinals only; the names come
 * from the Xenia export tables (data/export_names.json, data/NOTICE-xenia.txt) and each case was first identified from what the
 * callers pass, whose evidence stays in its comment. */
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "xb.h"

#define KRNL(imp) (xb_lib_name[(imp)->lib][1] == 'b')
#define KRNL_LIB(imp) (KRNL(imp) ? 1u : 0u)

typedef struct { int made; CRITICAL_SECTION cs; } HostCs;

static CRITICAL_SECTION *cs_of(u32 guest)
{
    HostCs *h = (HostCs *)xb_host_object(guest, sizeof *h);
    if (!h->made) { InitializeCriticalSection(&h->cs); h->made = 1; }
    return &h->cs;
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

void xb_token_set_event(u32 token) { SetEvent(host_handle(token)); }

/* System notifications waiting for XNotifyGetNext, posted and read on the main thread */
static u32 s_notes[8][2];
static unsigned s_note_read, s_note_write;

static void note_post(u32 id, u32 param)
{
    s_notes[s_note_write % 8][0] = id;
    s_notes[s_note_write % 8][1] = param;
    s_note_write++;
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
        if (type > 1) xb_stop("wait_object", "a waitable object that is not an event", type);
        e->h = CreateEventA(NULL, type == 0, rd32(guest + 4) != 0, NULL);
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
    return WaitForSingleObject(h, ms) == WAIT_TIMEOUT ? 0x102u : 0u;
}

/* A guest thread's processor block (r13) with the fields the game reads: +0x100 the thread block, whose +0x14c is the thread id. */
static u32 new_processor_block(u32 id)
{
    u32 pcr = xb_alloc(0x10000u);
    wr32(pcr + 0x100u, pcr + 0x1000u);
    wr32(pcr + 0x1000u + 0x14cu, id);
    return pcr;
}

/* A guest thread: a host thread, its own guest stack, and its own processor block. */
typedef struct { u32 entry, r3, r4, sp, pcr; } XbThread;

static DWORD WINAPI guest_thread(LPVOID p)
{
    XbThread t = *(XbThread *)p;
    TcArgs a;
    free(p);
    tc_r13 = t.pcr;
    memset(&a, 0, sizeof a);
    a.r[0] = t.r3;
    a.r[1] = t.r4;
    a.sp[0] = t.sp;
    (void)tc_call(t.entry, &a);
    return 0;
}

/* The console's audio output: one client, whose routine is called on the real clock for each frame (256 samples at 48 kHz) and which submits
 * the frame. Nothing is played yet: the log counts the frames and keeps the loudest sample, which is the proof the game's mix arrives. */
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
    if (!(InterlockedIncrement(&s_audio_frames) % 1875)) port_log("audio %ld frames, loudest sample %.3f\n", s_audio_frames, s_audio_peak);
}

static DWORD WINAPI audio_clock(LPVOID p)
{
    LARGE_INTEGER f, now, next;
    TcArgs a;
    (void)p;
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
#include "xb_imports_xam.h"

int xb_import_call(const XbImp *imp, const TcArgs *a, u64 *r3, f64 *f1)
{
    (void)f1;
    if (KRNL(imp) && xb_file_import(imp->ord, a, r3)) return 1;
    if (!KRNL(imp) && xb_net_import(imp->ord, a, r3)) return 1;
    if (!KRNL(imp) && xb_content_import(imp->ord, a, r3)) return 1;
    return KRNL(imp) ? krnl_call(imp, a, r3, f1) : xam_call(imp, a, r3, f1);
}
