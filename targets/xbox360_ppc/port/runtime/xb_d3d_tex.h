/* xb_d3d.c, the textures a game sets through the device's own fetch constants (one translation unit with it; here for the 400-line cap).
 * Too Human makes its texture headers itself and copies their fetch constants into the device (+0x480 + 24 * slot) with no SetTexture
 * call the kit sees, so before a draw each sampler's slot is read as the GPU reads it: the base level of a 2D texture, untiled, its
 * words in the slot's byte order, decoded to RGBA and put through the slot's swizzle. Re-read at most once a frame (a movie's planes
 * change every frame); a format or shape with no case binds nothing and is logged once. */

#include <tmmintrin.h>   /* constants_from_device's byte swap (SSSE3, as xb_vconv.c) */

/* Both per thread: the worker records draws (setting them) while the render thread runs a buffer's; shared, a run's draw read the recorder's
 * device, and the mask was cleared under a run (a crash at frame 61, build box A, 2026-11-06) */
static __declspec(thread) const u8 *s_snap_mask;   /* a replayed draw's: the constant registers its recording set (cb_snap); the rest are inherited */
static __declspec(thread) u32 s_fetch_dev;   /* the device of the last Too Human draw entry (xb_lib_entry): whose slots the samplers read */

/* The vertices of a draw by the game's own numbering (the gathered indices of DrawIndexedVertices, else first + i), for the streams past 0 */
static const u32 *s_vindex;
static u32 s_vfirst;

/* Vertex i of stream s (1..15) at the fetch's word offset and stride (its third word), from the stream's fetch constant in the device
 * (+8 * (0xef - s): the data's GPU address in the top 30 bits), or 0 with none set. Too Human's text reads its texture coordinates and
 * colour from streams 2 and 3 (two floats, 12 bytes apart; four floats), the position from the drawn vertices: with those inputs 0, 0, 0, 1 every glyph was black */
/* Whether n bytes of guest memory at a are committed. The last 16 good regions are kept for the frame, so most vertices ask the host nothing: with
 * only the last one kept, Too Human's menu draws (streams 2 and 3 in other regions than the drawn vertices) asked VirtualQuery for almost every element,
 * 14 us a vertex and 18 s of every 20 on the render thread (2026-11-05). s_region_asks and s_region_absent count the asks for the timing line */
static LONG64 s_region_asks, s_region_absent;

/* XB_VTX_OLD=1: the vertex path as it was before 2026-11-14 (vcache_key hashing the draw's vertex numbers one by one, guest_readable scanning its 16
 * regions in order, every element converted through stream_element and fetch_components). The new path hands the device the same bytes;
 * XB_VTX_CHECK=1 (xb_d3d_vconv.h) proves it draw by draw */
static int vtx_old(void)
{
    static int on = -1;
    if (on < 0) { const char *e = getenv("XB_VTX_OLD"); on = e && *e == '1'; }
    return on;
}

static int guest_readable_query(u32 a, u32 n);

/* XB_MEM_READABLE (a developer switch, not a player setting): unset or 1, the kit's own page map (xb_mem_readable, xb_mem.c), with no system call; 0, the
 * old way below (VirtualQuery, regions kept for the frame); check, the map's answer, compared at every call with a fresh VirtualQuery of every page the
 * range covers, the disagreements counted and logged every 100 frames (and the first 8 by address) */
static LONG64 s_readable_calls, s_readable_mism;

static int readable_mode(void)
{
    static int m = -1;
    if (m < 0) { const char *e = getenv("XB_MEM_READABLE"); m = !e || !*e ? 1 : e[0] == '0' ? 0 : e[0] == 'c' ? 2 : 1; }
    return m;
}

static int readable_fresh(u32 a, u32 n)   /* every page of the range committed and readable, asked of Windows now */
{
    u8 *p = tc_base + a, *end = tc_base + ((unsigned long long)a + (n ? n : 1u));
    MEMORY_BASIC_INFORMATION mi;
    if ((unsigned long long)a + (n ? n : 1u) > 0x100000000ull) return 0;
    while (p < end) {
        if (!VirtualQuery(p, &mi, sizeof mi) || mi.State != MEM_COMMIT || (mi.Protect & (PAGE_NOACCESS | PAGE_GUARD))) return 0;
        p = (u8 *)mi.BaseAddress + mi.RegionSize;
    }
    return 1;
}

static int guest_readable(u32 a, u32 n)
{
    int m = readable_mode(), r;
    if (m == 1) return xb_mem_readable(a, n);
    if (m == 0) return guest_readable_query(a, n);
    r = xb_mem_readable(a, n);
    s_readable_calls++;
    if (r != readable_fresh(a, n) && ++s_readable_mism <= 8)
        port_log("readable check MISMATCH frame %u: %08x + %x, the page map says %d, VirtualQuery %d\n", s_frame, a, n, r, !r);
    {
        static unsigned window = ~0u;
        if (s_frame / 100u != window) {
            window = s_frame / 100u;
            port_log("readable check to frame %u: %lld calls, %lld disagree with VirtualQuery\n", s_frame, (long long)s_readable_calls, (long long)s_readable_mism);
        }
    }
    return r;
}

static int guest_readable_query(u32 a, u32 n)
{
    static struct { u8 *lo, *hi; } got[16], gone[4];   /* gone: regions not committed, kept for the frame too (a stale stream 15 fetch constant asked 400,000 times a sample) */
    static unsigned next, next_gone, frame = ~0u, last;   /* last: the entry that answered last, tried first (the same answer as the scan: any entry's 1 is the scan's 1) */
    MEMORY_BASIC_INFORMATION mi = {0};
    u8 *p = tc_base + a;
    unsigned i;
    if (frame != s_frame) {   /* memory the game gives back, or takes, is asked about again each frame */
        memset(got, 0, sizeof got);
        memset(gone, 0, sizeof gone);
        frame = s_frame;
    }
    i = last & 15u;
    if (p >= got[i].lo && p + n <= got[i].hi && !vtx_old()) return 1;
    for (i = 0; i < 16; i++)
        if (p >= got[i].lo && p + n <= got[i].hi) return last = i, 1;
    for (i = 0; i < 4; i++)
        if (p >= gone[i].lo && p < gone[i].hi) return 0;
    s_region_asks++;
    if (!VirtualQuery(p, &mi, sizeof mi) || mi.State != MEM_COMMIT || (mi.Protect & (PAGE_NOACCESS | PAGE_GUARD))) {
        s_region_absent++;
        if (mi.RegionSize && mi.State != MEM_COMMIT) {
            gone[next_gone].lo = (u8 *)mi.BaseAddress, gone[next_gone].hi = gone[next_gone].lo + mi.RegionSize;
            next_gone = (next_gone + 1) & 3u;
        }
        return 0;
    }
    got[next].lo = (u8 *)mi.BaseAddress, got[next].hi = got[next].lo + mi.RegionSize;
    i = next, next = (next + 1) & 15u, last = i;
    return p + n <= got[i].hi;
}

