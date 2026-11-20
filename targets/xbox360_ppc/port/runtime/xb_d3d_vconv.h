/* xb_d3d.c, the conversion of a draw's vertices into the host's rows of four floats (one translation unit with it; here for the 400-line cap).
 * vtx_convert_old is the loop draw_vertices always had: every element read and converted where it is. With XB_VTX_OLD=1 (xb_d3d_tex.h) it is the
 * only path. XB_VTX_CHECK=1 converts every draw again with it, cached or not, and counts the bytes that differ from what goes to the device */

/* A draw's host rows. A draw that may be kept (it has a key) gets its own block, which the vertex cache then owns; any other converts into this
 * thread's pool, grown as a draw needs (the calloc and free of every draw's block went with them). Both conversions write every byte of n * nat * 16;
 * a draw with no inputs has its n rows zeroed, as calloc gave */
static __declspec(thread) float *s_vpool;
static __declspec(thread) size_t s_vpool_n;

static float *vtx_out(unsigned n, unsigned nat, int own)
{
    size_t nf = (size_t)n * (nat ? nat : 1) * 4;
    float *v;
    if (own) v = (float *)malloc(nf * sizeof(float) + 16);
    else {
        if (nf > s_vpool_n) {
            free(s_vpool);
            s_vpool_n = nf > 2 * s_vpool_n ? nf : 2 * s_vpool_n, s_vpool = (float *)malloc(s_vpool_n * sizeof(float) + 16);
        }
        v = s_vpool;
    }
    if (!v) xb_stop("d3d_draw", "no memory for a draw's vertices", (u32)nf);
    if (!nat) memset(v, 0, nf * sizeof(float));
    return v;
}

static void vtx_out_done(float *v)
{
    if (v != s_vpool) free(v);
}

static void vtx_convert_old(const XbDecl *d, unsigned n, unsigned nat, u32 p, u32 stride, float *v)
{
    unsigned i, k;
    for (i = 0; i < n; i++) {
        for (k = 0; k < nat; k++) {
            float *o = v + ((size_t)i * nat + k) * 4;
            u32 at = p + i * stride + d->el[k].offset, fmt = d->el[k].type & 0x3F, c;
            o[3] = 1.0f;
            if (d == &s_from_fetch) {   /* the selects: 0..3 a component, 4 zero, 5 one; 7 kept, which the HLSL never reads */
                float f[4];
                unsigned m;
                if (s_from_stream[k]) at = s_from_index[k] ? stream_indexed(k, v + (size_t)i * nat * 4, i) : stream_vertex(s_from_stream[k], i, d->el[k].offset, s_from_w2[k]);
                if (!at) f[0] = f[1] = f[2] = 0.0f, f[3] = 1.0f;
                else if (!fetch_components(at, s_from_w1[k], f)) { free(v); xb_stop("d3d_draw", "a vertex fetch format the host has no case for", s_from_w1[k]); }
                for (m = 0; m < 4; m++) {
                    u32 sel = (s_from_w1[k] >> (3 * m)) & 7u;
                    o[m] = sel < 4 ? f[sel] : sel == 5 ? 1.0f : 0.0f;
                }
            } else if (fmt >= 36 && fmt <= 38) {   /* 32-bit float, 1 to 4 of them */
                unsigned m, nc = fmt == 38 ? 4 : fmt - 35;   /* 36 one, 37 two, 38 four */
                for (m = 0; m < 4; m++) o[m] = m < nc ? 0.0f : (m == 3 ? 1.0f : 0.0f);
                for (m = 0; m < nc; m++) {
                    u32 w = rd32(at + 4 * m);
                    memcpy(&o[m], &w, 4);
                }
            } else if (fmt == 57) {          /* three 32-bit floats */
                unsigned m;
                for (m = 0; m < 3; m++) {
                    u32 w = rd32(at + 4 * m);
                    memcpy(&o[m], &w, 4);
                }
            } else if (fmt == 6) {           /* four 8-bit unsigned normalised; D3DCOLOR is ARGB in the word */
                c = rd32(at);
                o[0] = ((c >> 16) & 255) / 255.0f;
                o[1] = ((c >> 8) & 255) / 255.0f;
                o[2] = (c & 255) / 255.0f;
                o[3] = ((c >> 24) & 255) / 255.0f;
            } else {
                free(v);
                xb_stop("d3d_draw", "a vertex element format the host has no case for", d->el[k].type);
            }
        }
    }
}

/* The per-draw conversion of a draw with no declaration (Too Human's: the fetches its Bind patched, s_from_fetch). The old loop asked, for every
 * element of every vertex, what its fetch reads: the format through fetch_components' switch, a 16-bit float through ldexpf, another stream's
 * fetch constant from the device and whether its element is committed memory (stream_element, guest_readable). Here each fetch is planned once a
 * draw: its format and selects; another stream's base, with all the elements the draw reaches found committed in one ask (else the old loop
 * converts the draw, and logs as it did); an indexed fetch keeps stream_indexed, with its reach asked about once (s_ix_reach). An indexed draw's
 * stream 0 is read where the gather would copy it from (s_lazy), so the copy is skipped, unless an element runs past the stride. Half floats and
 * the 8-bit formats come from tables filled by half_value and fetch_components' own expressions, so every value is the same float.
 * 0: the draw has something the plan does not cover, and the caller uses vtx_convert_old */
