/* Opaque modifier volumes in cheap shadow mode, as the PowerVR applies them after a pass's opaque and punch-through
 * polygons. The stencil holds three bits per pixel:
 *   0x80  the visible polygon has the PCW shadow bit (written by the opaque and punch-through draws, pvr_states.c);
 *   0x02  parity: each volume face in front of the visible surface flips it, so a closed volume leaves it set on the
 *         surface pixels it encloses (volume instruction 0, the volume's triangles);
 *   0x01  inside: at a volume's last triangles (instruction 1, inclusion), the pixels with the parity set join the
 *         ones earlier volumes enclosed, and the parity is cleared for the next volume.
 * Last, the pixels with 0x80 and 0x01 are scaled by the cheap shadow intensity (FPU_SHAD_SCALE, kmSetCheapShadowMode)
 * and the volume bits are cleared. Exclusion volumes (instruction 2) and the two-parameter mode are to-dos. The same
 * scheme as Flycast's renderers (reference only). */
#include <string.h>
#include "pvr_int.h"
#include "platform.h"
#include "pvr_shadow.h"

#define ISP_VOLUME_SHIFT 29
#define VOLUME_INCLUSION 1u
#define VOLUME_EXCLUSION 2u
#define STENCIL_SHADOW 0x80u
#define STENCIL_PARITY 0x02u
#define STENCIL_INSIDE 0x01u
#define STENCIL_VOLUME (STENCIL_PARITY | STENCIL_INSIDE)

enum { MV_PARITY, MV_INCLUDE, MV_DROP, MV_SHADE, MV_STATES };

static ID3D11DepthStencilState *g_mv[MV_STATES];
static ID3D11BlendState *g_no_color, *g_shade;
static ID3D11PixelShader *g_shadow;
static int g_ready, g_broken;

static int stencil_state(u32 i, int depth, u32 read, u32 write, D3D11_COMPARISON_FUNC func, D3D11_STENCIL_OP pass,
                         D3D11_STENCIL_OP fail)
{
    D3D11_DEPTH_STENCIL_DESC d;
    memset(&d, 0, sizeof d);
    d.DepthEnable = depth;
    d.DepthWriteMask = D3D11_DEPTH_WRITE_MASK_ZERO;
    d.DepthFunc = D3D11_COMPARISON_GREATER; /* the face is nearer than the visible surface */
    d.StencilEnable = TRUE;
    d.StencilReadMask = (UINT8)read;
    d.StencilWriteMask = (UINT8)write;
    d.FrontFace.StencilFunc = func;
    d.FrontFace.StencilPassOp = pass;
    d.FrontFace.StencilFailOp = fail;
    d.FrontFace.StencilDepthFailOp = D3D11_STENCIL_OP_KEEP;
    d.BackFace = d.FrontFace;
    return SUCCEEDED(ID3D11Device_CreateDepthStencilState(g_gfx.dev, &d, &g_mv[i]));
}

static int blend_state(ID3D11BlendState **out, int shade)
{
    D3D11_BLEND_DESC d;
    memset(&d, 0, sizeof d);
    d.RenderTarget[0].RenderTargetWriteMask = shade ? D3D11_COLOR_WRITE_ENABLE_ALL : 0;
    if (shade) {
        d.RenderTarget[0].BlendEnable = TRUE;
        d.RenderTarget[0].SrcBlend = D3D11_BLEND_SRC_ALPHA;
        d.RenderTarget[0].DestBlend = D3D11_BLEND_INV_SRC_ALPHA;
        d.RenderTarget[0].BlendOp = D3D11_BLEND_OP_ADD;
        d.RenderTarget[0].SrcBlendAlpha = D3D11_BLEND_ZERO;
        d.RenderTarget[0].DestBlendAlpha = D3D11_BLEND_ONE;
        d.RenderTarget[0].BlendOpAlpha = D3D11_BLEND_OP_ADD;
    }
    return SUCCEEDED(ID3D11Device_CreateBlendState(g_gfx.dev, &d, out));
}

static int ready(void)
{
    ID3D11Device *dev = g_gfx.dev;
    if (g_ready || g_broken) return g_ready;
    /* parity: flip on each face in front of the surface; include: (value & 3) >= 1 becomes 1, the rest 0; drop: the
     * parity cleared (an exclusion volume, not applied); shade: 0x81 exactly, and the volume bits cleared everywhere */
    g_ready = stencil_state(MV_PARITY, TRUE, 0, STENCIL_PARITY, D3D11_COMPARISON_ALWAYS, D3D11_STENCIL_OP_INVERT,
                            D3D11_STENCIL_OP_KEEP) &&
              stencil_state(MV_INCLUDE, FALSE, STENCIL_VOLUME, STENCIL_VOLUME, D3D11_COMPARISON_LESS_EQUAL,
                            D3D11_STENCIL_OP_REPLACE, D3D11_STENCIL_OP_ZERO) &&
              stencil_state(MV_DROP, FALSE, 0, STENCIL_PARITY, D3D11_COMPARISON_ALWAYS, D3D11_STENCIL_OP_ZERO,
                            D3D11_STENCIL_OP_ZERO) &&
              stencil_state(MV_SHADE, FALSE, STENCIL_SHADOW | STENCIL_INSIDE, STENCIL_VOLUME, D3D11_COMPARISON_EQUAL,
                            D3D11_STENCIL_OP_ZERO, D3D11_STENCIL_OP_ZERO) &&
              blend_state(&g_no_color, 0) && blend_state(&g_shade, 1) &&
              pvr_full_vs() &&
              SUCCEEDED(ID3D11Device_CreatePixelShader(dev, g_pvr_shadow, sizeof g_pvr_shadow, NULL, &g_shadow));
    if (!g_ready) {
        g_broken = 1;
        port_log("pvr: no modifier volume resources; shadows are not drawn\n");
    }
    return g_ready;
}

