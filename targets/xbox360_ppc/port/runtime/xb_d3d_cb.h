/* xb_d3d.c, command buffers (one translation unit with it; here for the 400-line cap).
 *
 * Too Human records a command buffer on a worker thread (829ae698 under 829a6758, two buffers taken in turn) and its render thread runs
 * the other one (829ae2f0). The library writes GPU packets into the buffer between BeginCommandBuffer and EndCommandBuffer and the GPU
 * reads them when RunCommandBuffer jumps there. The kit has no packets: the device calls the recording thread makes in between are kept
 * with their arguments and made again, in order, on the thread that runs the buffer. What a call points at (a viewport, constants,
 * rects, a colour) is copied when it is recorded, as the packets would hold it. Calls that make, lock or read something back still run
 * at once: the game uses their answers while it records. */

typedef struct { u32 entry, snap, dc, dw[3], up[3]; TcArgs a; } CbCall;   /* dw: its colour mask, colour control and mode control words (XB_DEV_COLORMASK ..) */   /* dc: the device's depth-control word when it was recorded (XB_DEV_DEPTHCONTROL) */   /* snap: cb_snap's copy of the device's constants, or with cb_delta (XB_CB_DELTA) where its difference is kept, or 0; up: the guest callers as recorded (lib_up) */
#define CB_CHUNKS 16        /* a buffer's copies grow a chunk at a time, up to 16 (1 MB); the chunks are kept for its next recording */
#define CB_SNAPS 64
#define CB_SNAP_CHUNK 0x100000u   /* cb_snap's copies, a megabyte (113 draws) at a time */
#define CB_SNAP_AT 0x480u         /* cb_snap's copy: the device's fields from +0x480 (fetch constants) to +0x2780 (the last pixel shader constant) */
#define CB_SNAP_SIZE (0x2780u - 0x480u)
typedef struct { u32 cb, chunk[CB_CHUNKS], used; unsigned nchunk, cur; CbCall *call; unsigned n, cap; u32 snap[CB_SNAPS], snap_used, snap_last; unsigned nsnap, snap_cur; u32 baseg, dev; unsigned long tid; int recording; u8 touched[64]; unsigned running; int retire; u32 end_seq; u8 *dchunk[CB_SNAPS * 16], *dprev; unsigned dnchunk, dcur; u32 dused, dbytes, hdr0, last_use; } CbList;
/* baseg: cb_snap's baseline, the device's fields as recording began, in guest memory (a run's inherited constants, cb_inherit_from); dev and tid: the device
 * and host thread it was recorded on; touched: the constant registers its draws so far set (cb_snap); running: runs of it under way now (under s_cb_lock);
 * retire: the game freed its memory while it ran, given back when the last run ends; end_seq: its EndCommandBuffer's number (s_cb_end_n).
 * dchunk .. dbytes: cb_delta's store, host memory in 64 KB chunks (CB_DCHUNK, up to 1024), never moved while kept; dprev: the copy as of the last recorded
 * draw (cb_delta diffs against it); dbytes: the bytes this recording stored. hdr0: the guest word at the buffer as BeginCommandBuffer saw it; last_use:
 * s_cb_swaps at its last Begin or Run (cb_reclaim) */
#define CB_DCHUNK 0x10000u
#define CB_DCHUNKS (CB_SNAPS * 16)
#define CB_VECS ((CB_SNAP_SIZE + 64u) / 16u)   /* cb_delta's unit: 560 vectors of device fields and the 4 of the mask, 16 bytes each */
#define CB_IDLE_SWAPS 120u   /* cb_reclaim: a buffer neither recorded nor run for this many Swaps is looked at */
#define CB_HDR_SPAN 0x18u    /* cb_begin: a new buffer this close to a kept one's address means the kept one's memory was used again (a D3DResource
                              * header, Common to BaseFlush, is 0x18 bytes: two live buffers cannot be closer) */
#define CB_LISTS 256        /* Too Human made a 17th at frame 377 (295766f0) after the quote cards; each new one is logged */
#define CB_ARENA 0x10000u   /* one chunk of guest memory for a buffer's copies, taken when first needed; Too Human's first buffer is one
                             * 0x1080-byte segment of packets, its tenth (29085610) passed 64 KB at frame 346 */
static CbList s_cb[CB_LISTS];
static SRWLOCK s_cb_lock = SRWLOCK_INIT;
static __declspec(thread) CbList *t_rec;   /* the buffer this thread records, between its Begin and End */

/* The kit's chunks of guest memory for buffers' copies, pooled. xb_alloc never frees, and until 2026-11-13 every buffer kept the most it had ever needed
 * for good: Too Human's kit memory rose from 55 MB to 183 MB over vertical blanks 1000..1250 (the airport foyer's first frames, about 120 constant chunks
 * of a megabyte) and stayed there to its out-of-memory stop at 4847 (tx1112 census). A chunk goes back to the pool when a recording needs fewer than its
 * buffer holds (cb_end) or when the game frees the memory the buffer was in (xb_d3d_guest_freed), and is taken from the pool before xb_alloc is asked.
 * XB_CB_POOL=0: kept per buffer for good, as before */
