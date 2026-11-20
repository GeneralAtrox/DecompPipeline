/* xb_d3d.c, Too Human's own device use (one translation unit with it; here for the 400-line cap): the shader headers it makes itself and
 * the device state it sets inline, with no library call the kit sees. */

/* A shader header the game made itself (Too Human's CreateVertexShader 82758848 and CreatePixelShader 82758480 run as guest code): the
 * container is copied into the header, a vertex shader's (type 6) at +0x368 with its microcode's address at +0x20, a pixel shader's
 * (type 7) at +0x28 with it at +0x18. Its key is a hash of the container's bytes
 * (tools/shader_convert.py, made_key), the name the offline pass gave its HLSL when it took it from the game's packs, so nothing here reads
 * microcode but to capture it. A key with no converted copy is logged once; with XB_SHADER_CAPTURE naming a folder, the container and
 * microcode go there for the next offline pass. 0 when the header holds no container */
static u32 made_shader_key(u32 h)
{
    static u32 said[1024];
    static unsigned said_n;
    u32 c, code, size, x, key = 2166136261u, i;
    static unsigned said_bad;
    static const char *s_env;
    static volatile long s_env_read;   /* read once: getenv each draw cost 0.5 ms a gameplay frame */
    const char *dir;
    static char own[300];
    if (!s_env_read) s_env = getenv("XB_SHADER_CAPTURE"), s_env_read = 1;
    dir = s_env;
    if (!h) return 0;
    /* Without XB_SHADER_CAPTURE the data folder's shader_capture keeps them, so every run (the user's too) gathers the shaders the game made that
     * the build has no copy of, and the next build converts them (the user's run of 2026-11-12: 9 such keys, 90,000 draws left black or missing,
     * one in the opening scene). XB_SHADER_CAPTURE=0 turns it off. A few KB a shader, each once */
    if (dir && dir[0] == '0' && !dir[1]) dir = 0;
    else if (!dir) {
        if (!own[0]) sprintf_s(own, sizeof own, "%s\\shader_capture", host_data_folder()), CreateDirectoryA(own, 0);
        dir = own;
    }
    c = h + ((rd32(h) & 15) == 6 ? 0x368 : 0x28);
    code = rd32(h + ((rd32(h) & 15) == 6 ? 0x20 : 0x18));
    if ((rd32(h) & 15) < 6 || (rd32(h) & 15) > 7 || (rd32(c) & ~0x41u) != 0x102A1100u) {   /* 1 a vertex shader; 0x40 on those the game compiles at run time */
        if (said_bad++ < 16)
            port_log("d3d shader header %08x has no container: %08x %08x %08x %08x %08x %08x %08x %08x | +28 %08x %08x %08x %08x | +368 %08x %08x %08x %08x\n", h,
                rd32(h), rd32(h + 4), rd32(h + 8), rd32(h + 12), rd32(h + 16), rd32(h + 20), rd32(h + 24), rd32(h + 28),
                rd32(h + 0x28), rd32(h + 0x2c), rd32(h + 0x30), rd32(h + 0x34), rd32(h + 0x368), rd32(h + 0x36c), rd32(h + 0x370), rd32(h + 0x374));
        return 0;
    }
    size = rd32(c + 4);
    x = rd32(c + 8);
    for (i = 0; i < size; i++) key = (key ^ rd8(c + i)) * 16777619u;
    if (!(rd8(c + 3) & 1))   /* a pixel shader's microcode too (tools/shader_decode.py made_key) */
        for (i = 0; i < x; i++) key = (key ^ rd8(code + i)) * 16777619u;
    key = XB_MADE_SHADER | (key & ~XB_MADE_SHADER);
    for (i = 0; i < said_n; i++)
        if (said[i] == key) return key;
    if (said_n < sizeof said / sizeof *said) said[said_n++] = key;
    if (xb_shader_find(key)) return key;
    port_log("d3d shader header %08x: %s container %08x has no converted copy (%u bytes, microcode %u at %08x)\n", h, rd8(c + 3) & 1 ? "vertex" : "pixel", key, size, x, code);
    if (dir) {
        char path[260];
        FILE *f;
        sprintf_s(path, sizeof path, "%s/%08x.bin", dir, key);
        if (fopen_s(&f, path, "wb") == 0) {
            for (i = 0; i < size; i++) fputc(rd8(c + i), f);
            for (i = 0; i < x; i++) fputc(rd8(code + i), f);
            fclose(f);
        }
    }
    return key;
}

