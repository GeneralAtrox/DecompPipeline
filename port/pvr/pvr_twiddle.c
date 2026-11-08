/* Texture decoding: texture memory, as a texture control word (TCW) and the TSP's size bits describe it, to RGBA8.
 *
 * Layouts: twiddled (Morton order, y in the even bits, in squares of the smaller side), VQ (a 256-entry codebook
 * of 2x2 texels, then one twiddled index byte per 2x2 block), rectangle (rows of the texture's width) and stride
 * (rows of TEXT_CONTROL's width). Mip-mapped textures store their levels smallest first; the TCW addresses the chain.
 * 16-bit texels expand to 8 bits per channel by repeating their top bits, as the texture unit does. YUV422 stores
 * two texels in 32 bits (U, Y0, V, Y1) and converts with the ITU-R BT.601 equations; only the rectangle and stride
 * layouts hold it here (the TA's YUV converter writes those; the game's movie frames use them). A movie frame is the
 * largest texture decoded every tick, so its rows go a pair at a time with SSE2 (--pvr-yuv: simd, the default; table,
 * one texel at a time as before; check, simd and every texel compared with table, for a replay's proof).
 * Palettized textures (formats 5 and 6: 4-bit and 8-bit indices, twiddled, low nibble first) look their texels up in
 * palette RAM (pvr_pal.c). Their TCW bits 26..21 are the palette selector, not the layout bits: a 4-bit texture uses
 * entries selector * 16.., an 8-bit one (selector & 0x30) * 16... Their mip levels are where 16-bit ones are, counted
 * in texels (the 1x1 level at texel 3); a VQ-compressed palettized texture is not decoded.
 *
 * The twiddle and VQ decoding are adapted from Lodoss (port/src/setup/pvr.cpp), MIT License, Copyright (c) 2026
 * GeneralAtrox; see that project's LICENSE. */
#include <emmintrin.h>
#include <intrin.h>
#include <string.h>
#include "pvr_int.h"

#define TCW_MIP (1u << 31)
#define TCW_VQ (1u << 30)
#define TCW_FMT_SHIFT 27
#define TCW_NON_TWIDDLED (1u << 26)
#define TCW_STRIDE (1u << 25)
#define TCW_ADDR_MASK 0x1fffffu
#define FMT_1555 0u
#define FMT_565 1u
#define FMT_4444 2u
#define FMT_YUV422 3u
#define FMT_PAL4 5u
#define FMT_PAL8 6u
#define TCW_PAL_SHIFT 21
#define TCW_PAL_MASK 0x3fu
#define VQ_CODEBOOK 2048u
#define MAX_LEVELS 11u

static u32 tcw_addr(u32 tcw) { return (tcw & TCW_ADDR_MASK) << 3; }
static u32 tcw_fmt(u32 tcw) { return (tcw >> TCW_FMT_SHIFT) & 7u; }
static int palettized(u32 tcw) { return tcw_fmt(tcw) == FMT_PAL4 || tcw_fmt(tcw) == FMT_PAL8; }

/* The TCW as the layout reads it: a palettized texture's selector bits are no layout bits (it is twiddled). */
u32 pvr_tcw_layout(u32 tcw) { return palettized(tcw) ? tcw & ~(TCW_PAL_MASK << TCW_PAL_SHIFT) : tcw; }

/* The palette entries a palettized texture reads; 0 for another format. */
int pvr_palette_range(u32 tcw, u32 *first, u32 *count)
{
    u32 sel = (tcw >> TCW_PAL_SHIFT) & TCW_PAL_MASK;
    if (!palettized(tcw)) return 0;
    *first = tcw_fmt(tcw) == FMT_PAL4 ? sel * 16u : (sel & 0x30u) * 16u;
    *count = tcw_fmt(tcw) == FMT_PAL4 ? 16u : 256u;
    return 1;
}