#pragma optimize("t", on)   /* the kit builds /O1; these few hot functions for speed (/fp:strict still holds) */
typedef struct { u32 src, base, sz, off, fmt, nc, t; u32 sel[4]; } VxPlan;   /* src: 0 stream 0 at p, 3 stream 0 at the gather's source, 1 another stream, 2 an indexed fetch */

static float s_vx_half[65536], s_vx_byte[4][256];   /* s_vx_byte[signed | integer << 1][byte] */
static INIT_ONCE s_vx_once = INIT_ONCE_STATIC_INIT;

static BOOL CALLBACK vx_tables(PINIT_ONCE once, PVOID arg, PVOID *ctx)
{
    u32 h, b, t;
    (void)once, (void)arg, (void)ctx;
    for (h = 0; h < 65536u; h++) s_vx_half[h] = half_value(h);
    for (t = 0; t < 4u; t++)
        for (b = 0; b < 256u; b++) {   /* fetch_components' case 6, per component */
            float c = (t & 1u) ? (float)(signed char)b : (float)b;
            if (!(t & 2u)) c = (t & 1u) ? (c < -127.0f ? -1.0f : c / 127.0f) : c / 255.0f;
            s_vx_byte[t][b] = c;
        }
    return TRUE;
}

static __forceinline void vx_decode(const VxPlan *e, u32 at, float c[4])   /* fetch_components for a planned format */
{
    u32 i, x;
    c[0] = c[1] = c[2] = 0.0f;
    c[3] = 1.0f;
    switch (e->fmt) {
    case 36: case 37: case 38: case 57:
        for (i = 0; i < e->nc; i++) { x = rd32(at + 4 * i); memcpy(&c[i], &x, 4); }
        return;
    case 31: case 32:
        for (i = 0; i < e->nc; i++) c[i] = s_vx_half[rd32(at + 4 * (i >> 1)) >> (16 * (i & 1)) & 0xffffu];
        return;
    case 25: case 26:
        for (i = 0; i < e->nc; i++) {
            x = rd32(at + 4 * (i >> 1)) >> (16 * (i & 1)) & 0xffffu;
            c[i] = (e->t & 1u) ? (float)(short)x : (float)x;
            if (!(e->t & 2u)) c[i] = (e->t & 1u) ? (c[i] < -32767.0f ? -1.0f : c[i] / 32767.0f) : c[i] / 65535.0f;
        }
        return;
    default:   /* 6 */
        x = rd32(at);
        for (i = 0; i < 4; i++) c[i] = s_vx_byte[e->t][(x >> (8 * i)) & 255u];
        return;
    }
}

/* XB_VTX_CONV (a developer switch): unset, the planned inputs are converted by xb_vconv_run (xb_vconv.c, one input over every vertex); "old", by the
 * loop below, vertex by vertex, as at d508d40c: the answer key. vtx_convert_fast's loop stays for XB_VFETCH_CHECK and XB_VTX_REUSE=1, which watch each row */
static int vtx_conv_new(void)
{
    static int on = -1;
    if (on < 0) { const char *e = getenv("XB_VTX_CONV"), *f = getenv("XB_VFETCH_CHECK"); on = !(e && !strcmp(e, "old")) && !(f && f[0] == '1') && !vtx_old(); }
    return on;
}

/* XB_VTX_UNIQUE (on unless 0): an indexed draw whose every input is read by its vertex number (stream 0 at the gather's source, other streams by the
 * draw's indices, an indexed fetch by an earlier input of the same vertex or one index for all) converts each vertex it names once, not once an index
 * entry (line 75 of xb_vconv.c was 10.7 ms of the 19.8 a gameplay frame on build box A, 2026-11-14): the rows of its distinct vertices in first-use order, and the
 * host draws them by an index buffer of each entry's row (xb_gpu_draw_indexed). A row there is a function of its vertex number alone, so entry i gets the
 * row of vindex[i] either way. Not taken for quad lists and fans (the host expands those from the rows), draws in order, a gathered block (an element past
 * the stride), XB_VTX_REUSE's tracked rows, or a draw whose entries are all distinct: those convert every entry, as =0 does for all (the path at 734e682e).
 * XB_VTX_CHECK rebuilds every entry's row from the rows and the remap and compares those bytes */
static int vtx_unique_on(void)
{
    static int on = -1;
    if (on < 0) { const char *e = getenv("XB_VTX_UNIQUE"); on = !(e && e[0] == '0') && !vtx_old(); }
    return on;
}

