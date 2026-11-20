/* The Xbox 360 kit's records for the frame capture (port/base/frame_capture.h): included at the end of xb_gpu.c, so it sees the bound state.
 * Each draw (drawn, or left with its reason), clear, resolve (with a picture of the source target) and present of the recorded frame, in order.
 * Off, every entry point returns at its test of g_fcap_on. */
#include "frame_capture.h"
static unsigned s_fcap_after;   /* the chosen draw being recorded (XB_FCAP_DRAWS / XB_FCAP_PS), ~0u none: set below */

/* A small number for a host texture, so the records name targets and textures by the same id through the frame */
static unsigned fcap_id(const XbHostTex *t)
{
    static const XbHostTex *seen[256];
    static unsigned n;
    unsigned i;
    if (!t) return 0;
    for (i = 0; i < n; i++)
        if (seen[i] == t) return i + 1;
    if (n < 256) seen[n++] = t;
    return n;
}

static const char *fcap_guest_fmt(int f)
{
    static const char *const name[] = {"1_REVERSE", "1", "8", "1_5_5_5", "5_6_5", "6_5_5", "8_8_8_8", "2_10_10_10", "8_A", "8_B", "8_8", "Cr_Y1_Cb_Y0", "Y1_Cr_Y0_Cb",
                                       "13", "8_8_8_8_A", "4_4_4_4", "10_11_11", "11_11_10", "DXT1", "DXT2_3", "DXT4_5", "21", "24_8", "24_8_FLOAT", "16", "16_16",
                                       "16_16_16_16", "16_EXPAND", "16_16_EXPAND", "16_16_16_16_EXPAND", "16_FLOAT", "16_16_FLOAT", "16_16_16_16_FLOAT", "32",
                                       "32_32", "32_32_32_32", "32_FLOAT", "32_32_FLOAT", "32_32_32_32_FLOAT", "32_AS_8", "32_AS_8_8", "16_MPEG", "16_16_MPEG",
                                       "8_INTERLACED", "32_AS_8_INTERLACED", "32_AS_8_8_INTERLACED", "16_INTERLACED", "16_MPEG_INTERLACED", "16_16_MPEG_INTERLACED",
                                       "DXN", "8_8_8_8_AS_16_16_16_16", "DXT1_AS_16_16_16_16", "DXT2_3_AS_16_16_16_16", "DXT4_5_AS_16_16_16_16",
                                       "2_10_10_10_AS_16_16_16_16", "10_11_11_AS_16_16_16_16", "11_11_10_AS_16_16_16_16", "32_32_32_FLOAT", "DXT3A", "DXT5A", "CTX1",
                                       "DXT3A_AS_1_1_1_1", "8_8_8_8_GAMMA_EDRAM", "2_10_10_10_FLOAT_EDRAM"};
    return f >= 0 && f < (int)(sizeof name / sizeof *name) ? name[f] : "?";
}

/* A host texture as a PNG at most 640 wide (each n-th texel), colour clamped as the screen shows it; *alpha_mean the mean alpha 0..1. 1 if written */
static int fcap_tex_png(XbHostTex *t, const char *name, double *alpha_mean)
{
    unsigned w, h, ow, oh, x, y, step;
    int half;
    unsigned char *p, *o;
    double asum = 0;
    *alpha_mean = -1;
    if (!t || !t->tex || t->depth) return 0;
    p = tex_read(t->tex, 1, &w, &h, &half);
    if (!p) return 0;
    step = (w + 639) / 640;
    ow = w / step, oh = h / step;
    o = (unsigned char *)malloc((size_t)ow * oh * 4);
    if (!o) {
        free(p);
        return 0;
    }
    for (y = 0; y < oh; y++)
        for (x = 0; x < ow; x++) {
            size_t k = (size_t)y * step * w + (size_t)x * step;
            unsigned c;
            for (c = 0; c < 4; c++) o[((size_t)y * ow + x) * 4 + c] = (unsigned char)px_chan(p, k, c, half);
            asum += o[((size_t)y * ow + x) * 4 + 3];
        }
    *alpha_mean = ow && oh ? asum / 255.0 / ((double)ow * oh) : -1;
    x = (unsigned)fcap_png(name, ow, oh, o, ow * 4);
    free(o), free(p);
    return (int)x;
}

