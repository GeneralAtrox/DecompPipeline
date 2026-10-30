/* Platform layer: Kamui 1's callback, scanline, fog, palette, border, background, clipping and register calls.
 *
 * Kamui 1 keeps its callbacks in numbered slots (Kamui's own numbering: 0 end of render, 1 vertical sync, 2
 * horizontal sync, 3 texture overflow, 4 strip overrun, 5 end of vertex, 6 end of YUV, 7 wait vsync, 8 fatal error).
 * The library's registration functions carry no SDK name in the game's registry (Km_SetCallbackN, N the slot).
 * The kit fires the two a game can wait on: the end of render after kmRender draws (k1_render_done) and the
 * vertical sync at each vertical blank the kit waits for. The others need hardware the port has none of (an
 * overflowing texture memory, an overrunning strip, the horizontal sync), so they never fire.
 *
 * Fog, the border, the background plane, the pixel clip and the palette (pvr/pvr_pal.c, which Kamui 2 feeds the same
 * way) go to pvr/ in the hardware's own terms. The colour clamp, the culling value and the fog vertex colour have no pvr/ counterpart yet: they are kept, and a call
 * that sets them to something the picture would show goes to the to-do log (pvr_todo, each text once per run).
 *
 * Needs from the game's headers: KmCallbackFunc (void (*)(void *)), KMPACKEDARGB (u32), KMVERTEXCONTEXT and the
 * function names below as the game's registry has them. */
#include "game.h"
#include "platform.h"
#include "port.h"
#include "kamui1.h"

#define SLOT_EOR 0u
#define SLOT_VSYNC 1u
#define SLOTS 10u
#define FOG_ENTRIES 128u
#define VTX_TYPE_PACKED 0 /* the background plane's vertex type 0: x, y, 1/w at 4..12, packed ARGB at 0x18 */
#define VTX_XYZ 4u
#define VTX_ARGB 0x18u
#define FRAME_LINES 480u
#define US_PER_FRAME (1000000u / PORT_VBLANK_HZ)
#define PALETTE_ENTRIES 1024u

static struct {
    KmCallbackFunc fn;
    void *arg;
} g_cb[SLOTS];
static int g_vsync_task;
static s32 g_hsync_line;
static u32 g_clamp_max = 0xffffffffu, g_clamp_min;
static f32 g_cull_value;
static u32 g_enable_value;
static s32 g_param_pair[2];
static KmCallbackFunc g_irq_fn;
static void *g_irq_arg;

static void fire(u32 slot)
{
    if (g_cb[slot].fn) g_cb[slot].fn(g_cb[slot].arg);
}

static void vsync_task(void *arg)
{
    (void)arg;
    fire(SLOT_VSYNC);
}

/* kamui1_scene.c's kmRender, after the lists are drawn: the frame is rendered at once. */
static void end_of_render(void) { fire(SLOT_EOR); }

static KMSTATUS set_callback(u32 slot, KmCallbackFunc func, void *arg)
{
    g_cb[slot].fn = func;
    g_cb[slot].arg = arg;
    if (slot == SLOT_EOR) k1_render_done = func ? end_of_render : NULL;
    if (slot == SLOT_VSYNC && func && !g_vsync_task) {
        port_add_vblank_task(vsync_task, NULL);
        g_vsync_task = 1;
    }
    return K1_OK;
}

KMSTATUS Km_SetCallback0(KmCallbackFunc func, void *arg) { return set_callback(0u, func, arg); }
KMSTATUS Km_SetCallback1(KmCallbackFunc func, void *arg) { return set_callback(1u, func, arg); }
KMSTATUS Km_SetCallback2(KmCallbackFunc func, void *arg) { return set_callback(2u, func, arg); }
KMSTATUS Km_SetCallback3(KmCallbackFunc func, void *arg) { return set_callback(3u, func, arg); }
KMSTATUS Km_SetCallback4(KmCallbackFunc func, void *arg) { return set_callback(4u, func, arg); }
KMSTATUS Km_SetCallback5(KmCallbackFunc func, void *arg) { return set_callback(5u, func, arg); }
/* The fatal error callback gets Kamui's own error word, not a user argument. */
KMSTATUS Km_SetCallback8(KmCallbackFunc func) { return set_callback(8u, func, NULL); }

/* A system interrupt callback the game registers beside the slots; nothing the port runs raises it. */
KMSTATUS Km_SetInterruptCallback(KmCallbackFunc func, void *arg)
{
    g_irq_fn = func;
    g_irq_arg = arg;
    return K1_OK;
}

void kmSetHSyncLine(s32 line) { g_hsync_line = line; }

