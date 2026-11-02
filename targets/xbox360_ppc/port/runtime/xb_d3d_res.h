/* xb_d3d.c, the entries that make and bind things (one translation unit with it, for its state; here for the 400-line cap) */
static int entry_res(u32 target, const TcArgs *a, u64 *r3)
{
    (void)a;
    switch (target) {
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
        xb_gpu_viewport((float)s_view.x, (float)s_view.y, (float)s_view.w, (float)s_view.h, s_view.zmin, s_view.zmax);
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
        wr32(h + 24, xb_alloc(len) | 3u);
        wr32(h + 28, (len & 0x03FFFFFCu) | 0x10000002u);
        *r3 = h;
        return 1;
    }
    case 0x821d8300:     /* lock a vertex buffer (header, offset, size, flags): first stop, 820a52e0 under 82093230, which stores the result and copies its vertices there. The
                          * body waits for the GPU and hands back the fetch address; it never reads the offset. Nothing else uses the data until a draw */
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
        D3D_LOG("d3d SetRenderTarget %u := %08x (%s)\n", idx, surf, r ? r->kind : "not a made resource");
        xb_gpu_bind_target(idx, surf ? host_tex(surf, 0) : 0);
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
        if (dst) xb_gpu_resolve(host_tex(s_target[0], 0), host_tex(dst, 0));
        if (col)
            for (i = 0; i < 4; i++) {
                u32 w = rd32(col + 4 * i);
                memcpy(&rgba[i], &w, 4);
            }
        if (fl & 0x100) xb_gpu_clear(0xF0, rgba, 0, 0);
        if (fl & 0x200) xb_gpu_clear(0x300, rgba, a->f[0], (u32)a->r[5]);
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
        }
        D3D_LOG("d3d constants %s c%u: %g %g %g %g\n", target == 0x821ccf78 ? "pixel" : "vertex", first, v[0], v[1], v[2], v[3]);
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
