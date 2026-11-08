/* Decoded textures, cached by what the texture unit reads: the TCW, the TSP's size bits and (stride textures) the
 * stride. Each entry remembers the texture-memory range it was decoded from and (--tex-recheck on, the default) a
 * copy of those bytes and of the palette entries it read. A write there (kmLoadTexture) or a change of those palette
 * entries (pvr_palette_stamp) marks it; the next draw compares the bytes and entries with the copy and decodes again
 * only if they differ, so a game that uploads the same texture again each frame keeps it (--tex-recheck off: the old
 * way, decoded again on any such write). A texture pack's image (pvr_pack.c), when it has one, replaces the decoded
 * texels; switching the pack on or off decodes everything again. */
#include <stdlib.h>
#include <string.h>
#include "pvr_int.h"
#include "platform.h"

#define TEX_SLOTS 8192u /* a power of two */
#define TSP_SIZE_MASK 0x3fu
#define TCW_NON_TWIDDLED (1u << 26)
#define TCW_STRIDE (1u << 25)
#define TCW_ADDR_MASK 0x1fffffu
#define PAGE_SHIFT 12u
#define PAGES (PVRX_VRAM_SIZE >> PAGE_SHIFT)

typedef struct TexEntry {
    u32 tcw, size, stride, used;
    int failed; /* not decodable: not retried until its memory is written */
    int marked; /* its bytes or palette entries may have changed: compared before the next use */
    u32 pal_stamp; /* palettized: the palette's stamp it was decoded with */
    u32 pal_first, pal_count; /* palettized: the entries it read */
    u32 start, end; /* texture memory read */
    u8 *copy; /* the bytes and palette entries (and format) it was decoded from */
    u32 copy_cap;
    ID3D11ShaderResourceView *srv;
} TexEntry;

static TexEntry g_tex[TEX_SLOTS];
static u32 g_count;
static u32 *g_texels;
static u32 g_texels_cap;
static u32 g_pages[PAGES / 32u]; /* pages of texture memory some entry reads (cleared with the cache) */
static int g_no_recheck; /* --tex-recheck off */
static struct {
    u64 writes, skipped, marked, kept, changed, decodes, decode_us, scan_us, pack_us, dec_us, keys_us, d3d_us;
} g_st;
#define TOP 8u
static struct {
    u32 start, bytes, n;
} g_top[64]; /* the ranges decoded again because they changed, most often first at the report */

static u64 now_us(void)
{
    static LARGE_INTEGER f;
    LARGE_INTEGER c;
    if (!f.QuadPart) QueryPerformanceFrequency(&f);
    QueryPerformanceCounter(&c);
    return (u64)(c.QuadPart / f.QuadPart * 1000000 + c.QuadPart % f.QuadPart * 1000000 / f.QuadPart);
}

static void drop(TexEntry *e)
{
    if (e->srv) ID3D11ShaderResourceView_Release(e->srv);
    e->srv = NULL;
    e->marked = 0;
}

static void flush_all(void)
{
    for (u32 i = 0; i < TEX_SLOTS; i++) {
        drop(&g_tex[i]);
        free(g_tex[i].copy);
    }
    memset(g_tex, 0, sizeof g_tex);
    memset(g_pages, 0, sizeof g_pages);
    g_count = 0;
}

static void note_pages(u32 start, u32 end)
{
    if (end > PVRX_VRAM_SIZE) end = PVRX_VRAM_SIZE;
    for (u32 p = start >> PAGE_SHIFT; p < PAGES && p << PAGE_SHIFT < end; p++) g_pages[p / 32u] |= 1u << (p % 32u);
}

static int any_page(u32 start, u32 end)
{
    for (u32 p = start >> PAGE_SHIFT; p < PAGES && p << PAGE_SHIFT < end; p++)
        if (g_pages[p / 32u] & 1u << (p % 32u)) return 1;
    return 0;
}

int pvr_tex_option(const char *arg, const char *value)
{
    if (!value || strcmp(arg, "--tex-recheck")) return 0;
    g_no_recheck = !strcmp(value, "off");
    return 2;
}

void pvr_tex_invalidate(u32 offset, u32 size)
{
    u32 end = offset + size;
    g_st.writes++;
    if (!any_page(offset, end)) { /* no entry reads there */
        g_st.skipped++;
        return;
    }
    u64 t = now_us();
    for (u32 i = 0; i < TEX_SLOTS; i++) {
        TexEntry *e = &g_tex[i];
        if (e->used && e->start < end && offset < e->end) {
            e->failed = 0;
            if (!e->srv || e->marked) continue;
            g_st.marked++;
            if (g_no_recheck)
                drop(e);
            else
                e->marked = 1;
        }
    }
    g_st.scan_us += now_us() - t;
}

