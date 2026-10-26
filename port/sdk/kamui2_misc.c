/* Platform layer: Kamui 2's device, display, callback, fog and background calls.
 *
 * The display is the kit's window, so the mode only matters for its callbacks: kmSetDisplayMode calls the
 * line-mode callback (slot 11) as Kamui does, and the vertical blank callback (slot 8) runs at every vertical blank
 * the kit waits for. Fog and the background plane go to pvr/ in the hardware's own terms.
 *
 * Needs from the game's headers: KmCallbackFunc (void (*)(void *)), KmVersionInfo (12 bytes) and the function
 * names below as the game's registry has them (the Km_* ones are registry names for library functions with no SDK
 * name). */
#include "game.h"
#include "platform.h"
#include "kamui2.h"

#define SLOT_VBLANK 8
#define SLOT_LINES 11
#define FOG_ENTRIES 128u
#define VTX_TYPE_PACKED 0 /* the background plane's vertex type: x, y, 1/w at 4..12, packed ARGB at 0x18 */
#define LINES_NTSC 480u
#define LINES_PAL_BASE 496u /* PAL line mode 0; each mode up to 4 adds 16 lines */
#define LINES_PAL_STEP 16u
#define LINE_MODES 5u

static KmCallbackFunc g_lines_fn;
static u32 g_frame_lines = LINES_NTSC;

u32 k2_frame_lines(void) { return g_frame_lines; }

s32 Km_InitDevice(u32 flags)
{
    (void)flags;
    return K2_OK;
}

/* The display mode's line-count callback: it gets {0, line mode} and may choose the mode. */
s32 Km_SetCallback11(KmCallbackFunc func)
{
    g_lines_fn = func;
    return K2_OK;
}

s32 kmSetDisplayMode(u32 mode, u32 bpp, u32 dither, u32 antiAlias)
{
    static u32 lines_arg[2];
    port_log("kamui2: display mode %02x, pixel format %u, dither %u, anti-alias %u\n", mode, bpp, dither != 0,
             antiAlias != 0);
    if (g_lines_fn) {
        lines_arg[0] = 0;
        lines_arg[1] = 0;
        g_lines_fn(lines_arg);
        if (lines_arg[1] < LINE_MODES) g_frame_lines = LINES_PAL_BASE + LINES_PAL_STEP * lines_arg[1];
        port_log("kamui2: line mode %u: a frame buffer of %u lines\n", lines_arg[1], g_frame_lines);
    }
    return K2_OK;
}

s32 Km_SetCallback(s32 slot, KmCallbackFunc func, void *arg)
{
    if (slot == SLOT_VBLANK)
        port_set_vblank_hook((PortVblankFn)func, arg);
    else if (func)
        pvr_todo("kamui2: Km_SetCallback slot %d", slot);
    return K2_OK;
}

s32 kmSetBorderColor(u32 argb)
{
    pvr_set_border(argb);
    return K2_OK;
}

s32 Km_AdjustDisplayPosition(s32 x, s32 y)
{
    (void)x;
    (void)y;
    return K2_OK;
}

/* The version block: three words (size 0x10, the Kamui and library versions). 0 is success; without a block the
 * library returns 0xeeee. */
#define VERSION_WORDS 3u
#define VERSION_NO_BLOCK 0xeeeeu
u32 Km_GetVersionInfo(KmVersionInfo *info)
{
    static const u32 words[VERSION_WORDS] = {0x10u, 0x03000000u, 0x08000000u};
    if (!info) return VERSION_NO_BLOCK;
    memmove(info, words, sizeof words);
    return K2_OK;
}

s32 Km_StubNotImplemented(u32 flag, void *out)
{
    (void)flag;
    (void)out;
    return K2_OK;
}

/* The fog table as the FOG_TABLE registers hold it: per entry this density and the next one, 8 bits each. */
s32 kmSetFogTable(const f32 *table)
{
    u16 regs[FOG_ENTRIES];
    u32 next = (u32)(s32)(table[FOG_ENTRIES - 1u] * 255.0f) & 0xffu;
    for (u32 i = FOG_ENTRIES; i-- > 0;) {
        u32 cur = (u32)(s32)(table[i] * 255.0f) & 0xffu;
        regs[i] = (u16)(cur << 8 | next);
        next = cur;
    }
    pvr_set_fog_table(regs);
    return K2_OK;
}

/* The colour is the fifth argument (the first four words are unused). */
s32 kmSetFogTableColor(u32 unusedR4, u32 unusedR5, u32 unusedR6, u32 unusedR7, u32 argb)
{
    (void)unusedR4;
    (void)unusedR5;
    (void)unusedR6;
    (void)unusedR7;
    pvr_set_fog_color(argb);
    return K2_OK;
}

s32 kmSetFogDensity(u32 density)
{
    pvr_set_fog_density(density);
    return K2_OK;
}

/* Modifier volumes scale the base and offset colour of shadow polygons inside them by intensity / 256 (clamped to
 * 255; FPU_SHAD_SCALE); a negative intensity selects two-parameter polygons instead. */
s32 kmSetCheapShadowMode(s32 intensity)
{
    pvr_set_cheap_shadow(intensity);
    return K2_OK;
}

/* Strip contexts make the ISP/TSP words of a strip head; the background plane is all the kit draws from them so
 * far, and it needs only the vertices. */
u32 Km_StripContextPrepare(void *context)
{
    (void)context;
    return K2_OK;
}

u32 Km_StripContextCommit(void *context)
{
    (void)context;
    return K2_OK;
}

/* The background plane: three vertices of the given type (plane = three vertex pointers). */
u32 Km_SetBackgroundPlane(void *plane, s32 flags)
{
    const u8 *const *v = plane;
    f32 xyz[3][3];
    u32 argb[3];
    if (flags != VTX_TYPE_PACKED) {
        pvr_todo("kamui2: background plane of vertex type %d", flags);
        return K2_OK;
    }
    for (u32 i = 0; i < 3u; i++) {
        memmove(xyz[i], v[i] + 4, sizeof xyz[i]);
        memmove(&argb[i], v[i] + 0x18, sizeof argb[i]);
    }
    pvr_set_background(xyz, argb);
    return K2_OK;
}