static __declspec(thread) struct { u32 *ix, *uv, *map; unsigned cap_n, cap_span, nu; const u32 *at; } s_vu;   /* nu: the draw's rows (0: one an entry), at: its remap */

static u32 vc_slow(unsigned k, u32 index)   /* an index past the reach asked about once: the element as stream_indexed finds it */
{
    return stream_element(s_from_stream[k], index, s_from_fetch.el[k].offset, s_from_w2[k]);
}

static void vconv_planned(const VxPlan *q, unsigned n, unsigned nat, u32 p, u32 stride, float *v)
{
    XbVcEl el[32];
    unsigned k, m, b;
    memset(el, 0, sizeof el);
    for (k = 0; k < nat; k++) {
        const VxPlan *e = &q[k];
        XbVcEl *x = &el[k];
        u32 s = s_from_index[k];
        x->nc = e->nc, x->t = e->t, x->k = k;
        x->kind = e->fmt == 31 || e->fmt == 32 ? 1u : e->fmt == 25 || e->fmt == 26 ? 2u : e->fmt == 6 ? 3u : 0u;
        if (e->src == 2) {
            x->mode = 2, x->round = (s_from_w1[k] >> 15) & 1u, x->reach = s_ix_reach[k], x->ibase = s_ix_base[k] + e->off, x->isz = e->sz;
            x->isrc = (s & 0x100u) ? nat : s - 1u, x->icomp = (s & 0x100u) ? 0u : s_from_icomp[k];
            x->ix = (s & 0x100u) ? (float)(s & 1u) : 0.0f;   /* an input not converted yet read as the zeroed buffer's 0 */
            if (x->isrc >= k) x->isrc = nat, x->icomp = 0;
        } else {
            u32 step = e->src == 1 ? e->sz : stride, base = (e->src == 0 ? p : e->base) + e->off;
            x->step = step;
            if (e->src == 0) x->mode = 0, x->base = base;
            else if (s_vindex) x->mode = 1, x->base = base;
            else x->mode = 0, x->base = base + s_vfirst * step;
        }
        for (m = 0; m < 4; m++) {
            u32 sel = e->sel[m];
            int lane = sel < 4 && (x->kind != 0 || sel < e->nc);   /* a component read from the element */
            for (b = 0; b < 4; b++) x->mask[4 * m + b] = lane ? (u8)(x->kind == 0 ? 4 * sel + 3 - b : 4 * sel + b) : 0x80u;
            x->one[m] = (sel == 5 || (sel == 3 && !lane)) ? 0x3f800000u : 0u;   /* 1.0: select 5, or the fourth component a format leaves out */
            x->absent[m] = sel == 5 || sel == 3 ? 0x3f800000u : 0u;               /* an absent element is (0, 0, 0, 1) */
        }
    }
    if (vtx_unique_on() && s_vindex && s_lazy.vok && nat && n > 3u && (s_draw_prim == 4u || s_draw_prim == 6u) && s_lazy.vhi - s_lazy.vlo < 4u * n) {
        for (k = 0; k < nat && el[k].mode != 0; k++) {}
        if (k == nat) {   /* every input by the vertex number (mode 1), or an indexed fetch from those (mode 2) */
            u32 span = s_lazy.vhi - s_lazy.vlo + 1u, nu;
            if (n > s_vu.cap_n) {
                free(s_vu.ix), free(s_vu.uv);
                s_vu.cap_n = n + n / 2u, s_vu.ix = (u32 *)malloc((size_t)s_vu.cap_n * 4u), s_vu.uv = (u32 *)malloc((size_t)s_vu.cap_n * 4u);
            }
            if (span > s_vu.cap_span) free(s_vu.map), s_vu.cap_span = span + span / 2u, s_vu.map = (u32 *)malloc((size_t)s_vu.cap_span * 4u);
            if (!s_vu.ix || !s_vu.uv || !s_vu.map) xb_stop("d3d_draw", "no memory for a draw's distinct vertices", n);
            nu = xb_vconv_unique(s_vindex, n, s_lazy.vlo, span, s_vu.map, s_vu.uv, s_vu.ix);
            if (nu < n) {
                xb_vconv_run(el, nat, nu, s_vu.uv, v, s_vx_half, (const float (*)[256])s_vx_byte, vc_slow);
                s_vu.nu = nu, s_vu.at = s_vu.ix;
                return;
            }
        }
    }
    xb_vconv_run(el, nat, n, s_vindex, v, s_vx_half, (const float (*)[256])s_vx_byte, vc_slow);
}