/* Element v of stream s (its fetch constant in the device), 0 when it has none or the element is not committed memory */
static u32 stream_element(u32 s, u32 v, u32 offset, u32 w2)
{
    u32 base = s_fetch_dev ? rd32(s_fetch_dev + 8u * (0xefu - s)) & 0x1ffffffcu : 0, at;
    if (!base) {
        static int said;
        if (!said++) port_log("d3d draw: shader %08x reads stream %u, which has no fetch constant in the device\n", s_vs_container, s);
        return 0;
    }
    at =(base | 0x20000000u) + v * (w2 & 255u) * 4u + offset;
    if (!guest_readable(at, 16)) {   /* Too Human faulted here at frame 635 (2c7bf850 under 82999c98), inside memory handed out: a fetch constant left
                                      * pointing at memory given back. Logged and read as absent (0, 0, 0, 1), as a stream with no constant is */
        static unsigned bad;
        char st[160];
        if (bad++ < 4) {
            xb_stack_line(st, sizeof st);
            port_log("d3d draw: stream %u vertex %u at %08x is not committed guest memory (fetch constant %08x %08x, stride %u, offset %u, shader %08x, frame %u):%s\n",
                     s, v, at, rd32(s_fetch_dev + 8u * (0xefu - s)), rd32(s_fetch_dev + 8u * (0xefu - s) + 4u), (w2 & 255u) * 4u, offset, s_vs_container, s_frame, st);
        }
        return 0;
    }
    return at;
}

/* The draw's vertex i in stream s: the element the draw's index (or first vertex) names */
static u32 stream_vertex(u32 s, unsigned i, u32 offset, u32 w2)
{
    return stream_element(s, s_vindex ? s_vindex[i] : s_vfirst + i, offset, w2);
}

/* Its shader constants the same way: the game writes most of them into the device inline (+16 * (120 + n) vertex, +16 * (376 + n) pixel,
 * where SetVertexShaderConstantF stores them), so each draw takes all 512 from there; with only the setters' few, text and meshes drew black.
 *
 * XB_CONST_FAST (on by default; =0 the old loop): a register is decoded again only when its 16 guest bytes differ from the ones it was last
 * decoded from (one SSE compare a register), and xb_gpu_set_constants copies only what differs and uploads only then (xb_gpu_draw.h). The
 * old loop read and swapped all 2048 words and copied all 512 registers each draw: 7.2 ms of every 123 ms gameplay frame on build box A
 * (2026-11-14). Both copies are this thread's, as s_fetch_dev is: the worker and the render thread each keep their own.
 * XB_CONST_CHECK=1: each draw also runs the old loop and counts words and registers that differ from it (logged every 100 frames) */
static int const_fast(void)
{
    static int on = -1;
    if (on < 0) { const char *e = getenv("XB_CONST_FAST"); on = !(e && e[0] == '0'); }
    return on;
}

static int const_check_on(void)
{
    static int on = -1;
    if (on < 0) { const char *e = getenv("XB_CONST_CHECK"); on = e && e[0] == '1'; }
    return on;
}

static LONG64 s_cc_draws, s_cc_words, s_cc_regs;   /* XB_CONST_CHECK: draws checked, decoded words and host registers that differ from the old loop */

static void const_check(const float (*v)[256 * 4])
{
    static unsigned said_frame = ~0u;
    unsigned p, k, j;
    for (p = 0; p < 2; p++) {
        const float *gpu = xb_gpu_constants((int)p);
        for (k = 0; k < 256 * 4; k++) {
            u32 w = rd32(s_fetch_dev + 16u * (p ? 376u : 120u) + 4u * k), x;
            memcpy(&x, &v[p][k], 4);
            if (x != w) s_cc_words++;
        }
        for (k = 0; k < 256; k++) {   /* the host's register as the old loop sets it: all of them, or a recorded copy's own */
            unsigned bad = 0;
            if (s_snap_mask && !(s_snap_mask[(p * 256 + k) >> 3] >> ((p * 256 + k) & 7) & 1)) continue;
            for (j = 0; j < 4; j++) {
                u32 y;
                memcpy(&y, &gpu[4 * k + j], 4);
                bad |= y != rd32(s_fetch_dev + 16u * (p ? 376u : 120u) + 4u * (4 * k + j));
            }
            s_cc_regs += bad;
        }
    }
    s_cc_draws++;
    if (s_frame % 100u == 0u && said_frame != s_frame) {
        said_frame = s_frame;
        port_log("const check (XB_CONST_CHECK) at frame %u: %lld draws, %lld words and %lld host registers differ from the old loop\n", s_frame, s_cc_draws, s_cc_words, s_cc_regs);
    }
}

