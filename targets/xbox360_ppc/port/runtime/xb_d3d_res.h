/* xb_d3d.c, the entries that make and bind things (one translation unit with it, for its state; here for the 400-line cap) */

/* A device's command ring with no GPU behind it: translated library code (kept.json) writes packets at +0x30 (the last word written) up to +0x38, and nothing reads them */
#define RING_BYTES 0x10000u
static u32 s_ring;
/* The device's fences with no GPU behind it. +0x2a9c is the next fence: the ring setup the kit skips (82746f28) starts it at 3 (827471e4), and each kick
 * (827465b0, under 82746938) writes it into the ring and moves it on. +0x2a90 points at the completed value the GPU writes back. The render thread waits on
 * these before it lets a package go: its release list (8296d08c) takes a fence from +0x2a9c and treats 0 as "none yet", so with the counter left at 0 it
 * never posted CNotificationMessage_ResourcesReleased (82970288), the unload's hold stayed set and the loader's state 3 waited for ever at the quote card */
static u32 s_fence_done;
static void fence_kick(u32 dev)
{
    u32 f = rd32(dev + 0x2a9cu);
    wr32(s_fence_done, f);           /* the host draws in order: the fence is passed as soon as it is written */
    wr32(dev + 0x2a9cu, f + 1);
}

/* XB_GPU_CB_DEFER=1: InsertCallback's callback waits for the next vertical blank and runs on that thread (xb_gpu_callbacks_run), as the
 * console's GPU interrupt runs it some time after the call, on another thread, instead of at once below the caller. Off by default: a test of
 * whether running it at once is behind Too Human's guest faults (82c392b8, 82a49130) and its missing title scene. Queued in order; when the
 * queue is full the callback runs at once as before */
static SRWLOCK s_gcb_lock = SRWLOCK_INIT;
static struct { u32 fn, ctx; } s_gcb[256];
static unsigned s_gcb_in, s_gcb_out;
static int s_gcb_defer = -1;

static int gcb_defer(void)
{
    if (s_gcb_defer < 0) { const char *e = getenv("XB_GPU_CB_DEFER"); s_gcb_defer = e && *e == '1'; }
    return s_gcb_defer;
}

static int gcb_queue(u32 fn, u32 ctx)
{
    int ok;
    AcquireSRWLockExclusive(&s_gcb_lock);
    ok = s_gcb_in - s_gcb_out < 256u;
    if (ok) s_gcb[s_gcb_in & 255u].fn = fn, s_gcb[s_gcb_in & 255u].ctx = ctx, s_gcb_in++;
    ReleaseSRWLockExclusive(&s_gcb_lock);
    if (!ok) { static volatile long said; if (!InterlockedExchange(&said, 1)) port_log("d3d InsertCallback: 256 callbacks waiting, this one called at once\n"); }
    return ok;
}

/* The last 32 GPU callbacks, for a guest fault's report (xb_mem.c): when each was inserted (frame, host thread, the guest callers), its
 * callback and context, the context's first 8 words then, and whether it has run. A thread running one says so (t_gcb_in). The question
 * it answers: does a fault come under a callback, and had the callback's context changed (been written) since it was inserted */
static struct { u32 frame, tid, fn, ctx, caller[3], w[8], state; } s_gcb_ring[32];
static volatile long s_gcb_ring_n;
static __declspec(thread) u32 t_gcb_in;

static unsigned gcb_note(u32 fn, u32 ctx)
{
    unsigned k = (unsigned)InterlockedIncrement(&s_gcb_ring_n) - 1u, j = k & 31u;
    s_gcb_ring[j].frame = s_frame, s_gcb_ring[j].tid = GetCurrentThreadId(), s_gcb_ring[j].fn = fn, s_gcb_ring[j].ctx = ctx;
    s_gcb_ring[j].caller[0] = xb_caller(0), s_gcb_ring[j].caller[1] = xb_caller(1), s_gcb_ring[j].caller[2] = xb_caller(2);
    for (k = 0; k < 8; k++) s_gcb_ring[j].w[k] = ctx && guest_readable(ctx + 4 * k, 4) ? rd32(ctx + 4 * k) : 0;
    s_gcb_ring[j].state = 0;
    return j;
}

void xb_gcb_report(void)
{
    long n = s_gcb_ring_n, i;
    char st[160];
    xb_stack_line(st, sizeof st);
    port_log("fault context: host thread %lu, guest thread block %08x, frame %u, %s; %ld callbacks inserted in all; stack:%s\n", GetCurrentThreadId(), tc_r13, s_frame,
             t_gcb_in ? "inside a GPU callback" : "not inside a GPU callback", n, st);
    if (t_gcb_in) port_log("fault context: the callback running here is ring entry %u (callback %08x context %08x)\n", t_gcb_in - 1, s_gcb_ring[t_gcb_in - 1].fn, s_gcb_ring[t_gcb_in - 1].ctx);
    for (i = n > 12 ? n - 12 : 0; i < n; i++) {
        unsigned j = (unsigned)i & 31u, k, changed = 0;
        u32 c = s_gcb_ring[j].ctx;
        for (k = 0; k < 8; k++) changed |= (c && guest_readable(c + 4 * k, 4) ? rd32(c + 4 * k) : 0) != s_gcb_ring[j].w[k] ? 1u << k : 0;
        port_log("fault context: callback %ld (entry %u) frame %u, host thread %lu, callback %08x context %08x, callers %08x %08x %08x, %s, context then %08x %08x %08x %08x %08x %08x %08x %08x, words changed since %02x\n",
                 i, j, s_gcb_ring[j].frame, s_gcb_ring[j].tid, s_gcb_ring[j].fn, c, s_gcb_ring[j].caller[0], s_gcb_ring[j].caller[1], s_gcb_ring[j].caller[2],
                 s_gcb_ring[j].state == 2 ? "ran" : s_gcb_ring[j].state == 1 ? "running" : "not run yet", s_gcb_ring[j].w[0], s_gcb_ring[j].w[1], s_gcb_ring[j].w[2],
                 s_gcb_ring[j].w[3], s_gcb_ring[j].w[4], s_gcb_ring[j].w[5], s_gcb_ring[j].w[6], s_gcb_ring[j].w[7], changed);
    }
}