#define CB_POOL_N 4096
static SRWLOCK s_cb_pool_lock = SRWLOCK_INIT;
static u32 s_cb_pool[2][CB_POOL_N];         /* [0] 64 KB chunks (cb_copy's, and baselines), [1] 1 MB chunks (cb_snap's) */
static unsigned s_cb_pool_n[2], s_cb_made[2], s_cb_pool_lost, s_cb_retired;   /* waiting in the pool; made by xb_alloc in all; dropped with the pool full; buffers retired */
static volatile LONG s_cb_swaps;   /* Swaps seen by cb_reclaim, the clock of last_use */
static unsigned s_cb_reclaimed, s_cb_idle_kept, s_cb_lost_runs;   /* buffers reclaimed (cb_reclaim, cb_begin); idle but kept at the last sweep; runs of a reclaimed one */
static u32 s_cb_gone[64];          /* the last 64 buffers reclaimed, so a run of one is logged (a false reclaim would show there) */
static unsigned s_cb_gone_n;

static int cb_pool_on(void)
{
    static int on = -1;
    if (on < 0) { const char *e = getenv("XB_CB_POOL"); on = !(e && *e == '0'); }
    return on;
}

/* XB_CB_DELTA=0: each recorded draw keeps a full copy of the device's constants (cb_snap), as before 2026-11-13; else only the 16-byte vectors that
 * differ from the draw before (cb_delta) */
static int cb_delta_on(void)
{
    static int on = -1;
    if (on < 0) { const char *e = getenv("XB_CB_DELTA"); on = !(e && *e == '0'); }
    return on;
}

/* XB_CB_RECLAIM=0: a buffer the game abandons keeps its lists for good; =idle: reclaimed on idleness alone (a diagnostic, not safe for a buffer run rarely);
 * else idle and its guest header changed or gone, or a new buffer begun on top of it (cb_reclaim, cb_begin) */
static int cb_reclaim_mode(void)
{
    static int m = -1;
    if (m < 0) { const char *e = getenv("XB_CB_RECLAIM"); m = e && *e == '0' ? 0 : e && !strcmp(e, "idle") ? 2 : 1; }
    return m;
}

static u32 cb_pool_get(int big)
{
    u32 at = 0;
    AcquireSRWLockExclusive(&s_cb_pool_lock);
    if (s_cb_pool_n[big]) at = s_cb_pool[big][--s_cb_pool_n[big]];
    else s_cb_made[big]++;
    ReleaseSRWLockExclusive(&s_cb_pool_lock);
    return at ? at : xb_alloc(big ? CB_SNAP_CHUNK : CB_ARENA);
}

static void cb_pool_put(int big, u32 at)
{
    if (!at) return;
    AcquireSRWLockExclusive(&s_cb_pool_lock);
    if (s_cb_pool_n[big] < CB_POOL_N) s_cb_pool[big][s_cb_pool_n[big]++] = at;
    else s_cb_pool_lost++;
    ReleaseSRWLockExclusive(&s_cb_pool_lock);
}

/* how: 0 a lookup, 1 BeginCommandBuffer (recording from now, under the lock, so a free on another thread sees it), 2 RunCommandBuffer (one more run
 * under way, under the lock, so a free waits for its end: cb_run_end) */
static CbList *cb_list(u32 cb, int how)
{
    CbList *l = NULL;
    unsigned i;
    AcquireSRWLockExclusive(&s_cb_lock);
    for (i = 0; i < CB_LISTS && !l; i++)
        if (s_cb[i].cb == cb) l = &s_cb[i];
    for (i = 0; i < CB_LISTS && !l; i++)
        if (!s_cb[i].cb) {
            s_cb[i].cb = cb, l = &s_cb[i];
            if (i < 32 || !(i & (i - 1))) port_log("d3d command buffer %08x: number %u the game uses\n", cb, i + 1);
            if (how == 2) {   /* run with no recording kept: one cb_reclaim or cb_begin gave back would be a false reclaim */
                unsigned k;
                for (k = 0; k < 64 && s_cb_gone[k] != cb; k++) {}
                if (k < 64 && s_cb_lost_runs++ < 8) port_log("d3d RunCommandBuffer %08x: its recording was reclaimed (a false reclaim; XB_CB_RECLAIM=0 keeps them)\n", cb);
            }
        }
    if (l) l->last_use = (u32)s_cb_swaps;
    if (l && how == 1) {
        static int said;
        if (l->running && said++ < 8) port_log("d3d BeginCommandBuffer %08x while the render thread still runs it (%u runs)\n", cb, l->running);
        l->recording = 1, l->retire = 0;
    }
    if (l && how == 2) l->running++;
    ReleaseSRWLockExclusive(&s_cb_lock);
    if (!l) xb_stop("d3d_cb", "more than 256 command buffers", cb);
    return l;
}

/* A buffer whose memory the game gave back: its chunks to the pool and its slot free. Under s_cb_lock, with no run and no recording of it */
static void cb_retire(CbList *l)
{
    unsigned i;
    for (i = 0; i < l->nchunk; i++) cb_pool_put(0, l->chunk[i]);
    for (i = 0; i < l->nsnap; i++) cb_pool_put(1, l->snap[i]);
    for (i = 0; i < l->dnchunk; i++) free(l->dchunk[i]);
    cb_pool_put(0, l->baseg);
    free(l->call);
    free(l->dprev);
    memset(l, 0, sizeof *l);
    s_cb_retired++;
}

/* A buffer the game abandoned (cb_reclaim, cb_begin): retired, and remembered so a later run of it is logged. Under s_cb_lock, with no run and no recording */
static void cb_reclaim_one(CbList *l)
{
    s_cb_gone[s_cb_gone_n++ & 63u] = l->cb;
    s_cb_reclaimed++;
    cb_retire(l);
}

