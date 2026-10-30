/* Platform layer: Kamui 1's vertex buffer, render, flip and frame buffer calls over the kit's PowerVR renderer (pvr/).
 *
 * Kamui 1 games write TA parameters through the store queues into Kamui's per-list buffers (kamui1.h). This module
 * keeps Kamui's bookkeeping in a head at the start of the game's vertex buffer (guest RAM, so the game's own code
 * can read and write it as it reads Kamui's): the per-list write pointer table, each list's start, the global
 * parameter word, the list state words and the bank table. Kamui's list table word (game.json "kamui1" "list_word")
 * points at the write pointer table, and the vertex buffer descriptor gets the same pointers Kamui gives it.
 *
 * With the configuration's buffer model on (flags 0x10, buffer_model 0x20 | list), Kamui sends that one list straight
 * to the TA as it is drawn, so the game gives it no share of the buffer (e.g. the opaque list: every 3D model). The
 * port draws at kmRender, so that list is buffered too: it gets the room of the largest other list, all scaled to fit.
 * The lists share the buffer by the configuration's percentages (kmSetSystemConfiguration), or by the byte sizes
 * kmCreateVertexBuffer asks for. The port renders at once, so one bank does: kmRender hands every list's
 * [start, write pointer) to pvr_render and rewinds the pointers; kmFlipFrameBuffer shows the frame at the vertical
 * blank the vsync count asks for (kmSetWaitVsyncCount: 1 is 60 Hz, 2 is 30 Hz).
 *
 * Needs from the game's headers: the function names below as the game's registry has them. */
#include "game.h"
#include "platform.h"
#include "port.h"
#include "kamui1.h"
#include "lockstep.h"

#define HEAD_TABLE 0u   /* head words: the write pointer of each list */
#define HEAD_START 5u   /* each list's start */
#define HEAD_STATE 11u  /* four list state words */
#define HEAD_BANKS 15u  /* the bank table: one bank, pointing at the starts */
#define HEAD_GLOBAL 16u /* the global parameter buffer: 16 words, 32-byte aligned (kamui1_state.c fills it) */
#define HEAD_WORDS 32u
#define HEAD_BYTES (HEAD_WORDS * 4u)
#define LIST_ALIGN 32u
#define P2_BASE 0xa0000000u /* the lists' pointers are uncached (P2) addresses, as Kamui's are */
#define AREA_MASK 0x1fffffffu
#define PERCENT 100.0
#define SURFACE_FRAME_BUFFER 0u
#define FRAME_WIDTH 640u
#define FRAME_HEIGHT 480u
#define FRAME_DEPTH 16u
#define USER_CLIP_PCW 0x20000000u /* the user tile clip parameter (parameter type 1) */
#define CLIP_WORDS 8u

static K1SysConfig *g_config;
void (*k1_render_done)(void);
static u32 *g_head;                         /* the head words (host view of the guest buffer) */
static u32 g_buffer, g_buffer_size;         /* the vertex buffer (P2) and its bytes */
static u32 g_wait_vsync = 1u, g_last_flip;
#ifdef PORT_KAMUI1_LIST_WORD
static u32 g_list_word = PORT_KAMUI1_LIST_WORD; /* the guest address of Kamui's list table word */
#else
static u32 g_list_word; /* not given: the game cannot find its lists */
#endif

/* A guest pointer the game may pass as a P0 (0x0c..) mirror, as a host pointer. */
static void *host(u32 addr) { return (void *)(uintptr_t)guest_fold(addr); }

u32 k1_list_start(u32 list) { return g_head && list < K1_LISTS ? g_head[HEAD_START + list] : 0u; }
const K1SysConfig *k1_config(void) { return g_config; }

u32 k1_texture_bytes(const K1SysConfig *c) { return ((c->texture_memory_size + 7u) & ~7u) * 4u; }
u32 *k1_global_param(void) { return g_head ? &g_head[HEAD_GLOBAL] : NULL; }
u32 *k1_list_state(void) { return g_head ? &g_head[HEAD_STATE] : NULL; }

/* Lays the head and the lists out in the vertex buffer; bytes[i] are the lists' wishes (scaled down if they do not
 * fit), and fills desc as Kamui does. */
