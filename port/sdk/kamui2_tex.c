/* Platform layer: Kamui 2's texture surfaces in the kit's emulated texture memory (pvr_vram).
 *
 * kmCreateTextureSurface allocates first fit in the texture area (after the frame buffers, as large as the system
 * configuration's texture memory) and fills the surface as Kamui does: the byte address in texture memory, the TCW
 * pixel format bits and the TCW's mip-map / VQ / scan-order / stride bits. The game builds its TCWs from those, so
 * pvr/ finds the data by address. kmLoadTexture copies the data as stored (the game converts or twiddles it).
 *
 * Needs from the game's headers: the function names below as the game's registry has them. */
#include "game.h"
#include "platform.h"
#include "kamui2.h"

#define TEX_ALIGN 32u
#define TEX_BLOCKS 4096u
#define FRAME_BUFFER_BYTES (640u * 480u * 2u)
#define SURFACE_FRAME 1u
#define SURFACE_TEXTURE 2u
#define SURFACE_SMALL_VQ 3u
#define BITDEPTH_16 1u
#define TCW_MIPMAP 0x80000000u
#define TCW_VQ 0x40000000u
#define TCW_NONTWIDDLED 0x04000000u
#define TCW_STRIDE 0x02000000u
#define TCW_FORMAT_SHIFT 27
#define K2_NOT_ENOUGH_MEMORY 3

typedef struct Block {
    u32 at, size;
    int used;
} Block;

static Block g_blocks[TEX_BLOCKS];
static u32 g_block_count, g_stride_width;

/* After an in-between frame puts texture memory back (runtime/frame_extra.c): pvr/ decodes it again. */
static void vram_undone(void *p, u32 size) { pvr_vram_written((u32)((u8 *)p - pvr_vram()), size); }

void k2_tex_init(const K2SysConfig *config)
{
    port_extra_state(g_blocks, sizeof g_blocks);
    port_extra_state(&g_block_count, sizeof g_block_count);
    port_extra_state(&g_stride_width, sizeof g_stride_width);
    u32 base = (config->frame_buffer_count ? config->frame_buffer_count : 2u) * FRAME_BUFFER_BYTES;
    u32 size = config->texture_memory_size;
    base = (base + 0xffffu) & ~0xffffu;
    if (!size || size > PVRX_VRAM_SIZE - base) size = PVRX_VRAM_SIZE - base;
    g_blocks[0].at = base;
    g_blocks[0].size = size;
    g_blocks[0].used = 0;
    g_block_count = 1;
}

static u32 alloc(u32 size)
{
    size = (size + TEX_ALIGN - 1u) & ~(TEX_ALIGN - 1u);
    for (u32 i = 0; i < g_block_count; i++) {
        Block *b = &g_blocks[i];
        if (b->used || b->size < size) continue;
        if (b->size > size && g_block_count < TEX_BLOCKS) { /* split: the rest stays free after it */
            memmove(b + 2, b + 1, (g_block_count - i - 1u) * sizeof *b);
            b[1].at = b->at + size;
            b[1].size = b->size - size;
            b[1].used = 0;
            b->size = size;
            g_block_count++;
        }
        b->used = 1;
        return b->at;
    }
    return 0;
}

static void release(u32 at)
{
    for (u32 i = 0; i < g_block_count; i++) {
        if (g_blocks[i].at != at || !g_blocks[i].used) continue;
        g_blocks[i].used = 0;
        if (i + 1u < g_block_count && !g_blocks[i + 1].used) { /* merge with the next free block */
            g_blocks[i].size += g_blocks[i + 1].size;
            memmove(&g_blocks[i + 1], &g_blocks[i + 2], (g_block_count - i - 2u) * sizeof g_blocks[0]);
            g_block_count--;
        }
        if (i > 0 && !g_blocks[i - 1].used) { /* and with the previous one */
            g_blocks[i - 1].size += g_blocks[i].size;
            memmove(&g_blocks[i], &g_blocks[i + 1], (g_block_count - i - 1u) * sizeof g_blocks[0]);
            g_block_count--;
        }
        return;
    }
}

/* Bytes of a mip chain of 16-bit texels (Kamui keeps 6 bytes in front of the 1x1 level). */
static u32 mip_bytes(u32 edge, u32 bits)
{
    u32 total = 0;
    for (u32 e = edge; e; e >>= 1) total += e * e * bits / 8u;
    return total + (bits == 16u ? 6u : 3u);
}

