/* xb_d3d.c, a draw's vertex layout and the converted vertices kept (one translation unit with it; here for the 400-line cap) */

static XbDecl s_from_fetch;
static u32 s_from_w1[32], s_from_w2[32], s_from_stream[32];   /* each fetch's second word as the game's Bind patched it: its format and selects are applied here, the converted HLSL reads inputs whole */

/* Developer switches, not player settings. Too Human's gameplay converted vertices for 102 ms of each 210 ms frame (build box A profile, 2026-11-14).
 * XB_VTX_REUSE=1 (off unless 1): a draw that reads a bone palette (an indexed fetch) is kept by the palette rows it read (every byte of them,
 * xb_d3d_vconv.h vrows_key), not by every row its index could reach. Measured: 1% fewer vertices converted in gameplay (skinned rows change every
 * frame), so off.
 * XB_VTX_MEMO (on unless 0): draw_decl finds the declaration and the fetches the game patched only when their inputs changed since the last draw
 * (7.7 ms a gameplay frame before). vfetch_row_check (XB_VFETCH_CHECK, bring-up) is not called for every row read while it is off (8 ms) */
static int vtx_reuse(void)
{
    static int on = -1;
    if (on < 0) { const char *e = getenv("XB_VTX_REUSE"); on = e && e[0] == '1' && !vtx_old(); }
    return on;
}
static int vtx_memo(void)
{
    static int on = -1;
    if (on < 0) { const char *e = getenv("XB_VTX_MEMO"); on = !(e && e[0] == '0') && !vtx_old(); }
    return on;
}
static int s_vchk_live = 1;   /* cleared by vfetch_row_check's first call when XB_VFETCH_CHECK is off */
static __declspec(thread) u32 s_ix_lo[32], s_ix_hi[32], s_ix_absent, s_ix_track;   /* the rows each indexed fetch read in this draw (lo > hi: none) while s_ix_track */

/* Indexed vertex fetches. A fetch's source register is its element index; most read r0.x, the vertex index, but Too Human's skinned meshes read
 * a bone palette from another stream by the bone indices an earlier fetch gave: the tree of its title (f61653a1) fetches the indices into r3 and
 * then three rows a bone from stream 15 by r3.w, r3.z, r3.y and r3.x. Read at the vertex index, the rows were other bones' or none (past the
 * palette). The 2026-11-07 census of the 303 made vertex shaders: 177 index a fetch by an earlier fetch, none by a register the ALU
 * wrote, so the earlier fetch's converted value is the index. s_from_index[k]: 0 the vertex index; m + 1 fetch m's component s_from_icomp[k] as
 * its selects give it; 0x100 | v the constant v (a select of 4 or 5). The index is floored, or rounded to the nearest when the fetch says so
 * (second word bit 15), as Xenia's dxbc_shader_translator_fetch.cc does (data/NOTICE-xenia.txt).
 * On by default since 2026-11-08; XB_VFETCH_INDEX=0 turns it off (the old reads at the vertex index), XB_VFETCH_INDEX_ALT=n turns it on and
 * off every n frames (pictures side by side). Title runs on build box A (2026-11-07/08, 300 s, switching every 5 or 10 frames): with it on the
 * hall's skinned walls and pillars show, the tree reads its bones' rows at indices 0..74, and XB_VFETCH_CHECK=1 found no zero or wild palette
 * row in the title hall (frames 25..499) and no sampled row written between its draw and the Swap. The large white planes are on off frames
 * only (the old reads past the palette). After the title (frames 500 on) some palettes hold 0xffc00000 (NaN) rows the game had not written */
static u32 s_from_index[32], s_from_icomp[32];

static int vfetch_index_on(void)
{
    static int on = -1, alt;
    if (on < 0) {
        const char *e = getenv("XB_VFETCH_INDEX"), *a = getenv("XB_VFETCH_INDEX_ALT");
        alt = a ? atoi(a) : 0, on = !(e && e[0] == '0') || alt > 0;
    }
    return on && (alt <= 0 || !((s_frame / (unsigned)alt) & 1u));
}

