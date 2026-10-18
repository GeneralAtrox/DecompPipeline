/* Frame dumps: PNGs of the frame target through WIC, for the automated checks (goldens, emulator comparisons).
 *   --dump-frames 10,250,600   those frames (0 is the first presented)
 *   --dump-every N             every Nth frame
 *   --dump-dir DIR             where (default: frames, next to the log)
 *   --dump-watch ADDR,..       guest u32 words logged with each dump (e.g. a demo clock, to pair emulator frames)
 *   --dump-ram 1               also frame_NNNNNN.ram, the 16 MB of guest RAM (to diff against an emulator's)
 *   --dump-extra N             also the first N in-between frames (--present interp) after each dumped frame, as
 *                              frame_NNNNNN_eK.png (K from 1)
 * Each dump is frame_NNNNNN.png at the frame's size and, when that is not 640x480, frame_NNNNNN_ref.png scaled to
 * the console's 640x480 (WIC's Fant filter), the size the reference captures are compared at. No alpha. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "gfx.h"
#include <wincodec.h>
#include "platform.h"

#define MAX_LISTED 256
#define REF_W 640u
#define REF_H 480u
#define MAX_WATCH 4

static u32 g_listed[MAX_LISTED];
static u32 g_listed_count, g_every;
static u32 g_watch[MAX_WATCH], g_watch_count;
static int g_ram;
static u32 g_extra, g_extra_left, g_extra_of, g_extra_k; /* --dump-extra */
static const char *g_dir = "frames";
static IWICImagingFactory *g_wic;
static ID3D11Texture2D *g_staging;
static u32 g_staging_w, g_staging_h;

int gfx_capture_option(const char *arg, const char *value)
{
    if (!value) return 0;
    if (!strcmp(arg, "--dump-every")) {
        g_every = (u32)strtoul(value, NULL, 10);
        return 2;
    }
    if (!strcmp(arg, "--dump-dir")) {
        g_dir = value;
        return 2;
    }
    if (!strcmp(arg, "--dump-extra")) {
        g_extra = (u32)strtoul(value, NULL, 10);
        return 2;
    }
    if (!strcmp(arg, "--dump-ram")) {
        g_ram = atoi(value) != 0;
        return 2;
    }
    if (!strcmp(arg, "--dump-watch")) {
        for (const char *p = value; *p && g_watch_count < MAX_WATCH;) {
            char *end;
            g_watch[g_watch_count++] = (u32)strtoul(p, &end, 16);
            p = *end == ',' ? end + 1 : end + strlen(end);
        }
        return 2;
    }
    if (strcmp(arg, "--dump-frames")) return pvr_option(arg, value);
    for (const char *p = value; *p && g_listed_count < MAX_LISTED;) {
        char *end;
        g_listed[g_listed_count++] = (u32)strtoul(p, &end, 10);
        p = *end == ',' ? end + 1 : end + strlen(end);
    }
    return 2;
}

static int wanted(u32 frame)
{
    if (g_every && frame % g_every == 0) return 1;
    for (u32 i = 0; i < g_listed_count; i++)
        if (g_listed[i] == frame) return 1;
    return 0;
}