/* The bits of v (below 16) spread to the even bits of a word. */
static u32 spread(u32 v)
{
    v = (v | v << 8) & 0x00ff00ffu;
    v = (v | v << 4) & 0x0f0f0f0fu;
    v = (v | v << 2) & 0x33333333u;
    return (v | v << 1) & 0x55555555u;
}

/* The Morton index of (x, y) in a square of side `side` (a power of two): y in the even bits, x in the odd. */
static u32 morton_any(u32 x, u32 y)
{
    u32 r = 0;
    for (u32 bit = 0; (x | y) >> bit; bit++) {
        r |= ((y >> bit) & 1u) << (bit * 2u);
        r |= ((x >> bit) & 1u) << (bit * 2u + 1u);
    }
    return r;
}

/* The twiddled index of texel (x, y) in a w x h texture: squares of the smaller side, in order along the longer.
 * The sides are powers of two (the TSP's size bits; a mip level 1 << i; a VQ index square half a side), so the
 * division is a shift and the bits spread without a loop. Any other side takes the arithmetic the shift stands for. */
static u32 twiddled(u32 x, u32 y, u32 w, u32 h)
{
    u32 side = w < h ? w : h;
    unsigned long shift;
    if ((side & (side - 1u)) || x >= 0x10000u || y >= 0x10000u)
        return (x / side + y / side) * side * side + morton_any(x % side, y % side);
    _BitScanForward(&shift, side);
    return (((x >> shift) + (y >> shift)) << (2u * shift)) + (spread(y & (side - 1u)) | spread(x & (side - 1u)) << 1);
}

static u32 expand5(u32 v) { return (v << 3) | (v >> 2); }
static u32 expand6(u32 v) { return (v << 2) | (v >> 4); }

static __forceinline u32 texel(u16 v, u32 fmt)
{
    u32 r, g, b, a;
    switch (fmt) {
    case FMT_1555:
        a = v & 0x8000u ? 255u : 0u;
        r = expand5((v >> 10) & 31u), g = expand5((v >> 5) & 31u), b = expand5(v & 31u);
        break;
    case FMT_565:
        a = 255u;
        r = expand5(v >> 11), g = expand6((v >> 5) & 63u), b = expand5(v & 31u);
        break;
    default: /* 4444 */
        a = ((v >> 12) & 15u) * 17u;
        r = ((v >> 8) & 15u) * 17u, g = ((v >> 4) & 15u) * 17u, b = (v & 15u) * 17u;
        break;
    }
    return r | g << 8 | b << 16 | a << 24;
}

/* A palette entry in PAL_RAM_CTRL's format: a 16-bit one in its low half, ARGB8888 whole. */
static u32 palette_texel(u32 index)
{
    u32 e = pvr_palette_entry(index), fmt = pvr_palette_format();
    if (fmt == 3u) return (e >> 16 & 255u) | (e & 0xff00u) | (e & 255u) << 16 | (e & 0xff000000u);
    return texel((u16)e, fmt);
}

static u32 clamp8(s32 v) { return v < 0 ? 0u : v > 255 ? 255u : (u32)v; }

/* BT.601 in 16.16 fixed point, the products of the pair's U and V (terms of one texel's three channels). */
static s32 g_yuv_rv[256], g_yuv_gu[256], g_yuv_gv[256], g_yuv_bu[256];
static volatile int g_yuv_ready;

static void yuv_tables(void)
{
    for (s32 i = 0; i < 256; i++) {
        g_yuv_rv[i] = 91881 * (i - 128);
        g_yuv_gu[i] = 22554 * (i - 128);
        g_yuv_gv[i] = 46802 * (i - 128);
        g_yuv_bu[i] = 116130 * (i - 128);
    }
    g_yuv_ready = 1;
}

/* One texel from its Y and its pair's U and V (BT.601, 16.16 fixed point). */
static u32 yuv(u32 y, u32 u, u32 v)
{
    s32 c = (s32)y << 16;
    u32 r = clamp8((c + g_yuv_rv[v] + 32768) >> 16);
    u32 g = clamp8((c - g_yuv_gu[u] - g_yuv_gv[v] + 32768) >> 16);
    u32 b = clamp8((c + g_yuv_bu[u] + 32768) >> 16);
    return r | g << 8 | b << 16 | 0xff000000u;
}