static void draw_batches(const PvrScene *s, u32 first, u32 end, u32 state, UINT ref)
{
    ID3D11DeviceContext_OMSetDepthStencilState(g_gfx.ctx, g_mv[state], ref);
    for (u32 i = first; i < end; i++) {
        const PvrBatch *b = &s->batch[i];
        if (!b->count) continue;
        pvr_scissor(b);
        ID3D11DeviceContext_Draw(g_gfx.ctx, b->count, b->first);
    }
}

static f32 min3(f32 a, f32 b, f32 c) { return a < b ? (a < c ? a : c) : (b < c ? b : c); }
static f32 max3(f32 a, f32 b, f32 c) { return a > b ? (a > c ? a : c) : (b > c ? b : c); }

/* A triangle's screen box, clipped to the frame (640x480, widescreen wider); 0 if it is off screen. */
static int tri_box(const PvrVtx *v, f32 box[4])
{
    f32 side = 320.0f * (port_widescreen() - 1.0f);
    box[0] = max3(min3(v[0].x, v[1].x, v[2].x), -side, -side);
    box[1] = max3(min3(v[0].y, v[1].y, v[2].y), 0.0f, 0.0f);
    box[2] = min3(max3(v[0].x, v[1].x, v[2].x), 640.0f + side, 640.0f + side);
    box[3] = min3(max3(v[0].y, v[1].y, v[2].y), 480.0f, 480.0f);
    return box[0] < box[2] && box[1] < box[3];
}

int pvr_modvol_trans_needed(const PvrScene *s, u32 pass)
{
    const u32 *tr = s->range[pass][PVRX_LIST_TRANSLUCENT], *r = s->range[pass][PVRX_LIST_TRANS_MOD];
    f32 vol[4] = {1.0e9f, 480.0f, -1.0e9f, 0.0f}, b[4];
    for (u32 i = r[0]; i < r[1]; i++)
        for (u32 k = 0; k + 3u <= s->batch[i].count; k += 3u)
            if (tri_box(&s->vtx[s->batch[i].first + k], b)) {
                vol[0] = min3(vol[0], b[0], b[0]);
                vol[1] = min3(vol[1], b[1], b[1]);
                vol[2] = max3(vol[2], b[2], b[2]);
                vol[3] = max3(vol[3], b[3], b[3]);
            }
    if (vol[0] >= vol[2] || vol[1] >= vol[3]) return 0;
    for (u32 i = tr[0]; i < tr[1]; i++) {
        if (!(s->batch[i].flags & PVR_BF_SHADOW)) continue;
        for (u32 k = 0; k + 3u <= s->batch[i].count; k += 3u)
            if (tri_box(&s->vtx[s->batch[i].first + k], b) && b[0] < vol[2] && vol[0] < b[2] && b[1] < vol[3] &&
                vol[1] < b[3])
                return 1;
    }
    return 0;
}

void pvr_modvol_draw(const PvrScene *s, u32 pass, ID3D11Buffer *frame_consts)
{
    ID3D11DeviceContext *cx = g_gfx.ctx;
    const u32 *r = s->range[pass][PVRX_LIST_OPAQUE_MOD];
    D3D11_RECT all = {0, 0, (LONG)g_gfx.draw_w, (LONG)g_gfx.draw_h};
    u32 base = r[0];
    if (g_pvr.shadow < 0) {
        pvr_todo("pvr: modifier volumes with two-parameter polygons (cheap shadow off)");
        return;
    }
    if (!ready()) return;
    pvr_seen("modifier volumes: cheap shadow, intensity %d/256", g_pvr.shadow);
    ID3D11DeviceContext_PSSetShader(cx, NULL, NULL, 0);
    ID3D11DeviceContext_OMSetBlendState(cx, g_no_color, NULL, 0xffffffffu);
    for (u32 i = r[0]; i < r[1]; i++) {
        u32 mode = (s->batch[i].isp >> ISP_VOLUME_SHIFT) & 7u;
        draw_batches(s, i, i + 1u, MV_PARITY, 0);
        if (mode != VOLUME_INCLUSION && mode != VOLUME_EXCLUSION) continue;
        if (mode == VOLUME_EXCLUSION) pvr_todo("pvr: exclusion modifier volumes");
        draw_batches(s, base, i + 1u, mode == VOLUME_INCLUSION ? MV_INCLUDE : MV_DROP, STENCIL_INSIDE);
        base = i + 1u;
    }
    ID3D11DeviceContext_RSSetScissorRects(cx, 1, &all);
    ID3D11DeviceContext_IASetInputLayout(cx, NULL);
    ID3D11DeviceContext_VSSetShader(cx, pvr_full_vs(), NULL, 0);
    ID3D11DeviceContext_PSSetShader(cx, g_shadow, NULL, 0);
    ID3D11DeviceContext_PSSetConstantBuffers(cx, 0, 1, &frame_consts);
    ID3D11DeviceContext_OMSetBlendState(cx, g_shade, NULL, 0xffffffffu);
    ID3D11DeviceContext_OMSetDepthStencilState(cx, g_mv[MV_SHADE], STENCIL_SHADOW | STENCIL_INSIDE);
    ID3D11DeviceContext_Draw(cx, 3, 0);
}