s32 kmCreateTextureSurface(KMSURFACEDESC *surface, u32 width, u32 height, u32 type)
{
    K2Surface *s = (K2Surface *)surface;
    u32 kind = type & 0xff00u, format = type & 0xffu, flags = 0, bits = 16u, bytes;
    switch (kind) {
    case 0x0100u: break;                                                 /* twiddled */
    case 0x0200u: flags = TCW_MIPMAP; break;                             /* twiddled, mip-mapped */
    case 0x0300u: case 0x1000u: flags = TCW_VQ; break;                   /* VQ, small VQ */
    case 0x0400u: case 0x1100u: flags = TCW_VQ | TCW_MIPMAP; break;
    case 0x0500u: case 0x0600u: format = 5u; bits = 4u; flags = kind == 0x0600u ? TCW_MIPMAP : 0u; break;
    case 0x0700u: case 0x0800u: format = 6u; bits = 8u; flags = kind == 0x0800u ? TCW_MIPMAP : 0u; break;
    case 0x0900u: flags = TCW_NONTWIDDLED; break;                        /* rectangle */
    case 0x0b00u: flags = TCW_NONTWIDDLED | TCW_STRIDE; break;           /* stride (Km_SetFlag8Value's width) */
    case 0x0d00u: break;                                                 /* twiddled rectangle */
    default:
        pvr_todo("kamui2: kmCreateTextureSurface type %04x", type);
        return K2_ERROR;
    }
    if (format == 5u && bits == 16u) format = 0u; /* RGB555 is stored as ARGB1555 */
    if (flags & TCW_VQ)
        bytes = 2048u + ((flags & TCW_MIPMAP) ? mip_bytes(width, 8u) / 4u : width * height / 4u);
    else if (flags & TCW_MIPMAP)
        bytes = mip_bytes(width, bits);
    else
        bytes = ((flags & TCW_STRIDE) && g_stride_width ? g_stride_width : width) * height * bits / 8u;
    u32 at = alloc(bytes);
    if (!at) {
        pvr_todo("kamui2: texture memory full (%u bytes asked)", bytes);
        return K2_NOT_ENOUGH_MEMORY;
    }
    s->surface_type = kind == 0x1000u || kind == 0x1100u ? SURFACE_SMALL_VQ : SURFACE_TEXTURE;
    s->bit_depth = BITDEPTH_16;
    s->pixel_format = format << TCW_FORMAT_SHIFT;
    s->width = width;
    s->height = height;
    s->size = bytes;
    s->flags = flags;
    s->address = at;
    return K2_OK;
}

s32 kmLoadTexture(KMSURFACEDESC *surface, const void *texture)
{
    const K2Surface *s = (const K2Surface *)surface;
    if (!texture || !s->size || s->address + s->size > PVRX_VRAM_SIZE) return K2_ERROR;
    port_extra_undo(pvr_vram() + s->address, s->size, vram_undone);
    memmove(pvr_vram() + s->address, texture, s->size);
    pvr_vram_written(s->address, s->size);
    return K2_OK;
}

s32 kmFreeTexture(KMSURFACEDESC *surface)
{
    K2Surface *s = (K2Surface *)surface;
    if (s->address) release(s->address);
    s->address = 0;
    return K2_OK;
}

/* The stride of stride-type textures (the TEXT_CONTROL register: one stride for all of them). */
s32 Km_SetFlag8Value(u32 value)
{
    g_stride_width = value;
    pvr_set_stride(value);
    return K2_OK;
}

/* Copies a texture's stored data out (a frame buffer surface: the last frame, once pvr/ can read it back). */
s32 Km_ReadSurface(void *dst, KMSURFACEDESC *surface)
{
    const K2Surface *s = (const K2Surface *)surface;
    if (s->surface_type == SURFACE_FRAME) {
        if (!pvr_read_frame(dst, s->width, s->height)) pvr_todo("kamui2: Km_ReadSurface of the frame buffer");
        return K2_OK;
    }
    if (s->address + s->size > PVRX_VRAM_SIZE) return K2_ERROR;
    memmove(dst, pvr_vram() + s->address, s->size);
    return K2_OK;
}

/* The displayed frame buffer: 640x480, RGB565. */
s32 Km_GetFrameBufferSurface(KMSURFACEDESC *surface)
{
    K2Surface *s = (K2Surface *)surface;
    s->surface_type = SURFACE_FRAME;
    s->bit_depth = BITDEPTH_16;
    s->pixel_format = 0;
    s->width = 640;
    s->height = 480;
    s->size = FRAME_BUFFER_BYTES;
    s->flags = 0;
    s->address = 0;
    return K2_OK;
}