static void constants_from_device(void)
{
    static __declspec(thread) float v[2][256 * 4];
    static __declspec(thread) __declspec(align(16)) u8 raw[2][256 * 16];   /* XB_CONST_FAST: the guest bytes each register of v was decoded from */
    static __declspec(thread) int have_raw;
    static int said;
    unsigned k, p, set[2] = {0, 0};
    int fast = const_fast(), logs = s_log_left > 0 || (s_frame >= 1500 && !said);
    for (p = 0; p < 2; p++) {
        if (fast) {
            const u8 *src = tc_base + s_fetch_dev + 16u * (p ? 376u : 120u);
            const __m128i swap = _mm_setr_epi8(3, 2, 1, 0, 7, 6, 5, 4, 11, 10, 9, 8, 15, 14, 13, 12);
            for (k = 0; k < 256; k++) {
                __m128i g = _mm_loadu_si128((const __m128i *)(src + 16u * k));
                if (have_raw && _mm_movemask_epi8(_mm_cmpeq_epi8(g, _mm_load_si128((const __m128i *)&raw[p][16u * k]))) == 0xffff) continue;
                _mm_store_si128((__m128i *)&raw[p][16u * k], g);
                _mm_storeu_si128((__m128i *)&v[p][4u * k], _mm_shuffle_epi8(g, swap));   /* each word byte-swapped, as rd32 reads it */
            }
            if (logs)
                for (k = 0; k < 256 * 4; k++) {
                    u32 w;
                    memcpy(&w, &v[p][k], 4);
                    set[p] += w != 0;
                }
        } else
            for (k = 0; k < 256 * 4; k++) {
                u32 w = rd32(s_fetch_dev + 16u * (p ? 376u : 120u) + 4u * k);
                memcpy(&v[p][k], &w, 4);
                set[p] += w != 0;
            }
        if (!s_snap_mask) xb_gpu_set_constants((int)p, 0, 256, v[p]);
        else
            for (k = 0; k < 256; k++)
                if (s_snap_mask[(p * 256 + k) >> 3] >> ((p * 256 + k) & 7) & 1) xb_gpu_set_constants((int)p, k, 1, &v[p][4 * k]);
    }
    have_raw = fast;
    if (const_check_on()) const_check((const float (*)[256 * 4])v);
    if (s_log_left > 0) {   /* which vertex registers hold a value, for the frame log (XB_LOG_FROM) */
        char regs[400];
        int at = 0;
        for (k = 0; k < 256 && at < (int)sizeof regs - 8; k++)
            if (v[0][4 * k] || v[0][4 * k + 1] || v[0][4 * k + 2] || v[0][4 * k + 3]) at += sprintf_s(regs + at, sizeof regs - at, " c%u", k);
        regs[at] = 0;
        D3D_LOG("d3d constants for the draw from %08x%s: %u vertex and %u pixel words set, c12 %s (%g %g %g %g); vertex registers set:%s\n", s_fetch_dev,
                s_snap_mask ? " (a recorded copy)" : "", set[0], set[1], s_snap_mask && !(s_snap_mask[1] & 0x10) ? "inherited" : "from here",
                v[0][48], v[0][49], v[0][50], v[0][51], regs);
    }
    if (s_frame >= 1500 && !said++) port_log("d3d constants from the device at frame %u: %u vertex and %u pixel words set, c0 %g %g %g %g\n", s_frame, set[0], set[1], v[0][0], v[0][1], v[0][2], v[0][3]);
}

/* The block of (x, y) in a tiled surface whose rows hold width blocks (rounded up to 32 inside), bpb bytes each: the GPU's
 * 32 x 32 macro tiles of 8 x 2 micro tiles, as the XDK's XGAddress2DTiledOffset numbers them */
static u32 tiled_block(u32 x, u32 y, u32 width, u32 bpb)
{
    u32 aligned = (width + 31) & ~31u, lb = (bpb >> 2) + ((bpb >> 1) >> (bpb >> 2));
    u32 macro = ((x >> 5) + (y >> 5) * (aligned >> 5)) << (lb + 7);
    u32 micro = ((x & 7) + ((y & 6) << 2)) << lb;
    u32 off = macro + ((micro & ~15u) << 1) + (micro & 15) + ((y & 8) << (3 + lb)) + ((y & 1) << 4);
    return (((off & ~511u) << 3) + ((off & 448) << 2) + (off & 63) + ((y & 16) << 7) + (((((y & 8) >> 2) + (x >> 3)) & 3) << 6)) >> lb;
}

/* The word at a as the GPU sees it with the slot's byte order: 0 none (little-endian in memory), 1 bytes swapped in each half,
 * 2 in the word (a big-endian word as written), 3 halves swapped */
static u32 gpu_word(u32 a, u32 endian)
{
    u32 w = rd32(a);
    switch (endian) {
    case 0: return _byteswap_ulong(w);
    case 1: return (w >> 16) | (w << 16);
    case 3: w = _byteswap_ulong(w); return (w >> 16) | (w << 16);
    }
    return w;
}

static u32 rgb565(u32 c) { return ((c >> 11) & 31) * 255 / 31 | (((c >> 5) & 63) * 255 / 63) << 8 | ((c & 31) * 255 / 31) << 16; }

/* The 16 texels of a DXT colour block (lo: the two colours, hi: the indices) into c, RGB in the low 24 bits as x, y, z */
static void dxt_colours(u32 lo, u32 hi, int three, u32 c[16])
{
    u32 p[4], k, i;
    p[0] = rgb565(lo & 0xffff);
    p[1] = rgb565(lo >> 16);
    for (k = 0; k < 3; k++) {
        u32 a = (p[0] >> (8 * k)) & 255, b = (p[1] >> (8 * k)) & 255;
        if (three) {
            p[2] = (p[2] & ~(255u << (8 * k))) | ((a + b) / 2) << (8 * k);
            p[3] &= ~(255u << (8 * k));
        } else {
            p[2] = (p[2] & ~(255u << (8 * k))) | ((2 * a + b) / 3) << (8 * k);
            p[3] = (p[3] & ~(255u << (8 * k))) | ((a + 2 * b) / 3) << (8 * k);
        }
    }
    for (i = 0; i < 16; i++) c[i] = (p[(hi >> (2 * i)) & 3] & 0xffffff) | (three && ((hi >> (2 * i)) & 3) == 3 ? 0 : 0xff000000u);
}

/* The 16 values of a DXT5 alpha block (words lo, hi: two end points, then 3-bit indices) */
static void dxt_alpha(u32 lo, u32 hi, u32 a[16])
{
    u32 e0 = lo & 255, e1 = (lo >> 8) & 255, v[8], i;
    unsigned long long bits = (unsigned long long)(lo >> 16) | (unsigned long long)hi << 16;
    v[0] = e0, v[1] = e1;
    for (i = 2; i < 8; i++) v[i] = e0 > e1 ? ((8 - i) * e0 + (i - 1) * e1) / 7 : i < 6 ? ((6 - i) * e0 + (i - 1) * e1) / 5 : i == 6 ? 0 : 255;
    for (i = 0; i < 16; i++) a[i] = v[(bits >> (3 * i)) & 7];
}

/* The host copy of the texture in fetch slot i of the device, or 0. Which keys were decoded this frame is a table hashed by key, a slot taken from an
 * earlier frame when the key is new: the first table, 256 kept for the whole run, put every key after the 256th in one slot for the frame, so on Too
 * Human's menu they pushed each other out and were decoded again at almost every draw, 1.3 ms a draw (2026-11-05). s_tex_decodes counts the decodes */
static LONG64 s_tex_decodes, s_tex_texels, s_tex_same;
/* XB_VTX_STATS: why each decode happened (vtx stats line): a key not in the table (new, or pushed out), its bytes hashed differently from its last
 * decode, its bytes not hashable (not readable, or 256 MB and more), all 16 probed slots taken this frame; decodes and texels of each */
static LONG64 s_td_n[4], s_td_tx[4];

/* A hash of n bytes of guest memory at a (four lanes, so the multiplies overlap): a texture whose bytes hash as at its last decode is not decoded again.
 * Too Human's menu samples about 200 textures a frame, 80 million texels, and decoding each once a frame took 15 s of every 20 (2026-11-05) */