static int vtx_convert_fast(unsigned n, unsigned nat, u32 p, u32 stride, float *v)
{
    VxPlan q[32];
    unsigned i, k, m, have_span = 0, direct = s_lazy.n != 0 && s_vindex && s_lazy.stride == stride;
    u32 lo = ~0u, hi = 0;
    if (nat > 32) return 0;
    InitOnceExecuteOnce(&s_vx_once, vx_tables, 0, 0);
    for (k = 0; k < nat; k++) {
        VxPlan *e = &q[k];
        u32 w1 = s_from_w1[k], rb;
        unsigned long long a0, a1;
        e->fmt = (w1 >> 16) & 63u, e->t = ((w1 >> 12) & 1u) | ((w1 >> 13) & 1u) << 1, e->off = s_from_fetch.el[k].offset, e->sz = (s_from_w2[k] & 255u) * 4u;
        switch (e->fmt) {
        case 36: e->nc = 1, rb = 4; break;
        case 37: e->nc = 2, rb = 8; break;
        case 38: e->nc = 4, rb = 16; break;
        case 57: e->nc = 3, rb = 12; break;
        case 31: e->nc = 2, rb = 4; break;
        case 32: e->nc = 4, rb = 8; break;
        case 25: e->nc = 2, rb = 4; break;
        case 26: e->nc = 4, rb = 8; break;
        case 6: e->nc = 4, rb = 4; break;
        default: goto old;   /* no case: the old loop stops with its message */
        }
        for (m = 0; m < 4; m++) e->sel[m] = (w1 >> (3 * m)) & 7u;
        if (!s_from_stream[k]) {
            e->src = 0;
            if (e->off + rb > stride) direct = 0;   /* reads into the next gathered vertex: only the gathered block has those bytes */
            continue;
        }
        e->base = s_fetch_dev ? rd32(s_fetch_dev + 8u * (0xefu - s_from_stream[k])) & 0x1ffffffcu : 0;
        if (!e->base) goto old;   /* no fetch constant: the old loop, which logs it */
        e->base |= 0x20000000u;
        if (s_from_index[k]) {
            u32 s = s_from_index[k], f = (s & 0x100u) ? 0u : (s_from_w1[s - 1u] >> 16) & 63u;
            u32 reach = (s & 0x100u) ? (s & 1u) + 1u : f == 6 ? 256u : (f == 25 || f == 26) ? 65536u : 0u;
            e->src = 2;
            a0 = (unsigned long long)e->base + e->off, a1 = a0 + (unsigned long long)(reach ? reach - 1u : 0u) * e->sz + 16u;
            if (reach && a1 <= 0x100000000ull && a1 - a0 <= 0x4000000u && guest_readable((u32)a0, (u32)(a1 - a0))) s_ix_reach[k] = reach, s_ix_base[k] = e->base;
            continue;
        }
        e->src = 1;
        if (!have_span) {   /* the draw's lowest and highest vertex number, once */
            have_span = 1;
            if (!s_vindex && s_vfirst + (n - 1u) >= s_vfirst) lo = s_vfirst, hi = s_vfirst + (n - 1u);
            else if (s_vindex && s_lazy.vok) lo = s_lazy.vlo, hi = s_lazy.vhi;
            else
                for (i = 0; i < n; i++) {
                    u32 vi = s_vindex ? s_vindex[i] : s_vfirst + i;
                    if (vi < lo) lo = vi;
                    if (vi > hi) hi = vi;
                }
        }
        a0 = (unsigned long long)e->base + (unsigned long long)lo * e->sz + e->off, a1 = (unsigned long long)e->base + (unsigned long long)hi * e->sz + e->off + 16u;
        if (lo > hi || a1 > 0x100000000ull || a1 - a0 > 0x4000000u || !guest_readable((u32)a0, (u32)(a1 - a0))) goto old;   /* one element not committed: the old loop logs it */
    }
    if (direct) {
        for (k = 0; k < nat; k++)
            if (q[k].src == 0) q[k].src = 3, q[k].base = s_lazy.vb;
    } else if (s_lazy.n) lazy_gather();
    if (vtx_conv_new() && !s_ix_track) vconv_planned(q, n, nat, p, stride, v);   /* one input over every vertex (xb_vconv.c) */
    else for (i = 0; i < n; i++) {
        u32 vi = s_vindex ? s_vindex[i] : s_vfirst + i;
        for (k = 0; k < nat; k++) {
            const VxPlan *e = &q[k];
            float *o = v + ((size_t)i * nat + k) * 4, f[4];
            u32 at = e->src == 0 ? p + i * stride + e->off : e->src == 3 ? e->base + vi * stride + e->off : e->src == 1 ? e->base + vi * e->sz + e->off
                   : stream_indexed(k, v + (size_t)i * nat * 4, i);
            if (!at) f[0] = f[1] = f[2] = 0.0f, f[3] = 1.0f;
            else vx_decode(e, at, f);
            for (m = 0; m < 4; m++) o[m] = e->sel[m] < 4 ? f[e->sel[m]] : e->sel[m] == 5 ? 1.0f : 0.0f;
        }
    }
    memset(s_ix_reach, 0, sizeof s_ix_reach);
    return 1;
old:
    memset(s_ix_reach, 0, sizeof s_ix_reach);
    return 0;
}
#pragma optimize("", on)