/* --pvr-yuv */
enum { YUV_SIMD, YUV_TABLE, YUV_CHECK };
static int g_yuv_mode;
static u32 g_yuv_checked, g_yuv_differ;

int pvr_yuv_option(const char *value)
{
    if (!strcmp(value, "simd")) g_yuv_mode = YUV_SIMD;
    else if (!strcmp(value, "table")) g_yuv_mode = YUV_TABLE;
    else if (!strcmp(value, "check")) g_yuv_mode = YUV_CHECK;
    else return 0;
    return 2;
}

/* w texels of YUV422 from texture-memory byte `at` (even), whose pairs lie whole in texture memory: yuv()'s sums in
 * four lanes, its clamp from the two saturating packs (a sum's top half fits 16 bits), alpha set over its lane. */
static void yuv_row(const u8 *p, u32 at, u32 w, u32 *out)
{
    const __m128i round = _mm_set1_epi32(32768), alpha = _mm_set1_epi32((int)0xff000000u);
    u32 x = 0;
    if (at & 2u) *out++ = yuv(p[1], p[-2], p[0]), p += 2, x = 1;
    for (; x + 2u <= w; x += 2u, p += 4, out += 2) {
        __m128i uv = _mm_set_epi32(0, g_yuv_bu[p[0]], -g_yuv_gu[p[0]] - g_yuv_gv[p[2]], g_yuv_rv[p[2]]);
        __m128i a = _mm_add_epi32(_mm_add_epi32(uv, round), _mm_set1_epi32((s32)p[1] << 16));
        __m128i b = _mm_add_epi32(_mm_add_epi32(uv, round), _mm_set1_epi32((s32)p[3] << 16));
        __m128i s = _mm_packs_epi32(_mm_srai_epi32(a, 16), _mm_srai_epi32(b, 16));
        _mm_storel_epi64((__m128i *)out, _mm_or_si128(_mm_packus_epi16(s, s), alpha));
    }
    if (x < w) *out = yuv(p[1], p[0], p[2]);
}

static u16 rd16(const u8 *vram, u32 at)
{
    return (u16)(vram[at & (PVRX_VRAM_SIZE - 1u)] | vram[(at + 1u) & (PVRX_VRAM_SIZE - 1u)] << 8);
}
static u8 rd8(const u8 *vram, u32 at) { return vram[at & (PVRX_VRAM_SIZE - 1u)]; }

static u32 log2u(u32 v)
{
    u32 n = 0;
    while ((1u << n) < v) n++;
    return n;
}

/* Where a mip level of side 2^i starts in the chain: 16-bit texels (the 1x1 level at 6) or VQ index bytes. */
static u32 mip_offset16(u32 i) { return 6u + 2u * (((1u << (2u * i)) - 1u) / 3u); }
static u32 mip_offset_texels(u32 i) { return 3u + ((1u << (2u * i)) - 1u) / 3u; }
static u32 mip_offset_vq(u32 i) { return i == 0 ? 0u : 1u + ((1u << (2u * (i - 1u))) - 1u) / 3u; }

static int decodable(u32 tcw, u32 w, u32 h)
{
    u32 fmt = tcw_fmt(tcw);
    if (palettized(tcw) && (tcw & TCW_VQ)) {
        pvr_todo("pvr: a VQ palettized texture (tcw %08x)", tcw & 0xf8000000u);
        return 0;
    }
    tcw = pvr_tcw_layout(tcw);
    if (fmt == FMT_YUV422 && !(tcw & TCW_NON_TWIDDLED)) {
        pvr_todo("pvr: a twiddled YUV422 texture (tcw %08x)", tcw & 0xfe000000u);
        return 0;
    }
    if (fmt > FMT_YUV422 && !palettized(tcw)) {
        pvr_todo("pvr: texture pixel format %u (tcw %08x)", fmt, tcw & 0xfe000000u);
        return 0;
    }
    if ((tcw & TCW_MIP) && w != h && !(tcw & TCW_NON_TWIDDLED)) {
        pvr_todo("pvr: a mip-mapped texture that is not square (%ux%u)", w, h);
        return 0;
    }
    return 1;
}

