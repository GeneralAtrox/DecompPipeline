/* xb_d3d.c, the XB_SAMPLE timing (one translation unit with it; here for the 400-line cap): the host time of each library entry and of each
 * step of a draw, and what is in progress when a sample is taken. Off without XB_SAMPLE: one test of a flag per call */

/* With XB_SAMPLE set (xb_lib_entry), the host time of each step of a draw, which xb_d3d_times_since prints with each sample: Too Human's menu
 * spent all of its render thread's time in DrawIndexedVertices (829fcfc0) and the entry timing alone does not say which step (2026-11-05) */
static int s_timing = -1;
enum { PART_CHECK, PART_GATHER, PART_CONVERT, PART_TEXTURES, PART_CONSTANTS, PART_HOST, PARTS };
static const char *const s_part_name[PARTS] = {"index check", "gather", "convert", "textures", "constants", "host draw"};
static LONG64 s_vc_hits, s_vc_conv, s_vc_nokey, s_vc_full;   /* vertices whose conversion was kept, and converted (xb_d3d_vtx.h) */
static LONG64 s_part[PARTS], s_part_was[PARTS], s_part_draws, s_part_verts, s_part_draws_was, s_part_verts_was;
static LONG64 part_now(void)
{
    LARGE_INTEGER t;
    if (s_timing <= 0) return 0;
    QueryPerformanceCounter(&t);
    return t.QuadPart;
}
#define PART_END(k, t0) ((void)(s_timing > 0 ? (s_part[k] += part_now() - (t0)) : 0))
static volatile unsigned s_part_now, s_draw_n, s_draw_inputs, s_draw_prim;   /* the step and draw in progress, for a sample taken while one never returns */
#define PART_START(k) (s_part_now = (k) + 1u, part_now())

static int lib_entry(u32 target, const TcArgs *a, u64 *r3, f64 *f1);
static __declspec(thread) int t_replay;    /* this thread runs a command buffer's kept calls (xb_d3d_cb.h) */
static __declspec(thread) const u32 *t_up;   /* on a replay, the guest callers the kept call was recorded under (xb_d3d_cb.h) */
/* The guest function k + 1 calls up from the library entry being handled (0 the one that called it), or on a command buffer's replay the one it was recorded under */
static u32 lib_up(unsigned k) { return t_replay && t_up ? (k < 3 ? t_up[k] : 0) : xb_caller(k + 1); }
static __declspec(thread) u32 t_dc; static __declspec(thread) int t_dc_on;   /* the kept call being run: its device depth-control word (CbCall dc) */
static __declspec(thread) u32 t_dw[3];     /* and its colour mask, colour control and mode control words (CbCall dw) */
static __declspec(thread) u32 t_snap;      /* the kept call being run: its copy of the device's constants (cb_snap), or 0 */
static volatile u32 s_cb_run, s_cb_at, s_cb_n, s_cb_at_entry;   /* the command buffer run in progress and its call (xb_d3d_cb.h) */

/* With XB_SAMPLE set, the host time each library entry takes (the kit's whole handling of the call: its draw, conversions, the host GPU's calls), so a
 * sample says where a slow frame's time goes when the guest stacks only show the game waiting on its render thread (Too Human's menu, 2026-11-05).
 * Off without XB_SAMPLE: one test of a flag per call */
#define TIME_SLOTS 1024u
static struct { volatile LONG entry; volatile LONG64 ticks, calls; LONG64 ticks_was, calls_was; } s_time[TIME_SLOTS];
static volatile LONG s_entry_now;
static volatile LONG64 s_entry_t0;

int xb_lib_entry(u32 target, const TcArgs *a, u64 *r3, f64 *f1)
{
    LARGE_INTEGER t0, t1;
    int r;
    u32 h;
    if (s_timing < 0) s_timing = getenv("XB_SAMPLE") != NULL;
    if (s_log_left > 0 && getenv("XB_LOG_FROM") && s_frame >= (unsigned)atoi(getenv("XB_LOG_FROM")))   /* every library entry the game calls, by its own address, in the frame log (XB_LOG_FROM): the list to set beside Xenia's call log */
        D3D_LOG("lib frame %u %08x (%x %x %x %x %x %x %x %x) thread %lu%s from %08x %08x\n", s_frame, target, (u32)a->r[0], (u32)a->r[1], (u32)a->r[2], (u32)a->r[3],
                (u32)a->r[4], (u32)a->r[5], (u32)a->r[6], (u32)a->r[7], GetCurrentThreadId(), t_replay ? " replay" : "", xb_caller(1), xb_caller(2));
        /* r3..r10; "replay": a command buffer's kept call, made again; "from": the guest functions on the call stack above the entry (xb_call.c pushes
         * the entry itself, so 1 is the function that called it and 2 that one's caller; a call the kit makes itself shows the kit's enclosing entry) */
    if (!s_timing) return lib_entry(target, a, r3, f1);
    QueryPerformanceCounter(&t0);
    {   /* the innermost entry in progress, for a sample taken while one never returns (Too Human's render thread inside 82743a40 at frame 177) */
        LONG now = s_entry_now;
        LONG64 since = s_entry_t0;
        s_entry_now = (LONG)target, s_entry_t0 = t0.QuadPart;
        r = lib_entry(target, a, r3, f1);
        s_entry_now = now, s_entry_t0 = since;
    }
    QueryPerformanceCounter(&t1);
    for (h = (target * 2654435761u) >> 22, target |= 1u; ; h = (h + 1u) & (TIME_SLOTS - 1u)) {   /* | 1: no entry is 0, the empty slot */
        LONG was = s_time[h].entry;
        if (!was) was = InterlockedCompareExchange(&s_time[h].entry, (LONG)target, 0);
        if (!was || (u32)was == target) break;
    }
    InterlockedAdd64(&s_time[h].ticks, t1.QuadPart - t0.QuadPart);
    InterlockedIncrement64(&s_time[h].calls);
    return r;
}