/* The game gave back the guest block at..at+size (MmFreePhysicalMemory, NtFreeVirtualMemory's release, NtFreeEncryptedMemory: xb_imports_krnl.h). A
 * buffer in it is retired now, or when its last run ends (the render thread may be running it). Too Human's buffers are in its own heap (426ce200 and
 * the like, not block starts), so this sees them only when the heap gives a whole block back */
void xb_d3d_guest_freed(u32 at, u32 size)
{
    unsigned i;
    if (!size || !cb_pool_on()) return;
    AcquireSRWLockExclusive(&s_cb_lock);
    for (i = 0; i < CB_LISTS; i++) {
        CbList *l = &s_cb[i];
        if (!l->cb || l->cb - at >= size) continue;
        if (l->running || l->recording) l->retire = 1;
        else cb_retire(l);
    }
    ReleaseSRWLockExclusive(&s_cb_lock);
}

/* BeginCommandBuffer's bookkeeping: the counts back to the start (the chunks are kept for this recording) and the device's fields as recording starts,
 * cb_snap's baseline */
static CbList *cb_begin(u32 cb, u32 dev)
{
    CbList *l;
    if (cb_reclaim_mode() && cb_pool_on()) {   /* a buffer begun inside a kept one's header: that one's memory was used again, so the game abandoned it */
        unsigned i;
        AcquireSRWLockExclusive(&s_cb_lock);
        for (i = 0; i < CB_LISTS; i++) {
            CbList *o = &s_cb[i];
            if (!o->cb || o->cb == cb || (o->cb - cb >= CB_HDR_SPAN && cb - o->cb >= CB_HDR_SPAN)) continue;
            if (o->running || o->recording) o->retire = 1;
            else cb_reclaim_one(o);
        }
        ReleaseSRWLockExclusive(&s_cb_lock);
    }
    l = cb_list(cb, 1);
    InterlockedIncrement(&s_cb_recording);
    l->n = 0, l->used = 0, l->cur = 0, l->snap_used = 0, l->snap_cur = 0, l->snap_last = 0;
    if (!l->baseg) l->baseg = cb_pool_get(0);
    if (dev) memcpy(tc_base + l->baseg, tc_base + dev + CB_SNAP_AT, CB_SNAP_SIZE);
    else memset(tc_base + l->baseg, 0, CB_SNAP_SIZE);
    memset(l->touched, 0, sizeof l->touched);
    l->dev = dev, l->tid = GetCurrentThreadId();
    l->hdr0 = rd32(cb);
    if (cb_delta_on()) {   /* cb_delta's starting point: the baseline and an empty mask, as a run's scratch copy starts (cb_delta_start) */
        if (!l->dprev && !(l->dprev = (u8 *)malloc(CB_SNAP_SIZE + 64u))) xb_stop("d3d_cb", "out of host memory for a command buffer's constants", cb);
        memcpy(l->dprev, tc_base + l->baseg, CB_SNAP_SIZE);
        memset(l->dprev + CB_SNAP_SIZE, 0, 64);
        l->dcur = 0, l->dused = 0, l->dbytes = 0;
    }
    return l;
}

/* EndCommandBuffer's: the chunks this recording did not reach go back to the pool (its earlier recording's calls were dropped at its Begin, so nothing
 * points into them), and it is numbered for the deferred frees (xb_d3d_res.h res_drain) */
static void cb_end(CbList *l)
{
    AcquireSRWLockExclusive(&s_cb_lock);
    l->recording = 0;
    l->end_seq = (u32)++s_cb_end_n;
    if (cb_pool_on()) {
        unsigned keep = l->nchunk ? l->cur + 1 : 0;
        while (l->nchunk > keep) cb_pool_put(0, l->chunk[--l->nchunk]);
        keep = l->nsnap ? l->snap_cur + 1 : 0;
        while (l->nsnap > keep) cb_pool_put(1, l->snap[--l->nsnap]);
        keep = l->dnchunk ? l->dcur + 1 : 0;   /* host memory: freed only with no run of it under way (a run reads them in place) */
        while (!l->running && l->dnchunk > keep) free(l->dchunk[--l->dnchunk]);
    }
    if (l->retire && !l->running) cb_retire(l);
    ReleaseSRWLockExclusive(&s_cb_lock);
    InterlockedDecrement(&s_cb_recording);
}

/* A run's end, on the thread that ran it: the newest recording known to be run (buffers run in the order they are recorded), and a retire that waited */
static void cb_run_end(CbList *l)
{
    AcquireSRWLockExclusive(&s_cb_lock);
    if (l->running) l->running--;
    if ((LONG)l->end_seq > s_cb_run_done) s_cb_run_done = (LONG)l->end_seq;
    if (l->retire && !l->running && !l->recording) cb_retire(l);
    ReleaseSRWLockExclusive(&s_cb_lock);
}