static TexEntry *find(u32 tcw, u32 size, u32 stride)
{
    u32 h = (tcw * 2654435761u) ^ (size * 40503u) ^ stride;
    for (u32 n = 0; n < TEX_SLOTS; n++) {
        TexEntry *e = &g_tex[(h + n) & (TEX_SLOTS - 1u)];
        if (!e->used) {
            if (g_count * 4u >= TEX_SLOTS * 3u) { /* three quarters full: start over */
                port_log("pvr: texture cache full (%u entries), cleared\n", g_count);
                flush_all();
                return find(tcw, size, stride);
            }
            e->used = 1;
            e->tcw = tcw;
            e->size = size;
            e->stride = stride;
            g_count++;
            return e;
        }
        if (e->tcw == tcw && e->size == size && e->stride == stride) return e;
    }
    return NULL;
}

/* The bytes an entry is decoded from, then (palettized) its palette entries and the palette format, into buf (NULL:
 * only the length). */
static u32 source(const TexEntry *e, u8 *buf)
{
    u32 end = e->end < PVRX_VRAM_SIZE ? e->end : PVRX_VRAM_SIZE, n = end > e->start ? end - e->start : 0u;
    u32 len = n + (e->pal_count + 1u) * 4u;
    if (!buf) return len;
    memcpy(buf, pvr_vram() + e->start, n);
    for (u32 i = 0; i < e->pal_count; i++) {
        u32 v = pvr_palette_entry(e->pal_first + i);
        memcpy(buf + n + i * 4u, &v, 4u);
    }
    u32 f = pvr_palette_format();
    memcpy(buf + n + e->pal_count * 4u, &f, 4u);
    return len;
}

/* A marked entry: still the same bytes and palette entries as its copy? */
static int unchanged(const TexEntry *e)
{
    u32 end = e->end < PVRX_VRAM_SIZE ? e->end : PVRX_VRAM_SIZE, n = end > e->start ? end - e->start : 0u;
    static int fault = -1; /* DP_TEX_FAULT=1: a planted fault, a changed texture kept (replay_compare must see it) */
    if (fault < 0) fault = GetEnvironmentVariableA("DP_TEX_FAULT", NULL, 0) > 0;
    if (fault) return 1;
    if (!e->copy || source(e, NULL) > e->copy_cap || memcmp(e->copy, pvr_vram() + e->start, n)) return 0;
    for (u32 i = 0; i < e->pal_count; i++) {
        u32 v;
        memcpy(&v, e->copy + n + i * 4u, 4u);
        if (v != pvr_palette_entry(e->pal_first + i)) return 0;
    }
    u32 f;
    memcpy(&f, e->copy + n + e->pal_count * 4u, 4u);
    return !e->pal_count || f == pvr_palette_format();
}

static void keep_copy(TexEntry *e)
{
    u32 len = source(e, NULL);
    if (g_no_recheck) return;
    if (len > e->copy_cap) {
        u8 *p = realloc(e->copy, len);
        if (!p) return;
        e->copy = p;
        e->copy_cap = len;
    }
    source(e, e->copy);
}

static void note_changed(const TexEntry *e)
{
    u32 i, low = 0;
    for (i = 0; i < 64u && g_top[i].n; i++)
        if (g_top[i].start == e->start && g_top[i].bytes == e->end - e->start) break;
    if (i == 64u) { /* full: the least often one goes */
        for (u32 k = 1; k < 64u; k++)
            if (g_top[k].n < g_top[low].n) low = k;
        i = low;
        g_top[i].n = 0;
    }
    g_top[i].start = e->start;
    g_top[i].bytes = e->end - e->start;
    g_top[i].n++;
}

static const char *layout_name(u32 tcw)
{
    tcw = pvr_tcw_layout(tcw);
    if (tcw & (1u << 30)) return tcw & (1u << 31) ? "VQ mip-mapped" : "VQ";
    if (tcw & TCW_NON_TWIDDLED) return tcw & TCW_STRIDE ? "stride" : "rectangle";
    return tcw & (1u << 31) ? "twiddled mip-mapped" : "twiddled";
}