/* Every byte of n at a, whatever n (vc_span's tail left bytes 0..1 out when n is 6, a 3-index draw), and n itself */
static unsigned long long vx_span(u32 a, u32 n)
{
    unsigned long long t = 0;
    u32 r = n & 7u, j;
    for (j = 0; j < r; j++) t = t << 8 | rd8(a + (n - r) + j);
    return vc_mix(vc_mix(data_hash(a, n), t), n);
}

/* vcache_key with the per-vertex work taken out. vcache_key hashed the draw's vertex numbers one at a time (a multiply chain a vertex) and walked
 * them again for the lowest and highest once per stream read from another buffer: on Too Human's menu, about 1.3 million vertices a frame, that was
 * vcache_key's 6.9 ms of the game thread (build box A, 2026-11-13). The numbers are fixed by less, so the key stands on that:
 *  - in order (no s_vindex): first + i for i < n, so the first vertex and n;
 *  - an indexed draw whose indices are hashed in s_lazy.hash (every byte, vx_span): that hash and the base vertex (s_vindex[i] = base + index i);
 *  - else (an index span too wide to hash) the numbers one by one, as before.
 * The lowest and highest come once a draw, from the gather's own loop when it had them. Every other input is hashed exactly as vcache_key does */
static __declspec(thread) unsigned long long s_vr_key;   /* XB_VTX_REUSE: the draw's key without its palette rows (K), 0 when it reads none */

static unsigned long long vcache_key_fast(const XbDecl *d, unsigned n, unsigned nat, u32 p, u32 stride)
{
    unsigned long long h = 0xcbf29ce484222325ull, ix;
    unsigned k, i, have_span = 0, have_ix = 0, rows = 0;
    u32 lo = ~0u, hi = 0;
    s_vr_key = 0;
    if (!n || (unsigned long long)n * stride > 0x4000000u || !guest_readable(p, n * stride)) return 0;
    h = vc_mix(vc_mix(vc_mix(h, s_lazy.hash ? s_lazy.hash : vc_bytes(p, n * stride)), (unsigned long long)n << 32 | nat), (unsigned long long)stride << 32 | s_vs_container);
    h = vc_mix(h, d == &s_from_fetch);
    for (k = 0; k < nat; k++) {
        u32 base, sz, at;
        h = vc_mix(h, (unsigned long long)d->el[k].offset << 32 | d->el[k].type);
        if (d != &s_from_fetch) continue;
        h = vc_mix(h, (unsigned long long)s_from_w1[k] << 32 | s_from_w2[k]);
        if (!s_from_stream[k]) continue;
        base = s_fetch_dev ? rd32(s_fetch_dev + 8u * (0xefu - s_from_stream[k])) & 0x1ffffffcu : 0;
        h = vc_mix(h, (unsigned long long)s_from_stream[k] << 32 | base);
        if (!base) continue;   /* read as absent, as stream_vertex does */
        sz = (s_from_w2[k] & 255u) * 4u;
        if (s_from_index[k]) {   /* an indexed fetch: all its index can reach (an 8-bit index 256 elements, a 16-bit one 65536), else not kept */
            u32 s = s_from_index[k], f = (s & 0x100u) ? 0u : (s_from_w1[s - 1u] >> 16) & 63u;
            u32 reach = (s & 0x100u) ? (s & 1u) + 1u : f == 6 ? 256u : (f == 25 || f == 26) ? 65536u : 0u;
            h = vc_mix(h, (unsigned long long)s << 32 | s_from_icomp[k]);
            at = base | 0x20000000u;
            if (vtx_reuse()) {   /* the rows it reads are hashed by vrows_key, every byte of them; their rows come from the bytes hashed here */
                if (!reach) return 0;
                h = vc_mix(h, (unsigned long long)reach << 32 | sz), rows = 1;
                continue;
            }
            if (!reach || (unsigned long long)reach * sz > 0x1000000u || !guest_readable(at, reach * sz + 16u)) return 0;
            h = vc_mix(vc_mix(h, (unsigned long long)reach << 32 | sz), vc_bytes(at, reach * sz + 16u));
            continue;
        }
        if (!have_span) {   /* the draw's lowest and highest vertex number, once */
            have_span = 1;
            if (!s_vindex && s_vfirst + (n - 1u) >= s_vfirst) lo = s_vfirst, hi = s_vfirst + (n - 1u);
            else if (s_vindex && s_lazy.vok) lo = s_lazy.vlo, hi = s_lazy.vhi;
            else
                for (i = 0; i < n; i++) {
                    u32 v = s_vindex ? s_vindex[i] : s_vfirst + i;
                    if (v < lo) lo = v;
                    if (v > hi) hi = v;
                }
        }
        at = (base | 0x20000000u) + lo * sz;
        if ((unsigned long long)(hi - lo + 1u) * sz > 0x1000000u || !guest_readable(at, (hi - lo + 1u) * sz + 16u)) return 0;
        h = vc_mix(vc_mix(h, (unsigned long long)lo << 32 | sz), vc_bytes(at, (hi - lo + 1u) * sz + 16u));
        if (!have_ix) {   /* the vertex numbers, once */
            have_ix = 1;
            if (!s_vindex) ix = vc_mix(vc_mix(0x5d0f1a2b3c4d5e6full, s_vfirst), n);
            else if (s_lazy.vok && s_lazy.hash) ix = vc_mix(vc_mix(0x6e1f2a3b4c5d6e7full, s_lazy.hash), s_lazy.base);
            else
                for (ix = 0x84222325cbf29ce4ull, i = 0; i < n; i++) ix = vc_mix(ix, s_vindex[i]);
            h = vc_mix(h, ix);
        }
    }
    if (rows) s_vr_key = h | 1u;
    return h | 1u;
}

