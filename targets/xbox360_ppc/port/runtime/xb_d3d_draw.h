/* xb_d3d.c, the entries that draw, resolve and present (one translation unit with it, for its state; here for the 400-line cap) */
static int entry_draw(u32 target, const TcArgs *a, u64 *r3)
{
    (void)a;
    switch (target) {
    case 0x821cd418: {   /* SetVertexDeclaration(device, declaration): first stop, 82093a98 right after SetTexture, with the header of the
                          * fourth declaration made (one element) */
        s_bound_decl = (u32)a->r[1];
        D3D_LOG("d3d SetVertexDeclaration := %08x\n", s_bound_decl);
        *r3 = 0;
        return 1;
    }
    case 0x821cd248: {   /* SetVertexShader(device, shader): first stop, 82093a98 right after SetVertexDeclaration, with the header of the
                          * fourth vertex shader made */
        XbRes *r = find_res(s_bound_vs = (u32)a->r[1]);
        D3D_LOG("d3d SetVertexShader := %08x (%s)\n", s_bound_vs, r ? r->kind : "none");
        s_vs_container = r ? r->arg[0] : 0;
        xb_gpu_bind_shaders(s_vs_container, s_ps_container);
        *r3 = 0;
        return 1;
    }
    case 0x821cd088: {   /* SetPixelShader(device, shader): first stop, 82093a98 right after SetVertexShader, with the header of the fifth pixel
                          * shader made (0x20210000) */
        XbRes *r = find_res(s_bound_ps = (u32)a->r[1]);
        D3D_LOG("d3d SetPixelShader := %08x (%s)\n", s_bound_ps, r ? r->kind : "none");
        s_ps_container = r ? r->arg[0] : 0;
        xb_gpu_bind_shaders(s_vs_container, s_ps_container);
        *r3 = 0;
        return 1;
    }
    case 0x821d0418:     /* (device, 0): first stop, 82093970 at 82093a7c, the last library call of the start-up function. The library stores the
                          * flag in bit 6 of the device byte at +10941 (the mask the argument is rotated into), marks the device
                          * state dirty (+16 |= 8) and re-emits the registers that depend on it: a boolean device mode. Whether
                          * it is the half-pixel offset is not shown by the code and is not assumed; the value is logged */
        return state_only(target, a, r3, "boolean device mode (bit 6 of the byte at +10941)");
    case 0x821d0bc0:     /* (device): first stop, 82093a98 at 82093ab4, the first call of a function that then binds the render target and
                          * depth surface and clears: a frame start. The library body reads its own state (+10896, +10940, +13196)
                          * and appends packets to its command buffer; it takes no argument but the device. The name is not shown
                          * by the code (a begin-scene or a flush); nothing the game reads comes back */
        return state_only(target, a, r3, "device call with no argument (library state flush)");
    case 0x821cd7b8: {   /* create a pixel shader from a compiled container (pFunction in r3 and r4): first stop, 820937f0 (from 820979e8,
                          * 82093cf0) with 0x820184e0, magic 102a1100 and the version token ffff0300 (ps_3_0) at +0x30. So 1100 is
                          * the pixel container and 1101 the vertex one, and the two creators are 821cd9a0 and this */
        u32 f = (u32)a->r[0];
        D3D_LOG("d3d pixel shader container %08x magic %08x size %x\n", f, rd32(f), rd32(f + 4));
        *r3 = make_res("pixel shader", a, target);
        return 1;
    }
    case 0x821cd520: {   /* create a vertex declaration (pVertexElements, again in r3 and r4): first stop, 82093938 (from 820a6610, 82093cf0)
                          * with 0x8247f1e0: 12-byte elements {stream 0 offset 0 type 002a23b9 (three floats), stream 0 offset 12
                          * type 00182886 usage 10 (colour)} and the end marker (stream 0xff, type ffffffff) */
        XbDecl *d;
        u32 p = (u32)a->r[0];
        if (s_decl_n >= sizeof s_decl / sizeof *s_decl) xb_stop("d3d_resources", "more than 64 vertex declarations", target);
        d = &s_decl[s_decl_n++];
        d->guest = xb_alloc(0x40);
        for (d->n = 0; d->n < 16 && (rd32(p) >> 16) != 0xFF; d->n++, p += 12) {
            d->el[d->n].stream = (u16)(rd32(p) >> 16);
            d->el[d->n].offset = (u16)rd32(p);
            d->el[d->n].type = rd32(p + 4);
            d->el[d->n].usage = (u8)(rd32(p + 8) >> 16);
            d->el[d->n].index = (u8)(rd32(p + 8) >> 8);
        }
        D3D_LOG("d3d vertex declaration %u at %08x: %u elements\n", s_decl_n - 1, d->guest, d->n);
        {
            unsigned k;
            for (k = 0; k < d->n; k++)
                D3D_LOG("d3d   element %u: stream %u offset %u type %08x usage %u index %u\n", k, d->el[k].stream, d->el[k].offset,
                         d->el[k].type, d->el[k].usage, d->el[k].index);
        }
        *r3 = d->guest;
        return 1;
    }
    case 0x821d0e58: {   /* BeginVertices(device, primitive type, vertex count, stride): first stop 82256c90 in the UI library, the body of DrawVerticesUP (821d12f0 calls
                          * it and copies). Answers where the game writes the vertices; a zero answer is out of memory (0x8007000e). The draw happens at 821d0e48 */
        static u32 room;
        u32 need = (u32)a->r[2] * (u32)a->r[3];
        if (need > room) {
            room = need * 2 < 0x10000u ? 0x10000u : need * 2;
            s_begin_data = xb_alloc(room);
        }
        s_begin_prim = (unsigned)a->r[1];
        s_begin_n = (unsigned)a->r[2];
        s_begin_stride = (unsigned)a->r[3];
        *r3 = s_begin_data;
        return 1;
    }
    case 0x821d0e48:     /* EndVertices(device): the pair of 821d0e58 */
        draw_vertices(s_begin_prim, s_begin_n, s_begin_data, s_begin_stride);
        *r3 = 0;
        return 1;
    case 0x821d12f0: {   /* DrawVerticesUP(device, primitive type, vertex count, vertex data, stride): first stop, 82093a98 after the shaders are
                          * bound, r4 8 (the rectangle list), r5 3, r6 a pointer into the guest stack to three float2 vertices, r7 8. */
        draw_vertices((u32)a->r[1], (u32)a->r[2], (u32)a->r[3], (u32)a->r[4]);
        *r3 = 0;
        return 1;
    }
    case 0x821d18f0: {   /* DrawVertices(device, primitive type, first vertex, vertex count): first stop, 82093758 under 8209e9c0, r4 8 (the rectangle list), r5 0, r6 3.
                          * The vertices come from stream 0, the buffer the game locked and filled */
        u32 b = s_stream[0].buffer;
        if (!b) xb_stop("d3d_draw", "a draw from a stream with no buffer", 0);
        draw_vertices((u32)a->r[1], (u32)a->r[3], (rd32(b + 24) & ~3u) + s_stream[0].offset + (u32)a->r[2] * s_stream[0].stride, s_stream[0].stride);
        *r3 = 0;
        return 1;
    }
    case 0x821d24b0: {   /* Resolve(device, flags, source rect, destination texture, destination point, level, slice, clear colour *, clear z in
                          * f1, stencil on the stack): first stop, 82093a98 at 82093cd0 right after the draw, flags 0x100, rect 0, the
                          * header the first texture create returned, point 0, level 0, slice 0, a stack block of four floats and z. The
                          * library body takes the low three bits of the flags as the source (0..3 a render target, 4 the depth surface),
                          * bits 0x70 as another selection and, with a clear colour block, reloads it as four floats. 0x100 names no
                          * source, so it is the render target 0 and the clear of that target after the copy; any other bit stops */
        u32 fl = (u32)a->r[1], dst = (u32)a->r[3], cc = (u32)a->r[7];
        float rgba[4] = {0, 0, 0, 0};
        unsigned i;
        if (fl & ~0x107u) xb_stop("d3d_resolve", "resolve flags the host has no case for", fl);
        if ((fl & 7) > 3) xb_stop("d3d_resolve", "a depth-surface resolve has no case yet", fl);
        D3D_LOG("d3d Resolve flags %x: target %u -> %08x, clear colour block %08x\n", fl, fl & 7, dst, cc);
        xb_gpu_resolve(host_tex(s_target[fl & 3], 0), host_tex(dst, 0));
        if (fl & 0x100) {
            if (cc)
                for (i = 0; i < 4; i++) {
                    u32 w = rd32(cc + 4 * i);
                    memcpy(&rgba[i], &w, 4);
                }
            xb_gpu_clear(0xF0, rgba, 0, 0);
        }
        *r3 = 0;
        return 1;
    }
    case 0x821c65e8: {   /* AddRef(device): first stop, 82256f08 (from 8224d718, 8224d948 ... 82094328) with the device. The body is four
                          * instructions: it adds one to the word at device +60 and returns the new count */
        u32 dev = (u32)a->r[0], n = rd32(dev + 60) + 1;
        wr32(dev + 60, n);
        *r3 = n;
        return 1;
    }
    case 0x821c6800:     /* (device): first stop, 82093550 in the start-up function, the game's wrapper of the device at 8765ca64. The body takes the device's
                          * lock and sets bit 2 of the byte at +10942: the library's hand-over of the device between threads, paired with 821c6898
                          * (82093560). The host device has no owner to change, as long as one thread at a time draws */
        return state_only(target, a, r3, "device thread ownership: let go");
    case 0x821c6898:     /* (device): the pair of 821c6800, from 82093560 */
        return state_only(target, a, r3, "device thread ownership: take");
    case 0x821d7318:     /* AddRef(resource): first stop 822485b8 in the UI library. The pair of Release (821d8280), and the kit frees nothing, so no count is kept */
        *r3 = 1;
        return 1;
    case 0x821ce2f0: {   /* Swap(device, front buffer texture, 0): first stop, 82093a98 at 82093ce0 right after the resolve, with the resolve's destination
                          * (the start-up object +252). The body copies the 24 bytes of fetch constant at texture +28 to a local, edits them, keeps a ring of fence
                          * counters at device +21624/+21628 (it waits when more than six are in flight) and appends packets: it queues the front buffer for scan-out.
                          * XB_DUMP names a folder for one frame in every XB_DUMP_EVERY (60), so a test run shows the picture with no window */
        const char *dir = getenv("XB_DUMP"), *every = getenv("XB_DUMP_EVERY");
        XbHostTex *t = host_tex((u32)a->r[1], 0);
        xb_gpu_present(t);
        if (getenv("XB_LOG_FROM") && s_frame == (unsigned)atoi(getenv("XB_LOG_FROM"))) s_log_left = 400;   /* the order of calls at a later frame */
        if (dir && s_frame % (unsigned)(every ? atoi(every) : 60) == 0) {
            char path[260];
            sprintf_s(path, sizeof path, "%s/frame_%05u.bmp", dir, s_frame);
            xb_gpu_dump(t, path);
        }
        watch_poll();
        s_frame++;
        xb_tick_end();
        *r3 = 0;
        return 1;
    }
    case 0x821ce2e8:     /* (device): first stop, 82093a98 at 82093ca0 right after the draw and before the resolve. The entry loads r4 = 0 and
                          * joins a body shared with other callers (821cdec0) that writes packets into the library's command ring
                          * (device +48) and returns nothing the game reads. Nothing is drawn through a ring here: the draw has
                          * already landed in the host target */
        return state_only(target, a, r3, "device call after a draw (ring packets only)");
    case 0x821d5110:     /* (device, 2): first stop, 82093cf0 at 820941bc, the value from a field of the start-up object. The library
                          * inserts it into bits 12..13 of the device's register-state word at +10916 and appends one packet to its
                          * own command buffer (device +48 write pointer, +56 end): a GPU state setter with no D3D11 counterpart */
        return state_only(target, a, r3, "register-state field (bits 12..13)");
    case 0x821c9548:     /* SetIndices(device, index buffer): first stop, 82093648 (r4 passed straight through) with none, under 820a5348 clearing the streams */
        s_indices = (u32)a->r[1];
        *r3 = 0;
        return 1;
    case 0x821c6920: {   /* (device): first stop, 82093480, the game's reset of the device, just before Reset. Its body sets every render target and the depth surface
                          * back and then every sampler, stream and shader to none, so nothing stays bound through a Reset. Same here */
        unsigned i;
        for (i = 0; i < 4; i++) xb_gpu_bind_target(i, 0);
        xb_gpu_bind_depth(0);
        memset(s_target, 0, sizeof s_target);
        memset(s_texture, 0, sizeof s_texture);
        memset(s_stream, 0, sizeof s_stream);
        s_depth = s_bound_decl = s_bound_vs = s_bound_ps = s_indices = 0;
        *r3 = 0;
        return 1;
    }
    case 0x821c9660:     /* (device): first stop, 82093480 after Reset, returns the device's own colour surface (device +0x39f0) with a reference taken; stored at 8765ca68 and
                          * given back through 821d8280 at the next reset */
    case 0x821c9620: {   /* (device): the same for its own depth surface (device +0x30b0), stored at 8765ca6c */
        static u32 own[2];
        int depth = target == 0x821c9620;
        if (!own[depth]) {
            TcArgs m;
            memset(&m, 0, sizeof m);
            m.r[0] = s_mode.width;
            m.r[1] = s_mode.height;
            m.r[2] = depth ? s_mode.depth_format : s_mode.format;
            own[depth] = make_res(depth ? "device depth surface" : "device colour surface", &m, target);
        }
        *r3 = own[depth];
        return 1;
    }
    case 0x821d8280:     /* Release(resource): first stop, 82093480. The header stays: the kit frees nothing yet and a game that makes resources over and over stops at the limit */
        *r3 = 0;
        return 1;
    case 0x821c6780:     /* Reset(device, D3DPRESENT_PARAMETERS *): first stop, 82093cf0 at 82094208 right after the ring buffer call, the
                          * same parameter block CreateDevice got (the start-up object +32). The host swap chain follows the window,
                          * so only the game's record of the mode is taken */
        read_mode((u32)a->r[1], "Reset");
        *r3 = 0;
        return 1;
    case 0x821d4cc0: {   /* SetRingBufferParameters(device, D3DRING_BUFFER_PARAMETERS *): first stop, 82093cf0 at 820941fc, passes a stack block
                          * of six words {flags 0, primary size 0, primary 0, secondary size 0x300000, secondary 0, segments 0} (the
                          * library reads +4 .. +20 and sizes its own command ring from them). There is no ring here: the game's
                          * calls land in the host directly, so the sizes are only logged */
        u32 b = (u32)a->r[1];
        D3D_LOG("d3d SetRingBufferParameters: primary %x secondary %x segments %u, no ring here\n", rd32(b + 4), rd32(b + 12), rd32(b + 20));
        *r3 = 0;
        return 1;
    }
    }
    return 0;
}