void xb_gpu_callbacks_run(const TcArgs *a)
{
    unsigned end;
    AcquireSRWLockExclusive(&s_gcb_lock);
    end = s_gcb_in;   /* those queued before this blank: one a callback queues waits for the next */
    ReleaseSRWLockExclusive(&s_gcb_lock);
    for (;;) {
        u32 fn, ctx;
        TcArgs c = *a;
        AcquireSRWLockExclusive(&s_gcb_lock);
        if (s_gcb_out == end) { ReleaseSRWLockExclusive(&s_gcb_lock); return; }
        fn = s_gcb[s_gcb_out & 255u].fn, ctx = s_gcb[s_gcb_out & 255u].ctx, s_gcb_out++;
        ReleaseSRWLockExclusive(&s_gcb_lock);
        c.r[0] = ctx;
        (void)tc_call(fn, &c);
    }
}

/* The kit's own blocks of guest memory for resources, pooled: xb_alloc's memory is never freed and its grain is 64 KB, so each 0x40-byte header and each
 * small vertex buffer took 64 KB for good. Up to 32 KB: powers of two from 64 bytes, cut from 64 KB blocks, their free pieces linked through their first
 * word. Larger: whole 64 KB multiples, reused at the same size. Handed out zeroed, as xb_alloc's are */
#define KIT_CLASSES 10       /* 64 B .. 32 KB */
#define KIT_BIG_N 1024
static SRWLOCK s_kit_lock = SRWLOCK_INIT;
static u32 s_kit_free[KIT_CLASSES];
static struct { u32 at, size; } s_kit_big[KIT_BIG_N];
static unsigned s_kit_big_n;
static u32 s_kit_out, s_kit_pooled;   /* bytes handed out now, bytes waiting in the pool (kit_census) */

static u32 kit_size(u32 size, unsigned *cls)
{
    u32 s = 64;
    unsigned c = 0;
    if (size > 0x8000u) return *cls = KIT_CLASSES, (size + 0xffffu) & ~0xffffu;
    while (s < size) s <<= 1, c++;
    *cls = c;
    return s;
}

static u32 kit_get(u32 size)
{
    unsigned c, i;
    u32 cs = kit_size(size ? size : 4, &c), at = 0;
    AcquireSRWLockExclusive(&s_kit_lock);
    if (c < KIT_CLASSES) {
        if (!s_kit_free[c]) {   /* a new 64 KB block, cut into this class's pieces */
            u32 b = xb_alloc(0x10000u), k = 0x10000u;
            while (k) k -= cs, wr32(b + k, s_kit_free[c]), s_kit_free[c] = b + k;
            s_kit_pooled += 0x10000u;
        }
        at = s_kit_free[c], s_kit_free[c] = rd32(at);
        s_kit_pooled -= cs;
    } else {
        for (i = 0; i < s_kit_big_n && !at; i++)
            if (s_kit_big[i].size == cs) at = s_kit_big[i].at, s_kit_big[i] = s_kit_big[--s_kit_big_n], s_kit_pooled -= cs;
        if (!at) at = xb_alloc(cs);
    }
    s_kit_out += cs;
    ReleaseSRWLockExclusive(&s_kit_lock);
    memset(tc_base + at, 0, cs);
    return at;
}

static void kit_put(u32 at, u32 size)
{
    unsigned c;
    u32 cs = kit_size(size ? size : 4, &c);
    if (!at) return;
    AcquireSRWLockExclusive(&s_kit_lock);
    s_kit_out -= cs;
    if (c < KIT_CLASSES) wr32(at, s_kit_free[c]), s_kit_free[c] = at, s_kit_pooled += cs;
    else if (s_kit_big_n < KIT_BIG_N) s_kit_big[s_kit_big_n].at = at, s_kit_big[s_kit_big_n].size = cs, s_kit_big_n++, s_kit_pooled += cs;
    ReleaseSRWLockExclusive(&s_kit_lock);
}

/* A kit resource released to zero is given back only once nothing can still reach it. The game records command buffers on one thread and the render thread
 * runs them later, so a call recorded before the Release (a SetStreamSource's header copy points at the data) may not have run yet. Each release waits for
 * two Swaps and for the run of every recording ended by then plus the one under way (s_cb_end_n, s_cb_recording; buffers run in the order they are
 * recorded, s_cb_run_done), drained at the end of a run and at Swap, on the render thread. A release whose buffers never run is given back after 600 Swaps.
 * XB_RES_FREE=0: kit resources are kept for good, as before */