static s32 make_lists(K1VertexBuffDesc *desc, const u32 bytes[K1_LISTS])
{
    u32 room, total = 0, at;
    if (!g_buffer || g_buffer_size <= HEAD_BYTES) return K1_ERROR;
    room = g_buffer_size - HEAD_BYTES;
    for (u32 i = 0; i < K1_LISTS; i++) total += bytes[i];
    g_head = host(g_buffer);
    memset(g_head, 0, HEAD_BYTES);
    at = (g_buffer & AREA_MASK) | P2_BASE;
    at += HEAD_BYTES;
    for (u32 i = 0; i < K1_LISTS; i++) {
        u32 n = total > room ? (u32)((double)bytes[i] * room / total) : bytes[i];
        g_head[HEAD_START + i] = g_head[HEAD_TABLE + i] = at;
        at += n & ~(LIST_ALIGN - 1u);
    }
    g_head[HEAD_BANKS] = (u32)(uintptr_t)&g_head[HEAD_START];
    if (g_list_word)
        *(u32 *)host(g_list_word) = (u32)(uintptr_t)&g_head[HEAD_TABLE];
    else
        port_log("kamui1: no list table word (game.json kamui1.list_word): the game cannot find its lists\n");
    if (desc) {
        desc->current = &g_head[HEAD_TABLE];
        desc->global_param = &g_head[HEAD_GLOBAL];
        desc->list_state = &g_head[HEAD_STATE];
        desc->buffers = (u32 **)&g_head[HEAD_BANKS];
    }
    port_log("kamui1: vertex buffer %08x, %u bytes; lists %u %u %u %u %u bytes%s\n", g_buffer, g_buffer_size,
             bytes[0], bytes[1], bytes[2], bytes[3], bytes[4], total > room ? " (scaled to fit)" : "");
    return K1_OK;
}

/* The buffer model's direct list (header comment): no share of its own, so the largest other list's room. */
static void direct_list_room(const K1SysConfig *c, u32 bytes[K1_LISTS])
{
    u32 list = c->buffer_model & ~K1_BUFFER_DIRECT, most = 0;
    if (!(c->flags & K1_CONFIG_BUFFER_MODEL) || !(c->buffer_model & K1_BUFFER_DIRECT) || list >= K1_LISTS) return;
    if (bytes[list]) return;
    for (u32 i = 0; i < K1_LISTS; i++)
        if (bytes[i] > most) most = bytes[i];
    bytes[list] = most;
    port_log("kamui1: buffer model %x: list %u goes straight to the TA on the console; buffered here (%u bytes)\n",
             c->buffer_model, list, most);
}

/* The configuration's vertex buffer, shared by its list percentages. */
s32 kmSetSystemConfiguration(void *config)
{
    K1SysConfig *c = config;
    u32 bytes[K1_LISTS];
    if (!c || !c->vertex_buffer || !c->vertex_buffer_size) return K1_ERROR;
    g_config = c;
    g_buffer = c->vertex_buffer;
    g_buffer_size = c->vertex_buffer_size;
    for (u32 i = 0; i < K1_LISTS; i++)
        bytes[i] = (u32)((double)(g_buffer_size - HEAD_BYTES) * c->list_share[i] / PERCENT);
    direct_list_room(c, bytes);
    port_log("kamui1: system configuration, %u frame buffer(s), texture memory %u KB\n", c->frame_buffer_count,
             k1_texture_bytes(c) >> 10);
    return make_lists(c->buffer_desc ? host(c->buffer_desc) : NULL, bytes);
}

/* The lists by byte sizes (no punch-through list), in the configured vertex buffer. */
KMSTATUS kmCreateVertexBuffer(KMVERTEXBUFFDESC *bufferDesc, s32 opaquePolygonSize, s32 opaqueModifierSize,
                              s32 transPolygonSize, s32 transModifierSize)
{
    u32 bytes[K1_LISTS] = {(u32)opaquePolygonSize, (u32)opaqueModifierSize, (u32)transPolygonSize,
                           (u32)transModifierSize, 0u};
    return make_lists((K1VertexBuffDesc *)bufferDesc, bytes);
}

/* Kamui frees the vertex buffer here; the lists are gone until the next set-up. */
KMSTATUS Km_ResetVertexBuffers(void)
{
    g_head = NULL;
    return K1_OK;
}