static int mipped(u32 tcw) { return (tcw & TCW_MIP) && !(pvr_tcw_layout(tcw) & TCW_NON_TWIDDLED); }

u32 pvr_decode_texels(u32 tcw, u32 w, u32 h)
{
    u32 n = w * h;
    if (mipped(tcw))
        for (u32 s = w / 2u; s; s /= 2u) n += s * s;
    return n;
}

u32 pvr_texture_bytes(u32 tcw, u32 w, u32 h, u32 stride)
{
    u32 i = log2u(w), bits = tcw_fmt(tcw) == FMT_PAL4 ? 4u : 8u;
    if (palettized(tcw)) return (((mipped(tcw) ? mip_offset_texels(i) : 0u) + w * h) * bits + 7u) / 8u;
    if (tcw & TCW_VQ) return VQ_CODEBOOK + (mipped(tcw) ? mip_offset_vq(i) : 0u) + w * h / 4u;
    if (tcw & TCW_NON_TWIDDLED) return ((tcw & TCW_STRIDE) && stride ? stride : w) * h * 2u;
    return (mipped(tcw) ? mip_offset16(i) : 0u) + w * h * 2u;
}

static void level_vq(const u8 *vram, u32 base, u32 index_at, u32 w, u32 h, u32 fmt, u32 *out)
{
    u32 bw = w > 1u ? w / 2u : 1u, bh = h > 1u ? h / 2u : 1u;
    for (u32 y = 0; y < h; y++)
        for (u32 x = 0; x < w; x++) {
            u32 block = rd8(vram, index_at + twiddled(x / 2u < bw ? x / 2u : 0u, y / 2u < bh ? y / 2u : 0u, bw, bh));
            /* a block's texels 0 and 2 are its top row, 1 and 3 its bottom row */
            u32 t = (x & 1u) * 2u + (y & 1u);
            out[y * w + x] = texel(rd16(vram, base + block * 8u + t * 2u), fmt);
        }
}

/* One loop a format: the format is a constant where the loop is inlined, so the texel switch goes. */
static __forceinline void twiddled_loop(const u8 *vram, u32 at, u32 w, u32 h, u32 fmt, u32 *out)
{
    for (u32 y = 0; y < h; y++)
        for (u32 x = 0; x < w; x++) out[y * w + x] = texel(rd16(vram, at + twiddled(x, y, w, h) * 2u), fmt);
}

static void level_twiddled(const u8 *vram, u32 at, u32 w, u32 h, u32 fmt, u32 *out)
{
    if (fmt == FMT_1555) twiddled_loop(vram, at, w, h, FMT_1555, out);
    else if (fmt == FMT_565) twiddled_loop(vram, at, w, h, FMT_565, out);
    else twiddled_loop(vram, at, w, h, FMT_4444, out);
}

/* A palettized level: texel t's index is byte t (8-bit) or nibble t (4-bit, low nibble first) from `at` texels. */
static void level_palette(const u8 *vram, u32 tcw, u32 at, u32 w, u32 h, u32 *out)
{
    u32 first = 0, count, pal4 = tcw_fmt(tcw) == FMT_PAL4, base = tcw_addr(tcw), colour[256];
    pvr_palette_range(tcw, &first, &count);
    for (u32 i = 0; i < count; i++) colour[i] = palette_texel(first + i); /* the entries once, not a texel at a time */
    for (u32 y = 0; y < h; y++)
        for (u32 x = 0; x < w; x++) {
            u32 t = at + twiddled(x, y, w, h);
            u32 index = pal4 ? (u32)(rd8(vram, base + t / 2u) >> (t & 1u) * 4u) & 15u : rd8(vram, base + t);
            out[y * w + x] = colour[index];
        }
}