static unsigned long long data_hash(u32 a, size_t n)
{
    const unsigned long long *p = (const unsigned long long *)(tc_base + a), m = 0x100000001b3ull;
    unsigned long long h[4] = {0x9e3779b97f4a7c15ull, 0xc2b2ae3d27d4eb4full, 0x165667b19e3779f9ull, 0x27d4eb2f165667c5ull};
    size_t i, w = n / 8;
    for (i = 0; i + 4 <= w; i += 4) {
        h[0] = (h[0] ^ p[i]) * m;
        h[1] = (h[1] ^ p[i + 1]) * m;
        h[2] = (h[2] ^ p[i + 2]) * m;
        h[3] = (h[3] ^ p[i + 3]) * m;
    }
    for (; i < w; i++) h[0] = (h[0] ^ p[i]) * m;
    return (h[0] ^ (h[1] << 1 | h[1] >> 63) ^ (h[2] << 2 | h[2] >> 62) ^ (h[3] << 3 | h[3] >> 61)) | 1u;   /* never 0, which is "not hashed" */
}

/* XB_HASH_KEPT (on by default; =0 off, =N: ranges of N bytes and more, 16384 when unset or 1): data_hash's answer is kept per range for a
 * range no byte of which was written since it was hashed, which the guest memory's write generations decide over every page of the whole
 * range (xb_mem_write_gen: the reserve's write watch, which sees every writer). Never a sample or part of the range: a 4 KB check once kept a
 * stale picture (the blue title frame). The vertex and texture hashes read whole buffers every draw: 6.8 ms of every 123 ms gameplay frame
 * on build box A (2026-11-14). Each thread keeps its own table. XB_HASH_KEPT_CHECK=1: every kept answer is hashed again and compared, the
 * disagreements counted and logged every 100 frames (none is the proof) */
static size_t hash_kept_min(void)
{
    static long long m = -1;
    if (m < 0) {
        const char *e = getenv("XB_HASH_KEPT");
        long long n = e ? _atoi64(e) : 1;
        m = e && e[0] == '0' && !e[1] ? 0 : n <= 1 ? 16384 : n;
    }
    return (size_t)m;
}

static int hash_kept_check_on(void)
{
    static int on = -1;
    if (on < 0) { const char *e = getenv("XB_HASH_KEPT_CHECK"); on = e && e[0] == '1'; }
    return on;
}

typedef struct { u32 a, n; unsigned long long gen, h; } HashKept;
static __declspec(thread) HashKept t_hash_kept[4096];
static LONG64 s_hk_asks, s_hk_kept, s_hk_bad, s_hk_rehash_same;   /* XB_HASH_KEPT_CHECK: asks, answers kept, kept answers that differ, rehashes that found the same */

static unsigned long long data_hash_kept(u32 a, size_t n)
{
    size_t min = hash_kept_min();
    unsigned long long now = 0, top, h;
    HashKept *e;
    int check;
    if (!min || n < min || n > 0xffffffffu || (top = xb_mem_write_gen(a, (u32)n, &now)) == ~0ull) return data_hash(a, n);
    e = &t_hash_kept[((a >> 12) * 2654435761u ^ (u32)n * 40503u ^ a) & 4095u];
    check = hash_kept_check_on();
    if (check) InterlockedIncrement64(&s_hk_asks);
    if (e->a == a && e->n == (u32)n && e->gen && top <= e->gen) {   /* no page of the range written since it was hashed */
        if (check) {
            static unsigned said_frame = ~0u;
            InterlockedIncrement64(&s_hk_kept);
            if (data_hash(a, n) != e->h) InterlockedIncrement64(&s_hk_bad);
            if (s_frame % 100u == 0u && said_frame != s_frame) {
                said_frame = s_frame;
                port_log("hash kept check (XB_HASH_KEPT_CHECK) at frame %u: %lld asks, %lld kept, %lld kept answers differ from a fresh hash; %lld written ranges hashed the same\n",
                         s_frame, s_hk_asks, s_hk_kept, s_hk_bad, s_hk_rehash_same);
            }
        }
        return e->h;
    }
    h = data_hash(a, n);
    if (check && e->a == a && e->n == (u32)n && e->h == h) InterlockedIncrement64(&s_hk_rehash_same);
    e->a = a, e->n = (u32)n, e->gen = now, e->h = h;
    return h;
}
#define data_hash(a, n) data_hash_kept((a), (n))   /* every later caller (vertex keys, textures) asks the kept answer first */
/* Every Resolve's destination by the memory its fetch constant names (+0x20, as a fetch slot's second word): the host's Resolve copies into the host
 * texture of that header and writes no guest memory, so a texture the game samples there is that host copy. Too Human renders its frame to textures
 * (the composite into 4047f9e4, then a copy into the front buffer's target) and every frame read the empty memory: black. The hash of the first 4 KB
 * at the Resolve tells when the game has put other data there since */
static struct { u32 base, header; unsigned long long sum; int depth; } s_resolved_mem[128];   /* depth: a copy of the depth surface (XB_DEPTH_RESOLVE) */
static unsigned s_resolved_mem_next;

/* XB_DIAG_RESOLVE=frame, a diagnostic: every Resolve's destination header and memory, and every fetch that finds a Resolve's host copy, in that frame and
 * the next, at most 600 lines: which copy each pass samples */
static int resolve_diag(void)
{
    static long from = -2;
    static unsigned n;
    unsigned f;
    if (from == -2) { const char *e = getenv("XB_DIAG_RESOLVE"); from = e ? atol(e) : -1; }
    if (from < 0) return 0;
    f = xb_frame_number();
    return f >= (unsigned)from && f < (unsigned)from + 2u && n++ < 600u;
}

static unsigned long long resolved_sum(u32 base)
{
    u32 g = base | 0x20000000u;
    return guest_readable(g, 0x1000u) ? data_hash(g, 0x1000u) : 0ull;
}

static void resolved_note(u32 header, int depth)
{
    u32 base = rd32(header + 0x20u) & 0x1ffff000u;   /* the GPU's form: a header names 21a58000 where the slot the game samples names 01a58000 */
    unsigned k, at = ~0u;
    static int said;
    if (!base) return;
    if (said++ < 6) port_log("d3d Resolve into %08x: its memory %08x, kept for the textures sampled there\n", header, base);
    for (k = 0; k < 128u && at == ~0u; k++)
        if (s_resolved_mem[k].base == base) at = k;
    if (at == ~0u) at = s_resolved_mem_next++ & 127u;
    s_resolved_mem[at].base = base, s_resolved_mem[at].header = header, s_resolved_mem[at].sum = resolved_sum(base), s_resolved_mem[at].depth = depth;
    if (resolve_diag()) port_log("diag resolve frame %u: into %08x, memory %08x\n", xb_frame_number(), header, base);
}