/* XB_VTX_REUSE: the palette rows a draw read. A draw with an indexed fetch is keyed by vcache_key_fast without the palette (K: the vertex bytes, layout,
 * fetch words, shader, each palette's address) and then by every byte of the rows its conversion read, kept per K in s_vr. Which rows a draw reads follows
 * from the bytes in K (the index is an earlier fetch's converted value) and, for an index read from a palette, from rows in the ranges: so equal bytes there
 * give the same conversion. A draw whose rows are not known yet, or that read a row outside the ranges kept or an absent one, is converted and not kept;
 * the next one is. Each range is the union of every row read from one palette, offset and 16 bytes included */
#define VR_SLOTS 4096u
typedef struct { unsigned long long k; u32 n, base[8], a0[8], a1[8]; } VxRows;
static VxRows s_vr[VR_SLOTS];
static __declspec(thread) VxRows s_vr_cur;    /* the rows the key was made from (s_vr_known) */
static __declspec(thread) int s_vr_known;
static LONG64 s_vr_new;   /* draws whose rows were not known (XB_VTX_STATS) */

static void vrows_begin(unsigned nat)   /* before a draw's conversion: rows tracked by stream_indexed */
{
    unsigned k;
    for (k = 0; k < nat && k < 32; k++) s_ix_lo[k] = ~0u, s_ix_hi[k] = 0;
    s_ix_absent = 0, s_ix_track = s_vr_key != 0;
}

static unsigned long long vrows_key(unsigned long long key)   /* K and the rows' bytes, or 0 (rows not known: converted, not kept) */
{
    unsigned i = (unsigned)(key >> 52) & (VR_SLOTS - 1u), j, found = 0;
    unsigned long long h = key;
    AcquireSRWLockShared(&s_vc_lock);
    for (j = 0; j < 8 && !found; j++, i = (i + 1u) & (VR_SLOTS - 1u))
        if (s_vr[i].k == key) s_vr_cur = s_vr[i], found = 1;
    ReleaseSRWLockShared(&s_vc_lock);
    s_vr_known = 0;
    if (!found) { s_vr_new++; return 0; }
    for (j = 0; j < s_vr_cur.n; j++) {
        u32 a = s_vr_cur.a0[j], len = s_vr_cur.a1[j] - a;
        if (len > 0x1000000u || !guest_readable(a, len)) return 0;
        h = vc_mix(h, vx_span(a, len));
    }
    s_vr_known = 1;
    return h | 1u;
}

static int vrows_note(unsigned nat)   /* after the conversion: 1 when it read only rows inside the ranges its key hashed; else the ranges are kept for the next draw */
{
    VxRows r;
    unsigned k, j, in = 1;
    s_ix_track = 0;
    if (s_ix_absent) return 0;   /* a row past committed memory: as before, such a draw is not kept */
    memset(&r, 0, sizeof r);
    r.k = s_vr_key;
    for (k = 0; k < nat && k < 32; k++) {
        u32 base, sz, a0, a1;
        if (!s_from_stream[k] || !s_from_index[k] || s_ix_lo[k] > s_ix_hi[k]) continue;
        base = (rd32(s_fetch_dev + 8u * (0xefu - s_from_stream[k])) & 0x1ffffffcu) | 0x20000000u, sz = (s_from_w2[k] & 255u) * 4u;
        if ((unsigned long long)base + (unsigned long long)s_ix_hi[k] * sz + s_from_fetch.el[k].offset + 16u > 0x100000000ull) return 0;
        a0 = base + s_ix_lo[k] * sz + s_from_fetch.el[k].offset, a1 = base + s_ix_hi[k] * sz + s_from_fetch.el[k].offset + 16u;
        for (j = 0; j < r.n && r.base[j] != base; j++) {}
        if (j == r.n) {
            if (r.n == 8) return 0;
            r.base[j] = base, r.a0[j] = a0, r.a1[j] = a1, r.n++;
        } else {
            if (a0 < r.a0[j]) r.a0[j] = a0;
            if (a1 > r.a1[j]) r.a1[j] = a1;
        }
    }
    for (j = 0; j < r.n && in; j++) {   /* inside the ranges the key hashed */
        for (k = 0; k < s_vr_cur.n && !(s_vr_cur.base[k] == r.base[j] && s_vr_cur.a0[k] <= r.a0[j] && r.a1[j] <= s_vr_cur.a1[k]); k++) {}
        in = k < s_vr_cur.n;
    }
    if (s_vr_known && in) return 1;
    {
        unsigned i = (unsigned)(r.k >> 52) & (VR_SLOTS - 1u), at = i;
        AcquireSRWLockExclusive(&s_vc_lock);
        for (j = 0; j < 8; j++, i = (i + 1u) & (VR_SLOTS - 1u))
            if (s_vr[i].k == r.k || !s_vr[i].k) { at = i; break; }
        s_vr[at] = r;   /* a full probe overwrites its first slot */
        ReleaseSRWLockExclusive(&s_vc_lock);
    }
    return 0;
}

