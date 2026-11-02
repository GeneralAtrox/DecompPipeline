/* --texture-keys DIR: lists the textures a run decodes, for building a texture pack (pvr_pack.c) of a game that is
 * not built from its own files. Each texture, the first time it is decoded, is written as DIR/KEY.png (the top
 * level as the renderer draws it: palettes and all) and one line of DIR/keys.txt:
 *   KEY tcw=TTTTTTTT WxH stride=S vblank=V [palettized: not packable] [VQ] [mip-mapped]
 * KEY is the pack's key (pvr_pack_key), so a PNG redrawn at any size and named in index.txt replaces that texture.
 * A run that is not a check run asks for it with a pack off (--texture-pack off): the PNGs are the originals.
 * Nearest existing: pvr_pack.c's key and texture_pack.py (those need the game's own texture bytes; this reads them
 * out of a run's texture memory, so a disc game's textures can be found without its pack builder). */
#include <stdio.h>
#include <string.h>
#define COBJMACROS
#include <windows.h>
#include <wincodec.h>
#include "pvr_int.h"
#include "platform.h"

#define MAX_KEYS 16384u
#define PATH 1024

static char g_dir[PATH];
static u64 g_seen[MAX_KEYS];
static u32 g_seen_n;
static IWICImagingFactory *g_wic;

int pvr_keys_option(const char *arg, const char *value)
{
    if (strcmp(arg, "--texture-keys") || !value) return 0;
    snprintf(g_dir, sizeof g_dir, "%s", value);
    CreateDirectoryA(g_dir, NULL);
    return 2;
}

static int write_png(const char *path, const u32 *texels, u32 w, u32 h)
{
    WCHAR wpath[PATH];
    IWICBitmap *bmp = NULL;
    IWICStream *stream = NULL;
    IWICBitmapEncoder *enc = NULL;
    IWICBitmapFrameEncode *frame = NULL;
    WICPixelFormatGUID fmt = GUID_WICPixelFormat32bppRGBA;
    HRESULT hr = S_OK;
    if (!g_wic) {
        hr = CoInitializeEx(NULL, COINIT_APARTMENTTHREADED);
        if (SUCCEEDED(hr) || hr == RPC_E_CHANGED_MODE)
            hr = CoCreateInstance(&CLSID_WICImagingFactory, NULL, CLSCTX_INPROC_SERVER, &IID_IWICImagingFactory,
                                  (void **)&g_wic);
    }
    if (SUCCEEDED(hr) && !MultiByteToWideChar(CP_UTF8, 0, path, -1, wpath, PATH)) hr = E_INVALIDARG;
    if (SUCCEEDED(hr))
        hr = IWICImagingFactory_CreateBitmapFromMemory(g_wic, w, h, &GUID_WICPixelFormat32bppRGBA, w * 4u,
                                                       w * h * 4u, (BYTE *)texels, &bmp);
    if (SUCCEEDED(hr)) hr = IWICImagingFactory_CreateStream(g_wic, &stream);
    if (SUCCEEDED(hr)) hr = IWICStream_InitializeFromFilename(stream, wpath, GENERIC_WRITE);
    if (SUCCEEDED(hr)) hr = IWICImagingFactory_CreateEncoder(g_wic, &GUID_ContainerFormatPng, NULL, &enc);
    if (SUCCEEDED(hr)) hr = IWICBitmapEncoder_Initialize(enc, (IStream *)stream, WICBitmapEncoderNoCache);
    if (SUCCEEDED(hr)) hr = IWICBitmapEncoder_CreateNewFrame(enc, &frame, NULL);
    if (SUCCEEDED(hr)) hr = IWICBitmapFrameEncode_Initialize(frame, NULL);
    if (SUCCEEDED(hr)) hr = IWICBitmapFrameEncode_SetSize(frame, w, h);
    if (SUCCEEDED(hr)) hr = IWICBitmapFrameEncode_SetPixelFormat(frame, &fmt);
    if (SUCCEEDED(hr)) hr = IWICBitmapFrameEncode_WriteSource(frame, (IWICBitmapSource *)bmp, NULL);
    if (SUCCEEDED(hr)) hr = IWICBitmapFrameEncode_Commit(frame);
    if (SUCCEEDED(hr)) hr = IWICBitmapEncoder_Commit(enc);
    if (frame) IWICBitmapFrameEncode_Release(frame);
    if (enc) IWICBitmapEncoder_Release(enc);
    if (stream) IWICStream_Release(stream);
    if (bmp) IWICBitmap_Release(bmp);
    return SUCCEEDED(hr);
}

void pvr_keys_note(u32 tcw, u32 w, u32 h, u32 stride, const u32 *texels)
{
    char path[PATH + 40];
    u32 first, count, pal;
    u64 key;
    FILE *f = NULL;
    if (!g_dir[0]) return;
    key = pvr_pack_key(pvr_vram(), tcw, w, h, stride);
    for (u32 i = 0; i < g_seen_n; i++)
        if (g_seen[i] == key) return;
    if (g_seen_n == MAX_KEYS) return;
    g_seen[g_seen_n++] = key;
    pal = pvr_palette_range(tcw, &first, &count);
    snprintf(path, sizeof path, "%s/%016llx.png", g_dir, (unsigned long long)key);
    write_png(path, texels, w, h);
    snprintf(path, sizeof path, "%s/keys.txt", g_dir);
    if (fopen_s(&f, path, "a") || !f) return;
    fprintf(f, "%016llx tcw=%08x %ux%u stride=%u vblank=%u%s%s%s\n", (unsigned long long)key, tcw, w, h, stride,
            port_vblank_count(), pal ? " palettized: not packable" : "", tcw & (1u << 30) ? " VQ" : "",
            tcw & (1u << 31) ? " mip-mapped" : "");
    fclose(f);
}
