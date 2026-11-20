/* Texture packs: a PNG's pixels, its mip chain (built, or read from the chain stored beside it) and an over line's merge
 * (pvr_pack.c has the packs themselves). Nearest existing: pvr_pack.c, split for its 400-line cap. */
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define COBJMACROS
#include <windows.h>
#include <wincodec.h>
#include "pvr_int.h"
#include "platform.h"

#define MAX_PATH_TEXT PVR_PACK_PATH

static __declspec(thread) IWICImagingFactory *g_wic; /* each thread its own (COM is set up per thread) */

/* The PNG's pixels as RGBA8 rows (malloc'd), through WIC; NULL if it cannot be read (logged). */
u32 *pvr_pack_read_png(const char *path, u32 *w, u32 *h)
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
    if (SUCCEEDED(hr) && !pack_widen(path, wpath, MAX_PATH_TEXT)) hr = E_INVALIDARG;
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
        u32 levels_room = pvr_pack_chain_texels(fw, fh); /* the mip chain is built after the top level */
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

/* The texels of an image's levels below the top one, down to 1x1, each half the one above (rounded down, as D3D sizes
 * a texture's levels). */
u32 pvr_pack_chain_texels(u32 w, u32 h)
{
    u32 n = 0;
    while (w > 1u || h > 1u) {
        w = w > 1u ? w / 2u : 1u;
        h = h > 1u ? h / 2u : 1u;
        n += w * h;
    }
    return n;
}

/* One axis of a level's texel i (of half): the texels of the level above (of n) it covers, from *from, and the share
 * of each (at most 3). An even edge is two halves; an odd edge's texels are shared out, none dropped, so the level
 * stays where level 0's coordinates put it. */
static u32 footprint(u32 i, u32 n, u32 half, u32 *from, f32 share[3])
{
    double lo = (double)i * n / half, hi = (double)(i + 1u) * n / half;
    u32 a = (u32)lo, b = (u32)ceil(hi - 1e-9), k;
    if (b > n) b = n;
    if (b > a + 3u) b = a + 3u;
    for (k = 0; a + k < b; k++) {
        double l = lo > a + k ? lo : (double)(a + k), r = hi < a + k + 1u ? hi : (double)(a + k + 1u);
        share[k] = (f32)((r - l) / (hi - lo));
    }
    *from = a;
    return k;
}

/* The levels after the top one, down to 1x1; the level count. Each texel is the area-weighted average of what it covers
 * in the level above (a 2x2 box on an even edge). Colour is weighted by alpha, so a transparent texel's colour (often
 * black) does not darken the edge of what shows; alpha is averaged plainly. Averaged on the stored values, as the
 * sampler filters level 0 (an RGBA8 UNORM texture, not sRGB). */
u32 pvr_pack_mips(u32 *px, u32 w, u32 h)
{
    u32 levels = 1;
    while (w > 1u || h > 1u) {
        u32 nw = w > 1u ? w / 2u : 1u, nh = h > 1u ? h / 2u : 1u, *next = px + w * h;
        if (!(w & 1u) && !(h & 1u)) { /* both edges even: each texel is a 2x2 box, in integers (the same rounding) */
            for (u32 y = 0; y < nh; y++)
                for (u32 x = 0; x < nw; x++) {
                    const u32 *r0 = px + y * 2u * w + x * 2u, *r1 = r0 + w;
                    u32 t[4] = {r0[0], r0[1], r1[0], r1[1]}, a = 0, out;
                    for (u32 i = 0; i < 4u; i++) a += t[i] >> 24;
                    out = (a + 2u) / 4u << 24;
                    for (u32 c = 0; c < 24u; c += 8u) {
                        u32 s = 0;
                        if (a) {
                            for (u32 i = 0; i < 4u; i++) s += ((t[i] >> c) & 255u) * (t[i] >> 24);
                            s = (s + a / 2u) / a;
                        } else {
                            for (u32 i = 0; i < 4u; i++) s += (t[i] >> c) & 255u;
                            s = (s + 2u) / 4u;
                        }
                        out |= s << c;
                    }
                    next[y * nw + x] = out;
                }
            px = next;
            w = nw;
            h = nh;
            levels++;
            continue;
        }
        for (u32 y = 0; y < nh; y++) {
            f32 sy[3], sx[3];
            u32 y0, x0, ny = footprint(y, h, nh, &y0, sy);
            for (u32 x = 0; x < nw; x++) {
                u32 nx = footprint(x, w, nw, &x0, sx), out;
                f32 sum[4] = {0.0f, 0.0f, 0.0f, 0.0f}, plain[3] = {0.0f, 0.0f, 0.0f};
                for (u32 j = 0; j < ny; j++)
                    for (u32 i = 0; i < nx; i++) {
                        u32 t = px[(y0 + j) * w + x0 + i];
                        f32 share = sy[j] * sx[i], weight = (f32)(t >> 24) * share;
                        for (u32 c = 0; c < 3u; c++) {
                            f32 v = (f32)((t >> (c * 8u)) & 255u);
                            sum[c] += v * weight;
                            plain[c] += v * share;
                        }
                        sum[3] += weight;
                    }
                out = (u32)(sum[3] + 0.5f) << 24;
                for (u32 c = 0; c < 3u; c++) {
                    f32 v = (sum[3] > 0.0f ? sum[c] / sum[3] : plain[c]) + 0.5f;
                    out |= (v >= 255.0f ? 255u : (u32)v) << (c * 8u);
                }
                next[y * nw + x] = out;
            }
        }
        px = next;
        w = nw;
        h = nh;
        levels++;
    }
    return levels;
}