static int create(TexEntry *e, u32 w, u32 h)
{
    D3D11_TEXTURE2D_DESC d;
    D3D11_SUBRESOURCE_DATA init[16];
    const u32 *texels = NULL;
    u32 need = pvr_decode_texels(e->tcw, w, h), levels, at = 0;
    u64 t0 = now_us(), t1, t2;
    levels = pvr_pack_levels(e->tcw, w, h, e->stride, &texels, &w, &h);
    g_st.pack_us += (t1 = now_us()) - t0;
    if (levels) {
        if (levels > 16u) levels = 16u;
    } else if (need > g_texels_cap) {
        u32 *p = realloc(g_texels, (size_t)need * sizeof *g_texels);
        if (!p) return 0;
        g_texels = p;
        g_texels_cap = need;
    }
    if (!levels) {
        levels = pvr_decode(e->tcw, w, h, e->stride, g_texels, g_texels_cap);
        texels = g_texels;
        g_st.dec_us += (t2 = now_us()) - t1;
        pvr_keys_note(e->tcw, w, h, e->stride, texels);
        g_st.keys_us += now_us() - t2;
    }
    if (!levels) return 0;
    for (u32 i = 0; i < levels; i++) {
        u32 lw = w >> i ? w >> i : 1u, lh = h >> i ? h >> i : 1u;
        init[i].pSysMem = texels + at;
        init[i].SysMemPitch = lw * 4u;
        init[i].SysMemSlicePitch = 0;
        at += lw * lh;
    }
    memset(&d, 0, sizeof d);
    d.Width = w;
    d.Height = h;
    d.MipLevels = levels;
    d.ArraySize = 1;
    d.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
    d.SampleDesc.Count = 1;
    d.Usage = D3D11_USAGE_IMMUTABLE;
    d.BindFlags = D3D11_BIND_SHADER_RESOURCE;
    ID3D11Texture2D *tex = NULL;
    t0 = now_us();
    HRESULT hr = ID3D11Device_CreateTexture2D(g_gfx.dev, &d, init, &tex);
    if (SUCCEEDED(hr)) {
        hr = ID3D11Device_CreateShaderResourceView(g_gfx.dev, (ID3D11Resource *)tex, NULL, &e->srv);
        ID3D11Texture2D_Release(tex);
    }
    g_st.d3d_us += now_us() - t0;
    if (FAILED(hr)) {
        pvr_todo("pvr: no %ux%u texture (%08lx)", w, h, (unsigned long)hr);
        return 0;
    }
    pvr_seen("texture %s, pixel format %u, %ux%u", layout_name(e->tcw), (e->tcw >> 27) & 7u, w, h);
    return 1;
}

ID3D11ShaderResourceView *pvr_tex_get(u32 tcw, u32 tsp)
{
    static u32 generation;
    if (generation != pvr_pack_generation()) {
        generation = pvr_pack_generation();
        flush_all();
    }
    u32 size = tsp & TSP_SIZE_MASK, w = 8u << ((size >> 3) & 7u), h = 8u << (size & 7u);
    u32 layout = pvr_tcw_layout(tcw), pal_first = 0, pal_count = 0, pal_stamp = 0;
    u32 stride = (layout & TCW_NON_TWIDDLED) && (layout & TCW_STRIDE) ? g_pvr.stride : 0u;
    TexEntry *e = find(tcw, size, stride);
    if (!e || e->failed) return NULL;
    if (pvr_palette_range(tcw, &pal_first, &pal_count)) {
        pal_stamp = pvr_palette_stamp(pal_first, pal_count);
        if (e->srv && e->pal_stamp != pal_stamp) {
            g_st.marked++;
            if (g_no_recheck) drop(e);
            else e->marked = 1;
        }
    }
    if (e->srv && e->marked) {
        if (unchanged(e)) {
            e->marked = 0;
            e->pal_stamp = pal_stamp;
            g_st.kept++;
        } else {
            note_changed(e);
            drop(e);
            g_st.changed++;
        }
    }
    if (!e->srv) {
        u64 t = now_us();
        e->start = (tcw & TCW_ADDR_MASK) << 3;
        e->end = e->start + pvr_texture_bytes(tcw, w, h, stride);
        e->pal_first = pal_first;
        e->pal_count = pal_count;
        note_pages(e->start, e->end);
        if (!create(e, w, h)) {
            e->failed = 1;
            return NULL;
        }
        e->pal_stamp = pal_stamp;
        keep_copy(e);
        g_st.decodes++;
        g_st.decode_us += now_us() - t;
    }
    return e->srv;
}

/* At exit: what the cache decoded and kept, and the ranges decoded again most often because they changed. */
void pvr_tex_report(void)
{
    port_log("pvr: textures (--tex-recheck %s): %llu decoded (%llu ms); %llu writes to texture memory (%llu where no "
             "texture is, %llu ms looking); %llu marked by a write or palette change: %llu the same bytes (kept), "
             "%llu changed (decoded again); decoding's ms: pack lookup %llu, decode %llu, key notes %llu, device %llu\n",
             g_no_recheck ? "off" : "on", g_st.decodes, g_st.decode_us / 1000u, g_st.writes, g_st.skipped, g_st.scan_us / 1000u,
             g_st.marked, g_st.kept, g_st.changed, g_st.pack_us / 1000u, g_st.dec_us / 1000u, g_st.keys_us / 1000u,
             g_st.d3d_us / 1000u);
    for (u32 k = 0; k < TOP; k++) {
        u32 best = 64u;
        for (u32 i = 0; i < 64u; i++)
            if (g_top[i].n && (best == 64u || g_top[i].n > g_top[best].n)) best = i;
        if (best == 64u) break;
        port_log("pvr:   changed %u time(s): %u bytes at %06x\n", g_top[best].n, g_top[best].bytes, g_top[best].start);
        g_top[best].n = 0;
    }
}
