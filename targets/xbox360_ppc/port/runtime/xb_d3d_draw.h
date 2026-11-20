/* xb_d3d.c, the entries that draw, resolve and present (one translation unit with it, for its state; here for the 400-line cap) */
/* The tile mask of the last SetPredication (82761d90): 3 tile 0, c tile 1, 0 none. The host has no tiles: see Resolve */
static u32 s_predication;

static int entry_draw(u32 target, const TcArgs *a, u64 *r3)
{
    (void)a;
    switch (target) {
    case 0x82765b00: {   /* Clear(device, count, rects, flags, D3DCOLOR, z in f1 (its slot is r8's), stencil in r9): Too Human, first stop 8296a520 after
                          * SetRenderTarget, flags 0x3f. Its 82765488 takes flags 0xF as render targets 0..3 and drops 0xF0 with no depth surface: those are
                          * depth (0x10) and stencil (0x20). Which of the two is which is assumed, only both together seen. Mega Man 10 clears only through
                          * Resolve and EndTiling, so this name is Too Human's own address */
        u32 fl = (u32)a->r[3], c = (u32)a->r[4], host = 0, n = (u32)a->r[1];
        float rgba[4];
        if (fl & ~0x3Fu) xb_stop("d3d_clear", "clear flags the host has no case for", fl);
        rgba[0] = ((c >> 16) & 255) / 255.0f;
        rgba[1] = ((c >> 8) & 255) / 255.0f;
        rgba[2] = (c & 255) / 255.0f;
        rgba[3] = (c >> 24) / 255.0f;
        if (fl & 0xF) host |= 0xF0;
        if (fl & 0x10) host |= 0x100;
        if (fl & 0x20) host |= 0x200;
        if ((u32)a->r[2] && n) {   /* count D3DRECTs {x1, y1, x2, y2}: first stop at frame 636, one rectangle */
            xb_gpu_clear_rects(host, rgba, (float)a->f[0], (u32)a->r[6], (u32)a->r[2], n);
            *r3 = 0;
            return 1;
        }
        D3D_LOG("d3d Clear flags %x colour %08x z %g stencil %x\n", fl, c, a->f[0], (u32)a->r[6]);
        clear_diag("clear", host, s_target[0], 0, rgba, (float)a->f[0], (u32)a->r[6]);
        if (edram_diag()) edram_diag_n++, port_log("diag edram frame %u clear flags %x: target %08x, colour %g %g %g %g\n", s_frame, host, s_target[0], rgba[0], rgba[1], rgba[2], rgba[3]);
        xb_gpu_clear(host, rgba, (float)a->f[0], (u32)a->r[6]);
        *r3 = 0;
        return 1;
    }
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
        s_vs_container = r ? r->arg[0] : made_shader_key(s_bound_vs);
        xb_gpu_bind_shaders(s_vs_container, s_ps_container);
        *r3 = 0;
        return 1;
    }
    case 0x821cd088: {   /* SetPixelShader(device, shader): first stop, 82093a98 right after SetVertexShader, with the header of the fifth pixel
                          * shader made (0x20210000) */
        XbRes *r = find_res(s_bound_ps = (u32)a->r[1]);
        D3D_LOG("d3d SetPixelShader := %08x (%s)\n", s_bound_ps, r ? r->kind : "none");
        s_ps_container = r ? r->arg[0] : made_shader_key(s_bound_ps);
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
        static SRWLOCK lock = SRWLOCK_INIT;   /* Too Human makes them from its loader and render threads */
        static u32 pool, left;                /* the guest objects, 0x100 each from shared 64 KB blocks (xb_alloc's grain) */
        XbDecl *d;
        u32 p = (u32)a->r[0];
        AcquireSRWLockExclusive(&lock);
        if (s_decl_n >= sizeof s_decl / sizeof *s_decl) xb_stop("d3d_resources", "more than 4096 vertex declarations", target);
        d = &s_decl[s_decl_n];
        if (left < 0x100u) { pool = xb_alloc(0x10000u); left = 0x10000u; }
        d->guest = pool;
        pool += 0x100u;
        left -= 0x100u;
        for (d->n = 0; d->n < 16 && (rd32(p) >> 16) != 0xFF; d->n++, p += 12) {
            d->el[d->n].stream = (u16)(rd32(p) >> 16);
            d->el[d->n].offset = (u16)rd32(p);
            d->el[d->n].type = rd32(p + 4);
            d->el[d->n].usage = (u8)(rd32(p + 8) >> 16);
            d->el[d->n].index = (u8)(rd32(p + 8) >> 8);
        }
        {   /* the library's own layout, which the game's VertexShader::Bind (Too Human 82749348, kept as guest code) and its fetch patcher 82747ed8 read:
             * the element count at +0x18, the highest stream at +0x1c and the elements as given at +0x34. Left empty, Bind patched every fetch as
             * having no element (0, 0, 0, 1) and the rectangles drew nothing */
            unsigned k, top = 0;
            for (k = 0; k < d->n; k++) {
                if (d->el[k].stream > top) top = d->el[k].stream;
                wr32(d->guest + 0x34u + 12u * k, rd32((u32)a->r[0] + 12u * k));
                wr32(d->guest + 0x38u + 12u * k, rd32((u32)a->r[0] + 12u * k + 4u));
                wr32(d->guest + 0x3cu + 12u * k, rd32((u32)a->r[0] + 12u * k + 8u));
            }
            wr32(d->guest + 0x18u, d->n);
            wr32(d->guest + 0x1cu, top);
        }
        s_decl_n++;   /* counted once filled: a draw on another thread looks it up by s_decl_n */
        ReleaseSRWLockExclusive(&lock);
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
        static __declspec(thread) u32 room;   /* with s_begin_data, each thread's own */
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
    case 0x829fc210: {   /* Too Human's BeginVertices(device, primitive type, vertex count, stride), outside the main library range: first stop 829a6240,
                          * (8, 3, 8), the rectangle list of a full-screen pass. The caller copies 24 bytes of vertices into the answer and ends the
                          * vertices itself (it puts back the command buffer pointer from device +0x3484) with no EndVertices call, so the draw waits
                          * for the next Direct3D or state call (xb_lib_entry), the first point the vertices are surely written. Zero is out of memory */
        static __declspec(thread) u32 room;   /* with s_begin_data, each thread's own */
        u32 need = (u32)a->r[2] * (u32)a->r[3];
        if (need > room) {
            room = need * 2 < 0x10000u ? 0x10000u : need * 2;
            s_begin_data = xb_alloc(room);
        }
        s_begin_prim = (unsigned)a->r[1];
        s_begin_n = (unsigned)a->r[2];
        s_begin_stride = (unsigned)a->r[3];
        s_begin_decl = rd32((u32)a->r[0] + 0x2e2cu);   /* set inline just before (829a62cc) and cleared before the draw is made */
        s_begin_pending = 1; wr32((u32)a->r[0] + 0x3484u, rd32((u32)a->r[0] + 0x30u));   /* the ring position the caller puts back at +0x30 (829a6240): 0 left it at 0, and 82744270's flush wrote at 4 */
        *r3 = s_begin_data;
        return 1;
    }
    case 0x829fc6f0: {   /* Too Human's BeginIndexedVertices(device, primitive type, base vertex, vertex count, index count, 1, stride, u16 ** indices, and on
                          * the stack void ** vertices), linked between BeginVertices and DrawVertices: first stop 829b0650 under 8298aea8 in the level load,
                          * (4, 0, 8, 0x24, 1, 0xc), a box of 8 corners and 12 triangles. The body flushes the dirty constants (82747d80) and reserves both in
                          * the command buffer for a DRAW_INDX of 16-bit indices; the caller copies 0x60 bytes of vertices and 0x48 of indices and ends it
                          * inline (+0x3484 into +0x30) as 829fc210's caller does, so the draw waits the same way. Zero is success */
        static __declspec(thread) u32 room;
        u32 st = (u32)a->r[6], nv = (u32)a->r[3], ni = (u32)a->r[4], need = nv * st + ni * 2u + 4u;
        if ((st & 3u) || !st || nv > 0x10000u || ni > 0x100000u) xb_stop("d3d_draw", "BeginIndexedVertices with a stride not of words or past its limits", st);
        if (need > room) {
            room = need * 2 < 0x10000u ? 0x10000u : need * 2;
            s_begin_data = xb_alloc(room);
        }
        s_begin_prim = (unsigned)a->r[1];
        s_begin_n = nv;
        s_begin_stride = st;
        s_begin_index = (s_begin_data + nv * st + 3u) & ~3u;
        s_begin_icount = ni;
        s_begin_decl = rd32((u32)a->r[0] + 0x2e2cu);
        s_begin_pending = 1; wr32((u32)a->r[0] + 0x3484u, rd32((u32)a->r[0] + 0x30u));
        wr32((u32)a->r[7], s_begin_index);
        wr32((u32)a->s[0], s_begin_data);
        *r3 = 0;
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
        u32 b = s_stream[0].buffer, stride = s_stream[0].stride ? s_stream[0].stride : fetch_stride();
        if (!b) xb_stop("d3d_draw", "a draw from a stream with no buffer", 0);
        s_vfirst = (u32)a->r[2];
        u32 vsize;
        draw_vertices((u32)a->r[1], (u32)a->r[3], stream0_at(b, &vsize, stride) + (u32)a->r[2] * stride, stride);
        s_vfirst = 0;
        *r3 = 0;
        return 1;
    }
    case 0x829fcfc0: {   /* Too Human's DrawIndexedVertices(device, primitive type, base vertex, start index, index count), linked after DrawVertices: first stop
                          * 82999c98 on the worker 829a6758 while it records a command buffer, a triangle list. The body reads the index buffer SetIndices bound
                          * (device +0x3094): its data at +0x18, 32-bit indices when the header's top bit is set, and writes DRAW_INDX (0xc0032201). The host
                          * gathers the indexed vertices of stream 0 into one guest block and draws them as DrawVertices does; no restart index (0xffff) */
        static u32 gather, room, *index, index_room; LONG64 t0;   /* t0: XB_SAMPLE's step times */
        static unsigned reset_seen, reset_said;   /* an index of all ones: the console's reset index if the game turned it on (not handled yet: logged, to see whether any draw has one) */
        u32 b = s_stream[0].buffer, ib = s_indices, prim = (u32)a->r[1], base = (u32)a->r[2], first = (u32)a->r[3], n = (u32)a->r[4];
        u32 stride = s_stream[0].stride ? s_stream[0].stride : fetch_stride(), vb, vsize, data, wide, i, k;
        if (!b || !ib) xb_stop("d3d_draw", "an indexed draw with no vertex or index buffer", ib);
        if (!stride && !made_shaders_converted()) { *r3 = 0; return 1; }   /* a made shader with no converted copy (f15a4638 at frame 634): skipped, as draw_vertices does */
        if (!stride) xb_stop("d3d_draw", "an indexed draw with no stride on its stream or in its shader's fetch", s_vs_container);
        data = rd32(ib + 0x18) & ~3u, wide = rd32(ib) >> 31;
        if (xb_gpu_draw_nothing()) { *r3 = 0; return 1; }   /* no pixel shader, z off: before its indices and vertices are read */
        vb = stream0_at(b, &vsize, stride);
        t0 = PART_START(PART_CHECK), k = check_indexed(b, ib, first, n, base, stride, wide, data, vb, vsize), PART_END(PART_CHECK, t0);   if (!k) { *r3 = 0; return 1; }
        if ((stride & 3) || n * stride > 0x1000000u) xb_stop("d3d_draw", "an indexed draw past 16 MB or with a stride not of words", n << 8 | stride);
        if (n * stride > room) {   /* at least doubled: each one outgrown stays in xb_alloc's memory for good, so 64 KB steps left a trail of them */
            u32 want = (n * stride + 0xffffu) & ~0xffffu;
            room = want > 2u * room ? want : 2u * room;
            gather = xb_alloc(room);
        }
        if (n > index_room) {
            index_room = (n + 0xfffu) & ~0xfffu;
            index = (u32 *)realloc(index, index_room * sizeof *index);
        }
        t0 = PART_START(PART_GATHER);
        if (xb_draw_fast()) {   /* the indices only; the vertices when they must be converted (xb_d3d_vtx.h s_lazy) */
            u32 lo = ~0u, top = 0, isz = wide ? 4u : 2u;
            for (i = 0; i < n; i++) {
                u32 idx = wide ? rd32(data + (first + i) * 4) : (u32)rd16(data + (first + i) * 2);
                index[i] = base + idx;   /* the other streams' vertex (stream_vertex) */
                if (idx == (wide ? 0xffffffffu : 0xffffu)) reset_seen++;
                if (idx < lo) lo = idx;
                if (idx > top) top = idx;
            }
            if (reset_seen && reset_said < 4) reset_said++, port_log("d3d indexed draw prim %u, %u indices, with %u of all ones (the reset index): drawn as a vertex, %u draws so far\n", prim, n, reset_seen, reset_said), reset_seen = 0;
            s_vindex = index, s_lazy.vb = vb, s_lazy.stride = stride, s_lazy.n = n, s_lazy.to = gather, s_lazy.hash = 0;
            s_lazy.base = base, s_lazy.vlo = base + lo, s_lazy.vhi = base + top, s_lazy.vok = !vtx_old() && n && base + top >= base;   /* no index wraps past 2^32 */
            if (n && top - lo < 4u * n)   /* the new path's vx_span hashes every index byte (vc_span left the first index out of a 3-index draw) */
                s_lazy.hash = vtx_old() ? vc_mix(vc_mix(vc_span(data + first * isz, n * isz), vc_span(vb + (base + lo) * stride, (top - lo + 1u) * stride)), (unsigned long long)lo << 32 | isz) | 1u
                                        : vc_mix(vc_mix(vx_span(data + first * isz, n * isz), vx_span(vb + (base + lo) * stride, (top - lo + 1u) * stride)), (unsigned long long)lo << 32 | isz) | 1u;
            else lazy_gather();
        } else {
            for (i = 0; i < n; i++) {
                u32 idx = wide ? rd32(data + (first + i) * 4) : (u32)rd16(data + (first + i) * 2);
                for (k = 0; k < stride; k += 4) wr32(gather + i * stride + k, rd32(vb + (base + idx) * stride + k));
                index[i] = base + idx;   /* the other streams' vertex (stream_vertex) */
            }
            s_vindex = index;
        }
        PART_END(PART_GATHER, t0);
        draw_vertices(prim, n, gather, stride);
        s_vindex = 0, s_lazy.n = 0, s_lazy.hash = 0, s_lazy.vok = 0;
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
        if (edram_diag()) {   /* XB_DIAG_EDRAM: the copy's source and destination */
            u32 src = (fl & 7) == 4 ? s_depth : s_target[fl & 3];
            edram_diag_n++;
            u32 rc = (u32)a->r[2], pt = (u32)a->r[4];
            port_log("diag edram frame %u resolve flags %x: source %08x -> %08x, memory %08x; source rect %08x (%d %d %d %d), point %08x (%d %d), predication %x; "
                     "called from %08x %08x %08x%s\n", s_frame, fl,
                     src, dst, dst ? rd32(dst + 0x20u) & 0x1ffff000u : 0, rc, rc ? (int)rd32(rc) : 0, rc ? (int)rd32(rc + 4) : 0, rc ? (int)rd32(rc + 8) : 0,
                     rc ? (int)rd32(rc + 12) : 0, pt, pt ? (int)rd32(pt) : 0, pt ? (int)rd32(pt + 4) : 0, s_predication, lib_up(0), lib_up(1), lib_up(2),
                     t_replay ? " (as recorded)" : "");
            edram_diag_header(src), edram_diag_header(dst);
        }
        /* Too Human (8275e9e0, first stop 829a5858 under 8296aa98) resolves with flags 0x14, the depth surface into the texture header inside the device
         * at +0x5fa0, with bit 0x10 of the 0x70 selection. The host has no copy of depth to give: that resolve is logged and skipped, a known gap */
        /* Bits 26..31 are the exponent bias, a signed power of two the copy scales by: Too Human's 8298f2f8 (under 8299d110) resolves render target 0
         * with ec000000, -5, its bright scene brought into range. Unscaled, every colour over 1/32 was white: the title's background overexposed and the
         * game's frames all white (build box A, 2026-11-06). The host targets are 16-bit float (xb_d3d.c host_tex) so the scene keeps its range until here */
        resolve_sig('R', fl, (fl & 7) == 4 ? s_depth : s_target[fl & 3], dst);
        if (fl >> 26) {
            static int said;
            if (!said++) port_log("d3d Resolve flags %x: exponent bias %d, the copy scaled by 2^%d\n", fl, (int)fl >> 26, (int)fl >> 26);
        }
        if (fl & ~0xfc000377u) xb_stop("d3d_resolve", "resolve flags the host has no case for", fl);
        /* Too Human resolves each tile of a predicated-tiling frame under its own mask: SetPredication(3) Resolve, SetPredication(c) the same Resolve
         * (Xenia's call log of the title, vblank 3300). The host draws the whole frame as one tile, so the first resolve copies all of it and its
         * clear (0x100, 0x200) clears all of it. The second then copied the cleared target over the scene: the title's 3D scene was drawn (the stage
         * picture before it) and the frame went white from frame 170 on (build box A, 2026-11-07). A resolve whose mask names later tiles only is skipped */
        if ((s_predication & 0xffffu) && !(s_predication & 3u)) {
            static int said;
            xb_fcap_resolve_skip("predicated, later tiles only (the host resolved its one tile whole)", dst);
            if (!said++) port_log("d3d Resolve flags %x into %08x under predication %x, later tiles only: skipped, the host's one tile was resolved whole\n", fl, dst, s_predication);
            *r3 = 0;
            return 1;
        }
        if ((fl & 7) > 4) xb_stop("d3d_resolve", "a resolve source past the depth surface", fl);
        D3D_LOG("d3d Resolve flags %x: target %u -> %08x, clear colour block %08x\n", fl, fl & 7, dst, cc);
        if (dst && (fl & 7) != 4) s_last_resolved = dst, s_resolved_ring[s_resolved_at++ & 15u] = dst, resolved_note(dst, 0);
        if ((fl & 7) == 4 && dst && xb_depth_resolve_on() && xb_gpu_depth_now()) {
            /* XB_DEPTH_RESOLVE=1, off by default (see xb_gpu.c xb_depth_resolve_on): the host's depth into the destination. Too Human's intro, menus and
             * game resolve their depth (flags 14, tile mask 3) into a texture its depth-of-field composite (f625a666) and fog (f9fd4dd1, fb4b1c78)
             * sample in a 24_8_FLOAT fetch; with no copy that slot was left unbound and the whole scene blurred (2026-11-14). Earlier notes: Too Human resolves its depth surface (flags 0x14)
             * to 016c0000, which its fog pass (pixel shader fab6ceb9, DepthBufferTexture) samples. On build box A (bk3, bk4, 2026-11-08) the copy read all zero
             * and the black frames stayed, while the logo intro and the scene draws changed colour, which is not explained yet. Under
             * targets/xbox360_ppc/DESIGN_RESOLVE.md it becomes a named XB_HANDOFF entry, and only after layer 1 (copies keyed by address) is right */
            static int said;
            XbHostTex *z = xb_gpu_depth_now();
            if (said++ < 4) port_log("d3d Resolve of the depth surface (flags %x) into %08x, memory %08x: the host's depth %ux%u copied (XB_DEPTH_RESOLVE)\n", fl, dst, rd32(dst + 0x20u) & 0x1ffff000u, xb_gpu_tex_w(z), xb_gpu_tex_h(z));
            xb_gpu_resolve(z, host_tex(dst, 0), 0);
            resolved_note(dst, 1);
            stage_dump("after_resolve", dst, 0);
        } else if ((fl & 7) == 4) {
            static int said;
            if (!said++) port_log("d3d Resolve of the depth surface (flags %x) into %08x: not copied, the host keeps no depth to give\n", fl, dst);
        } else if (dst) {   /* Too Human's 8296a8d0 resolves with no texture, flags 0x300: the two clears only */
            if (!s_target[fl & 3]) {   /* the library reads the source from the device's slot (+0x3098 + 4 * index), which game code may write itself */
                u32 slot = rd32((u32)a->r[0] + 0x3098 + 4 * (fl & 3));
                static int said;
                if (said++ < 8)
                    port_log("d3d Resolve flags %x into %08x with render target %u unbound in the kit; the device's slot holds %08x, own colour surface %08x\n", fl,
                             dst, fl & 3, slot, rd32((u32)a->r[0] + 0x39f0));
                /* Too Human on the fixed clock: the movie player (pixel shader f789b3bb) draws and resolves at frame 2, before the game has bound any
                 * target or set the device's own surface. Its draws went to no host target, so there is nothing to copy: skipped, the texture keeps
                 * what it held. A stop here halted the render thread, and a replay waited at frame 2 for good */
                s_target[fl & 3] = slot;
                s_target_host[fl & 3] = slot ? host_tex(slot, 0) : 0;
            }
            if (s_target[fl & 3]) xb_edram_sync_host(s_target_host[fl & 3]);   /* XB_EDRAM: the copy reads what the tiles hold now */
            if (s_target[fl & 3]) xb_gpu_resolve(s_target_host[fl & 3] ? s_target_host[fl & 3] : host_tex(s_target[fl & 3], 0), host_tex(dst, 0), (int)fl >> 26);
            if (s_target[fl & 3]) stage_dump("after_resolve_src", s_target[fl & 3], 0), stage_dump("after_resolve", dst, 0);
        }
        if (fl & 0x100) {
            if (cc)
                for (i = 0; i < 4; i++) {
                    u32 w = rd32(cc + 4 * i);
                    memcpy(&rgba[i], &w, 4);
                }
            edram_clear_value(s_target[fl & 3], rgba);   /* XB_EDRAM: the value as the target's format holds it */
            xb_gpu_clear(0xF0, rgba, 0, 0);
        }
        /* 0x200 clears the depth surface: z in f1, the stencil the caller's stack word at +0x5c (8275e9e0 reads it at 0x1cc past its 0x170 frame), the low half of s[1] */
        if (fl & 0x200) xb_gpu_clear(0x300, rgba, (float)a->f[0], (u32)a->s[1]);
        if (fl & 0x300) clear_diag("resolve", fl, s_target[fl & 3], cc, rgba, (float)a->f[0], (u32)a->s[1]);
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
    case 0x8274f368: {   /* Release(device), Too Human: first stop 82c45d78 on the job thread 82c44ee8. Above one it takes one off the word at +60 and
                          * returns the count; at one the library destroys the device (82757a18) and frees it, which the kit's one host device does not
                          * do: the count stays and 0 is returned */
        u32 dev = (u32)a->r[0], n = rd32(dev + 60);
        if (n != 1) wr32(dev + 60, n - 1);
        else D3D_LOG("d3d Release(device %08x) at one: the device is kept\n", dev);
        *r3 = n == 1 ? 0 : n - 1;
        return 1;
    }
    case 0x821c6800:    /* (device): first stop, 82093550 in the start-up function, the game's wrapper of the device at 8765ca64. The body takes the device's
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
        u32 front = (u32)a->r[1], shown = front, base = rd32(front + 0x20) & ~0xfffu, match = 0;
        XbHostTex *t;
        unsigned k;
        /* The console scans out the front buffer texture's memory, and the host keeps a texture per header: the newest Resolve into a header over the
         * same memory (base address at +0x20) is what it shows. Too Human's gameplay frame ends with resolves into other headers (the composite into
         * 4047f9e4, then a small pass into 400febc0), so the last Resolve alone showed the wrong one, black */
        for (k = 1; base && k <= 16u && !match; k++) {
            u32 h = s_resolved_ring[(s_resolved_at - k) & 15u];
            if (h && (rd32(h + 0x20) & ~0xfffu) == base) match = h;
        }
        if (match) shown = match;
        else if (front != s_last_resolved && s_last_resolved && !find_res(front))
            shown = s_last_resolved;   /* Too Human's Swap (8274fd28, from 8296a718) names the device's own front buffer header right after a Resolve into another */
        {
            static u32 was_front, was_shown;
            static int said;
            if ((front != was_front || shown != was_shown) && said++ < (getenv("XB_SWAP_LOG_N") ? atoi(getenv("XB_SWAP_LOG_N")) : 16))   /* XB_SWAP_LOG_N: more changes logged */
                port_log("d3d Swap at frame %u of %08x (base %08x, %s) shows %08x (base %08x, %s); last Resolve %08x (base %08x)\n", s_frame, front, base,
                         find_res(front) ? "a kit resource" : "not the kit's", shown, rd32(shown + 0x20) & ~0xfffu, match ? "same memory" : "no resolve over it",
                         s_last_resolved, s_last_resolved ? rd32(s_last_resolved + 0x20) & ~0xfffu : 0u);
            was_front = front, was_shown = shown;
        }
        front = shown;
        t = host_tex(front, 0);
        {   /* The display's gamma ramp (xb_xenos_gamma.h): the console sends a 2_10_10_10 front buffer out through the ramp the game set with
             * SetGammaRamp (8274ef90, kept by 82754110 at device +0x3a9c). XB_GAMMA_RAMP=0 shows the buffer as it is */
            u32 dev = (u32)a->r[0], fmt = (u32)a->r[1] ? rd32((u32)a->r[1] + 0x20) & 63u : 0u, pairs[384], any = 0;
            static u32 said, was_first, was_last;
            if (xb_gamma_ramp_on() && dev && (fmt == 7u || fmt == 54u))
                for (k = 0; k < 384u; k++) any |= pairs[k] = rd32(dev + 0x3a9cu + 4u * k);
            xb_gpu_gamma_ramp(any ? pairs : 0);
            if (said < 4u && (!said || (any && (pairs[0] != was_first || pairs[383] != was_last)))) {
                said++;
                port_log("d3d gamma ramp (XB_GAMMA_RAMP=0 turns it off): front buffer format %u, %s; red 0 %08x, red 127 %08x, blue 127 %08x\n", fmt,
                         any ? "shown through the ramp" : xb_gamma_ramp_on() ? "no ramp set or not 2_10_10_10, shown as it is" : "off",
                         any ? pairs[0] : 0u, any ? pairs[127] : 0u, any ? pairs[383] : 0u);
                if (any) was_first = pairs[0], was_last = pairs[383];
            }
        }
        xb_gpu_present(t);
        if (getenv("XB_LOG_FROM") && s_frame == (unsigned)atoi(getenv("XB_LOG_FROM"))) s_log_left = getenv("XB_LOG_N") ? atoi(getenv("XB_LOG_N")) : 400;   /* the order of calls at a later frame; XB_LOG_N for more than 400 */
        {   /* XB_DUMP_AT: more frames to dump, comma apart (2885,3100): moments off the XB_DUMP_EVERY grid, compared side by side between runs. A name and an
             * offset (fog+0,fog+50) count from the scene marker's latest start (xb_d3d.c mark_draw) and are dumped as mark_<offset>_frame_<frame>.bmp */
            const char *at = getenv("XB_DUMP_AT");
            unsigned mk = xb_mark_frame();
            while (dir && at && *at) {
                char *e, path[260];
                const char *p = at;
                int rel = 0;
                unsigned long v;
                while (*p && *p != ',' && *p != '+' && !(*p >= '0' && *p <= '9')) p++, rel = 1;
                if (*p == '+') p++;
                v = strtoul(p, &e, 10);
                if (rel ? mk != ~0u && s_frame == mk + v : v == s_frame) {
                    if (rel) sprintf_s(path, sizeof path, "%s/mark_%lu_frame_%05u.bmp", dir, v, s_frame);
                    else sprintf_s(path, sizeof path, "%s/frame_%05u.bmp", dir, s_frame);
                    xb_gpu_dump(t, path);
                    if (rel) port_log("dump at scene marker + %lu: frame %u\n", v, s_frame);
                }
                while (*e && *e != ',') e++;
                at = *e ? e + 1 : e;
            }
        }
        if (dir && (!every || atoi(every) > 0) && s_frame % (unsigned)(every ? atoi(every) : 60) == 0) {   /* XB_DUMP_EVERY 0 or less: none */
            char path[260];
            sprintf_s(path, sizeof path, "%s/frame_%05u.bmp", dir, s_frame);
            xb_gpu_dump(t, path);
        }
        watch_poll();
        xb_databp_frame(s_frame);   /* XB_MEMLOG's census (xb_mem.c), then the kit's own line for the same frame */
        res_drain();
        kit_census(s_frame);
        cb_reclaim();
        if (s_vre_n) vfetch_row_recheck();
        s_frame++;
        xb_tick_end();
        *r3 = 0;
        return 1;
    }
    case 0x8274f7c0:     /* Too Human's (device, 0, 0): first stop 8296a718, right before Swap. The body maps the presentation interval at +0x351c
                          * (0, 1, 2, 4, 0x80000000) into a packet and appends packets to the command buffer; nothing the game reads comes back */
        return state_only(target, a, r3, "device call before Swap (presentation interval packets)");
    case 0x82761d90:     /* Too Human's SetPredication(device, tile mask): first stop 829a5858 with 3, both tiles of BeginTiling. The body keeps the mask at +0x31a4
                          * and appends one packet (c0006000, mask). The host draws without tiles, so every draw is for the whole frame; the mask is kept
                          * for Resolve, which skips the resolves of later tiles */
        s_predication = (u32)a->r[1];
        return state_only(target, a, r3, "SetPredication (the host has no tiles)");
    case 0x821ce2e8:     /* (device): first stop, 82093a98 at 82093ca0 right after the draw and before the resolve. The entry loads r4 = 0 and
                          * joins a body shared with other callers (821cdec0) that writes packets into the library's command ring
                          * (device +48) and returns nothing the game reads. Nothing is drawn through a ring here: the draw has
                          * already landed in the host target */
        return state_only(target, a, r3, "device call after a draw (ring packets only)");
    case 0x821d5110:     /* (device, 2): first stop, 82093cf0 at 820941bc, the value from a field of the start-up object. The library
                          * inserts it into bits 12..13 of the device's register-state word at +10916 and appends one packet to its
                          * own command buffer (device +48 write pointer, +56 end): a GPU state setter with no D3D11 counterpart */
        return state_only(target, a, r3, "register-state field (bits 12..13)");
    case 0x82758c98:     /* SetShaderGPRAllocation(device, flags, vertex count, pixel count): Too Human, first stop 8296aa98 with (0, 0x24, 0x5c). Both 0
                          * means 64 and 64; the split goes into the register word at +0x2aa8 and three ring packets reload the bound shaders. The
                          * GPU's register split has no D3D11 counterpart. Mega Man 10 never calls it, so the name is Too Human's address */
        return state_only(target, a, r3, "shader GPR allocation (vertex, pixel)");
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
        memset(s_target_host, 0, sizeof s_target_host);
        memset(s_texture, 0, sizeof s_texture);
        memset(s_stream, 0, sizeof s_stream);
        s_depth = s_bound_decl = s_bound_vs = s_bound_ps = s_indices = 0;
        *r3 = 0;
        return 1;
    }
    case 0x8274f410: {   /* Too Human's device reset (device): first stop 829a4aa0 right after the devices, then on the render thread between frames. A render
                          * target is set to none only when it is not the device's own colour surface (+0x39f0), the depth surface only when it is not the
                          * device's own (+0x39e8); then samplers, streams, shaders and indices to none, as 821c6920 */
        u32 dev = (u32)a->r[0], own = rd32(dev + 0x39f0), own_depth = rd32(dev + 0x39e8);
        unsigned i;
        if (s_target[0] && s_target[0] != own) {
            static int said;
            if (said++ < 20) port_log("d3d device reset unbinds render target 0 (%08x) on host thread %lu\n", s_target[0], GetCurrentThreadId());
        }
        for (i = 0; i < 4; i++)
            if (s_target[i] != own) { s_target[i] = 0; s_target_host[i] = 0; wr32(dev + 0x3098 + 4 * i, 0); xb_gpu_bind_target(i, 0); }
        if (s_depth != own_depth) { s_depth = 0; wr32(dev + 0x30a8, 0); xb_gpu_bind_depth(0); }
        memset(s_texture, 0, sizeof s_texture);
        memset(s_stream, 0, sizeof s_stream);
        s_bound_decl = s_bound_vs = s_bound_ps = s_indices = 0;
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