static void vfetch_index_sources(unsigned n, u32 code, const XbShader *vs)
{
    static u32 said[8];
    unsigned k, m, idx = 0, first = 0, on = (unsigned)vfetch_index_on();
    for (k = 0; k < n; k++) {
        u32 w0 = rd32(code + vs->fetch[k] - 1), src = (w0 >> 5) & 63u, c = w0 >> 30, sel = 7;
        s_from_index[k] = s_from_icomp[k] = 0;
        for (m = k; m-- > 0;)   /* the last earlier fetch that wrote that component */
            if ((rd32(code + vs->fetch[m] - 1) >> 12 & 63u) == src && (sel = (s_from_w1[m] >> (3 * c)) & 7u) != 7u) break;
        if (m < k && on) {
            s_from_index[k] = sel < 4 ? m + 1u : 0x100u | (sel == 5);
            s_from_icomp[k] = c;
            if (!idx++) first = k;
        } else if (m >= k && (src || c) && on) {
            static int said_odd;
            if (said_odd++ < 4) port_log("d3d draw: shader %08x fetch %u reads its index from r%u.%c, which no earlier fetch wrote: read at the vertex index\n", s_vs_container, k, src, "xyzw"[c]);
        }
    }
    if (idx) {
        for (m = 0; m < 8 && said[m] && said[m] != s_vs_container; m++) {}
        if (m < 8 && !said[m]) {
            said[m] = s_vs_container;
            port_log("d3d draw: shader %08x reads %u of its %u vertex fetches at an index an earlier fetch gives (fetch %u: fetch %u's %c, stream %u, %u bytes apart)\n", s_vs_container, idx, n, first,
                     s_from_index[first] & 0x100u ? 0u : s_from_index[first] - 1u, "xyzw"[s_from_icomp[first]], s_from_stream[first], (s_from_w2[first] & 255u) * 4u);
        }
    }
}

/* What the indexed fetches read, per vertex shader, logged every 25 frames (bring-up, XB_VFETCH_CHECK=1): the rows read (each 16 bytes of a
 * bone's 48), how many are all zero or near it (|xyz| under 0.05), how many are wild (not finite, a component past 1e5 or |xyz| past 50), the
 * highest index and the palettes (stream bases) seen. A bone palette's rows are a rotation's (|xyz| near 1) with a translation */
static struct { u32 vs, rows, zero, wild, top, bases, base; u32 first_bad_idx, first_bad_at, first_bad_v; } s_vchk[16];
static unsigned s_vchk_window = ~0u;
/* Rows sampled at draw time (every 16th) and read again at the frame's Swap: a row that changed between its draw and the Swap was written after
 * the draw read it, as the console's GPU (reading the palette when it reaches the draw, later) would not see. Counted per window in s_vre_* */
static struct { u32 at, w[4]; u32 wild; } s_vre[1024];
static unsigned s_vre_n, s_vre_tick, s_vre_rows, s_vre_changed, s_vre_was_wild, s_vre_now_sane;

static void vfetch_row_recheck(void)   /* at Swap (xb_d3d_draw.h) */
{
    unsigned i, w;
    for (i = 0; i < s_vre_n; i++) {
        u32 at = s_vre[i].at, ch = 0;
        for (w = 0; w < 4; w++) ch |= rd32(at + 4 * w) != s_vre[i].w[w];
        s_vre_rows++;
        if (ch) {
            float f[4], l;
            for (w = 0; w < 4; w++) { u32 x = rd32(at + 4 * w); memcpy(&f[w], &x, 4); }
            l = sqrtf(f[0] * f[0] + f[1] * f[1] + f[2] * f[2]);
            s_vre_changed++;
            if (s_vre[i].wild) s_vre_was_wild++, s_vre_now_sane += l == l && l <= 50.0f && fabsf(f[3]) < 1e5f;
        }
    }
    s_vre_n = 0;
}

