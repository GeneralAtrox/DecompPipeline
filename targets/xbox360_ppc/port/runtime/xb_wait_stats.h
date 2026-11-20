/* xb_imports.c, XB_WAIT_STATS=1 (one translation unit with it; here for the 400-line cap): how long each guest thread waits, and on whom. Every
 * kernel wait (KeWaitForSingleObject, NtWaitForSingleObjectEx, KeWaitForMultipleObjects) and contended critical section is timed, and charged
 * to the thread that last set or released the object that ended it (the section's owner when it was found held). Every 100 frames the log
 * has a line per waiting thread with its waits in ms a frame by the thread they waited on, each named by the guest function it started in:
 * does Too Human's game thread wait on the mix thread 82a041b8 (fn_82a03608)? 00000000: an object no guest thread set (a host clock, the
 * vertical blank); 00000001: a section whose owner was not known. Off: one test of a flag per wait and per set.
 * Nearest existing: xb_cs_waits_dump (who holds a critical section now, for the watchdog) - a moment, not time summed per pair of threads. */

static int ws_on(void)
{
    static int on = -1;
    if (on < 0) { const char *e = getenv("XB_WAIT_STATS"); on = e && e[0] == '1'; }
    return on;
}

static struct { HANDLE h; u32 by; } s_ws_set[1024];   /* the last thread to set or release each object (racy: a stale name only misnames one wait) */
typedef struct { u32 waiter, on; LONG64 ticks, n; } WsPair;
static WsPair s_ws_pair[128];
static SRWLOCK s_ws_lock = SRWLOCK_INIT;
static unsigned s_ws_from = ~0u;

static unsigned ws_slot(HANDLE h) { return (unsigned)(((ULONG_PTR)h >> 2) * 2654435761u) & 1023u; }

static void ws_signal(HANDLE h)
{
    unsigned k;
    if (!ws_on() || !h) return;
    k = ws_slot(h);
    s_ws_set[k].by = xb_thread_entry(), s_ws_set[k].h = h;
}

#define WS_SIGNAL(e) (ws_on() ? ws_signal(e) : (void)0)   /* the object is looked up only when counting */

static LONG64 ws_start(void)
{
    LARGE_INTEGER t;
    if (!ws_on()) return 0;
    QueryPerformanceCounter(&t);
    return t.QuadPart;
}

static void ws_report(unsigned frame)
{
    LARGE_INTEGER f;
    unsigned i, j, n, frames = frame > s_ws_from ? frame - s_ws_from : 1u;
    char line[1024];
    QueryPerformanceFrequency(&f);
    for (i = 0; i < 128; i++) {   /* one line per waiting thread: its pairs, largest first */
        u32 w = s_ws_pair[i].waiter;
        int at;
        double all = 0;
        if (!s_ws_pair[i].n) continue;
        for (j = 0; j < i; j++) if (s_ws_pair[j].n && s_ws_pair[j].waiter == w) break;
        if (j < i) continue;
        for (j = i; j < 128; j++) if (s_ws_pair[j].n && s_ws_pair[j].waiter == w) all += (double)s_ws_pair[j].ticks;
        at = sprintf_s(line, sizeof line, "waits frames %u-%u: thread %08x waited %.2f ms a frame; on", s_ws_from, frame - 1u, w, all * 1000.0 / (double)f.QuadPart / frames);
        for (n = 0; n < 8 && at > 0; n++) {
            unsigned best = ~0u;
            for (j = i; j < 128; j++)
                if (s_ws_pair[j].n > 0 && s_ws_pair[j].waiter == w && (best == ~0u || s_ws_pair[j].ticks > s_ws_pair[best].ticks)) best = j;
            if (best == ~0u) break;
            at += sprintf_s(line + at, sizeof line - at, " %08x %.2f ms (%lld waits)", s_ws_pair[best].on, (double)s_ws_pair[best].ticks * 1000.0 / (double)f.QuadPart / frames,
                            (long long)s_ws_pair[best].n / frames);
            s_ws_pair[best].n = -s_ws_pair[best].n;   /* listed */
        }
        port_log("%s\n", line);
    }
    memset(s_ws_pair, 0, sizeof s_ws_pair);
    s_ws_from = frame;
}

/* A wait that began at t0 (ws_start) ended by object h, or by the critical section's owner `owner_tid` when h is 0 */
static void ws_waited(LONG64 t0, HANDLE h, unsigned long owner_tid)
{
    LARGE_INTEGER t;
    u32 me, on;
    unsigned i, frame;
    if (!t0) return;
    QueryPerformanceCounter(&t);
    me = xb_thread_entry();
    on = h ? (s_ws_set[ws_slot(h)].h == h ? s_ws_set[ws_slot(h)].by : 0u) : (xb_thread_entry_of(owner_tid) | (owner_tid ? 0u : 1u));
    frame = xb_frame_number();
    AcquireSRWLockExclusive(&s_ws_lock);
    if (s_ws_from == ~0u) s_ws_from = frame;
    if (frame / 100u != s_ws_from / 100u) ws_report(frame);
    for (i = 0; i < 128; i++)
        if (!s_ws_pair[i].n || (s_ws_pair[i].waiter == me && s_ws_pair[i].on == on)) break;
    if (i < 128) s_ws_pair[i].waiter = me, s_ws_pair[i].on = on, s_ws_pair[i].ticks += t.QuadPart - t0, s_ws_pair[i].n++;
    ReleaseSRWLockExclusive(&s_ws_lock);
}
