/* Decoded textures, cached by what the texture unit reads: the TCW, the TSP's size bits and (stride textures) the
 * stride. Each entry remembers the texture-memory range it was decoded from; a write there (kmLoadTexture) drops
 * it, and the next draw decodes it again. */
#include <stdlib.h>
#include <string.h>
#include "pvr_int.h"
#include "platform.h"

#define TEX_SLOTS 8192u /* a power of two */
#define TSP_SIZE_MASK 0x3fu
#define TCW_NON_TWIDDLED (1u << 26)
#define TCW_STRIDE (1u << 25)
#define TCW_ADDR_MASK 0x1fffffu

typedef struct TexEntry {
    u32 tcw, size, stride, used;
    int failed; /* not decodable: not retried until its memory is written */
    u32 start, end; /* texture memory read */
    ID3D11ShaderResourceView *srv;
} TexEntry;

static TexEntry g_tex[TEX_SLOTS];
static u32 g_count;
static u32 *g_texels;
static u32 g_texels_cap;

static void drop(TexEntry *e)
{
    if (e->srv) ID3D11ShaderResourceView_Release(e->srv);
    e->srv = NULL;
}

static void flush_all(void)
{
    for (u32 i = 0; i < TEX_SLOTS; i++) drop(&g_tex[i]);
    memset(g_tex, 0, sizeof g_tex);
    g_count = 0;
}

void pvr_tex_invalidate(u32 offset, u32 size)
{
    u32 end = offset + size;
    for (u32 i = 0; i < TEX_SLOTS; i++) {
        TexEntry *e = &g_tex[i];
        if (e->used && e->start < end && offset < e->end) {
            drop(e);
            e->failed = 0;
        }
    }
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

static const char *layout_name(u32 tcw)
{
    if (tcw & (1u << 30)) return tcw & (1u << 31) ? "VQ mip-mapped" : "VQ";
    if (tcw & TCW_NON_TWIDDLED) return tcw & TCW_STRIDE ? "stride" : "rectangle";
    return tcw & (1u << 31) ? "twiddled mip-mapped" : "twiddled";
}

static int create(TexEntry *e, u32 w, u32 h)
{
    D3D11_TEXTURE2D_DESC d;
    D3D11_SUBRESOURCE_DATA init[12];
    u32 need = pvr_decode_texels(e->tcw, w, h), levels, at = 0;
    if (need > g_texels_cap) {
        u32 *p = realloc(g_texels, (size_t)need * sizeof *g_texels);
        if (!p) return 0;
        g_texels = p;
        g_texels_cap = need;
    }
    levels = pvr_decode(e->tcw, w, h, e->stride, g_texels, g_texels_cap);
    if (!levels) return 0;
    for (u32 i = 0; i < levels; i++) {
        u32 lw = w >> i ? w >> i : 1u, lh = h >> i ? h >> i : 1u;
        init[i].pSysMem = g_texels + at;
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
    HRESULT hr = ID3D11Device_CreateTexture2D(g_gfx.dev, &d, init, &tex);
    if (SUCCEEDED(hr)) {
        hr = ID3D11Device_CreateShaderResourceView(g_gfx.dev, (ID3D11Resource *)tex, NULL, &e->srv);
        ID3D11Texture2D_Release(tex);
    }
    if (FAILED(hr)) {
        pvr_todo("pvr: no %ux%u texture (%08lx)", w, h, (unsigned long)hr);
        return 0;
    }
    pvr_seen("texture %s, pixel format %u, %ux%u", layout_name(e->tcw), (e->tcw >> 27) & 7u, w, h);
    return 1;
}

ID3D11ShaderResourceView *pvr_tex_get(u32 tcw, u32 tsp)
{
    u32 size = tsp & TSP_SIZE_MASK, w = 8u << ((size >> 3) & 7u), h = 8u << (size & 7u);
    u32 stride = (tcw & TCW_NON_TWIDDLED) && (tcw & TCW_STRIDE) ? g_pvr.stride : 0u;
    TexEntry *e = find(tcw, size, stride);
    if (!e || e->failed) return NULL;
    if (!e->srv) {
        e->start = (tcw & TCW_ADDR_MASK) << 3;
        e->end = e->start + pvr_texture_bytes(tcw, w, h, stride);
        if (!create(e, w, h)) {
            e->failed = 1;
            return NULL;
        }
    }
    return e->srv;
}
