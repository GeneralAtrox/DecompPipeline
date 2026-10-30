/* Texture packs: replacement images for guest textures, host-side only (guest memory, timing and game logic are
 * untouched). Off unless game.json "texture_pack" names a folder or --texture-pack DIR is given; --texture-pack off
 * (or the Display menu) turns it off, on turns it back on.
 *
 * A pack is a folder: index.txt, one "KEY FILE" per line ('#' starts a comment), FILE a PNG beside it (any size: the
 * renderer samples by normalized coordinates, so a higher-resolution image just works). KEY, 16 hex digits, is the
 * FNV-1a 64 hash of what the texture unit reads for the texture: the TCW's format bits (tcw & 0xfe000000: mip-map,
 * VQ, pixel format, non-twiddled, stride), width, height and stride as little-endian u32s, then the texture memory
 * bytes the decoder reads (pvr_texture_bytes from the TCW address; a 16-bit twiddled mip chain from its 1x1 level
 * at +6, the bytes before it are not texels). The VQ codebook is part of those bytes, so a VQ texture with the same
 * indices and another codebook is another key. port/tools/texture_pack.py computes the same key from a texture's
 * bytes and writes packs (docs: port/README.md, Texture packs).
 *
 * pvr_tex.c asks when it decodes a texture, which is only after the guest wrote its memory: an unchanged texture is
 * never hashed again. A hit's image is read once and kept with its mip chain (box-filtered, for mip-mapped guests).
 * A palettized texture (pixel formats 5 and 6) has no pack image: its texels depend on palette RAM, not hashed.
 * Nearest existing: pvr_tex.c's decoded-texture cache (keyed by TCW, not by content; this sits under it). */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define COBJMACROS
#include <windows.h>
#include <wincodec.h>
#include "pvr_int.h"
#include "platform.h"

#ifdef PORT_TEXTURE_PACK
#define PACK_DEFAULT PORT_TEXTURE_PACK /* game.json texture_pack */
#else
#define PACK_DEFAULT ""
#endif
#define TCW_MIP (1u << 31)
#define TCW_VQ (1u << 30)
#define TCW_NON_TWIDDLED (1u << 26)
#define TCW_ADDR_MASK 0x1fffffu
#define TCW_FORMAT_BITS 0xfe000000u
#define MIP16_FIRST 6u
#define FNV_BASIS 0xcbf29ce484222325ull
#define FNV_PRIME 0x100000001b3ull
#define MAX_PATH_TEXT 1024

typedef struct PackEntry {
    u64 key;
    char *file;
    u32 *texels; /* the image's levels, top first; NULL: not read yet */
    u32 w, h, levels, mipped, failed;
} PackEntry;

static char g_dir[MAX_PATH_TEXT] = PACK_DEFAULT;
static int g_wanted = 1;            /* --texture-pack off: 0 */
static volatile LONG g_enabled = -1; /* -1: not loaded yet */
static volatile LONG g_generation;   /* bumped by a switch: pvr_tex.c then decodes everything again */
static PackEntry *g_entry;
static u32 g_count, g_hits;
static IWICImagingFactory *g_wic;

static u64 fnv(u64 h, const u8 *p, u32 n)
{
    while (n--) h = (h ^ *p++) * FNV_PRIME;
    return h;
}

static u64 fnv32(u64 h, u32 v)
{
    u8 b[4] = {(u8)v, (u8)(v >> 8), (u8)(v >> 16), (u8)(v >> 24)};
    return fnv(h, b, 4);
}

u64 pvr_pack_key(const u8 *vram, u32 tcw, u32 w, u32 h, u32 stride)
{
    u32 at = (tcw & TCW_ADDR_MASK) << 3, n = pvr_texture_bytes(tcw, w, h, stride), skip = 0;
    u64 k = fnv32(fnv32(fnv32(fnv32(FNV_BASIS, tcw & TCW_FORMAT_BITS), w), h), stride);
    if ((tcw & TCW_MIP) && !(tcw & (TCW_VQ | TCW_NON_TWIDDLED))) skip = MIP16_FIRST;
    for (u32 i = skip; i < n; i++) k = (k ^ vram[(at + i) & (PVRX_VRAM_SIZE - 1u)]) * FNV_PRIME;
    return k;
}