/* XB_ZVIEW_COLOUR=1 brings back the old binding; unset or 0 (the default since 2026-11-14), a fetch in a depth format (22 k_24_8, 23 k_24_8_FLOAT)
 * over memory a colour Resolve copied last is not given that colour copy. Too Human's title and menu fog pass (vertex shader f425d113, pixel shader
 * fb4b1c78) reads its slot 0 as the scene's depth, 24_8_FLOAT at 019c8000, where each frame the game resolves render target 0 right after clearing
 * it to white (Resolve flags 100 into 4046f9b0, then flags 0 into 4046f9e4, both over 019c8000). The host's copy of that is white, depth 1
 * everywhere: the fog covered the whole frame, flat pale blue on the title and grey on the menu. Whether it was bound turned on the 4 KB hash below,
 * which other writes to that memory change in some runs and not in others: 2 title runs in 8 at vblank 280 (build box A, 2026-11-14). The slot is left
 * unbound now, as in the runs that looked right and the live pictures; the console gives the pass the real depth, which the host does not copy yet
 * (XB_DEPTH_RESOLVE, an experiment) */
static int zview_colour(void)
{
    static int on = -1;
    if (on < 0) { const char *e = getenv("XB_ZVIEW_COLOUR"); on = e && e[0] == '1'; }
    return on;
}

/* XB_ZVIEW_FORCE_BOUND=1, a diagnostic that never ships on: a depth-format fetch over a colour Resolve's copy skips the 4 KB written-since check, so
 * the copy is found every time (a fetch of another format that finds the memory written since gets no copy, as before, but no longer drops the
 * record, which the fog pass's own fetch then missed); with XB_ZVIEW_COLOUR=1 it is then bound every time (the pale-blue title frame on demand),
 * without it refused */
static int zview_force(void)
{
    static int on = -1;
    if (on < 0) { const char *e = getenv("XB_ZVIEW_FORCE_BOUND"); on = e && e[0] == '1'; }
    return on;
}

/* Each depth-format fetch over a colour copy, by its draw's shaders: the first time a pair meets one it is logged with the running count, so a
 * run's log names every draw that loses (or, under XB_ZVIEW_COLOUR=1, is given) a colour copy */
static void zview_note(unsigned slot, u32 base, u32 fmt, u32 header, int bound)
{
    static u32 pairs[64][2];
    static unsigned npairs, total;
    unsigned k;
    total++;
    for (k = 0; k < npairs; k++)
        if (pairs[k][0] == s_vs_container && pairs[k][1] == s_ps_container) break;
    if (k == npairs && npairs < 64) {
        pairs[npairs][0] = s_vs_container, pairs[npairs][1] = s_ps_container, npairs++;
        port_log("d3d zview frame %u: vs %08x ps %08x, fetch slot %u at %08x, depth format %u over the colour copy Resolve made into %08x: %s (pair %u, %u such fetches so far)\n",
                 xb_frame_number(), s_vs_container, s_ps_container, slot, base, fmt, header, bound ? "bound (XB_ZVIEW_COLOUR=1)" : "left unbound", npairs, total);
    }
    if (!(total % 500u)) port_log("d3d zview: %u depth-format fetches over colour copies so far, %u shader pairs\n", total, npairs);
}

static XbHostTex *resolved_texture(unsigned slot, u32 base, u32 fmt)
{
    unsigned k;
    static int said;
    for (k = 0; k < 128u; k++)
        if (s_resolved_mem[k].base == base) {
            if (!((fmt == 22 || fmt == 23) && zview_force()) && s_resolved_mem[k].sum != resolved_sum(base)) { if (resolve_diag()) port_log("diag fetch frame %u: slot %u at %08x, written since the Resolve into %08x: the game's own\n", xb_frame_number(), slot, base, s_resolved_mem[k].header); if (!zview_force()) s_resolved_mem[k].base = 0; return 0; }   /* written since: a texture of the game's own (kept under XB_ZVIEW_FORCE_BOUND) */
            if ((fmt == 22 || fmt == 23) && !s_resolved_mem[k].depth) {
                zview_note(slot, base, fmt, s_resolved_mem[k].header, zview_colour());
                if (!zview_colour()) return 0;
            }
            if (said++ < 8) port_log("d3d texture in fetch slot %u at %08x: the host copy Resolve made into %08x\n", slot, base, s_resolved_mem[k].header);
            if (resolve_diag()) port_log("diag fetch frame %u: slot %u at %08x, the host copy of %08x\n", xb_frame_number(), slot, base, s_resolved_mem[k].header);
            return host_tex(s_resolved_mem[k].header, 0);
        }
    return 0;
}

/* One face (or the only image) of w x h texels from base, rows pitch texels apart, into rgba: the slot's format, byte order and swizzle.
 * This is the reference route, texel by texel (XB_TEX_FAST=0 selects it); decode_image below picks it or the fast route */
