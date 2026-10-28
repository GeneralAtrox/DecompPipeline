/* Platform layer: Kamui 1's texture surfaces in the kit's emulated texture memory (pvr_vram).
 *
 * kmCreateTextureSurface fills the surface as Kamui 1 does from the texture type (kind << 8 | pixel format): the
 * surface type (2, or 3 for a small VQ texture), the TCW pixel format bits, the flags (bit 0 mip-mapped, 2 twiddled,
 * 3 VQ, 4 stride, 5 palettized), the size in bytes by Kamui's formulas, and at 0x1c the byte address in texture
 * memory, first fit after the frame buffers. The game builds its TCWs from those, so pvr/ finds the data by address.
 * kmLoadTexture copies the data as stored (the game converts or twiddles it), where Kamui puts it: a 16-bit twiddled
 * mip chain 4 bytes in, a small VQ texture's short codebook at the start and its index bytes 2 KB in.
 *
 * Needs from the game's headers: KMSURFACEDESC (read through kamui1.h's K1Surface) and the function names below as
 * the game's registry has them. */
#include "game.h"
#include "platform.h"
#include "port.h"
#include "kamui1.h"

#define K1_NOT_ENOUGH_MEMORY 3
#define K1_INVALID 4
#define TEX_ALIGN 32u
#define TEX_BLOCKS 4096u
#define FRAME_BUFFER_BYTES (640u * 480u * 2u)
#define SURFACE_TEXTURE 2u
#define SURFACE_SMALL_VQ 3u
#define FLAG_MIPMAP 0x01u
#define FLAG_TWIDDLED 0x04u
#define FLAG_VQ 0x08u
#define FLAG_STRIDE 0x10u
#define FLAG_PALETTE 0x20u
#define FORMAT_SHIFT 27
#define FORMAT_PAL4 0x28000000u
#define FORMAT_PAL8 0x30000000u
#define FORMATS 5u /* ARGB1555, RGB565, ARGB4444, YUV422, bump */
#define VQ_CODEBOOK 0x800u
#define MIP16_LEAD 4u /* a 16-bit twiddled mip chain starts 4 bytes in */
#define EDGE_MIN 8u
#define EDGE_MAX 1024u
#define EDGE_CODES 11u

typedef struct Block {
    u32 at, size;
    int used;
} Block;

static Block g_blocks[TEX_BLOCKS];
static u32 g_block_count;

/* Per edge code (log2 of the larger edge): a small VQ texture's codebook bytes and index bytes, plain and
 * mip-mapped, and a 16-bit twiddled mip chain's bytes (from the 1x1 level up). */
static const u16 g_small_vq_book[2][7] = {{0, 0, 0, 128, 128, 256, 1024}, {0, 0, 0, 128, 128, 512, 2048}};
static const u16 g_small_vq_index[2][7] = {{0, 1, 4, 16, 64, 256, 1024}, {1, 2, 6, 22, 86, 342, 1366}};
static const u32 g_mip16_bytes[EDGE_CODES] = {0, 0, 0, 172, 684, 2732, 10924, 43692, 174764, 699052, 2796204};

static void *host(u32 addr) { return (void *)(uintptr_t)guest_fold(addr); }

static void vram_undone(void *p, u32 size) { pvr_vram_written((u32)((u8 *)p - pvr_vram()), size); }

/* The texture area: after the frame buffers, as large as the system configuration's texture memory. */
static void init(void)
{
    const K1SysConfig *c = k1_config();
    u32 count = c && c->frame_buffer_count ? c->frame_buffer_count : 2u;
    u32 base = (count * FRAME_BUFFER_BYTES + 0xffffu) & ~0xffffu, size = c ? k1_texture_bytes(c) : 0u;
    if (!size || size > PVRX_VRAM_SIZE - base) size = PVRX_VRAM_SIZE - base;
    port_extra_state(g_blocks, sizeof g_blocks);
    port_extra_state(&g_block_count, sizeof g_block_count);
    g_blocks[0].at = base;
    g_blocks[0].size = size;
    g_blocks[0].used = 0;
    g_block_count = 1;
}

