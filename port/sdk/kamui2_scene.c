/* Platform layer: Kamui 2's scene, pass and render calls over the kit's PowerVR renderer (pvr/).
 *
 * The game writes TA parameters straight into the vertex buffer: kmBeginPass gives each list a start (an uncached
 * P2 address; the game writes through start ^ 0x4c000000, the store-queue window, which the kit maps onto RAM),
 * and at the end of the pass the game stores each list's end back into the same words (the top six bits kept).
 * kmEndPass records the ranges and kmRender hands the scene's passes to pvr_render, then flips at the vertical
 * blank the driver's vsync interval asks for (30 Hz when the game sets 2 frames, 60 Hz for 1), as Kamui does.
 *
 * The lists of a pass share the bank by the pass's buffer percentages; each scene takes the next bank. A later pass
 * of the same scene continues each list where the previous pass ended.
 *
 * Needs from the game's headers: the function names below as the game's registry has them, and g_kmDriverState
 * (Kamui's driver state, a KmDriverState with the vsync words vsync_wait and vsync_wait_neg, whose offsets differ
 * between Kamui 2 versions: 0x754 / 0x78c in 16,3,0,0, 0x7f4 / 0x82c in 16,3,2,0). */
#include "game.h"
#include "platform.h"
#include "kamui2.h"
#include "lockstep.h"

#define K2_QUEUE_IDLE 0x101          /* the queue status with nothing queued */
#define K2_RENDER_DONE 0x69          /* a render id's status once rendered */
#define K2_RENDER_NOFLIP 1u          /* kmRender's KM_RENDER_NOFLIP (KM_RENDER_FLIP is 0) */
#define RAM_MASK 0x00ffffffu         /* 16 MB main RAM */
#define RAM_CACHED 0x8c000000u
#define RAM_UNCACHED 0xac000000u
#define LIST_ALIGN 32u

K2SysConfig *g_k2_config;
static u32 g_bank, g_list_ptrs[K2_LISTS], g_last_flip;
static s32 g_render_id;
static PvrPass g_passes[PVRX_MAX_PASSES];
static u32 g_pass_count;

static u32 cached(u32 addr) { return RAM_CACHED | (addr & RAM_MASK); }

s32 kmSetSystemConfiguration(void *config)
{
    K2SysConfig *c = config;
    if (!c || !c->vertex_buffer) return K2_ERROR;
    g_k2_config = c;
    g_bank = 0;
    k2_tex_init(c);
    u32 size = k2_vertex_buffer_size(c->vertex_buffer, c->vertex_buffer_size);
    port_log("kamui2: vertex buffer %08x, %u bank(s) of %u bytes, %u pass(es), texture memory %u KB\n",
             c->vertex_buffer, c->vertex_banks, size / (c->vertex_banks ? c->vertex_banks : 1u),
             c->pass_depth, c->texture_memory_size >> 10);
    if (size != c->vertex_buffer_size)
        port_log("kamui2: the vertex buffer's %u bytes run past main RAM's end; using %u\n",
                 c->vertex_buffer_size, size);
    return K2_OK;
}

s32 kmBeginScene(KMSYSTEMCONFIGSTRUCT *config)
{
    K2SysConfig *c = (K2SysConfig *)config;
    if (!c) return K2_ERROR;
    /* Kamui's kmBeginScene never sets the system up again; only kmSetSystemConfiguration does, and that forgets
     * every texture. A game may hand the same config through another address mirror here (its set-up through the
     * 0x0c.. mirror, its scenes through 0x8c..), so only a game that never set it up is set up here. */
    if (!g_k2_config && kmSetSystemConfiguration(c) != K2_OK) return K2_ERROR;
    g_bank = (g_bank + 1u) % (c->vertex_banks ? c->vertex_banks : 1u);
    g_pass_count = 0;
    c->buffer_desc->pass = &c->pass[0];
    lockstep_scene();
    return K2_OK;
}

s32 kmBeginPass(KMVERTEXBUFFDESC *vertexBuffer)
{
    K2VertexBuffDesc *d = (K2VertexBuffDesc *)vertexBuffer;
    K2SysConfig *c = g_k2_config;
    if (!c || g_pass_count >= PVRX_MAX_PASSES) return K2_ERROR;
    u32 banks = c->vertex_banks ? c->vertex_banks : 1u;
    u32 bank_size = k2_vertex_buffer_size(c->vertex_buffer, c->vertex_buffer_size) / banks;
    u32 at = cached(c->vertex_buffer) + g_bank * bank_size;
    const K2PassInfo *info = d->pass;
    PvrPass *p = &g_passes[g_pass_count];
    for (u32 i = 0; i < K2_LISTS; i++) {
        /* the list's share, rounded to whole bytes (the game sized it in bytes, then turned it into a percent) */
        u32 bytes = (u32)((double)bank_size * (double)info->buffer_percent[i] / 100.0 + 0.5) & ~(LIST_ALIGN - 1u);
        u32 start = at;
        if (g_pass_count > 0) start = (g_passes[g_pass_count - 1].end[i] + LIST_ALIGN - 1u) & ~(LIST_ALIGN - 1u);
        p->start[i] = p->end[i] = start;
        g_list_ptrs[i] = RAM_UNCACHED | (start & RAM_MASK);
        at += bytes;
    }
    p->autosort = !(info->region_array_flag & 1u); /* KM_PASSINFO_PRESORT */
    p->z_clear = !(info->region_array_flag & 2u);  /* KM_PASSINFO_DISABLE_Z_CLEAR */
    d->current = g_list_ptrs;
    return K2_OK;
}