static void decode_image_ref(u32 *rgba, u32 base, u32 w, u32 h, u32 pitch, u32 tiled, u32 fmt, u32 endian, u32 swz, u32 bw, u32 bpb)
{
    u32 x, y;
    for (y = 0; y < h; y += bw)
        for (x = 0; x < w; x += bw) {
            u32 bx = x / bw, by = y / bw, rowb = pitch / bw;
            u32 at = base + (tiled ? tiled_block(bx, by, rowb, bpb) : by * rowb + bx) * bpb, c[16], a[16], m;
            if (bw == 1) {
                u32 v = gpu_word(at & ~3u, endian), px;
                if (bpb == 1) v = (v >> (8 * (at & 3))) & 255, c[0] = v | v << 8 | v << 16 | v << 24;
                else if (bpb == 2) {
                    v = (v >> (8 * (at & 2))) & 0xffff;
                    c[0] = fmt == 4 ? rgb565(v) | 0xff000000u
                         : fmt == 3 ? ((v & 31) * 255 / 31) | (((v >> 5) & 31) * 255 / 31) << 8 | (((v >> 10) & 31) * 255 / 31) << 16 | (v >> 15 ? 0xff000000u : 0)
                         : fmt == 15 ? (v & 15) * 17 | ((v >> 4) & 15) * 17 << 8 | ((v >> 8) & 15) * 17 << 16 | (v >> 12) * 17 << 24
                         : (v & 255) | ((v >> 8) & 255) << 8 | 0xff000000u;   /* 10: two bytes */
                } else c[0] = fmt == 7 ? ((v & 1023) >> 2) | (((v >> 10) & 1023) >> 2) << 8 | (((v >> 20) & 1023) >> 2) << 16 | (v >> 30) * 85 << 24 : v;
                px = 0;
                for (m = 0; m < 4; m++) {   /* the slot's swizzle: 0..3 a component, 4 zero, 5 one */
                    u32 sel = (swz >> (3 * m)) & 7;
                    px |= (sel < 4 ? (c[0] >> (8 * sel)) & 255 : sel == 5 ? 255 : 0) << (8 * m);
                }
                rgba[(size_t)y * w + x] = px;
                continue;
            }
            if (fmt == 18) dxt_colours(gpu_word(at, endian), gpu_word(at + 4, endian), (gpu_word(at, endian) & 0xffff) <= (gpu_word(at, endian) >> 16), c);
            else if (fmt == 49) {   /* DXN: two DXT5 alpha blocks, x then y */
                dxt_alpha(gpu_word(at, endian), gpu_word(at + 4, endian), a);
                dxt_alpha(gpu_word(at + 8, endian), gpu_word(at + 12, endian), c);
                for (m = 0; m < 16; m++) c[m] = a[m] | c[m] << 8 | 0xff000000u;
            } else {
                dxt_colours(gpu_word(at + 8, endian), gpu_word(at + 12, endian), 0, c);
                if (fmt == 19) for (m = 0; m < 16; m++) a[m] = ((gpu_word(at + 4 * (m >> 3), endian) >> (4 * (m & 7))) & 15) * 17;
                else dxt_alpha(gpu_word(at, endian), gpu_word(at + 4, endian), a);
                for (m = 0; m < 16; m++) c[m] = (c[m] & 0xffffff) | a[m] << 24;
            }
            for (m = 0; m < 16; m++) {
                u32 px = 0, q, tx = x + (m & 3), ty = y + (m >> 2);
                if (tx >= w || ty >= h) continue;
                for (q = 0; q < 4; q++) {
                    u32 sel = (swz >> (3 * q)) & 7;
                    px |= (sel < 4 ? (c[m] >> (8 * sel)) & 255 : sel == 5 ? 255 : 0) << (8 * q);
                }
                rgba[(size_t)ty * w + tx] = px;
            }
        }
}

/* The fast route: the same output as decode_image_ref byte for byte (tests/test_x360_tex_fast.py), with the byte order a constant of the loop
 * (decode_core is instantiated once per order), the swizzle as four table lookups (skipped when it is the identity 0x688), a DXT block's words
 * read and swapped once and its texels stored a row at a time. The profile (2026-11-13) had decode_image at 50 ms a frame, 5 ms of it in
 * gpu_word's switch, which the reference runs per word */
typedef struct { u32 sh[4], mk[4], kc[4]; int ident; } XbSwz;

static void swz_init(XbSwz *t, u32 swz)
{
    u32 m;
    for (m = 0; m < 4; m++) {   /* 0..3 a component, 5 one, else zero: (c >> sh & mk) | kc */
        u32 sel = (swz >> (3 * m)) & 7;
        t->sh[m] = sel < 4 ? 8 * sel : 0;
        t->mk[m] = sel < 4 ? 255u : 0;
        t->kc[m] = sel == 5 ? 255u : 0;
    }
    t->ident = (swz & 0xfffu) == 0x688u;
}

static __forceinline u32 swz_apply(const XbSwz *t, u32 c)
{
    if (t->ident) return c;
    return (((c >> t->sh[0]) & t->mk[0]) | t->kc[0]) | ((((c >> t->sh[1]) & t->mk[1]) | t->kc[1]) << 8)
         | ((((c >> t->sh[2]) & t->mk[2]) | t->kc[2]) << 16) | ((((c >> t->sh[3]) & t->mk[3]) | t->kc[3]) << 24);
}

/* gpu_word of a raw little-endian word, the order e a constant */
static __forceinline u32 gw(u32 raw, const u32 e)
{
    return e == 0 ? raw : e == 1 ? (_byteswap_ulong(raw) >> 16) | (_byteswap_ulong(raw) << 16) : e == 3 ? (raw >> 16) | (raw << 16) : _byteswap_ulong(raw);
}

static __forceinline u32 raw_at(u32 a) { return *(const u32 *)(tc_base + a); }

/* One non-block texel at address at, as decode_image_ref's c[0]. Byte k of gpu_word(at & ~3) is the memory byte at ^ xm (xm 0, 1, 3, 2 for orders
 * 0, 1, 2, 3), so a byte or a halfword is read where it lies, with no word read and shift */
static __forceinline u32 texel_c(u32 fmt, u32 bpb, u32 at, const u32 e)
{
    const u32 xm = e == 0 ? 0u : e == 1 ? 1u : e == 2 ? 3u : 2u;
    if (bpb == 1) return (u32)tc_base[at ^ xm] * 0x01010101u;
    if (bpb == 2) {
        u32 v = *(const unsigned short *)(tc_base + (at ^ (xm & 2u)));
        if (xm & 1u) v = ((v & 255u) << 8) | (v >> 8);
        return fmt == 4 ? rgb565(v) | 0xff000000u
             : fmt == 3 ? ((v & 31) * 255 / 31) | (((v >> 5) & 31) * 255 / 31) << 8 | (((v >> 10) & 31) * 255 / 31) << 16 | (v >> 15 ? 0xff000000u : 0)
             : fmt == 15 ? (v & 15) * 17 | ((v >> 4) & 15) * 17 << 8 | ((v >> 8) & 15) * 17 << 16 | (v >> 12) * 17 << 24
             : (v & 255) | ((v >> 8) & 255) << 8 | 0xff000000u;
    }
    {
        u32 v = gw(raw_at(at), e);
        return fmt == 7 ? ((v & 1023) >> 2) | (((v >> 10) & 1023) >> 2) << 8 | (((v >> 20) & 1023) >> 2) << 16 | (v >> 30) * 85 << 24 : v;
    }
}