static void vfetch_row_check(unsigned k, u32 idx, u32 at, unsigned i)
{
    static int on = -1;
    unsigned j;
    float f[4], l;
    u32 base, w;
    if (on < 0) { const char *e = getenv("XB_VFETCH_CHECK"); on = e && e[0] == '1'; }
    if (!on) { s_vchk_live = 0; return; }
    if (s_frame / 25u != s_vchk_window) {   /* a new window: the last one's numbers */
        for (j = 0; j < 16 && s_vchk[j].vs; j++)
            if (s_vchk[j].rows)
                port_log("vfetch check frames %u..%u: shader %08x %u rows, %u zero, %u wild, highest index %u, %u palette bases (last %08x)%s\n",
                         s_vchk_window * 25u, s_vchk_window * 25u + 24u, s_vchk[j].vs, s_vchk[j].rows, s_vchk[j].zero, s_vchk[j].wild, s_vchk[j].top,
                         s_vchk[j].bases, s_vchk[j].base, "");
        if (s_vre_rows)
            port_log("vfetch check frames %u..%u: %u sampled rows read again at Swap, %u changed since their draw read them (%u of those were wild then, %u of those sane at Swap)\n",
                     s_vchk_window * 25u, s_vchk_window * 25u + 24u, s_vre_rows, s_vre_changed, s_vre_was_wild, s_vre_now_sane);
        s_vre_rows = s_vre_changed = s_vre_was_wild = s_vre_now_sane = 0;
        for (j = 0; j < 16 && s_vchk[j].vs; j++)
            if (s_vchk[j].zero + s_vchk[j].wild)
                port_log("vfetch check frames %u..%u: shader %08x first bad row: index %u at %08x, vertex %u\n", s_vchk_window * 25u, s_vchk_window * 25u + 24u,
                         s_vchk[j].vs, s_vchk[j].first_bad_idx, s_vchk[j].first_bad_at, s_vchk[j].first_bad_v);
        memset(s_vchk, 0, sizeof s_vchk);
        s_vchk_window = s_frame / 25u;
    }
    for (j = 0; j < 16 && s_vchk[j].vs && s_vchk[j].vs != s_vs_container; j++) {}
    if (j == 16) return;
    s_vchk[j].vs = s_vs_container;
    base = s_fetch_dev ? rd32(s_fetch_dev + 8u * (0xefu - s_from_stream[k])) & 0x1ffffffcu : 0;
    if (base != s_vchk[j].base) s_vchk[j].bases++, s_vchk[j].base = base;
    if (idx > s_vchk[j].top) s_vchk[j].top = idx;
    s_vchk[j].rows++;
    for (l = 0, w = 0; w < 4; w++) {
        u32 x = rd32(at + 4 * w);
        memcpy(&f[w], &x, 4);
    }
    l = sqrtf(f[0] * f[0] + f[1] * f[1] + f[2] * f[2]);
    if (!(s_vre_tick++ & 15u) && s_vre_n < 1024u) {
        s_vre[s_vre_n].at = at, memcpy(s_vre[s_vre_n].w, f, 16);
        s_vre[s_vre_n++].wild = !(l == l) || !(fabsf(f[3]) < 1e5f) || l > 50.0f || !(fabsf(f[0]) < 1e5f);
    }
    if (!(l == l) || !(fabsf(f[3]) < 1e5f) || l > 50.0f || !(fabsf(f[0]) < 1e5f)) s_vchk[j].wild++;
    else if (l < 0.05f) s_vchk[j].zero++;
    else return;
    if (s_vchk[j].zero + s_vchk[j].wild == 1) {
        static unsigned said;
        u32 e = at - (at - (base | 0x20000000u)) % ((s_from_w2[k] & 255u) ? (s_from_w2[k] & 255u) * 4u : 4u), s = s_from_index[k];
        s_vchk[j].first_bad_idx = idx, s_vchk[j].first_bad_at = at, s_vchk[j].first_bad_v = s_vindex ? s_vindex[i] : s_vfirst + i;
        if (said < 24 && guest_readable(e - 16u, 64u)) {   /* what a bad row holds: the 12 words of its element, the 4 before it, the fetch constant's two words and the index source's */
            said++;
            port_log("vfetch check bad row: shader %08x fetch %u frame %u index %u at %08x (element %08x, palette %08x), constant %08x %08x; before %08x %08x %08x %08x | %08x %08x %08x %08x | %08x %08x %08x %08x | %08x %08x %08x %08x; index from fetch %u word %08x %08x\n",
                     s_vs_container, k, s_frame, idx, at, e, base, s_fetch_dev ? rd32(s_fetch_dev + 8u * (0xefu - s_from_stream[k])) : 0, s_fetch_dev ? rd32(s_fetch_dev + 8u * (0xefu - s_from_stream[k]) + 4u) : 0,
                     rd32(e - 16), rd32(e - 12), rd32(e - 8), rd32(e - 4), rd32(e), rd32(e + 4), rd32(e + 8), rd32(e + 12), rd32(e + 16), rd32(e + 20), rd32(e + 24), rd32(e + 28),
                     rd32(e + 32), rd32(e + 36), rd32(e + 40), rd32(e + 44), (s & 0x100u) ? 0u : s - 1u, (s & 0x100u) ? 0u : s_from_w1[s - 1u], (s & 0x100u) ? 0u : s_from_w2[s - 1u]);
        }
    }
}