static int by_key(const void *a, const void *b)
{
    u64 x = ((const PackEntry *)a)->key, y = ((const PackEntry *)b)->key;
    return x < y ? -1 : x > y;
}

/* index.txt into g_entry, sorted by key; the number of entries (0: no pack, logged) */
static u32 load_index(void)
{
    char path[MAX_PATH_TEXT + 16], line[MAX_PATH_TEXT];
    u32 cap = 0, bad = 0;
    snprintf(path, sizeof path, "%s/index.txt", g_dir);
    FILE *f = NULL;
    if (fopen_s(&f, path, "r") != 0 || !f) {
        port_log("pvr: texture pack: no %s, the pack is off\n", path);
        return 0;
    }
    while (fgets(line, sizeof line, f)) {
        char *hash = strchr(line, '#'), *end, *file;
        unsigned long long key;
        if (hash) *hash = '\0';
        key = _strtoui64(line, &end, 16);
        file = end + strspn(end, " \t");
        file[strcspn(file, " \t\r\n")] = '\0';
        if (end == line || file == end || !*file) {
            bad += strspn(line, " \t\r\n") != strlen(line);
            continue;
        }
        if (g_count == cap) {
            PackEntry *p = realloc(g_entry, (cap = cap ? cap * 2u : 256u) * sizeof *p);
            if (!p) break;
            g_entry = p;
        }
        memset(&g_entry[g_count], 0, sizeof *g_entry);
        g_entry[g_count].key = key;
        g_entry[g_count++].file = _strdup(file);
    }
    fclose(f);
    if (g_count) qsort(g_entry, g_count, sizeof *g_entry, by_key);
    port_log("pvr: texture pack: %u images in %s%s\n", g_count, g_dir, bad ? " (some lines unreadable)" : "");
    return g_count;
}

static int enabled(void)
{
    if (g_enabled < 0) InterlockedExchange(&g_enabled, g_dir[0] && g_wanted && load_index() ? 1 : 0);
    return g_enabled > 0;
}

/* The PNG's pixels as RGBA8 rows (malloc'd), through WIC; NULL if it cannot be read (logged). */
static u32 *read_png(const char *path, u32 *w, u32 *h)
{
    IWICBitmapDecoder *dec = NULL;
    IWICBitmapFrameDecode *frame = NULL;
    IWICFormatConverter *conv = NULL;
    WCHAR wpath[MAX_PATH_TEXT];
    u32 *px = NULL;
    UINT fw = 0, fh = 0;
    HRESULT hr = S_OK;
    if (!g_wic) {
        hr = CoInitializeEx(NULL, COINIT_APARTMENTTHREADED);
        if (SUCCEEDED(hr) || hr == RPC_E_CHANGED_MODE)
            hr = CoCreateInstance(&CLSID_WICImagingFactory, NULL, CLSCTX_INPROC_SERVER, &IID_IWICImagingFactory,
                                  (void **)&g_wic);
    }
    if (SUCCEEDED(hr) && !MultiByteToWideChar(CP_UTF8, 0, path, -1, wpath, MAX_PATH_TEXT)) hr = E_INVALIDARG;
    if (SUCCEEDED(hr))
        hr = IWICImagingFactory_CreateDecoderFromFilename(g_wic, wpath, NULL, GENERIC_READ,
                                                          WICDecodeMetadataCacheOnDemand, &dec);
    if (SUCCEEDED(hr)) hr = IWICBitmapDecoder_GetFrame(dec, 0, &frame);
    if (SUCCEEDED(hr)) hr = IWICImagingFactory_CreateFormatConverter(g_wic, &conv);
    if (SUCCEEDED(hr))
        hr = IWICFormatConverter_Initialize(conv, (IWICBitmapSource *)frame, &GUID_WICPixelFormat32bppRGBA,
                                            WICBitmapDitherTypeNone, NULL, 0.0, WICBitmapPaletteTypeCustom);
    if (SUCCEEDED(hr)) hr = IWICFormatConverter_GetSize(conv, &fw, &fh);
    if (SUCCEEDED(hr) && (!fw || !fh || fw > 16384u || fh > 16384u)) hr = E_INVALIDARG;
    if (SUCCEEDED(hr)) {
        u32 levels_room = (fw * fh * 4u + 2u) / 3u + 64u; /* the mip chain is built after the top level */
        px = malloc(((size_t)fw * fh + levels_room) * sizeof *px);
        if (!px) hr = E_OUTOFMEMORY;
    }
    if (SUCCEEDED(hr)) hr = IWICFormatConverter_CopyPixels(conv, NULL, fw * 4u, fw * fh * 4u, (BYTE *)px);
    if (conv) IWICFormatConverter_Release(conv);
    if (frame) IWICBitmapFrameDecode_Release(frame);
    if (dec) IWICBitmapDecoder_Release(dec);
    if (FAILED(hr)) {
        port_log("pvr: texture pack: cannot read %s (%08lx)\n", path, (unsigned long)hr);
        free(px);
        return NULL;
    }
    *w = fw;
    *h = fh;
    return px;
}