static __forceinline void decode_core(u32 *rgba, u32 base, u32 w, u32 h, u32 pitch, u32 tiled, u32 fmt, u32 swz, u32 bw, u32 bpb, const u32 e)
{
    XbSwz t;
    u32 x, y, rowb = pitch / bw;
    swz_init(&t, swz);
    if (bw == 1) {
        for (y = 0; y < h; y++) {
            u32 *dst = rgba + (size_t)y * w, row0 = base + y * rowb * bpb;
            for (x = 0; x < w; x++) {
                u32 at = tiled ? base + tiled_block(x, y, rowb, bpb) * bpb : row0 + x * bpb;
                dst[x] = swz_apply(&t, texel_c(fmt, bpb, at, e));
            }
        }
        return;
    }
    for (y = 0; y < h; y += 4)
        for (x = 0; x < w; x += 4) {
            u32 bx = x / 4, by = y / 4, c[16], a[16], m, r, q;
            u32 at = base + (tiled ? tiled_block(bx, by, rowb, bpb) : by * rowb + bx) * bpb;
            u32 w0 = gw(raw_at(at), e), w1 = gw(raw_at(at + 4), e), w2 = 0, w3 = 0;
            if (bpb == 16) w2 = gw(raw_at(at + 8), e), w3 = gw(raw_at(at + 12), e);
            if (fmt == 18) dxt_colours(w0, w1, (w0 & 0xffff) <= (w0 >> 16), c);
            else if (fmt == 49) {   /* DXN: two DXT5 alpha blocks, x then y */
                dxt_alpha(w0, w1, a);
                dxt_alpha(w2, w3, c);
                for (m = 0; m < 16; m++) c[m] = a[m] | c[m] << 8 | 0xff000000u;
            } else {
                dxt_colours(w2, w3, 0, c);
                if (fmt == 19) for (m = 0; m < 16; m++) a[m] = (((m >> 3) ? w1 : w0) >> (4 * (m & 7)) & 15) * 17;
                else dxt_alpha(w0, w1, a);
                for (m = 0; m < 16; m++) c[m] = (c[m] & 0xffffff) | a[m] << 24;
            }
            if (x + 4 <= w && y + 4 <= h) {
                for (r = 0; r < 4; r++) {
                    u32 *dst = rgba + (size_t)(y + r) * w + x;
                    for (q = 0; q < 4; q++) dst[q] = swz_apply(&t, c[r * 4 + q]);
                }
            } else
                for (m = 0; m < 16; m++) {
                    u32 tx = x + (m & 3), ty = y + (m >> 2);
                    if (tx < w && ty < h) rgba[(size_t)ty * w + tx] = swz_apply(&t, c[m]);
                }
        }
}

static void decode_image_fast(u32 *rgba, u32 base, u32 w, u32 h, u32 pitch, u32 tiled, u32 fmt, u32 endian, u32 swz, u32 bw, u32 bpb)
{
    switch (endian & 3u) {
    case 0: decode_core(rgba, base, w, h, pitch, tiled, fmt, swz, bw, bpb, 0); break;
    case 1: decode_core(rgba, base, w, h, pitch, tiled, fmt, swz, bw, bpb, 1); break;
    case 3: decode_core(rgba, base, w, h, pitch, tiled, fmt, swz, bw, bpb, 3); break;
    default: decode_core(rgba, base, w, h, pitch, tiled, fmt, swz, bw, bpb, 2); break;
    }
}

/* XB_TEX_FAST=0 keeps the reference route (read once) */
static void decode_image(u32 *rgba, u32 base, u32 w, u32 h, u32 pitch, u32 tiled, u32 fmt, u32 endian, u32 swz, u32 bw, u32 bpb)
{
    static int fast = -1;
    if (fast < 0) {
        const char *e = getenv("XB_TEX_FAST");
        fast = !(e && e[0] == '0');
    }
    if (fast) decode_image_fast(rgba, base, w, h, pitch, tiled, fmt, endian, swz, bw, bpb);
    else decode_image_ref(rgba, base, w, h, pitch, tiled, fmt, endian, swz, bw, bpb);
}

u32 xb_slot_fetch[16][4];
u32 xb_slot_fetch_w05[16][3];
u32 xb_tex_gamma[16];   /* each slot's gamma channels for the converted shader (xb_xenos_gamma.h xenos_gamma_mask), 0 when none or not decoded */

