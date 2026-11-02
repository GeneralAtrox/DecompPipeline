/* xb_gpu.c, shaders, blending, sampling, the draw, resolve, present and the frame dump (one translation unit with it, for its device; here for the 400-line cap) */
static HostShader *shader_for(const XbShader *s)
{
    unsigned i;
    HostShader *h;
    for (i = 0; i < s_shader_n; i++)
        if (s_shader[i].addr == s->addr) return &s_shader[i];
    if (s_shader_n >= sizeof s_shader / sizeof *s_shader) xb_stop("d3d_host", "more than 64 shaders in use", s->addr);
    h = &s_shader[s_shader_n++];
    h->addr = s->addr;
    if (s->is_ps) {
        ID3DBlob *code = compile(s->hlsl, "ps_5_0", "pixel shader");
        ID3D11Device_CreatePixelShader(s_dev, ID3D10Blob_GetBufferPointer(code), ID3D10Blob_GetBufferSize(code), 0, &h->ps);
        ID3D10Blob_Release(code);
    } else {
        h->vs_code = compile(s->hlsl, "vs_5_0", "vertex shader");
        ID3D11Device_CreateVertexShader(s_dev, ID3D10Blob_GetBufferPointer(h->vs_code), ID3D10Blob_GetBufferSize(h->vs_code), 0, &h->vs);
        if (s->gs_rect) {
            ID3DBlob *code = compile(s->gs_rect, "gs_5_0", "rect list geometry shader");
            ID3D11Device_CreateGeometryShader(s_dev, ID3D10Blob_GetBufferPointer(code), ID3D10Blob_GetBufferSize(code), 0, &h->gs);
            ID3D10Blob_Release(code);
        }
    }
    port_log("d3d host: shader %08x converted from the offline HLSL and compiled\n", s->addr);
    return h;
}

const XbShader *xb_shader_find(u32 addr)
{
    unsigned i;
    for (i = 0; i < xb_shader_count; i++)
        if (xb_shader_table[i].addr == addr) return &xb_shader_table[i];
    return 0;
}

void xb_gpu_set_constants(int pixel, unsigned first, unsigned count, const float *v)
{
    if (first + count > 256) count = first < 256 ? 256 - first : 0;
    memcpy(&s_const[pixel ? 1 : 0][first * 4], v, count * 16);
}

/* Draw nverts vertices of nattr float4 attributes each (the vertex shader's inputs, in its fetch order). prim is the platform's
 * primitive type: 4 triangle list, 6 triangle strip, 8 rectangle list (three vertices, the fourth derived by the geometry shader). */
/* The game's blend states as the host's, made once for each combination. The numbers are the rows of 824b0900 and what the game passes: render states 60 enable, 64 separate alpha,
 * 72/76/80 source, destination, operation, 84/88/92 the same for alpha; blend 0 zero, 1 one, 4 source colour, 5 its inverse, 6 source alpha, 7 its inverse, 8 destination
 * colour, 9 its inverse, 10 destination alpha, 11 its inverse */
static D3D11_BLEND host_blend(u32 v)
{
    switch (v) {
    case 0: return D3D11_BLEND_ZERO;
    case 1: return D3D11_BLEND_ONE;
    case 4: return D3D11_BLEND_SRC_COLOR;
    case 5: return D3D11_BLEND_INV_SRC_COLOR;
    case 6: return D3D11_BLEND_SRC_ALPHA;
    case 7: return D3D11_BLEND_INV_SRC_ALPHA;
    case 8: return D3D11_BLEND_DEST_COLOR;
    case 9: return D3D11_BLEND_INV_DEST_COLOR;
    case 10: return D3D11_BLEND_DEST_ALPHA;
    case 11: return D3D11_BLEND_INV_DEST_ALPHA;
    }
    xb_stop("d3d_state", "a blend factor the host has no case for", v);
    return D3D11_BLEND_ONE;
}