u32 pvr_decode(u32 tcw, u32 w, u32 h, u32 stride, u32 *out, u32 out_texels)
{
    const u8 *vram = pvr_vram();
    u32 fmt = (tcw >> TCW_FMT_SHIFT) & 7u, base = tcw_addr(tcw), levels = 0, top = log2u(w);
    if (!decodable(tcw, w, h) || out_texels < pvr_decode_texels(tcw, w, h)) return 0;
    if (pvr_tcw_layout(tcw) & TCW_NON_TWIDDLED) {
        u32 pitch = (tcw & TCW_STRIDE) && stride ? stride : w;
        if ((tcw & TCW_STRIDE) && !stride) pvr_todo("pvr: a stride texture before the stride is set");
        u32 pair_at = ~0u, u = 0, v = 0;
        if (fmt == FMT_YUV422 && !g_yuv_ready) yuv_tables();
        if (fmt == FMT_YUV422 && g_yuv_mode != YUV_TABLE) {
            for (u32 y = 0; y < h; y++) {
                u32 at = (base + y * pitch * 2u) & (PVRX_VRAM_SIZE - 1u);
                /* near the end of texture memory a row's addresses wrap: the texel loop below reads them so */
                if (at + w * 2u + 4u > PVRX_VRAM_SIZE) {
                    for (u32 x = 0; x < w; x++) {
                        u32 a = at + x * 2u, pair = a & ~3u;
                        out[y * w + x] = yuv(rd8(vram, a + 1u), rd8(vram, pair), rd8(vram, pair + 2u));
                    }
                    continue;
                }
                yuv_row(vram + at, at, w, out + y * w);
            }
            if (g_yuv_mode != YUV_CHECK) return 1;
            for (u32 y = 0; y < h; y++)
                for (u32 x = 0; x < w; x++) {
                    u32 at = base + (y * pitch + x) * 2u, pair = at & ~3u;
                    g_yuv_differ += out[y * w + x] != yuv(rd8(vram, at + 1u), rd8(vram, pair), rd8(vram, pair + 2u));
                }
            if (!(g_yuv_checked++ & 63u) || g_yuv_differ)
                port_log("pvr: --pvr-yuv check: %u textures, %u texels differ\n", g_yuv_checked, g_yuv_differ);
            return 1;
        }
        for (u32 y = 0; y < h; y++)
            for (u32 x = 0; x < w; x++) {
                u32 at = base + (y * pitch + x) * 2u;
                if (fmt != FMT_YUV422) {
                    out[y * w + x] = texel(rd16(vram, at), fmt);
                } else { /* the pair's words: Y0 << 8 | U, then Y1 << 8 | V */
                    u32 pair = at & ~3u;
                    if (pair != pair_at) pair_at = pair, u = rd8(vram, pair), v = rd8(vram, pair + 2u);
                    out[y * w + x] = yuv(rd8(vram, at + 1u), u, v);
                }
            }
        return 1;
    }
    for (u32 i = top + 1u; i-- > 0 && levels < MAX_LEVELS;) {
        u32 lw = i == top ? w : 1u << i, lh = i == top ? h : 1u << i;
        if (palettized(tcw))
            level_palette(vram, tcw, mipped(tcw) ? mip_offset_texels(i) : 0u, lw, lh, out);
        else if (tcw & TCW_VQ)
            level_vq(vram, base, base + VQ_CODEBOOK + (mipped(tcw) ? mip_offset_vq(i) : 0u), lw, lh, fmt, out);
        else
            level_twiddled(vram, base + (mipped(tcw) ? mip_offset16(i) : 0u), lw, lh, fmt, out);
        out += lw * lh;
        levels++;
        if (!mipped(tcw)) break;
    }
    return levels;
}