static XbHostTex *fetch_texture(unsigned i)
{
    static struct { u32 key, frame; unsigned long long sum; } seen[4096];
    unsigned long long sum = 0;
    static unsigned said[64];
    u32 f0 = rd32(s_fetch_dev + 0x480u + 24u * i), f1 = rd32(s_fetch_dev + 0x484u + 24u * i), f2 = rd32(s_fetch_dev + 0x488u + 24u * i);
    u32 f3 = rd32(s_fetch_dev + 0x48cu + 24u * i), f5 = rd32(s_fetch_dev + 0x494u + 24u * i);
    u32 fmt = f1 & 63, endian = (f1 >> 6) & 3, base = (f1 & ~0xfffu) | 0x20000000u, tiled = f0 >> 31, pitch = ((f0 >> 22) & 0x1ff) << 5;
    u32 w = (f2 & 0x1fff) + 1, h = ((f2 >> 13) & 0x1fff) + 1, swz = (f3 >> 1) & 0xfff, dim = (f5 >> 9) & 3, key, k, bw, bpb, face;
    u32 cube = dim == 3 && xb_cube_on(), stride = 0;
    u32 *rgba;
    XbHostTex *t;
    if (i < 16) xb_slot_fetch[i][0] = f1, xb_slot_fetch[i][1] = f2, xb_slot_fetch[i][2] = f3, xb_slot_fetch[i][3] = 0;
    if (i < 16) xb_slot_fetch_w05[i][0] = f0, xb_slot_fetch_w05[i][1] = f5, xb_slot_fetch_w05[i][2] = 1;
    if ((f0 & 3) != 2 || !(f1 & ~0xfffu)) return 0;   /* not a texture, or none set */
    if ((t = resolved_texture(i, f1 & 0x1ffff000u, fmt)) != 0) {
        static int said_rg;
        if (i < 16) xb_slot_fetch[i][3] = 2;
        if (xenos_gamma_mask(f0, swz) && !said_rg++) port_log("d3d texture gamma: slot %u reads a resolve's host copy with gamma channels (word 0 %08x), sampled as it is\n", i, f0);
        return t;
    }
    if (i < 16) {
        static int said_g;
        xb_slot_fetch[i][3] = 1;
        xb_tex_gamma[i] = xb_tex_gamma_on() ? xenos_gamma_mask(f0, swz) : 0;
        if (xb_tex_gamma[i] && !said_g++)
            port_log("d3d texture gamma on (XB_TEX_GAMMA=0 turns it off): slot %u format %u word 0 %08x swizzle %03x, channels %x to linear\n", i, fmt, f0, swz, xb_tex_gamma[i]);
    }
    bw = fmt == 18 || fmt == 19 || fmt == 20 || fmt == 49 ? 4 : 1;
    bpb = fmt == 2 ? 1 : fmt == 3 || fmt == 4 || fmt == 10 || fmt == 15 ? 2 : fmt == 6 || fmt == 7 ? 4 : fmt == 18 ? 8 : bw == 4 ? 16 : 0;
    if (cube) {   /* six faces, each a 2D slice: rows of the pitch in 32-block tiles, 32-row aligned, every face 4 KB aligned (Xenia texture_util.cc
                   * GetGuestTextureLayout, BSD-3-Clause, see data/NOTICE-xenia.txt). A base small enough to sit in the packed mip tail is not done */
        u32 rowb = ((pitch < w ? w : pitch) / bw + 31) & ~31u, rows = ((h + bw - 1) / bw + 31) & ~31u, lg = 0;
        while ((1u << lg) < (w < h ? w : h)) lg++;
        stride = (rowb * bpb * rows + 4095) & ~4095u;
        if (w != h || ((f5 >> 11) & 1 && lg <= 4)) {
            static int said_cube;
            if (said_cube++ < 4) port_log("d3d texture in fetch slot %u: cube %ux%u%s at %08x not done (%08x %08x %08x %08x %08x)\n", i, w, h, w != h ? " not square" : " with its base in the packed mip tail", base, f0, f1, f2, f3, f5);
            return 0;
        }
    }
    if (!bpb || (dim != 1 && !cube) || w > 4096 || h > 4096) {
        for (k = 0; k < 64 && said[k] && said[k] != (fmt | dim << 8) + 1; k++) {}
        if (k < 64 && !said[k]) {
            said[k] = (fmt | dim << 8) + 1;
            port_log("d3d texture in fetch slot %u: format %u, dimension %u, %ux%u has no case (%08x %08x %08x %08x %08x)\n", i, fmt, dim, w, h, f0, f1, f2, f3, f5);
        }
        return 0;
    }
    key = (f1 & ~0xfffu) | ((fmt * 0x9e5u + w * 0x2b3u + h * 0x47u + (cube ? 0x5a5u : 0)) & 0xfffu);   /* one memory read as 1280x720 and 640x360 is two textures */
    t = cube ? xb_gpu_tex_cube(key, w) : xb_gpu_tex_plain(key, w, h);
    if (xb_gpu_tex_w(t) != w || xb_gpu_tex_h(t) != h) {
        static int said_size;
        if (!said_size++) port_log("d3d texture in fetch slot %u: %ux%u at %08x meets another shape under one key, not bound\n", i, w, h, base);
        return 0;
    }
    {
        u32 h0 = (key * 2654435761u) >> 20, spare = ~0u, j;   /* 16 slots probed: the key's, else an empty one or one last used in an earlier frame */
        for (j = 0; j < 16; j++) {
            k = (h0 + j) & 4095u;
            if (seen[k].key == key && seen[k].key) break;
            if (spare == ~0u && (!seen[k].key || seen[k].frame != s_frame)) spare = k;
        }
        if (j < 16 && seen[k].frame == s_frame) return t;
        if (pitch < w) pitch = (w + 31) & ~31u;
        {   /* the bytes the decode reads: whole 32 x 32 tiles of blocks when tiled, else the rows */
            u32 rowb = pitch / bw, rows = (h + bw - 1) / bw;
            size_t bytes = cube ? (size_t)stride * 6 : tiled ? (size_t)((rowb + 31) & ~31u) * ((rows + 31) & ~31u) * bpb : (size_t)rowb * rows * bpb;
            if (bytes < 0x10000000u && guest_readable(base, (u32)bytes)) sum = data_hash(base, bytes);
        }
        if (j < 16 && sum && seen[k].sum == sum) {   /* unchanged since its last decode: the host copy stands */
            seen[k].frame = s_frame;
            s_tex_same++;
            return t;
        }
        {
            unsigned why = j == 16 ? (spare != ~0u ? 0u : 3u) : sum ? 1u : 2u;
            s_td_n[why]++, s_td_tx[why] += (LONG64)w * h;
        }
        if (j == 16) k = spare != ~0u ? spare : h0;   /* all 16 used this frame: decoded again, as the old table did */
        seen[k].key = key;
        seen[k].frame = s_frame;
        seen[k].sum = sum;
    }
    s_tex_decodes++;
    s_tex_texels += (LONG64)w * h;
    rgba = (u32 *)malloc((size_t)w * h * 4);
    if (!rgba) return t;
    if (cube) {
        for (face = 0; face < 6; face++) {
            decode_image(rgba, base + face * stride, w, h, pitch, tiled, fmt, endian, swz, bw, bpb);
            xb_gpu_upload_face(t, face, rgba, w * 4);
        }
        free(rgba);
        {
            static unsigned said_cube_n;
            if (said_cube_n++ < 8) port_log("d3d texture in fetch slot %u: cube %ux%u format %u%s, byte order %u, at %08x, faces %x bytes apart, swizzle %03x\n", i, w, h, fmt, tiled ? " tiled" : "", endian, base, stride, swz);
        }
        return t;
    }
    decode_image(rgba, base, w, h, pitch, tiled, fmt, endian, swz, bw, bpb);
    {   /* XB_DIAG_VS=hex: each texture decoded for that vertex shader's draws, its fetch constant and the alpha it decoded to (bring-up) */
        static u32 diag = ~0u;
        static unsigned said_diag;
        if (diag == ~0u) {
            const char *e = getenv("XB_DIAG_VS");
            diag = e ? (u32)strtoul(e, 0, 16) : 0;
        }
        if (diag && s_vs_container == diag && said_diag < 8) {
            size_t n = (size_t)w * h, m, a0 = 0, a255 = 0, g0 = 0, g255 = 0;
            for (m = 0; m < n; m++) {
                u32 a = rgba[m] >> 24, g = (rgba[m] >> 8) & 255u;
                a0 += a == 0, a255 += a == 255;
                g0 += g == 0, g255 += g == 255;
            }
            said_diag++;
            port_log("diag texture slot %u for %08x/%08x at frame %u: %ux%u format %u%s, byte order %u, swizzle %03x, at %08x, constant %08x %08x %08x %08x %08x %08x; alpha 0 in %.1f%%, 255 in %.1f%%, between %.1f%%; green 0 in %.1f%%, 255 in %.1f%%; first texel %08x\n",
                     i, s_vs_container, s_ps_container, s_frame, w, h, fmt, tiled ? " tiled" : "", endian, swz, base, f0, f1, f2, f3, rd32(s_fetch_dev + 0x490u + 24u * i), f5,
                     100.0 * a0 / n, 100.0 * a255 / n, 100.0 * (n - a0 - a255) / n, 100.0 * g0 / n, 100.0 * g255 / n, rgba[0]);
        }
    }
    xb_gpu_upload(t, rgba, w * 4);
    free(rgba);
    stage_dump("fetch", key, 1);
    {
        static unsigned said_n;
        if (said_n++ < 24) port_log("d3d texture in fetch slot %u: %ux%u format %u%s, byte order %u, at %08x, swizzle %03x\n", i, w, h, fmt, tiled ? " tiled" : "", endian, base, swz);
    }
    (void)said;
    return t;
}
