/* Kamui 2 (the Dreamcast PowerVR library, the version with the game-written vertex buffer: kmBeginPass hands out
 * list starts, the game writes TA parameters there itself) as the kit's sdk/kamui2_*.c see it. The structures are
 * the kit's own declarations of the documented layouts; the game's headers name these structures too, so the
 * module takes the game's pointers and reads them through these. Layouts as in the SDK's km2strct.h (Kamui 2
 * R10.1 and later). */
#ifndef KAMUI2_H
#define KAMUI2_H

#include "pvr.h"

#define K2_OK 0
#define K2_ERROR (-1)
#define K2_LISTS 5u

/* KMSURFACEDESC: 0x20 bytes */
typedef struct K2Surface {
    u32 surface_type; /* 1 frame buffer, 2 texture, 3 small VQ texture */
    u32 bit_depth;
    u32 pixel_format; /* textures: the TCW pixel format bits (27..29) */
    u32 width, height;
    u32 size;         /* bytes */
    u32 flags;        /* textures: the TCW's mip-map, VQ, scan-order and stride bits (0xc6000000) */
    u32 address;      /* textures: the byte offset in texture memory ("pSurface") */
} K2Surface;

/* KMPASSINFO: 0x38 bytes */
typedef struct K2PassInfo {
    u32 region_array_flag; /* 0: autosort translucency */
    u32 vertex_buffer, vertex_buffer_size, direct_transfer_list;
    f32 buffer_percent[K2_LISTS];
    u32 opb_size[K2_LISTS];
} K2PassInfo;

/* KMVERTEXBUFFDESC: 0x20 bytes */
typedef struct K2VertexBuffDesc {
    u32 *current; /* the current pass's list pointers: starts after kmBeginPass, the game's ends at kmEndPass */
    u32 global_param, list_state, buffers, first_pass, max_bank;
    K2PassInfo *pass;
    u32 reserved;
} K2VertexBuffDesc;

/* KMSYSTEMCONFIGSTRUCT */
typedef struct K2SysConfig {
    u32 size, flags;
    K2Surface **frame_buffers;
    u32 frame_buffer_count, strip_buffer_height;
    u32 texture_memory_size, texture_structs, small_vq_structs, texture_work;
    K2VertexBuffDesc *buffer_desc;
    u32 vertex_banks, vertex_buffer, vertex_buffer_size, pass_depth;
    K2PassInfo pass[PVRX_MAX_PASSES];
} K2SysConfig;

#define K2_CONFIG_FLAG_NOWAITVSYNC 0x20u /* kmEndScene: the frame flips without waiting */

/* kamui2_scene.c */
extern K2SysConfig *g_k2_config; /* from kmSetSystemConfiguration */
s32 kmSetSystemConfiguration(void *config); /* the documented call; a game's display set-up calls it */

/* kamui2_tex.c */
void k2_tex_init(const K2SysConfig *config);

#endif