static u32 alloc(u32 size)
{
    if (!g_block_count) init();
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

static u32 edge_code(u32 width, u32 height)
{
    u32 e = width > height ? width : height, n = 0;
    while (n + 1u < EDGE_CODES && (1u << n) < e) n++;
    return n;
}

static int edge_ok(u32 e) { return e >= EDGE_MIN && e <= EDGE_MAX && !(e & (e - 1u)); }

/* Texels (or VQ indices) of a mip chain below a w x h level, Kamui's way: the levels down to the smaller edge's 1,
 * then `last` for a square 1x1 level (padding included), else the last level's own count. */
static u32 mip_tail(u32 w, u32 h, u32 last)
{
    u32 t = 0, m;
    w >>= 1;
    h >>= 1;
    for (m = w < h ? w : h; m > 1u; m >>= 1, w >>= 1, h >>= 1) t += w * h;
    return t + (w == h ? last : w * h);
}

static u32 surface_bytes(const K1Surface *s)
{
    u32 w = s->width, h = s->height, mip = s->flags & FLAG_MIPMAP, t;
    if (s->surface_type == SURFACE_SMALL_VQ) {
        t = (w / 2u) * (h / 2u) + (mip ? mip_tail(w / 2u, h / 2u, 2u) + 10u : 0u);
        return t + g_small_vq_book[mip != 0u][edge_code(w, h)];
    }
    if (s->flags & FLAG_VQ) {
        t = (w / 2u) * (h / 2u);
        if (mip)
            t += mip_tail(w / 2u, h / 2u, 2u) + 10u;
        else if (w == EDGE_MIN && h == EDGE_MIN)
            t += 16u;
        return t + VQ_CODEBOOK;
    }
    if (s->flags & FLAG_PALETTE) {
        int pal4 = s->pixel_format == FORMAT_PAL4;
        t = w * h + (mip ? mip_tail(w, h, 1u) + (pal4 ? 43u : 11u) : 0u);
        return pal4 ? (t == 1u ? 1u : t >> 1) : t;
    }
    t = w * h + (mip ? mip_tail(w, h, 4u) : 0u);
    return t * (s->bit_depth == 1u ? 2u : 4u);
}

/* A 16-bit twiddled mip chain: loaded MIP16_LEAD bytes in. */
static int mip16(u32 flags) { return (flags & (FLAG_TWIDDLED | FLAG_MIPMAP | FLAG_VQ | FLAG_PALETTE)) == (FLAG_TWIDDLED | FLAG_MIPMAP); }

KMSTATUS kmCreateTextureSurface(KMSURFACEDESC *surface, s32 width, s32 height, u32 textureType)
{
    K1Surface *s = (K1Surface *)surface;
    u32 kind = textureType & 0xff00u, fmt = textureType & 0xffu, w = (u32)width, h = (u32)height, bytes, at;
    s32 status = K1_OK;
    s->surface_type = kind == 0x1000u || kind == 0x1100u ? SURFACE_SMALL_VQ : SURFACE_TEXTURE;
    s->bit_depth = 1u; /* 16-bit texels (Kamui gives palettized ones 3 under an ARGB8888 palette; nothing reads it) */
    if (kind >= 0x0500u && kind <= 0x0800u)
        s->pixel_format = kind <= 0x0600u ? FORMAT_PAL4 : FORMAT_PAL8;
    else if (fmt < FORMATS)
        s->pixel_format = fmt << FORMAT_SHIFT;
    else
        status = K1_INVALID;
    s->width = w;
    s->height = h;
    if (!edge_ok(w) || !edge_ok(h)) status = K1_INVALID;
    if (w != h && kind != 0x0900u && kind != 0x0b00u && kind != 0x0d00u && kind != 0x0500u && kind != 0x0700u)
        status = K1_INVALID; /* only rectangles, stride textures and plain palettized ones may be oblong */
    s->flags = 0;
    switch (kind) {
    case 0x0200u: case 0x0f00u: s->flags = FLAG_MIPMAP | FLAG_TWIDDLED; break;
    case 0x0100u: case 0x0d00u: case 0x0e00u: s->flags = FLAG_TWIDDLED; break;
    case 0x0300u: case 0x1000u: s->flags = FLAG_VQ | FLAG_TWIDDLED; break;
    case 0x0400u: case 0x1100u: s->flags = FLAG_VQ | FLAG_TWIDDLED | FLAG_MIPMAP; break;
    case 0x0500u: case 0x0700u: s->flags = FLAG_PALETTE | FLAG_TWIDDLED; break;
    case 0x0600u: case 0x0800u: s->flags = FLAG_PALETTE | FLAG_TWIDDLED | FLAG_MIPMAP; break;
    case 0x0b00u: s->flags = FLAG_STRIDE; break;
    default: break; /* 0x0900 rectangle */
    }
    s->size = surface_bytes(s);
    if (status != K1_OK) {
        pvr_todo("kamui1: kmCreateTextureSurface %ux%u type %04x refused", w, h, textureType);
        return status;
    }
    if (s->surface_type == SURFACE_SMALL_VQ) {
        u32 mip = (s->flags & FLAG_MIPMAP) != 0u;
        bytes = VQ_CODEBOOK + g_small_vq_index[mip][edge_code(w, h)] + (mip ? 10u : 0u);
    } else {
        bytes = s->size + (mip16(s->flags) ? MIP16_LEAD : 0u);
    }
    at = alloc(bytes);
    if (!at) {
        pvr_todo("kamui1: texture memory full (%u bytes asked)", bytes);
        return K1_NOT_ENOUGH_MEMORY;
    }
    s->surface = at;
    return K1_OK;
}

static void load(u32 at, const void *data, u32 size)
{
    if (at + size > PVRX_VRAM_SIZE) return;
    port_extra_undo(pvr_vram() + at, size, vram_undone);
    memmove(pvr_vram() + at, data, size);
    pvr_vram_written(at, size);
}

/* Kamui 1 refuses automatic mip-maps and dithering (both are the caller's work). */
KMSTATUS kmLoadTexture(KMSURFACEDESC *surface, const u32 *texture, s32 bAutoMipMap, s32 bDither)
{
    const K1Surface *s = (const K1Surface *)surface;
    const u8 *src = texture ? host((u32)(uintptr_t)texture) : NULL;
    if (bAutoMipMap == 1 || bDither == 1) return K1_INVALID;
    if (!src || !s->surface) return K1_ERROR;
    if (s->surface_type == SURFACE_SMALL_VQ) {
        u32 mip = (s->flags & FLAG_MIPMAP) != 0u, code = edge_code(s->width, s->width);
        u32 book = g_small_vq_book[mip][code];
        load(s->surface, src, book);
        load(s->surface + VQ_CODEBOOK, src + book, g_small_vq_index[mip][code] + (mip ? 10u : 0u));
    } else if (mip16(s->flags)) {
        load(s->surface + MIP16_LEAD, src, g_mip16_bytes[edge_code(s->width, s->width)] + MIP16_LEAD);
    } else {
        load(s->surface, src, s->size);
    }
    return K1_OK;
}

KMSTATUS kmFreeTexture(KMSURFACEDESC *surface)
{
    K1Surface *s = (K1Surface *)surface;
    if (s->surface) release(s->surface);
    s->surface = 0;
    return K1_OK;
}

KMSTATUS kmGetFreeTextureMem(u32 *sizeOfTexture, u32 *maxBlockSize)
{
    u32 total = 0, largest = 0;
    if (!g_block_count) init();
    for (u32 i = 0; i < g_block_count; i++) {
        if (g_blocks[i].used) continue;
        total += g_blocks[i].size;
        if (g_blocks[i].size > largest) largest = g_blocks[i].size;
    }
    if (sizeOfTexture) *sizeOfTexture = total;
    if (maxBlockSize) *maxBlockSize = largest;
    return K1_OK;
}

/* Kamui's deferred texture work, run once a frame; the kit loads at once, so there is none. */
void Km_RunTextureService(void) {}