/* Set by vtx_convert_fast for one draw, 0 otherwise: fetch k's stream base (with 0x20000000) whose elements 0..s_ix_reach[k]-1 were found committed
 * in one ask, so an index under the reach takes its element there with no ask (the address stream_element gives) */
static __declspec(thread) u32 s_ix_reach[32], s_ix_base[32];   /* per thread, as the fetch device is */

/* The element an indexed fetch k reads for the vertex whose inputs so far are row (each four floats), as stream_vertex gives a plain one's */
static u32 stream_indexed(unsigned k, const float *row, unsigned i)
{
    u32 s = s_from_index[k];
    float x = (s & 0x100u) ? (float)(s & 1u) : row[4u * (s - 1u) + s_from_icomp[k]];
    u32 at;
    static unsigned said;
    x = ((s_from_w1[k] >> 15) & 1u) ? floorf(x + 0.5f) : floorf(x);
    if (!(x >= 0.0f && x < 16777216.0f)) return 0;   /* read as absent */
    if ((u32)x < s_ix_reach[k]) at = s_ix_base[k] + (u32)x * ((s_from_w2[k] & 255u) * 4u) + s_from_fetch.el[k].offset;
    else at = stream_element(s_from_stream[k], (u32)x, s_from_fetch.el[k].offset, s_from_w2[k]);
    if (s_ix_track) {
        if (!at) s_ix_absent |= s_fetch_dev && (rd32(s_fetch_dev + 8u * (0xefu - s_from_stream[k])) & 0x1ffffffcu);   /* a palette row not committed (no palette: absent, as the key says) */
        else {
            if ((u32)x < s_ix_lo[k]) s_ix_lo[k] = (u32)x;
            if ((u32)x > s_ix_hi[k]) s_ix_hi[k] = (u32)x;
        }
    }
    if (at && s_vchk_live) vfetch_row_check(k, (u32)x, at, i);
    if (at && said < 48) {   /* each shader's highest index so far, when it grows: the index, the source's whole value and the element (bring-up) */
        static u32 vs_of[16];
        static float top[16];
        unsigned j;
        for (j = 0; j < 16 && vs_of[j] && vs_of[j] != s_vs_container; j++) {}
        if (j == 16 || (vs_of[j] && x <= top[j])) return at;
        vs_of[j] = s_vs_container, top[j] = x;
    }
    if (at && said < 48) {
        const float *src = (s & 0x100u) ? 0 : row + 4u * (s - 1u);
        u32 a = rd32(at), b = rd32(at + 4), c = rd32(at + 8), e = rd32(at + 12);
        float fa, fb, fc, fe;
        memcpy(&fa, &a, 4), memcpy(&fb, &b, 4), memcpy(&fc, &c, 4), memcpy(&fe, &e, 4);
        port_log("d3d indexed fetch %u of %08x at frame %u: index %g (fetch %u %g %g %g %g, its word %08x), element %08x: %g %g %g %g; stream %u constant %08x %08x, %u vertices\n", k, s_vs_container, s_frame, x,
                 (s & 0x100u) ? 0u : s - 1u, src ? src[0] : 0.0f, src ? src[1] : 0.0f, src ? src[2] : 0.0f, src ? src[3] : 0.0f,
                 (s & 0x100u) ? 0u : s_from_w1[s - 1u], at, fa, fb, fc, fe, s_from_stream[k], rd32(s_fetch_dev + 8u * (0xefu - s_from_stream[k])),
                 rd32(s_fetch_dev + 8u * (0xefu - s_from_stream[k]) + 4u), s_draw_n);
        said++;
    }
    return at;
}