/* A 16-bit float's value */
static float half_value(u32 h)
{
    u32 e = (h >> 10) & 31u, m = h & 1023u;
    float f = e == 0 ? (float)m * (1.0f / 16777216.0f) : e == 31 ? (m ? 0.0f : 65504.0f) : ldexpf(1.0f + (float)m / 1024.0f, (int)e - 15);
    return (h & 0x8000u) ? -f : f;
}

/* The components of one vertex fetch as the GPU numbers them, for the selects a made shader's Bind patched (Too Human; draw_vertices): 32-bit
 * components in memory order, and the narrower ones from the low bits of each big-endian word up, so a D3DCOLOR's blue is component 0 and a
 * half pair's second half is. w1 is the fetch's second word: the format, and bits 12 and 13 signed and integer (else unsigned fractions).
 * 0 for a format with no case */
static int fetch_components(u32 at, u32 w1, float c[4])
{
    u32 fmt = (w1 >> 16) & 63u, sgn = (w1 >> 12) & 1u, whole = (w1 >> 13) & 1u, i, x;
    c[0] = c[1] = c[2] = 0.0f;
    c[3] = 1.0f;
    switch (fmt) {
    case 36: case 37: case 38: case 57:   /* 32-bit floats: one, two, four, three */
        for (i = 0; i < (fmt == 38 ? 4u : fmt == 57 ? 3u : fmt - 35u); i++) { x = rd32(at + 4 * i); memcpy(&c[i], &x, 4); }
        return 1;
    case 31: case 32:                     /* 16-bit floats: two, four */
        for (i = 0; i < (fmt == 31 ? 2u : 4u); i++) c[i] = half_value(rd32(at + 4 * (i >> 1)) >> (16 * (i & 1)) & 0xffffu);
        return 1;
    case 25: case 26:                     /* 16-bit integers: two, four */
        for (i = 0; i < (fmt == 25 ? 2u : 4u); i++) {
            x = rd32(at + 4 * (i >> 1)) >> (16 * (i & 1)) & 0xffffu;
            c[i] = sgn ? (float)(short)x : (float)x;
            if (!whole) c[i] = sgn ? (c[i] < -32767.0f ? -1.0f : c[i] / 32767.0f) : c[i] / 65535.0f;
        }
        return 1;
    case 6:                               /* 8-bit integers: four */
        x = rd32(at);
        for (i = 0; i < 4; i++) {
            u32 b = (x >> (8 * i)) & 255u;
            c[i] = sgn ? (float)(signed char)b : (float)b;
            if (!whole) c[i] = sgn ? (c[i] < -127.0f ? -1.0f : c[i] / 127.0f) : c[i] / 255.0f;
        }
        return 1;
    }
    return 0;
}