static ID3D11BlendState *blend_for_draw(void)
{
    static struct { u32 key[6]; ID3D11BlendState *st; } cache[64];
    static unsigned n;
    u32 key[6];
    unsigned i;
    D3D11_BLEND_DESC bl;
    if (!xb_rs(60)) return s_blend;
    key[0] = xb_rs(72); key[1] = xb_rs(76); key[2] = xb_rs(80);
    key[3] = xb_rs(64) ? xb_rs(84) : key[0];
    key[4] = xb_rs(64) ? xb_rs(88) : key[1];
    key[5] = xb_rs(64) ? xb_rs(92) : key[2];
    for (i = 0; i < n; i++)
        if (!memcmp(cache[i].key, key, sizeof key)) return cache[i].st;
    if (n == 64) xb_stop("d3d_state", "more than 64 blend states", n);
    if (key[2] || key[5]) xb_stop("d3d_state", "a blend operation the host has no case for", key[2]);
    memset(&bl, 0, sizeof bl);
    bl.RenderTarget[0].BlendEnable = TRUE;
    bl.RenderTarget[0].SrcBlend = host_blend(key[0]);
    bl.RenderTarget[0].DestBlend = host_blend(key[1]);
    bl.RenderTarget[0].BlendOp = D3D11_BLEND_OP_ADD;
    bl.RenderTarget[0].SrcBlendAlpha = host_blend(key[3] == 4 || key[3] == 5 ? key[3] + 2 : key[3]);
    bl.RenderTarget[0].DestBlendAlpha = host_blend(key[4] == 4 || key[4] == 5 ? key[4] + 2 : key[4]);
    bl.RenderTarget[0].BlendOpAlpha = D3D11_BLEND_OP_ADD;
    bl.RenderTarget[0].RenderTargetWriteMask = D3D11_COLOR_WRITE_ENABLE_ALL;
    memcpy(cache[n].key, key, sizeof key);
    ID3D11Device_CreateBlendState(s_dev, &bl, &cache[n].st);
    return cache[n++].st;
}

/* The sampler a slot's states ask for: sampler states 0 and 4 address (0 wrap, 1 mirror, 2 clamp), 16 and 20 mag and min filter (0 point, 1 linear) */
static ID3D11SamplerState *sampler_for_draw(unsigned slot)
{
    static ID3D11SamplerState *cache[3][3][2];
    u32 u = xb_ss(slot, 0), v = xb_ss(slot, 4), lin = xb_ss(slot, 16) || xb_ss(slot, 20);
    static const D3D11_TEXTURE_ADDRESS_MODE am[3] = {D3D11_TEXTURE_ADDRESS_WRAP, D3D11_TEXTURE_ADDRESS_MIRROR, D3D11_TEXTURE_ADDRESS_CLAMP};
    if (u > 2 || v > 2) xb_stop("d3d_state", "a texture address mode the host has no case for", u << 8 | v);
    if (!cache[u][v][lin]) {
        D3D11_SAMPLER_DESC d;
        memset(&d, 0, sizeof d);
        d.Filter = lin ? D3D11_FILTER_MIN_MAG_MIP_LINEAR : D3D11_FILTER_MIN_MAG_MIP_POINT;
        d.AddressU = am[u];
        d.AddressV = am[v];
        d.AddressW = D3D11_TEXTURE_ADDRESS_CLAMP;
        d.MaxLOD = D3D11_FLOAT32_MAX;
        ID3D11Device_CreateSamplerState(s_dev, &d, &cache[u][v][lin]);
    }
    return cache[u][v][lin];
}

