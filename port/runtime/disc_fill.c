/* A large file read behind its reader: the sectors come in on a thread of their own, a chunk at a time, behind a fill
 * mark that the reader waits on for the bytes it needs next (disc_fill_wait). A 90 MB movie then starts at once
 * instead of holding the tick while the whole file comes in (a 92 MB movie held it 217 ms on the user's laptop,
 * 1.7 s on build box A). The reader sees the same bytes in the same order; only when they arrive moves.
 *
 * The read is counted when it is asked for, on the asking thread, as host_disc_read counts it, so the work counts and
 * the play log's loading marks do not move; the thread reads with host_disc_read_quiet. Only the kit's own readers
 * use it (sdk/adx_mw.c's movies): game code may touch a buffer as soon as its read returns, with no call to wait in,
 * so gdFsRead stays synchronous. disc_lock is gdi.c's: the game thread's reads and the fill take turns on the
 * image's files, a chunk at a time.
 *
 * The waits are counted for the play log's tick cost line (prof_tc.c, disc_wait_line): how often and how long the
 * game thread waited, by reader (gdi: disc_lock behind a fill's chunk; adx: a movie's first bytes inside adx_mw.c's
 * StartFname; movie: the bytes a playing movie needs next), and the other threads' waits. Only a wait that blocks is
 * timed; the game thread alone writes its counters, so no lock is added.
 *
 * Nearest existing: pvr/pvr_pack_load.c's loader thread - it loads pack textures by name for the renderer, not
 * sectors into a caller's buffer behind a mark the caller waits on. */
#include <windows.h>
#include <stdio.h>
#include <string.h>
#include "platform.h"
#include "work_count.h"
#include "play_stats.h"
#include "gdi_int.h"

#define CHUNK 256u /* sectors a read (512 KB): a reader waits at most about one chunk for bytes just past the mark */
#define SECTOR 2048u
#define FILLS 4 /* at once; no heap block, so the work counters' allocations do not move */

struct DiscFill {
    u32 lba, sectors;
    u8 *out;
    SRWLOCK lock;
    CONDITION_VARIABLE moved;
    u32 have;       /* sectors in, from the first */
    int stop, done; /* done: the thread has ended (all in, a read error, or stopped) */
    HANDLE thread;
    int used, reader; /* reader: DISC_READER_*, whose waits these are */
};

static DiscFill g_fills[FILLS];
static SRWLOCK g_disc = SRWLOCK_INIT;

/* Waits: the game thread's by reader (written by it alone), the other threads' with interlocked adds. QPC ticks. */
static DWORD g_wait_game;
static u32 g_wait_n[DISC_READERS];
static LONGLONG g_wait_t[DISC_READERS], g_wait_max[DISC_READERS];
static volatile LONG g_wait_other_n;
static volatile LONGLONG g_wait_other_t;

static LONGLONG wait_now(void)
{
    LARGE_INTEGER t;
    QueryPerformanceCounter(&t);
    return t.QuadPart;
}

static void wait_note(int reader, LONGLONG t0)
{
    LONGLONG t = wait_now() - t0;
    if (g_wait_game && GetCurrentThreadId() == g_wait_game) {
        g_wait_n[reader]++;
        g_wait_t[reader] += t;
        if (t > g_wait_max[reader]) g_wait_max[reader] = t;
    } else {
        InterlockedIncrement(&g_wait_other_n);
        InterlockedAdd64(&g_wait_other_t, t);
    }
}

void disc_wait_game_thread(void) { g_wait_game = GetCurrentThreadId(); }

/* On the game thread: the waits since the last call, appended to the tick cost line; then from zero again. */
void disc_wait_line(char *out, size_t cap)
{
    static const char *const names[DISC_READERS] = {"gdi", "adx", "movie"};
    LARGE_INTEGER f;
    double ms, all_t = 0.0, all_max = 0.0;
    u32 all_n = 0;
    size_t at;
    QueryPerformanceFrequency(&f);
    ms = 1000.0 / (double)f.QuadPart;
    for (int i = 0; i < DISC_READERS; i++) {
        all_n += g_wait_n[i];
        all_t += (double)g_wait_t[i] * ms;
        if ((double)g_wait_max[i] * ms > all_max) all_max = (double)g_wait_max[i] * ms;
    }
    sprintf_s(out, cap, " | disc waits on the game thread: %u, %.2f ms, the longest %.2f ms (", all_n, all_t, all_max);
    for (int i = 0; i < DISC_READERS; i++) {
        at = strlen(out);
        sprintf_s(out + at, cap - at, "%s%s %u, %.2f ms, longest %.2f", i ? "; " : "", names[i], g_wait_n[i],
                  (double)g_wait_t[i] * ms, (double)g_wait_max[i] * ms);
        g_wait_n[i] = 0, g_wait_t[i] = g_wait_max[i] = 0;
    }
    at = strlen(out);
    sprintf_s(out + at, cap - at, "), other threads %ld, %.2f ms", InterlockedExchange(&g_wait_other_n, 0),
              (double)InterlockedExchange64(&g_wait_other_t, 0) * ms);
}