/* XB_VTX_CHECK=1: every draw's rows (kept or converted now) against vtx_convert_old's fresh conversion of the guest memory as it is at the draw.
 * Counted per run and logged every 50 frames: draws, vertices, bytes compared, bytes and draws that differ, and a hash of all bytes of each side */
static int vtx_check_on(void)
{
    static int on = -1;
    if (on < 0) { const char *e = getenv("XB_VTX_CHECK"); on = e && (*e == '1' || *e == '2') ? *e - '0' : 0; }   /* 2: only the draws converted now */
    return on;
}

static struct { LONG64 draws, verts, bytes, bad_bytes, bad_draws, hits, uniq; unsigned long long h_new, h_ref; unsigned window; } s_vx_chk = {0, 0, 0, 0, 0, 0, 0, 0, 0, ~0u};

static unsigned long long vx_host_hash(unsigned long long h, const float *v, size_t bytes)   /* every byte of the host rows */
{
    const unsigned char *b = (const unsigned char *)v;
    size_t i;
    for (i = 0; i + 8 <= bytes; i += 8) {
        unsigned long long w;
        memcpy(&w, b + i, 8);
        h = vc_mix(h, w);
    }
    for (; i < bytes; i++) h = vc_mix(h, b[i]);
    return vc_mix(h, bytes);
}

static void vtx_check_draw(const XbDecl *d, unsigned n, unsigned nat, u32 p, u32 stride, const float *v, int hit)   /* v: the draw's rows (s_vu: its distinct rows) */
{
    size_t bytes = (size_t)n * (nat ? nat : 1) * 16u, i, bad = 0;   /* the whole block, a draw with no inputs' zeroed rows too */
    float *r, *e = 0;
    if (hit && vtx_check_on() == 2) return;
    if (s_lazy.n) lazy_gather();   /* the old loop reads stream 0 from the gathered block */
    r = (float *)calloc((size_t)n * (nat ? nat : 1) * 4, sizeof(float));
    vtx_convert_old(d, n, nat, p, stride, r);
    if (s_vu.nu) {   /* XB_VTX_UNIQUE: each entry's row rebuilt from the distinct rows and the remap, the stream the host's index buffer draws */
        if (!(e = (float *)malloc(bytes))) xb_stop("d3d_draw", "no memory for the vertex check", (u32)bytes);
        for (i = 0; i < n; i++) memcpy(e + i * nat * 4u, v + (size_t)s_vu.at[i] * nat * 4u, (size_t)nat * 16u);
        v = e, s_vx_chk.uniq++;
    }
    for (i = 0; i < bytes; i++) bad += ((const unsigned char *)v)[i] != ((const unsigned char *)r)[i];
    s_vx_chk.draws++, s_vx_chk.verts += n, s_vx_chk.bytes += (LONG64)bytes, s_vx_chk.hits += hit;
    s_vx_chk.h_new = vx_host_hash(s_vx_chk.h_new, v, bytes), s_vx_chk.h_ref = vx_host_hash(s_vx_chk.h_ref, r, bytes);
    if (bad) {
        s_vx_chk.bad_bytes += (LONG64)bad, s_vx_chk.bad_draws++;
        if (s_vx_chk.bad_draws <= 12) {
            for (i = 0; i < bytes && ((const unsigned char *)v)[i] == ((const unsigned char *)r)[i]; i++) {}
            port_log("vtx check MISMATCH frame %u: %u bytes of %u differ (%s, %u vertices, %u inputs, stride %u, shader %08x, %s); first at vertex %u input %u: new %08x ref %08x\n",
                     s_frame, (unsigned)bad, (unsigned)bytes, hit ? "kept" : "converted", n, nat, stride, s_vs_container, s_vindex ? "indexed" : "in order",
                     (unsigned)(i / 16u / (nat ? nat : 1)), (unsigned)(i / 16u % (nat ? nat : 1)), ((const u32 *)v)[i / 4], ((const u32 *)r)[i / 4]);
        }
    }
    free(r), free(e);
    if (s_frame / 50u != s_vx_chk.window) {
        s_vx_chk.window = s_frame / 50u;
        port_log("vtx check to frame %u (%s path): %lld draws (%lld kept, %lld from distinct rows), %lld vertices, %lld bytes compared, %lld bytes differ in %lld draws; hash new %016llx ref %016llx\n",
                 s_frame, vtx_old() ? "old" : "new", (long long)s_vx_chk.draws, (long long)s_vx_chk.hits, (long long)s_vx_chk.uniq, (long long)s_vx_chk.verts, (long long)s_vx_chk.bytes,
                 (long long)s_vx_chk.bad_bytes, (long long)s_vx_chk.bad_draws, s_vx_chk.h_new, s_vx_chk.h_ref);
    }
}