/* 0 for a draw to skip: a made shader with no converted copy (made_shader_key logged it) */
static int made_shaders_converted(void)
{
    const XbShader *vs = xb_shader_find(s_vs_container), *ps = xb_shader_find(s_ps_container);
    static unsigned skipped;
    if ((s_vs_container & XB_MADE_SHADER) == XB_MADE_SHADER && (!vs || !vs->hlsl)) goto skip;
    if ((s_ps_container & XB_MADE_SHADER) == XB_MADE_SHADER && (!ps || !ps->hlsl)) goto skip;
    return 1;
skip:
    xb_fcap_skip("a made shader with no converted copy", s_vs_container, s_ps_container);
    if (!skipped++ || skipped % 1000 == 0) port_log("d3d draw skipped (%u so far): shaders %08x %08x, one with no converted copy\n", skipped, s_vs_container, s_ps_container);
    {   /* each pair once, so a run's log names every shader that costs draws, not only those at each thousandth */
        static u32 pairs[64][2];
        unsigned k;
        for (k = 0; k < 64 && pairs[k][0] && (pairs[k][0] != s_vs_container || pairs[k][1] != s_ps_container); k++) {}
        if (k < 64 && !pairs[k][0]) {
            pairs[k][0] = s_vs_container, pairs[k][1] = s_ps_container;
            port_log("d3d draw skipped: new pair %08x %08x (vertex %s, pixel %s), %u skipped so far\n", s_vs_container, s_ps_container,
                     !vs ? "not in the shader table (no such container in the packs)" : vs->hlsl ? "converted" : vs->why ? vs->why : "refused",
                     !ps ? "not in the shader table (no such container in the packs)" : ps->hlsl ? "converted" : ps->why ? ps->why : "refused", skipped);
        }
    }
    return 0;
}

/* Too Human's SetStreamSource(device, stream, buffer, offset, stride) keeps the buffer at +0x30ac + 4 * stream, a quarter of the stride in the byte at
 * +0x30f0 + stream and the buffer's data address plus the offset in the stream's fetch constant at +8 * (0xef - stream), the GPU's address of a physical
 * one (8274e658; GetStreamSource 8274e778 reads it back); SetVertexDeclaration keeps the declaration at +0x2e2c */
/* Not for a call replayed from a command buffer (replaying): the GPU takes a run's streams from its packets, and the device's fields stay the
 * recorder's. Written at the run too, the render thread's replay of the last frame's buffer put that buffer's last stream 0 into the device while
 * the game recorded the next one; the game sets a mesh's stream once for all its instances, so the instances recorded after it read another mesh's
 * positions: the menu's stone ring and pillars (ffb6e14b/fcceb41e, 41 of 78 draws) drew from 18475b6c, a mesh drawn 170 draws later, where Xenia's
 * trace has each mesh's own buffer (build box A, 2026-11-13, captures q5/q6). XB_REPLAY_STREAM_STORE=1 gives the old behaviour */
static void th_state_store(u32 entry, const TcArgs *a, int replaying)
{
    u32 dev = (u32)a->r[0], s = (u32)a->r[1], b = (u32)a->r[2], data;
    static int old = -1;
    if (old < 0) { const char *e = getenv("XB_REPLAY_STREAM_STORE"); old = e && *e == '1'; }
    if (replaying && !old) return;
    if (entry == 0x82758b08u) { wr32(dev + 0x2e2cu, (u32)a->r[1]); return; }
    if (s >= 16) return;
    wr32(dev + 0x30acu + 4u * s, b);
    wr8(dev + 0x30f0u + s, ((u32)a->r[4] >> 2) & 255u);
    if (!b) return;
    data = rd32(b + 0x18u) + (u32)a->r[3];
    wr32(dev + 8u * (0xefu - s), ((((data >> 20) & 0xfffu) + 0x200u) & 0x1000u) + (data & 0x1fffffffu));
}

/* SetRenderTarget's colour info: RB_COLOR_INFO, which the XDK keeps in a surface header's +0x1c and Too Human's SetRenderTarget (8274e890) copies into
 * the device's registers. Its bits 20..25 are the target's own exponent bias, handed to the host (used with XB_COGBOX_RT_BIAS=1). A header the kit made
 * has none (0). Each distinct value is logged once */