/* An over line's image (px, iw x ih): the game's own texels (game: its texture's w x h, decoded where the image was
 * kicked, pvr_pack_load.c) into its transparent pixels; 0 if it cannot (logged). */
int pvr_pack_put_over(u32 *px, u32 iw, u32 ih, const char *file, const u32 *game, u32 w, u32 h)
{
    u32 sx = iw / w, sy = ih / h;
    if (!game || !sx || !sy || iw % w || ih % h) {
        port_log("pvr: texture pack: %s is %ux%u, not a multiple of the texture's %ux%u%s\n", file, iw, ih, w, h,
                 game ? "" : " (or its texels could not be decoded)");
        return 0;
    }
    for (u32 y = 0; y < ih; y++)
        for (u32 x = 0; x < iw; x++)
            if (!(px[y * iw + x] >> 24)) px[y * iw + x] = game[(y / sy) * w + x / sx];
    return 1;
}

/* Stored mip chains: FILE.mips beside a pack's image holds the levels below the top one exactly as pvr_pack_mips makes
 * them from the PNG, so a run reads them instead of building them on the frame that first draws the image. port.exe
 * --pack-mips DIR writes them (tools/pack_builds.py runs it after a game's pack is built). Little-endian: "PMIP", the
 * version, w, h, levels, the PNG's size in bytes, the packed bytes that follow, 0, and the FNV-1a 64 of the PNG's last
 * 4096 bytes (they hold the zlib stream's Adler-32 of every pixel row and the last IDAT's CRC, so an image changed since
 * does not match). Then pvr_pack_chain_texels(w, h) RGBA8 texels, level 1 first, each byte as its difference from
 * left + above - above-left in its level (mod 256), packed with XPRESS Huffman (Windows' Compression API, cabinet.dll):
 * about a third of the raw size, unpacked at several hundred MB/s. A file that does not match is ignored and the chain
 * built as before (logged). Bump MIPS_VERSION whenever pvr_pack_mips's filter or this layout changes. */
#define MIPS_MAGIC 0x50494d50u /* "PMIP" */
#define MIPS_VERSION 1u
#define MIPS_TAIL 4096u
#define XPRESS_HUFF 4u /* COMPRESS_ALGORITHM_XPRESS_HUFF */
typedef struct MipsHead {
    u32 magic, version, w, h, levels, png_bytes, packed, spare;
    u64 png_tail;
} MipsHead;
typedef BOOL(WINAPI *MakeFn)(DWORD, void *, HANDLE *);
typedef BOOL(WINAPI *RunFn)(HANDLE, const void *, SIZE_T, void *, SIZE_T, SIZE_T *);
typedef BOOL(WINAPI *CloseFn)(HANDLE);

/* src (n bytes) packed (pack) or unpacked into dst (cap bytes) with XPRESS Huffman, through cabinet.dll loaded on first
 * use (nothing new is linked); the bytes written, 0 if it cannot. */
static size_t xpress(int pack, const void *src, size_t n, void *dst, size_t cap)
{
    static HMODULE dll;
    HANDLE h = NULL;
    SIZE_T got = 0;
    if (!dll) dll = LoadLibraryW(L"cabinet.dll");
    if (!dll) return 0;
    MakeFn make = (MakeFn)GetProcAddress(dll, pack ? "CreateCompressor" : "CreateDecompressor");
    RunFn run = (RunFn)GetProcAddress(dll, pack ? "Compress" : "Decompress");
    CloseFn done = (CloseFn)GetProcAddress(dll, pack ? "CloseCompressor" : "CloseDecompressor");
    if (!make || !run || !done || !make(XPRESS_HUFF, NULL, &h)) return 0;
    if (!run(h, src, n, dst, cap, &got)) got = 0;
    done(h);
    return got;
}