void xb_gpu_draw(unsigned prim, unsigned nverts, unsigned nattr, const float *vdata)
{
    const XbShader *vsd = xb_shader_find(s_vs_addr), *psd = xb_shader_find(s_ps_addr);
    HostShader *vs, *ps;
    D3D11_BUFFER_DESC bd;
    D3D11_SUBRESOURCE_DATA sr;
    ID3D11Buffer *vb;
    D3D11_MAPPED_SUBRESOURCE m;
    ID3D11ShaderResourceView *srv[16];
    unsigned i, stride = nattr * 16;
    D3D11_PRIMITIVE_TOPOLOGY topo;
    float bf[4] = {0, 0, 0, 0};

    gpu_init();
    if (!vsd) xb_stop("d3d_draw", "the bound vertex shader is not one of the converted containers", s_vs_addr);
    if (!psd) xb_stop("d3d_draw", "the bound pixel shader is not one of the converted containers", s_ps_addr);
    if (!vsd->hlsl) xb_stop("d3d_draw", "the vertex shader has an opcode the converter cannot do (shaders.json names it)", s_vs_addr);
    if (!psd->hlsl) xb_stop("d3d_draw", "the pixel shader has an opcode the converter cannot do (shaders.json names it)", s_ps_addr);
    if (vsd->n_inputs != nattr) xb_stop("d3d_draw", "the declaration has a different number of elements than the shader fetches", nattr);
    if (prim == 8 && !vsd->gs_rect) xb_stop("d3d_draw", "no rectangle-list geometry shader for this vertex shader", s_vs_addr);
    vs = shader_for(vsd);
    ps = shader_for(psd);
    if (!vs->layout || vs->layout_n != nattr) {
        D3D11_INPUT_ELEMENT_DESC el[16];
        if (nattr > 16) xb_stop("d3d_draw", "more than 16 vertex attributes", nattr);
        for (i = 0; i < nattr; i++) {
            el[i].SemanticName = "ATTR";
            el[i].SemanticIndex = i;
            el[i].Format = DXGI_FORMAT_R32G32B32A32_FLOAT;
            el[i].InputSlot = 0;
            el[i].AlignedByteOffset = i * 16;
            el[i].InputSlotClass = D3D11_INPUT_PER_VERTEX_DATA;
            el[i].InstanceDataStepRate = 0;
        }
        if (vs->layout) ID3D11InputLayout_Release(vs->layout);
        if (FAILED(ID3D11Device_CreateInputLayout(s_dev, el, nattr, ID3D10Blob_GetBufferPointer(vs->vs_code), ID3D10Blob_GetBufferSize(vs->vs_code), &vs->layout)))
            xb_stop("d3d_draw", "input layout does not match the vertex shader", s_vs_addr);
        vs->layout_n = nattr;
    }
    memset(&bd, 0, sizeof bd);
    bd.ByteWidth = stride * nverts;
    bd.Usage = D3D11_USAGE_IMMUTABLE;
    bd.BindFlags = D3D11_BIND_VERTEX_BUFFER;
    sr.pSysMem = vdata;
    sr.SysMemPitch = sr.SysMemSlicePitch = 0;
    ID3D11Device_CreateBuffer(s_dev, &bd, &sr, &vb);

    for (i = 0; i < 2; i++) {
        if (SUCCEEDED(ID3D11DeviceContext_Map(s_ctx, (ID3D11Resource *)s_cb[i], 0, D3D11_MAP_WRITE_DISCARD, 0, &m))) {
            memcpy(m.pData, s_const[i], 256 * 16);
            ID3D11DeviceContext_Unmap(s_ctx, (ID3D11Resource *)s_cb[i], 0);
        }
    }
    bind_targets();
    ID3D11DeviceContext_VSSetConstantBuffers(s_ctx, 0, 1, &s_cb[0]);
    ID3D11DeviceContext_PSSetConstantBuffers(s_ctx, 1, 1, &s_cb[1]);
    if (SUCCEEDED(ID3D11DeviceContext_Map(s_ctx, (ID3D11Resource *)s_kit_cb, 0, D3D11_MAP_WRITE_DISCARD, 0, &m))) {
        /* render state 304 off (the library writes 0x400 where on is 0x43f to the viewport control): the vertices are the game's pixels, whose centres are at whole numbers.
         * Render states 96 alpha test, 100 reference, 104 comparison; 7 is always */
        float *k = (float *)m.pData;
        k[0] = 2.0f / (float)s_game_w;
        k[1] = 2.0f / (float)s_game_h;
        k[2] = xb_rs(304) ? 0.0f : 1.0f;
        k[3] = 0;
        k[4] = (float)xb_rs(100);
        k[5] = xb_rs(96) ? (float)xb_rs(104) : 7.0f;
        k[6] = k[7] = 0;
        ID3D11DeviceContext_Unmap(s_ctx, (ID3D11Resource *)s_kit_cb, 0);
    }
    ID3D11DeviceContext_VSSetConstantBuffers(s_ctx, 2, 1, &s_kit_cb);
    ID3D11DeviceContext_PSSetConstantBuffers(s_ctx, 2, 1, &s_kit_cb);
    {
        ID3D11SamplerState *sm[16];
        for (i = 0; i < 16; i++) sm[i] = s_tex[i] ? sampler_for_draw(i) : s_sampler;
        ID3D11DeviceContext_PSSetSamplers(s_ctx, 0, 16, sm);
    }
    for (i = 0; i < 16; i++) srv[i] = s_tex[i] ? s_tex[i]->srv : 0;
    ID3D11DeviceContext_PSSetShaderResources(s_ctx, 0, 16, srv);
    ID3D11DeviceContext_RSSetState(s_ctx, s_raster);
    ID3D11DeviceContext_OMSetBlendState(s_ctx, blend_for_draw(), bf, 0xFFFFFFFF);
    ID3D11DeviceContext_OMSetDepthStencilState(s_ctx, s_depth_off, 0);
    ID3D11DeviceContext_IASetInputLayout(s_ctx, vs->layout);
    {
        UINT st = stride, off = 0;
        ID3D11DeviceContext_IASetVertexBuffers(s_ctx, 0, 1, &vb, &st, &off);
    }
    topo = prim == 6 ? D3D11_PRIMITIVE_TOPOLOGY_TRIANGLESTRIP : D3D11_PRIMITIVE_TOPOLOGY_TRIANGLELIST;
    ID3D11DeviceContext_IASetPrimitiveTopology(s_ctx, topo);
    ID3D11DeviceContext_VSSetShader(s_ctx, vs->vs, 0, 0);
    ID3D11DeviceContext_GSSetShader(s_ctx, prim == 8 ? vs->gs : 0, 0, 0);
    ID3D11DeviceContext_PSSetShader(s_ctx, ps->ps, 0, 0);
    ID3D11DeviceContext_Draw(s_ctx, nverts, 0);
    ID3D11Buffer_Release(vb);
    s_draws++;
}