/* The layout of a draw's vertices: the declaration bound, or with none the fetches the game patched into the shader. 0: the draw is skipped */
/* The last declaration made at guest address g, or 0. Declarations are only added (each at its own new address), so with the same g and the same count
 * the answer is the last one's (XB_VTX_MEMO): the walk over every declaration made, about 390 bytes apart, ran at every draw */
static XbDecl *decl_find(u32 g)
{
    static __declspec(thread) u32 last_g, last_n;
    static __declspec(thread) XbDecl *last_d;
    unsigned i, n = s_decl_n;
    XbDecl *d = 0;
    if (n && n == last_n && g == last_g && vtx_memo()) return last_d;
    for (i = 0; i < n; i++)
        if (s_decl[i].guest == g) d = &s_decl[i];
    last_g = g, last_n = n, last_d = d;
    return d;
}

/* draw_decl's fetches from the shader (s_from_*) are filled again only when what they are read from changed: the shader, its microcode's address, the fetch
 * words the game patched, the index switch. s_from_gen counts the fills, so a fill by another thread is seen */
static volatile LONG s_from_gen;
static __declspec(thread) struct { const XbShader *vs; u32 bound, code, container, on, n, gen; u32 w[96]; } s_from_memo;

static int from_fetch_same(const XbShader *vs, u32 code)
{
    unsigned k, n = vs->n_inputs < 32 ? vs->n_inputs : 32;
    u32 on = (u32)vfetch_index_on();
    int same = vtx_memo() && s_from_memo.vs == vs && s_from_memo.bound == s_bound_vs && s_from_memo.code == code && s_from_memo.container == s_vs_container
               && s_from_memo.on == on && s_from_memo.n == n && s_from_memo.gen == (u32)s_from_gen;
    for (k = 0; k < n; k++) {
        u32 at = code + vs->fetch[k] - 1, a = rd32(at), b = rd32(at + 4), c = rd32(at + 8);
        same &= s_from_memo.w[3 * k] == a && s_from_memo.w[3 * k + 1] == b && s_from_memo.w[3 * k + 2] == c;
        s_from_memo.w[3 * k] = a, s_from_memo.w[3 * k + 1] = b, s_from_memo.w[3 * k + 2] = c;
    }
    if (same) return 1;
    s_from_memo.vs = vs, s_from_memo.bound = s_bound_vs, s_from_memo.code = code, s_from_memo.container = s_vs_container, s_from_memo.on = on, s_from_memo.n = n;
    s_from_memo.gen = (u32)InterlockedIncrement(&s_from_gen);
    return 0;
}