/* XB_MEMLOG's line for the kit's Direct3D memory, at the same frames as xb_mem.c's census (Swap): what the buffers hold, the pools, the resources */
static void kit_census(unsigned frame)
{
    static int every = -1;
    unsigned i, lists = 0, arena = 0, snaps = 0, pool0, pool1, dchunks = 0, idle = 0;
    unsigned long long host = 0, dbytes = 0;
    if (every < 0) { const char *v = getenv("XB_MEMLOG"); every = v ? atoi(v) : 0; }
    if (every <= 0 || frame % (unsigned)every) return;
    AcquireSRWLockShared(&s_cb_lock);
    for (i = 0; i < CB_LISTS; i++) {
        const CbList *l = &s_cb[i];
        if (!l->cb) continue;
        lists++, arena += l->nchunk + (l->baseg != 0), snaps += l->nsnap, dchunks += l->dnchunk, dbytes += l->dbytes;
        host += (unsigned long long)l->cap * sizeof(CbCall) + (l->dprev ? CB_SNAP_SIZE + 64u : 0u);
        idle += (u32)s_cb_swaps - l->last_use >= CB_IDLE_SWAPS;
    }
    ReleaseSRWLockShared(&s_cb_lock);
    AcquireSRWLockShared(&s_cb_pool_lock);
    pool0 = s_cb_pool_n[0], pool1 = s_cb_pool_n[1];
    ReleaseSRWLockShared(&s_cb_pool_lock);
    port_log("memlog frame %u kit d3d: command buffers %u holding %u KB (%u 64 KB chunks, %u 1 MB), pool %u KB (%u + %u), made %u KB, retired %u, pool full %u%s; "
             "resources %u live, %u waiting for their runs, kit blocks out %u KB pooled %u KB\n", frame, lists, arena * 64u + snaps * 1024u, arena, snaps,
             pool0 * 64u + pool1 * 1024u, pool0, pool1, s_cb_made[0] * 64u + s_cb_made[1] * 1024u, s_cb_retired, s_cb_pool_lost, cb_pool_on() ? "" : " (XB_CB_POOL=0)",
             res_live(), s_defer_n, s_kit_out >> 10, s_kit_pooled >> 10);
    /* the lists' whole cost: guest (above) and host, the constant store (cb_delta) and what cb_reclaim did. Flat across level loads is the aim */
    port_log("memlog frame %u kit cb: lists %u, guest %u KB + pool %u KB, host %llu KB (calls and copies %llu KB, constant deltas %u KB in %u chunks), deltas stored "
             "this recording %llu KB%s; idle %u (kept at last sweep %u), reclaimed %u, runs of reclaimed %u%s\n", frame, lists, arena * 64u + snaps * 1024u,
             pool0 * 64u + pool1 * 1024u, (host >> 10) + dchunks * (CB_DCHUNK >> 10), host >> 10, dchunks * (CB_DCHUNK >> 10), dchunks, dbytes >> 10,
             cb_delta_on() ? "" : " (XB_CB_DELTA=0: full copies)", idle, s_cb_idle_kept, s_cb_reclaimed, s_cb_lost_runs,
             cb_reclaim_mode() == 0 ? " (XB_CB_RECLAIM=0)" : cb_reclaim_mode() == 2 ? " (XB_CB_RECLAIM=idle)" : "");
}

/* The calls that only record into the buffer: the library's device setters and draws the kit has cases for. A call with a case that
 * is not here runs at once (logged once each), so a new setter mapped later must be added */
static int cb_recorded(u32 target)
{
    switch (target) {
    case 0x821c9bf0: case 0x821c9428: case 0x821ca348: case 0x821ca070: case 0x821d0708: case 0x821d00a8: case 0x82761f00: case 0x821ccea0:
    case 0x821ccf78: case 0x821c88b8: case 0x821c8a60: case 0x821cc448: case 0x82765b00: case 0x821cd418: case 0x821cd248: case 0x821cd088:
    case 0x821d12f0: case 0x821d18f0: case 0x829fcfc0: case 0x821d24b0: case 0x82761d90: case 0x82758c98: case 0x821c9548: case 0x82746ed0:
        return 1;
    }
    return 0;
}

/* Copies what argument k points at into the buffer's arena and points the kept call there */
static void cb_copy(CbList *l, CbCall *c, unsigned k, u32 size)
{
    u32 src = (u32)c->a.r[k], i;
    if (!src || !size) return;
    size = (size + 3) & ~3u;
    if (size > CB_ARENA) xb_stop("d3d_cb", "one copy past a command buffer chunk's 64 KB", l->cb);
    if (!l->nchunk || l->used + size > CB_ARENA) {   /* the next chunk: kept from an earlier recording, or new */
        if (l->nchunk) l->cur++;
        if (l->cur == CB_CHUNKS) xb_stop("d3d_cb", "a command buffer's copies past 16 chunks (1 MB)", l->cb);
        if (l->cur == l->nchunk) l->chunk[l->nchunk++] = cb_pool_get(0);
        l->used = 0;
    }
    for (i = 0; i < size; i += 4) wr32(l->chunk[l->cur] + l->used + i, rd32(src + i));
    c->a.r[k] = l->chunk[l->cur] + l->used;
    l->used += size;
}

/* Too Human writes its fetch constants (+0x480) and its 256 vertex and 256 pixel shader constants (+0x780 to +0x2780) into the device inline while it
 * records, with no call the kit sees; the packets would carry them. Each recorded DrawIndexedVertices keeps a copy of that block and its run reads the
 * copy (t_snap). Read live, every draw of a run had the values the recorder held last and the menu's 3D drew nothing, even in flat colour (build box A,
 * 2026-11-06). A copy the same as the one before is shared. Answers the address the draw reads the device's fields from: the copy less 0x480.
 * The copy ends with a mask of the 512 constant registers that differ from the device at BeginCommandBuffer (cb_base): only those were set while
 * recording, and a run takes only those; the rest it inherits from the device it runs on, as the GPU's registers would hold them (Too Human's
 * ViewProjectionMatrix, c12, is never set while recording: copied whole, every 3D draw had a zero matrix and drew nothing).
 * The mask is every register any draw of the recording so far has set, not only those that differ at this draw: a register a draw sets and the game puts
 * back before the next draw is set again in the packets, so the next draw takes its copy's value too. With the difference alone, a later draw whose
 * register was back at the baseline kept an earlier draw's value on the host. XB_SNAP_MASK=diff gives the mask as before (2026-11-13) */
