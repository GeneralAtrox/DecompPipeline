/* dpimg console textures: Dreamcast PVR texture bytes to RGBA through the kit's own decoder, port/pvr/pvr_twiddle.c,
 * compiled here as it is (twiddled, VQ, rectangle and stride layouts; ARGB1555, RGB565, ARGB4444, YUV422, 4- and
 * 8-bit palettes; mip chains), so an art tool decodes a texture exactly as the port draws it. The decoder reads the
 * kit's texture memory and palette RAM: here they are a private 8 MB buffer holding the caller's bytes at address 0
 * and the caller's palette. Saturn, GameCube and Xbox 360 have no standalone decoder in DecompPipeline and no art
 * tool needs one yet; one is added here the same way when a card does. */
#define DPIMG_BUILD
#include "dpimg_int.h"

#include <stdarg.h>
#include <stdlib.h>
#include <string.h>

/* What pvr_twiddle.c takes from the kit (port/pvr/pvr_int.h, pvr.h), standing in for that header. */
#define PVRX_INT_H
typedef uint8_t u8;
typedef uint16_t u16;
typedef uint32_t u32;
typedef int32_t s32;
#define PVRX_VRAM_SIZE 0x800000u

static u8 *g_vram;
static const uint32_t *g_pal;
static u32 g_pal_count, g_pal_format;

static const u8 *pvr_vram(void) { return g_vram; }
static u32 pvr_palette_entry(u32 index) { return index < g_pal_count ? g_pal[index] : 0u; }
static u32 pvr_palette_format(void) { return g_pal_format; }
static void pvr_todo(const char *fmt, ...) { (void)fmt; }
static void port_log(const char *fmt, ...) { (void)fmt; }

#include "../../pvr/pvr_twiddle.c"

dpimg *dpimg_decode_pvr(uint32_t tcw, int w, int h, int stride, const void *data, size_t size,
                        const uint32_t *palette, int palette_count, int palette_format)
{
    u32 n, *texels;
    dpimg *out;
    if (w <= 0 || h <= 0 || w > 1024 || h > 1024 || size > PVRX_VRAM_SIZE) return NULL;
    tcw &= ~TCW_ADDR_MASK; /* the bytes sit at address 0 */
    if (!g_vram && !(g_vram = (u8 *)calloc(PVRX_VRAM_SIZE, 1))) return NULL;
    memset(g_vram, 0, PVRX_VRAM_SIZE);
    memcpy(g_vram, data, size);
    g_pal = palette, g_pal_count = palette ? (u32)palette_count : 0u, g_pal_format = (u32)palette_format;
    n = pvr_decode_texels(tcw, (u32)w, (u32)h);
    texels = (u32 *)malloc((size_t)n * 4);
    out = texels ? dpimg_new(w, h, 0) : NULL;
    if (!out || !pvr_decode(tcw, (u32)w, (u32)h, (u32)stride, texels, n)) {
        free(texels);
        dpimg_free(out);
        return NULL;
    }
    memcpy(out->px, texels, (size_t)w * h * 4); /* the top level first; r | g << 8 | b << 16 | a << 24 is RGBA */
    free(texels);
    return out;
}

uint32_t dpimg_pvr_bytes(uint32_t tcw, int w, int h, int stride)
{
    return pvr_texture_bytes(tcw & ~TCW_ADDR_MASK, (u32)w, (u32)h, (u32)stride);
}
