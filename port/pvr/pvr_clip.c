/* The scissor a batch draws under: the batch's clip within the global one, or, during a wipe that opens to the frame
 * (pvr_set_clip_opens_frame), its share of the whole frame. */
#include "pvr_int.h"
#include "platform.h"

/* A clip that opens to the frame (pvr_set_clip_opens_frame): its share of the screen is its share of the frame. */
int pvr_frame_share_clip(D3D11_RECT *r)
{
    u32 x0 = g_pvr_drawn->clip[0], y0 = g_pvr_drawn->clip[1], x1 = g_pvr_drawn->clip[2] + 1u, y1 = g_pvr_drawn->clip[3] + 1u;
    if (!g_pvr_drawn->clip_opens_frame || (port_widescreen() <= 1.0f && port_tallscreen() <= 1.0f)) return 0;
    if (x0 == 0u && x1 >= 640u && y0 == 0u && y1 >= 480u) return 0;
    r->left = (LONG)(x0 * g_gfx.draw_w / 640u);
    r->right = (LONG)((x1 < 640u ? x1 : 640u) * g_gfx.draw_w / 640u);
    r->top = (LONG)(y0 * g_gfx.draw_h / 480u);
    r->bottom = (LONG)((y1 < 480u ? y1 : 480u) * g_gfx.draw_h / 480u);
    return 1;
}

/* A batch's own top and bottom still hold during a wipe: its share of the frame's height, so bars a game draws by
 * clipping a view reach the window's sides as they do at the original shape. */
static void batch_rows_clip(const PvrBatch *b, D3D11_RECT *r)
{
    LONG top = (LONG)(b->clip[1] * g_gfx.draw_h / 480u), bottom = (LONG)((b->clip[3] < 480u ? b->clip[3] : 480u) * g_gfx.draw_h / 480u);
    if (top > r->top) r->top = top;
    if (bottom < r->bottom) r->bottom = bottom;
    if (r->bottom < r->top) r->bottom = r->top;
}

void pvr_scissor(const PvrBatch *b)
{
    D3D11_RECT r;
    if (pvr_frame_share_clip(&r)) { /* the game's view clip is the wipe's rectangle too */
        batch_rows_clip(b, &r);
        ID3D11DeviceContext_RSSetScissorRects(g_gfx.ctx, 1, &r);
        return;
    }
    u32 x0 = b->clip[0] > g_pvr_drawn->clip[0] ? b->clip[0] : g_pvr_drawn->clip[0];
    u32 y0 = b->clip[1] > g_pvr_drawn->clip[1] ? b->clip[1] : g_pvr_drawn->clip[1];
    u32 x1 = b->clip[2] < g_pvr_drawn->clip[2] + 1u ? b->clip[2] : g_pvr_drawn->clip[2] + 1u;
    u32 y1 = b->clip[3] < g_pvr_drawn->clip[3] + 1u ? b->clip[3] : g_pvr_drawn->clip[3] + 1u;
    r.left = pvr_draw_x(x0, x0 == 0u && x1 >= 640u);
    r.top = pvr_draw_y(y0, y0 == 0u && y1 >= 480u);
    r.right = pvr_draw_x(x1, x0 == 0u && x1 >= 640u);
    r.bottom = pvr_draw_y(y1, y0 == 0u && y1 >= 480u);
    ID3D11DeviceContext_RSSetScissorRects(g_gfx.ctx, 1, &r);
}