static u32 cb_snap(CbList *l, u32 dev)
{
    u32 at;
    if (!dev) return 0;
    if (l->snap_last && !memcmp(tc_base + l->snap_last, tc_base + dev + CB_SNAP_AT, CB_SNAP_SIZE)) return l->snap_last - CB_SNAP_AT;
    if (!l->nsnap || l->snap_used + CB_SNAP_SIZE + 64u > CB_SNAP_CHUNK) {   /* the next chunk: kept from an earlier recording, or new */
        if (l->nsnap) l->snap_cur++;
        if (l->snap_cur == CB_SNAPS) xb_stop("d3d_cb", "a command buffer's constant copies past 64 chunks (64 MB)", l->cb);
        if (l->snap_cur == l->nsnap) l->snap[l->nsnap++] = cb_pool_get(1);
        l->snap_used = 0;
    }
    at = l->snap[l->snap_cur] + l->snap_used;
    memcpy(tc_base + at, tc_base + dev + CB_SNAP_AT, CB_SNAP_SIZE);
    {
        static int diff_only = -1;
        u8 *mask = tc_base + at + CB_SNAP_SIZE;
        unsigned r;
        if (diff_only < 0) { const char *e = getenv("XB_SNAP_MASK"); diff_only = e && !strcmp(e, "diff"); }
        memset(mask, 0, 64);
        for (r = 0; r < 512; r++)
            if (!l->baseg || memcmp(tc_base + at + 0x300u + 16u * r, tc_base + l->baseg + 0x300u + 16u * r, 16)) mask[r >> 3] |= (u8)(1u << (r & 7));
        if (!diff_only)
            for (r = 0; r < 64; r++) l->touched[r] |= mask[r], mask[r] = l->touched[r];
    }
    l->snap_used += CB_SNAP_SIZE + 64u, l->snap_last = at;
    return at - CB_SNAP_AT;
}

/* cb_snap's copy stored as a difference: the 16-byte vectors of the copy (the device's fields +0x480..+0x2780, then the 64-byte mask) that differ from
 * the copy the draw before had (dprev; at the first draw, the baseline and an empty mask). A full copy was about 9 KB a draw and Too Human's kit memory
 * rose by 358 MB of them to its out-of-memory stop (tx1112 census, 2026-11-13). The copy is made and compared, not built from the setters the kit
 * sees: the game writes most constants into the device inline, so a diff against the previous copy is exact whatever wrote them. The mask is worked
 * out as cb_snap does (XB_SNAP_MASK=diff too). A run rebuilds each draw's copy in order (cb_delta_start, cb_delta_apply), byte for byte cb_snap's.
 * Stored: a word n, then n of (vector number, 16 bytes), in host memory. Answers (chunk << 16) | offset | 1, or 0 with no device */
static u32 cb_delta(CbList *l, u32 dev)
{
    static __declspec(thread) u8 cur[CB_SNAP_SIZE + 64u];
    static int diff_only = -1;
    u8 *mask = cur + CB_SNAP_SIZE, *rec;
    u32 n = 0, r, need, k, at;
    if (!dev) return 0;
    if (diff_only < 0) { const char *e = getenv("XB_SNAP_MASK"); diff_only = e && !strcmp(e, "diff"); }
    memcpy(cur, tc_base + dev + CB_SNAP_AT, CB_SNAP_SIZE);   /* once: the recorder may write the device inline, but not during this call */
    memset(mask, 0, 64);
    for (r = 0; r < 512; r++)
        if (!l->baseg || memcmp(cur + 0x300u + 16u * r, tc_base + l->baseg + 0x300u + 16u * r, 16)) mask[r >> 3] |= (u8)(1u << (r & 7));
    if (!diff_only)
        for (r = 0; r < 64; r++) l->touched[r] |= mask[r], mask[r] = l->touched[r];
    for (r = 0; r < CB_VECS; r++) n += memcmp(cur + 16u * r, l->dprev + 16u * r, 16) != 0;
    need = 4u + 20u * n;
    if (!l->dnchunk || l->dused + need > CB_DCHUNK) {   /* the next chunk: kept from an earlier recording, or new */
        if (l->dnchunk) l->dcur++;
        if (l->dcur == CB_DCHUNKS) xb_stop("d3d_cb", "a command buffer's constant deltas past 1024 chunks (64 MB)", l->cb);
        if (l->dcur == l->dnchunk) {
            if (!(l->dchunk[l->dnchunk] = (u8 *)malloc(CB_DCHUNK))) xb_stop("d3d_cb", "out of host memory for a command buffer's constants", l->cb);
            l->dnchunk++;
        }
        l->dused = 0;
    }
    rec = l->dchunk[l->dcur] + l->dused;
    memcpy(rec, &n, 4);
    for (r = 0, k = 4; r < CB_VECS; r++)
        if (memcmp(cur + 16u * r, l->dprev + 16u * r, 16)) {
            memcpy(rec + k, &r, 4), memcpy(rec + k + 4, cur + 16u * r, 16), k += 20;
            memcpy(l->dprev + 16u * r, cur + 16u * r, 16);
        }
    at = (l->dcur << 16) | l->dused | 1u;
    l->dused += need, l->dbytes += need;
    return at;
}

