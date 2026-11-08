/* --interp-gpu on: a scene-blended game's in-between frames (pvr_blend_draw in pvr_draw.c) blended by the GPU. The
 * scene's own vertices stay in the vertex buffer the renderer drew them from. Where the blend at t = 0 puts them (the
 * old scene's places, colours and texture coordinates: pvr_blend_vertices) goes into a second buffer once a scene, and
 * an in-between frame then only sets t, which vs_blend (pvr.hlsl) mixes the two by. The CPU work of a frame after the
 * first is the draw calls alone; without the option the CPU blends every frame, as before. The two give the same
 * picture to within the GPU's rounding of the colours, and a check run draws no in-between frames. */
#include <string.h>
#include "pvr_int.h"
#include "platform.h"
#include "pvr_blend_vs.h"

static int g_on, g_broken;
static ID3D11VertexShader *g_vs;
static ID3D11InputLayout *g_layout;
static ID3D11Buffer *g_from_vb, *g_cb;
static u32 g_from_cap;   /* bytes */
static u32 g_from_scene; /* the pvr_blend_scene the buffer holds the blend for */
static int g_from_set;

int pvr_blend_gpu_option(const char *value)
{
    g_on = !strcmp(value, "on");
    return 2;
}

static int init(void)
{
    static const D3D11_INPUT_ELEMENT_DESC el[] = {
        {"POSITION", 0, DXGI_FORMAT_R32G32B32_FLOAT, 0, 0, D3D11_INPUT_PER_VERTEX_DATA, 0},
        {"TEXCOORD", 0, DXGI_FORMAT_R32G32_FLOAT, 0, 12, D3D11_INPUT_PER_VERTEX_DATA, 0},
        {"COLOR", 0, DXGI_FORMAT_B8G8R8A8_UNORM, 0, 20, D3D11_INPUT_PER_VERTEX_DATA, 0},
        {"COLOR", 1, DXGI_FORMAT_B8G8R8A8_UNORM, 0, 24, D3D11_INPUT_PER_VERTEX_DATA, 0},
        {"BATCH", 0, DXGI_FORMAT_R32_UINT, 0, 28, D3D11_INPUT_PER_VERTEX_DATA, 0},
        {"POSITION", 1, DXGI_FORMAT_R32G32B32_FLOAT, 1, 0, D3D11_INPUT_PER_VERTEX_DATA, 0},
        {"TEXCOORD", 1, DXGI_FORMAT_R32G32_FLOAT, 1, 12, D3D11_INPUT_PER_VERTEX_DATA, 0},
        {"COLOR", 2, DXGI_FORMAT_B8G8R8A8_UNORM, 1, 20, D3D11_INPUT_PER_VERTEX_DATA, 0},
        {"COLOR", 3, DXGI_FORMAT_B8G8R8A8_UNORM, 1, 24, D3D11_INPUT_PER_VERTEX_DATA, 0},
    };
    D3D11_BUFFER_DESC d;
    if (g_cb) return 1;
    if (g_broken) return 0;
    memset(&d, 0, sizeof d);
    d.ByteWidth = 16;
    d.Usage = D3D11_USAGE_DYNAMIC;
    d.BindFlags = D3D11_BIND_CONSTANT_BUFFER;
    d.CPUAccessFlags = D3D11_CPU_ACCESS_WRITE;
    if (FAILED(ID3D11Device_CreateVertexShader(g_gfx.dev, g_pvr_blend_vs, sizeof g_pvr_blend_vs, NULL, &g_vs)) ||
        FAILED(ID3D11Device_CreateInputLayout(g_gfx.dev, el, 9, g_pvr_blend_vs, sizeof g_pvr_blend_vs, &g_layout)) ||
        FAILED(ID3D11Device_CreateBuffer(g_gfx.dev, &d, NULL, &g_cb))) {
        port_log("pvr: no GPU blend (--interp-gpu); the CPU blends the in-between frames\n");
        g_broken = 1;
        return 0;
    }
    return 1;
}

static int put(ID3D11Buffer *b, const void *data, u32 bytes)
{
    D3D11_MAPPED_SUBRESOURCE m;
    if (FAILED(ID3D11DeviceContext_Map(g_gfx.ctx, (ID3D11Resource *)b, 0, D3D11_MAP_WRITE_DISCARD, 0, &m))) return 0;
    memcpy(m.pData, data, bytes);
    ID3D11DeviceContext_Unmap(g_gfx.ctx, (ID3D11Resource *)b, 0);
    return 1;
}

/* The blend at t = 0 in the second buffer, made once a scene */
static int from_vertices(const PvrScene *cur)
{
    u32 bytes = cur->nvtx * (u32)sizeof(PvrVtx);
    const PvrVtx *from;
    if (g_from_set && g_from_scene == pvr_blend_scene()) return 1;
    if ((from = pvr_blend_vertices(cur, 0.0f, NULL)) == NULL) return 0;
    if (bytes > g_from_cap) {
        D3D11_BUFFER_DESC d;
        if (g_from_vb) ID3D11Buffer_Release(g_from_vb);
        g_from_vb = NULL;
        g_from_cap = 0;
        memset(&d, 0, sizeof d);
        d.ByteWidth = bytes + bytes / 4u; /* room to grow */
        d.Usage = D3D11_USAGE_DYNAMIC;
        d.BindFlags = D3D11_BIND_VERTEX_BUFFER;
        d.CPUAccessFlags = D3D11_CPU_ACCESS_WRITE;
        if (FAILED(ID3D11Device_CreateBuffer(g_gfx.dev, &d, NULL, &g_from_vb))) return 0;
        g_from_cap = d.ByteWidth;
    }
    if (!put(g_from_vb, from, bytes)) return 0;
    g_from_scene = pvr_blend_scene();
    g_from_set = 1;
    return 1;
}

int pvr_blend_gpu_ready(const PvrScene *cur, f32 t)
{
    f32 c[4] = {t, cur->has_from ? 1.0f : 0.0f, 0.0f, 0.0f}; /* y: --interp-parts, blend in (x w, y w, w) */
    if (!g_on || !cur->nvtx || (cur->has_from && pvr_parts_holds()) || !init() || !from_vertices(cur)) return 0;
    return put(g_cb, c, sizeof c);
}

/* bind_pipeline (pvr_draw.c) in a frame pvr_blend_gpu_ready said yes to: vs_blend, the old places beside the scene's */
void pvr_blend_gpu_bind(ID3D11DeviceContext *cx, ID3D11Buffer *scene_vb)
{
    ID3D11Buffer *vb[2] = {scene_vb, g_from_vb};
    UINT stride[2] = {sizeof(PvrVtx), sizeof(PvrVtx)}, offset[2] = {0, 0};
    ID3D11DeviceContext_IASetInputLayout(cx, g_layout);
    ID3D11DeviceContext_IASetVertexBuffers(cx, 0, 2, vb, stride, offset);
    ID3D11DeviceContext_VSSetShader(cx, g_vs, NULL, 0);
    ID3D11DeviceContext_VSSetConstantBuffers(cx, 1, 1, &g_cb);
}