void disc_lock(int on)
{
    if (!on) ReleaseSRWLockExclusive(&g_disc);
    else if (!TryAcquireSRWLockExclusive(&g_disc)) { /* a fill's chunk is being read */
        LONGLONG t0 = wait_now();
        AcquireSRWLockExclusive(&g_disc);
        wait_note(DISC_READER_GDI, t0);
    }
}

static DWORD WINAPI filler(void *arg)
{
    DiscFill *f = (DiscFill *)arg;
    int ok = 1, stop = 0;
    for (u32 at = 0; at < f->sectors && ok && !stop;) {
        u32 n = f->sectors - at < CHUNK ? f->sectors - at : CHUNK;
        ok = host_disc_read_quiet(f->lba + at, n, f->out + (size_t)at * SECTOR);
        if (!ok) port_log("disc: read behind: error at LBA %u\n", f->lba + at);
        at += ok ? n : 0u;
        AcquireSRWLockExclusive(&f->lock);
        f->have = at;
        stop = f->stop;
        WakeAllConditionVariable(&f->moved);
        ReleaseSRWLockExclusive(&f->lock);
    }
    AcquireSRWLockExclusive(&f->lock);
    f->done = 1;
    WakeAllConditionVariable(&f->moved);
    ReleaseSRWLockExclusive(&f->lock);
    return 0;
}

DiscFill *disc_fill_start(u32 lba, u32 sectors, void *out)
{
    DiscFill *f = NULL;
    for (int i = 0; i < FILLS && out && sectors && !f; i++)
        if (!g_fills[i].used) f = &g_fills[i];
    if (!f) return NULL;
    memset(f, 0, sizeof *f);
    f->lba = lba;
    f->sectors = sectors;
    f->out = (u8 *)out;
    f->reader = DISC_READER_MOVIE;
    InitializeSRWLock(&f->lock);
    InitializeConditionVariable(&f->moved);
    f->thread = CreateThread(NULL, 0, filler, f, 0, NULL);
    if (!f->thread) return NULL;
    f->used = 1;
    work_add(WC_DISC_BYTES, sectors * SECTOR);
    play_stats_disc(sectors * SECTOR); /* a load, counted now as host_disc_read would have counted it */
    return f;
}

int disc_fill_wait(DiscFill *f, u32 bytes)
{
    u32 need = bytes / SECTOR + (bytes % SECTOR != 0);
    int ok;
    if (!f) return 1;
    if (need > f->sectors) need = f->sectors;
    AcquireSRWLockExclusive(&f->lock);
    if (f->have < need && !f->done) {
        LONGLONG t0 = wait_now();
        while (f->have < need && !f->done) SleepConditionVariableSRW(&f->moved, &f->lock, INFINITE, 0);
        wait_note(f->reader, t0);
    }
    ok = f->have >= need;
    ReleaseSRWLockExclusive(&f->lock);
    return ok;
}

void disc_fill_reader(DiscFill *f, int reader)
{
    if (f && reader >= 0 && reader < DISC_READERS) f->reader = reader;
}

int disc_fill_wait_v(void *f, u32 bytes) { return disc_fill_wait((DiscFill *)f, bytes); }

void disc_fill_end(DiscFill *f)
{
    if (!f) return;
    AcquireSRWLockExclusive(&f->lock);
    f->stop = 1;
    ReleaseSRWLockExclusive(&f->lock);
    WaitForSingleObject(f->thread, INFINITE); /* the chunk in flight, then the thread ends */
    CloseHandle(f->thread);
    f->used = 0;
}

void disc_fill_end_v(void *f) { disc_fill_end((DiscFill *)f); }
