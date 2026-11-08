/* Texture packs: a PNG's pixels, its mip chain and an over line's merge (pvr_pack.c has the packs themselves). Nearest existing: pvr_pack.c,
 * split for its 400-line cap. */
#include <stdlib.h>
#define COBJMACROS
#include <windows.h>
#include <wincodec.h>
#include "pvr_int.h"
#include "platform.h"

#define MAX_PATH_TEXT PVR_PACK_PATH

static IWICImagingFactory *g_wic;

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
u32 pvr_pack_mips(u32 *px, u32 w, u32 h)
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

/* An over line's image (px, iw x ih): the game's own texels into its transparent pixels; 0 if it cannot (logged). */
int pvr_pack_put_over(u32 *px, u32 iw, u32 ih, const char *file, u32 tcw, u32 w, u32 h, u32 stride)
{
    u32 need = pvr_decode_texels(tcw, w, h), sx = iw / w, sy = ih / h, done = 0;
    u32 *game;
    if (!sx || !sy || iw % w || ih % h) {
        port_log("pvr: texture pack: %s is %ux%u, not a multiple of the texture's %ux%u\n", file, iw, ih, w, h);
        return 0;
    }
    game = malloc((size_t)need * sizeof *game);
    if (game && pvr_decode(tcw, w, h, stride, game, need)) {
        for (u32 y = 0; y < ih; y++)
            for (u32 x = 0; x < iw; x++)
                if (!(px[y * iw + x] >> 24)) px[y * iw + x] = game[(y / sy) * w + x / sx];
        done = 1;
    }
    free(game);
    return (int)done;
}