s32 kmEndPass(KMVERTEXBUFFDESC *vertexBuffer)
{
    K2VertexBuffDesc *d = (K2VertexBuffDesc *)vertexBuffer;
    PvrPass *p = &g_passes[g_pass_count];
    if (g_pass_count >= PVRX_MAX_PASSES) return K2_ERROR;
    for (u32 i = 0; i < K2_LISTS; i++) {
        u32 end = cached(d->current[i]);
        p->end[i] = end > p->start[i] ? end : p->start[i];
    }
    g_pass_count++;
    d->pass++;
    return K2_OK;
}

/* vsync_wait + 1: the vertical blanks between flips */
static u32 flip_interval(void)
{
    s32 wait = g_kmDriverState.vsync_wait;
    return (u32)(wait < 0 ? 0 : wait) + 1u;
}

/* The SDK's kmSetWaitVsyncCount, as its code writes the driver state: vsync_wait = count - 1, and for count <= 0
 * vsync_wait_neg = 1 - count, vsync_wait = -count - 1. Always 0. */
s32 kmSetWaitVsyncCount(s32 count)
{
    if (count <= 0) {
        count = -count;
        g_kmDriverState.vsync_wait_neg = count + 1;
    }
    g_kmDriverState.vsync_wait = count - 1;
    return K2_OK;
}

/* Flips at the vertical blank: at least one, and vsync_wait + 1 since the last flip. extra: in-between frames are
 * drawn while it waits (runtime/frame_extra.c). */
static void wait_flip(int extra)
{
    u32 target = g_last_flip + flip_interval();
    do {
        if (extra) port_wait_vblank_extra();
        else port_wait_vblank();
    } while ((s32)(port_vblank_count() - target) < 0);
    g_last_flip = port_vblank_count();
}

/* Renders the scene's passes and flips; returns the render id the status calls take. With --present interp the
 * frame is counted (and dumped) at once and shown only if no in-between frame was shown while it waited; an
 * in-between frame's own render is shown at once, with no wait. A KM_RENDER_NOFLIP render (one view of a split
 * screen) only draws into the frame: no wait and nothing shown until a render that flips. */
s32 kmRender(u32 flip)
{
    int extra;
    hw_check_scene(g_passes, g_pass_count);
    pvr_render(g_passes, g_pass_count);
    hw_check_rendered(g_passes, g_pass_count);
    g_pass_count = 0;
    k2_run_callback(0); /* the end-of-render callback: the scene is drawn */
    if (flip == K2_RENDER_NOFLIP) return ++g_render_id;
    if (port_extra_drawing()) {
        gfx_capture_extra();
        gfx_show();
        return ++g_render_id;
    }
    extra = port_extra_begin(flip_interval());
    if (extra) {
        port_extra_state(&g_bank, sizeof g_bank);
        port_extra_state(&g_render_id, sizeof g_render_id);
        port_extra_state(g_list_ptrs, sizeof g_list_ptrs);
        gfx_frame_done();
    }
    wait_flip(extra);
    if (!extra) gfx_present();
    else if (!port_extra_count()) gfx_show();
    return ++g_render_id;
}

s32 kmEndScene(KMSYSTEMCONFIGSTRUCT *config)
{
    (void)config;
    return K2_OK;
}

s32 Km_WaitVSync(void)
{
    port_wait_vblank();
    return K2_OK;
}

u32 Km_GetFrameCounter(void) { return port_vblank_count(); }

/* Rendering finishes inside kmRender, so the queue is always idle and every render id is done. */
s32 Km_GetQueueStatus(s32 id)
{
    (void)id;
    return K2_QUEUE_IDLE;
}

s32 Km_GetTextureStatus(s32 id)
{
    (void)id;
    return K2_RENDER_DONE;
}

/* The y edges are in frame buffer lines (a PAL line mode has more than the 480 the scene is drawn in). */
s32 Km_SetClipRect(u32 x0, u32 y0, u32 x1, u32 y1)
{
    u32 lines = k2_frame_lines(), half = lines / 2u;
    pvr_set_clip(x0, (y0 * 480u + half) / lines, x1, ((y1 + 1u) * 480u + half) / lines - 1u);
    return K2_OK;
}

/* The SDK's own name for it (the same four stores into the driver state, at another offset in later versions). */
s32 kmSetPixelClipping(s32 xMin, s32 yMin, s32 xMax, s32 yMax)
{
    return Km_SetClipRect((u32)xMin, (u32)yMin, (u32)xMax, (u32)yMax);
}