/* A chosen draw's bound texture (XB_FCAP_DRAWS / XB_FCAP_PS): the host copy's level 0 read back whole, any format, its bytes hashed (FNV-1a 64) and
 * its mean colour 0..1 (8-bit and 16-bit float texels; BC1 to BC3 by each block's two end colours; otherwise -1) */
static void fcap_tex_stats(XbHostTex *t, unsigned long long *hash, double mean[4])
{
    D3D11_TEXTURE2D_DESC d;
    D3D11_MAPPED_SUBRESOURCE m;
    ID3D11Texture2D *st = 0;
    unsigned long long h = 1469598103934665603ull;
    double sum[4] = {0, 0, 0, 0}, cnt = 0;
    unsigned y, x, rows, rowbytes, bc = 0, c;
    *hash = 0, mean[0] = mean[1] = mean[2] = mean[3] = -1;
    if (!t || !t->tex) return;
    ID3D11Texture2D_GetDesc(t->tex, &d);
    if (d.SampleDesc.Count != 1) return;
    switch (d.Format) {
    case DXGI_FORMAT_BC1_UNORM: case DXGI_FORMAT_BC1_UNORM_SRGB: bc = 8; break;
    case DXGI_FORMAT_BC2_UNORM: case DXGI_FORMAT_BC2_UNORM_SRGB: case DXGI_FORMAT_BC3_UNORM: case DXGI_FORMAT_BC3_UNORM_SRGB: bc = 16; break;
    default: break;
    }
    rows = bc ? (d.Height + 3) / 4 : d.Height;
    rowbytes = bc ? (d.Width + 3) / 4 * bc : d.Format == DXGI_FORMAT_R16G16B16A16_FLOAT ? d.Width * 8 : d.Width * 4;
    d.MipLevels = 1, d.ArraySize = 1, d.Usage = D3D11_USAGE_STAGING, d.BindFlags = 0, d.CPUAccessFlags = D3D11_CPU_ACCESS_READ, d.MiscFlags = 0;
    if (FAILED(ID3D11Device_CreateTexture2D(s_dev, &d, 0, &st))) return;
    ID3D11DeviceContext_CopySubresourceRegion(s_ctx, (ID3D11Resource *)st, 0, 0, 0, 0, (ID3D11Resource *)t->tex, 0, 0);
    if (SUCCEEDED(ID3D11DeviceContext_Map(s_ctx, (ID3D11Resource *)st, 0, D3D11_MAP_READ, 0, &m))) {
        if (rowbytes > m.RowPitch) rowbytes = m.RowPitch;
        for (y = 0; y < rows; y++) {
            const unsigned char *r = (const unsigned char *)m.pData + (size_t)y * m.RowPitch;
            for (x = 0; x < rowbytes; x++) h = (h ^ r[x]) * 1099511628211ull;
            if (bc)
                for (x = 0; x + bc <= rowbytes; x += bc) {
                    const unsigned char *b = r + x + (bc == 16 ? 8 : 0);
                    unsigned e;
                    for (e = 0; e < 2; e++) {
                        unsigned v = b[2 * e] | b[2 * e + 1] << 8;
                        sum[0] += ((v >> 11) & 31) / 31.0, sum[1] += ((v >> 5) & 63) / 63.0, sum[2] += (v & 31) / 31.0, sum[3] += 1, cnt++;
                    }
                }
            else if (d.Format == DXGI_FORMAT_R8G8B8A8_UNORM || d.Format == DXGI_FORMAT_R16G16B16A16_FLOAT)
                for (x = 0; x < d.Width; x++, cnt++)
                    for (c = 0; c < 4; c++) sum[c] += px_chan(r, x, c, d.Format == DXGI_FORMAT_R16G16B16A16_FLOAT) / 255.0;
        }
        ID3D11DeviceContext_Unmap(s_ctx, (ID3D11Resource *)st, 0);
        *hash = h;
        if (cnt)
            for (c = 0; c < 4; c++) mean[c] = sum[c] / cnt;
    }
    ID3D11Texture2D_Release(st);
}