typedef struct { u32 h, data, len, frame, need; } KitDefer;
static KitDefer s_defer[2048];
static unsigned s_defer_n;
static volatile LONG s_cb_end_n, s_cb_run_done, s_cb_recording;   /* EndCommandBuffers made; the newest recording run to its end; recordings under way (xb_d3d_cb.h) */

static int res_free_on(void)
{
    static int on = -1;
    if (on < 0) { const char *e = getenv("XB_RES_FREE"); on = !(e && *e == '0'); }
    return on;
}

static void res_defer(XbRes *r, u32 data, u32 len)
{
    static int said;
    AcquireSRWLockExclusive(&s_kit_lock);
    if (s_defer_n < sizeof s_defer / sizeof *s_defer) {
        KitDefer *d = &s_defer[s_defer_n++];
        d->h = r->guest, d->data = data, d->len = len, d->frame = s_frame, d->need = (u32)s_cb_end_n + (s_cb_recording ? 1u : 0u);
        r->dying = 1;
    } else if (!said++)
        port_log("d3d Release: 2048 resources waiting to be given back, %08x kept\n", r->guest);
    ReleaseSRWLockExclusive(&s_kit_lock);
}

static void res_drain(void)
{
    unsigned i = 0;
    for (;;) {
        KitDefer d;
        XbRes *r;
        int late;
        AcquireSRWLockExclusive(&s_kit_lock);
        for (; i < s_defer_n; i++)
            if (s_frame >= s_defer[i].frame + 2u && ((u32)s_cb_run_done >= s_defer[i].need || s_frame >= s_defer[i].frame + 600u)) break;
        if (i == s_defer_n) { ReleaseSRWLockExclusive(&s_kit_lock); return; }
        d = s_defer[i], s_defer[i] = s_defer[--s_defer_n];
        ReleaseSRWLockExclusive(&s_kit_lock);
        late = (u32)s_cb_run_done < d.need;
        if (late) { static int said; if (!said++) port_log("d3d Release of %08x: given back after 600 Swaps with recording %u not run\n", d.h, d.need); }
        AcquireSRWLockExclusive(&s_res_lock);
        if ((r = find_res(d.h)) != 0) r->guest = 0, MemoryBarrier(), r->kind = 0;
        ReleaseSRWLockExclusive(&s_res_lock);
        kit_put(d.h, 0x40);
        kit_put(d.data, d.len);
    }
}

static unsigned res_live(void)
{
    unsigned i, n = 0;
    for (i = 0; i < s_res_n; i++) n += s_res[i].guest != 0;
    return n;
}