KMSTATUS Km_SetBufferModel(u32 model, u32 flags)
{
    port_log("kamui1: buffer model %u, flags %x\n", model, flags);
    return K1_OK;
}

/* A user tile clip parameter at the list's write pointer. Kamui 1 takes the rectangle in tiles (32 pixels), the
 * maxima inclusive, as the TA's words hold it: a game's view set-up passes x / 32 .. (x + w) / 32 - 1,
 * so 0, 0, 19, 14 for the whole screen. (Dividing by 32 again made that the top-left tile alone: every 3D polygon,
 * drawn with clipping "inside", was cut away.) */
KMSTATUS kmSetUserClipping(KMVERTEXBUFFDESC *bufferDesc, s32 listType, s32 xMin, s32 yMin, s32 xMax, s32 yMax)
{
    u32 *at;
    (void)bufferDesc;
    if (!g_head || (u32)listType >= K1_LISTS) return K1_ERROR;
    at = host(g_head[HEAD_TABLE + (u32)listType]);
    memset(at, 0, CLIP_WORDS * 4u);
    at[0] = USER_CLIP_PCW;
    at[4] = (u32)xMin;
    at[5] = (u32)yMin;
    at[6] = (u32)xMax;
    at[7] = (u32)yMax;
    g_head[HEAD_TABLE + (u32)listType] += CLIP_WORDS * 4u;
    return K1_OK;
}

/* Draws every list from its start to its write pointer, then rewinds the pointers. */
KMSTATUS kmRender(void)
{
    PvrPass pass;
    if (!g_head) return K1_ERROR;
    memset(&pass, 0, sizeof pass);
    for (u32 i = 0; i < K1_LISTS; i++) {
        u32 start = guest_fold(g_head[HEAD_START + i]), end = guest_fold(g_head[HEAD_TABLE + i]);
        pass.start[i] = start;
        pass.end[i] = end > start ? end : start;
        g_head[HEAD_TABLE + i] = g_head[HEAD_START + i];
    }
    pass.autosort = 1;
    pass.z_clear = 1;
    hw_check_scene(&pass, 1u);
    pvr_render(&pass, 1u);
    hw_check_rendered(&pass, 1u);
    if (k1_render_done) k1_render_done();
    return K1_OK;
}

/* Shows the frame once the vsync count of vertical blanks has passed since the last flip. A game loop that waits for
 * the vertical blank itself and then renders and flips has already waited that blank, so a count of 1 flips at once
 * and the loop runs at 60 Hz, as on the console (a forced extra blank halved it and dropped one-blank pad edges); a
 * second flip in the same blank still waits for the next one. */
KMSTATUS kmFlipFrameBuffer(void)
{
    u32 target = g_last_flip + g_wait_vsync;
    while ((s32)(port_vblank_count() - target) < 0)
        port_wait_vblank();
    g_last_flip = port_vblank_count();
    gfx_present();
    return K1_OK;
}

KMSTATUS kmSetWaitVsyncCount(s32 count)
{
    g_wait_vsync = count > 0 ? (u32)count : 1u;
    return K1_OK;
}

/* The display is the kit's window: a frame buffer surface only records its size. */
KMSTATUS kmCreateFrameBufferSurface(KMSURFACEDESC *surface, s32 width, s32 height, s32 bStripBuffer,
                                    s32 bBufferClear)
{
    K1Surface *s = (K1Surface *)surface;
    (void)bStripBuffer;
    (void)bBufferClear;
    if (!s) return K1_ERROR;
    s->surface_type = SURFACE_FRAME_BUFFER;
    s->bit_depth = FRAME_DEPTH;
    s->width = width > 0 ? (u32)width : FRAME_WIDTH;
    s->height = height > 0 ? (u32)height : FRAME_HEIGHT;
    s->size = s->width * s->height * (FRAME_DEPTH / 8u);
    return K1_OK;
}

KMSTATUS kmActivateFrameBuffer(KMSURFACEDESC *primary, KMSURFACEDESC *backBuffer, s32 bStripBuffer, s32 bWaitVSync)
{
    (void)primary;
    (void)backBuffer;
    (void)bStripBuffer;
    (void)bWaitVSync;
    return K1_OK;
}