static void fcap_tex_list(char *s, size_t cap)
{
    unsigned i;
    int at = 0;
    s[0] = 0;
    for (i = 0; i < 16; i++) {
        XbHostTex *t = s_tex[i];
        int n;
        if (!t) continue;
        n = sprintf_s(s + at, cap - (size_t)at, "%s{\"slot\":%u,\"id\":%u,\"guest\":\"%08x\",\"w\":%u,\"h\":%u,\"guest_fmt\":\"%s\",\"source\":\"%s\",\"cube\":%d,\"depth\":%d,\"exp_adjust\":%d,\"fetch_w0\":\"%08x\",\"fetch_w5\":\"%08x\",\"clamp_xyz\":[%u,%u,%u],\"host_ss_addr_uv\":[%u,%u],\"host_ss_filter\":[%u,%u]}",
                      at ? "," : "", i, fcap_id(t), s_tex_guest[i], t->w, t->h, fcap_guest_fmt((int)(xb_slot_fetch[i][0] & 63u)),
                      xb_slot_fetch[i][3] == 2 ? "resolve" : xb_slot_fetch[i][3] == 1 ? "decoded" : "?", t->cube, t->depth,
                      (int)((xb_slot_fetch[i][2] >> 13) & 63u) - (int)((xb_slot_fetch[i][2] >> 13) & 32u) * 2,
                      xb_slot_fetch_w05[i][0], xb_slot_fetch_w05[i][1], (xb_slot_fetch_w05[i][0] >> 10) & 7u, (xb_slot_fetch_w05[i][0] >> 13) & 7u, (xb_slot_fetch_w05[i][0] >> 16) & 7u,
                      xb_ss(i, 0), xb_ss(i, 4), xb_ss(i, 16), xb_ss(i, 20));   /* fetch constant word 3 bits 13..18, signed */
        if (n < 0) break;
        at += n;
        if (s_fcap_after != ~0u && at > 1) {   /* a chosen draw: the texture's hash and mean colour, inside its record */
            unsigned long long hh;
            double mn[4];
            fcap_tex_stats(t, &hh, mn);
            at--;   /* over the closing brace */
            n = sprintf_s(s + at, cap - (size_t)at, ",\"hash\":\"%016llx\",\"mean\":[%.4f,%.4f,%.4f,%.4f]}", hh, mn[0], mn[1], mn[2], mn[3]);
            if (n < 0) break;
            at += n;
        }
    }
}

static u32 s_fcap_s0[5], s_fcap_slots[20];

void xb_fcap_stream0(u32 kit_at, u32 device_at, u32 device_bytes, u32 stride, u32 fetch_stride, const u32 *streams0_9)
{
    s_fcap_s0[0] = kit_at, s_fcap_s0[1] = device_at, s_fcap_s0[2] = device_bytes, s_fcap_s0[3] = stride, s_fcap_s0[4] = fetch_stride;
    memcpy(s_fcap_slots, streams0_9, sizeof s_fcap_slots);
}