void xb_d3d_times_since(void)
{
    LARGE_INTEGER f;
    unsigned top[8] = {0}, n = 0;
    LONG64 d[TIME_SLOTS], dc[TIME_SLOTS], all = 0, calls = 0;
    char line[1024];
    int at;
    if (s_timing <= 0) return;
    QueryPerformanceFrequency(&f);
    for (unsigned i = 0; i < TIME_SLOTS; i++) {
        LONG64 t = s_time[i].ticks, c = s_time[i].calls;
        d[i] = t - s_time[i].ticks_was;
        dc[i] = c - s_time[i].calls_was;
        calls += dc[i];
        all += d[i];
        s_time[i].calls_was = c;   /* calls_was is read below before ticks_was is moved on */
    }
    for (n = 0; n < 8; n++) {   /* the eight that took longest, longest first */
        unsigned best = TIME_SLOTS;
        for (unsigned i = 0; i < TIME_SLOTS; i++) {
            int taken = 0;
            for (unsigned k = 0; k < n; k++) taken |= top[k] == i;
            if (d[i] > 0 && !taken && (best == TIME_SLOTS || d[i] > d[best])) best = i;
        }
        if (best == TIME_SLOTS) break;
        top[n] = best;
    }
    at = sprintf_s(line, sizeof line, "graphics entries since the last sample: %.0f ms over %lld calls;", all * 1000.0 / f.QuadPart, (long long)calls);
    for (unsigned k = 0; k < n && at > 0 && at < (int)sizeof line - 64; k++)
        at += sprintf_s(line + at, sizeof line - at, " %08x %.0f ms/%lld", (u32)s_time[top[k]].entry & ~1u, d[top[k]] * 1000.0 / f.QuadPart, (long long)dc[top[k]]);
    for (unsigned i = 0; i < TIME_SLOTS; i++) s_time[i].ticks_was += d[i];
    port_log("%s\n", line);
    at = sprintf_s(line, sizeof line, "draw steps since the last sample: %lld draws, %lld vertices;", (long long)(s_part_draws - s_part_draws_was), (long long)(s_part_verts - s_part_verts_was));
    s_part_draws_was = s_part_draws, s_part_verts_was = s_part_verts;
    for (unsigned k = 0; k < PARTS && at > 0; k++) {
        LONG64 t = s_part[k];
        at += sprintf_s(line + at, sizeof line - at, " %s %.0f ms", s_part_name[k], (t - s_part_was[k]) * 1000.0 / f.QuadPart);
        s_part_was[k] = t;
    }
    {
        static LONG64 asks_was, absent_was, dec_was, texels_was, same_was, hits_was, conv_was, nokey_was, full_was;
        LONG64 asks = s_region_asks, absent = s_region_absent, dec = s_tex_decodes, texels = s_tex_texels, same = s_tex_same, hits = s_vc_hits, conv = s_vc_conv, nokey = s_vc_nokey, full = s_vc_full;
        if (at > 0) sprintf_s(line + at, sizeof line - at, "; %lld memory region asks, %lld not committed; %lld textures decoded, %lld texels, %lld unchanged and kept; %lld vertices kept, %lld converted (%lld not hashable, %lld with no room)", (long long)(asks - asks_was),
                              (long long)(absent - absent_was), (long long)(dec - dec_was), (long long)(texels - texels_was), (long long)(same - same_was), (long long)(hits - hits_was), (long long)(conv - conv_was), (long long)(nokey - nokey_was), (long long)(full - full_was));
        asks_was = asks, absent_was = absent, dec_was = dec, texels_was = texels, same_was = same, hits_was = hits, conv_was = conv, nokey_was = nokey, full_was = full;
    }
    port_log("%s\n", line);
    if (s_entry_now) {
        LARGE_INTEGER now;
        QueryPerformanceCounter(&now);
        port_log("graphics entry in progress: %08x for %.0f ms; command buffer %08x at call %u of %u (%08x); draw step %s, %u vertices of %u inputs, primitive %u\n", (u32)s_entry_now, (now.QuadPart - s_entry_t0) * 1000.0 / f.QuadPart,
                 s_cb_run, s_cb_at, s_cb_n, s_cb_at_entry, s_part_now ? s_part_name[s_part_now - 1] : "none", s_draw_n, s_draw_inputs, s_draw_prim);
    }
}
