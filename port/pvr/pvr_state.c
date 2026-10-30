/* The render state the SDK sets between scenes (fog, background plane, global clip), the texture memory, and
 * the to-do log. The renderer (pvr_draw.c) reads g_pvr. */
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include "pvr_int.h"
#include "platform.h"
#include "lockstep.h"

PvrState g_pvr = {
    .fog_color = 0xff000000u,
    .bg_argb = {0xff000000u, 0xff000000u, 0xff000000u},
    .clip = {0, 0, 639, 479},
    .shadow = -1,
};
static u8 g_vram[PVRX_VRAM_SIZE];
u32 g_pvr_debug;

u8 *pvr_vram(void) { return g_vram; }

void pvr_vram_written(u32 offset, u32 size)
{
    hw_check_vram(offset, size);
    if (offset < PVRX_VRAM_SIZE)
        pvr_tex_invalidate(offset, size > PVRX_VRAM_SIZE - offset ? PVRX_VRAM_SIZE - offset : size);
}

/* Texture memory as guest code writes it through the 64-bit path's window (0x04000000..0x047fffff in any area: a
 * game library that copies texture data itself rather than through the SDK): the host bytes of [addr, addr + size)
 * in that window, NULL for any other range. The bytes are kept for an in-between frame's undo, as the SDK layer
 * keeps them; the writer then calls port_texture_written. */
#define TEX_WINDOW 0x04000000u
#define AREA_MASK 0x1fffffffu

static void vram_undone(void *p, u32 size) { pvr_vram_written((u32)((u8 *)p - g_vram), size); }

u8 *port_texture_window(u32 addr, u32 size)
{
    u32 a = (addr & AREA_MASK) - TEX_WINDOW;
    if ((addr & AREA_MASK) < TEX_WINDOW || a >= PVRX_VRAM_SIZE || size > PVRX_VRAM_SIZE - a) return NULL;
    port_extra_undo(g_vram + a, size, vram_undone);
    return g_vram + a;
}

void port_texture_written(u32 addr, u32 size) { pvr_vram_written((addr & AREA_MASK) - TEX_WINDOW, size); }

/* --dump-vram SCENE (ta_parse.c): the 8 MB of texture memory as the decoder reads it (the linear view), to
 * ta_vram_SCENE.bin beside --ta-dump's ta_scene_SCENE.txt, for decoding a scene's textures by hand
 * (tests/test_pvr_pack.py's driver decodes a texture word from it with the renderer's own decoder). */
void pvr_vram_dump(u32 scene)
{
    char name[64];
    FILE *f;
    snprintf(name, sizeof name, "ta_vram_%u.bin", scene);
    if (fopen_s(&f, name, "wb") || !f) return;
    fwrite(g_vram, 1, PVRX_VRAM_SIZE, f);
    fclose(f);
    port_log("pvr: texture memory of scene %u (vblank %u) written to %s\n", scene, port_vblank_count(), name);
}

/* An in-between frame's settings are put back after it (runtime/frame_extra.c); the fog table is uploaded again. */
static void state_undone(void *p, u32 size)
{
    (void)p;
    (void)size;
    g_pvr.fog_dirty = 1;
}

static void will_set(void) { port_extra_undo(&g_pvr, sizeof g_pvr, state_undone); }

void pvr_set_fog_table(const u16 entries[128])
{
    will_set();
    memcpy(g_pvr.fog_table, entries, sizeof g_pvr.fog_table);
    g_pvr.fog_dirty = 1;
}

void pvr_set_fog_color(u32 argb)
{
    will_set();
    g_pvr.fog_color = argb;
}

void pvr_set_fog_density(u32 reg)
{
    will_set();
    g_pvr.fog_density = reg;
}

void pvr_set_background(const f32 xyz[3][3], const u32 argb[3])
{
    will_set();
    memcpy(g_pvr.bg_xyz, xyz, sizeof g_pvr.bg_xyz);
    memcpy(g_pvr.bg_argb, argb, sizeof g_pvr.bg_argb);
}

void pvr_set_clip(u32 x0, u32 y0, u32 x1, u32 y1)
{
    will_set();
    g_pvr.clip[0] = x0;
    g_pvr.clip[1] = y0;
    g_pvr.clip[2] = x1;
    g_pvr.clip[3] = y1;
}

void pvr_set_stride(u32 width)
{
    will_set();
    g_pvr.stride = width;
}

void pvr_set_border(u32 argb) { gfx_set_border(argb); }

void pvr_set_cheap_shadow(s32 intensity)
{
    will_set();
    g_pvr.shadow = intensity < 0 ? -1 : intensity > 255 ? 255 : intensity;
}

/* Each distinct text once per run, so a long run's log stays a list of classes and to-dos. */
#define ONCE_MAX 2048u
static void log_once(const char *prefix, const char *fmt, va_list ap)
{
    static u32 seen[ONCE_MAX], count;
    char text[256];
    u32 h = 2166136261u;
    vsnprintf(text, sizeof text, fmt, ap);
    for (const char *p = text; *p; p++) h = (h ^ (u8)*p) * 16777619u;
    for (u32 i = 0; i < count; i++)
        if (seen[i] == h) return;
    if (count == ONCE_MAX) return;
    seen[count++] = h;
    port_log("%s%s (frame %u)\n", prefix, text, port_vblank_count());
    if (count == ONCE_MAX) port_log("pvr: %u classes logged, no more are\n", ONCE_MAX);
}

void pvr_todo(const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    log_once("TODO ", fmt, ap);
    va_end(ap);
}

void pvr_seen(const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    log_once("ta: ", fmt, ap);
    va_end(ap);
}