/* A draw: drawn, or left, and why. The state is what the host binds for it */
void xb_fcap_draw(unsigned prim, unsigned nverts, unsigned nattr, const char *why)
{
    const XbShader *vsd, *psd;
    XbHostTex *dz;
    char tex[4096];
    const float *vc = s_const[0], *pc = s_const[1];
    D3D11_RECT sc[1] = {{0, 0, 0, 0}};
    UINT nsc = 1;
    int sc_on = 0;
    char rsl[1600];
    if (!g_fcap_on) return;
    {   /* the scissor the host rasterises with (its enable and rectangle), and every guest render state not 0 as index:value, the guest's scissor
         * test and rectangle among them (the kit has no case for them) */
        ID3D11RasterizerState *rs = 0;
        unsigned i;
        int at = 0;
        ID3D11DeviceContext_RSGetState(s_ctx, &rs);
        if (rs) {
            D3D11_RASTERIZER_DESC rd;
            ID3D11RasterizerState_GetDesc(rs, &rd);
            sc_on = rd.ScissorEnable;
            ID3D11RasterizerState_Release(rs);
        }
        ID3D11DeviceContext_RSGetScissorRects(s_ctx, &nsc, sc);
        rsl[0] = 0;
        for (i = 0; i < XB_RS_COUNT && at < (int)sizeof rsl - 24; i++)
            if (xb_rs(i * 4)) at += sprintf_s(rsl + at, sizeof rsl - (size_t)at, "%s%u:%x", at ? " " : "", i * 4, xb_rs(i * 4));
    }
    vsd = xb_shader_find(s_vs_addr), psd = s_ps_addr ? xb_shader_find(s_ps_addr) : 0;
    dz = depth_tex();
    fcap_tex_list(tex, sizeof tex);
    fcap_rec("\"k\":\"draw\",\"drawn\":%s,\"why\":\"%s\",\"prim\":%u,\"verts\":%u,\"attrs\":%u,\"vs\":\"%08x\",\"ps\":\"%08x\",\"vs_converted\":%d,\"ps_converted\":%d,"
             "\"vs_bytecode\":%d,\"ps_bytecode\":%d,\"vs_refused\":\"%s\",\"ps_refused\":\"%s\","
             "\"blend\":{\"on\":%u,\"rs72\":\"%x\",\"rs76\":\"%x\",\"rs80\":\"%x\",\"rs64\":\"%x\",\"rs84\":\"%x\",\"rs88\":\"%x\",\"rs92\":\"%x\"},\"write_mask\":\"%x\","
             "\"alpha_test\":{\"on\":%u,\"ref\":%u,\"func\":%u},\"z\":{\"on\":%u,\"func\":%u,\"write\":%u},\"stencil\":{\"on\":%u,\"two_sided\":%u,\"ops\":[%u,%u,%u,%u],\"ref\":%u,\"mask\":\"%x\",\"write\":\"%x\",\"ccw\":[%u,%u,%u,%u]},\"depth_word\":\"%x\",\"colour_mask_word\":\"%x\",\"colour_control_word\":\"%x\",\"mode_control_word\":\"%x\",\"cull\":\"%x\",\"rs304\":\"%x\","
             "\"rt0\":{\"id\":%u,\"w\":%u,\"h\":%u,\"guest_fmt\":%d,\"exp_bias\":%d,\"clamp\":%g},\"rt1\":%u,\"depth\":{\"id\":%u,\"w\":%u,\"h\":%u,\"host_on\":%d},"
             "\"viewport\":[%g,%g,%g,%g,%g,%g],\"textures\":[%s],\"stream0\":{\"kit\":\"%08x\",\"device\":\"%08x\",\"device_bytes\":%u,\"stride\":%u,\"fetch_stride\":%u,\"streams0_9\":\"%08x %08x %08x %08x %08x %08x %08x %08x %08x %08x %08x %08x %08x %08x %08x %08x %08x %08x %08x %08x\"},"
             "\"scissor\":{\"host_on\":%d,\"host_rect\":[%ld,%ld,%ld,%ld],\"guest_states\":\"%s\"},\"vs_c0_3\":[%g,%g,%g,%g,%g,%g,%g,%g,%g,%g,%g,%g,%g,%g,%g,%g],\"ps_c0_3\":[%g,%g,%g,%g,%g,%g,%g,%g,%g,%g,%g,%g,%g,%g,%g,%g]",
             why ? "false" : "true", why ? why : "", prim, nverts, nattr, s_vs_addr, s_ps_addr, vsd && vsd->hlsl, psd && psd->hlsl, vsd && vsd->code_len, psd && psd->code_len,
             vsd && vsd->why ? vsd->why : vsd ? "" : "not in the table", psd && psd->why ? psd->why : (s_ps_addr && !psd) ? "not in the table" : "",
             xb_rs(60), xb_rs(72), xb_rs(76), xb_rs(80), xb_rs(64), xb_rs(84), xb_rs(88), xb_rs(92), xb_rs(212),
             xb_rs(96), xb_rs(100), xb_rs(104), xb_rs(40), xb_rs(44), xb_rs(48),
             xb_rs(108), xb_rs(112), xb_rs(116), xb_rs(120), xb_rs(124), xb_rs(128), xb_rs(132), xb_rs(136), xb_rs(140), xb_rs(144), xb_rs(148), xb_rs(152), xb_rs(156),
             s_dc_known ? s_dc : 0xffffffffu, s_dw_known ? s_dw[0] : 0xffffffffu, s_dw_known ? s_dw[1] : 0xffffffffu, s_dw_known ? s_dw[2] : 0xffffffffu, xb_rs(56), xb_rs(304),
             fcap_id(s_target[0]), s_target[0] ? s_target[0]->w : 0, s_target[0] ? s_target[0]->h : 0, s_target_fmt[0], s_target_bias[0], (double)target_clamp_mode(),
             fcap_id(s_target[1]), fcap_id(dz), dz ? dz->w : 0, dz ? dz->h : 0, xb_depth_on(),
             (double)s_vp.x, (double)s_vp.y, (double)s_vp.w, (double)s_vp.h, (double)s_vp.zmin, (double)s_vp.zmax, tex,
             s_fcap_s0[0], s_fcap_s0[1], s_fcap_s0[2], s_fcap_s0[3], s_fcap_s0[4], s_fcap_slots[0], s_fcap_slots[1], s_fcap_slots[2], s_fcap_slots[3], s_fcap_slots[4],
             s_fcap_slots[5], s_fcap_slots[6], s_fcap_slots[7], s_fcap_slots[8], s_fcap_slots[9], s_fcap_slots[10], s_fcap_slots[11], s_fcap_slots[12], s_fcap_slots[13], s_fcap_slots[14],
             s_fcap_slots[15], s_fcap_slots[16], s_fcap_slots[17], s_fcap_slots[18], s_fcap_slots[19],
             sc_on, (long)sc[0].left, (long)sc[0].top, (long)sc[0].right, (long)sc[0].bottom, rsl,
             (double)vc[0], (double)vc[1], (double)vc[2], (double)vc[3], (double)vc[4], (double)vc[5], (double)vc[6], (double)vc[7],
             (double)vc[8], (double)vc[9], (double)vc[10], (double)vc[11], (double)vc[12], (double)vc[13], (double)vc[14], (double)vc[15],
             (double)pc[0], (double)pc[1], (double)pc[2], (double)pc[3], (double)pc[4], (double)pc[5], (double)pc[6], (double)pc[7],
             (double)pc[8], (double)pc[9], (double)pc[10], (double)pc[11], (double)pc[12], (double)pc[13], (double)pc[14], (double)pc[15]);
    memset(s_fcap_s0, 0, sizeof s_fcap_s0), memset(s_fcap_slots, 0, sizeof s_fcap_slots);
}