/* Box-filtered levels after the top one, down to 1x1 (a level's odd edge repeats its last texel); the level count. */
static u32 build_mips(u32 *px, u32 w, u32 h)
{
    u32 levels = 1;
    while (w > 1u || h > 1u) {
        u32 nw = w > 1u ? w / 2u : 1u, nh = h > 1u ? h / 2u : 1u, *next = px + w * h;
        for (u32 y = 0; y < nh; y++)
            for (u32 x = 0; x < nw; x++) {
                u32 x0 = x * 2u < w ? x * 2u : w - 1u, y0 = y * 2u < h ? y * 2u : h - 1u;
                u32 x1 = x0 + 1u < w ? x0 + 1u : x0, y1 = y0 + 1u < h ? y0 + 1u : y0, out = 0;
                u32 t[4] = {px[y0 * w + x0], px[y0 * w + x1], px[y1 * w + x0], px[y1 * w + x1]};
                for (u32 c = 0; c < 32u; c += 8u) {
                    u32 s = 2u;
                    for (u32 i = 0; i < 4u; i++) s += (t[i] >> c) & 255u;
                    out |= (s / 4u) << c;
                }
                next[y * nw + x] = out;
            }
        px = next;
        w = nw;
        h = nh;
        levels++;
    }
    return levels;
}

u32 pvr_pack_levels(u32 tcw, u32 w, u32 h, u32 stride, const u32 **texels, u32 *pw, u32 *ph)
{
    PackEntry probe, *e;
    char path[MAX_PATH_TEXT * 2 + 2];
    u32 mipped = (tcw & TCW_MIP) && !(tcw & TCW_NON_TWIDDLED), pal_first, pal_count;
    if (!enabled() || pvr_palette_range(tcw, &pal_first, &pal_count)) return 0;
    probe.key = pvr_pack_key(pvr_vram(), tcw, w, h, stride);
    e = bsearch(&probe, g_entry, g_count, sizeof *g_entry, by_key);
    if (!e || e->failed) return 0;
    if (!e->texels) {
        snprintf(path, sizeof path, "%s/%s", g_dir, e->file);
        e->texels = read_png(path, &e->w, &e->h);
        if (!e->texels) {
            e->failed = 1;
            return 0;
        }
        e->levels = 1;
    }
    if (mipped && !e->mipped) {
        e->levels = build_mips(e->texels, e->w, e->h);
        e->mipped = 1;
    }
    if (!g_hits++) port_log("pvr: texture pack: first image used (%016llx)\n", (unsigned long long)probe.key);
    *texels = e->texels;
    *pw = e->w;
    *ph = e->h;
    return mipped ? e->levels : 1u;
}

u32 pvr_pack_generation(void) { return (u32)g_generation; }
int pvr_pack_available(void) { return g_dir[0] != 0; }
int pvr_pack_enabled(void) { return enabled(); }

void pvr_pack_set_enabled(int on)
{
    g_wanted = on;
    if (!on) InterlockedExchange(&g_enabled, 0);
    else InterlockedExchange(&g_enabled, g_count ? 1 : -1);
    InterlockedIncrement(&g_generation);
}

/* --texture-pack DIR | on | off */
int pvr_pack_option(const char *arg, const char *value)
{
    if (strcmp(arg, "--texture-pack") || !value) return 0;
    if (!strcmp(value, "off") || !strcmp(value, "on")) {
        g_wanted = value[1] == 'n';
    } else {
        snprintf(g_dir, sizeof g_dir, "%s", value);
        g_wanted = 1;
    }
    return 2;
}