/* The game's resolve: its render target copied into a texture it samples later (the original's copy out of the render target into memory; here both are host
 * textures, so it is one copy). Both are made at the same output scale; a size or format mismatch stops. */
void xb_gpu_resolve(XbHostTex *src, XbHostTex *dst)
{
    gpu_init();
    if (!src || !dst || !src->tex || !dst->tex) xb_stop("d3d_resolve", "a resolve with a missing host surface", 0);
    {   /* a target is rounded up to its tiles (the first such, 82093a98's texture at the object +256, is the frame's size and the target taller): the part both have */
        D3D11_BOX box;
        static int said;
        box.left = box.top = box.front = 0;
        box.right = src->w < dst->w ? src->w : dst->w;
        box.bottom = src->h < dst->h ? src->h : dst->h;
        box.back = 1;
        if ((src->w != dst->w || src->h != dst->h) && !said++) port_log("d3d resolve %ux%u into %ux%u\n", src->w, src->h, dst->w, dst->h);
        ID3D11DeviceContext_CopySubresourceRegion(s_ctx, (ID3D11Resource *)dst->tex, 0, 0, 0, 0, (ID3D11Resource *)src->tex, 0, &box);
    }
}

/* Show the game's frame: the first bound target, letterboxed in the output, never stretched. */
void xb_gpu_present(XbHostTex *src)
{
    D3D11_VIEWPORT vp;
    float clear[4] = {0, 0, 0, 1};
    float ar = (float)s_game_w / (float)s_game_h, w, h;
    ID3D11ShaderResourceView *srv;
    gpu_init();
    if (GetCurrentThreadId() != s_gfx_thread) xb_stop("d3d_thread", "a frame shown from another thread than the window's", 0);
    if (!src || !src->srv) return;
    w = (float)g_gfx.frame_w;
    h = w / ar;
    if (h > (float)g_gfx.frame_h) {
        h = (float)g_gfx.frame_h;
        w = h * ar;
    }
    vp.TopLeftX = ((float)g_gfx.frame_w - w) * 0.5f;
    vp.TopLeftY = ((float)g_gfx.frame_h - h) * 0.5f;
    vp.Width = w;
    vp.Height = h;
    vp.MinDepth = 0;
    vp.MaxDepth = 1;
    ID3D11DeviceContext_OMSetRenderTargets(s_ctx, 1, &g_gfx.frame_rtv, 0);
    ID3D11DeviceContext_ClearRenderTargetView(s_ctx, g_gfx.frame_rtv, clear);
    ID3D11DeviceContext_RSSetViewports(s_ctx, 1, &vp);
    ID3D11DeviceContext_RSSetState(s_ctx, s_raster);
    srv = src->srv;
    ID3D11DeviceContext_PSSetShaderResources(s_ctx, 0, 1, &srv);
    ID3D11DeviceContext_PSSetSamplers(s_ctx, 0, 1, &s_sampler);
    ID3D11DeviceContext_IASetPrimitiveTopology(s_ctx, D3D11_PRIMITIVE_TOPOLOGY_TRIANGLELIST);
    ID3D11DeviceContext_IASetInputLayout(s_ctx, 0);
    ID3D11DeviceContext_VSSetShader(s_ctx, s_blit_vs, 0, 0);
    ID3D11DeviceContext_GSSetShader(s_ctx, 0, 0, 0);
    ID3D11DeviceContext_PSSetShader(s_ctx, s_blit_ps, 0, 0);
    ID3D11DeviceContext_Draw(s_ctx, 3, 0);
    srv = 0;
    ID3D11DeviceContext_PSSetShaderResources(s_ctx, 0, 1, &srv);
    if (!g_gfx.headless) {
        present_show();
        gfx_pump();
    }
    s_frames++;
}