static XbDecl *draw_decl(const XbShader *vs, u32 p, u32 stride)
{
    XbDecl *d = 0;
    static int said_stride;
    static u32 said_from[8];
    unsigned i;
    d = decl_find(s_bound_decl);
    if (!d && s_draw_decl) {   /* the deferred BeginVertices draw: the declaration the device held when it began (th_draw_state) */
        u32 g = s_draw_decl;
        static int said;
        d = decl_find(g);
        if (!d && said++ < 4)
            port_log("d3d draw: declaration %08x is not one the kit made (%u made): %08x %08x %08x %08x %08x %08x %08x %08x | %08x %08x %08x %08x %08x %08x %08x %08x\n", g, s_decl_n,
                g ? rd32(g) : 0, g ? rd32(g + 4) : 0, g ? rd32(g + 8) : 0, g ? rd32(g + 12) : 0, g ? rd32(g + 16) : 0, g ? rd32(g + 20) : 0, g ? rd32(g + 24) : 0, g ? rd32(g + 28) : 0,
                g ? rd32(g + 32) : 0, g ? rd32(g + 36) : 0, g ? rd32(g + 40) : 0, g ? rd32(g + 44) : 0, g ? rd32(g + 48) : 0, g ? rd32(g + 52) : 0, g ? rd32(g + 56) : 0, g ? rd32(g + 60) : 0);
    }
    if (!d && vs && vs->fetch[0]) {   /* no declaration (Too Human binds none): the vertex fetches the game patched in the shader's microcode */
        u32 code = rd32(s_bound_vs + 0x20);
        d = &s_from_fetch;
        if (from_fetch_same(vs, code)) return d;   /* filled from the same words at the last draw */
        for (d->n = 0; d->n < vs->n_inputs && d->n < 32; d->n++) {
            u32 at = code + vs->fetch[d->n] - 1, w1 = rd32(at + 4), w2 = rd32(at + 8);
            d->el[d->n].offset = (u16)((w2 >> 8) << 2);
            d->el[d->n].type = (w1 >> 16) & 0x3F;
            s_from_w1[d->n] = w1;
            s_from_w2[d->n] = w2;
            s_from_stream[d->n] = 95u - ((rd32(at) >> 20 & 31u) * 3u + (rd32(at) >> 25 & 3u));   /* fetch constant 95 - s is stream s */
            if (d->n == 0 && said_from[7] == 0 && said_from[0] != s_vs_container && said_from[1] != s_vs_container && said_from[2] != s_vs_container
                && said_from[3] != s_vs_container && said_from[4] != s_vs_container && said_from[5] != s_vs_container && said_from[6] != s_vs_container) {   /* which game routine draws with no declaration, what it hands over and the fetch it left (bring-up) */
                char st[160];
                for (i = 0; said_from[i]; i++) {}
                said_from[i] = s_vs_container;
                xb_stack_line(st, sizeof st);
                port_log("d3d draw with no declaration, shader %08x (header %08x, decl %08x):%s | fetch %08x %08x %08x | vertex %08x %08x %08x %08x %08x %08x\n", s_vs_container, s_bound_vs, s_draw_decl, st,
                         rd32(at), w1, w2, rd32(p), rd32(p + 4), rd32(p + 8), rd32(p + 12), rd32(p + 16), rd32(p + 20));
            }
            if ((w2 & 255) * 4 != stride && !said_stride++) port_log("d3d draw: vertex fetch %u of shader %08x has stride %u, the draw %u\n", d->n, s_vs_container, (w2 & 255) * 4, stride);
        }
        vfetch_index_sources(d->n, code, vs);
    }
    if (!d && !vs) {   /* no declaration, and the shader has no converted copy to read fetches from (Too Human, 82990808 under 8299d110): skipped as
                        * made_shaders_converted skips, a known gap while the pack is part converted */
        static unsigned skipped;
        xb_fcap_skip("no declaration and the vertex shader has no converted copy", s_vs_container, s_ps_container);
        if (!skipped++ || skipped % 1000 == 0)
            port_log("d3d draw skipped (%u so far): no declaration, vertex shader %08x (container %08x) has no converted copy\n", skipped, s_bound_vs, s_vs_container);
        return 0;
    }
    if (!d) xb_stop("d3d_draw", "a draw with no vertex declaration bound", s_bound_decl);
    if (!vs) xb_stop("d3d_draw", "a draw with no converted vertex shader", s_vs_container);
    return vs ? d : 0;
}

/* Converted vertices kept by what they were made from, so a mesh drawn again unchanged is not converted again (Too Human's menu: about 1.3 million
 * vertices a frame, the convert step 15 s of every 20 on build box A, 2026-11-06). The key is a hash of everything the conversion reads: the vertex bytes,
 * the layout and fetch words, the shader, and for a fetch from another stream its constant and the vertices the draw's indices reach there, with the
 * indices. A draw whose reads cannot all be hashed (past 64 MB, or memory not committed) is converted each time, as before. An entry not used for two
 * frames may be given up for a new one; past 512 MB nothing more is kept until a frame's sweep frees room */