/* A run's copy before its first draw: the buffer's baseline and an empty mask, what dprev held at BeginCommandBuffer. In guest memory (scratch, the
 * size of a cb_snap copy), since the draw reads it as a device's fields */
static void cb_delta_start(const CbList *l, u32 scratch)
{
    memcpy(tc_base + scratch, tc_base + l->baseg, CB_SNAP_SIZE);
    memset(tc_base + scratch + CB_SNAP_SIZE, 0, 64);
}

/* A recorded draw's difference (cb_delta's answer) into the run's copy: after it the copy is the one cb_snap would have kept for that draw */
static void cb_delta_apply(const CbList *l, u32 at, u32 scratch)
{
    const u8 *rec = l->dchunk[at >> 16] + (at & 0xfffcu);
    u32 n, k, r;
    memcpy(&n, rec, 4);
    for (k = 0; k < n; k++) {
        memcpy(&r, rec + 4u + 20u * k, 4);
        memcpy(tc_base + scratch + 16u * r, rec + 8u + 20u * k, 16);
    }
}

static int cb_record(u32 entry, u32 target, const TcArgs *a, u64 *r3)
{
    CbList *l = t_rec;
    CbCall *c;
    int state = xb_state_kind(target, entry);   /* 0 not a state call, 1 a setter, 2 a getter */
    if (target == 0x829fc210 || target == 0x821d0e58 || target == 0x821d0e48)
        xb_stop("d3d_cb", "BeginVertices while a command buffer records: its vertices are written after the call returns", entry);
    if (state == 2 || (!state && !cb_recorded(target))) {
        static u32 said[32];
        unsigned i;
        if (!target && !state) return 0;   /* not a call the kit has: it stops where it is */
        for (i = 0; i < 32 && said[i] && said[i] != entry; i++) {}
        if (i < 32 && !said[i]) {
            said[i] = entry;
            D3D_LOG("d3d command buffer %08x: %08x (%08x) made at once while it records\n", l->cb, entry, target);
        }
        return 0;
    }
    if (l->n == l->cap) {
        l->cap = l->cap ? l->cap * 2 : 256;
        l->call = (CbCall *)realloc(l->call, l->cap * sizeof *l->call);
        if (!l->call) xb_stop("d3d_cb", "out of host memory for a command buffer", l->cb);
    }
    c = &l->call[l->n++];
    c->entry = entry;
    c->a = *a;
    c->up[0] = xb_caller(1), c->up[1] = xb_caller(2), c->up[2] = xb_caller(3);
    /* every draw kind keeps its copy: a replayed draw reads its fetch constants (textures, streams) and constants from it, never from the device the
     * recorder is changing (DrawVertices read them live until 2026-11-13). With cb_delta (XB_CB_DELTA, on) it keeps the difference from the draw before */
    c->snap = cb_delta_on() && (target == 0x829fcfc0 || target == 0x821d18f0 || target == 0x821d12f0) ? cb_delta(l, (u32)a->r[0])
            : target == 0x829fcfc0 || target == 0x821d18f0 || target == 0x821d12f0 ? cb_snap(l, (u32)a->r[0]) : 0;
#ifdef XB_DEV_DEPTHCONTROL
    c->dc = a->r[0] ? rd32((u32)a->r[0] + XB_DEV_DEPTHCONTROL) : 0;
#endif
#ifdef XB_DEV_COLORMASK
    c->dw[0] = a->r[0] ? rd32((u32)a->r[0] + XB_DEV_COLORMASK) : 0xffffu;
    c->dw[1] = a->r[0] ? rd32((u32)a->r[0] + XB_DEV_COLORCONTROL) : 7u;
    c->dw[2] = a->r[0] ? rd32((u32)a->r[0] + XB_DEV_MODECONTROL) : 0;
#endif
    if (state == 1) xb_state_recorded(target, entry, a);   /* the game's next getter sees it, as the library's copy would */
    switch (target) {
    case 0x821c9bf0:   /* SetViewport: D3DVIEWPORT9. The device's six floats now, as the library writes them while it records: Too Human's full-screen
                        * passes (82990808) record a target and viewport, then size their quads from the device's fields. Written only at the run, they
                        * held the 320x180 of the last pass run on the render thread, and the final copy and the interface came out in a 320x180 corner
                        * of the 1280x720 frame (build box A, 2026-11-06) */
        if (a->r[0] && a->r[1]) {
            u32 v = (u32)a->r[1], z0 = rd32(v + 16), z1 = rd32(v + 20);
            float f[6] = {(float)rd32(v), (float)rd32(v + 4), (float)rd32(v + 8), (float)rd32(v + 12), 0, 0};
            memcpy(&f[4], &z0, 4), memcpy(&f[5], &z1, 4);
            view_to_device((u32)a->r[0], f);
        }
        cb_copy(l, c, 1, 24); break;
    case 0x821ccea0: case 0x821ccf78:   /* shader constants, count vectors: stored in the device now as the library does, so the next draw's copy (cb_snap) holds them */
        if ((u32)a->r[1] + (u32)a->r[3] <= 256u && a->r[0] && a->r[2])
            memcpy(tc_base + (u32)a->r[0] + 16u * ((target == 0x821ccf78 ? 376u : 120u) + (u32)a->r[1]), tc_base + (u32)a->r[2], (u32)a->r[3] * 16u);
        cb_copy(l, c, 2, (u32)a->r[3] * 16); break;
    case 0x82765b00: cb_copy(l, c, 2, (u32)a->r[1] * 16); break;                         /* Clear: count rects */
    case 0x82761f00: cb_copy(l, c, 3, (u32)a->r[2] * 16); cb_copy(l, c, 4, 16); break;   /* BeginTiling: count rects, colour */
    case 0x821d00a8: cb_copy(l, c, 3, (u32)a->r[2] * 16); cb_copy(l, c, 4, 16); break;
    case 0x821d0708: cb_copy(l, c, 2, 64); cb_copy(l, c, 4, 16); break;                  /* EndTiling: rects (up to 4 tiles), colour */
    case 0x821d24b0: cb_copy(l, c, 2, 16); cb_copy(l, c, 4, 8); cb_copy(l, c, 7, 16); break;   /* Resolve: rect, point, colour */
    case 0x821d12f0: cb_copy(l, c, 3, (u32)a->r[2] * (u32)a->r[4]); break;               /* DrawVerticesUP: count vertices of stride */
    /* SetStreamSource and SetIndices: the buffer's header (data at +0x18, size at +0x1c), as the packets hold its addresses. Too Human frees headers once
     * recorded: a run while the town loaded read one given back (build box A, 2026-11-06, frames 504 and 514) */
    case 0x821c9428: cb_copy(l, c, 2, 32); break;
    case 0x821ca348:   /* SetRenderTarget: a header the game made, as described now (the host picks its surface by it at the run) */
        if (a->r[2] && !find_res((u32)a->r[2])) {
            u32 w, h, type = header_decode((u32)a->r[2], &w, &h);
            c->a.r[5] = CB_RT_MARK, c->a.r[6] = w, c->a.r[7] = (type << 16) | (h & 0xFFFF);
            c->a.r[4] = rd32((u32)a->r[2] + 0x1c);   /* its colour info, with the target's exponent bias (th_target_bias) */
            c->a.s[7] = rd32((u32)a->r[2] + 0x18);   /* its surface info, pitch and multisampling (XB_EDRAM, xb_d3d_edram.h) */
        }
        break;
    case 0x821c9548: cb_copy(l, c, 1, 32); break;
    }
    *r3 = 0;
    return 1;
}