/* The chain's levels (from level 1, the top w x h) as each byte's difference from left + above - above-left (mod 256,
 * 0 off the level's edge); run from the end, so every neighbour is still the original. pvr_pack_unfilter undoes it. */
static void gradient(u8 *p, u32 w, u32 h)
{
    while (w > 1u || h > 1u) {
        u32 row;
        w = w > 1u ? w / 2u : 1u;
        h = h > 1u ? h / 2u : 1u;
        row = w * 4u;
        for (u32 y = h; y-- > 0;) {
            u8 *r = p + (size_t)y * row, *up = y ? r - row : NULL;
            for (u32 i = row; i-- > 0;)
                r[i] = (u8)(r[i] - ((i >= 4u ? r[i - 4u] : 0u) + (up ? up[i] : 0u) - (up && i >= 4u ? up[i - 4u] : 0u)));
        }
        p += (size_t)row * h;
    }
}

/* The chain (n texels after the top w x h) from f's packed bytes; 1 if it is all there. split (if not NULL): the ms of
 * the file read, the unpack and the unfilter. */
static int read_chain(FILE *f, u32 packed, u32 *chain, u32 w, u32 h, u32 n, f32 *split)
{
    u8 *z = packed ? malloc(packed) : NULL;
    f32 s[3];
    LARGE_INTEGER t;
    QueryPerformanceCounter(&t);
    int ok = n ? z && fread(z, 1, packed, f) == packed : !packed;
    s[0] = pvr_pack_ms(&t);
    ok = ok && (!n || xpress(0, z, packed, chain, (size_t)n * 4u) == (size_t)n * 4u);
    s[1] = pvr_pack_ms(&t);
    free(z);
    if (ok) pvr_pack_unfilter((u8 *)chain, w, h);
    s[2] = pvr_pack_ms(&t);
    if (split) memcpy(split, s, sizeof s);
    return ok && fgetc(f) == EOF;
}

static FILE *open_wide(const char *path, const wchar_t *mode)
{
    WCHAR wide[MAX_PATH_TEXT * 2 + 16];
    FILE *f = NULL;
    return pack_widen(path, wide, MAX_PATH_TEXT * 2 + 16) && !_wfopen_s(&f, wide, mode) ? f : NULL;
}

static u64 fnv64(u64 k, const u8 *p, size_t n)
{
    while (n--) k = (k ^ *p++) * 0x100000001b3ull;
    return k;
}

/* The header a FILE.mips for the PNG at path (w x h) has; 0 if the PNG cannot be read. */
static int mips_head(const char *path, u32 w, u32 h, MipsHead *m)
{
    u8 tail[MIPS_TAIL];
    FILE *f = open_wide(path, L"rb");
    __int64 size = -1;
    size_t n = 0;
    u32 levels = 1;
    if (!f) return 0;
    if (!_fseeki64(f, 0, SEEK_END)) size = _ftelli64(f);
    if (size > 0 && size < 0xffffffffll) {
        n = (size_t)(size < MIPS_TAIL ? size : MIPS_TAIL);
        if (_fseeki64(f, size - (__int64)n, SEEK_SET) || fread(tail, 1, n, f) != n) n = 0;
    }
    fclose(f);
    for (u32 a = w, b = h; a > 1u || b > 1u; levels++) {
        a = a > 1u ? a / 2u : 1u;
        b = b > 1u ? b / 2u : 1u;
    }
    *m = (MipsHead){MIPS_MAGIC, MIPS_VERSION, w, h, levels, (u32)size, 0, 0, fnv64(0xcbf29ce484222325ull, tail, n)};
    return n != 0;
}

/* The levels below the top of the PNG at path, whose pixels px (w x h, with room for the chain) were just read: from
 * FILE.mips when it matches the PNG, else built (pvr_pack_mips). *levels gets the count; returns how, for the log. over:
 * the game's texels went into the image, so levels stored from the PNG alone are not its levels. */
const char *pvr_pack_chain(const char *path, u32 *px, u32 w, u32 h, int over, u32 *levels, f32 *split)
{
    char side[MAX_PATH_TEXT * 2 + 8];
    MipsHead want = {0}, got;
    FILE *f;
    u32 n = pvr_pack_chain_texels(w, h);
    const char *how = "built (an over line's image)";
    if (!over) {
        snprintf(side, sizeof side, "%s.mips", path);
        how = "built (no stored mips)";
        if ((f = open_wide(side, L"rb")) != NULL) {
            how = "built (the stored mips do not match the image)";
            if (mips_head(path, w, h, &want) && fread(&got, sizeof got, 1, f) == 1 && (want.packed = got.packed, 1) &&
                !memcmp(&got, &want, sizeof got) && read_chain(f, got.packed, px + (size_t)w * h, w, h, n, split))
                how = NULL;
            fclose(f);
        }
    }
    if (!how) {
        *levels = want.levels;
        return "stored";
    }
    *levels = pvr_pack_mips(px, w, h);
    return how;
}