/* XB_VTX_STATS=1: the vertex cache's hits and misses, logged every 50 frames for the 50 just ended (one test of a flag a draw when off). Draws and
 * vertices drawn say whether two runs are on the same scene; kept against converted says how often the key found the last frames' rows. Converted
 * vertices are split by why they were not kept (no key, no room) and by whether the draw reads an indexed fetch (a bone palette, hashed whole) */
static int vtx_stats_on(void)
{
    static int on = -1;
    if (on < 0) { const char *e = getenv("XB_VTX_STATS"); on = e && *e == '1'; }
    return on;
}

static struct { LONG64 draws, verts, hit_draws, hit_verts, conv_draws, conv_verts, nokey, full, ixf_draws, ixf_verts, u_draws, u_ents, u_rows; unsigned window, from; } s_vx_st = {0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, ~0u, 0};

static void vtx_stats_draw(const XbDecl *d, unsigned n, unsigned nat, unsigned long long key, int kept, int hit)
{
    unsigned k, ixf = 0;
    if (s_frame / 50u != s_vx_st.window) {
        if (s_vx_st.window != ~0u)
            port_log("vtx stats frames %u-%u (%s path%s): %lld draws %lld vertices; kept %lld draws %lld vertices; converted %lld draws %lld vertices (%.0f a frame; %lld no key, %lld no room; %lld draws %lld vertices reading an indexed fetch; palette rows not known yet %lld draws in all; distinct rows %lld draws, %lld entries as %lld rows)\n",
                     s_vx_st.from, s_frame - 1u, vtx_old() ? "old" : "new", vtx_reuse() ? ", reuse" : "", (long long)s_vx_st.draws, (long long)s_vx_st.verts, (long long)s_vx_st.hit_draws, (long long)s_vx_st.hit_verts,
                     (long long)s_vx_st.conv_draws, (long long)s_vx_st.conv_verts, (double)s_vx_st.conv_verts / (double)(s_frame > s_vx_st.from ? s_frame - s_vx_st.from : 1u),
                     (long long)s_vx_st.nokey, (long long)s_vx_st.full, (long long)s_vx_st.ixf_draws, (long long)s_vx_st.ixf_verts, (long long)s_vr_new,
                     (long long)s_vx_st.u_draws, (long long)s_vx_st.u_ents, (long long)s_vx_st.u_rows);
        if (s_vx_st.window != ~0u) {   /* the textures of the same frames, and why each decode happened (xb_d3d_tex.h fetch_texture) */
            static LONG64 dec_was, same_was;
            port_log("tex stats frames %u-%u: %lld decodes, %lld unchanged and kept; new key %lld (%lld texels), bytes changed %lld (%lld texels), not hashable %lld (%lld texels), table full %lld (%lld texels)\n",
                     s_vx_st.from, s_frame - 1u, (long long)(s_tex_decodes - dec_was), (long long)(s_tex_same - same_was), (long long)s_td_n[0], (long long)s_td_tx[0],
                     (long long)s_td_n[1], (long long)s_td_tx[1], (long long)s_td_n[2], (long long)s_td_tx[2], (long long)s_td_n[3], (long long)s_td_tx[3]);
            dec_was = s_tex_decodes, same_was = s_tex_same;
            memset(s_td_n, 0, sizeof s_td_n), memset(s_td_tx, 0, sizeof s_td_tx);
        }
        memset(&s_vx_st, 0, sizeof s_vx_st);
        s_vx_st.window = s_frame / 50u, s_vx_st.from = s_frame;
    }
    s_vx_st.draws++, s_vx_st.verts += n;
    if (hit) { s_vx_st.hit_draws++, s_vx_st.hit_verts += n; return; }
    s_vx_st.conv_draws++, s_vx_st.conv_verts += n;
    if (s_vu.nu) s_vx_st.u_draws++, s_vx_st.u_ents += n, s_vx_st.u_rows += s_vu.nu;
    if (!key) s_vx_st.nokey += n;
    else if (!kept) s_vx_st.full += n;
    if (d == &s_from_fetch)
        for (k = 0; k < nat; k++) ixf |= s_from_stream[k] && s_from_index[k];
    if (ixf) s_vx_st.ixf_draws++, s_vx_st.ixf_verts += n;
}