/* Where a run takes the constant registers its draws did not set (cb_snap's mask): on the device it was recorded on, the buffer's copy of that device's
 * fields as its recording began (baseg). The device itself is the recorder's, which the game changes inline while it records the next buffer on
 * another thread: read at the run, the inherited registers were whatever the next frame's recording held at that moment. Run on another device, that
 * device's fields, as before. XB_REPLAY_INHERIT=live: the running device's fields always (before 2026-11-13) */
static u32 cb_inherit_from(const CbList *l, u32 run_dev)
{
    static int live = -1;
    if (live < 0) { const char *e = getenv("XB_REPLAY_INHERIT"); live = e && !strcmp(e, "live"); }
    return !live && l->baseg && run_dev && run_dev == l->dev ? l->baseg - CB_SNAP_AT : run_dev;
}

/* A diagnostic line for the first 6 runs and every 2000th: which device and thread recorded the buffer and which runs it, whether another thread is
 * recording on that device now, and how many of the 512 constant registers differ between the buffer's baseline and the device at the run */
static void cb_run_diag(const CbList *l, u32 run_dev)
{
    static LONG runs;
    LONG k = InterlockedIncrement(&runs);
    unsigned i, r, ndiff = 0, vp = 0, other = 0, basec12 = 0;
    if (k > 6 && k % 2000) return;
    AcquireSRWLockShared(&s_cb_lock);
    for (i = 0; i < CB_LISTS; i++)
        if (&s_cb[i] != l && s_cb[i].recording && s_cb[i].dev == run_dev) other++;
    ReleaseSRWLockShared(&s_cb_lock);
    if (l->baseg && run_dev)
        for (r = 0; r < 512; r++)
            if (memcmp(tc_base + l->baseg + 0x300u + 16u * r, tc_base + run_dev + 0x780u + 16u * r, 16)) ndiff++, vp += r >= 12 && r < 16;
    for (r = 0; l->baseg && r < 16; r++) basec12 |= rd32(l->baseg + 0x300u + 16u * 12u + 4u * r);
    port_log("d3d run %ld of %08x: recorded on device %08x thread %lu, run on %08x thread %lu; %u other buffers recording on it now; %u of 512 constant registers "
             "differ between its baseline and the device (vertex c12..c15: %u), baseline c12..c15 %s; inherited from %s\n", (long)k, l->cb, l->dev, l->tid, run_dev,
             GetCurrentThreadId(), other, ndiff, vp, basec12 ? "set" : "zero", cb_inherit_from(l, run_dev) == run_dev ? "the device" : "the baseline");
}

/* At each Swap (xb_d3d_draw.h), on the render thread: buffers the game abandoned give their lists back. Too Human makes new buffers in its own heap at
 * each level load (about 13 a load window, tx1112 log) and frees the old ones without a whole block going back (xb_d3d_guest_freed never sees them), so
 * each kept its lists for good. A buffer is reclaimed when it has been neither recorded nor run for CB_IDLE_SWAPS Swaps and the guest word at its address
 * (the D3DResource Common word) is no longer what BeginCommandBuffer saw, or its memory is gone: its memory holds something else, so the game cannot run it.
 * A buffer recorded once and run every frame is never idle; one idle but untouched is kept (counted, "kept at last sweep"); a run of a reclaimed buffer is
 * logged (cb_list). Every 16 Swaps; under s_cb_lock, so no Begin or Run of it is under way (both take the lock and stamp last_use) */