/* XB_FCAP_DRAWS=a[-b][,...] (bring-up): for those records of the captured frame (the numbers frame_capture_view.py prints), the draw's
 * converted vertices and its vertex constants as draw_<n>.bin, and render target 0 after it as after_<n>.png: which draws make a shape and what
 * their vertices hold. draw_<n>.bin: the words "XBDV", vertices, inputs; then vertices * inputs float4s (the shader's inputs in its fetch order),
 * the 256 vertex constants, the viewport (x, y, w, h, zmin, zmax) and the game's size, then the 256 pixel constants */
static unsigned s_fcap_after = ~0u;

static int fcap_chosen(unsigned n)
{
    static int got;
    static unsigned lo[16], hi[16], k;
    unsigned i;
    if (!got) {
        const char *e = getenv("XB_FCAP_DRAWS");
        char *end;
        got = 1;
        while (e && *e && k < 16) {
            lo[k] = hi[k] = (unsigned)strtoul(e, &end, 10);
            if (end == e) break;
            if (*end == '-') hi[k] = (unsigned)strtoul(end + 1, &end, 10);
            k++, e = *end ? end + 1 : end;
        }
    }
    for (i = 0; i < k; i++)
        if (n >= lo[i] && n <= hi[i]) return 1;
    {   /* XB_FCAP_PS=key: every draw with that pixel shader as well (P1: Too Human's post pass fea9caac, wherever its records fall) */
        static int got_ps;
        static u32 ps;
        if (!got_ps) { const char *e = getenv("XB_FCAP_PS"); got_ps = 1; ps = e ? (u32)strtoul(e, 0, 16) : 0; }
        if (ps && s_ps_addr == ps) return 1;
    }
    return 0;
}