static void th_target_bias(u32 idx, u32 surf, u32 ci)
{
    static u32 said[32];
    unsigned i;
    int fmt = surf && !find_res(surf) ? (int)((ci >> 16) & 15u) : -1;   /* a header the kit made: its format unknown, its output left unclamped */
    if (!surf || find_res(surf)) ci = 0;
    for (i = 0; i < 32 && said[i] && said[i] != (ci | 1u); i++) {}
    if (surf && i < 32 && !said[i]) {
        said[i] = ci | 1u;
        port_log("d3d SetRenderTarget %u: header %08x colour info %08x (format %u, exponent bias %d)\n", idx, surf, ci, (ci >> 16) & 15u, xenos_color_info_exp_bias(ci));
    }
    xb_gpu_target_bias(idx, xenos_color_info_exp_bias(ci));
    xb_gpu_target_format(idx, fmt);
}

int xb_lib_entry(u32 target, const TcArgs *a, u64 *r3, f64 *f1);

/* The stride a stream with none given is read with: the bound vertex shader's first fetch, in its microcode, as the GPU reads it (Too Human's
 * DrawIndexedVertices with no declaration under 82999c98: stream stride 0, the fetch's 8), or 0 */
static u32 fetch_stride(void)
{
    const XbShader *vs = xb_shader_find(s_vs_container);
    if (!vs || !vs->fetch[0] || !s_bound_vs) return 0;
    return (rd32(rd32(s_bound_vs + 0x20) + vs->fetch[0] - 1 + 8) & 255u) * 4u;
}

/* An indexed draw's indices checked against its buffers before the gather: Too Human's DrawIndexedVertices under 82999c98 read 2c7bf850 at frame 635, a
 * fault inside memory handed out. The index buffer's size is at +0x1c (after the resource header, beside the data at +0x18); the vertex buffer's is in its
 * fetch constant's second word, bits 2..25 in words. The GPU clamps neither (as Xenia, it reads the memory that follows; 829ae178 in Too Human's first room
 * draws 450 indices up to 606 from a fetch of 396 vertices), so past one is logged and drawn; 0, a draw to skip, only when the indices are not in memory
 * (stream_vertex reads vertices that are not as absent) */
/* Where stream 0's vertices are for a draw: the kit's SetStreamSource keeps the buffer, offset and stride (s_stream[0]), but leaves the device's
 * fetch constant (slot 95, device +8 * 0xef) to the game. The other streams are read from the device's constants (stream_element), so a draw whose
 * game code set stream 0 in the device without SetStreamSource read its positions from the last buffer SetStreamSource named and its other inputs
 * from the device. XB_STREAM0_DEV=1 (bring-up): stream 0 from the device's constant too when the two differ. Each difference is logged (the first
 * 12) and named in the frame capture's record ("stream0"). *size: the bytes its fetch constant gives */
static u32 stream0_at(u32 b, u32 *size, u32 stride)
{
    static int mode = -1;
    static unsigned said;
    u32 hdr = (rd32(b + 24) & ~3u) + s_stream[0].offset, w0 = s_fetch_dev ? rd32(s_fetch_dev + 8u * 0xefu) : 0, w1 = s_fetch_dev ? rd32(s_fetch_dev + 8u * 0xefu + 4u) : 0;
    u32 dev = (w0 & 0x1ffffffcu) ? (w0 & 0x1ffffffcu) | 0x20000000u : 0, fs = fetch_stride();
    if (mode < 0) {
        const char *e = getenv("XB_STREAM0_DEV");
        mode = e && *e == '1';
    }
    *size = ((rd32(b + 28) >> 2) & 0xffffffu) * 4u;
    if (g_fcap_on) {   /* the device's vertex fetch constants of streams 0..9 (+8 * (0xef - stream)), both words, for the record */
        u32 sl[20], k;
        for (k = 0; k < 20; k++) sl[k] = s_fetch_dev ? rd32(s_fetch_dev + 8u * (0xefu - k / 2u) + 4u * (k & 1u)) : 0;
        xb_fcap_stream0(hdr, dev, ((w1 >> 2) & 0xffffffu) * 4u, stride, fs, sl);
    }
    if (dev && ((hdr & 0x1ffffffcu) | 0x20000000u) != dev) {
        if (said++ < 12)
            port_log("d3d draw: stream 0 at %08x (SetStreamSource %08x + %u, %u bytes, stride %u) but the device's fetch constant says %08x %08x (%u bytes; shader fetch stride %u), shader %08x/%08x, frame %u%s\n",
                     hdr, rd32(b + 24), s_stream[0].offset, *size, stride, w0, w1, ((w1 >> 2) & 0xffffffu) * 4u, fs, s_vs_container, s_ps_container, s_frame, mode ? ": the device's taken" : "");
        if (mode) {
            *size = ((w1 >> 2) & 0xffffffu) * 4u;
            return dev;
        }
    }
    return hdr;
}