/* port.exe --pack-mips DIR: FILE.mips for each image of the pack in DIR that has none matching it, every line whatever
 * its if= setting (an over line's image gets none: the game's texels go into it, so its chain is built on the loader's
 * thread). --pack-mips-check DIR also builds every image's chain as a run without stored
 * mips does and compares it with what a run reads back. A line an image (the FNV-1a 64 of its levels below the top) and
 * a total on stdout; exit 1 if an image could not be done or differs, 2 if DIR has no index.txt. */
int pvr_pack_write_mips(const char *dir, int check)
{
    char path[MAX_PATH_TEXT * 2 + 2], side[MAX_PATH_TEXT * 2 + 8];
    u32 i, over, wrote = 0, kept = 0, overs = 0, bad = 0;
    u64 raw_all = 0, packed_all = 0;
    if (!pack_has_index(dir)) {
        printf("pack mips: no %s/index.txt\n", dir);
        return 2;
    }
    pvr_pack_every_line = 1;
    pvr_pack_option("--texture-pack", dir);
    for (i = 0; pvr_pack_entry(i, path, sizeof path, &over); i++) {
        u32 w = 0, h = 0, levels = 0, n, *px = over ? NULL : pvr_pack_read_png(path, &w, &h), *built = NULL;
        const char *how, *same = "";
        MipsHead m = {0};
        FILE *f = NULL;
        if (!px) {
            overs += over != 0u;
            bad += !over;
            continue;
        }
        n = pvr_pack_chain_texels(w, h);
        how = pvr_pack_chain(path, px, w, h, 0, &levels, NULL);
        if (strcmp(how, "stored")) {
            size_t raw = (size_t)n * 4u, cap = raw + raw / 2u + 65536u;
            u8 *z = malloc(cap), *filtered = malloc(raw + 1u);
            snprintf(side, sizeof side, "%s.mips", path);
            if (z && filtered && mips_head(path, w, h, &m)) {
                memcpy(filtered, px + (size_t)w * h, raw);
                gradient(filtered, w, h);
                m.packed = raw ? (u32)xpress(1, filtered, raw, z, cap) : 0u;
                if (m.packed || !raw) f = open_wide(side, L"wb");
            }
            if (!f || fwrite(&m, sizeof m, 1, f) != 1 || fwrite(z, 1, m.packed, f) != m.packed) bad++;
            if (f) fclose(f);
            free(z);
            free(filtered);
            wrote++;
        } else {
            kept++;
        }
        if (check) {
            built = malloc(((size_t)w * h + n) * sizeof *built);
            if (built) {
                memcpy(built, px, (size_t)w * h * 4u);
                pvr_pack_mips(built, w, h);
            }
            memset(px + (size_t)w * h, 0, (size_t)n * 4u);
            same = built && !strcmp(pvr_pack_chain(path, px, w, h, 0, &levels, NULL), "stored") &&
                           !memcmp(px + (size_t)w * h, built + (size_t)w * h, (size_t)n * 4u)
                       ? " read back = built"
                       : " DIFFERS";
            bad += same[1] == 'D';
            free(built);
        }
        mips_head(path, w, h, &m);
        snprintf(side, sizeof side, "%s.mips", path);
        if ((f = open_wide(side, L"rb")) != NULL) {
            if (fread(&m, sizeof m, 1, f) != 1) m.packed = 0;
            fclose(f);
        }
        raw_all += (u64)n * 4u;
        packed_all += m.packed;
        printf("%s %ux%u %u levels %016llx %s%s, %u KB packed of %u\n", path, w, h, levels,
               (unsigned long long)fnv64(0xcbf29ce484222325ull, (const u8 *)(px + (size_t)w * h), (size_t)n * 4u),
               strcmp(how, "stored") ? "written" : "already stored", same, m.packed / 1024u, n / 256u);
        free(px);
    }
    printf("pack mips: %u written, %u already stored, %u over lines (built at load), %u failed; %llu MB packed of %llu\n", wrote,
           kept, overs, bad, (unsigned long long)(packed_all >> 20), (unsigned long long)(raw_all >> 20));
    fflush(stdout);
    return bad != 0;
}