static void fcap_draw_data(unsigned nverts, unsigned nattr, const float *vdata)
{
    unsigned n;
    size_t sz;
    u32 *b;
    char name[40];
    s_fcap_after = ~0u;
    if (!g_fcap_on || !fcap_chosen(n = fcap_next())) return;
    s_fcap_after = n;
    sz = 12 + (size_t)nverts * nattr * 16 + 256 * 16 + 8 * 4 + 256 * 16;   /* the pixel constants last */
    if (!(b = (u32 *)malloc(sz))) return;
    b[0] = 0x56444258u, b[1] = nverts, b[2] = nattr;   /* "XBDV" */
    memcpy(b + 3, vdata, (size_t)nverts * nattr * 16);
    memcpy(b + 3 + (size_t)nverts * nattr * 4, s_const[0], 256 * 16);
    {
        float *f = (float *)(b + 3 + (size_t)nverts * nattr * 4 + 1024);
        f[0] = s_vp.x, f[1] = s_vp.y, f[2] = s_vp.w, f[3] = s_vp.h, f[4] = s_vp.zmin, f[5] = s_vp.zmax, f[6] = (float)s_game_w, f[7] = (float)s_game_h;
        memcpy(f + 8, s_const[1], 256 * 16);
    }
    sprintf_s(name, sizeof name, "draw_%u.bin", n);
    fcap_blob(name, b, sz);
    free(b);
}

static void fcap_draw_after(void)
{
    char name[40];
    double am;
    if (s_fcap_after == ~0u || !g_fcap_on) return;
    sprintf_s(name, sizeof name, "after_%u", s_fcap_after);
    fcap_tex_png(s_target[0], name, &am);
    s_fcap_after = ~0u;
}

/* A draw left before it reaches the host (the d3d layer's skips): the shaders bound in the guest and why */
void xb_fcap_skip(const char *why, u32 vs, u32 ps)
{
    if (!g_fcap_on) return;
    fcap_rec("\"k\":\"draw\",\"drawn\":false,\"why\":\"%s\",\"vs\":\"%08x\",\"ps\":\"%08x\",\"rt0\":{\"id\":%u},\"z\":{\"on\":%u,\"func\":%u,\"write\":%u},\"stencil_on\":%u,\"depth_word\":\"%x\"", why, vs, ps, fcap_id(s_target[0]),
             xb_rs(40), xb_rs(44), xb_rs(48), xb_rs(108), s_dc_known ? s_dc : 0xffffffffu);
}

void xb_fcap_resolve_skip(const char *why, u32 dst)
{
    if (!g_fcap_on) return;
    fcap_rec("\"k\":\"resolve\",\"done\":false,\"why\":\"%s\",\"dst_guest\":\"%08x\"", why, dst);
}

static void fcap_clear(const char *kind, u32 flags, const float *rgba, float z, u32 stencil, unsigned rects)
{
    if (!g_fcap_on) return;
    fcap_rec("\"k\":\"clear\",\"how\":\"%s\",\"flags\":\"%x\",\"rgba\":[%g,%g,%g,%g],\"z\":%g,\"stencil\":%u,\"rects\":%u,\"rt0\":{\"id\":%u,\"w\":%u,\"h\":%u},\"depth\":%u",
             kind, flags, (double)rgba[0], (double)rgba[1], (double)rgba[2], (double)rgba[3], (double)z, stencil, rects, fcap_id(s_target[0]),
             s_target[0] ? s_target[0]->w : 0, s_target[0] ? s_target[0]->h : 0, fcap_id(s_depth));
}

static void fcap_resolve(XbHostTex *src, XbHostTex *dst, int exp_bias)
{
    char name[48];
    double am = -1;
    int png;
    static unsigned n;
    if (!g_fcap_on) return;
    sprintf_s(name, sizeof name, "resolve_%03u_src%u", n++, fcap_id(src));
    png = fcap_tex_png(src, name, &am);
    fcap_rec("\"k\":\"resolve\",\"done\":true,\"src\":{\"id\":%u,\"w\":%u,\"h\":%u,\"depth\":%d},\"dst\":{\"id\":%u,\"w\":%u,\"h\":%u},\"exp_bias\":%d,"
             "\"picture\":\"%s\",\"src_alpha_mean\":%.3f",
             fcap_id(src), src ? src->w : 0, src ? src->h : 0, src ? src->depth : 0, fcap_id(dst), dst ? dst->w : 0, dst ? dst->h : 0, exp_bias, png ? name : "", am);
}

static void fcap_present(XbHostTex *src)
{
    if (!g_fcap_on) return;
    fcap_rec("\"k\":\"present\",\"src\":{\"id\":%u,\"w\":%u,\"h\":%u},\"game\":[%u,%u]", fcap_id(src), src ? src->w : 0, src ? src->h : 0, s_game_w, s_game_h);
}