static void cb_reclaim(void)
{
    unsigned i, kept = 0;
    int mode = cb_reclaim_mode();
    u32 now = (u32)InterlockedIncrement(&s_cb_swaps);
    if (!mode || !cb_pool_on() || now % 16u) return;
    AcquireSRWLockExclusive(&s_cb_lock);
    for (i = 0; i < CB_LISTS; i++) {
        CbList *l = &s_cb[i];
        if (!l->cb || l->running || l->recording || now - l->last_use < CB_IDLE_SWAPS) continue;
        if (mode == 2 || !guest_readable(l->cb, 4) || rd32(l->cb) != l->hdr0) cb_reclaim_one(l);
        else kept++;
    }
    s_cb_idle_kept = kept;
    ReleaseSRWLockExclusive(&s_cb_lock);
}

/* BeginCommandBuffer(device, buffer, flags, inherited tags, persistent tags, tiling rects, rect count): 82743190, called by 829ae698.
 * EndCommandBuffer(device): 82743650, by 829ae728. RunCommandBuffer(device, buffer, ...): 82743a40, by 829ae2f0. The tag collections
 * (five doublewords each) say which device states the buffer takes from the device it runs on; the host keeps one set of states, so
 * every recorded setter is simply made again */
static int cb_entry(u32 target, const TcArgs *a, u64 *r3)
{
    CbList *l;
    unsigned i;
    u32 scratch = 0;
    *r3 = 0;
    if (target == 0x82743190) {
        static int said;
        if (t_rec) xb_stop("d3d_cb", "BeginCommandBuffer while this thread records another", t_rec->cb);
        l = cb_begin((u32)a->r[1], (u32)a->r[0]);
        t_rec = l;
        if (!said++) D3D_LOG("d3d BeginCommandBuffer %08x flags %x: the calls up to EndCommandBuffer are kept for RunCommandBuffer\n", l->cb, (u32)a->r[2]);
        return 1;
    }
    if (target == 0x82743650) {
        if (!t_rec) xb_stop("d3d_cb", "EndCommandBuffer with no buffer recording on this thread", 0);
        l = t_rec;
        t_rec = NULL;
        cb_end(l);
        return 1;
    }
    l = cb_list((u32)a->r[1], 2);
    if (l == t_rec) xb_stop("d3d_cb", "RunCommandBuffer on the buffer this thread records", l->cb);
    {   /* the calls of the first few runs, a diagnostic for state a buffer leaves behind */
        static int runs;
        if (runs < 4) {
            char line[1024];
            int at = 0;
            runs++;
            for (i = 0; i < l->n && at < (int)sizeof line - 12; i++) at += sprintf(line + at, " %08x", l->call[i].entry);
            line[at] = 0;
            port_log("d3d RunCommandBuffer %08x on host thread %lu, %u calls:%s\n", l->cb, GetCurrentThreadId(), l->n, line);
        }
    }
    cb_run_diag(l, (u32)a->r[0]);
    if (s_fetch_dev && a->r[0]) {   /* the registers a run inherits (cb_snap): cb_inherit_from, never the fields another thread is recording into */
        s_fetch_dev = cb_inherit_from(l, (u32)a->r[0]), s_snap_mask = 0;
        constants_from_device();
        s_fetch_dev = (u32)a->r[0];
    }
    xb_state_replaying(1);
    if (cb_delta_on() && l->dnchunk) {   /* the run's copy of the constants, rebuilt draw by draw from the deltas (cb_delta): this thread's, one per depth */
        static __declspec(thread) u32 scratch_at[4];
        if (t_replay >= 4) xb_stop("d3d_cb", "command buffer runs nested past 4", l->cb);
        if (!scratch_at[t_replay]) scratch_at[t_replay] = cb_pool_get(0);
        scratch = scratch_at[t_replay];
        cb_delta_start(l, scratch);
    }
    t_replay++;
    for (i = 0; i < l->n; i++) {
        TcArgs c = l->call[i].a;
        u64 r = 0;
        f64 f = 0.0;
        u32 snap = l->call[i].snap;
        c.sp[0] = a->sp[0], c.r12[0] = a->r12[0];   /* the running thread's frame: InsertCallback calls guest code below it */
        memcpy(c.nv, a->nv, sizeof c.nv);
        s_cb_run = l->cb, s_cb_at = i, s_cb_n = l->n, s_cb_at_entry = l->call[i].entry;
        if (snap && scratch) cb_delta_apply(l, snap, scratch), snap = scratch - CB_SNAP_AT;   /* this draw's copy, as cb_snap would have kept it */
        t_snap = snap, t_up = l->call[i].up, t_dc = l->call[i].dc, t_dc_on = 1, memcpy(t_dw, l->call[i].dw, sizeof t_dw);
        if (!xb_lib_entry(l->call[i].entry, &c, &r, &f)) xb_stop("d3d_cb", "a kept command buffer call has no case", l->call[i].entry);
        if (t_snap) s_fetch_dev = (u32)c.r[0], s_snap_mask = 0;   /* a later draw outside the run reads the device again, not this copy */
    }
    t_replay--;
    t_snap = 0, t_up = NULL, t_dc_on = 0;
    s_cb_run = 0;
    xb_state_replaying(0);
    cb_run_end(l);
    res_drain();   /* resources released before this run was recorded, now nothing recorded points at them (xb_d3d_res.h) */
    return 1;
}