#define VC_SLOTS 16384u
#define VC_BUDGET ((size_t)512 << 20)
static struct { unsigned long long key; unsigned n, nat, frame, nu; float *v; void *vb, *ib; u32 *ix; } s_vc[VC_SLOTS];   /* vb: the host's buffer of v, made at its first draw (xb_gpu_draw_kept);
                                                                                                         * nu: rows of a draw of distinct rows (XB_VTX_UNIQUE), ix its n entries' rows, ib their host index buffer */
static size_t s_vc_bytes;
static unsigned s_vc_swept = ~0u;
static SRWLOCK s_vc_lock = SRWLOCK_INIT;

static unsigned long long vc_mix(unsigned long long h, unsigned long long x)
{
    h = (h ^ x) * 0x9e3779b97f4a7c15ull;
    return h ^ (h >> 31);
}

static unsigned long long vc_bytes(u32 a, u32 n)   /* data_hash's whole words, and the last word when it hashes only those */
{
    return vc_mix(data_hash(a, n), (n & 7u) ? rd32(a + n - 4u) : 0u);
}

/* An indexed draw's vertices are gathered into one block only when they must be converted (XB_DRAW_FAST, on by default). The key then stands on what the
 * gather reads, the index bytes and the vertices between the lowest and highest index, in place of the gathered block's bytes: a mesh drawn again
 * unchanged costs two hashes and no copy. A draw whose indices span over four times its count is gathered at once, as before (its span is not hashed) */
static struct { u32 vb, stride, n, to; unsigned long long hash; u32 base, vlo, vhi, vok; } s_lazy;   /* n: vertices still to gather into "to", 0 none; hash: 0 the block's own bytes;
                                                                                        * vok: s_vindex runs vlo..vhi, base + the indices hashed in hash (new path only) */

static void lazy_gather(void)
{
    u32 i, k;
    for (i = 0; i < s_lazy.n; i++)
        for (k = 0; k < s_lazy.stride; k += 4) wr32(s_lazy.to + i * s_lazy.stride + k, rd32(s_lazy.vb + s_vindex[i] * s_lazy.stride + k));
    s_lazy.n = 0;
}

static unsigned long long vc_span(u32 a, u32 n)   /* every byte: data_hash's whole words and the last eight bytes (indices are 2 bytes each) */
{
    unsigned long long t = n >= 8u ? ((unsigned long long)rd32(a + n - 8u) << 32 | rd32(a + n - 4u)) : n >= 4u ? rd32(a + n - 4u) : n >= 2u ? rd16(a) : 0u;
    return vc_mix(data_hash(a, n), t);
}

static unsigned long long vcache_key(const XbDecl *d, unsigned n, unsigned nat, u32 p, u32 stride)
{
    unsigned long long h = 0xcbf29ce484222325ull, ix = 0;
    unsigned k, i;
    if (!n || (unsigned long long)n * stride > 0x4000000u || !guest_readable(p, n * stride)) return 0;
    h = vc_mix(vc_mix(vc_mix(h, s_lazy.hash ? s_lazy.hash : vc_bytes(p, n * stride)), (unsigned long long)n << 32 | nat), (unsigned long long)stride << 32 | s_vs_container);
    h = vc_mix(h, d == &s_from_fetch);
    for (k = 0; k < nat; k++) {
        u32 base, lo = ~0u, hi = 0, sz, at;
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
            if (!reach || (unsigned long long)reach * sz > 0x1000000u || !guest_readable(at, reach * sz + 16u)) return 0;
            h = vc_mix(vc_mix(h, (unsigned long long)reach << 32 | sz), vc_bytes(at, reach * sz + 16u));
            continue;
        }
        for (i = 0; i < n; i++) {
            u32 v = s_vindex ? s_vindex[i] : s_vfirst + i;
            if (v < lo) lo = v;
            if (v > hi) hi = v;
        }
        at = (base | 0x20000000u) + lo * sz;
        if ((unsigned long long)(hi - lo + 1u) * sz > 0x1000000u || !guest_readable(at, (hi - lo + 1u) * sz + 16u)) return 0;
        h = vc_mix(vc_mix(h, (unsigned long long)lo << 32 | sz), vc_bytes(at, (hi - lo + 1u) * sz + 16u));
        if (!ix) {   /* the indices, once */
            ix = 0x84222325cbf29ce4ull;
            for (i = 0; i < n; i++) ix = vc_mix(ix, s_vindex ? s_vindex[i] : s_vfirst + i);
            h = vc_mix(h, ix);
        }
    }
    return h | 1u;
}