static int entry_res(u32 target, const TcArgs *a, u64 *r3)
{
    (void)a;
    switch (target) {
    case 0x827577d0:     /* A device's start (device, D3DPRESENT_PARAMETERS *): Too Human, first stop 829a47d8, on its render thread 8296cea8. The game has CreateDevice's work inlined: it allocates
                          * the device itself, 0x5e80 bytes at r3, with the mode at r4 as CreateDevice's block has it (1280x720). Non-zero is success */
        read_mode((u32)a->r[1], "device start");
        /* The body's 82757380 makes the device's own back buffer (+0x39f0, bound as render target 0) unless DisableAutoBackBuffer (+0x38), its depth surface
         * (+0x39e8) when EnableAutoDepthStencil (+0x24), its front buffer (+0x39ec) unless DisableAutoFrontBuffer (+0x3c). The kit makes none of them */
        port_log("d3d device start: auto depth %u, disable auto back buffer %u, disable auto front buffer %u\n", rd32((u32)a->r[1] + 0x24),
                 rd32((u32)a->r[1] + 0x38), rd32((u32)a->r[1] + 0x3c));
        wr32(rd32(0x820008bcu), (u32)a->r[0]);   /* the library's current device, as the body stores it: 82744270 (a texture's cache flush) and 18 more read it back */
        xb_state_init((u32)a->r[0]);
        /* The ring the body would set up through 82746f28: 82744270, on the thread 829a47d8 makes the device's owner (+0x2a88), writes its flush packets at
         * +0x30. Room for a few hundred packets; 82746938 below starts it again */
        if (!s_ring) s_ring = xb_alloc(RING_BYTES);
        wr32((u32)a->r[0] + 0x30u, s_ring);
        wr32((u32)a->r[0] + 0x38u, s_ring + RING_BYTES - 0x1000u);
        if (!s_fence_done) s_fence_done = xb_alloc(8);
        wr32((u32)a->r[0] + 0x2a90u, s_fence_done);
        wr32(s_fence_done, 2);
        wr32((u32)a->r[0] + 0x2a9cu, 3);
        xb_vblank_start((u32)a->r[0]);
        *r3 = 1;
        return 1;
    case 0x82754448:     /* GetDisplayMode(device, 0, block *): Too Human, first stop 82c37248 (the aspect ratio from words +0 and +4). The body copies four words of
                          * the device the host start never fills: +0x34e8 and +0x34ec (the mode's width and height, as at D3DPRESENT_PARAMETERS +0 and +4), +0x5434
                          * (the device's own, taken as the refresh rate xam#977 answers) and +0x34f0 (the format at +8) */
        wr32((u32)a->r[2] + 0, s_mode.width);
        wr32((u32)a->r[2] + 4, s_mode.height);
        wr32((u32)a->r[2] + 8, 60);
        wr32((u32)a->r[2] + 12, s_mode.format);
        return 1;
    case 0x82746938:     /* A ring's make-room (device): Too Human, called by 82744270 when +0x30 has passed +0x38. The body kicks the GPU and waits for space, and
                          * returns the new write position from +0x30. The kit's ring is never read, so it starts again, and the kick's fence moves on */
        fence_kick((u32)a->r[0]);
        wr32((u32)a->r[0] + 0x30u, s_ring);
        *r3 = s_ring;
        return 1;
    case 0x82746e58:     /* IsFencePending(fence): Too Human, first stop 8296c898 on the render thread with fence 0, in its state machine that waits for the
                          * GPU before reusing a buffer. The body kicks the ring when the fence is the device's next (+0x2a9c) and compares it with the
                          * completed value +0x2a90 points at. The host draws in order with no GPU behind it: every fence is passed */
        *r3 = 0;
        return 1;
    case 0x82746a38:     /* BlockOnFence(device, fence, ...): Too Human, first stop 82744270 (a texture's cache flush) on the render thread. The body kicks the ring when
                          * the fence is the device's next (+0x2a9c), then spins on the completed value +0x2a90 points at. The host draws in order: the kick passes it */
        if ((u32)a->r[1] == rd32((u32)a->r[0] + 0x2a9cu)) fence_kick((u32)a->r[0]);
        *r3 = 0;
        return 1;
    case 0x82746ed0: {   /* InsertCallback(device, type, callback, context): Too Human, first stop 82c389a8 after its first draw, type 0x1000000, callback 82c37090
                          * (the context's method at vtable +0x5c). The body writes a packet through 82746b30 so the GPU calls back when it gets there. The host
                          * draws in order, so that point is now: the callback runs at once on this thread, below the caller's frame */
        static int said;
        TcArgs c = *a;
        if (!said++) D3D_LOG("d3d InsertCallback type %08x callback %08x context %08x: %s\n", (u32)a->r[1], (u32)a->r[2], (u32)a->r[3],
                             gcb_defer() ? "at the next vertical blank, on its thread (XB_GPU_CB_DEFER)" : "called at once");
        if (gcb_defer() && gcb_queue((u32)a->r[2], (u32)a->r[3])) { *r3 = 0; return 1; }
        c.r[0] = (u32)a->r[3];
        {
            unsigned j = gcb_note((u32)a->r[2], (u32)a->r[3]);
            u32 was = t_gcb_in;
            s_gcb_ring[j].state = 1, t_gcb_in = j + 1;
            (void)tc_call((u32)a->r[2], &c);
            s_gcb_ring[j].state = 2, t_gcb_in = was;
        }
        *r3 = 0;
        return 1;
    }
    case 0x82744750: {   /* Release(resource), Too Human: first stop 82c370d8 under 82c386a8 on the job thread, a vertex buffer the kit made. Takes one off the header's
                          * +4; at zero it releases a type 4's parent (+0x18) when bit 30 (0x40000000) is set, then destroys it through 82744058, which frees the header (and
                          * its data) with the game's XMemFree. The kit makes its headers and data outside the game's heap, so a kit resource stays as it is;
                          * a header the game made (index buffers, surfaces) goes to the translated destroy as before */
        u32 h = (u32)a->r[0], old, w;
        volatile long *p = (volatile long *)(tc_base + h + 4);
        do old = _byteswap_ulong((u32)*p);
        while ((u32)InterlockedCompareExchange(p, (long)_byteswap_ulong(old - 1), (long)_byteswap_ulong(old)) != _byteswap_ulong(old));
        *r3 = old - 1;
        if (old - 1) return 1;
        w = rd32(h);
        if ((w & 15) == 4 && (w & 0x40000000u)) {
            TcArgs c = *a;
            c.r[0] = rd32(h + 0x18);
            (void)entry_res(0x82744750, &c, r3);
        }
        if (find_res(h)) {   /* a kit vertex buffer, its header and data, given back once no recorded call can reach them (res_drain); the rest kept: a
                              * texture's host copy is found by its header's address (xb_gpu_tex_plain), so a header given back and made again could show the old one */
            static int said;
            XbRes *kr = find_res(h);
            const char *kind = kr->kind;
            if (kind && !strcmp(kind, "vertex buffer") && res_free_on()) {
                if (!kr->dying) res_defer(kr, rd32(h + 24) & ~3u, kr->arg[0]);
                if (!said++) D3D_LOG("d3d Release(%08x %s) at zero: a kit resource, given back after the runs that may use it\n", h, kind);
            } else if (!said++)
                D3D_LOG("d3d Release(%08x %s) at zero: a kit resource, kept\n", h, kind ? kind : "?");
        } else {
            TcArgs c = *a;
            c.r[0] = h;
            (void)tc_call(0x82744058, &c);
        }
        *r3 = 0;
        return 1;
    }
    case 0x827570a0:     /* The states' defaults (device): Too Human, first stop 829a47d8 on a second device it makes right after the first, with a 0x12c0-byte command block at +0x30.
                          * The body fills the device's state tables and sets every default, as 827577d0 does inside. The kit keeps one set of states for both */
        xb_state_init((u32)a->r[0]);
        return 1;
    case 0x821c6c50: {   /* Direct3D_CreateDevice(adapter 0, type 1, focus window 0, behaviour flags, D3DPRESENT_PARAMETERS *, D3DDevice **):
                          * the only caller, 82093cf0, builds the parameter block in its own object at +32 and passes &object+4 for
                          * the device and reads it back at once. The device is a zeroed guest block: the game reads fields of it
                          * only where a later stop shows it */
        u32 pp = (u32)a->r[4], dev = (u32)a->r[5];
        read_mode(pp, "CreateDevice");
        wr32(dev, xb_alloc(0x10000));
        xb_state_init(rd32(dev));   /* the game calls its render and sampler states through tables in the device: runtime/xb_state.c */
        *r3 = 0;
        return 1;
    }
    case 0x821c9bf0: {   /* SetViewport(device, D3DVIEWPORT9 *): the only caller so far, 82093cf0 at 82093fc4 right after CreateDevice,
                          * passes the device it just got and a stack block {0, 0, back buffer width, height, 0.0f, 1.0f} (words
                          * +8 and +12 are read from the object's mode record, +16 and +20 are floats loaded from the constants
                          * at 82000a8c). The host viewport follows the window at present time; the game's value is kept */
        u32 v = (u32)a->r[1];
        u32 zmin = rd32(v + 16), zmax = rd32(v + 20);
        s_view.x = rd32(v + 0);
        s_view.y = rd32(v + 4);
        s_view.w = rd32(v + 8);
        s_view.h = rd32(v + 12);
        memcpy(&s_view.zmin, &zmin, 4);
        memcpy(&s_view.zmax, &zmax, 4);
        D3D_LOG("d3d SetViewport %u,%u %ux%u z %g..%g\n", s_view.x, s_view.y, s_view.w, s_view.h, s_view.zmin, s_view.zmax);
        view_apply(t_replay ? 0 : (u32)a->r[0]);   /* replayed from a command buffer, the device's fields are the recorder's (cb_record wrote them) */
        *r3 = 0;
        return 1;
    }
    case 0x821c93d8: {   /* GetViewport(device, D3DVIEWPORT9 *): first stop 82256b48 in the UI library. The block SetViewport took */
        u32 v = (u32)a->r[1], z;
        wr32(v + 0, s_view.x);
        wr32(v + 4, s_view.y);
        wr32(v + 8, s_view.w);
        wr32(v + 12, s_view.h);
        memcpy(&z, &s_view.zmin, 4);
        wr32(v + 16, z);
        memcpy(&z, &s_view.zmax, 4);
        wr32(v + 20, z);
        *r3 = 0;
        return 1;
    }
    case 0x821d73f8: {   /* create a vertex buffer (length, usage): first stop, 820a52a0 under 8209f058, length 0x70. The library makes a 0x20 header and physical memory
                          * for the data and writes the header's type, the reference count, the fetch address and the length. The same fields here, as the game holds
                          * the header; the data stays in guest memory, and a draw reads it */
        u32 h = make_res("vertex buffer", a, target), len = (u32)a->r[0];
        wr32(h, (u32)a->r[1] & 4 ? 0x00300001u : 0x00100001u);
        wr32(h + 4, 1);
        wr32(h + 20, 0xFFFF0000u);
        wr32(h + 24, kit_get(len) | 3u);   /* from the pool, at least 64-byte aligned (the fetch needs words); given back with the header (res_drain) */
        wr32(h + 28, (len & 0x03FFFFFCu) | 0x10000002u);
        *r3 = h;
        return 1;
    }
    case 0x821d8300:     /* lock a vertex buffer (header, offset, size, flags): first stop, 820a52e0 under 82093230, which stores the result and copies its vertices there. The
                          * body waits for the GPU and hands back the fetch address; it never reads the offset. Nothing else uses the data until a draw. Too Human's
                          * 82744928 is the same: it waits through 82744270 and returns the data at +0x18, which 82c373c8 fills (answering 0 made that copy fault) */
        *r3 = rd32((u32)a->r[0] + 24) & ~3u;
        return 1;
    case 0x821d74c0:     /* unlock (header): 820a5320, right after that copy. The body hands the block back to the GPU */
        return state_only(target, a, r3, "unlock a vertex buffer");
    case 0x821c9428:     /* SetStreamSource(device, stream, vertex buffer, offset, stride, ...): first stop, 820935e0 under 82092cd0, stream 0 with no buffer. The body writes the
                          * stream's fetch constant from the buffer's header (a null buffer leaves it). Kept for the draw that reads it */
        if ((u32)a->r[1] >= 16) xb_stop("d3d_stream", "a stream past the 16 the library has", (u32)a->r[1]);
        s_stream[(u32)a->r[1]].buffer = (u32)a->r[2];
        s_stream[(u32)a->r[1]].offset = (u32)a->r[3];
        s_stream[(u32)a->r[1]].stride = (u32)a->r[4];
        *r3 = 0;
        return 1;
    case 0x8274e778: {   /* GetStreamSource(device, stream, offset *, stride *): Too Human, first stop 82c373c8, stream 0. The body returns the buffer at device
                          * +0x30ac + 4 * stream after its AddRef (827446d8), the fetch constant's address less the buffer's as the offset, and 4 times the byte at
                          * +0x30f0 + stream as the stride. What SetStreamSource kept; the count at the header's +4 as the AddRef (kept.json) adds, since the game's
                          * Release runs translated */
        /* The device's fields, as the body reads them (th_state_store keeps them at each SetStreamSource made or recorded). Until 2026-11-13 the kit's
         * own copy, which is the drawing thread's and changes at a command buffer's run on the render thread */
        u32 s = (u32)a->r[1], dev = (u32)a->r[0], b, data, off = 0;
        if (s >= 16) xb_stop("d3d_stream", "a stream past the 16 the library has", s);
        b = dev ? rd32(dev + 0x30acu + 4u * s) : s_stream[s].buffer;
        if (b && dev) data = rd32(b + 0x18u), off = rd32(dev + 8u * (0xefu - s)) - ((((data >> 20) & 0xfffu) + 0x200u) & 0x1000u) - (data & 0x1fffffffu);
        else if (!dev) off = s_stream[s].offset;
        if (b) wr32(b + 4, rd32(b + 4) + 1);
        wr32((u32)a->r[2], off);
        wr32((u32)a->r[3], dev ? rd8(dev + 0x30f0u + s) * 4u : s_stream[s].stride);
        *r3 = b;
        return 1;
    }
    case 0x821cbf30: {   /* LockRect(texture, level, D3DLOCKED_RECT *, rect, flags): first stop, 820a1aa8 under 82092cd0, level 0 and no rect, into the game's object +0x40: pitch,
                          * then the data, which the caller fills (here w * h * 2 bytes of zero). See texture_upload for the rows */
        XbRes *r = find_res((u32)a->r[0]);
        u32 lk = (u32)a->r[2];
        if (!r) xb_stop("d3d_resources", "a lock of a texture the port did not make", (u32)a->r[0]);
        u32 rect = (u32)a->r[3], from = 0;   /* a part (left, top, right, bottom): 820a1aa8 under 820a5ae0. The unlock uploads the whole level */
        if ((u32)a->r[1]) xb_stop("d3d_texture", "a lock of a lower level", (u32)a->r[1]);
        if (!r->data) {
            r->pitch = r->arg[0] * texel_bytes(r->arg[5]);
            r->data = xb_alloc(r->pitch * r->arg[1]);
        }
        if (rect) from = rd32(rect + 4) * r->pitch + rd32(rect) * texel_bytes(r->arg[5]);
        wr32(lk, r->pitch);
        wr32(lk + 4, r->data + from);
        *r3 = 0;
        return 1;
    }
    case 0x821ca488: {   /* Unlock(texture, level): 820a1ae8, the pair of 821cbf30 */
        XbRes *r = find_res((u32)a->r[0]);
        if (r) r->dirty = 1;
        *r3 = 0;
        return 1;
    }
    case 0x821cc008: {   /* GetLevelDesc(texture, level, D3DSURFACE_DESC *): first stop, 820a1a00 right after creating a texture (r3 the result, level 0, the game's object
                          * +0x1c). The body (821cb000) writes format, type, four zero words, width and height; it rebuilds the format from the texture's fetch
                          * constant, the arguments the game created it with here */
        XbRes *t = find_res((u32)a->r[0]);
        u32 d = (u32)a->r[2];
        if (!t) xb_stop("d3d_resources", "a description of a texture the port did not make", (u32)a->r[0]);
        wr32(d, t->arg[5]);
        wr32(d + 4, 3);
        wr32(d + 8, 0);
        wr32(d + 12, 0);
        wr32(d + 16, 0);
        wr32(d + 20, 0);
        wr32(d + 24, t->arg[0]);
        wr32(d + 28, t->arg[1]);
        *r3 = 0;
        return 1;
    }
    case 0x821cc0c8:     /* create a 2D texture (width, height, 1, 1, 0, format 0x28280106, 0, 3): first stop, 82093cf0 at 82093ff0 and again
                          * at 82094020 with 0x28280186 as the format; width and height are the mode record's (1280x720) and the
                          * result is stored in the start-up object (+252, +256) for later calls. The result is a guest header; the
                          * host texture is made when a later call uses it */
        *r3 = make_res("texture", a, target);
        return 1;
    case 0x821cc1e8:     /* create a render target (width, height, format, multisample, D3DSURFACE_PARAMETERS * (zero words)): three
                          * calls in 82093cf0 (82094140, 8209417c, 820941a4), the first two with a height rounded up to the 80-row
                          * tile and 0x18280186 / 0x2d200196 as formats, the third at the mode record's size; each result is stored in
                          * the start-up object (+260, +264, +268). The tile rounding is the original's tiling bookkeeping; the host
                          * target is the window's size and the game's size is only what it believes */
        *r3 = make_res("render target", a, target);
        return 1;
    case 0x821cd9a0: {   /* create a vertex shader from a compiled container (pFunction twice: the compiler passed it in both r3 and r4):
                          * first stop, 820937b8 (called from 820a6610, from 82093cf0) with 0x8201b9c0, a container with the magic
                          * 102a1101 whose word at +0x30 is fffe0300, the D3D shader version token of vs_3_0. 8 such containers and 16 with
                          * the magic 102a1100 are in the image. The host shader is made from the offline conversion of the container
                          * when a draw uses it; the result is a guest header the game stores */
        u32 f = (u32)a->r[0];
        D3D_LOG("d3d vertex shader container %08x magic %08x size %x\n", f, rd32(f), rd32(f + 4));
        *r3 = make_res("vertex shader", a, target);
        return 1;
    }
    case 0x821ca348: {   /* SetRenderTarget(device, index, surface): first stop, 82093970 (from 82093cf0) at 82093994 with index 0 and the
                          * header the first render-target create returned (stored in the start-up object +260). The host binds the
                          * render target made for that header when a draw needs one */
        u32 idx = (u32)a->r[1] & 3, surf = (u32)a->r[2];
        XbRes *r = find_res(surf);
        s_target[idx] = surf;
        if (!surf && idx == 0) {   /* a diagnostic: Too Human resolves render target 0 at frame 635 with none bound */
            static int said;
            if (said++ < 20) port_log("d3d SetRenderTarget 0 := none on host thread %lu\n", GetCurrentThreadId());
        }
        D3D_LOG("d3d SetRenderTarget %u := %08x (%s)\n", idx, surf, r ? r->kind : "not a made resource");
        /* replayed, the header as it was when the call was recorded (cb_record keeps its type and size in r6 and r7, marked in r5): Too Human's
         * main thread describes a header again for its next frame's 320x180 pass while the render thread still runs this frame's buffer, and the
         * final copy and the interface went into a 320x180 surface, a corner of the frame (build box A, 2026-11-06) */
        s_target_host[idx] = !surf ? 0 : t_replay && (u32)a->r[5] == CB_RT_MARK ? host_tex_as(surf, 0, (u32)a->r[7] >> 16, (u32)a->r[6], (u32)a->r[7] & 0xFFFF)
                                                                               : host_tex(surf, 0);
        if (surf && !r && xb_edram_on()) {   /* XB_EDRAM: the host surface of the tiles the header covers (xb_d3d_edram.h) */
            u32 mk = t_replay && (u32)a->r[5] == CB_RT_MARK, w, h = (u32)a->r[7] & 0xFFFF;
            if (!mk) header_decode(surf, &w, &h);
            s_target_host[idx] = edram_bind(surf, s_target_host[idx], mk ? (u32)a->r[4] : rd32(surf + 0x1c), mk ? (u32)a->s[7] : rd32(surf + 0x18), h);
        }
        xb_gpu_bind_target(idx, s_target_host[idx]);
        th_target_bias(idx, surf, !surf ? 0 : t_replay && (u32)a->r[5] == CB_RT_MARK ? (u32)a->r[4] : rd32(surf + 0x1c));   /* as recorded, on a replay */
        *r3 = 0;
        return 1;
    }
    case 0x821ca070: {   /* SetDepthStencilSurface(device, surface): first stop, 82093970 at 820939a0 right after SetRenderTarget, with the
                          * header the second render-target create returned (the start-up object +264) */
        XbRes *r = find_res(s_depth = (u32)a->r[1]);
        D3D_LOG("d3d SetDepthStencilSurface := %08x (%s)\n", s_depth, r ? r->kind : "not a made resource");
        xb_gpu_bind_depth(s_depth ? host_tex(s_depth, 1) : 0);
        *r3 = 0;
        return 1;
    }
    case 0x821d0708: {   /* EndTiling, resolve and clear for a tiled target (device, flags, source rect, destination texture, colour block, ...): 82093a98 at 82093b74 when the mode record
                          * says more than one tile, the other path being plain Resolve to the same texture (the object +256). The body ends in a tail call of 821d24b0
                          * with these. Flags as in Resolve: 0x100 clears the target, 0x200 its depth and stencil. The copy is what the frame's last draw samples */
        u32 fl = (u32)a->r[1], dst = (u32)a->r[3], col = (u32)a->r[4];
        float rgba[4] = {0, 0, 0, 0};
        unsigned i;
        if ((u32)a->r[2] || (fl & ~0x300u)) xb_stop("d3d_resolve", "a tiled resolve with a part or flags the host has no case for", fl);
        if (edram_diag())
            edram_diag_n++, port_log("diag edram frame %u end tiling flags %x: target %08x -> %08x, colour block %08x; called from %08x %08x %08x%s\n", s_frame, fl,
                                     s_target[0], dst, col, lib_up(0), lib_up(1), lib_up(2), t_replay ? " (as recorded)" : "");
        resolve_sig('E', fl, s_target[0], dst);
        if (dst && s_target[0]) xb_edram_sync_host(s_target_host[0]);
        if (dst) xb_gpu_resolve(s_target_host[0] ? s_target_host[0] : host_tex(s_target[0], 0), host_tex(dst, 0), 0);
        if (col)
            for (i = 0; i < 4; i++) {
                u32 w = rd32(col + 4 * i);
                memcpy(&rgba[i], &w, 4);
            }
        if (fl & 0x100) edram_clear_value(s_target[0], rgba), xb_gpu_clear(0xF0, rgba, 0, 0);
        if (fl & 0x200) xb_gpu_clear(0x300, rgba, a->f[0], (u32)a->r[5]);
        if (fl & 0x300) clear_diag("end tiling", fl, s_target[0], col, rgba, (float)a->f[0], (u32)a->r[5]);
        *r3 = 0;
        return 1;
    }
    /* BeginTiling and its clear (r4 flags, r5 a count of 16-byte tile rects at r6, a colour block at r7, z in f1). First stop: 82093970 at 82093a54 with r4 0, r5 2, r6 0x8236d368,
     * colour words all zero, z 1.0, the start-up clear. Flags: colour is 0xF0, depth and stencil 0x300 */
    case 0x821d00a8: {
        u32 col = (u32)a->r[4], c[4];
        int i;
        for (i = 0; i < 4; i++) c[i] = col ? rd32(col + 4 * i) : 0;
        D3D_LOG("d3d clear %08x: r4 %x r5 %x r6 %x r7 %x (words %08x %08x %08x %08x) r8 %x, f1 %g\n", target, (u32)a->r[1], (u32)a->r[2],
                 (u32)a->r[3], col, c[0], c[1], c[2], c[3], (u32)a->r[5], a->f[0]);
        {
            u32 fl = (u32)a->r[1];
            float rgba[4] = {0, 0, 0, 0};
            if (fl & 0xF0) {
                if (!col) xb_stop("d3d_clear", "a colour clear with no colour block", fl);
                for (i = 0; i < 4; i++) memcpy(&rgba[i], &c[i], 4);
            }
            if (fl & 0x3F0) xb_gpu_clear(fl & 0x3F0, rgba, a->f[0], (u32)a->r[5]);
        }
        *r3 = 0;
        return 1;
    }
    case 0x82761f00: {   /* Too Human's BeginTiling(device, flags, count, rects, colour vector *, z in f1, stencil): first stop 8296a520 with flags 1, 2 tiles, right
                          * after its Clear of the same target. Flag 1 skips the first tile's clear (the body then only keeps the colour at +0x33a0 for the later
                          * tiles); without it the body clears through 82765bc0 with flags 0x3f. The host has no tiles, so only that clear is done */
        u32 fl = (u32)a->r[1], col = (u32)a->r[4];
        float rgba[4] = {0, 0, 0, 0};
        int i;
        if (fl & ~7u) xb_stop("d3d_clear", "BeginTiling flags the host has no case for", fl);
        if (!(fl & 1)) {
            for (i = 0; col && i < 4; i++) {
                u32 w = rd32(col + 4 * i);
                memcpy(&rgba[i], &w, 4);
            }
            xb_gpu_clear((col ? 0xF0 : 0) | 0x300, rgba, a->f[0], (u32)a->r[5]);
        }
        D3D_LOG("d3d BeginTiling flags %x, %u tiles: %s\n", fl, (u32)a->r[2], fl & 1 ? "the first tile keeps the Clear before it" : "cleared");
        *r3 = 0;
        return 1;
    }
    /* (device, sampler, value): first stops, 82093a98 at 82093bc0 and 82093bd0 with sampler 0 and value 0. The library body indexes its
     * per-sampler state by 24 bytes * (48 + sampler) from the device and merges the value into a bit field there: one setter per
     * sampler state, two different fields (shifts 22 and 21). The library's rows at 824b0dc0 name them: states 20 and 16 */
    case 0x821ccea0:     /* SetVertexShaderConstantF(device, first, floats, count): the library stores it at device + 16 * (120 + first) */
    case 0x821ccf78: {   /* SetPixelShaderConstantF, the same at 16 * (376 + first), 256 further: first stop, 820938d0 under 8209e9c0, c1, one float4 */
        u32 first = (u32)a->r[1], count = (u32)a->r[3], k;
        float v[256 * 4];
        if (first + count > 256) xb_stop("d3d_state", "shader constants past the 256", first << 16 | count);
        for (k = 0; k < count * 4; k++) {
            u32 w = rd32((u32)a->r[2] + k * 4);
            memcpy(&v[k], &w, 4);
            if (!t_replay)   /* replayed, they are in the draws' copies already (cb_record), and the device is the recorder's */
                wr32((u32)a->r[0] + 16u * ((target == 0x821ccf78 ? 376u : 120u) + first) + k * 4, w);   /* where the library keeps them, which Too Human's draws read (constants_from_device) */
        }
        D3D_LOG("d3d constants %s c%u: %g %g %g %g\n", target == 0x821ccf78 ? "pixel" : "vertex", first, v[0], v[1], v[2], v[3]);
        if (target == 0x821ccea0 && first <= 12 && first + count > 12 && v[4 * (12 - first)] != v[4 * (12 - first)]) {   /* a NaN view-projection: who set it (XB_NAN_WHO) */
            static int said_nan;
            if (getenv("XB_NAN_WHO") && said_nan++ < 6) {
                u32 sp = a->sp[0], src = (u32)a->r[2] + 16u * (12 - first), d;
                char chain[200];
                int at = 0;
                for (d = 0; d < 10 && sp && guest_readable(sp, 4); d++) {   /* the guest back chain: [sp] the caller's r1, its saved LR 8 below that */
                    u32 up = rd32(sp);
                    if (!up || !guest_readable(up - 8, 4)) break;
                    at += sprintf_s(chain + at, sizeof chain - at, " %08x", rd32(up - 8));
                    sp = up;
                }
                port_log("d3d NaN c12 from %08x (count %u, first %u, thread sp %08x), saved LRs up the guest stack:%s; the 64 bytes before the matrix:", src, count, first, a->sp[0], chain);
                for (d = 0; d < 16 && guest_readable(src - 64, 64); d++) port_log(" %08x", rd32(src - 64 + 4 * d));
                port_log("\n");
            }
        }
        xb_gpu_set_constants(target == 0x821ccf78, first, count, v);
        *r3 = 0;
        return 1;
    }
    case 0x821c88b8:
        xb_ss_set((u32)a->r[1], 5, (u32)a->r[2]);
        *r3 = 0;
        return 1;
    case 0x821c8a60:
        xb_ss_set((u32)a->r[1], 4, (u32)a->r[2]);
        *r3 = 0;
        return 1;
    case 0x821cc448: {   /* SetTexture(device, sampler, texture): first stop, 82093a98 at 82093bd8 right after the sampler states, sampler 0 and
                          * the header the second texture create returned (the start-up object +256) */
        u32 smp = (u32)a->r[1] & 15, t = (u32)a->r[2];
        XbRes *r = find_res(t);
        s_texture[smp] = t;
        D3D_LOG("d3d SetTexture %u := %08x (%s)\n", smp, t, r ? r->kind : t ? "not a made resource" : "none");
        *r3 = 0;
        return 1;
    }
    }
    return 0;
}