/* The line the beam would be on: the time since the last vertical blank, as a share of the frame's lines. */
KMSTATUS kmGetCurrentScanline(s32 *scanline)
{
    u32 us = (u32)(host_ticks(1000000u) % US_PER_FRAME);
    if (scanline) *scanline = (s32)(us * FRAME_LINES / US_PER_FRAME);
    return K1_OK;
}

/* The kit's window filters as the host does. */
KMSTATUS kmChangeDisplayFilterMode(s32 bDither, s32 bAntiAlias)
{
    (void)bDither;
    (void)bAntiAlias;
    return K1_OK;
}

/* FPU_CULL_VAL: polygons smaller than this are dropped; the renderer draws them, which shows nothing more. */
KMSTATUS kmSetCullingRegister(f32 cullValue)
{
    g_cull_value = cullValue;
    return K1_OK;
}

KMSTATUS kmSetColorClampValue(KMPACKEDARGB maxValue, KMPACKEDARGB minValue)
{
    if ((maxValue != 0xffffffffu || minValue != 0u) && (maxValue != g_clamp_max || minValue != g_clamp_min))
        pvr_todo("kamui1: colour clamp %08x..%08x", minValue, maxValue);
    g_clamp_max = maxValue;
    g_clamp_min = minValue;
    return K1_OK;
}

KMSTATUS kmSetFogTableColor(KMPACKEDARGB color)
{
    pvr_set_fog_color(color);
    return K1_OK;
}

KMSTATUS kmSetFogVertexColor(KMPACKEDARGB color)
{
    pvr_todo("kamui1: fog vertex colour %08x", color);
    return K1_OK;
}

/* The fog table as the FOG_TABLE registers hold it: per entry this density and the next one, 8 bits each. */
KMSTATUS kmSetFogTable(const f32 *table)
{
    u16 regs[FOG_ENTRIES];
    u32 next = (u32)(s32)(table[FOG_ENTRIES - 1u] * 255.0f) & 0xffu;
    for (u32 i = FOG_ENTRIES; i-- > 0;) {
        u32 cur = (u32)(s32)(table[i] * 255.0f) & 0xffu;
        regs[i] = (u16)(cur << 8 | next);
        next = cur;
    }
    pvr_set_fog_table(regs);
    return K1_OK;
}

KMSTATUS kmSetFogDensity(u32 density)
{
    pvr_set_fog_density(density);
    return K1_OK;
}

/* PAL_RAM_CTRL: the palette entries' format (0 ARGB1555, 1 RGB565, 2 ARGB4444, 3 ARGB8888). */
KMSTATUS kmSetPaletteMode(s32 mode)
{
    pvr_set_palette_format((u32)mode);
    return K1_OK;
}

/* PAL_RAM: the 1024 entries. */
KMSTATUS kmSetPaletteData(const u32 *palette)
{
    if (!palette) return K1_ERROR;
    pvr_set_palette(0, palette, PALETTE_ENTRIES);
    return K1_OK;
}

KMSTATUS kmSetBorderColor(KMPACKEDARGB color)
{
    pvr_set_border(color);
    return K1_OK;
}

/* The background plane's ISP/TSP words; the kit draws the plane from its vertices' colours. */
KMSTATUS kmSetBackGroundRenderState(KMVERTEXCONTEXT *context)
{
    (void)context;
    return K1_OK;
}

/* The background plane: three vertices of the given type (vertices = three vertex pointers). */
KMSTATUS kmSetBackGroundPlane(void **vertices, s32 vertexType, s32 structSize)
{
    f32 xyz[3][3];
    u32 argb[3];
    (void)structSize;
    if (!vertices) return K1_ERROR;
    if (vertexType != VTX_TYPE_PACKED) {
        pvr_todo("kamui1: background plane of vertex type %d", vertexType);
        return K1_OK;
    }
    for (u32 i = 0; i < 3u; i++) {
        const u8 *v = (const u8 *)(uintptr_t)guest_fold((u32)(uintptr_t)vertices[i]);
        memmove(xyz[i], v + VTX_XYZ, sizeof xyz[i]);
        memmove(&argb[i], v + VTX_ARGB, sizeof argb[i]);
    }
    pvr_set_background(xyz, argb);
    return K1_OK;
}

KMSTATUS kmSetPixelClipping(s32 xMin, s32 yMin, s32 xMax, s32 yMax)
{
    pvr_set_clip((u32)xMin, (u32)yMin, (u32)xMax, (u32)yMax);
    return K1_OK;
}

/* Two library state setters with no documented name: kept, nothing the port draws reads them. */
KMSTATUS Km_SetEnableValue(u32 value)
{
    g_enable_value = value;
    return K1_OK;
}

KMSTATUS Km_SetParamPair(s32 value1, s32 value2)
{
    g_param_pair[0] = value1;
    g_param_pair[1] = value2;
    return K1_OK;
}