static int check_indexed(u32 b, u32 ib, u32 first, u32 n, u32 base, u32 stride, u32 wide, u32 data, u32 vat, u32 vsize)
{
    u32 isize = rd32(ib + 0x1cu), i, idx, top = 0, offset = 0;
    static unsigned said_i, said_v, said_gone;
    if (!guest_readable(data + (first << (wide ? 2 : 1)), n << (wide ? 2 : 1))) {
        xb_fcap_skip("indices not in committed guest memory", s_vs_container, s_ps_container);
        if (said_gone++ < 8) port_log("d3d indexed draw skipped: indices %u..%u (%s) at %08x are not committed guest memory\n", first, first + n, wide ? "32-bit" : "16-bit", data);
        return 0;
    }
    if (((first + n) << (wide ? 2 : 1)) > isize && said_i++ < 8)
        port_log("d3d indexed draw: indices %u..%u (%s) past the index buffer's %u bytes, header %08x data %08x (drawn, as the GPU does)\n", first, first + n, wide ? "32-bit" : "16-bit", isize, rd32(ib), data);
    for (i = 0; i < n; i++) {
        idx = wide ? rd32(data + (first + i) * 4) : (u32)rd16(data + (first + i) * 2);
        if (idx > top) top = idx;
    }
    if (offset + (base + top + 1) * stride > vsize && said_v++ < 8)
        port_log("d3d indexed draw: vertex %u (base %u, highest index %u) of stride %u at %08x past the vertex buffer's %u bytes (fetch %08x %08x), %u indices from %u, %s, index data %08x, shader %08x/%08x (drawn, as the GPU does)\n",
                 base + top, base, top, stride, vat, vsize, rd32(b + 24), rd32(b + 28), n, first, wide ? "32-bit" : "16-bit", data, s_vs_container, s_ps_container);
    /* The vertices must be in memory too: at frames 504-552, loading the town, a command buffer replayed on the render thread gathered from a vertex
     * buffer already given back (faults at 3882fa70, 38d28bd0, 3928f944, all inside memory handed out). Skipped like absent indices */
    if (!guest_readable(vat + offset + base * stride, (top + 1) * stride)) {
        xb_fcap_skip("vertices not in committed guest memory", s_vs_container, s_ps_container);
        if (said_gone++ < 8) port_log("d3d indexed draw skipped: vertices %u..%u of stride %u at %08x are not committed guest memory\n", base, base + top, stride, vat);
        return 0;
    }
    return 1;
}

static void th_draw_state(u32 dev)
{
    TcArgs s;
    u64 r = 0;
    u32 b = rd32(dev + 0x30acu), data;
    memset(&s, 0, sizeof s);
    s.r[0] = dev;
    s.r[2] = b;
    s.r[4] = rd8(dev + 0x30f0u) * 4u;
    if (b) {
        data = rd32(b + 0x18u);
        s.r[3] = (u32)(rd32(dev + 8u * 0xefu) - ((((data >> 20) & 0xfffu) + 0x200u) & 0x1000u) - (data & 0x1fffffffu));
    }
    xb_lib_entry(0x8274e658u, &s, &r, NULL);
    memset(&s, 0, sizeof s);
    s.r[0] = dev;
    s.r[1] = rd32(dev + 0x2e2cu);
    xb_lib_entry(0x82758b08u, &s, &r, NULL);
}