/* Writes a texture as a 24-bit BMP (a readback, so a test run can show the picture without a window). */
static int dump_tex(ID3D11Texture2D *tex, const char *path)
{
    D3D11_TEXTURE2D_DESC d;
    ID3D11Texture2D *st = 0;
    D3D11_MAPPED_SUBRESOURCE m;
    FILE *f;
    unsigned y, x, row;
    unsigned char hdr[54], *line;
    unsigned long sz;
    int done = 0;
    ID3D11Texture2D_GetDesc(tex, &d);
    d.Usage = D3D11_USAGE_STAGING;
    d.BindFlags = 0;
    d.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
    if (FAILED(ID3D11Device_CreateTexture2D(s_dev, &d, 0, &st))) return 0;
    ID3D11DeviceContext_CopyResource(s_ctx, (ID3D11Resource *)st, (ID3D11Resource *)tex);
    if (FAILED(ID3D11DeviceContext_Map(s_ctx, (ID3D11Resource *)st, 0, D3D11_MAP_READ, 0, &m))) {
        ID3D11Texture2D_Release(st);
        return 0;
    }
    row = (d.Width * 3 + 3) & ~3u;
    line = (unsigned char *)calloc(1, row);
    f = fopen(path, "wb");
    if (f && line) {
        sz = 54 + row * d.Height;
        memset(hdr, 0, sizeof hdr);
        hdr[0] = 'B'; hdr[1] = 'M';
        memcpy(hdr + 2, &sz, 4);
        hdr[10] = 54; hdr[14] = 40;
        memcpy(hdr + 18, &d.Width, 4);
        memcpy(hdr + 22, &d.Height, 4);
        hdr[26] = 1; hdr[28] = 24;
        fwrite(hdr, 1, 54, f);
        for (y = d.Height; y-- > 0;) {
            const unsigned char *px = (const unsigned char *)m.pData + y * m.RowPitch;
            for (x = 0; x < d.Width; x++) {   /* RGBA texture to BGR file */
                line[x * 3] = px[x * 4 + 2];
                line[x * 3 + 1] = px[x * 4 + 1];
                line[x * 3 + 2] = px[x * 4];
            }
            fwrite(line, 1, row, f);
        }
        done = 1;
    }
    if (f) fclose(f);
    free(line);
    ID3D11DeviceContext_Unmap(s_ctx, (ID3D11Resource *)st, 0);
    ID3D11Texture2D_Release(st);
    return done;
}

void xb_gpu_dump(XbHostTex *t, const char *path)
{
    if (t && t->tex) dump_tex(t->tex, path);
}

int xb_gpu_capture(const char *path) { return g_gfx.frame ? dump_tex(g_gfx.frame, path) : 0; }

XbHostTex *xb_gpu_main_target(void) { return s_target[0]; }
unsigned xb_gpu_draw_count(void) { return s_draws; }
unsigned xb_gpu_frame_count(void) { return s_frames; }