/* Writes a source (the frame, or the frame scaled) as a 24-bit PNG. */
static int write_png(const char *path, IWICBitmapSource *src, u32 w, u32 h)
{
    wchar_t wpath[MAX_PATH];
    IWICStream *stream = NULL;
    IWICBitmapEncoder *enc = NULL;
    IWICBitmapFrameEncode *frame = NULL;
    WICPixelFormatGUID fmt = GUID_WICPixelFormat24bppBGR;
    HRESULT hr;
    if (!MultiByteToWideChar(CP_UTF8, 0, path, -1, wpath, MAX_PATH)) return 0;
    hr = IWICImagingFactory_CreateStream(g_wic, &stream);
    if (SUCCEEDED(hr)) hr = IWICStream_InitializeFromFilename(stream, wpath, GENERIC_WRITE);
    if (SUCCEEDED(hr)) hr = IWICImagingFactory_CreateEncoder(g_wic, &GUID_ContainerFormatPng, NULL, &enc);
    if (SUCCEEDED(hr)) hr = IWICBitmapEncoder_Initialize(enc, (IStream *)stream, WICBitmapEncoderNoCache);
    if (SUCCEEDED(hr)) hr = IWICBitmapEncoder_CreateNewFrame(enc, &frame, NULL);
    if (SUCCEEDED(hr)) hr = IWICBitmapFrameEncode_Initialize(frame, NULL);
    if (SUCCEEDED(hr)) hr = IWICBitmapFrameEncode_SetSize(frame, w, h);
    if (SUCCEEDED(hr)) hr = IWICBitmapFrameEncode_SetPixelFormat(frame, &fmt);
    if (SUCCEEDED(hr)) hr = IWICBitmapFrameEncode_WriteSource(frame, src, NULL);
    if (SUCCEEDED(hr)) hr = IWICBitmapFrameEncode_Commit(frame);
    if (SUCCEEDED(hr)) hr = IWICBitmapEncoder_Commit(enc);
    if (frame) IWICBitmapFrameEncode_Release(frame);
    if (enc) IWICBitmapEncoder_Release(enc);
    if (stream) IWICStream_Release(stream);
    if (FAILED(hr)) port_log("gfx: cannot write %s (%08lx)\n", path, (unsigned long)hr);
    return SUCCEEDED(hr);
}

static int capture_init(void)
{
    HRESULT hr = CoInitializeEx(NULL, COINIT_APARTMENTTHREADED);
    if (FAILED(hr) && hr != RPC_E_CHANGED_MODE) return 0;
    hr = CoCreateInstance(&CLSID_WICImagingFactory, NULL, CLSCTX_INPROC_SERVER, &IID_IWICImagingFactory,
                          (void **)&g_wic);
    if (FAILED(hr)) {
        port_log("gfx: no WIC imaging factory (%08lx): frame dumps are off\n", (unsigned long)hr);
        g_every = 0;
        g_listed_count = 0;
        return 0;
    }
    CreateDirectoryA(g_dir, NULL);
    return 1;
}

/* Copies the frame target into a CPU-readable staging texture; returns the BGR pixels (malloc'd) or NULL. */
static u8 *read_frame(void)
{
    D3D11_TEXTURE2D_DESC d;
    D3D11_MAPPED_SUBRESOURCE m;
    u32 w = g_gfx.frame_w, h = g_gfx.frame_h;
    if (!g_staging || g_staging_w != w || g_staging_h != h) {
        if (g_staging) ID3D11Texture2D_Release(g_staging);
        g_staging = NULL;
        ID3D11Texture2D_GetDesc(g_gfx.frame, &d);
        d.Usage = D3D11_USAGE_STAGING;
        d.BindFlags = 0;
        d.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
        if (FAILED(ID3D11Device_CreateTexture2D(g_gfx.dev, &d, NULL, &g_staging))) return NULL;
        g_staging_w = w;
        g_staging_h = h;
    }
    ID3D11DeviceContext_CopyResource(g_gfx.ctx, (ID3D11Resource *)g_staging, (ID3D11Resource *)g_gfx.frame);
    if (FAILED(ID3D11DeviceContext_Map(g_gfx.ctx, (ID3D11Resource *)g_staging, 0, D3D11_MAP_READ, 0, &m))) return NULL;
    u8 *bgr = malloc((size_t)w * h * 3u);
    if (bgr) {
        for (u32 y = 0; y < h; y++) {
            const u8 *s = (const u8 *)m.pData + (size_t)y * m.RowPitch;
            u8 *o = bgr + (size_t)y * w * 3u;
            for (u32 x = 0; x < w; x++, s += 4, o += 3) {
                o[0] = s[2];
                o[1] = s[1];
                o[2] = s[0];
            }
        }
    }
    ID3D11DeviceContext_Unmap(g_gfx.ctx, (ID3D11Resource *)g_staging, 0);
    return bgr;
}