static float *vcache_find(unsigned long long key, unsigned n, unsigned nat, void ***vb, void ***ib, unsigned *nu, const u32 **ix)
{
    unsigned i = (unsigned)(key >> 50), j;
    float *v = 0;
    AcquireSRWLockExclusive(&s_vc_lock);
    for (j = 0; j < 16 && !v; j++, i = (i + 1u) & (VC_SLOTS - 1u))
        if (s_vc[i].v && s_vc[i].key == key && s_vc[i].n == n && s_vc[i].nat == nat)
            s_vc[i].frame = s_frame, v = s_vc[i].v, *vb = &s_vc[i].vb, *ib = &s_vc[i].ib, *nu = s_vc[i].nu, *ix = s_vc[i].ix;
    ReleaseSRWLockExclusive(&s_vc_lock);
    if (v) s_vc_hits += n;
    return v;
}

static size_t vc_size(unsigned n, unsigned nat, unsigned nu)   /* the block (n rows allocated, whatever nu) and a unique draw's remap */
{
    return (size_t)n * nat * 16u + (nu ? (size_t)n * 4u : 0u);
}

static void vc_drop(unsigned i)   /* under s_vc_lock */
{
    s_vc_bytes -= vc_size(s_vc[i].n, s_vc[i].nat, s_vc[i].nu);
    free(s_vc[i].v), free(s_vc[i].ix), xb_gpu_buffer_free(s_vc[i].vb), xb_gpu_buffer_free(s_vc[i].ib);
    s_vc[i].v = 0, s_vc[i].ix = 0, s_vc[i].vb = 0, s_vc[i].ib = 0, s_vc[i].nu = 0;
}

static int vcache_keep(unsigned long long key, unsigned n, unsigned nat, float *v, void ***vb, void ***ib, unsigned nu, const u32 *ix)
{
    size_t bytes = vc_size(n, nat, nu);
    unsigned i, j, at = VC_SLOTS;
    u32 *own = 0;
    if (nu && !(own = (u32 *)malloc((size_t)n * 4u))) return 0;   /* the remap is the pool's: a kept draw has its own copy */
    if (own) memcpy(own, ix, (size_t)n * 4u);
    AcquireSRWLockExclusive(&s_vc_lock);
    if (s_vc_bytes + bytes > VC_BUDGET && s_vc_swept != s_frame) {   /* once a frame: give up what the last two frames did not draw */
        s_vc_swept = s_frame;
        for (i = 0; i < VC_SLOTS; i++)
            if (s_vc[i].v && s_vc[i].frame + 2u <= s_frame) vc_drop(i);
    }
    for (i = (unsigned)(key >> 50), j = 0; j < 16 && s_vc_bytes + bytes <= VC_BUDGET; j++, i = (i + 1u) & (VC_SLOTS - 1u))
        if (!s_vc[i].v || s_vc[i].frame + 2u <= s_frame) { at = i; break; }
    if (at < VC_SLOTS) {
        if (s_vc[at].v) vc_drop(at);
        s_vc[at].key = key, s_vc[at].n = n, s_vc[at].nat = nat, s_vc[at].frame = s_frame, s_vc[at].v = v, s_vc[at].vb = 0, *vb = &s_vc[at].vb;
        s_vc[at].nu = nu, s_vc[at].ix = own, s_vc[at].ib = 0, *ib = &s_vc[at].ib, own = 0;
        s_vc_bytes += bytes;
    }
    ReleaseSRWLockExclusive(&s_vc_lock);
    free(own);
    return at < VC_SLOTS;
}
