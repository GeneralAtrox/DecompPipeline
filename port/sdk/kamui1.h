/* Kamui 1 (the Dreamcast PowerVR library's first API, 1.x: the layouts below hold for 1.11 and later) as the kit's
 * sdk/kamui1_*.c see it.
 *
 * Kamui 1 games write TA parameters through the store queues straight into Kamui's per-list RAM buffers: they read
 * the per-list write pointer table through Kamui's list table word (a u32 ** at a fixed guest address inside the
 * library's state), write at table[list type] and store the advanced pointer back. kmRender then hands each list's
 * [start, write pointer) to the TA. The word's address differs by game and library build: game.json "kamui1"
 * "list_word" gives it (port_config.h PORT_KAMUI1_LIST_WORD). The structures are the kit's own declarations of the
 * documented layouts (the Kamui 1.34 reference, section 5); the game's headers name these structures too, so the
 * modules take the game's pointers and read them through these. */
#ifndef KAMUI1_H
#define KAMUI1_H

#include "pvr.h"

#define K1_OK 0
#define K1_ERROR (-1)
#define K1_LISTS 5u /* opaque, opaque modifier, translucent, translucent modifier, punch-through */

/* KMSURFACEDESC: 0x28 bytes */
typedef struct K1Surface {
    u32 surface_type; /* 0 frame buffer, 1 texture */
    u32 bit_depth;
    u32 pixel_format;
    u32 width, height;
    u32 size;  /* bytes */
    u32 flags; /* textures: bit 0 mip-mapped, 2 twiddled, 3 VQ, 4 stride, 5 palettized */
    u32 surface, virtual_addr, physical_addr;
} K1Surface;

/* KMVERTEXBUFFDESC: 0x20 bytes. Kamui fills it; the game reads the pointers. */
typedef struct K1VertexBuffDesc {
    u32 *current;     /* the write pointer of each list (the table the list table word points at) */
    u32 *global_param; /* the global parameter buffer, 32 or 64 bytes (the game ORs the user clip mode into word 0) */
    u32 *list_state;  /* list type, global parameter size in bytes, bank */
    u32 **buffers;    /* per bank, the start of each list */
    u32 reserved[4];
} K1VertexBuffDesc;

/* KMVERTEXCONTEXT, the Kamui 1 layout: 0xc0 bytes (the last three words only matter with their render state bits).
 * render_state says which fields kmProcessVertexRenderState folds into the four hardware words. */
typedef struct K1VertexContext {
    u32 render_state;
    u32 param_type, list_type, color_type, uv_format;
    u32 depth_mode, culling_mode, screen_coordination, shading_mode, select_modifier;
    u32 z_write_disable, src_blend, dst_blend, src_select, dst_select;
    u32 fog_mode, use_specular, use_alpha, ignore_texture_alpha;
    u32 clamp_uv, flip_uv; /* 0x4c, 0x50 */
    u32 filter_mode, super_sample, mipmap_adjust, texture_shading, color_clamp, palette_bank;
    u32 texture;           /* 0x6c: K1Surface * */
    f32 face_color[4];     /* 0x70: alpha, red, green, blue */
    f32 offset_color[4];   /* 0x80 */
    u32 pcw, isp, tsp, tcw; /* 0x90: the hardware words */
    u32 modifier_isp;      /* 0xa0: a modifier volume's instruction word */
    u32 reserved_a4[4];
    u32 dcalc_exact, strip_length, user_clip; /* 0xb4 */
} K1VertexContext;

/* KMSYSTEMCONFIGSTRUCT, the Kamui 1 layout: 0x60 bytes. Pointers may be P0 (0x0c..) mirrors. */
typedef struct K1SysConfig {
    u32 size, flags;
    u32 frame_buffers; /* K1Surface ** */
    u32 frame_buffer_count;
    u32 unknown_10, unknown_14, unknown_18;
    u32 texture_memory_size; /* in 32-bit words: Kamui 1 rounds it up to 8 words, 4 bytes each */
    u32 buffer_desc;   /* K1VertexBuffDesc * */
    u32 vertex_buffer; /* the vertex buffer (often a P2 address) */
    u32 vertex_buffer_size;
    f32 list_share[K1_LISTS]; /* percent of the vertex buffer per list */
    u32 unknown_40[7];
    u32 buffer_model; /* with flags K1_CONFIG_BUFFER_MODEL: K1_BUFFER_DIRECT | the list Kamui sends straight to the TA */
} K1SysConfig;

#define K1_CONFIG_BUFFER_MODEL 0x10u /* K1SysConfig.flags: buffer_model is on */
#define K1_BUFFER_DIRECT 0x20u       /* buffer_model: one list (the low bits: 0, 2 or 4) is not buffered */

/* kamui1_scene.c */
s32 kmSetSystemConfiguration(void *config); /* the documented call; a game's display set-up shim calls it */
u32 k1_list_start(u32 list);                /* a list's start (P2), 0 before the vertex buffer exists */
const K1SysConfig *k1_config(void);         /* the system configuration, NULL before kmSetSystemConfiguration */
u32 k1_texture_bytes(const K1SysConfig *c); /* the configuration's texture memory in bytes */
u32 *k1_global_param(void);                 /* the global parameter buffer (16 words), NULL before the buffer exists */
u32 *k1_list_state(void);                   /* the list state words: list type, global parameter bytes */
extern void (*k1_render_done)(void);        /* called after kmRender draws (kamui1_misc.c's end of render) */

#endif