/* Writes the frame target as <name>.png and, when it is not 640x480, <name>_ref.png scaled to it. */
static void dump(const char *name)
{
    static int ready = -1;
    char path[MAX_PATH];
    IWICBitmap *bmp = NULL;
    IWICBitmapScaler *scaler = NULL;
    u32 w = g_gfx.frame_w, h = g_gfx.frame_h;
    if (ready < 0) ready = capture_init();
    if (!ready) return;
    u8 *bgr = read_frame();
    if (!bgr) return;
    if (SUCCEEDED(IWICImagingFactory_CreateBitmapFromMemory(g_wic, w, h, &GUID_WICPixelFormat24bppBGR, w * 3u,
                                                            w * h * 3u, bgr, &bmp))) {
        snprintf(path, sizeof path, "%s/%s.png", g_dir, name);
        write_png(path, (IWICBitmapSource *)bmp, w, h);
        if ((w != REF_W || h != REF_H) && SUCCEEDED(IWICImagingFactory_CreateBitmapScaler(g_wic, &scaler)) &&
            SUCCEEDED(IWICBitmapScaler_Initialize(scaler, (IWICBitmapSource *)bmp, REF_W, REF_H,
                                                  WICBitmapInterpolationModeFant))) {
            snprintf(path, sizeof path, "%s/%s_ref.png", g_dir, name);
            write_png(path, (IWICBitmapSource *)scaler, REF_W, REF_H);
        }
        if (scaler) IWICBitmapScaler_Release(scaler);
        IWICBitmap_Release(bmp);
    }
    free(bgr);
}

/* The scene dump's picture: the frame target as drawn, a 24-bit bottom-up BMP (no WIC needed to read it back). */
int gfx_capture_bmp(const char *path)
{
    u32 w = g_gfx.frame_w, h = g_gfx.frame_h, row = (w * 3u + 3u) & ~3u, size = 54u + row * h;
    u8 head[54] = {'B', 'M'}, pad[3] = {0};
    FILE *f = NULL;
    int ok;
    if (!g_gfx.frame || !w || !h) return 0;
    u8 *bgr = read_frame();
    if (!bgr) return 0;
    memcpy(head + 2, &size, 4);
    head[10] = 54;
    head[14] = 40;
    memcpy(head + 18, &w, 4);
    memcpy(head + 22, &h, 4);
    head[26] = 1;
    head[28] = 24;
    ok = fopen_s(&f, path, "wb") == 0 && f && fwrite(head, sizeof head, 1, f) == 1;
    for (u32 y = h; ok && y-- > 0;)
        ok = fwrite(bgr + (size_t)y * w * 3u, w * 3u, 1, f) == 1 && (row == w * 3u || fwrite(pad, row - w * 3u, 1, f) == 1);
    if (f) ok = fclose(f) == 0 && ok;
    free(bgr);
    return ok;
}

void gfx_capture_extra(void)
{
    char name[32];
    if (!g_extra_left || !g_gfx.frame) return;
    g_extra_left--;
    snprintf(name, sizeof name, "frame_%06u_e%u", g_extra_of, ++g_extra_k);
    dump(name);
}

void gfx_capture_frame(u32 frame)
{
    char path[MAX_PATH];
    if (!wanted(frame) || !g_gfx.frame) return;
    snprintf(path, sizeof path, "frame_%06u", frame);
    dump(path);
    g_extra_left = g_extra;
    g_extra_of = frame;
    g_extra_k = 0;
    port_log("gfx: frame %u dumped (vblank %u)", frame, port_vblank_count());
    for (u32 i = 0; i < g_watch_count; i++) {
        u32 a = guest_fold(g_watch[i] & ~3u);
        if (guest_is_ram(a)) port_log(", [%08x] = %u", a, *(volatile const u32 *)(uintptr_t)a);
    }
    port_log("\n");
    if (g_ram) {
        FILE *f = NULL;
        snprintf(path, sizeof path, "%s/frame_%06u.ram", g_dir, frame);
        if (fopen_s(&f, path, "wb") == 0 && f) {
            fwrite((const void *)(uintptr_t)GUEST_RAM_BASE, 1, GUEST_RAM_SIZE, f);
            fclose(f);
        }
    }
}
